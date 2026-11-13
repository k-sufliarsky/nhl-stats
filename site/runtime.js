// A small stand-in for the claude.ai dashboard runtime, so the same page code runs as a plain static site.
// It loads the data files on first use, works out calculations, and redraws every [data-draw] element
// whenever data arrives, the team changes, a hidden section opens or the page width changes.
(function () {
  const SOURCES = {
    ratings: { url: 'data/nhl_ratings.csv', type: 'csv' },
    games: { url: 'data/nhl_games.csv', type: 'csv' },
    goals: { url: 'data/nhl_goals.csv', type: 'csv' },
    lines: { url: 'data/nhl_lines.csv', type: 'csv' },
    standings: { url: 'data/nhl_standings.csv', type: 'csv' },
    upcoming: { url: 'data/nhl_upcoming.csv', type: 'csv' },
    players: { url: 'data/nhl_players.csv', type: 'csv' },
    logosLight: { url: 'data/logos-light.json', type: 'json' },
    logosDark: { url: 'data/logos-dark.json', type: 'json' },
    meta: { url: 'data/nhl_meta.json', type: 'json' },
  };
  const loaded = {};
  const calcs = {};
  const draws = {};
  const dataListeners = [];
  const defaults = { team: '' };   // no team picked until the reader picks one
  let params = { ...defaults };

  const fromUrl = new URLSearchParams(location.search).get('team');
  if (fromUrl && /^[A-Z]{3}$/.test(fromUrl)) params.team = fromUrl;

  // The data files keep their names between updates. nhl_meta.json is always fetched fresh, and its update time
  // tags every other file's URL, so a browser never reuses data from before the latest update.
  const version = fetch('data/nhl_meta.json', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((m) => (m && m.generated_at) || String(Date.now()))
    .catch(() => String(Date.now()));
  function load(id) {
    const src = SOURCES[id];
    const entry = (loaded[id] = { status: 'loading', data: [], message: null });
    version
      .then((v) => {
        const url = src.url + '?v=' + encodeURIComponent(v);
        return src.type === 'csv' ? d3.csv(url) : d3.json(url);
      })
      .then((d) => { entry.status = 'ok'; entry.data = d; })
      .catch((e) => { entry.status = 'error'; entry.message = String(e); })
      .finally(schedule);
    return entry;
  }

  function calcValue(id) {
    const c = calcs[id];
    const inputs = c.inputs.map((i) => data(i));
    if (inputs.some((i) => i.status === 'error')) return { status: 'error', data: null };
    if (inputs.some((i) => i.status !== 'ok')) return { status: 'loading', data: null };
    const values = inputs.map((i) => i.data);
    if (!c.values || c.values.some((v, k) => v !== values[k])) {
      c.values = values;
      try { c.value = c.fn(...values); c.error = null; } catch (e) { c.error = e; console.error('calc ' + id, e); }
    }
    return c.error ? { status: 'error', data: null } : { status: 'ok', data: c.value };
  }

  function data(id) {
    if (calcs[id]) return calcValue(id);
    if (!SOURCES[id]) return { status: 'missing', data: [] };
    return loaded[id] || load(id);
  }

  function isShown(el) { return !el.closest('[hidden]'); }
  function runDraw(name) {
    const fn = draws[name];
    if (!fn) return;
    document.querySelectorAll('[data-draw="' + name + '"]').forEach((el) => {
      if (!isShown(el)) return;
      try { fn(el); } catch (e) { console.error('draw ' + name, e); }
    });
  }

  let pending = false;
  function schedule() {
    if (pending) return;
    pending = true;
    const run = () => {
      if (!pending) return;
      pending = false;
      dataListeners.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
      Object.keys(draws).forEach(runDraw);
    };
    // Animation frames pause in background tabs; the timeout makes sure data still starts loading there.
    requestAnimationFrame(run);
    setTimeout(run, 120);
  }

  window.dash = {
    data,
    calc(id, spec) { calcs[id] = { inputs: spec.inputs || [], fn: spec.fn }; },
    loader() {},
    draw(name, fn) {
      if (fn === undefined) return runDraw(name);
      draws[name] = typeof fn === 'function' ? fn : (el) => (fn.update || fn.mount)(el);
    },
    onData(fn) { dataListeners.push(fn); fn(); },
    params: () => ({ ...params }),
    setParams(next) {
      params = { ...params, ...next };
      const q = new URLSearchParams(location.search);
      if (params.team && params.team !== defaults.team) q.set('team', params.team); else q.delete('team');
      history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
      schedule();
    },
    resetParams() { params = { ...defaults }; schedule(); },
    setLink(name) { history.replaceState(null, '', location.pathname + location.search + (name ? '#' + name : '')); },
    refresh(id) { delete loaded[id]; schedule(); },
    colors: ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'],
  };

  // Redraw when a section is opened or closed (tabs, the standings toggle).
  new MutationObserver((list) => {
    if (list.some((m) => !m.target.closest('[data-draw]') && m.target.querySelector && m.target.querySelector('[data-draw]'))) schedule();
  }).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['hidden'] });

  // Redraw charts when the page width changes.
  let lastWidth = 0;
  new ResizeObserver(() => {
    const w = document.documentElement.clientWidth;
    if (Math.abs(w - lastWidth) > 4) { lastWidth = w; schedule(); }
  }).observe(document.documentElement);

  // Redraw logos when the system theme flips.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', schedule);

  document.addEventListener('DOMContentLoaded', schedule);
})();
