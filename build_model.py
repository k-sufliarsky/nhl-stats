#!/usr/bin/env python3
"""
Build the frozen rating model from past seasons (run locally once per season; needs numpy and scipy):

    python3 build_model.py                                  # train on 2024-25 and 2025-26, check on 2026-27 so far
    python3 build_model.py --train 20242025 20252026 --check 20262027

Writes model/model.json (xG model, filter settings, checks) and model/priors_<next season>.json (every player's
rating at the end of the last training season, the starting point for the next one). See rating_model.py.
"""
import argparse, json, math, os, sys, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import nhl_ratings as N
import rating_model as M


def season_games(season):
    sid, first, last = N.season_window(season)
    ids = [g for g in N.league_game_ids(first, 2, last) if str(g)[:4] == str(season)[:4]]
    with ThreadPoolExecutor(8) as ex:
        raw = list(ex.map(N.load_game, ids))
    out = []
    for gid, box, pbp, sh in raw:
        try:
            P = N.parse_game(gid, box, pbp, sh)
        except Exception as e:
            print(f"  skip {gid}: {e}", file=sys.stderr); continue
        out.append((gid, box, P))
    print(f"{season}: {len(out)} games parsed", file=sys.stderr)
    return out


def rows_for(games, k, season):
    rows = []
    for gid, box, P in games:
        for r in N.player_rows(gid, P, box, k):
            r.update(season=season, date=box["gameDate"])
            rows.append(r)
    return rows


def corr(a, b, w=None):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return float("nan")
    w = np.ones_like(a) if w is None else np.asarray(w, float)
    ma, mb = np.average(a, weights=w), np.average(b, weights=w)
    cov = np.average((a - ma) * (b - mb), weights=w)
    return float(cov / math.sqrt(np.average((a - ma) ** 2, weights=w) * np.average((b - mb) ** 2, weights=w)))


def split_half(rows, field, group, per60=False, min_games=20):
    """Odd/even-game split-half correlation of each player's average (or per-60 rate) of `field`."""
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["date"], r["game_id"])):
        if r["group"] in group:
            by[r["player_id"]].append(r)
    a, b = [], []
    for games in by.values():
        if len(games) < 2 * min_games:
            continue
        halves = games[0::2], games[1::2]
        if per60:
            vals = [sum(float(g[field]) for g in h) / max(1e-9, sum(N.secs(g["toi"]) for g in h)) * 3600 for h in halves]
        else:
            vals = [sum(float(g[field]) for g in h) / len(h) for h in halves]
        a.append(vals[0]); b.append(vals[1])
    return round(corr(a, b), 3), len(a)


