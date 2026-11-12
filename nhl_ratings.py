#!/usr/bin/env python3
"""
Per-game 0-10 player ratings for every NHL team, built on the public NHL API
(endpoints documented at https://github.com/Zmalski/NHL-API-Reference).

Skaters  -> Game Score (Dom Luszczyszyn's weights) using boxscore + play-by-play + shift charts.
Goalies  -> Goals Saved Above Expected (GSAx) from a small expected-goals model fitted on
            every unblocked shot in the NHL this season.
Both raw scores are ranked against every NHL player-game this season (same game type), separately for
forwards, defensemen and goalies, and the rank is placed on a bell curve centred on 5 (1.5 points per
standard deviation), clipped to [0, 10]. Ranking first keeps the scale honest even though Game Score is
lopsided (a few huge nights, many quiet ones):
    5 = average game, 7 = top ~9%, 8 = top ~2%, 9 = top ~0.4%, 10 = top ~0.05% (a few games a season).

Writes, for the whole league (site/data/, which the static site reads):
    nhl_ratings.csv    one row per player per game: rating and the stats behind it
    nhl_games.csv      one row per game: score, pre-game win chance, upset flag, video links
    nhl_goals.csv      one row per goal, with its highlight clip link
    nhl_lines.csv      forward lines and defense pairs per game, from 5-on-5 shift overlap
    nhl_standings.csv  current standings
    logos-*.js         team logos for the dashboard

Usage:
    python3 nhl_ratings.py                          # current regular season, whole league
    python3 nhl_ratings.py --season 20252026        # a past season
    python3 nhl_ratings.py --playoffs               # playoff games instead
    python3 nhl_ratings.py --team MTL               # also print a team summary and write mtl_ratings.csv
"""
import argparse, csv, json, math, os, re, sys, time, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")

# Game Score weights (Luszczyszyn, 2016)
W = dict(G=0.75, A1=0.70, A2=0.55, SOG=0.075, BLK=0.05, PD=0.15, PT=-0.15,
         FOW=0.01, FOL=-0.01, CF=0.05, CA=-0.05, GF=0.15, GA=-0.15)
SPREAD = 1.5          # rating points per standard deviation of the bell curve the ranks are placed on
CORSI = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}
FENWICK = {"shot-on-goal", "missed-shot", "goal"}


# ----------------------------------------------------------------------------- fetching
def fetch(url, cache_key=None, ttl=None):
    path = os.path.join(CACHE, cache_key) if cache_key else None
    if path and os.path.exists(path) and (ttl is None or time.time() - os.path.getmtime(path) < ttl):
        with open(path) as f:
            return json.load(f)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nhl-ratings/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    if path:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
    return data


def load_game(gid):
    box = fetch(f"{WEB}/gamecenter/{gid}/boxscore", f"{gid}_box.json")
    pbp = fetch(f"{WEB}/gamecenter/{gid}/play-by-play", f"{gid}_pbp.json")
    try:
        sh = fetch(f"{STATS}/shiftcharts?cayenneExp=gameId={gid}", f"{gid}_shifts.json")["data"]
        if not sh:   # shift charts are sometimes posted late: don't keep an empty answer
            os.remove(os.path.join(CACHE, f"{gid}_shifts.json"))
    except Exception:
        sh = []
    return gid, box, pbp, sh


# ----------------------------------------------------------------------------- helpers
def secs(mmss):
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def shot_geometry(play, shooter_is_home):
    d = play["details"]
    x, y = d.get("xCoord"), d.get("yCoord")
    side = play.get("homeTeamDefendingSide")
    if x is None or y is None or side not in ("left", "right"):
        return None
    # away team shoots at the net the home team defends; home shoots at the other one
    home_net_x = 89 if side == "right" else -89
    net_x = -home_net_x if shooter_is_home else home_net_x
    dx = abs(net_x - x)
    dist = math.hypot(dx, y)
    angle = math.degrees(math.atan2(abs(y), dx)) if dx > 0 else 90.0
    return dist, angle


def xg_features(dist, angle, rebound, rush):
    return [1.0, dist / 10, math.log1p(dist), angle / 30, float(rebound), float(rush)]


def fit_logit(X, y, l2=1.0, iters=50):
    X, y = np.asarray(X), np.asarray(y, float)
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


