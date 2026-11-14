#!/usr/bin/env python3
"""
Decide whether it's time to refresh the data: the GitHub workflow runs this every 30 minutes overnight.

It's time when the most recent NHL game day (US Eastern date) has finished, i.e.
  * every game that day is final, and
  * at least HOURS_AFTER_LAST_START hours have passed since the day's last game started
    (default 3: a game takes about 2.5 hours, plus a little time for the NHL to post recaps), and
  * that game day hasn't been processed yet (site/data/nhl_meta.json -> last_game_day).

Prints `update=true|false` and `game_day=YYYY-MM-DD` lines; in GitHub Actions they're also written to $GITHUB_OUTPUT.

With --wait (used by scheduled runs): if the newest game day has started but isn't ready yet, keep checking every
10 minutes until it is, for at most WAIT_HOURS hours (default 5). GitHub's scheduler drops most runs, so one run
that fires mid-evening has to be enough to catch the night's games.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

HOURS = float(os.environ.get("HOURS_AFTER_LAST_START", "3"))
WAIT_HOURS = float(os.environ.get("WAIT_HOURS", "5"))
POLL_SECONDS = 600
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
    """(update, game_day, reason, ready_at) — ready_at is set only when waiting longer could turn this into an update."""
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
            return True, day, "forced", None
        if day <= last_done:
            return False, day, f"{day} already processed", None
        if not final:
            return False, day, f"{day}: games still in progress", ready_at
        if now_utc < ready_at:
            return False, day, f"{day}: waiting until {ready_at:%H:%M} UTC ({HOURS:g} h after the last start)", ready_at
        return True, day, f"{day}: all {len(games)} games final", None
    return FORCE, "", "no games in the last two days" + (" (forced)" if FORCE else ""), None


def now():
    return dt.datetime.now(dt.timezone.utc)


def decide_waiting(wait):
    deadline = now() + dt.timedelta(hours=WAIT_HOURS)
    while True:
        try:
            update, day, why, ready_at = decide(now())
        except OSError as e:                      # a flaky NHL API call shouldn't end a long wait
            if not wait or now() >= deadline:
                raise
            print(f"NHL API error ({e}); retrying", file=sys.stderr, flush=True)
            time.sleep(60)
            continue
        if update or not wait or ready_at is None or now() >= deadline:
            return update, day, why
        # sleep until the ready time if that's sooner, else poll
        pause = POLL_SECONDS if now() >= ready_at else min(POLL_SECONDS, (ready_at - now()).total_seconds() + 5)
        pause = max(30.0, min(pause, (deadline - now()).total_seconds()))
        print(f"{why}; checking again in {pause / 60:.0f} min", file=sys.stderr, flush=True)
        time.sleep(pause)


if __name__ == "__main__":
    update, day, why = decide_waiting("--wait" in sys.argv[1:])
    print(why, file=sys.stderr)
    lines = [f"update={'true' if update else 'false'}", f"game_day={day}"]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("\n".join(lines) + "\n")
