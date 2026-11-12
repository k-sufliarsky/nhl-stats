# NHL Game Ratings

A 0–10 rating for every NHL player in every game, plus a spoiler-free list of the latest games, standings, trends and line combinations.

**Live site:** https://k-sufliarsky.github.io/nhl-stats/

- **Latest games** opens first and hides scores until you reveal them. Recap and condensed-game videos open in the NHL's own player, and their titles name only the teams.
- **Up next** lists the next game day's schedule with start times in your time zone, national TV, each side's pre-game win chance and its best-rated player.
- **Teams and ratings**: pick a team by its logo for season averages, trend charts, who's trending up or down, line combinations, a game-by-game grid, single-game detail, upsets and standings.

The data refreshes automatically each night after the last game finishes (see [Automatic updates](#automatic-updates)).

## How the rating works

- **Skaters** get a Game Score (Dom Luszczyszyn's single-game metric): goals, primary and secondary assists, shots, blocks, penalties drawn and taken, faceoffs, 5-on-5 shot attempts for and against while on the ice, and goals for and against while on the ice.
- **Goalies** are rated on goals saved above expected. Expected goals come from a small model (shot distance, angle, rebounds, turnovers) fitted on every unblocked shot of the season.
- Each raw score is ranked against every NHL game at the same position this season, and the rank is placed on a bell curve centred on 5 (1.5 points per standard deviation), capped at 0 and 10. 5 is an average game, 7 beats about 91% of games, 8 about 98%, 9 about 99.6%, and 10 is reserved for a few games a season. Ranking first keeps big nights from piling up at 10, because Game Score is lopsided: most games are quiet and a few are huge.
- **Lines** are the three forwards and two defensemen who spent the most 5-on-5 time together in a game, from the NHL's shift charts.
- **Upsets** are wins by a team with under a 40% pre-game chance, estimated from points percentage (blended with last season's early on) plus a home-ice edge.

The rating only sees what the scoresheet and shot counts record, so treat one game's number as a summary, not a verdict.

## Run it yourself

Needs Python 3.10+ and numpy.

```bash
pip install -r requirements.txt
python3 nhl_ratings.py                 # current regular season, whole league -> site/data/
python3 nhl_ratings.py --team MTL      # also print one team's summary and write site/data/mtl_ratings.csv
python3 nhl_ratings.py --season 20252026
python3 nhl_ratings.py --playoffs
python3 -m http.server 8765 --directory site   # then open http://localhost:8765
```

API responses are cached in `cache/` (not committed), so re-runs only download new games.

## Automatic updates

`.github/workflows/update.yml` runs every 30 minutes between 02:00 and 13:30 UTC (9 PM to 8:30 AM US Eastern). Each run does a quick check with `should_update.py`. The full update runs only when:

1. every game of the latest game day (US Eastern date) is final,
2. at least `HOURS_AFTER_LAST_START` hours (default **3**) have passed since that day's last game started, and
3. that day hasn't been processed yet.

Then it recomputes everything, commits `site/data/`, and publishes the site to GitHub Pages. On a normal night with a 10:30 PM ET last puck drop, that's around 1:30–2 AM ET. Change `HOURS_AFTER_LAST_START` in the workflow to update sooner or later. To update right away, run the workflow by hand from the Actions tab with **force** ticked.

Recap videos are sometimes posted after the update runs. Games still missing a video are checked again on the next night's run.

**YouTube highlights (optional).** Each game links to the NHL's own highlights video on YouTube (youtube.com/@NHL, titled like "Flyers vs. Oilers | NHL Highlights | Nov 11, 2026"). To link straight to each video, add a free YouTube Data API key as a repository secret named `YOUTUBE_API_KEY`. In Google Cloud, enable "YouTube Data API v3", create an API key (ideally restricted to that API), then add it in Settings → Secrets and variables → Actions. Each nightly run uses a few of the 10,000 free daily quota units. Without a key, the newest games are still matched from the channel's public feed and the rest link to an exact-title YouTube search, whose other results may show scores.

**One-time setup:** in the repository's Settings → Pages, set Source to **GitHub Actions**.

## Project layout

| Path | What it is |
| --- | --- |
| `nhl_ratings.py` | Downloads NHL data, computes ratings, lines, upsets and standings, writes `site/data/` |
| `should_update.py` | Decides whether the nightly update should run |
| `site/` | The static website: `index.html`, `app.js`, `app.css`, `theme.css`, `runtime.js` (loads the data and draws the page) |
| `site/data/` | Generated data the site reads, including `nhl_upcoming.csv` (the next game day) |
| `site/vendor/d3.min.js` | d3.js 7.9.0, with its license |
| `dashboard/index.html` | Source of the claude.ai version of the dashboard. It only runs inside claude.ai, not on its own |

## Licensing and attribution

This is an independent, non-commercial fan project. It is not affiliated with, endorsed by or sponsored by the National Hockey League or any of its teams. What follows is a summary, not legal advice.

**NHL data.** Statistics, schedules, standings, shift charts and play-by-play come from the NHL's public web API (`api-web.nhle.com` and `api.nhle.com`). The API is unofficial and undocumented, and the NHL grants no license to use it. NHL.com's Terms of Service limit its content to personal, non-commercial use. The files in `site/data/` are derived from that data and carry the same restriction. Don't use them commercially (ads, paid products, betting) without a data license from the NHL or one of its official data distributors. The NHL can change or block the API at any time.

**Team names and logos.** NHL, team names and team logos are trademarks of the National Hockey League and its teams. They appear here only to identify teams. The logos are loaded from the NHL's own logo files and stored in `site/data/logos-*.json`. On a public site this is the biggest licensing risk: remove the logos if you take the project commercial or if the NHL objects.

**Videos.** Recap, condensed-game and goal clips are links to the NHL's own video player, NHL.com and the NHL's YouTube channel. No video is downloaded, copied or rehosted here.

**API documentation.** Endpoints were found with [NHL-API-Reference](https://github.com/Zmalski/NHL-API-Reference) by Zmalski, MIT License. That license covers its documentation only, not NHL data.

**Rating method.** The skater rating uses the Game Score formula published by Dom Luszczyszyn. The expected-goals model, line detection, upset model and 0–10 scale are this project's own.

**Third-party code.**
- [d3.js](https://d3js.org) 7.9.0, Copyright 2010–2023 Mike Bostock, ISC License. Copy in `site/vendor/d3-LICENSE.txt`.
- [numpy](https://numpy.org), BSD 3-Clause License (installed at run time, not included).
- GitHub Actions `actions/checkout`, `actions/setup-python`, `actions/cache`, `actions/configure-pages`, `actions/upload-pages-artifact` and `actions/deploy-pages`, MIT License (run by GitHub, not included).

**This project's own code** is released under the [MIT License](LICENSE). That license covers the code only (`nhl_ratings.py`, `should_update.py`, the site's HTML, CSS and JavaScript and the workflow), not the NHL data, logos or videos described above.