# ----------------------------------------------------------------------------- per-game parsing
def parse_game(gid, box, pbp, shifts):
    home_id, away_id = box["homeTeam"]["id"], box["awayTeam"]["id"]
    team_of, info = {}, {}
    for side, tid in (("homeTeam", home_id), ("awayTeam", away_id)):
        for grp in ("forwards", "defense", "goalies"):
            for p in box["playerByGameStats"][side][grp]:
                team_of[p["playerId"]] = tid
                info[p["playerId"]] = dict(p, team=tid, group={"forwards": "F", "defense": "D", "goalies": "G"}[grp])
    for r in pbp.get("rosterSpots", []):
        team_of.setdefault(r["playerId"], r["teamId"])

    # shifts -> {period: [(pid, start, end)]}
    on = defaultdict(list)
    for s in shifts:
        if s.get("typeCode") != 517 or not s.get("startTime") or not s.get("endTime"):
            continue
        on[s["period"]].append((s["playerId"], secs(s["startTime"]), secs(s["endTime"])))
    have_shifts = bool(on)

    def on_ice(period, t, faceoff=False):
        if faceoff:
            return [pid for pid, a, b in on[period] if a <= t < b]
        return [pid for pid, a, b in on[period] if a < t <= b]

    stat = defaultdict(lambda: defaultdict(float))
    shots = []          # unblocked shots for the xG model / goalie GSAx
    last_shot = {}      # team -> (period, t) of last shot attempt
    last_event = None

    for pl in pbp["plays"]:
        pd_ = pl["periodDescriptor"]
        if pd_.get("periodType") == "SO":
            continue
        kind, d = pl["typeDescKey"], pl.get("details") or {}
        per, t = pd_["number"], secs(pl["timeInPeriod"])
        sit = pl.get("situationCode") or ""

        if kind == "faceoff":
            if d.get("winningPlayerId"): stat[d["winningPlayerId"]]["FOW"] += 1
            if d.get("losingPlayerId"): stat[d["losingPlayerId"]]["FOL"] += 1

        elif kind == "penalty":
            if d.get("typeCode") in ("MIN", "MAJ", "BEN") or (d.get("duration") in (2, 4, 5) and d.get("typeCode") != "MIS"):
                if d.get("committedByPlayerId"): stat[d["committedByPlayerId"]]["PT"] += 1
                if d.get("drawnByPlayerId"): stat[d["drawnByPlayerId"]]["PD"] += 1

        if kind in CORSI:
            shooter = d.get("scoringPlayerId") or d.get("shootingPlayerId")
            team = team_of.get(shooter, d.get("eventOwnerTeamId"))
            opp = away_id if team == home_id else home_id

            if kind == "goal":
                stat[shooter]["G"] += 1
                if d.get("assist1PlayerId"): stat[d["assist1PlayerId"]]["A1"] += 1
                if d.get("assist2PlayerId"): stat[d["assist2PlayerId"]]["A2"] += 1
                for pid in on_ice(per, t):
                    if info.get(pid, {}).get("group") == "G":
                        continue
                    stat[pid]["GF" if team_of.get(pid) == team else "GA"] += 1

            if sit == "1551" and have_shifts:   # 5v5 Corsi
                for pid in on_ice(per, t):
                    if info.get(pid, {}).get("group") == "G":
                        continue
                    stat[pid]["CF" if team_of.get(pid) == team else "CA"] += 1

            if kind in FENWICK:
                geo = shot_geometry(pl, team == home_id)
                if geo:
                    prev = last_shot.get(team)
                    rebound = prev is not None and prev[0] == per and 0 <= t - prev[1] <= 3
                    rush = (last_event is not None and last_event[0] == per and 0 <= t - last_event[1] <= 4
                            and last_event[2] in ("takeaway", "giveaway") )
                    shots.append(dict(gid=gid, team=team, shooter=shooter, goalie=d.get("goalieInNetId"),
                                      goal=int(kind == "goal"), dist=geo[0], angle=geo[1],
                                      rebound=rebound, rush=rush))
            if kind == "shot-on-goal":      # only a saved shot can produce a rebound
                last_shot[team] = (per, t)
        last_event = (per, t, kind)

    return dict(home=home_id, away=away_id, info=info, stat=stat, shots=shots, have_shifts=have_shifts)


