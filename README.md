# NHL Game Ratings

A 0–10 rating for every NHL player in every game, plus a spoiler-free list of the latest games, standings, trends and line combinations.

**Live site:** https://k-sufliarsky.github.io/nhl-stats/

- **Latest games** opens first and hides scores until you reveal them. Recap and condensed-game videos open in the NHL's own player, and their titles name only the teams.
- **Up next** lists the next game day's schedule with start times in your time zone, national TV, each side's pre-game win chance and its best-rated player.
- **Teams and ratings**: pick a team by its logo for season averages, trend charts, who's trending up or down, line combinations, a game-by-game grid, single-game detail, upsets and standings.

The data refreshes automatically each night after the last game finishes (see [Automatic updates](#automatic-updates)).

## How the rating works

Two layers, following the usual split in hockey analytics: a **game score** describes one game, and a **rating** estimates how good a player is and is updated after every game. One game is a very noisy sample and goals are its noisiest part (split-half reliability of team goals is about a quarter of that of shot-based numbers), so both layers lean on shots and expected goals.

**Expected goals (xG).** A logistic model of whether an unblocked shot scores, from distance, angle, shot type, rebound, off a turnover, power play, shorthanded and empty net. It is trained on the two previous seasons and frozen (`model/model.json`), and checked on the current season's shots, which it never saw.

**Game score (layer 1).**
- **Skaters** get an *xG Game Score*: Dom Luszczyszyn's published Game Score (0.75·G + 0.7·A1 + 0.55·A2 + 0.075·SOG + 0.05·BLK + 0.15·PD − 0.15·PT + 0.01·FOW − 0.01·FOL + 0.05·CF − 0.05·CA + 0.15·GF − 0.15·GA, with CF/CA and GF/GA at 5-on-5 on ice), with goals replaced by individual expected goals and the on-ice terms replaced by on-ice 5-on-5 xG for and against. The on-ice xG weight is set so a season's totals match the published terms. The published Game Score is stored too (`gs`).
- **Goalies** get goals saved above expected (GSAx), and goalie Game Score (−0.75·GA + 0.1·SV) is stored.
- The site's 0–10 game score ranks each game against every game this season at the position (forwards, defense, goalies) and places the rank on a bell curve centred on 5 (1.5 points per standard deviation): 7 beats about 91% of games, 8 about 98%, 9 about 99.6%.

**Rating (layer 2).** A Kalman filter per player (`rating_model.py`):

```
offseason:    m = ρ·m_last + (1 − ρ)·μ_position      v = ρ²·v_last + q_season
each game:    v = v + q_game · (team games since the player's last game)   (+5 games' worth after a trade)
              R = s² · noise scale     K = v / (v + R)     m = m + K·(y − m)     v = (1 − K)·v
```

- **Rating** (skaters): y = xG Game Score, the same noise every game.
- **Play-driving** (skaters): y = on-ice 5-on-5 xG difference per 60 minutes; noise s² / 5-on-5 minutes, so a short night counts less.
- **Goalies**: y = GSAx per unblocked shot faced; noise s² / shots.

`s²`, `q_game`, `q_season`, `ρ` and the starting uncertainty for new players are fitted for each filter by maximising one-game-ahead predictive likelihood over the 2024-25 and 2025-26 seasons. Every player's rating at the end of 2025-26 is the starting point for 2026-27 (`model/priors_20262027.json`). On the site, ratings run 0–100: 50 is the position average and each 10 points is one standard deviation of last season's spread among regulars (70 ≈ top 2%). Game scores stay on 0–10.

**Teammate-adjusted impact.** A ridge regression (RAPM) of 5-on-5 xG per 60 on the ten skaters on the ice, shrunk toward last season's result, gives each skater's own offensive and defensive effect (`nhl_players.csv`: `adj_off`, `adj_def`, `adj_net`). The penalty is chosen by odd/even-game cross-validation.

**Checks.** `build_model.py` reports, and the site's method notes show: odd/even split-half reliability of each per-game number, the xG model's AUC on unseen shots, and whether the rating before a game predicts that game better than the season-to-date average. No published study shows that any per-game hockey score predicts the next game well; expect low game-to-game correlations.

- **Lines** are the three forwards and two defensemen who spent the most 5-on-5 time together in a game, from the NHL's shift charts.
- **Upsets** are wins by a team with under a 40% pre-game chance, estimated from points percentage (blended with last season's early on) plus a home-ice edge.

The numbers only see what play-by-play and shift charts record, and on-ice numbers depend on teammates. Treat one game as a summary, not a verdict.

**References.** Luszczyszyn, "Measuring Single Game Productivity: An Introduction to Game Score", Hockey Graphs (2016). Macdonald, Lennon & Sturdivant, "Evaluating NHL Goalies, Skaters, and Teams Using Weighted Shots" (arXiv 1205.1746). Macdonald, "Adjusted plus-minus for NHL players using ridge regression" (arXiv 1201.0317). Glickman & Stern, "A state-space model for National Football League scores", JASA (1998). Lopez, Matthews & Baumer, "How often does the best team win?", Annals of Applied Statistics (2018).

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

**Rebuilding the model** (once a season, locally; also needs scipy): `python3 build_model.py --train 20242025 20252026 --check 20262027` downloads the training seasons (about 2,600 games, roughly 1.5 GB of cache), refits the xG model and filter settings, and writes `model/`. The nightly update only reads `model/`.

## Automatic updates

`.github/workflows/update.yml` runs a quick check with `should_update.py` twice an hour overnight (US Eastern) and hourly the rest of the day, at :23 and :53. GitHub's scheduler is best-effort and often skips runs at :00 and :30; the daytime checks catch up any night it missed. Pushes to `main` run the check too. The full update runs only when:

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
| `rating_model.py` | The xG model, Kalman-filter ratings and RAPM |
| `build_model.py` | Fits the model on past seasons and writes `model/` (run locally once a season) |
| `model/` | Frozen xG model, fitted settings and checks (`model.json`), and each player's starting rating (`priors_<season>.json`) |
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
- [SciPy](https://scipy.org), BSD 3-Clause License (used only by `build_model.py` when refitting locally; not included).
- The rating method follows published research cited under "How the rating works"; no code or data from those works is copied.
- GitHub Actions `actions/checkout`, `actions/setup-python`, `actions/cache`, `actions/configure-pages`, `actions/upload-pages-artifact` and `actions/deploy-pages`, MIT License (run by GitHub, not included).

**This project's own code** is released under the [MIT License](LICENSE). That license covers the code only (`nhl_ratings.py`, `should_update.py`, the site's HTML, CSS and JavaScript and the workflow), not the NHL data, logos or videos described above.