def next_game(rows, predictor, target, group="FD", window=10):
    """Correlation between a player's average `predictor` over his previous `window` games and his next game's `target`."""
    by = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["date"], r["game_id"])):
        if r["group"] in group:
            by[r["player_id"]].append(r)
    xs, ys = [], []
    for games in by.values():
        for i in range(window, len(games)):
            xs.append(sum(float(g[predictor]) for g in games[i - window:i]) / window)
            ys.append(float(games[i][target]))
    return round(corr(xs, ys), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", default=["20242025", "20252026"])
    ap.add_argument("--check", default="20262027", help="season used only to check the model (out of sample)")
    a = ap.parse_args()
    t0 = time.time()
    train = {s: season_games(s) for s in a.train}
    check = season_games(a.check) if a.check and a.check not in a.train else []
    last = a.train[-1]
    nxt = f"{int(last[:4]) + 1}{int(last[:4]) + 2}"

    # --- expected goals, trained on the training seasons and frozen
    tr_shots = [s for g in train.values() for _, _, P in g for s in P["shots"]]
    xg = M.fit_xg(tr_shots)
    for g in list(train.values()) + [check]:
        for _, _, P in g:
            M.apply_xg(P["shots"], xg)
    ck_shots = [s for _, _, P in check for s in P["shots"]]
    def quality(shots):
        p = np.clip([s["xg"] for s in shots], 1e-6, 1 - 1e-6); y = np.array([s["goal"] for s in shots])
        return dict(shots=len(shots), goals=int(y.sum()), expected=round(float(p.sum()), 1), auc=round(M.auc(p, y), 3),
                    log_loss=round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4))
    xg["train_fit"] = quality(tr_shots)
    xg["check"] = quality(ck_shots) if ck_shots else None
    print("xG", xg["train_fit"], "check", xg["check"], file=sys.stderr)

    k = M.xg_onice_weight([P for g in train.values() for _, _, P in g])
    print(f"on-ice xG weight {k:.3f}", file=sys.stderr)
    rows = {s: rows_for(g, k, s) for s, g in train.items()}
    ck_rows = rows_for(check, k, a.check)
    tr_rows = [r for s in a.train for r in rows[s]]

    # --- per-game scores: means (same scale?) and reliability
    sk = [r for r in tr_rows if r["group"] != "G"]
    checks = dict(
        mean_gs=round(float(np.mean([r["gs"] for r in sk])), 3), mean_xgs=round(float(np.mean([r["xgs"] for r in sk])), 3),
        sd_gs=round(float(np.std([r["gs"] for r in sk])), 3), sd_xgs=round(float(np.std([r["xgs"] for r in sk])), 3),
        split_half={}, next_game={})
    rel = rows[last]
    for name, field, grp, per60 in [("Game Score per game", "gs", "FD", False), ("xG Game Score per game", "xgs", "FD", False),
                                    ("goals per 60", "G", "FD", True), ("ixG per 60", "ixG", "FD", True),
                                    ("points per 60", "PTS", "FD", True), ("goalie GSAx per game", "raw", "G", False)]:
        checks["split_half"][name] = split_half(rel, field, grp, per60, 15 if grp == "G" else 20)
    for pred in ("gs", "xgs"):
        for tgt in ("gs", "xgs"):
            checks["next_game"][f"last 10 {pred} -> next {tgt}"] = next_game(rel, pred, tgt)
    print(json.dumps(checks, indent=1), file=sys.stderr)

    # --- Kalman filters: fit on the training seasons, check on the new season
    filters, state_out = {}, {}
    for which in M.FILTERS:
        obs = M.observations(tr_rows, which)
        mu = M.group_means(obs)
        prm, ll = M.fit_filter(obs, mu)
        _, _, state, _ = M.run_filter(obs, prm, mu)
        # spread of ratings among regulars at the end of the last training season (sets the 0-10 scale)
        games, grp_of = defaultdict(int), {}
        for o in obs:
            grp_of[o[1]] = o[2]
            if o[0] == last and o[5] is not None:
                games[o[1]] += 1
        sd = {}
        for grp in mu:
            ms = [state[p][0] for p, n in games.items() if n >= 41 and state[p][2] == last and grp_of[p] == grp]
            sd[grp] = float(np.std(ms)) if len(ms) > 5 else 1.0
        f = dict(params=prm, mu={g: round(v, 5) for g, v in mu.items()}, sd={g: round(v, 5) for g, v in sd.items()},
                 train_loglik_per_game=round(ll, 4))
        if ck_rows:
            cobs = M.observations(ck_rows, which)
            _, _, _, res = M.run_filter(cobs, prm, mu, prior=state, collect=True)
            # naive benchmark: the player's season-to-date average (position average before his first game)
            seen = defaultdict(lambda: [0.0, 0.0])
            xs, ns, ys, ws = [], [], [], []
            for row, pre, post, y, scale in res:
                if y is None:
                    continue
                pid = row["player_id"]; grp = row["group"]
                s_, w_ = seen[pid]
                ns.append(s_ / w_ if w_ else mu[grp]); xs.append(pre[0]); ys.append(y); ws.append(1 / scale)
                seen[pid][0] += y / scale; seen[pid][1] += 1 / scale
            f["check"] = dict(games=len(ys), corr_rating_vs_next_game=round(corr(xs, ys, ws), 3),
                              corr_season_avg_vs_next_game=round(corr(ns, ys, ws), 3))
        filters[which] = f
        state_out[which] = {str(p): [round(v[0], 6), round(v[1], 8)] for p, v in state.items() if v[2] == last}
        print(which, json.dumps(f), file=sys.stderr)

    # --- RAPM: choose the ridge penalty by odd/even-game cross-validation on the first season, then daisy-chain
    stints = {s: {gid: M.stint_rows(P) for gid, _, P in g} for s, g in train.items()}
    first = a.train[0]
    ids = sorted(stints[first])
    odd = [st for i, g in enumerate(ids) if i % 2 for st in stints[first][g]]
    even = [st for i, g in enumerate(ids) if not i % 2 for st in stints[first][g]]
    errs = {lam: (M.rapm_error(odd, even, lam) + M.rapm_error(even, odd, lam)) / 2 for lam in (5, 10, 20, 40, 80, 160, 320)}
    lam = min(errs, key=errs.get)
    print("RAPM penalty", {k_: round(v, 4) for k_, v in errs.items()}, "->", lam, file=sys.stderr)
    prior = None
    for s in a.train:
        fit, _, _ = M.rapm([st for g in stints[s].values() for st in g], lam, prior)
        prior = {p: (v[0], v[1]) for p, v in fit.items()}
    rapm_out = {str(p): [round(v[0], 4), round(v[1], 4)] for p, v in prior.items()}

    os.makedirs(M.MODEL_DIR, exist_ok=True)
    model = dict(built=time.strftime("%Y-%m-%d"), trained_on=a.train, checked_on=a.check, xg=xg,
                 xg_onice_weight=round(k, 4), filters=filters, trade_games=M.TRADE_GAMES,
                 rapm=dict(penalty=lam, cv_error=errs), checks=checks)
    with open(os.path.join(M.MODEL_DIR, "model.json"), "w") as f:
        json.dump(model, f, indent=1)
    with open(os.path.join(M.MODEL_DIR, f"priors_{nxt}.json"), "w") as f:
        json.dump(dict(from_season=last, **state_out, rapm=rapm_out), f, separators=(",", ":"))
    print(f"done in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
