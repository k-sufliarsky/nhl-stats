(function () {
  const fmt1 = d3.format('.1f');
  const fmt2 = d3.format('.2f');
  const dateFmt = d3.utcFormat('%b %-d');
  const longDate = d3.utcFormat('%B %-d, %Y');
  const parseDate = d3.utcParse('%Y-%m-%d');
  const safeUrl = (u) => (/^https:\/\/((www\.)?nhl\.com\/|players\.brightcove\.net\/6415718365001\/)/.test(u || '') ? u : null);

  const hrColor = (v) => {
    if (v == null || isNaN(v)) return 'transparent';
    const pct = Math.round(8 + Math.min(1, Math.abs(v - 5) / 4.5) * 52);
    return 'color-mix(in oklab, var(' + (v >= 5 ? '--color-ok' : '--color-bad') + ') ' + pct + '%, var(--color-panel))';
  };
  const team = () => dash.params().team || 'MTL';
  // Index a dataset once per load instead of filtering 11,000 rows on every redraw.
  const indexCache = new WeakMap();
  function groupOf(rows, name, keyFn) {
    let m = indexCache.get(rows);
    if (!m) { m = {}; indexCache.set(rows, m); }
    return m[name] || (m[name] = d3.group(rows, keyFn));
  }
  // Remember what each drawing was last built from, so a team change only updates what depends on the team.
  const builtFrom = new WeakMap();
  function unchanged(el, key) {
    const prev = builtFrom.get(el);
    builtFrom.set(el, key);
    return !!prev && el.childNodes.length > 0 && prev.length === key.length && prev.every((v, i) => v === key[i]);
  }
  const state = { game: null, upset: null, team: null, player: null };
  function syncTeam() {
    if (state.team !== team()) { state.team = team(); state.game = null; state.upset = null; state.player = null; }
  }

  dash.calc('season', {
    title: 'Season averages by player',
    description: 'Games played, average, last-5 average, best and worst per-game rating, change (last 5 minus season) and spread (standard deviation) for each player on each team.',
    inputs: ['ratings'],
    fn: (ratings) => {
      const byPlayer = new Map();
      for (const r of ratings) {
        const key = r.team + ',' + r.player_id;
        if (!byPlayer.has(key)) byPlayer.set(key, { team: r.team, player_id: r.player_id, name: r.name, pos: r.pos, games: [] });
        byPlayer.get(key).games.push({ date: r.date, rating: +r.rating });
        byPlayer.get(key).pos = r.pos;
      }
      const round2 = (x) => Math.round(x * 100) / 100;
      const mean = (a) => a.reduce((s, x) => s + x, 0) / a.length;
      return [...byPlayer.values()].map((p) => {
        const ratingsInOrder = p.games.sort((a, b) => (a.date < b.date ? -1 : 1)).map((g) => g.rating);
        return {
          team: p.team, player_id: p.player_id, name: p.name, pos: p.pos, gp: ratingsInOrder.length,
          avg: round2(mean(ratingsInOrder)), last5: round2(mean(ratingsInOrder.slice(-5))),
          best: Math.max(...ratingsInOrder), worst: Math.min(...ratingsInOrder), group: p.pos === 'G' ? 'G' : (p.pos === 'D' ? 'D' : 'F'),
          delta: round2(mean(ratingsInOrder.slice(-5)) - mean(ratingsInOrder)),
          spread: round2(Math.sqrt(mean(ratingsInOrder.map((x) => (x - mean(ratingsInOrder)) ** 2)))),
        };
      }).sort((a, b) => b.avg - a.avg);
    },
  });

  dash.calc('summary', {
    title: 'Headline numbers per team',
    description: 'For each team: games rated, the leading regular (played at least half the games) and the top-rated player in its latest game.',
    inputs: ['ratings', 'season'],
    fn: (ratings, season) => {
      const out = [];
      for (const [teamCode, rows] of d3.group(ratings, (r) => r.team)) {
        const games = new Set(rows.map((r) => r.game_id)).size;
        const players = season.filter((p) => p.team === teamCode);
        const regulars = players.filter((p) => p.gp >= games / 2);
        const leader = (regulars.length ? regulars : players)[0] || {};
        const lastDate = d3.max(rows, (r) => r.date);
        const last = rows.filter((r) => r.date === lastDate);
        const best = last.reduce((a, b) => (+b.rating > +a.rating ? b : a), last[0]);
        out.push({
          team: teamCode, games, leader: leader.name, leader_avg: leader.avg,
          last_date: lastDate, last_opp: best.opp, last_home: best.home, last_score: best.score, last_result: best.result,
          last_best: best.name, last_best_rating: +best.rating,
        });
      }
      return out;
    },
  });

  function mark(tag, source, field, where, text) {
    const el = document.createElement(tag);
    el.dataset.source = source; el.dataset.field = field; el.dataset.where = where;
    el.textContent = text;
    return el;
  }
  function summary() {
    const s = dash.data('summary');
    return s.status === 'ok' && s.data ? s.data.find((r) => r.team === team()) || null : null;
  }
  function gameLabel(r) {
    const d = parseDate(r.date || r.last_date);
    const home = String(r.home ?? r.last_home) === 'True';
    return (d ? dateFmt(d) : '') + ', ' + (r.result || r.last_result) + ' ' + (r.score || r.last_score) + (home ? ' vs ' : ' at ') + (r.opp || r.last_opp);
  }
  const textDraw = (name, pick) => dash.draw(name, (el) => {
    const s = summary();
    el.textContent = s ? pick(s) : '—';
  });
  const markDraw = (name, field, show) => dash.draw(name, (el) => {
    const s = summary();
    if (!s) { el.textContent = '—'; return; }
    el.replaceChildren(mark('b', 'summary', field, 'team=' + encodeURIComponent(s.team), show(s[field])));
  });
  textDraw('leader', (s) => s.leader);
  textDraw('teamName', (s) => s.team);
  markDraw('leaderAvg', 'leader_avg', fmt2);
  markDraw('games', 'games', String);
  textDraw('lastGame', (s) => gameLabel(s));
  textDraw('lastBest', (s) => s.last_best);
  markDraw('lastBestRating', 'last_best_rating', fmt1);

  function chip(v, text) {
    const span = document.createElement('span');
    span.className = 'hr-chip';
    span.style.background = hrColor(v);
    span.textContent = text ?? (v == null ? '—' : fmt1(v));
    return span;
  }

  dash.draw('seasonRows', (tb) => {
    const s = dash.data('season');
    if (s.status === 'ok' && s.data && unchanged(tb, [s.data, team()])) return;
    tb.replaceChildren();
    if (s.status !== 'ok' || !s.data) return;
    for (const p of teamPlayers()) {
      const tr = document.createElement('tr');
      tr.dataset.key = p.team + ',' + p.player_id;
      const td = (txt, cls) => { const c = document.createElement('td'); if (cls) c.className = cls; c.textContent = txt; tr.appendChild(c); return c; };
      td(p.name, 'l'); td(p.pos, 'l'); td(String(p.gp));
      const avg = document.createElement('td');
      const wrap = document.createElement('span'); wrap.className = 'hr-bar';
      const bar = document.createElement('i'); const fill = document.createElement('b');
      fill.style.width = (p.avg * 10) + '%';
      bar.appendChild(fill); wrap.append(bar, chip(p.avg, fmt2(p.avg)));
      avg.appendChild(wrap); tr.appendChild(avg);
      const l5 = document.createElement('td'); l5.appendChild(chip(p.last5, fmt2(p.last5))); tr.appendChild(l5);
      td(fmt1(p.best)); td(fmt1(p.worst));
      tr.className = 'pick'; tr.tabIndex = 0;
      tr.addEventListener('click', () => openPlayer(p.player_id));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPlayer(p.player_id); } });
      tb.appendChild(tr);
    }
  });

  dash.draw('legend', (el) => {
    el.replaceChildren();
    const lab = document.createElement('span'); lab.textContent = 'Rating'; el.appendChild(lab);
    for (const [v, t] of [[1, '0–2 rough'], [3.5, '3–4'], [5, '5 average'], [6.5, '6–7'], [8.5, '8+ standout']]) el.appendChild(chip(v, t));
  });

  function teamRows() {
    const d = dash.data('ratings');
    return d.status === 'ok' && d.data ? groupOf(d.data, 'team', (r) => r.team).get(team()) || [] : null;
  }

  dash.draw('heat', (el) => {
    const d = dash.data('ratings');
    const s = dash.data('season');
    if (d.status === 'ok' && s.status === 'ok' && s.data && unchanged(el, [d.data, s.data, team()])) return;
    el.replaceChildren();
    if (d.status === 'error') { el.textContent = 'Failed to load'; return; }
    const rows = teamRows();
    if (!rows || s.status !== 'ok' || !s.data) { el.textContent = '—'; return; }
    const games = d3.sort(d3.groups(rows, (r) => r.game_id), (g) => g[1][0].date).map(([id, rs]) => ({ id, r: rs[0] }));
    const cell = new Map(rows.map((r) => [r.game_id + '|' + r.player_id, r]));
    const table = document.createElement('table');
    const thead = table.createTHead().insertRow();
    const corner = document.createElement('th'); corner.className = 'nm'; corner.textContent = 'Player'; thead.appendChild(corner);
    for (const g of games) {
      const th = document.createElement('th');
      const d0 = parseDate(g.r.date);
      const res = g.r.result.split(' ')[0];
      th.append(d0 ? dateFmt(d0) : '', document.createElement('br'), (String(g.r.home) === 'True' ? '' : '@') + g.r.opp, document.createElement('br'));
      const rs = document.createElement('span'); rs.textContent = res; rs.className = res === 'W' ? 'w' : 'lo'; th.appendChild(rs);
      thead.appendChild(th);
    }
    const tbody = table.createTBody();
    const tip = document.createElement('div'); tip.className = 'hr-tip'; tip.hidden = true;
    for (const p of teamPlayers()) {
      const tr = tbody.insertRow();
      const nm = document.createElement('th'); nm.className = 'nm'; nm.textContent = p.name; tr.appendChild(nm);
      for (const g of games) {
        const r = cell.get(g.id + '|' + p.player_id);
        const td = tr.insertCell();
        td.className = 'c' + (r ? '' : ' empty');
        if (!r) { td.textContent = '·'; continue; }
        const v = +r.rating;
        td.textContent = fmt1(v);
        td.style.background = hrColor(v);
        td.tabIndex = 0;
        td.dataset.source = 'ratings'; td.dataset.field = 'rating';
        td.dataset.where = 'game_id=' + r.game_id + '&player_id=' + r.player_id;
        const show = () => {
          tip.replaceChildren();
          const h = document.createElement('strong'); h.textContent = r.name + ' · ' + fmt1(v); tip.appendChild(h);
          const sub = document.createElement('span'); sub.textContent = gameLabel(r) + ' · TOI ' + r.toi; tip.appendChild(sub);
          tip.appendChild(document.createElement('br'));
          const line = document.createElement('span');
          line.textContent = r.pos === 'G'
            ? (r.SV + ' saves on ' + r.SA + ' shots, ' + r.GA + ' GA vs ' + r.xGA + ' expected')
            : (r.G + ' G, ' + (+r.A1 + +r.A2) + ' A, ' + r.SOG + ' SOG, ' + r.BLK + ' BLK · 5v5 attempts ' + r.CF + '–' + r.CA + ' · on-ice goals ' + r.GF + '–' + r.GA);
          tip.appendChild(line);
          tip.hidden = false;
          const box = el.getBoundingClientRect(), c = td.getBoundingClientRect();
          let x = c.left - box.left + el.scrollLeft + c.width / 2 - 130;
          x = Math.max(el.scrollLeft + 4, Math.min(x, el.scrollLeft + el.clientWidth - 264));
          tip.style.left = x + 'px';
          tip.style.top = (c.bottom - box.top + el.scrollTop + 4) + 'px';
        };
        td.addEventListener('mouseenter', show);
        td.addEventListener('focus', show);
        td.addEventListener('click', show);
        td.addEventListener('mouseleave', () => { tip.hidden = true; });
        td.addEventListener('blur', () => { tip.hidden = true; });
      }
    }
    el.append(table, tip);
  });

  function gamesList() {
    const rows = teamRows();
    if (!rows) return [];
    return d3.sort(d3.groups(rows, (r) => r.game_id), (g) => g[1][0].date).reverse().map(([id, rs]) => ({ id, r: rs[0] }));
  }
  function currentGame() {
    syncTeam();
    const list = gamesList();
    return list.find((g) => g.id === state.game) || list[0] || null;
  }
  function gameInfo(id) {
    const g = dash.data('games');
    return g.status === 'ok' ? g.data.find((r) => r.game_id === id) || null : null;
  }
  function link(text, url) {
    const a = document.createElement('a');
    a.textContent = text; a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    return a;
  }

  dash.draw('gamePick', (sel) => {
    const list = gamesList();
    sel.replaceChildren(...list.map((g) => new Option(gameLabel(g.r), g.id)));
    const cur = currentGame();
    if (cur) sel.value = cur.id;
    sel.onchange = () => {
      state.game = sel.value;
      dash.draw('gameLinks'); dash.draw('gameSkaters'); dash.draw('gameGoalies'); dash.draw('gameLines');
    };
  });

  dash.draw('gameLinks', (el) => {
    const g = currentGame();
    const info = g && gameInfo(g.id);
    const url = info && safeUrl(info.gamecenter_url);
    if (!url) { el.textContent = '—'; return; }
    el.replaceChildren(link('Recap and highlights on NHL.com', url));
  });

  function gameRows(goalies) {
    const g = currentGame(); const rows = teamRows();
    if (!rows || !g) return [];
    return rows.filter((r) => r.game_id === g.id && (r.pos === 'G') === goalies).sort((a, b) => +b.rating - +a.rating);
  }
  function fillRows(tb, rows, cols) {
    tb.replaceChildren();
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.dataset.key = r.game_id + ',' + r.player_id;
      for (const c of cols) {
        const td = document.createElement('td');
        if (c === 'name' || c === 'pos') td.className = 'l';
        if (c === 'rating') td.appendChild(chip(+r.rating));
        else td.textContent = r[c] === '' || r[c] == null ? '—' : r[c];
        tr.appendChild(td);
      }
      tb.appendChild(tr);
    }
  }
  dash.draw('gameSkaters', (tb) => fillRows(tb, gameRows(false),
    ['name', 'pos', 'toi', 'rating', 'G', 'A1', 'A2', 'SOG', 'ixG', 'BLK', 'CF', 'CA', 'GF', 'GA', 'PD', 'PT', 'FOW', 'FOL']));
  dash.draw('gameGoalies', (tb) => fillRows(tb, gameRows(true),
    ['name', 'toi', 'rating', 'SA', 'SV', 'GA', 'xGA', 'decision']));

  // ---------------------------------------------------------------- trends
  dash.calc('rolling', {
    title: 'Rolling five-game average',
    description: "Each player's rating in every game, with the average of that game and the four before it.",
    inputs: ['ratings'],
    fn: (ratings) => {
      const out = [];
      for (const rows of d3.group(ratings, (r) => r.team + ',' + r.player_id).values()) {
        const games = rows.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
        games.forEach((r, i) => {
          const window5 = games.slice(Math.max(0, i - 4), i + 1);
          out.push({
            team: r.team, player_id: r.player_id, game_id: r.game_id, date: r.date, opp: r.opp, home: r.home,
            result: r.result, score: r.score, rating: +r.rating,
            roll5: Math.round(d3.mean(window5, (x) => +x.rating) * 100) / 100,
          });
        });
      }
      return out;
    },
  });

  dash.calc('teamGames', {
    title: 'Team rating per game',
    description: "Each team's skater ratings in a game, averaged with ice time as the weight, plus a rolling five-game average.",
    inputs: ['ratings'],
    fn: (ratings) => {
      const seconds = (toi) => { const [m, s] = String(toi).split(':'); return +m * 60 + +s; };
      const out = [];
      for (const rows of d3.group(ratings, (r) => r.team).values()) {
        const games = [];
        for (const g of d3.group(rows, (r) => r.game_id).values()) {
          const skaters = g.filter((r) => r.pos !== 'G');
          const toi = d3.sum(skaters, (r) => seconds(r.toi));
          const r0 = g[0];
          games.push({
            team: r0.team, game_id: r0.game_id, date: r0.date, opp: r0.opp, home: r0.home, score: r0.score, result: r0.result,
            team_rating: Math.round(d3.sum(skaters, (r) => seconds(r.toi) * +r.rating) / toi * 100) / 100,
          });
        }
        games.sort((a, b) => (a.date < b.date ? -1 : 1));
        games.forEach((g, i) => { g.roll5 = Math.round(d3.mean(games.slice(Math.max(0, i - 4), i + 1), (x) => x.team_rating) * 100) / 100; });
        out.push(...games);
      }
      return out;
    },
  });

  dash.calc('teamForm', {
    title: 'Team form, start vs now',
    description: "Each team's average game rating in its first five and last five games, and its best and worst game.",
    inputs: ['teamGames'],
    fn: (teamGames) => {
      const out = [];
      for (const games of d3.group(teamGames, (g) => g.team).values()) {
        const round2 = (x) => Math.round(x * 100) / 100;
        const best = games.reduce((a, b) => (b.team_rating > a.team_rating ? b : a));
        const worst = games.reduce((a, b) => (b.team_rating < a.team_rating ? b : a));
        out.push({
          team: games[0].team, games: games.length,
          first5: round2(d3.mean(games.slice(0, 5), (g) => g.team_rating)),
          last5: round2(d3.mean(games.slice(-5), (g) => g.team_rating)),
          best_rating: best.team_rating, best_opp: best.opp, best_home: best.home, best_date: best.date, best_score: best.score, best_result: best.result,
          worst_rating: worst.team_rating, worst_opp: worst.opp, worst_home: worst.home, worst_date: worst.date, worst_score: worst.score, worst_result: worst.result,
        });
      }
      return out;
    },
  });

  const reduceMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function openPlayer(id) {
    state.player = id;
    ['playerGrid', 'playerText', 'playerChart', 'hotRows'].forEach((n) => dash.draw(n));
    const s = document.getElementById('player-trend');
    if (s) s.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' });
  }
  document.querySelectorAll('.hr-sw-roll').forEach((el) => { el.style.background = dash.colors[0]; });

  function teamPlayers() {
    const s = dash.data('season');
    return s.status === 'ok' && s.data ? groupOf(s.data, 'team', (p) => p.team).get(team()) || [] : [];
  }
  function currentPlayer() {
    syncTeam();
    const players = teamPlayers();
    if (!players.length) return null;
    const s = summary();
    return players.find((p) => p.player_id === state.player)
      || (s && players.find((p) => p.name === s.leader)) || players[0];
  }

  // A line chart of 0-10 ratings over the season: dots per game, a rolling-average line, a reference line at 5.
  function ratingChart(el, pts, opts) {
    el.replaceChildren();
    if (!pts.length) { el.textContent = '—'; return; }
    const width = Math.max(280, el.clientWidth || 600);
    const height = 250;
    const svg = d3.create('svg').attr('viewBox', `0 0 ${width} ${height}`).attr('width', '100%')
      .attr('role', 'img').attr('aria-label', opts.label);
    const yTicks = [0, 2, 4, 6, 8, 10];
    const probe = svg.append('g').attr('class', 'tick');
    el.appendChild(svg.node());
    const tw = d3.max(yTicks, (t) => { const n = probe.append('text').text(t).node(); return n.getComputedTextLength(); }) || 14;
    probe.remove();
    const m = { top: 12, right: 16, bottom: 26, left: Math.ceil(tw) + 18 };
    const x = d3.scaleUtc().domain(d3.extent(pts, (p) => p.d)).range([m.left, width - m.right]);
    if (pts.length === 1) x.domain([d3.utcDay.offset(pts[0].d, -1), d3.utcDay.offset(pts[0].d, 1)]);
    const y = d3.scaleLinear().domain([0, 10]).range([height - m.bottom, m.top]);

    svg.append('g').attr('transform', `translate(${m.left},0)`)
      .call(d3.axisLeft(y).tickValues(yTicks).tickSize(-(width - m.left - m.right)).tickPadding(8));
    const xTickCount = Math.max(2, Math.floor((width - m.left - m.right) / 120));
    svg.append('g').attr('transform', `translate(0,${height - m.bottom})`)
      .call(d3.axisBottom(x).ticks(Math.min(xTickCount, pts.length)).tickFormat(d3.utcFormat('%b %-d')).tickSize(0).tickPadding(10));

    svg.append('line').attr('x1', m.left).attr('x2', width - m.right).attr('y1', y(5)).attr('y2', y(5))
      .style('stroke', 'var(--cds-chart-reference)').style('stroke-width', 1.5).style('stroke-dasharray', '5 4');

    svg.append('path').datum(pts).attr('d', d3.line().x((p) => x(p.d)).y((p) => y(p.v)))
      .style('fill', 'none').style('stroke', 'var(--cds-chart-muted)').style('stroke-width', 1);
    const roll = svg.append('path').datum(pts).attr('d', d3.line().x((p) => x(p.d)).y((p) => y(p.roll)))
      .style('fill', 'none').style('stroke', dash.colors[0]).style('stroke-width', 2.5).style('stroke-linejoin', 'round');
    svg.append('g').selectAll('circle').data(pts).join('circle')
      .attr('cx', (p) => x(p.d)).attr('cy', (p) => y(p.v)).attr('r', 4.5)
      .style('fill', (p) => p.fill).style('stroke', (p) => p.stroke || 'var(--color-fg-muted)').style('stroke-width', 1.5);
    const last = pts[pts.length - 1];
    svg.append('circle').attr('cx', x(last.d)).attr('cy', y(last.roll)).attr('r', 4)
      .style('fill', dash.colors[0]).style('stroke', 'var(--color-panel)').style('stroke-width', 2);

    if (!el.dataset.animated && !reduceMotion()) {
      el.dataset.animated = '1';
      const len = roll.node().getTotalLength();
      roll.style('stroke-dasharray', `${len} ${len}`).style('stroke-dashoffset', len)
        .transition().duration(900).ease(d3.easeCubicOut).style('stroke-dashoffset', 0);
    }

    const tip = document.createElement('div'); tip.className = 'hr-tip'; tip.hidden = true;
    el.appendChild(tip);
    const focus = svg.append('circle').attr('r', 7).style('fill', 'none').style('stroke', 'var(--color-fg)').style('stroke-width', 1.5).style('display', 'none');
    const show = (event) => {
      const [mx] = d3.pointer(event, svg.node());
      const sx = mx * width / svg.node().getBoundingClientRect().width;
      const p = d3.least(pts, (q) => Math.abs(x(q.d) - sx));
      if (!p) return;
      focus.style('display', null).attr('cx', x(p.d)).attr('cy', y(p.v));
      tip.replaceChildren();
      const h = document.createElement('strong'); h.textContent = opts.title(p); tip.appendChild(h);
      for (const line of opts.lines(p)) { const s = document.createElement('span'); s.textContent = line; tip.append(s, document.createElement('br')); }
      tip.hidden = false;
      const scale = svg.node().getBoundingClientRect().width / width;
      const px = x(p.d) * scale;
      tip.style.left = Math.max(4, Math.min(px - 120, el.clientWidth - 248)) + 'px';
      tip.style.top = (y(p.v) * scale + 14) + 'px';
    };
    svg.append('rect').attr('x', m.left).attr('y', m.top).attr('width', width - m.left - m.right).attr('height', height - m.top - m.bottom)
      .style('fill', 'transparent')
      .on('pointermove', show).on('pointerdown', show)
      .on('pointerleave', () => { tip.hidden = true; focus.style('display', 'none'); });
  }

  const sign = (v) => (v > 0 ? '+' : '') + fmt2(v);

  dash.draw('teamTrendText', (el) => {
    const f = dash.data('teamForm');
    const row = f.status === 'ok' && f.data ? f.data.find((r) => r.team === team()) : null;
    if (!row) { el.textContent = '—'; return; }
    const W = 'team=' + encodeURIComponent(row.team);
    const diff = row.last5 - row.first5;
    const dir = Math.abs(diff) < 0.15 ? 'about the same as' : (diff > 0 ? 'up from' : 'down from');
    const bd = parseDate(row.best_date), wd = parseDate(row.worst_date);
    el.replaceChildren(
      row.team + "'s team rating over the last five games is ", mark('b', 'teamForm', 'last5', W, fmt2(row.last5)), ', ' + dir + ' ',
      mark('span', 'teamForm', 'first5', W, fmt2(row.first5)), ' in the first five. Best game: ',
      mark('span', 'teamForm', 'best_rating', W, fmt2(row.best_rating)), ' (' + row.best_result + ' ' + row.best_score + (String(row.best_home) === 'True' ? ' vs ' : ' at ') + row.best_opp + ', ' + (bd ? dateFmt(bd) : '') + '). Worst: ',
      mark('span', 'teamForm', 'worst_rating', W, fmt2(row.worst_rating)), ' (' + row.worst_result + ' ' + row.worst_score + (String(row.worst_home) === 'True' ? ' vs ' : ' at ') + row.worst_opp + ', ' + (wd ? dateFmt(wd) : '') + ').');
  });

  dash.draw('teamChart', (el) => {
    const d = dash.data('teamGames');
    if (d.status === 'error') { el.textContent = 'Failed to load'; return; }
    if (d.status !== 'ok' || !d.data) { el.textContent = '—'; return; }
    const pts = (groupOf(d.data, 'team', (g) => g.team).get(team()) || []).map((g) => ({
      d: parseDate(g.date), v: g.team_rating, roll: g.roll5, g,
      fill: g.result.startsWith('W') ? 'var(--color-ok)' : 'var(--color-bad)', stroke: 'var(--color-panel)',
    }));
    ratingChart(el, pts, {
      label: team() + ' team rating by game this season, with a five-game rolling average',
      title: (p) => p.g.result + ' ' + p.g.score + (String(p.g.home) === 'True' ? ' vs ' : ' at ') + p.g.opp,
      lines: (p) => [longDate(p.d), 'Team rating ' + fmt2(p.v) + ' · 5-game average ' + fmt2(p.roll)],
    });
  });

  dash.draw('playerText', (el) => {
    const p = currentPlayer();
    if (!p) { el.textContent = '—'; return; }
    const W = 'team=' + encodeURIComponent(p.team) + '&player_id=' + encodeURIComponent(p.player_id);
    const players = teamPlayers().filter((q) => q.gp >= 6);
    const medianSpread = d3.median(players, (q) => q.spread);
    const parts = [
      p.name + ' has averaged ', mark('b', 'season', 'avg', W, fmt2(p.avg)), ' over ', mark('span', 'season', 'gp', W, String(p.gp)),
      ' games, ranging from ', mark('span', 'season', 'worst', W, fmt1(p.worst)), ' to ', mark('span', 'season', 'best', W, fmt1(p.best)), '. ',
    ];
    if (p.gp >= 6) {
      const word = Math.abs(p.delta) < 0.25 ? 'level with' : (p.delta > 0 ? 'above' : 'below');
      parts.push('The last five games average ', mark('b', 'season', 'last5', W, fmt2(p.last5)), ', ' + word + ' the season (',
        mark('span', 'season', 'delta', W, sign(p.delta)), '). ');
      if (medianSpread != null) {
        parts.push('Game-to-game spread is ', mark('span', 'season', 'spread', W, fmt2(p.spread)),
          p.spread > medianSpread * 1.15 ? ', streakier than most of the team.' : (p.spread < medianSpread * 0.85 ? ', steadier than most of the team.' : ', typical for the team.'));
      }
    } else {
      parts.push('Too few games yet to call a trend.');
    }
    el.replaceChildren(...parts);
  });

  dash.draw('playerChart', (el) => {
    const d = dash.data('rolling');
    if (d.status === 'error') { el.textContent = 'Failed to load'; return; }
    const p = currentPlayer();
    if (d.status !== 'ok' || !d.data || !p) { el.textContent = '—'; return; }
    const pts = (groupOf(d.data, 'teamPlayer', (r) => r.team + ',' + r.player_id).get(p.team + ',' + p.player_id) || [])
      .map((r) => ({ d: parseDate(r.date), v: r.rating, roll: r.roll5, r, fill: hrColor(r.rating) }));
    ratingChart(el, pts, {
      label: p.name + "'s rating by game this season, with a five-game rolling average",
      title: (q) => p.name + ' · ' + fmt1(q.v),
      lines: (q) => [longDate(q.d) + ', ' + q.r.result + ' ' + q.r.score + (String(q.r.home) === 'True' ? ' vs ' : ' at ') + q.r.opp, '5-game average ' + fmt2(q.roll)],
    });
  });

  function sparkline(values) {
    const w = 84, h = 22;
    const svg = d3.create('svg').attr('class', 'hr-spark').attr('width', w).attr('height', h).attr('viewBox', `0 0 ${w} ${h}`)
      .attr('role', 'img').attr('aria-label', 'Last ' + values.length + ' ratings: ' + values.map(fmt1).join(', '));
    if (values.length < 2) return svg.node();
    const x = d3.scaleLinear().domain([0, values.length - 1]).range([3, w - 3]);
    const y = d3.scaleLinear().domain([0, 10]).range([h - 3, 3]);
    svg.append('line').attr('x1', 0).attr('x2', w).attr('y1', y(5)).attr('y2', y(5)).style('stroke', 'var(--cds-chart-reference)').style('stroke-dasharray', '2 2');
    svg.append('path').datum(values).attr('d', d3.line().x((v, i) => x(i)).y((v) => y(v)))
      .style('fill', 'none').style('stroke', dash.colors[0]).style('stroke-width', 1.5);
    svg.append('circle').attr('cx', x(values.length - 1)).attr('cy', y(values[values.length - 1])).attr('r', 2.5).style('fill', dash.colors[0]);
    return svg.node();
  }

  dash.draw('hotRows', (tb) => {
    tb.replaceChildren();
    const r = dash.data('rolling');
    const players = teamPlayers().filter((p) => p.gp >= 6);
    if (!players.length) return;
    const recent = new Map();
    if (r.status === 'ok' && r.data) {
      for (const row of groupOf(r.data, 'team', (x) => x.team).get(team()) || []) {
        if (!recent.has(row.player_id)) recent.set(row.player_id, []);
        recent.get(row.player_id).push(row);
      }
    }
    const sorted = d3.sort(players, (p) => -p.delta);
    const up = sorted.filter((p) => p.delta > 0).slice(0, 5);
    const down = sorted.filter((p) => p.delta < 0).slice(-5).reverse();
    const cur = currentPlayer();
    for (const [label, list] of [['Trending up', up], ['Trending down', down]]) {
      if (!list.length) continue;
      const gr = document.createElement('tr'); gr.className = 'grp';
      const gtd = document.createElement('td'); gtd.colSpan = 7; gtd.textContent = label; gr.appendChild(gtd); tb.appendChild(gr);
      for (const p of list) {
        const tr = document.createElement('tr');
        tr.dataset.key = p.team + ',' + p.player_id;
        tr.className = 'pick' + (cur && cur.player_id === p.player_id ? ' on' : '');
        tr.tabIndex = 0;
        const td = (txt, cls) => { const c = document.createElement('td'); if (cls) c.className = cls; c.textContent = txt; tr.appendChild(c); return c; };
        td(p.name, 'l'); td(p.pos, 'l'); td(String(p.gp)); td(fmt2(p.avg));
        const l5 = document.createElement('td'); l5.appendChild(chip(p.last5, fmt2(p.last5))); tr.appendChild(l5);
        td(sign(p.delta), p.delta > 0 ? 'pos' : 'neg');
        const games = (recent.get(p.player_id) || []).sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-10).map((g) => g.rating);
        const sp = document.createElement('td'); sp.className = 'l'; sp.appendChild(sparkline(games)); tr.appendChild(sp);
        tr.addEventListener('click', () => openPlayer(p.player_id));
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPlayer(p.player_id); } });
        tb.appendChild(tr);
      }
    }
  });

  // ---------------------------------------------------------------- player picker
  dash.draw('playerGrid', (el) => {
    const players = teamPlayers();
    el.replaceChildren();
    if (!players.length) { el.textContent = '—'; return; }
    const cur = currentPlayer();
    for (const [grp, label] of [['F', 'Forwards'], ['D', 'Defense'], ['G', 'Goalies']]) {
      const list = d3.sort(players.filter((p) => p.group === grp), (p) => -p.avg);
      if (!list.length) continue;
      const box = document.createElement('div');
      const h = document.createElement('h4'); h.textContent = label;
      const grid = document.createElement('div'); grid.className = 'hr-pgrid';
      for (const p of list) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'hr-player';
        b.setAttribute('aria-pressed', String(!!cur && cur.player_id === p.player_id));
        b.setAttribute('aria-label', p.name + ', ' + p.pos + ', average ' + fmt2(p.avg) + ' over ' + p.gp + ' games');
        const n = document.createElement('span'); n.className = 'pn'; n.textContent = p.name;
        const pp = document.createElement('span'); pp.className = 'pp'; pp.textContent = p.pos + ' · ' + p.gp; n.appendChild(pp);
        b.append(n, chip(p.avg, fmt2(p.avg)));
        b.addEventListener('click', () => {
          state.player = p.player_id;
          ['playerGrid', 'playerText', 'playerChart', 'hotRows'].forEach((x) => dash.draw(x));
        });
        grid.appendChild(b);
      }
      box.append(h, grid); el.appendChild(box);
    }
  });

  // ---------------------------------------------------------------- lines
  const mmss = (sec) => Math.floor(sec / 60) + ':' + String(Math.round(sec % 60)).padStart(2, '0');
  dash.calc('lineSeason', {
    title: 'Line combinations over the season',
    description: 'Each forward line and defense pair, summed over the games it was used: games, 5-on-5 minutes together, shot attempts and goals for and against while together, and its average line rating.',
    inputs: ['lines'],
    fn: (lines) => {
      const out = [];
      for (const rows of d3.group(lines, (r) => r.team + ',' + r.player_ids).values()) {
        const r0 = rows[0];
        const cf = d3.sum(rows, (r) => +r.CF), ca = d3.sum(rows, (r) => +r.CA);
        const rated = rows.filter((r) => r.unit_rating !== '' && r.unit_rating != null);
        out.push({
          team: r0.team, player_ids: r0.player_ids, names: r0.names, positions: r0.positions, kind: r0.kind,
          games: rows.length, toi_min: Math.round(d3.sum(rows, (r) => +r.toi_5v5) / 6) / 10,
          CF: cf, CA: ca, cf_pct: cf + ca ? Math.round(cf / (cf + ca) * 1000) / 10 : null,
          GF: d3.sum(rows, (r) => +r.GF), GA: d3.sum(rows, (r) => +r.GA),
          avg_rating: rated.length ? Math.round(d3.mean(rated, (r) => +r.unit_rating) * 100) / 100 : null,
        });
      }
      return out;
    },
  });

  dash.draw('lineRows', (tb) => {
    const s = dash.data('lineSeason');
    tb.replaceChildren();
    if (s.status !== 'ok' || !s.data) return;
    const mine = groupOf(s.data, 'team', (r) => r.team).get(team()) || [];
    for (const [kind, label, n] of [['F', 'Forward lines', 8], ['D', 'Defense pairs', 5]]) {
      const list = d3.sort(mine.filter((r) => r.kind === kind), (r) => -r.toi_min).slice(0, n);
      if (!list.length) continue;
      const gr = document.createElement('tr'); gr.className = 'grp';
      const g = document.createElement('td'); g.colSpan = 9; g.textContent = label + ', most used first'; gr.appendChild(g);
      tb.appendChild(gr);
      for (const r of list) {
        const tr = document.createElement('tr');
        tr.dataset.key = r.team + ',' + r.player_ids;
        const td = (txt, cls) => { const c = document.createElement('td'); if (cls) c.className = cls; c.textContent = txt; tr.appendChild(c); return c; };
        td(r.names, 'l'); td(String(r.games)); td(String(r.toi_min)); td(String(r.CF)); td(String(r.CA));
        td(r.cf_pct == null ? '—' : String(r.cf_pct), r.cf_pct == null ? '' : (r.cf_pct >= 50 ? 'pos' : 'neg'));
        td(String(r.GF)); td(String(r.GA));
        const c = document.createElement('td'); c.appendChild(r.avg_rating == null ? document.createTextNode('—') : chip(r.avg_rating, fmt2(r.avg_rating))); tr.appendChild(c);
        tb.appendChild(tr);
      }
    }
  });

  dash.draw('gameLines', (el) => {
    const l = dash.data('lines'), rt = dash.data('ratings');
    el.replaceChildren();
    if (l.status === 'error') { el.textContent = 'Failed to load'; return; }
    const g = currentGame();
    if (l.status !== 'ok' || rt.status !== 'ok' || !g) { el.textContent = '—'; return; }
    const units = d3.sort((groupOf(l.data, 'game', (u) => u.game_id).get(g.id) || []).filter((u) => u.team === team()), (u) => (u.kind === 'F' ? 0 : 1) * 10 + +u.unit.slice(1));
    if (!units.length) { el.textContent = 'No shift data for this game, so lines could not be worked out.'; return; }
    const byId = new Map((groupOf(rt.data, 'game', (r) => r.game_id).get(g.id) || []).map((r) => [r.player_id, r]));
    let lastKind = null;
    const unitRow = (label, members, stats, unitMark) => {
      const row = document.createElement('div'); row.className = 'hr-unit';
      const ul = document.createElement('div'); ul.className = 'ul'; ul.textContent = label;
      const um = document.createElement('div'); um.className = 'um';
      for (const r of members) {
        const pm = document.createElement('span'); pm.className = 'pm';
        const c = chip(+r.rating); c.dataset.source = 'ratings'; c.dataset.field = 'rating'; c.dataset.where = 'game_id=' + r.game_id + '&player_id=' + r.player_id;
        pm.append(r.name + ' ', c); um.appendChild(pm);
      }
      row.append(ul, um);
      if (stats) { const us = document.createElement('div'); us.className = 'us'; us.append(...stats); if (unitMark) us.append(' · line rating ', unitMark); row.appendChild(us); }
      el.appendChild(row);
    };
    for (const u of units) {
      if (lastKind && lastKind !== u.kind) { const gap = document.createElement('div'); gap.className = 'hr-unit-gap'; el.appendChild(gap); }
      lastKind = u.kind;
      const members = u.player_ids.split('|').map((id) => byId.get(id)).filter(Boolean);
      const W = 'game_id=' + u.game_id + '&team=' + encodeURIComponent(u.team) + '&unit=' + u.unit;
      const m = (field, text) => mark('span', 'lines', field, W, text);
      unitRow((u.kind === 'F' ? 'Line ' : 'Pair ') + u.unit.slice(1), members,
        ['5v5 together ' + mmss(+u.toi_5v5) + ' · shot attempts ', m('CF', u.CF), '–', m('CA', u.CA), ' · goals ', m('GF', u.GF), '–', m('GA', u.GA)],
        u.unit_rating === '' ? null : m('unit_rating', u.unit_rating));
    }
    const goalies = [...byId.values()].filter((r) => r.team === team() && r.pos === 'G');
    if (goalies.length) {
      const gap = document.createElement('div'); gap.className = 'hr-unit-gap'; el.appendChild(gap);
      unitRow('Goal', goalies, null, null);
    }
  });

  // ---------------------------------------------------------------- latest games (spoiler-free)
  const reveal = { all: false, ids: new Set(), day: null };
  const timeFmt = d3.timeFormat('%-I:%M %p');
  const dayFmt = d3.utcFormat('%a, %b %-d');
  function gameDays() {
    const g = dash.data('games');
    if (g.status !== 'ok') return [];
    return d3.sort(d3.groups(g.data, (r) => r.date), (x) => x[0]).reverse();
  }
  function setRevealButtons() {
    document.querySelectorAll('#hr-reveal-all').forEach((b) => { b.hidden = reveal.all; });
    document.querySelectorAll('#hr-hide-all').forEach((b) => { b.hidden = !reveal.all; });
  }
  document.querySelectorAll('#hr-reveal-all').forEach((b) => b.addEventListener('click', () => { reveal.all = true; setRevealButtons(); dash.draw('latestGames'); }));
  document.querySelectorAll('#hr-hide-all').forEach((b) => b.addEventListener('click', () => { reveal.all = false; reveal.ids.clear(); setRevealButtons(); dash.draw('latestGames'); }));

  dash.draw('dayPick', (sel) => {
    const days = gameDays();
    sel.replaceChildren(...days.map(([d, rows]) => new Option(dayFmt(parseDate(d)) + ' · ' + rows.length + (rows.length === 1 ? ' game' : ' games'), d)));
    if (!reveal.day && days.length) reveal.day = days[0][0];
    if (reveal.day) sel.value = reveal.day;
    sel.onchange = () => { reveal.day = sel.value; dash.draw('latestGames'); };
  });

  function openTeamStats(code) {
    dash.setParams({ team: code });
    showTab('stats');
    showStandings(false);
    const s = document.getElementById('team');
    if (s) s.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' });
  }

  dash.draw('latestGames', (el) => {
    const g = dash.data('games');
    const dk = darkTheme(el);
    if (g.status === 'ok' && unchanged(el, [g.data, dash.data('standings').data, logoSet(dk), dk, reveal.day, reveal.all, [...reveal.ids].join()])) return;
    el.replaceChildren();
    if (g.status === 'error') { el.textContent = 'Failed to load'; return; }
    const days = gameDays();
    if (g.status !== 'ok' || !days.length) { el.textContent = '—'; return; }
    const day = days.find(([d]) => d === reveal.day) || days[0];
    const st = dash.data('standings');
    const names = new Map(st.status === 'ok' ? st.data.map((t) => [t.team, t.name]) : []);
    const dark = darkTheme(el);
    for (const r of d3.sort(day[1], (x) => x.start_utc, (x) => x.game_id)) {
      const shown = reveal.all || reveal.ids.has(r.game_id);
      const W = 'game_id=' + r.game_id;
      const card = document.createElement('article'); card.className = 'hr-card';
      card.setAttribute('aria-label', (names.get(r.away) || r.away) + ' at ' + (names.get(r.home) || r.home));
      const match = document.createElement('div'); match.className = 'hr-match';
      for (const [code, field] of [[r.away, 'away_score'], [r.home, 'home_score']]) {
        const tm = document.createElement('button');
        tm.type = 'button';
        tm.className = 'tm' + (shown ? (code === r.winner ? ' win' : ' lose') : '');
        tm.setAttribute('aria-label', 'Open ' + (names.get(code) || code) + ' stats');
        tm.title = 'Open team stats (shows results)';
        tm.addEventListener('click', () => openTeamStats(code));
        const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = names.get(code) || code;
        tm.append(logo(code, dark), nm);
        if (shown) { const sc = mark('span', 'games', field, W, r[field]); sc.className = 'sc'; tm.appendChild(sc); }
        match.appendChild(tm);
      }
      const start = r.start_utc ? new Date(r.start_utc) : null;
      const meta = document.createElement('div'); meta.className = 'meta';
      meta.textContent = (start && !isNaN(start) ? timeFmt(start) + ' your time' : '') + (r.venue ? ' · ' + r.venue : '');
      card.append(match, meta);

      if (shown) {
        const res = document.createElement('div'); res.className = 'hr-res';
        res.append(r.ended === 'REG' ? 'Final' : 'Final (' + r.ended + ')');
        if (r.upset === 'True') {
          const b = document.createElement('span'); b.className = 'hr-badge'; b.textContent = 'Upset';
          res.append(' · ', b, ' ' + r.winner + ' had a ', mark('span', 'games', 'winner_pregame_pct', W, r.winner_pregame_pct), '% chance');
        }
        res.append(document.createElement('br'), 'Best rated: ' + r.winner_star + ' ', mark('b', 'games', 'winner_star_rating', W, r.winner_star_rating),
          ' (' + r.winner + ') · ' + r.loser_star + ' ', mark('b', 'games', 'loser_star_rating', W, r.loser_star_rating), ' (' + r.loser + ')');
        card.appendChild(res);
      } else {
        const veil = document.createElement('div'); veil.className = 'hr-veil';
        const t = document.createElement('span'); t.textContent = 'Score hidden';
        const b = document.createElement('button'); b.type = 'button'; b.className = 'hr-btn'; b.textContent = 'Reveal score';
        b.setAttribute('aria-label', 'Reveal the score of ' + (names.get(r.away) || r.away) + ' at ' + (names.get(r.home) || r.home));
        b.addEventListener('click', () => { reveal.ids.add(r.game_id); dash.draw('latestGames'); });
        veil.append(t, b); card.appendChild(veil);
      }

      const acts = document.createElement('div'); acts.className = 'hr-acts';
      const recap = safeUrl(r.recap_video_url), cond = safeUrl(r.condensed_video_url);
      if (recap) { const a = link('Watch recap', recap); a.className = 'hr-btn'; acts.appendChild(a); }
      if (cond) { const a = link('Condensed game', cond); a.className = 'hr-btn'; acts.appendChild(a); }
      if (!recap && !cond) { const n = document.createElement('span'); n.className = 'meta'; n.textContent = 'Video not posted yet'; acts.appendChild(n); }
      card.appendChild(acts);
      el.appendChild(card);
    }
  });

  // ---------------------------------------------------------------- tabs
  function showTab(name) {
    for (const id of ['latest', 'stats']) {
      const p = document.getElementById(id);
      if (p) p.hidden = id !== name;
    }
    document.querySelectorAll('.hr-tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
    dash.setLink(name);
  }
  document.querySelectorAll('.hr-tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));
  if (location.hash === '#stats') showTab('stats');

  // ---------------------------------------------------------------- teams, logos, standings
  let themeCache = null;
  function darkTheme(el) {
    if (themeCache && performance.now() - themeCache.at < 1500) return themeCache.dark;
    themeCache = { at: performance.now(), dark: readTheme(el) };
    return themeCache.dark;
  }
  function readTheme(el) {
    const probe = document.createElement('span');
    probe.style.color = 'var(--color-bg)'; probe.hidden = true;
    el.appendChild(probe);
    const c = getComputedStyle(probe).color; probe.remove();
    const n = (c.match(/[\d.]+/g) || []).map(Number);
    if (/^okl/.test(c)) return (n[0] > 1 ? n[0] / 100 : n[0]) < 0.5;
    return n.length >= 3 ? (0.2126 * n[0] + 0.7152 * n[1] + 0.0722 * n[2]) / 255 < 0.5 : false;
  }
  function logoSet(dark) {
    const d = dash.data(dark ? 'logosDark' : 'logosLight');
    return d.status === 'ok' && d.data && typeof d.data === 'object' && !Array.isArray(d.data) ? d.data : null;
  }
  function logo(abbr, dark) {
    const src = (logoSet(dark) || {})[abbr];
    const img = document.createElement('img');
    img.alt = ''; img.decoding = 'async';
    if (typeof src === 'string' && src.startsWith('data:image/svg+xml,')) img.src = src;
    return img;
  }
  function standings() {
    const s = dash.data('standings');
    return s.status === 'ok' ? s.data : null;
  }
  const redrawTeam = () => { themeCache = null; ['teamGrid', 'standingsRows', 'teamTitle', 'latestGames'].forEach((n) => dash.draw(n)); };
  try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', redrawTeam); } catch (e) { /* older browsers */ }

  dash.draw('teamGrid', (el) => {
    const s = dash.data('standings');
    const rows = standings();
    const dark = darkTheme(el);
    if (rows && unchanged(el, [rows, logoSet(dark), dark])) {
      el.querySelectorAll('.hr-logo').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.team === team())));
      return;
    }
    el.replaceChildren();
    if (s.status === 'error') { el.textContent = 'Failed to load'; return; }
    if (!rows) { el.textContent = '—'; return; }
    for (const [conf, confRows] of d3.sort(d3.groups(rows, (r) => r.conference), (g) => g[0])) {
      const c = document.createElement('div'); c.className = 'hr-conf';
      const h3 = document.createElement('h3'); h3.textContent = conf + ' Conference'; c.appendChild(h3);
      for (const [div, divRows] of d3.sort(d3.groups(confRows, (r) => r.division), (g) => g[0])) {
        const d = document.createElement('div'); d.className = 'hr-div';
        const h4 = document.createElement('h4'); h4.textContent = div; d.appendChild(h4);
        const grid = document.createElement('div'); grid.className = 'hr-logos';
        for (const t of d3.sort(divRows, (r) => r.team)) {
          const b = document.createElement('button');
          b.type = 'button'; b.className = 'hr-logo'; b.title = t.name; b.dataset.team = t.team;
          b.setAttribute('aria-label', t.name);
          b.setAttribute('aria-pressed', String(t.team === team()));
          const lab = document.createElement('span'); lab.textContent = t.team;
          b.append(logo(t.team, dark), lab);
          b.addEventListener('click', () => pickTeam(t.team));
          grid.appendChild(b);
        }
        d.appendChild(grid); c.appendChild(d);
      }
      el.appendChild(c);
    }
  });

  dash.draw('teamTitle', (el) => {
    const rows = standings();
    const t = rows && rows.find((r) => r.team === team());
    el.replaceChildren();
    if (!t) return;
    const h = document.createElement('h2'); h.textContent = t.name;
    el.append(logo(t.team, darkTheme(el)), h);
  });

  const ordinal = (n) => n + (['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 14) ? 0 : (n % 10 < 4 ? n % 10 : 0)] || 'th');
  dash.draw('teamRecord', (el) => {
    const rows = standings();
    const t = rows && rows.find((r) => r.team === team());
    if (!t) { el.textContent = '—'; return; }
    const W = 'team=' + encodeURIComponent(t.team);
    el.replaceChildren(
      t.name + ' are ', mark('b', 'standings', 'w', W, t.w), '-', mark('b', 'standings', 'l', W, t.l), '-', mark('b', 'standings', 'otl', W, t.otl),
      ' with ', mark('b', 'standings', 'pts', W, t.pts), ' points: ',
      ordinal(+t.division_rank) + ' in the ' + t.division + ' Division and ' + ordinal(+t.league_rank) + ' in the league.');
  });

  function showStandings(open) {
    const sec = document.getElementById('standings');
    if (sec) sec.hidden = !open;
    document.querySelectorAll('#hr-show-stand').forEach((b) => { b.hidden = open; b.setAttribute('aria-expanded', 'false'); });
    document.querySelectorAll('#hr-hide-stand').forEach((b) => b.setAttribute('aria-expanded', String(open)));
  }
  document.querySelectorAll('#hr-show-stand').forEach((b) => b.addEventListener('click', () => {
    showStandings(true);
    const sec = document.getElementById('standings');
    if (sec) sec.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }));
  document.querySelectorAll('#hr-hide-stand').forEach((b) => b.addEventListener('click', () => showStandings(false)));
  function pickTeam(code) {
    dash.setParams({ team: code });
    showStandings(false);
    const s = document.getElementById('team');
    if (s) s.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  }

  const view = { by: 'division' };
  function setView(by) {
    view.by = by;
    document.querySelectorAll('.hr-tog').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === by)));
    dash.draw('standingsRows'); dash.draw('rankHead');
  }
  document.querySelectorAll('.hr-tog').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  dash.draw('rankHead', (th) => {
    th.textContent = view.by === 'conference' ? 'Conf' : '#';
  });

  function standingsBlocks(rows) {
    // [{label, sub?, rows, cut?, rank}]: cut = rows above the playoff line
    const byConf = d3.sort(d3.groups(rows, (r) => r.conference), (g) => g[0]);
    if (view.by === 'league') return [{ rows: d3.sort(rows, (r) => +r.league_rank), rank: (r, i) => i + 1 }];
    if (view.by === 'division') {
      return d3.sort(d3.groups(rows, (r) => r.division), (g) => g[0]).map(([div, rs]) => ({
        label: div + ' Division', rows: d3.sort(rs, (r) => +r.division_rank), cut: 3, rank: (r) => +r.division_rank }));
    }
    const out = [];
    for (const [conf, rs] of byConf) {
      const divs = d3.sort(d3.groups(rs, (r) => r.division), (g) => g[0]);
      divs.forEach(([div, drs], i) => out.push({
        label: i === 0 ? conf + ' Conference' : null, sub: div + ' · top 3',
        rows: d3.sort(drs.filter((r) => +r.division_rank <= 3), (r) => +r.division_rank), rank: (r) => +r.conference_rank }));
      out.push({ sub: 'Wild card', rows: d3.sort(rs.filter((r) => +r.division_rank > 3), (r) => +r.wildcard_rank || +r.conference_rank), cut: 2, rank: (r) => +r.conference_rank });
    }
    return out;
  }

  dash.draw('standingsRows', (tb) => {
    const rows = standings();
    const dark = darkTheme(tb);
    if (rows && unchanged(tb, [rows, logoSet(dark), dark, view.by])) {
      tb.querySelectorAll('tr[data-key]').forEach((tr) => tr.classList.toggle('on', tr.dataset.key === team()));
      return;
    }
    tb.replaceChildren();
    if (!rows) return;
    const ncol = 16;
    const header = (cls, text) => {
      const gr = document.createElement('tr'); gr.className = cls;
      const td = document.createElement('td'); td.colSpan = ncol; td.textContent = text; gr.appendChild(td);
      tb.appendChild(gr);
    };
    for (const block of standingsBlocks(rows)) {
      if (block.label) header('grp', block.label);
      if (block.sub) header('sub', block.sub);
      block.rows.forEach((t, i) => {
        const tr = document.createElement('tr');
        tr.dataset.key = t.team;
        tr.className = 'pick' + (t.team === team() ? ' on' : '') + (block.cut && i === block.cut - 1 && block.rows.length > block.cut ? ' cut' : '');
        tr.tabIndex = 0;
        tr.setAttribute('aria-label', 'Open ' + t.name + (block.cut ? (i < block.cut ? ', in a playoff spot' : ', outside the playoff spots') : ''));
        const cell = (txt, cls) => { const c = document.createElement('td'); if (cls) c.className = cls; c.textContent = txt; tr.appendChild(c); return c; };
        cell(String(block.rank(t, i)));
        const tm = document.createElement('td'); tm.className = 'tm';
        const wrap = document.createElement('span'); const nm = document.createElement('span'); nm.textContent = t.name;
        wrap.append(logo(t.team, dark), nm); tm.appendChild(wrap); tr.appendChild(tm);
        for (const f of ['gp', 'w', 'l', 'otl']) cell(t[f]);
        const pts = cell(t.pts); pts.style.fontWeight = 'var(--cds-font-weight-medium)';
        cell(d3.format('.3f')(+t.pts_pct)); cell(t.rw); cell(t.gf); cell(t.ga);
        cell((+t.diff > 0 ? '+' : '') + t.diff, +t.diff > 0 ? 'pos' : (+t.diff < 0 ? 'neg' : ''));
        cell(t.home); cell(t.road); cell(t.l10); cell(t.streak);
        const go = () => pickTeam(t.team);
        tr.addEventListener('click', go);
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        tb.appendChild(tr);
      });
    }
  });

  dash.draw('standingsAsOf', (el) => {
    const rows = standings();
    const d = rows && rows[0] && parseDate(rows[0].as_of);
    el.textContent = d ? 'Standings as of ' + longDate(d) + '.' : '—';
  });

  // ---------------------------------------------------------------- upsets
  function upsetGames() {
    const g = dash.data('games');
    return g.status === 'ok' ? g.data.filter((r) => r.upset === 'True').sort((a, b) => +a.winner_pregame_prob - +b.winner_pregame_prob) : [];
  }
  function storyGame() {
    syncTeam();
    const ups = upsetGames();
    if (state.upset) { const pick = ups.find((r) => r.game_id === state.upset); if (pick) return pick; }
    return ups.find((r) => r.winner === team()) || ups.find((r) => r.loser === team()) || null;
  }

  dash.draw('upsetStory', (el) => {
    const gs = dash.data('games'), rs = dash.data('ratings'), go = dash.data('goals');
    el.replaceChildren();
    if ([gs, rs, go].some((d) => d.status === 'error')) { el.textContent = 'Failed to load'; return; }
    if ([gs, rs, go].some((d) => d.status !== 'ok')) { el.textContent = '—'; return; }
    const g = storyGame();
    if (!g) {
      const p = document.createElement('p');
      p.textContent = team() + ' have not been part of an upset this season. Pick one from the league list below.';
      el.appendChild(p); return;
    }
    const W = 'game_id=' + g.game_id;
    const winnerHome = g.winner === g.home;
    const winPts = winnerHome ? 'home_pregame_pts' : 'away_pregame_pts';
    const losePts = winnerHome ? 'away_pregame_pts' : 'home_pregame_pts';
    const d = parseDate(g.date);

    const meta = document.createElement('div'); meta.className = 'meta';
    meta.textContent = (d ? longDate(d) : g.date) + ' · ' + (g.winner === team() ? 'Upset win for ' + team() : g.loser === team() ? 'Upset loss for ' + team() : 'League upset');
    const h = document.createElement('h3');
    h.append(g.winner + ' ', mark('span', 'games', 'final', W, g.final), ' ' + g.loser);

    const p1 = document.createElement('p');
    p1.append(
      g.winner + ' went in with only a ', mark('b', 'games', 'winner_pregame_pct', W, g.winner_pregame_pct), '% chance of winning ',
      (winnerHome ? 'at home' : 'on the road') + '. Going into the game ' + g.winner + ' had a points percentage of ',
      mark('span', 'games', winPts, W, g[winPts]), ' against ' + g.loser + "'s ", mark('span', 'games', losePts, W, g[losePts]),
      ' (blended with last season early on).');

    const p2 = document.createElement('p');
    p2.append(
      g.winner_star + ' was the best player for ' + g.winner + ' with a rating of ', mark('b', 'games', 'winner_star_rating', W, g.winner_star_rating),
      '; ' + g.loser_star + ' led ' + g.loser + ' at ', mark('b', 'games', 'loser_star_rating', W, g.loser_star_rating), '.');

    const goals = groupOf(go.data, 'game', (x) => x.game_id).get(g.game_id) || [];
    const ol = document.createElement('ol');
    for (const x of goals) {
      const li = document.createElement('li');
      const per = x.period_type === 'OT' ? 'OT' : 'P' + x.period;
      li.append(per + ' ' + x.time + ' · ' + x.team + ' · ' + x.scorer + (x.assists ? ' (' + x.assists + ')' : '') + (x.strength !== 'EV' ? ' · ' + x.strength : '') + ' · ');
      const u = safeUrl(x.clip_url);
      if (u) li.appendChild(link('Watch goal', u)); else li.append('no clip');
      ol.appendChild(li);
    }
    const goalsHead = document.createElement('h4'); goalsHead.textContent = 'Goals and highlight clips';

    const cols = document.createElement('div'); cols.className = 'cols';
    for (const t of [g.winner, g.loser]) {
      const box = document.createElement('div');
      const hh = document.createElement('h4'); hh.textContent = 'Top rated · ' + t;
      const ul = document.createElement('ul');
      (groupOf(rs.data, 'game', (r) => r.game_id).get(g.game_id) || []).filter((r) => r.team === t).sort((a, b) => +b.rating - +a.rating).slice(0, 4).forEach((r) => {
        const li = document.createElement('li'); li.className = 'p';
        const n = document.createElement('span'); n.textContent = r.name + ' '; const tg = document.createElement('span'); tg.className = 'hr-tag'; tg.textContent = r.pos; n.appendChild(tg);
        const c = chip(+r.rating); c.dataset.source = 'ratings'; c.dataset.field = 'rating'; c.dataset.where = 'game_id=' + r.game_id + '&player_id=' + r.player_id;
        li.append(n, c); ul.appendChild(li);
      });
      box.append(hh, ul); cols.appendChild(box);
    }

    const foot = document.createElement('p');
    const gc = safeUrl(g.gamecenter_url);
    if (gc) foot.append(link('Watch the full recap on NHL.com GameCenter', gc));

    el.append(meta, h, p1, p2, cols, goalsHead, ol, foot);
  });

  dash.draw('upsetRows', (tb) => {
    tb.replaceChildren();
    const cur = storyGame();
    for (const g of upsetGames().slice(0, 12)) {
      const tr = document.createElement('tr');
      tr.dataset.key = g.game_id;
      if (cur && cur.game_id === g.game_id) tr.className = 'on';
      const d = parseDate(g.date);
      for (const [v, cls] of [[d ? dateFmt(d) : g.date, 'l'], [g.winner, 'l'], [g.final, 'l'], [g.loser, 'l'], [g.winner_pregame_pct, ''], [g.winner_star + ' (' + g.winner_star_rating + ')', 'l']]) {
        const td = document.createElement('td'); if (cls) td.className = cls; td.textContent = v; tr.appendChild(td);
      }
      const td = document.createElement('td'); td.className = 'l';
      const b = document.createElement('button'); b.type = 'button'; b.className = 'hr-btn'; b.textContent = 'Read';
      b.addEventListener('click', () => {
        state.upset = g.game_id;
        dash.draw('upsetStory'); dash.draw('upsetRows');
        const story = document.getElementById('upset-story');
        if (story) story.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
      });
      td.appendChild(b); tr.appendChild(td);
      tb.appendChild(tr);
    }
  });
})();

// ---- static-site extras
(function () {
  const when = d3.timeFormat('%b %-d, %Y at %-I:%M %p');
  dash.draw('updatedAt', (el) => {
    const m = dash.data('meta');
    if (m.status !== 'ok' || !m.data) { el.textContent = '—'; return; }
    const at = m.data.generated_at ? new Date(m.data.generated_at) : null;
    el.textContent = 'Data updated ' + (at && !isNaN(at) ? when(at) + ' your time' : m.data.generated)
      + (m.data.last_game_day ? ' · games through ' + m.data.last_game_day : '');
  });
})();
