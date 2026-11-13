"""
The rating model, in two layers.

Layer 1, one number per game (what happened):
    * Game Score as published by Luszczyszyn (2016), with on-ice 5v5 shot attempts and goals.
    * xG Game Score: the same, but goals are replaced by individual expected goals (ixG) and the on-ice
      5v5 shot-attempt and goal terms by on-ice 5v5 expected goals for and against. Shots and xG repeat far
      better from game to game than goals do, so this is the per-game score the site shows on its 0-10 scale.
    * Goalies: goals saved above expected (GSAx) and goalie Game Score.
  Expected goals come from a logistic model trained on earlier seasons and then frozen (model/xg.json).

Layer 2, one rating per player (how good is he), updated after every game with a Kalman filter:
    offseason (once):  m = rho*m_last + (1-rho)*mu_position     v = rho^2*v_last + q_season
    before each game:  v = v + q_game * (team games since his last game)      (+ a bump after a trade)
    after the game:    R = s2 * noise_scale    K = v/(v+R)    m = m + K*(y-m)    v = (1-K)*v
  Three filters, each fitted on past seasons by maximising one-game-ahead predictive likelihood:
    form      skaters, y = xG Game Score of the game,                 noise the same every game
    drive     skaters, y = on-ice 5v5 xG differential per 60 minutes, noise s2 / 5v5 minutes
    goalie    goalies, y = GSAx per unblocked shot faced,             noise s2 / shots faced
  Settings and every player's starting point for this season live in model/ (written by build_model.py).

Context: a ridge-regression adjusted plus-minus (RAPM) on 5v5 expected goals, with last season's result as
the prior, separates each skater's own effect from his teammates' and opponents'.
"""
import json, math, os
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(HERE, "model")

# ----------------------------------------------------------------------------- expected goals
SHOT_TYPES = ["snap", "slap", "backhand", "tip-in", "deflected", "wrap-around"]   # wrist shot is the baseline
XG_NAMES = ["const", "dist/10", "log(1+dist)", "angle/30", "rebound", "rush", "rebound*angle/30",
            "power play", "shorthanded", "empty net"] + SHOT_TYPES + ["other type"]


def xg_row(s):
    d, a = s["dist"], s["angle"] / 30
    st = s.get("shot_type", "")
    return [1.0, d / 10, math.log1p(d), a, float(s["rebound"]), float(s["rush"]), float(s["rebound"]) * a,
            float(s.get("pp", False)), float(s.get("sh", False)), float(s.get("en", False))] + \
           [float(st == t) for t in SHOT_TYPES] + [float(bool(st) and st != "wrist" and st not in SHOT_TYPES)]


