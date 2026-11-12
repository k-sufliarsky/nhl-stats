#!/usr/bin/env python3
"""
Decide whether it's time to refresh the data: the GitHub workflow runs this every 30 minutes overnight.

It's time when the most recent NHL game day (US Eastern date) has finished, i.e.
  * every game that day is final, and
  * at least HOURS_AFTER_LAST_START hours have passed since the day's last game started
    (default 3: a game takes about 2.5 hours, plus a little time for the NHL to post recaps), and
  * that game day hasn't been processed yet (site/data/nhl_meta.json -> last_game_day).

Prints `update=true|false` and `game_day=YYYY-MM-DD` lines; in GitHub Actions they're also written to $GITHUB_OUTPUT.
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

HOURS = float(os.environ.get("HOURS_AFTER_LAST_START", "3"))
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
META = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "data", "nhl_meta.json")
ET = ZoneInfo("America/New_York")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nhl-ratings/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def games_on(day):
    week = get(f"https://api-web.nhle.com/v1/schedule/{day}")
    for d in week.get("gameWeek", []):
        if d["date"] == day:
            return [g for g in d["games"] if g.get("gameType") in (2, 3)]
    return []


def decide(now_utc):
    last_done = ""
    if os.path.exists(META):
        last_done = json.load(open(META)).get("last_game_day", "")
    today_et = now_utc.astimezone(ET).date()
    # The newest game day that could be over: today's (late at night) or yesterday's (after midnight ET).
    for day in (today_et, today_et - dt.timedelta(days=1)):
        day = day.isoformat()
        games = games_on(day)
        if not games:
            continue
        starts = [dt.datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00")) for g in games]
        if starts and now_utc < min(starts):
            continue                                    # this day hasn't started yet: look at the day before
        played = [g for g in games if g.get("gameScheduleState", "OK") == "OK"]   # skip postponed / cancelled
        final = all(g.get("gameState") in ("OFF", "FINAL") for g in played)
        ready_at = max(starts) + dt.timedelta(hours=HOURS)
        if FORCE:
            return True, day, "forced"
        if day <= last_done:
            return False, day, f"{day} already processed"
        if not final:
            return False, day, f"{day}: games still in progress"
        if now_utc < ready_at:
            return False, day, f"{day}: waiting until {ready_at:%H:%M} UTC ({HOURS:g} h after the last start)"
        return True, day, f"{day}: all {len(games)} games final"
    return FORCE, "", "no games in the last two days" + (" (forced)" if FORCE else "")


if __name__ == "__main__":
    update, day, why = decide(dt.datetime.now(dt.timezone.utc))
    print(why, file=sys.stderr)
    lines = [f"update={'true' if update else 'false'}", f"game_day={day}"]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("\n".join(lines) + "\n")