# ----------------------------------------------------------------------------- scoring
def player_rows(gid, P, box):
    """Raw score for every player (both teams) in one game."""
    abbrev = {box["homeTeam"]["id"]: box["homeTeam"]["abbrev"], box["awayTeam"]["id"]: box["awayTeam"]["abbrev"]}
    xga = defaultdict(float); ga = defaultdict(int); ixg = defaultdict(float)
    for s in P["shots"]:
        ixg[s["shooter"]] += s["xg"]
        if s["goalie"]:
            xga[s["goalie"]] += s["xg"]; ga[s["goalie"]] += s["goal"]
    out = []
    for pid, p in P["info"].items():
        toi = p.get("toi", "00:00")
        if secs(toi) == 0:
            continue
        st = P["stat"][pid]
        r = dict(game_id=gid, team=abbrev[p["team"]], player_id=pid, name=p["name"]["default"],
                 pos=p.get("position"), group=p["group"], toi=toi)
        if p["group"] == "G":
            r.update(SA=p.get("shotsAgainst", 0), SV=p.get("saves", 0), GA=ga[pid],
                     xGA=round(xga[pid], 2), raw=round(xga[pid] - ga[pid], 3),
                     svpct=p.get("savePctg"), decision=p.get("decision", ""))
        else:
            comp = dict(G=st["G"], A1=st["A1"], A2=st["A2"], SOG=p.get("sog", 0), BLK=p.get("blockedShots", 0),
                        PD=st["PD"], PT=st["PT"], FOW=st["FOW"], FOL=st["FOL"],
                        CF=st["CF"], CA=st["CA"], GF=st["GF"], GA=st["GA"])
            if not P["have_shifts"]:     # fall back to boxscore +/- when shift charts are missing
                pm = p.get("plusMinus", 0); comp["GF"], comp["GA"] = max(pm, 0), max(-pm, 0)
            r.update({k: int(v) for k, v in comp.items()})
            r.update(PTS=p.get("points", 0), PM=p.get("plusMinus", 0), HIT=p.get("hits", 0),
                     TK=p.get("takeaways", 0), GV=p.get("giveaways", 0), ixG=round(ixg[pid], 2),
                     raw=round(sum(W[k] * v for k, v in comp.items()), 3))
        out.append(r)
    return out


def league_game_ids(first_date, gtype, last_date=None):
    """Every completed NHL game of this type from first_date up to last_date (default today)."""
    ids, date = set(), first_date
    today = time.strftime("%Y-%m-%d")
    last_date = last_date or today
    while date and date <= last_date:
        import datetime as dt
        week_over = (dt.date.fromisoformat(date) + dt.timedelta(days=8)).isoformat() < today
        wk = fetch(f"{WEB}/schedule/{date}", f"week_{date}.json", ttl=None if week_over else 1800)
        for day in wk.get("gameWeek", []):
            for g in day["games"]:
                if g["gameType"] == gtype and g["gameState"] in ("OFF", "FINAL"):
                    ids.add(g["id"])
        date = wk.get("nextStartDate")
    return sorted(ids)



# ----------------------------------------------------------------------------- upsets & highlights
HOME_EDGE = 0.03   # home teams win ~53-55% of NHL games
PRIOR_GAMES = 10   # early in the season, lean on last season's points %


def pregame_strength(date, gtype):
    """Points % per team as of the morning of `date`, shrunk toward last season's final points %."""
    import datetime as dt
    day_before = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    seasons = fetch(f"{WEB}/standings-season", "standings_seasons.json", ttl=86400)["seasons"]
    cur = next((s for s in reversed(seasons) if s["standingsStart"] <= date), seasons[-1])
    prev = seasons[seasons.index(cur) - 1]
    prior = {t["teamAbbrev"]["default"]: t["pointPctg"]
             for t in fetch(f"{WEB}/standings/{prev['standingsEnd']}", f"standings_{prev['standingsEnd']}.json")["standings"]}
    now = {}
    if day_before >= cur["standingsStart"]:
        for t in fetch(f"{WEB}/standings/{day_before}", f"standings_{day_before}.json")["standings"]:
            if str(t.get("seasonId")) == str(cur["id"]):
                now[t["teamAbbrev"]["default"]] = (t.get("gamesPlayed", 0), t.get("pointPctg") or 0.5)
    out = {}
    for team in set(prior) | set(now):
        gp, pct = now.get(team, (0, 0.5))
        out[team] = (gp * pct + PRIOR_GAMES * prior.get(team, 0.5)) / (gp + PRIOR_GAMES)
    return out