def fit_logit(X, y, l2=1.0, iters=60):
    X, y = np.asarray(X, float), np.asarray(y, float)
    w = np.zeros(X.shape[1])
    R = l2 * np.eye(X.shape[1]); R[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + R @ w
        H = (X * (p * (1 - p))[:, None]).T @ X + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def fit_xg(shots):
    w = fit_logit([xg_row(s) for s in shots], [s["goal"] for s in shots])
    return dict(names=XG_NAMES, coef=[round(float(c), 5) for c in w], shots=len(shots), goals=int(sum(s["goal"] for s in shots)))


def apply_xg(shots, model):
    if not shots:
        return
    w = np.asarray(model["coef"])
    p = 1 / (1 + np.exp(-np.asarray([xg_row(s) for s in shots]) @ w))
    for s, v in zip(shots, p):
        s["xg"] = float(v)


def auc(p, y):
    p, y = np.asarray(p), np.asarray(y)
    order = np.argsort(p)
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    n1 = y.sum(); n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def xg_onice_weight(parsed_games):
    """Weight of one on-ice 5v5 expected goal in xG Game Score: chosen so that, over the training seasons, the
    on-ice term adds up to the same total as Game Score's 0.05 x shot attempts + 0.15 x goals (for and against)."""
    cf = gf = xgf = 0.0
    for P in parsed_games:
        for st in P["stat"].values():
            cf += st.get("CF", 0); gf += st.get("GF", 0)
        for s in P["shots"]:
            xgf += s["xg"] * len(s["on_for"])
    return (0.05 * cf + 0.15 * gf) / xgf if xgf else 1.0


# ----------------------------------------------------------------------------- Kalman filter
FILTERS = ("form", "drive", "goalie")
TRADE_GAMES = 5   # a trade adds as much uncertainty as five games of drift


def observations(rows, which):
    """Turn player-game rows (with date, season, team, game_id) into the filter's input, in game order:
    (season, player, group, team games since his last game, traded?, y or None, noise scale)."""
    team_games = defaultdict(list)
    for r in sorted({(r["season"], r["team"], r["date"], r["game_id"]) for r in rows}):
        team_games[(r[0], r[1])].append(r[3])
    index = {(k[0], k[1], g): i for k, gl in team_games.items() for i, g in enumerate(gl)}
    last = {}
    out = []
    for r in sorted(rows, key=lambda r: (r["date"], r["game_id"])):
        grp = r["group"]
        if (which == "goalie") != (grp == "G"):
            continue
        y, scale = None, 1.0
        if which == "form":
            y = float(r["xgs"])
        elif which == "drive":
            toi = float(r.get("toi5") or 0)
            if toi >= 1.0:
                y, scale = (float(r["xGF5"]) - float(r["xGA5"])) / toi * 60, 1.0 / toi
        else:
            fa = int(r.get("FA") or 0)
            if fa > 0:
                y, scale = float(r["raw"]) / fa, 1.0 / fa
        i = index[(r["season"], r["team"], r["game_id"])]
        prev = last.get(r["player_id"])
        traded = False
        if prev and prev[0] == r["season"] and prev[1] == r["team"]:
            gap = i - prev[2]
        elif prev and prev[0] == r["season"]:
            gap, traded = 1, True
        else:
            gap = i + 1
        last[r["player_id"]] = (r["season"], r["team"], i)
        out.append((r["season"], r["player_id"], grp, max(gap, 1), traded, y, scale, r))
    return out


def run_filter(obs, prm, mu, prior=None, collect=False):
    """Run the filter over `obs`. prm = dict(s2, q_game, v_new, rho, q_season). prior: player -> (m, v, season).
    Returns (log-likelihood of each game given the games before it, final state, per-game results if collect)."""
    s2, qg, vnew, rho, qs = prm["s2"], prm["q_game"], prm["v_new"], prm["rho"], prm["q_season"]
    state = dict(prior or {})
    ll, n, res = 0.0, 0, []
    for season, pid, grp, gap, traded, y, scale, row in obs:
        e = state.get(pid)
        if e is None:
            m, v = mu[grp], vnew
        else:
            m, v, s0 = e
            if season != s0:     # offseason: keep a share rho of last season, add uncertainty
                m, v = rho * m + (1 - rho) * mu[grp], rho * rho * v + qs
        v += qg * (gap + (TRADE_GAMES if traded else 0))
        pre = (m, v)
        if y is not None:
            R = s2 * scale
            S = v + R
            ll += -0.5 * (math.log(2 * math.pi * S) + (y - m) ** 2 / S); n += 1
            K = v / S
            m, v = m + K * (y - m), (1 - K) * v
        state[pid] = (m, v, season)
        if collect:
            res.append((row, pre, (m, v), y, scale))
    return ll, n, state, res


def fit_filter(obs, mu, start=None):
    """Maximum-likelihood settings for one filter (local use only: needs scipy)."""
    from scipy.optimize import minimize
    ys = np.array([o[5] for o in obs if o[5] is not None]); sc = np.array([o[6] for o in obs if o[5] is not None])
    var = float(np.average((ys - ys.mean()) ** 2 / sc, weights=1 / sc))   # noise variance per unit of scale
    x0 = start or [math.log(var), math.log(var * 1e-4 * np.mean(sc)), math.log(var * 0.05 * np.mean(sc)),
                   math.log(0.7 / 0.3), math.log(var * 0.01 * np.mean(sc))]      # rho starts at 0.7 (team Elo's value)
    names = ["s2", "q_game", "v_new", "rho", "q_season"]

    def unpack(x):
        p = dict(zip(names, [math.exp(v) for v in x]))
        p["rho"] = 1 / (1 + math.exp(-x[3]))
        return p

    def nll(x):
        ll, n, _, _ = run_filter(obs, unpack(x), mu)
        return -ll / n

    simplex = [list(x0)] + [[v + (1.0 if i == j else 0.0) for j, v in enumerate(x0)] for i in range(len(x0))]
    best = minimize(nll, x0, method="Nelder-Mead",
                    options=dict(maxiter=1500, xatol=1e-3, fatol=1e-7, initial_simplex=np.array(simplex)))
    p = unpack(best.x)
    return {k: float(f"{v:.6g}") for k, v in p.items()}, -best.fun


def group_means(obs):
    by = defaultdict(lambda: [0.0, 0.0])
    for o in obs:
        if o[5] is not None:
            by[o[2]][0] += o[5] / o[6]; by[o[2]][1] += 1 / o[6]
    return {g: a / b for g, (a, b) in by.items()}


RATING_SPREAD = 10.0   # rating points per standard deviation of last season's spread among regulars


def to_hundred(m, mu, sd):
    """A rating on the site's 0-100 scale: 50 = average player at the position, +10 per standard deviation of
    the spread of ratings among regulars at the end of last season (70 is about the top 2%)."""
    return min(100.0, max(0.0, 50 + RATING_SPREAD * (m - mu) / sd))


# ----------------------------------------------------------------------------- RAPM on 5v5 expected goals
def stint_rows(P):
    """(seconds, home skaters, away skaters, home xG, away xG) for every 5v5 stint of a parsed game."""
    if not P["stints"]:
        return []
    import bisect
    ends = defaultdict(list)
    idx = defaultdict(list)
    for i, (per, t0, t1, hs, as_) in enumerate(P["stints"]):
        ends[per].append(t1); idx[per].append(i)
    xg = [[0.0, 0.0] for _ in P["stints"]]
    for s in P["shots"]:
        if not s["five"]:
            continue
        e = ends.get(s["per"])
        if not e:
            continue
        j = bisect.bisect_left(e, s["t"])          # first stint ending at or after the shot
        if j < len(e):
            i = idx[s["per"]][j]
            per, t0, t1, hs, as_ = P["stints"][i]
            if t0 < s["t"] <= t1:
                xg[i][0 if s["team"] == P["home"] else 1] += s["xg"]
    return [(t1 - t0, hs, as_, xg[i][0], xg[i][1]) for i, (per, t0, t1, hs, as_) in enumerate(P["stints"])]


def rapm(stints, lam, prior=None, min_seconds=0):
    """Ridge regression of 5v5 xG per 60 on who is on the ice, shrunk toward `prior` (player -> (off, def)).
    Returns player -> (offence, defence, 5v5 seconds): xG for and against per 60 he adds versus an average skater
    (defence: positive = allows more)."""
    players = sorted({p for st in stints for p in st[1] + st[2]})
    col = {p: i for i, p in enumerate(players)}
    n = 2 * len(players) + 2                  # offence, defence for each player; intercept; home offence
    A = np.zeros((n, n)); b = np.zeros(n)
    secs = defaultdict(float)
    for dur, hs, as_, xh, xa in stints:
        w = dur / 3600
        for off, de, x, home in ((hs, as_, xh, 1), (as_, hs, xa, 0)):
            ix = [2 * col[p] for p in off] + [2 * col[p] + 1 for p in de] + [n - 2] + ([n - 1] if home else [])
            ix = np.array(ix)
            A[np.ix_(ix, ix)] += w
            b[ix] += x                          # w * (x / w)
        for p in hs + as_:
            secs[p] += dur
    pri = np.zeros(n)
    if prior:
        for p, i in col.items():
            if p in prior:
                pri[2 * i], pri[2 * i + 1] = prior[p]
    L = np.full(n, float(lam)); L[-2:] = 0
    beta = np.linalg.solve(A + np.diag(L), b + L * pri)
    out = {p: (float(beta[2 * i]), float(beta[2 * i + 1]), secs[p]) for p, i in col.items()}
    return out, float(beta[-2]), float(beta[-1])


def rapm_error(train, test, lam, prior=None):
    """Weighted squared error of predicted 5v5 xG per 60 on held-out stints."""
    fit, base, home = rapm(train, lam, prior)
    err = wsum = 0.0
    for dur, hs, as_, xh, xa in test:
        w = dur / 3600
        for off, de, x, h in ((hs, as_, xh, home), (as_, hs, xa, 0.0)):
            pred = base + h + sum(fit.get(p, (0, 0, 0))[0] for p in off) + sum(fit.get(p, (0, 0, 0))[1] for p in de)
            err += w * (x / w - pred) ** 2; wsum += w
    return err / wsum


# ----------------------------------------------------------------------------- per-game 0-100 score and expected score
def rank_scores(rows, spread):
    """The per-game 0-100 score: rank every player-game within its position group (F, D, G) by raw score and place the
    rank on a bell curve, 50 + spread * normal quantile. Sets row["pct"] and row["rating"]; returns reference info."""
    from statistics import NormalDist
    bell = NormalDist()
    ref = {}
    for grp in "FDG":
        group = sorted((r for r in rows if r["group"] == grp), key=lambda r: r["raw"])
        n = len(group)
        ref[grp] = dict(games=n, median_raw=group[n // 2]["raw"] if n else None)
        i = 0
        while i < n:                                   # equal raw scores share their average rank
            j = i
            while j + 1 < n and group[j + 1]["raw"] == group[i]["raw"]:
                j += 1
            pct = ((i + j) / 2 + 0.5) / n
            for r in group[i:j + 1]:
                r["pct"] = round(pct * 100, 1)
                r["rating"] = int(round(min(100.0, max(0.0, 50 + spread * bell.inv_cdf(pct)))))
            i = j + 1
    return ref


# What a skater's game score "should" be, from what was known before the game. Fitted by least squares on past seasons
# (build_model.py); division, conference, repeat meetings and the opposing goalie were tested and added nothing.
EXPECTED_FEATURES = ["rating before the game", "average TOI so far (min)", "defenceman", "home",
                     "own team xGF per game", "own team xGA per game", "opponent xGF per game", "opponent xGA per game",
                     "own back-to-back", "opponent back-to-back"]
TEAM_PRIOR_GAMES = 10    # team xG rates start at the league average, worth this many games
TOI_PRIOR_GAMES = 5      # ice time starts at the position average (17.5 min F, 21 min D), worth this many games


def team_xg(shots, home_id):
    """(home xG, away xG) of one game's unblocked shots, empty-net shots left out."""
    h = sum(s["xg"] for s in shots if not s["en"] and s["team"] == home_id)
    return h, sum(s["xg"] for s in shots if not s["en"]) - h


def expected_features(rows, games, league_xg):
    """Pre-game features for every skater row of one season, stored in row["xf"]. games: (game_id, date, home team,
    away team, home xG, away xG) for every game of the season; rows need "form_pre" (0-100 rating before the game)."""
    import datetime as dt
    team = defaultdict(lambda: [0, 0.0, 0.0, None])      # games, xGF, xGA, date of last game
    ctx = {}
    n0 = TEAM_PRIOR_GAMES
    for gid, date, h, a, xh, xa in sorted(games, key=lambda g: (g[1], g[0])):
        d = dt.date.fromisoformat(date)
        for t in (h, a):
            gp, f, ag, last = team[t]
            ctx[(gid, t)] = ((f + n0 * league_xg) / (gp + n0), (ag + n0 * league_xg) / (gp + n0),
                             float(last is not None and (d - last).days == 1), t == h, a if t == h else h)
        for t, f, ag in ((h, xh, xa), (a, xa, xh)):
            x = team[t]; x[0] += 1; x[1] += f; x[2] += ag; x[3] = d
    usage = defaultdict(lambda: [0, 0.0])
    for r in sorted(rows, key=lambda r: (r["date"], r["game_id"])):
        if r["group"] == "G" or (r["game_id"], r["team"]) not in ctx:
            continue
        own = ctx[(r["game_id"], r["team"])]
        opp = ctx[(r["game_id"], own[4])]
        u = usage[r["player_id"]]
        base = 17.5 if r["group"] == "F" else 21.0
        if r.get("form_pre") is not None:
            r["xf"] = [float(r["form_pre"]), (u[1] + TOI_PRIOR_GAMES * base) / (u[0] + TOI_PRIOR_GAMES),
                       float(r["group"] == "D"), float(own[3]), own[0], own[1], opp[0], opp[1], own[2], opp[2]]
        u[0] += 1; u[1] += secs_of(r["toi"]) / 60


def secs_of(mmss):
    m, s = str(mmss).split(":")
    return int(m) * 60 + int(s)


def expected_score(xf, coef):
    return min(100.0, max(0.0, coef[0] + sum(c * x for c, x in zip(coef[1:], xf))))


# ----------------------------------------------------------------------------- saved model
def load():
    path = os.path.join(MODEL_DIR, "model.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_priors(season):
    path = os.path.join(MODEL_DIR, f"priors_{season}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)