def log5(a, b):
    a, b = min(max(a, 0.05), 0.95), min(max(b, 0.05), 0.95)
    return (a - a * b) / (a + b - 2 * a * b)


BC_PLAYER = "https://players.brightcove.net/6415718365001/D3UCGynRWU_default/index.html?videoId="  # NHL's own embeddable player


def game_videos(gid):
    """Recap and condensed-game video links. Their titles name only the teams, so they don't spoil the score."""
    key = f"{gid}_rail.json"
    path = os.path.join(CACHE, key)
    data = fetch(f"{WEB}/gamecenter/{gid}/right-rail", key)
    vids = data.get("gameVideo") or {}
    if not vids.get("threeMinRecap") and time.time() - os.path.getmtime(path) > 3600:
        os.remove(path)                       # videos are posted a few hours after the game; look again
        vids = fetch(f"{WEB}/gamecenter/{gid}/right-rail", key).get("gameVideo") or {}
    url = lambda v: f"{BC_PLAYER}{int(v)}" if str(v).isdigit() else ""
    return url(vids.get("threeMinRecap")), url(vids.get("condensedGame"))


def league_games(ids, boxes, pbps, by_game, gtype):
    games, goals = [], []
    with ThreadPoolExecutor(12) as ex:
        videos = dict(zip(ids, ex.map(game_videos, ids)))
    for gid in ids:
        box, pbp = boxes[gid], pbps[gid]
        h, aw = box["homeTeam"], box["awayTeam"]
        last = (box.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        st = pregame_strength(box["gameDate"], gtype)
        sh, sa = st.get(h["abbrev"], 0.5), st.get(aw["abbrev"], 0.5)
        p_home = log5(min(sh + HOME_EDGE, 0.95), sa)
        home_won = h["score"] > aw["score"]
        win, lose = (h, aw) if home_won else (aw, h)
        p_win = p_home if home_won else 1 - p_home
        best = {}
        for r in by_game[gid]:
            if r["team"] not in best or r["rating"] > best[r["team"]]["rating"]:
                best[r["team"]] = r
        d = box["gameDate"].split("-")
        gc = f"https://www.nhl.com/gamecenter/{aw['abbrev'].lower()}-vs-{h['abbrev'].lower()}/{d[0]}/{d[1]}/{d[2]}/{gid}"
        games.append(dict(
            game_id=gid, date=box["gameDate"], home=h["abbrev"], away=aw["abbrev"],
            home_score=h["score"], away_score=aw["score"], ended=last,
            final=f'{win["score"]}-{lose["score"]}' + ("" if last == "REG" else f" {last}"),
            winner=win["abbrev"], loser=lose["abbrev"],
            home_pregame_pts=round(sh, 3), away_pregame_pts=round(sa, 3),
            winner_pregame_prob=round(p_win, 3), winner_pregame_pct=round(p_win * 100), upset=p_win < 0.40,
            winner_star=best.get(win["abbrev"], {}).get("name", ""), winner_star_rating=best.get(win["abbrev"], {}).get("rating", ""),
            loser_star=best.get(lose["abbrev"], {}).get("name", ""), loser_star_rating=best.get(lose["abbrev"], {}).get("rating", ""),
            gamecenter_url=gc, start_utc=box.get("startTimeUTC", ""), venue=(box.get("venue") or {}).get("default", ""),
            recap_video_url=videos[gid][0], condensed_video_url=videos[gid][1]))
        names = {r["playerId"]: f'{r["firstName"]["default"]} {r["lastName"]["default"]}' for r in pbp.get("rosterSpots", [])}
        abbr = {h["id"]: h["abbrev"], aw["id"]: aw["abbrev"]}
        for pl in pbp["plays"]:
            if pl["typeDescKey"] != "goal" or pl["periodDescriptor"].get("periodType") == "SO":
                continue
            dd = pl["details"]; sit = pl.get("situationCode") or "1551"
            team = abbr.get(dd.get("eventOwnerTeamId"), "")
            is_home = team == h["abbrev"]
            mine, theirs = (int(sit[2]), int(sit[1])) if is_home else (int(sit[1]), int(sit[2]))
            opp_goalie = int(sit[0]) if is_home else int(sit[3])
            kind = "EN" if opp_goalie == 0 else ("PP" if mine > theirs else ("SH" if mine < theirs else "EV"))
            url = dd.get("highlightClipSharingUrl") or ""
            goals.append(dict(
                game_id=gid, period=pl["periodDescriptor"]["number"], period_type=pl["periodDescriptor"].get("periodType"),
                time=pl["timeInPeriod"], team=team, scorer=names.get(dd.get("scoringPlayerId"), ""),
                assists=", ".join(names[x] for x in (dd.get("assist1PlayerId"), dd.get("assist2PlayerId")) if x in names),
                strength=kind, score_after=f'{dd.get("awayScore")}-{dd.get("homeScore")} ({aw["abbrev"]}-{h["abbrev"]})',
                clip_url=url if url.startswith("https://nhl.com/") or url.startswith("https://www.nhl.com/") else ""))
    return games, goals



# ----------------------------------------------------------------------------- standings & logos
def fetch_bytes(url, cache_key):
    path = os.path.join(CACHE, cache_key)
    if os.path.exists(path):
        return open(path, "rb").read()
    req = urllib.request.Request(url, headers={"User-Agent": "nhl-ratings/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    os.makedirs(CACHE, exist_ok=True)
    open(path, "wb").write(data)
    return data


def svg_data_url(svg):
    """Strip comments, round coordinates to 0.1 (invisible at logo size) and URL-encode: much smaller than base64."""
    import urllib.parse
    svg = re.sub(r"<!--.*?-->|<\?xml.*?\?>|<metadata.*?</metadata>", "", svg, flags=re.S)
    svg = re.sub(r">\s+<", "><", svg).strip()
    def num(m):
        v = ("%.1f" % round(float(m.group(0)), 1)).rstrip("0").rstrip(".")
        v = v.replace("-0.", "-.") if v.startswith("-0.") else (v[1:] if v.startswith("0.") else v)
        return "0" if v in ("", "-0", "-") else v
    svg = re.sub(r"-?\d*\.\d+", num, svg)
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe=" /=:;,.-_'()!*~[]?@$")


def write_standings_and_logos(out_dir):
    import base64
    st = fetch(f"{WEB}/standings/now", "standings_now.json", ttl=3600)["standings"]
    rows, logos = [], {"light": {}, "dark": {}}   # variant -> {team: data URL}
    rec = lambda w, l, o: f"{w}-{l}-{o}"
    for t in st:
        ab = t["teamAbbrev"]["default"]
        rows.append(dict(
            team=ab, name=t["teamName"]["default"], conference=t["conferenceName"], division=t["divisionName"],
            league_rank=t["leagueSequence"], conference_rank=t["conferenceSequence"], division_rank=t["divisionSequence"],
            wildcard_rank=t.get("wildcardSequence", 0),
            gp=t["gamesPlayed"], w=t["wins"], l=t["losses"], otl=t["otLosses"], pts=t["points"],
            pts_pct=round(t["pointPctg"], 3), rw=t["regulationWins"], gf=t["goalFor"], ga=t["goalAgainst"],
            diff=t["goalDifferential"], home=rec(t["homeWins"], t["homeLosses"], t["homeOtLosses"]),
            road=rec(t["roadWins"], t["roadLosses"], t["roadOtLosses"]),
            l10=rec(t["l10Wins"], t["l10Losses"], t["l10OtLosses"]),
            streak=f'{t.get("streakCode", "")}{t.get("streakCount", "")}', as_of=t["date"]))
        for variant, key in (("light", "teamLogo"), ("dark", "teamLogoDark")):
            url = t.get(key) or ""
            if url.startswith("https://assets.nhle.com/") and url.endswith(".svg"):
                svg = fetch_bytes(url, f"logo_{ab}_{variant}.svg")
                logos[variant][ab] = svg_data_url(svg.decode("utf-8", "replace"))
    rows.sort(key=lambda r: r["league_rank"])
    with open(os.path.join(out_dir, "nhl_standings.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
    # one JSON file per theme; the dashboard loads only the one the reader's theme needs
    for variant, m in logos.items():
        with open(os.path.join(out_dir, f"logos-{variant}.json"), "w") as f:
            json.dump(m, f, separators=(",", ":"))
    return rows



# ----------------------------------------------------------------------------- line combinations
def lines_for_game(gid, box, pbp, shifts, rating_of):
    """Forward lines and defense pairs from 5-on-5 shift overlap, with what happened while each unit was on the ice."""
    info = {}
    for side in ("homeTeam", "awayTeam"):
        tid = box[side]["id"]
        for grp, g in (("forwards", "F"), ("defense", "D"), ("goalies", "G")):
            for p in box["playerByGameStats"][side][grp]:
                info[p["playerId"]] = (tid, g, p["name"]["default"], p.get("position"))
    abbrev = {box["homeTeam"]["id"]: box["homeTeam"]["abbrev"], box["awayTeam"]["id"]: box["awayTeam"]["abbrev"]}
    per = defaultdict(list)
    for s in shifts:
        if s.get("typeCode") == 517 and s.get("startTime") and s.get("endTime") and s["playerId"] in info:
            a, b = secs(s["startTime"]), secs(s["endTime"])
            if b > a:
                per[s["period"]].append((s["playerId"], a, b))
    if not per:
        return []
    together = {tid: (defaultdict(int), defaultdict(int)) for tid in abbrev}   # (forward trios, D pairs) -> seconds at 5v5
    for period, sh in per.items():
        cuts = sorted({a for _, a, _ in sh} | {b for _, _, b in sh})
        for t0, t1 in zip(cuts, cuts[1:]):
            on = [pid for pid, a, b in sh if a <= t0 and b >= t1]
            groups = {tid: {"F": [], "D": [], "G": []} for tid in abbrev}
            for pid in on:
                tid, g, _, _ = info[pid]
                groups[tid][g].append(pid)
            if any(len(v["G"]) != 1 or len(v["F"]) + len(v["D"]) != 5 for v in groups.values()):
                continue
            for tid, v in groups.items():
                if len(v["F"]) == 3 and len(v["D"]) == 2:
                    together[tid][0][frozenset(v["F"])] += t1 - t0
                    together[tid][1][frozenset(v["D"])] += t1 - t0
    units = []
    for tid, (trios, pairs) in together.items():
        for kind, combos, n in (("F", trios, 4), ("D", pairs, 3)):
            used, picked = set(), []
            for combo, sec in sorted(combos.items(), key=lambda kv: -kv[1]):
                if sec >= 60 and not (combo & used) and len(picked) < n:
                    picked.append((combo, sec)); used |= combo
            for i, (combo, sec) in enumerate(picked, 1):
                order = {"L": 0, "C": 1, "R": 2, "D": 3}
                members = sorted(combo, key=lambda pid: (order.get(info[pid][3], 9), info[pid][2]))
                units.append(dict(team_id=tid, team=abbrev[tid], kind=kind, unit=f"{kind}{i}", members=members, toi=sec,
                                  CF=0, CA=0, GF=0, GA=0))
    # what happened at 5v5 while each unit was together on the ice
    for pl in pbp["plays"]:
        if pl["typeDescKey"] not in CORSI or pl.get("situationCode") != "1551" or pl["periodDescriptor"].get("periodType") == "SO":
            continue
        d = pl.get("details") or {}
        shooter = d.get("scoringPlayerId") or d.get("shootingPlayerId")
        team = info.get(shooter, (d.get("eventOwnerTeamId"),))[0]
        t = secs(pl["timeInPeriod"])
        on = {pid for pid, a, b in per.get(pl["periodDescriptor"]["number"], []) if a < t <= b}
        for u in units:
            if set(u["members"]) <= on:
                mine = u["team_id"] == team
                u["CF" if mine else "CA"] += 1
                if pl["typeDescKey"] == "goal":
                    u["GF" if mine else "GA"] += 1
    out = []
    for u in units:
        rs = [rating_of.get((gid, pid)) for pid in u["members"]]
        rs = [r for r in rs if r is not None]
        out.append(dict(game_id=gid, date=box["gameDate"], team=u["team"], unit=u["unit"], kind=u["kind"],
                        player_ids="|".join(str(p) for p in u["members"]),
                        names=" / ".join(info[p][2] for p in u["members"]),
                        positions="/".join(info[p][3] or "" for p in u["members"]),
                        toi_5v5=round(u["toi"]), CF=u["CF"], CA=u["CA"], GF=u["GF"], GA=u["GA"],
                        unit_rating=round(sum(rs) / len(rs), 2) if rs else ""))
    return out


# ----------------------------------------------------------------------------- main
def season_window(season):
    """(season id, first day, last day to look at) from the NHL's list of seasons."""
    seasons = fetch(f"{WEB}/standings-season", "standings_seasons.json", ttl=86400)["seasons"]
    today = time.strftime("%Y-%m-%d")
    if season == "now":
        cur = next(s for s in reversed(seasons) if s["standingsStart"] <= today)
    else:
        cur = next((s for s in seasons if str(s["id"]) == str(season)), None)
        if not cur:
            sys.exit(f"Unknown season {season}")
    import datetime as dt
    last = (dt.date.fromisoformat(cur["standingsEnd"]) + dt.timedelta(days=75)).isoformat()  # leaves room for playoffs
    return cur["id"], cur["standingsStart"], min(today, last)


def main():
    ap = argparse.ArgumentParser(description="Per-game 0-10 player ratings for the whole NHL.")
    ap.add_argument("--season", default="now", help="e.g. 20252026 (default: current season)")
    ap.add_argument("--playoffs", action="store_true", help="rate playoff games instead of the regular season")
    ap.add_argument("--team", help="team code such as MTL: also print its summary and write <team>_ratings.csv")
    ap.add_argument("--out", default=os.path.join(HERE, "site", "data"), help="where to write the files (default: site/data)")
    a = ap.parse_args()
    if a.team:
        a.team = a.team.upper()

    gtype = 3 if a.playoffs else 2
    season_id, first_day, last_day = season_window(a.season)
    all_ids = [g for g in league_game_ids(first_day, gtype, last_day) if str(g)[:4] == str(season_id)[:4]]
    if not all_ids:
        sys.exit("No completed games found.")
    print(f"NHL {season_id} {'playoffs' if a.playoffs else 'regular season'}: {len(all_ids)} completed games", file=sys.stderr)

    with ThreadPoolExecutor(12) as ex:
        raw = list(ex.map(load_game, all_ids))
    parsed = {gid: parse_game(gid, box, pbp, sh) for gid, box, pbp, sh in raw}
    boxes = {gid: box for gid, box, _, _ in raw}

    # --- expected goals model, fitted on every unblocked shot in the calibration games
    all_shots = [s for g in parsed.values() for s in g["shots"]]
    X = [xg_features(s["dist"], s["angle"], s["rebound"], s["rush"]) for s in all_shots]
    y = [s["goal"] for s in all_shots]
    w = fit_logit(X, y)
    xg = 1 / (1 + np.exp(-np.asarray(X) @ w))
    for s, v in zip(all_shots, xg):
        s["xg"] = float(v)
    print(f"xG model: {len(all_shots)} unblocked shots, {sum(y)} goals", file=sys.stderr)

    rows = [r for gid in all_ids for r in player_rows(gid, parsed[gid], boxes[gid])]

    # --- 0-10: rank every player-game within its position group, then place the rank on a bell curve.
    # (A straight z-score sent ~1 in 60 forward games to 10, because Game Score has a long right tail.)
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
                r["rating"] = round(min(10.0, max(0.0, 5 + SPREAD * bell.inv_cdf(pct))), 1)
            i = j + 1

    # --- game context for every row, from each player's own team's point of view
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["game_id"]].append(r)
    for gid in all_ids:
        box = boxes[gid]
        last = (box.get("gameOutcome") or {}).get("lastPeriodType", "REG")
        for r in by_game[gid]:
            is_home = box["homeTeam"]["abbrev"] == r["team"]
            me, opp = (box["homeTeam"], box["awayTeam"]) if is_home else (box["awayTeam"], box["homeTeam"])
            res = "W" if me["score"] > opp["score"] else ("OTL" if last in ("OT", "SO") else "L")
            r.update(date=box["gameDate"], opp=opp["abbrev"], home=is_home, score=f'{me["score"]}-{opp["score"]}',
                     result=res + ("" if last == "REG" else f" ({last})"))
    os.makedirs(a.out, exist_ok=True)
    keys = ["date", "team", "opp", "home", "score", "result", "name", "pos", "toi", "rating", "pct", "raw",
            "G", "A1", "A2", "PTS", "SOG", "ixG", "BLK", "PD", "PT", "FOW", "FOL", "CF", "CA", "GF", "GA", "PM",
            "HIT", "TK", "GV", "SA", "SV", "xGA", "svpct", "decision", "game_id", "player_id"]
    def write_csv(name, data, cols):
        with open(os.path.join(a.out, name), "w", newline="") as f:
            wr = csv.DictWriter(f, cols, extrasaction="ignore"); wr.writeheader(); wr.writerows(data)

    write_csv("nhl_ratings.csv", sorted(rows, key=lambda r: (r["date"], r["game_id"], r["team"])), keys)
    games_out, goals_out = league_games(all_ids, boxes, {gid: pbp for gid, _, pbp, _ in raw}, by_game, gtype)
    write_csv("nhl_games.csv", games_out, list(games_out[0].keys()))
    write_csv("nhl_goals.csv", goals_out, list(goals_out[0].keys()))
    rating_of = {(r["game_id"], r["player_id"]): r["rating"] for r in rows}
    lines = [u for gid, box, pbp, sh in raw for u in lines_for_game(gid, box, pbp, sh, rating_of)]
    write_csv("nhl_lines.csv", lines, list(lines[0].keys()))
    standings = write_standings_and_logos(a.out)
    meta = dict(season=str(season_id), playoffs=a.playoffs, games=len(all_ids), generated=time.strftime("%Y-%m-%d"),
                generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), last_game_day=max(g["date"] for g in games_out),
                reference=ref, scale="rank -> 5 + %g * normal quantile" % SPREAD,
                xg_shots=len(all_shots), xg_coef=[round(float(c), 4) for c in w], weights=W)
    with open(os.path.join(a.out, "nhl_meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    ups = sorted((g for g in games_out if g["upset"]), key=lambda g: g["winner_pregame_prob"])
    print(f"Written to {a.out}: {len(rows)} player-games, {len(games_out)} games, {len(goals_out)} goals, "
          f"{len(lines)} line units, standings as of {standings[0]['as_of']}", file=sys.stderr)
    for g in ups[:5]:
        print(f'  upset {g["date"]} {g["winner"]} beat {g["loser"]} {g["final"]} (pre-game win chance {g["winner_pregame_prob"]:.0%})', file=sys.stderr)

    # --- console summary: league leaders, or one team
    def table(rows_, title, min_gp):
        agg = defaultdict(list)
        for r in rows_:
            agg[(r["name"], r["team"], r["pos"])].append(r["rating"])
        print(f"\n{title}\n{'Player':<22}{'Team':<5}{'Pos':<4}{'GP':>3}{'Avg':>6}{'Best':>6}{'Worst':>7}")
        ranked = sorted(((k, v) for k, v in agg.items() if len(v) >= min_gp), key=lambda kv: -np.mean(kv[1]))
        for (n, t, pos), v in ranked[: None if a.team else 20]:
            print(f"{n:<22}{t:<5}{pos:<4}{len(v):>3}{np.mean(v):>6.2f}{max(v):>6.1f}{min(v):>7.1f}")
    if a.team:
        team_rows = [r for r in rows if r["team"] == a.team]
        if not team_rows:
            sys.exit(f"No games found for team {a.team}")
        write_csv(f"{a.team.lower()}_ratings.csv", team_rows, keys)
        table(team_rows, f"{a.team}: average rating per player", 1)
    else:
        min_gp = max(1, len(all_ids) * 2 // 32 // 2)   # at least half of a team's games
        table(rows, f"League leaders (at least {min_gp} games)", min_gp)


if __name__ == "__main__":
    main()
