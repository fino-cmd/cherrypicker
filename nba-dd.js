/* ════════════════════════════════════════════════════════════════════════════
   NBA · Doble-doble y triple-doble.

   Misma idea que la pestaña de Fútbol: el backend juega 20.000 veces cada
   partido entero (ritmo, tiros, rebotes, asistencias de los dos equipos) y lo
   combina con la historia de cada jugador. Cuenta cuántas veces llega a 10+ en
   dos (DD) o tres (TD) estadísticas. Contra las cuotas de bet365, Betano y
   Betsson salen las jugadas.

   Diseño: el de siempre (azul NBA, tarjetas blancas) con los recursos del kit
   de NBA.com en assets/nba: Action NBA Cond, Knockout, logos oficiales de los
   equipos, sus colores y las canchas de fondo.

   Vistas: Jugadas · Partidos (se despliegan) · Resultados.
   API: /api/nba/dd/slate · /api/nba/dd/game/{id} · POST /api/nba/dd/odds ·
        /api/nba/dd/results
   ════════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // El mismo backend que el resto de la app (app.js define API; se carga después).
  const API = `http://${location.hostname || 'localhost'}:8002`;
  // Casas: las manda el backend (futbol/books.json, 'nba': true); esto es el arranque.
  let BOOKS = [
    { id: 'bet365.bet.ar', name: 'bet365', logo: 'assets/books/bet365.png' },
    { id: 'betano', name: 'Betano', logo: 'assets/books/betano.png' },
    { id: 'betsson', name: 'Betsson' },
  ];
  const MAX_CHIPS = 4;
  const MK = { dd: 'Doble-doble', td: 'Triple-doble', props: 'Líneas de jugador' };
  // Líneas de jugador (altlines/props_live.py)
  const PMK = ['pts', 'reb', 'ast', 'threes', 'pr', 'pa', 'ra', 'pra'];
  // Combinaciones: siempre abreviadas (también en jugadas guardadas con el nombre largo)
  const COMBO = { 'Puntos + Rebotes + Asistencias': 'PTS+REB+ASIS', 'Puntos + Rebotes': 'PTS+REB', 'Puntos + Asistencias': 'PTS+ASIS', 'Rebotes + Asistencias': 'REB+ASIS' };
  const lbl = (s) => COMBO[s] || s || '';
  const PMK_SHORT = { pts: 'Puntos', reb: 'Rebotes', ast: 'Asist.', threes: 'Triples', pr: 'PTS+REB', pa: 'PTS+ASIS', ra: 'REB+ASIS', pra: 'PTS+REB+ASIS' };
  const STAT = { pts: 'PTS', reb: 'REB', ast: 'AST', stl: 'ROB', blk: 'TAP' };
  const FIRM_MIN_ODDS = 1.40;

  const state = {
    view: 'picks', slate: null, sims: {}, open: new Set(), busy: new Set(),
    results: null, error: null, oddsBusy: false, loaded: false, team: {},
    played: new Set(), oddsGame: new Set(), oddsOpen: false, resKind: 'all', flip: new Map(), propLive: {}, liveStats: {}, barShown: {}, liveJustWon: new Set(),
    props: {}, propOpen: new Set(), propMk: {},
    hlMode: {}, hlSpin: null, parlayN: {}, shotBusy: new Set(),
    hlBoost: new Set(),          // partidos con el booster activado dentro de Parlay
    inj: null, injOpen: false,   // parte de lesionados: qué cambió desde la simulación
    back: null,               // de dónde se vino al abrir un partido (para "Volver")
    // Jugadas: tope de cuota y orden (se recuerdan entre visitas)
    oddsCap: (() => { try { return Number(localStorage.getItem('nd-odds-cap')) || 0; } catch (_) { return 0; } })(),
    sortBy: (() => { try { return localStorage.getItem('nd-sort') || 'edge'; } catch (_) { return 'edge'; } })(),
    market: 'props',          // siempre abre en Líneas de jugador
  };

  // ── utilidades ──────────────────────────────────────────────────────────
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pct = (p) => `${Math.round(p * 100)}%`;
  const odd = (x) => (x == null ? '—' : Number(x).toFixed(2).replace('.', ','));
  // De dónde salen las cuotas (NBA_ODDS en backend/.env): odds-api.net u OddsPapi.
  const apiName = (u) => ((u || {}).api === 'net' ? 'odds-api.net' : (u || {}).api === 'optic' ? 'OpticOdds' : 'OddsPapi');
  const book = (id) => BOOKS.find((b) => b.id === id) || { id, name: id };
  const bookBadge = (id, sm = false) => {
    const b = book(id);
    return b.logo
      ? `<img class="nd-bk${sm ? ' is-sm' : ''}" src="${b.logo}" alt="${esc(b.name)}" title="${esc(b.name)}">`
      : `<span class="nd-bk nd-bk--txt nd-bk--${esc(String(id).replace(/[^a-z0-9]/gi, ''))}${sm ? ' is-sm' : ''}" title="${esc(b.name)}">${esc(b.name)}</span>`;
  };
  const initials = (n) => String(n).split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  const hhmm = (iso) => new Date(iso).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false });
  // La noche NBA dura hasta las 3 de la mañana: un partido de las 00:30 es de "hoy"
  // (igual que NBA_DAY_CUTOFF_H en altlines/slate.py).
  const NBA_DAY_CUTOFF_MS = 3 * 3600 * 1000;
  const dayLabel = (iso) => {
    const d = new Date(new Date(iso).getTime() - NBA_DAY_CUTOFF_MS), t = new Date(Date.now() - NBA_DAY_CUTOFF_MS);
    const k = (x) => x.toDateString();
    const tm = new Date(t); tm.setDate(t.getDate() + 1);
    if (k(d) === k(t)) return 'Hoy';
    if (k(d) === k(tm)) return 'Mañana';
    return d.toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' });
  };
  function ago(iso) {
    if (!iso) return '';
    const s = (Date.now() - Date.parse(iso)) / 1000;
    if (s < 90) return 'recién';
    if (s < 3600) return `hace ${Math.round(s / 60)} min`;
    if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
    return `hace ${Math.round(s / 86400)} d`;
  }
  const ageTxt = (s) => (s == null ? null : s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`);
  // La foto de ESPN viene de 600×436 (200 KB) y acá se muestra a 22-60 px: se pide la
  // versión reducida. Decenas de fotos grandes trababan el scroll en el celu.
  const thumb = (u) => { const m = /^https?:\/\/a\.espncdn\.com(\/i\/headshots\/.+\.png)$/.exec(u || ''); return m ? `https://a.espncdn.com/combiner/i?img=${m[1]}&w=200&h=145` : u; };
  const face = (p, cls = '') => `
    <span class="nd-face ${cls}"><i>${esc(initials(p.name || p.player))}</i>
      <img src="${esc(thumb(p.photo))}" alt="" loading="lazy" decoding="async" onerror="if(!this.dataset.f&&this.src!=='${esc(p.photo)}'){this.dataset.f=1;this.src='${esc(p.photo)}'}else this.remove()"></span>`;

  // Kit NBA.com: logo oficial por sigla y color de cada equipo.
  const TEAM_COLOR = { ATL: '#e03a3e', BKN: '#000', BOS: '#008348', CHA: '#00788c', CHI: '#ce1141', CLE: '#860038', DAL: '#0053bc', DEN: '#0e2240', DET: '#1d428a', GSW: '#1d428a', HOU: '#ce0e2d', IND: '#002d62', LAC: '#12173f', LAL: '#552583', MEM: '#5d76a9', MIA: '#98002e', MIL: '#00471b', MIN: '#1d428a', NOP: '#002b5c', NYK: '#1d428a', OKC: '#007ac1', ORL: '#0050b5', PHI: '#006bb6', PHX: '#1d1160', POR: '#e03a3e', SAC: '#5a2d81', SAS: '#000', TOR: '#000', UTA: '#4e008e', WAS: '#002b5c' };
  // Si los dos comparten color (DET/NYK, GSW/MIN…), el visitante usa el secundario.
  const TEAM_ALT = { DET: '#c8102e', NYK: '#f58426', GSW: '#ffc72c', MIN: '#236192', BKN: '#777d84', SAS: '#8a8d8f', TOR: '#ce1141', NOP: '#85714d', WAS: '#e31837', POR: '#000' };
  const tcolor = (tr) => TEAM_COLOR[tr] || '#1d428a';
  const pairColors = (away, home) => {
    const a = tcolor(away), h = tcolor(home);
    return a === h ? [TEAM_ALT[away] || '#6a81b7', h] : [a, h];
  };
  const teamLogo = (tr) => (TEAM_COLOR[tr] ? `assets/nba/teams/${tr}.svg` : '');
  const logoImg = (t) => `<img src="${esc(teamLogo(t.tricode) || t.logo)}" alt="" onerror="this.onerror=null;this.src='${esc(t.logo || '')}'">`;

  function gameState(g) {
    if (g.state === 'in') return { live: true, t: 'En juego' };
    if (g.state === 'post') return { past: true, t: 'Final' };
    const m = (Date.parse(g.tip_off) - Date.now()) / 60000;
    if (m <= 0) return { live: true, t: 'En juego' };
    if (m < 90) return { soon: true, t: `en ${Math.round(m)} min` };
    return { t: '' };
  }

  const I = {
    ball: ICON('basketball', 20),
    ticket: ICON('ticket', 20),
    cup: ICON('trophy', 20),
    tail: ICON('bellCurve', 20),
    play: ICON('play', 18),
    refresh: ICON('refresh', 16),
    chev: ICON('chevronDown', 16),
    check: ICON('checkmark', 14),
    clock: ICON('clock', 13),
  };

  // ── datos ───────────────────────────────────────────────────────────────
  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* nada */ }
      throw new Error(msg);
    }
    return r.json();
  }
  async function loadSlate() {
    try {
      state.slate = await getJSON(`${API}/api/nba/dd/slate`);
      loadPlayed();
      if (Array.isArray(state.slate.books) && state.slate.books.length) BOOKS = state.slate.books;
      state.error = null;
    } catch (e) {
      state.error = e.message;
    }
    render();
    if (state.slate) { runQueue(state.slate.games.map((g) => g.game_id)); loadInj(); pollLive(); }
  }
  // Parte de lesionados: el backend compara el parte de ESPN de ahora con el que
  // usó cada simulación. Se consulta al cargar, cada 5 minutos y al terminar de simular.
  const INJ_EVERY_MS = 5 * 60 * 1000;
  let injTimer = 0;
  async function loadInj() {
    try { state.inj = await getJSON(`${API}/api/nba/dd/injuries`); }
    catch (_) { state.inj = null; }          // backend viejo o sin red: no se muestra el aviso
    render();
    clearTimeout(injTimer);
    injTimer = setTimeout(() => { if (document.body.classList.contains('nd-on')) loadInj(); else injTimer = 0; }, INJ_EVERY_MS);
  }
  const injGames = () => (state.inj && state.inj.games) || [];
  const injOf = (gid) => injGames().find((x) => x.game_id === gid);
  let queueBusy = false;
  const queue = [];
  function runQueue(ids, refresh = false) {
    ids.forEach((id) => { if (!queue.some((q) => q.id === id)) queue.push({ id, refresh }); });
    if (queueBusy) return;
    queueBusy = true;
    (async () => {
      while (queue.length) {
        const { id, refresh: rf } = queue.shift();
        state.busy.add(id); render();
        try {
          state.sims[id] = await getJSON(`${API}/api/nba/dd/game/${id}${rf ? '?refresh=true' : ''}`);
        } catch (e) {
          state.sims[id] = state.sims[id] || { error: e.message, players: [], picks: [] };
        }
        state.busy.delete(id); render();
        loadProps(id);
      }
      queueBusy = false;
      if (state.slate) loadInj();     // lo recién simulado ya usa el parte nuevo
    })();
  }
  async function loadProps(id) {
    try { state.props[id] = await getJSON(`${API}/api/nba/props/game/${id}`); } catch (e) { state.props[id] = { error: e.message, players: [], picks: [] }; }
    render();
  }
  const playKey = (gid, pid, mk) => `${gid}|${pid}|${mk}`;
  async function loadPlayed() {
    try { state.played = new Set((await getJSON(`${API}/api/nba/dd/played`)).keys || []); render(); } catch (_) { /* sin red */ }
  }
  async function togglePlayed(key) {
    const [game_id, player_id, market] = key.split('|');
    const on = !state.played.has(key);
    if (on) state.played.add(key); else state.played.delete(key);
    render(true);
    try {
      const r = await getJSON(`${API}/api/nba/dd/played`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game_id, player_id, market, on }),
      });
      state.played = new Set(r.keys || []);
    } catch (e) { toast(`No se pudo guardar: ${esc(e.message)}`); }
    render(true);
  }
  function playBtn(gid, pid, mk, sm = false) {
    const key = playKey(gid, pid, mk);
    const on = state.played.has(key);
    return `<button class="nd-play${on ? ' is-on' : ''}${sm ? ' is-sm' : ''}" data-play="${esc(key)}" aria-pressed="${on}">${on ? I.check : '<i class="nd-play-plus">+</i>'}<span>La juego</span></button>`;
  }

  async function loadResults() {
    const get = async (u) => { try { return await getJSON(u); } catch (e) { return { error: e.message }; } };
    [state.results, state.propResults] = await Promise.all([get(`${API}/api/nba/dd/results`), get(`${API}/api/nba/props/results`)]);
    render();
    pollLive();
  }
  // ── Seguimiento en vivo: lo que lleva cada jugador de las jugadas abiertas ──
  const LIVE_MS = 25000;
  const liveKey = (r) => `${r.game_id}|${r.player_id}|${r.market}|${Number(r.line)}|${r.side}`;
  const liveWon = (r, v) => (r.side === 'over' ? v > r.line : false);          // un "más de" se gana en el momento
  const liveLost = (r, v) => (r.side === 'under' ? v > r.line : false);        // un "menos de" se pierde en el momento
  const LIVE_STATS = { pts: ['pts'], reb: ['reb'], ast: ['ast'], threes: ['threes'], pr: ['pts', 'reb'], pa: ['pts', 'ast'], ra: ['reb', 'ast'], pra: ['pts', 'reb', 'ast'] };
  // Lo que lleva el jugador de una jugada cuyo partido ya empezó (null si todavía no empezó).
  function lvOf(r, stats = state.liveStats) {
    const G = (stats || {})[r.game_id];
    if (!G || G.state === 'pre' || !LIVE_STATS[r.market]) return null;
    const s = (G.players || {})[r.player_id];
    return { value: s ? LIVE_STATS[r.market].reduce((a, k) => a + (s[k] || 0), 0) : 0, min: s ? s.min : 0, game: G };
  }
  let liveTimer = null, liveBusy = false;
  async function pollLive() {
    clearTimeout(liveTimer);
    if (liveBusy || !document.body.classList.contains('nd-on')) return;
    const games = (state.slate && state.slate.games) || [];
    const openRes = ((state.propResults && state.propResults.picks) || []).filter((r) => !r.result);
    const started = games.some((g) => g.state === 'in' || (g.state !== 'post' && Date.parse(g.tip_off) <= Date.now()))
      || openRes.some((r) => new Date(r.tip_off) <= new Date());
    const soon = started || games.some((g) => g.state === 'pre') || openRes.length > 0;
    if (started) {
      liveBusy = true;
      try {
        const L = await getJSON(`${API}/api/nba/props/live`);
        const prev = state.liveStats || {};
        const next = L.games || {};
        // las que se acaban de ganar: aviso y festejo (guardadas y las de las tarjetas de Jugadas)
        const seen = new Set();
        openRes.concat(allPicks('props')).forEach((r) => {
          const k = liveKey(r);
          if (seen.has(k)) return;
          seen.add(k);
          const now = lvOf(r, next), was = lvOf(r, prev);
          if (now && was && now.game.state === 'in' && liveWon(r, now.value) && !liveWon(r, was.value)) {
            state.liveJustWon.add(k);
            toast(`<b>¡Ganada!</b> ${esc(r.player)} · ${esc(lineTxt(r))} ${esc(lbl(r.label))}`);
          }
        });
        state.liveStats = next;
        liveBusy = false;
        if (L.settled && state.view === 'results') return loadResults();
        render();
      } catch (_) { liveBusy = false; }
    }
    if (soon) liveTimer = setTimeout(pollLive, LIVE_MS);
  }
  // Barra de una jugada: se llena hasta el número que hace falta (o el que la rompe, en un "menos de").
  function liveBar(r, compact = false) {
    const lv = !r.result ? lvOf(r) : null;
    const val = r.result ? r.value : lv ? lv.value : null;
    if (val == null) return '';
    const need = Math.floor(r.line) + 1;                 // más de 5,5 → 6
    const over = r.side === 'over';
    const won = r.result ? r.result === 'win' : liveWon(r, val);
    const lost = r.result ? r.result === 'loss' : liveLost(r, val);
    const live = !r.result && lv.game.state === 'in';
    const w = Math.max(3, Math.min(100, val / need * 100));       // siempre se ve un arranque de barra, aunque lleve 0
    const k = liveKey(r);
    const from = state.barShown[k] != null ? state.barShown[k] : 0;
    const verb = r.result ? 'hizo' : 'lleva';
    const txt = over
      ? (won ? `${verb} ${val} · necesitaba ${need}` : `${verb} ${val} de ${need}`)
      : (lost ? `${verb} ${val} · perdía con ${need}` : r.result ? `${verb} ${val} · perdía con ${need}` : `lleva ${val} · pierde con ${need}`);
    const tag = r.result ? '' : live ? `<i class="nd-lv-live"><u></u>En vivo${lv.game.detail ? ` · ${esc(lv.game.detail)}` : ''}</i>`
      : `<i class="nd-lv-wait">${esc(lv.game.detail || 'por cerrar')}</i>`;
    const verdict = won ? `<i class="nd-lv-ok">${I.check}${r.result ? 'Ganada' : '¡Ganada!'}</i>` : lost ? '<i class="nd-lv-ko">Perdida</i>' : '';
    return `
      <span class="nd-lv${won ? ' is-won' : ''}${lost ? ' is-lost' : ''}${live ? ' is-live' : ''}${over ? '' : ' is-under'}${state.liveJustWon.has(k) ? ' is-pop' : ''}">
        ${compact ? '' : `<span class="nd-lv-top">${tag}${verdict}<em>${txt}</em></span>`}
        <span class="nd-lv-bar" title="${esc(`${live ? 'En vivo: ' : ''}${txt}${won ? ' · ganada' : lost ? ' · perdida' : ''}`)}" role="progressbar" aria-valuemin="0" aria-valuemax="${need}" aria-valuenow="${val}"><b style="width:${from}%" data-w="${w}" data-k="${esc(k)}"></b>${over ? '' : '<s></s>'}</span>
      </span>`;
  }
  // después de dibujar: cada barra crece desde donde estaba hasta su valor nuevo
  function fillBars(sec) {
    const bars = sec.querySelectorAll('.nd-lv-bar b[data-w]');
    if (!bars.length) return;
    setTimeout(() => bars.forEach((b) => {
      b.style.width = `${b.dataset.w}%`;
      state.barShown[b.dataset.k] = Number(b.dataset.w);
    }), 40);
    if (state.liveJustWon.size) setTimeout(() => state.liveJustWon.clear(), 2500);
  }
  async function askOdds() {
    const s = state.slate || {};
    const u = s.usage || {};
    const api = apiName(u);
    const ok = window.confirm(`Pedir cuotas de doble-doble, triple-doble y líneas de jugador (puntos, rebotes, asistencias, triples y combinaciones) a ${BOOKS.map((b) => b.name).join(', ')}.\n\nGasta unos ${s.odds_cost ?? 10} pedidos de ${api} (1 por partido${u.api === 'optic' ? '' : ', el mismo plan que Fútbol'}): ${u.api === 'optic' ? `van ${u.used ?? 0} este mes, sin tope mensual` : `van ${u.used ?? '?'} de ${u.limit ?? 250} este mes (quedan ${u.remaining ?? '?'})`}. Sin tope diario.`);
    if (!ok) return;
    state.oddsBusy = true; render();
    try {
      const r = await getJSON(`${API}/api/nba/dd/odds`, { method: 'POST' });
      if (!r.ok) toast(r.reason === 'cap' ? `Ya se pidieron cuotas ${r.cap} veces hoy.` : r.reason === 'month' ? `Se terminó el plan de ${apiName((state.slate || {}).usage)} de este mes.` : 'No se pudieron bajar las cuotas.');
      else toast(`Cuotas nuevas de ${r.books} casa${r.books > 1 ? 's' : ''}${r.props_lines ? ` · ${r.props_lines.toLocaleString('es-AR')} líneas de jugador` : ''}.`);
    } catch (e) { toast(`Error: ${esc(e.message)}`); }
    state.oddsBusy = false;
    state.props = {};
    await loadSlate();
  }
  // Cuotas de un solo partido: 1 pedido, reemplaza las suyas y no toca las de los demás.
  async function askGameOdds(id) {
    const g = (state.slate?.games || []).find((x) => x.game_id === id);
    if (!g || state.oddsGame.has(id)) return;
    const u = (state.slate || {}).usage || {};
    const api = apiName(u);
    const ok = window.confirm(`Pedir cuotas sólo de ${g.away.tricode} @ ${g.home.tricode}: doble-doble, triple-doble y líneas de jugador.\n\nGasta 1 pedido de ${api} (2 si la lista de partidos está vencida): ${u.api === 'optic' ? 'sin tope mensual' : `van ${u.used ?? '?'} de ${u.limit ?? 250} este mes`}. Tarda unos 30 segundos.`);
    if (!ok) return;
    state.oddsGame.add(id); render(true);
    try {
      const r = await getJSON(`${API}/api/nba/dd/odds/${id}`, { method: 'POST' });
      if (!r.ok) {
        toast({ started: 'El partido ya empezó: no hay cuotas previas para pedir.', month: `Quedan muy pocos pedidos de ${api} este mes.`,
          nofixture: `${api} todavía no tiene este partido.` }[r.reason] || 'No se pudieron bajar las cuotas.');
      } else {
        toast(`Cuotas nuevas de ${esc(g.away.tricode)} @ ${esc(g.home.tricode)}${r.props_lines ? ` · ${r.props_lines.toLocaleString('es-AR')} líneas de jugador` : ''}.`);
        if (r.usage && state.slate) state.slate.usage = r.usage;
        delete state.props[id];
        runQueue([id]);
      }
    } catch (e) { toast(`Error: ${esc(e.message)}`); }
    state.oddsGame.delete(id); render(true);
  }
  function toast(html) {
    let t = document.getElementById('nd-toast');
    if (!t) { t = Object.assign(document.createElement('div'), { id: 'nd-toast' }); document.body.appendChild(t); }
    t.innerHTML = html; t.classList.add('is-on');
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('is-on'), 3600);
  }

  // ── piezas ──────────────────────────────────────────────────────────────
  const allSims = () => (state.slate?.games || []).map((g) => state.sims[g.game_id]).filter((s) => s && !s.error);
  const allProps = () => (state.slate?.games || []).map((g) => state.props[g.game_id]).filter((x) => x && !x.error);
  const allPicks = (mk = state.market) => (mk === 'props'
    ? allProps().flatMap((x) => (x.picks || []).map((k) => ({ ...k, sim: state.sims[x.game_id] || x })))
    : allSims().flatMap((s) => (s.picks || []).filter((k) => k.market === mk).map((k) => ({ ...k, sim: s })))).sort((a, b) => b.edge - a.edge);
  // Porcentaje con un decimal cuando es chico (los triple-doble viven abajo del 10%).
  const pctF = (p) => (p < 0.1 ? `${(p * 100).toFixed(1).replace('.', ',')}%` : pct(p));
  const playerOf = (sim, pid) => sim.players.find((p) => p.player_id === pid);

  function pBar(p, cls = '') {
    return `<span class="nd-pbar ${cls}"><i style="width:${Math.max(2, Math.round(p * 100))}%"></i></span>`;
  }
  function statLine(p) {
    return ['pts', 'reb', 'ast'].map((s) => `<span><b>${p.mean[s].toFixed(1).replace('.', ',')}</b>${STAT[s]}</span>`).join('');
  }
  function comboTxt(p) {
    const c = Object.entries(p.combos || {})[0];
    if (!c) return '';
    return c[0].split('+').map((s) => STAT[s]).join(' + ');
  }
  // Las casas con precio, de mejor a peor (como mucho MAX_CHIPS).
  function bookRow(o, pm) {
    const list = BOOKS.filter((b) => o?.books?.[b.id] != null)
      .sort((a, b) => o.books[b.id] - o.books[a.id]).slice(0, MAX_CHIPS);
    if (!list.length) return `<span class="nd-book is-na">${bookBadge(BOOKS[0].id, true)}<b>—</b></span>`;
    return list.map((b) => {
      const pr = o.books[b.id];
      const good = pr * pm - 1 >= 0;
      return `<span class="nd-book${good ? ' is-good' : ''}${o.best_book === b.id ? ' is-best' : ''}">${bookBadge(b.id, true)}<b>${odd(pr)}</b></span>`;
    }).join('');
  }
  function versusOf(sim, team) {
    const other = sim.home.tricode === team ? sim.away : sim.home;
    return `${sim.home.tricode === team ? 'vs' : '@'} ${other.tricode}`;
  }

  function ticket(k) {
    const s = k.sim, p = playerOf(s, k.player_id) || {};
    const o = (p.odds || {})[k.market] || {};
    return `
      <article class="nd-tk nd-tk--${k.market}" style="--tc:${tcolor(k.team)};--tl:url('${esc(teamLogo(k.team))}')" data-tk="${esc(`${s.game_id}|${k.player_id}|${k.market}`)}" tabindex="0" role="button" aria-label="Ver el detalle de ${esc(k.player)}">
        <header class="nd-tk-head">
          ${face(p, 'is-lg')}
          <span class="nd-tk-who">
            <small><img src="${esc(teamLogo(k.team))}" alt="" onerror="this.remove()">${esc(k.team)} ${esc(versusOf(s, k.team))} · ${hhmm(s.tip_off)}</small>
            <b>${esc(k.player)}</b>
            <em>${MK[k.market]}</em>
          </span>
          <span class="nd-tk-odd">${bookBadge(k.book)}<b>${odd(k.price)}</b></span>
        </header>
        <div class="nd-tk-body">
          <div class="nd-tk-prob">
            <b>${pctF(k.p_model)}</b>
            <span>${pBar(k.p_model, k.market === 'td' ? 'is-td' : '')}<small>${k.market === 'dd' && p.p_dd_player != null
              ? `partido simulado <b>${pct(p.p_dd_game)}</b> · su historial <b>${pct(p.p_dd_player)}</b> · mitad y mitad`
              : `pasa en <b>${Math.round(k.p_model * 100)}</b> de cada 100 simulaciones`}</small></span>
          </div>
          <dl class="nd-tk-facts">
            <div><dt>Jugala desde</dt><dd>${odd(k.min_odds)}</dd></div>
            <div><dt>Ventaja</dt><dd class="is-pos">+${Math.round(k.edge * 100)}%</dd></div>
            <div><dt>Minutos</dt><dd>${p.min != null ? Math.round(p.min) : '—'}</dd></div>
          </dl>
          <div class="nd-tk-line">${p.mean ? statLine(p) : ''}${comboTxt(p) ? `<span class="nd-combo">vía ${esc(comboTxt(p))}</span>` : ''}</div>
          <div class="nd-books">${bookRow(o, k.p_model)}</div>
          <div class="nd-tk-act">${playBtn(s.game_id, k.player_id, k.market)}</div>
        </div>
        ${flipLayer(k)}
      </article>`;
  }

  // ── Ficha de la jugada ──────────────────────────────────────────────────
  // Al tocar una tarjeta de Jugadas, la ficha entra DENTRO de la tarjeta (mismo
  // tamaño) desde su costado de afuera. Se pueden tener varias abiertas.
  //   · Índice Cherry (0–100): qué tan buena es la jugada (chance + ventaja).
  //   · Hexágono de juego: lo que la simulación ve del jugador esta noche.
  //   · Pulso de la línea: cómo se movió la cuota desde que abrió (OddsPapi,
  //     historial gratis; se pide al abrir y se guarda unos minutos).
  const lineCache = new Map();          // game|pid|mk → {at, data}
  const lineBusy = new Set();
  const flipKey = (k) => `${k.sim.game_id}|${k.player_id}|${k.market}`;
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const fmt = {
    int: (v) => String(Math.round(v)),
    odd: (v) => v.toFixed(2).replace('.', ','),
  };
  const cnt = (v, f = 'int') => `<span data-count="${v}" data-fmt="${f}">${fmt[f](v)}</span>`;

  // Índice Cherry: 55% la chance (contra un techo por mercado) y 45% la ventaja.
  function cherryIndex(k) {
    const cap = k.market === 'td' ? 0.12 : 0.55;
    return Math.round(100 * (0.55 * clamp01(k.p_model / cap) + 0.45 * clamp01(k.edge / 0.5)));
  }
  const TIER = [[75, 'Élite'], [55, 'Sólida'], [35, 'Picante'], [0, 'Especulativa']];
  const tierOf = (x) => TIER.find(([m]) => x >= m)[1];

  // Hexágono: cada eje de 0 a 100 con lo que sale de la simulación.
  function hexAxes(k, p) {
    const sm = k.sim.sim || {};
    const p10 = Object.values(p.p10 || {}).sort((a, b) => b - a);
    return [
      ['Anota', clamp01((p.mean?.pts || 0) / 30)],
      ['Rebote', clamp01((p.mean?.reb || 0) / 13)],
      ['Asiste', clamp01((p.mean?.ast || 0) / 10)],
      ['Minutos', clamp01(((p.min || 0) - 12) / 26)],
      ['Ritmo', clamp01(((sm.pace || 98) - 94) / 12)],
      ['Doble', clamp01(p10[1] || 0)],            // chance de su SEGUNDA mejor stat en 10+
    ];
  }
  function hexSvg(axes) {
    const cx = 104, cy = 76, R = 46;
    const pt = (i, r) => {
      const a = -Math.PI / 2 + (i * Math.PI) / 3;
      return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
    };
    const ring = (f) => axes.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(',')).join(' ');
    const val = axes.map(([, v], i) => pt(i, R * Math.max(0.06, v)).map((x) => x.toFixed(1)).join(',')).join(' ');
    const labels = axes.map(([n, v], i) => {
      const [x, y] = pt(i, R + 15);
      const anchor = Math.abs(x - cx) < 4 ? 'middle' : x > cx ? 'start' : 'end';
      return `<text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="${anchor}"><tspan class="n">${n}</tspan><tspan class="v" dx="3">${Math.round(v * 100)}</tspan></text>`;
    }).join('');
    const dots = axes.map(([, v], i) => {
      const [x, y] = pt(i, R * Math.max(0.06, v));
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" style="--d:${0.55 + i * 0.05}s"/>`;
    }).join('');
    return `
      <svg class="nd-fx-hex" viewBox="0 0 208 150" aria-label="Hexágono de juego">
        <polygon points="${ring(1)}" class="r"/><polygon points="${ring(0.66)}" class="r"/><polygon points="${ring(0.33)}" class="r"/>
        ${axes.map((_, i) => { const [x, y] = pt(i, R); return `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="r"/>`; }).join('')}
        <g class="val"><polygon points="${val}"/>${dots}</g>
        ${labels}
      </svg>`;
  }

  // Pulso de la línea: una tira chica con el escalón de la mejor casa.
  function pulse(k) {
    const c = lineCache.get(flipKey(k));
    if (!c) return '<div class="nd-fx-pulse is-wait"><b>Pulso</b><span class="nd-fx-skel"></span></div>';
    const ser = Object.entries(c.data.series || {});
    const best = ser.find(([b]) => b === k.book) || ser[0];
    if (!best || !best[1].length) return '<div class="nd-fx-pulse is-wait"><b>Pulso</b><small>sin historial todavía</small></div>';
    const v = best[1], W = 120, H = 26;
    const t0 = Date.parse(v[0].t), t1 = Math.max(Date.parse(v[v.length - 1].t), t0 + 1);
    const ps = v.map((x) => x.p), lo = Math.min(...ps), hi = Math.max(...ps, lo + 0.01);
    const X = (t) => ((Date.parse(t) - t0) / (t1 - t0)) * W, Y = (p) => H - 2 - ((p - lo) / (hi - lo)) * (H - 4);
    let d = '';
    v.forEach((x, i) => { d += i ? `H${X(x.t).toFixed(1)}V${Y(x.p).toFixed(1)}` : `M0 ${Y(x.p).toFixed(1)}`; });
    const open = v[0].p, now = v[v.length - 1].p, mv = (now / open - 1) * 100;
    const dir = mv > 0.5 ? 'is-up' : mv < -0.5 ? 'is-down' : '';
    const word = mv > 3 ? 'la casa la ve menos' : mv < -3 ? 'le están entrando' : 'quieta';
    return `
      <div class="nd-fx-pulse ${dir}${c.fresh ? ' is-new' : ''}">
        <b>Pulso</b>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${d}" pathLength="1"/></svg>
        <span class="nd-fx-mv">${odd(open)} → <em>${odd(now)}</em> <i>${mv > 0.5 ? '▲' : mv < -0.5 ? '▼' : '•'}${Math.abs(Math.round(mv))}%</i></span>
        <small>${word}</small>
      </div>`;
  }

  function flipLayer(k) {
    const key = flipKey(k), f = state.flip.get(key);
    if (!f) return '';
    const p = playerOf(k.sim, k.player_id) || {};
    const ix = cherryIndex(k);
    const anim = f.anim ? ' is-anim' : '';
    return `
      <div class="nd-fx nd-fx--${f.side}${anim}${f.closing ? ' is-closing' : ''}" data-fxin>
        <header class="nd-fx-head">
          ${face(p, 'is-fx')}
          <span><small><img src="${esc(teamLogo(k.team))}" alt="" onerror="this.remove()">${esc(k.team)} ${esc(versusOf(k.sim, k.team))} · ${hhmm(k.sim.tip_off)}</small><b>${esc(k.player)}</b><em>${MK[k.market]}</em></span>
          <button class="nd-fx-x" data-flipx="${esc(key)}" aria-label="Volver a la jugada">${ICON('xmark')}</button>
        </header>
        <div class="nd-fx-body">
          ${hexSvg(hexAxes(k, p))}
          <div class="nd-fx-ix">
            <small>Índice Cherry</small>
            <b>${cnt(ix)}</b>
            <em class="t-${tierOf(ix).toLowerCase().replace('é', 'e')}">${tierOf(ix)}</em>
            <dl>
              <div><dt>Chance</dt><dd>${pctF(k.p_model)}</dd></div>
              <div><dt>Filo</dt><dd class="is-pos">+${Math.round(k.edge * 100)}%</dd></div>
            </dl>
          </div>
        </div>
        ${pulse(k)}
      </div>`;
  }

  async function loadLine(k) {
    const key = flipKey(k);
    const c = lineCache.get(key);
    if ((c && Date.now() - c.at < 5 * 60000) || lineBusy.has(key)) return;
    lineBusy.add(key);
    try {
      const data = await getJSON(`${API}/api/nba/dd/line/${encodeURIComponent(k.sim.game_id)}?player_id=${encodeURIComponent(k.player_id)}&market=${k.market}`);
      lineCache.set(key, { at: Date.now(), data, fresh: true });
    } catch (e) {
      lineCache.set(key, { at: Date.now() - 4 * 60000, data: { series: {} } });
    } finally {
      lineBusy.delete(key);
    }
    if (state.flip.has(key)) render(true);
    const cc = lineCache.get(key);
    setTimeout(() => { if (cc) cc.fresh = false; }, 1500);
  }

  function countUp(root) {
    root.querySelectorAll('.nd-fx.is-anim [data-count]').forEach((el) => {
      const to = parseFloat(el.dataset.count), f = fmt[el.dataset.fmt] || fmt.int;
      if (!isFinite(to) || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
      const t0 = performance.now() + 260, dur = 800;
      el.textContent = f(0);
      const step = (now) => {
        const x = Math.min(1, Math.max(0, (now - t0) / dur));
        el.textContent = f(to * (1 - Math.pow(1 - x, 3)));
        if (x < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  function openFlip(key, card) {
    if (state.flip.has(key)) return;
    const k = allPicks().find((x) => flipKey(x) === key);
    if (!k) return;
    const list = card.closest('.nd-tk-list');
    const r = card.getBoundingClientRect(), lr = list ? list.getBoundingClientRect() : r;
    // La columna de la izquierda entra desde la izquierda; la de la derecha, desde la derecha.
    const side = r.left + r.width / 2 < lr.left + lr.width / 2 ? 'left' : 'right';
    state.flip.set(key, { side, anim: true });
    render(true);
    setTimeout(() => { const f = state.flip.get(key); if (f) f.anim = false; }, 1400);
    loadLine(k);
  }
  function closeFlip(key) {
    const f = state.flip.get(key);
    if (!f || f.closing) return;
    f.closing = true; f.anim = false;
    const el = document.querySelector(`[data-tk="${CSS.escape(key)}"] .nd-fx`);
    if (el) el.classList.add('is-closing');
    setTimeout(() => { state.flip.delete(key); render(true); }, 320);
  }

  function firmList() {
    const rows = [];
    allSims().forEach((s) => s.players.forEach((p) => {
      const o = (p.odds || {}).dd;
      if (o && o.best_price >= FIRM_MIN_ODDS && p.p_dd >= 0.4 && !p.doubt) rows.push({ s, p, o });
    }));
    rows.sort((a, b) => b.p.p_dd - a.p.p_dd);
    return rows;
  }
  function tdWatch() {
    const rows = [];
    allSims().forEach((s) => s.players.forEach((p) => { if (p.p_td >= 0.02) rows.push({ s, p }); }));
    return rows.sort((a, b) => b.p.p_td - a.p.p_td).slice(0, 8);
  }
  function miniRow({ s, p, o }, mk = 'dd', canPlay = false) {
    const pm = p['p_' + mk];
    const oo = o || (p.odds || {})[mk];
    return `
      <div class="nd-mini" data-open="${esc(s.game_id)}">
        ${face(p)}
        <span class="nd-mini-txt"><b>${esc(p.name)}</b><small>${esc(p.team)} ${esc(versusOf(s, p.team))} · ${mk === 'dd' ? 'doble-doble' : 'triple-doble'}</small></span>
        <span class="nd-mini-p"><b>${pctF(pm)}</b>${pBar(pm, mk === 'td' ? 'is-td' : '')}</span>
        ${oo ? `<span class="nd-mini-odd${oo.edge >= 0 ? ' is-ok' : ''}">${bookBadge(oo.best_book, true)}<b>${odd(oo.best_price)}</b></span>` : '<span class="nd-mini-odd is-na">sin cuota</span>'}
        ${canPlay ? playBtn(s.game_id, p.player_id, mk, true) : ''}
      </div>`;
  }

  // Botón de cuotas de la barra superior (al costado de los deportes): al
  // tocarlo se despliega la píldora con la antigüedad, las casas y "Pedir cuotas".
  // Ícono del parte de lesionados (al lado de Cuotas). Gris: nada cambió desde la
  // simulación. Rojo y latiendo: hay novedades, conviene volver a simular.
  const INJ_IC = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="5"/><path d="M12 7.5v9M7.5 12h9"/></svg>';
  const stTxt = (s) => (s ? esc(s) : 'disponible');
  function injTop() {
    if (!state.inj) return '';
    const games = injGames(), n = state.inj.n_changed || 0;
    const busy = games.some((g) => state.busy.has(g.game_id));
    const pop = state.injOpen ? `
      <div class="nd-otop-pop nd-inj-pop" role="dialog" aria-label="Parte de lesionados">
        <div class="nd-inj-h"><b>Parte de lesionados</b><small>revisado ${esc(ago(state.inj.checked_at))}${state.inj.report_at ? ` · última novedad ${esc(ago(state.inj.report_at))}` : ''}</small></div>
        ${n ? `
        <p class="nd-inj-p">Cambió desde que simulaste. Volvé a simular estos partidos y, si querés, pedí cuotas nuevas.</p>
        ${games.map((g) => `
          <div class="nd-inj-g">
            <div class="nd-inj-gh"><b>${esc(g.away)} @ ${esc(g.home)}</b><small>${hhmm(g.tip_off)} · simulado ${esc(ago(g.computed_at))}</small></div>
            ${g.changes.map((c) => `<div class="nd-inj-c"><span>${esc(c.name)} <i>${esc(c.team)}</i></span><em>${stTxt(c.was)} <u>→</u> <b class="${c.now ? 'is-bad' : 'is-good'}">${stTxt(c.now)}</b></em></div>`).join('')}
          </div>`).join('')}
        <div class="nd-inj-acts">
          <button class="nd-btn nd-btn--pill" data-act="inj-sim" ${busy ? 'disabled' : ''}>${busy ? '<span class="nd-spin"></span>Simulando…' : `${I.play}Volver a simular ${games.length} partido${games.length > 1 ? 's' : ''}`}</button>
          <button class="nd-btn nd-btn--pill nd-btn--ghost" data-act="inj-odds">${I.refresh}Pedir cuotas</button>
        </div>` : '<p class="nd-inj-p">Sin novedades: las simulaciones ya usan el parte actual.</p>'}
      </div>` : '';
    return `
      <button class="nd-otop-btn nd-inj-btn${n ? ' is-alert' : ''}${state.injOpen ? ' is-open' : ''}" data-act="inj" aria-expanded="${state.injOpen}" title="${n ? `El parte de lesionados cambió: ${n} novedad${n > 1 ? 'es' : ''}` : 'Parte de lesionados: sin novedades'}">
        ${INJ_IC}<span>Lesionados</span>${n ? `<b>${n}</b>` : ''}
      </button>${pop}`;
  }
  function oddsTop() {
    const s = state.slate || {};
    const age = ageTxt(s.odds_age_s);
    return `
      ${injTop()}
      <button class="nd-otop-btn${state.oddsOpen ? ' is-open' : ''}" data-act="otop" aria-expanded="${state.oddsOpen}" title="Cuotas">
        ${I.clock}<span>Cuotas</span><b>${age || '—'}</b>
      </button>
      ${state.oddsOpen ? `<div class="nd-otop-pop" role="dialog">${oddsBar()}</div>` : ''}`;
  }
  function renderTop() {
    const bar = document.querySelector('.topbar-actions');
    if (!bar) return;
    let el = document.getElementById('nd-otop');
    if (!el) {
      el = Object.assign(document.createElement('div'), { id: 'nd-otop' });
      el.addEventListener('click', onClick);
      bar.prepend(el);
    }
    const html = state.slate ? oddsTop() : '';
    if (html !== el._ndHtml) { el.innerHTML = html; el._ndHtml = html; }
  }
  function oddsBar() {
    const s = state.slate || {};
    const age = ageTxt(s.odds_age_s);
    return `
      <div class="nd-oddsbar">
        <span class="nd-oddsbar-ic">${I.clock}</span>
        <span class="nd-oddsbar-txt">
          <b>${age ? `Cuotas de hace ${age}` : 'Todavía no hay cuotas'}</b>
          <small>${esc(BOOKS.map((b) => b.name).join(' · '))}</small>
        </span>
        <button class="nd-btn nd-btn--pill" data-act="odds" ${state.oddsBusy || (s.odds_cap != null && s.odds_today >= s.odds_cap) || (s.usage && s.usage.remaining === 0) ? 'disabled' : ''}>
          ${state.oddsBusy ? '<span class="nd-spin"></span>Pidiendo…' : `${I.refresh}Pedir cuotas`}
        </button>
      </div>`;
  }

  function hero(title, em, sub) {
    const s = state.slate || {};
    const n = (s.games || []).length;
    const day = slateDay();
    return `
      <header class="nd-hero">
        <div class="nd-hero-kick"><img src="assets/nba/logo-nba.svg" alt="NBA"><span>${day ? esc(dayLabel(day)) : ''}${n ? ` · ${n} partido${n > 1 ? 's' : ''}` : ''}</span></div>
        <h2>${title} <em>${em}</em></h2>
        <p>${sub}</p>
      </header>`;
  }

  // Doble-doble o triple-doble: decide qué jugadas, listas y probabilidades se ven.
  // Selector de mercado: tres tarjetas con ícono, nombre y qué es cada una.
  const MK_TAB = {
    dd: { short: 'Doble-doble', sub: '10+ en dos estadísticas',
      ic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="5" height="14" rx="1.6" fill="currentColor"/><rect x="9.5" y="3" width="5" height="17" rx="1.6" fill="currentColor"/><rect x="16" y="13" width="5" height="7" rx="1.6" fill="currentColor" opacity=".3"/><path d="M2 9.5h20" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2 2" opacity=".7"/></svg>' },
    td: { short: 'Triple-doble', sub: '10+ en tres estadísticas',
      ic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="5" height="14" rx="1.6" fill="currentColor"/><rect x="9.5" y="3" width="5" height="17" rx="1.6" fill="currentColor"/><rect x="16" y="7.5" width="5" height="12.5" rx="1.6" fill="currentColor"/><path d="M2 9.5h20" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2 2" opacity=".7"/></svg>' },
    props: { short: 'Líneas', sub: 'puntos, triples y combinadas',
      ic: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18l5-6 4 3 6-8"/><path d="M15 7h3v3"/><path d="M3 21h18" opacity=".45"/></svg>' },
  };
  function marketSeg() {
    const n = (mk) => allPicks(mk).length;
    return `
      <div class="nd-mk nd-mk--v2" role="tablist" aria-label="Mercado">
        ${['props', 'dd', 'td'].map((mk) => {
          const on = state.market === mk, t = MK_TAB[mk], c = n(mk);
          return `<button role="tab" data-mk="${mk}" aria-selected="${on}" title="${MK[mk]}: ${t.sub}" class="nd-mkt is-${mk}${on ? ' is-on' : ''}">
            <span class="nd-mkt-ic">${t.ic}</span>
            <span class="nd-mkt-txt"><b><span class="nd-mkt-l">${MK[mk]}</span><span class="nd-mkt-s">${t.short}</span></b><small>${t.sub}</small></span>
            ${c ? `<i class="nd-mkt-n">${c}</i>` : ''}
          </button>`;
        }).join('')}
      </div>`;
  }

  // ── tope de cuota y orden de Jugadas ────────────────────────────────────
  const CAPS = [[0, 'Todas'], [2, 'Hasta 2'], [3, 'Hasta 3'], [5, 'Hasta 5'], [10, 'Hasta 10']];
  const SORTS = [['edge', 'Ventaja'], ['p', 'Chance'], ['odds-asc', 'Cuota ↑'], ['odds-desc', 'Cuota ↓']];
  // la mejor de bet365 / Betano / Betsson; 1xBet sólo si ninguna de las tres tiene la línea
  const pickOdds = (k) => {
    if (k.books) { const m = Math.max(0, ...SHARP_BOOKS.map(([id]) => k.books[id] || 0)); if (m) return m; }
    return k.best_odds ?? k.price ?? 0;
  };
  const pickP = (k) => (k.p ?? k.p_model ?? 0);
  function shapePicks(list) {
    const cap = state.oddsCap;
    const out = cap ? list.filter((k) => pickOdds(k) <= cap) : list.slice();
    const by = {
      edge: (a, b) => b.edge - a.edge,
      p: (a, b) => pickP(b) - pickP(a),
      'odds-asc': (a, b) => pickOdds(a) - pickOdds(b),
      'odds-desc': (a, b) => pickOdds(b) - pickOdds(a),
    }[state.sortBy] || ((a, b) => b.edge - a.edge);
    return out.sort(by);
  }
  function pickTools(total, shown) {
    const chip = (attr, v, l, on) => `<button class="nd-tool${on ? ' is-on' : ''}" ${attr}="${v}" aria-pressed="${on}">${l}</button>`;
    return `
      <div class="nd-tools">
        <div class="nd-tools-g" role="group" aria-label="Cuota máxima"><span>Cuota</span>${CAPS.map(([v, l]) => chip('data-cap', v, l, state.oddsCap === v)).join('')}</div>
        <div class="nd-tools-g" role="group" aria-label="Ordenar por"><span>Ordenar</span>${SORTS.map(([v, l]) => chip('data-sort', v, l, state.sortBy === v)).join('')}</div>
        ${state.oddsCap && shown < total ? `<p class="nd-tools-n">${shown} de ${total} · ${total - shown} pagan más de ${odd(state.oddsCap)}</p>` : ''}
      </div>`;
  }
  const capEmpty = () => `<div class="nd-empty">Ninguna jugada paga ${odd(state.oddsCap)} o menos. Subí el tope de cuota.</div>`;

  // ── líneas de jugador ───────────────────────────────────────────────────
  const lineTxt = (it) => `${it.side === 'over' ? 'Más de' : 'Menos de'} ${String(it.line).replace('.', ',')}`;
  const edgeTxt = (e) => { const r = Math.round(e * 100); return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r)}%`; };
  // Tarjeta de línea (v3). Mismo orden de siempre, cada zona legible:
  //   · nombre en Knockout, partido con los dos escudos, ventaja;
  //   · la apuesta en una frase con la etiqueta de la estadística;
  //   · chance / justa / proyección / mercado en casilleros (Roboto, números anchos);
  //   · las casas con la cuota DEBAJO del logo.
  // Dejando el mouse 1 segundo sobre "Mercado" (o tocándolo) sale un cartelito que lo explica.
  const PTAG = { pts: ['PTS', '#c2410c'], reb: ['REB', '#0f766e'], ast: ['AST', '#6d28d9'], threes: ['3P', '#a16207'], pr: ['PTS+REB', '#1d428a'], pa: ['PTS+ASIS', '#1d428a'], ra: ['REB+ASIS', '#1d428a'], pra: ['PTS+REB+ASIS', '#1d428a'] };
  const jcKey = (k) => `${k.game_id}|${k.player_id}|${k.market}|${k.side}|${k.line}`;
  const srcTxt = (s) => (s === 'pinnacle' ? 'Pinnacle' : 'casas');
  // Las casas que se muestran y la que más paga.
  function jcBest(k) {
    const bk = k.books || {};
    const ids = SHARP_BOOKS.map(([id]) => id);
    // si la mejor cuota está en otra casa (1xBet), va como cuarta
    if (k.best_book && !ids.includes(k.best_book) && bk[k.best_book] != null) ids.push(k.best_book);
    const top = Math.max(0, ...ids.map((id) => bk[id] || 0));
    return { bk, ids, top, topId: ids.find((id) => bk[id] === top) };
  }
  function jcBooks(k) {
    const { bk, ids, top } = jcBest(k);
    // la ventaja de la jugada va en un globito sobre la casa que mejor paga (una sola si empatan)
    let tipped = false;
    return ids.map((id) => {
      const pr = bk[id];
      const best = pr != null && pr === top;
      const tip = best && !tipped && k.edge != null && isFinite(k.edge);
      if (tip) tipped = true;
      return `<span class="nd-jc-bk${pr == null ? ' is-na' : ''}${best ? ' is-best' : ''}${pr != null && pr >= k.fair ? ' is-ok' : ''}">${tip ? `<em class="nd-jc-tip${k.edge > 0 ? '' : ' is-neg'}" title="Ventaja: cuánto paga de más esta cuota frente a la justa del modelo"><u>${edgeTxt(k.edge)}</u> ventaja</em>` : best ? '<em>Mejor</em>' : ''}${bookBadge(id)}<b>${pr != null ? odd(pr) : '—'}</b></span>`;
    }).join('');
  }
  // Texto corto del cartelito de "Mercado": qué es ese % y por qué es jugada.
  function jcTip(k) {
    const a = k.anchor, hasMkt = k.p_mkt != null;
    if (!hasMkt && !a) return null;
    const { top, topId } = jcBest(k);
    const best = top || k.best_odds || k.price || 0;
    const who = (s) => (s === 'pinnacle' ? 'Pinnacle' : 'el mercado');
    const pay = best ? ` El modelo le da ${pct(k.p)} y ${book(topId || k.best_book).name} paga ${odd(best)}: ${edgeTxt(k.edge)} de ventaja.` : '';
    return hasMkt
      ? { h: `Mercado ${pct(k.p_mkt)}`, t: `Es la chance que ${who(k.mkt_src)} le da a esta línea, sin su margen.${pay}` }
      : { h: 'Modelo y mercado', t: `En la línea central (${String(a.line).replace('.', ',')}) el modelo da ${pct(a.p)} y ${who(a.src)} ${pct(a.p_mkt)}: coinciden.${pay}` };
  }
  function propTicket(k) {
    const sim = k.sim || {};
    const a = k.anchor;
    const P = state.props[k.game_id] || {};
    const pl = (P.players || []).find((x) => x.player_id === k.player_id) || {};
    const mean = pl.mean ? pl.mean[k.market] : null;
    const num = (x) => String(Math.round(x * 10) / 10).replace('.', ',');
    const key = jcKey(k);
    const tagColor = (PTAG[k.market] || [])[1] || '#1d428a';
    const lg = (tr) => (teamLogo(tr) ? `<img src="${esc(teamLogo(tr))}" alt="" onerror="this.remove()">` : '');
    // partido con los dos escudos: LAL @ MIL
    let vs = esc(k.team);
    if (sim.home && sim.away) {
      const home = sim.home.tricode === k.team;
      const opp = home ? sim.away.tricode : sim.home.tricode;
      vs = `${lg(k.team)}${esc(k.team)} <i>${home ? 'vs' : '@'}</i> ${lg(opp)}${esc(opp)}`;
    }
    const meta = [
      `<span class="nd-jc-vs">${vs}</span>`,
      sim.tip_off ? `<span>${hhmm(sim.tip_off)}</span>` : '',
      pl.min != null ? `<span>${Math.round(pl.min)} min</span>` : '',
    ].join('');
    // proyección: barra hasta el promedio simulado, con la línea marcada en rojo
    let proj = '';
    if (mean != null) {
      const top = Math.max(k.line, mean) * 1.35 || 1;
      proj = `<span class="nd-jc-st" title="Promedio simulado de ${esc(/\+/.test(lbl(k.label)) ? lbl(k.label) : String(k.label).toLowerCase())}; la marca roja es la línea">
            <b>${num(mean)}</b><span class="nd-jc-proj"><i style="width:${Math.min(100, mean / top * 100)}%"></i><u style="left:${Math.min(99, k.line / top * 100)}%"></u></span><small>Proyección</small></span>`;
    }
    const hasMkt = k.p_mkt != null;
    const tip = jcTip(k);
    const tipAttr = tip ? ` data-why="${esc(key)}" data-tip-h="${esc(tip.h)}" data-tip="${esc(tip.t)}" aria-label="${esc(`${tip.h}. ${tip.t}`)}"` : '';
    const mkt = hasMkt
      ? `<button type="button" class="nd-jc-st nd-jc-mk"${tipAttr}>
            <i class="nd-jc-i" aria-hidden="true">i</i><b>${pct(k.p_mkt)}</b><small>Mercado <em>${srcTxt(k.mkt_src)}</em></small></button>` : '';
    const note = a && !hasMkt
      ? `<button type="button" class="nd-jc-note nd-jc-mk"${tipAttr}><i class="nd-jc-i" aria-hidden="true">i</i>En ${String(a.line).replace('.', ',')}: modelo ${pct(a.p)} · mercado ${pct(a.p_mkt)} (${srcTxt(a.src)})</button>` : '';
    return `
      <article class="nd-jc" tabindex="0" role="button" aria-label="Ver el partido de ${esc(k.player)}" data-open="${esc(k.game_id)}" data-jc="${esc(key)}" style="--tc:${tcolor(k.team)};--tl:url('${esc(teamLogo(k.team))}');--sc:${tagColor}">
        <header class="nd-jc-top">
          <span class="nd-jc-pic">${face(k)}${teamLogo(k.team) ? `<img class="nd-jc-tm" src="${teamLogo(k.team)}" alt="">` : ''}</span>
          <span class="nd-jc-who"><b>${esc(k.player)}</b><small>${meta}</small></span>
          ${jcBest(k).top ? '' : `<span class="nd-jc-edge"><b>${edgeTxt(k.edge)}</b><small>ventaja</small></span>`}
        </header>
        <div class="nd-jc-bet"><span class="nd-jc-line">${lineTxt(k)} <b>${esc(lbl(k.label))}</b></span></div>
        ${liveBar(k, true)}
        ${note}
        <div class="nd-jc-stats">
          <span class="nd-jc-st is-p"><b>${pct(k.p)}</b><small>Chance</small></span>
          <span class="nd-jc-st"><b>${odd(k.fair)}</b><small>Justa</small></span>
          ${proj}${mkt}
        </div>
        <div class="nd-jc-books">${jcBooks(k)}</div>
      </article>`;
  }
  // Las tres casas, como en Fútbol: la que más paga con "Mejor", en verde las que
  // pagan la cuota justa o más, "—" si la casa no tiene esa línea.
  function ppBooks(k) {
    const bk = k.books || {};
    const ids = SHARP_BOOKS.map(([id]) => id);
    // si la mejor cuota está en otra casa (1xBet), va como cuarta
    if (k.best_book && !ids.includes(k.best_book) && bk[k.best_book] != null) ids.push(k.best_book);
    const top = Math.max(0, ...ids.map((id) => bk[id] || 0));
    return ids.map((id) => {
      const pr = bk[id];
      const best = pr != null && pr === top;
      return `<span class="nd-ppb${pr == null ? ' is-na' : ''}${best ? ' is-best' : ''}${pr != null && pr >= k.fair ? ' is-ok' : ''}">${best ? '<em>Mejor</em>' : ''}${bookBadge(id, true)}<b>${pr != null ? odd(pr) : '—'}</b></span>`;
    }).join('');
  }
  function propsPicksView() {
    const picks = allPicks('props');
    const shown = shapePicks(picks);
    const pending = (state.slate?.games || []).filter((g) => !state.props[g.game_id]).length;
    const noOdds = allProps().length && allProps().every((x) => !x.players.length);
    return `
      ${hero('Líneas', 'de jugador', 'La misma simulación del doble-doble da la distribución entera de cada jugador: puntos, rebotes, asistencias, triples y sus sumas, que se mueven juntas. bet365 sólo ofrece overs; Betano, Betsson y 1xBet dan también unders.')}
      ${marketSeg()}
      ${pending > 0 ? `<div class="nd-busy"><span class="nd-spin"></span>Calculando ${pending} partido${pending > 1 ? 's' : ''}…</div>` : ''}
      <section class="nd-block">
        <div class="nd-block-head"><b>Jugadas</b><small>${picks.length ? `${picks.length} con ventaja de +12% o más · una por jugador` : 'ninguna con ventaja por ahora'}</small></div>
        ${picks.length ? pickTools(picks.length, shown.length) + (shown.length ? `<div class="nd-pp-list">${shown.map(propTicket).join('')}</div>` : capEmpty())
          : `<div class="nd-empty">${noOdds || !allProps().length ? 'Pedí las cuotas: el mismo pedido del doble-doble trae las líneas de jugador.' : 'Con las cuotas de ahora ninguna línea paga más de lo que vale. Mirá cada partido para ver todas.'}</div>`}
      </section>
      <p class="nd-rec-note">Una jugada paga al menos 12% más que la cuota justa del modelo y tiene 15% o más de chance. Además, en la línea central del jugador el modelo tiene que coincidir (±10 puntos) con el mercado sin margen: Pinnacle, o las casas que dan over y under. Así la ventaja está en cómo la casa cotiza la escalera, no en que el modelo lea mal al jugador. Backtest 2025-26: 571.737 líneas, calibradas.</p>`;
  }
  function propsPanel(g, sim) {
    const P = state.props[g.game_id];
    if (!P) return `<div class="nd-busy"><span class="nd-spin"></span>Buscando las líneas…</div>`;
    if (P.error) return `<div class="nd-busy is-err">${esc(P.error)}</div>`;
    if (!P.players.length) return `<div class="nd-empty">Todavía no hay líneas de jugador para este partido. Pedí las cuotas.</div>`;
    // La lista de todos los jugadores se sacó: quedan Destacados, Sharp y Parlay.
    return '';
  }
  function propPlayer(gid, p) {
    const key = `${gid}|${p.player_id}`;
    const open = state.propOpen.has(key);
    const mks = PMK.filter((m) => p.markets[m]);
    const mk = mks.includes(state.propMk[key]) ? state.propMk[key] : mks[0];
    const picks = mks.reduce((n, m) => n + p.markets[m].filter((it) => it.pick).length, 0);
    const mean = (m) => (p.mean[m] != null ? String(p.mean[m].toFixed(1)).replace('.', ',') : '—');
    return `
      <div class="nd-pr${open ? ' is-open' : ''}${picks ? ' is-pick' : ''}">
        <button class="nd-pr-head" data-prop="${esc(key)}" aria-expanded="${open}">
          ${face(p)}
          <span class="nd-pl-who"><b>${esc(p.name)}${p.doubt ? `<i class="nd-doubt">${esc(p.status || 'duda')}</i>` : ''}</b>
            <small><span><b>${mean('pts')}</b>PTS</span><span><b>${mean('reb')}</b>REB</span><span><b>${mean('ast')}</b>AST</span><span><b>${mean('threes')}</b>3P</span></small></span>
          ${picks ? `<span class="nd-tag is-picks"><b>${picks}</b> jugada</span>` : ''}
          <span class="nd-pr-chev">${I.chev}</span>
        </button>
        ${open ? `
        <div class="nd-pr-body">
          <div class="nd-pr-mks">${mks.map((m) => `<button data-pmk="${esc(key)}" data-m="${m}" class="${m === mk ? 'is-on' : ''}">${PMK_SHORT[m]}<small>${mean(m)}</small></button>`).join('')}</div>
          <div class="nd-pr-head2"><span>Línea</span><span>Modelo</span><span>Mercado</span><span>Mejor cuota</span></div>
          ${p.markets[mk].map((it) => `
            <div class="nd-pr-row${it.pick ? ' is-pick' : it.dubious ? ' is-dub' : it.edge >= 0 ? ' is-ok' : ''}">
              <span class="nd-pr-line"><b>${lineTxt(it)}</b><small>justa ${odd(it.fair)}</small></span>
              <span class="nd-pr-p">${pctF(it.p)}</span>
              <span class="nd-pr-m">${it.p_mkt != null ? pctF(it.p_mkt) : '—'}</span>
              <span class="nd-pr-odd">${bookBadge(it.best_book, true)}<b>${odd(it.best_odds)}</b><i>${edgeTxt(it.edge)}</i></span>
            </div>`).join('')}
        </div>` : ''}
      </div>`;
  }

  // ── Destacados · Sharp · Parlay de cada partido (como en Fútbol) ─────────
  // Sharp: entre bet365, Betano y Betsson una paga 0,75 o más por encima de otra
  // que paga menos de 2,15. No depende del modelo.
  const SHARP_GAP = 0.75;
  const SHARP_REST_MAX = 2.15;
  const SHARP_BOOKS = [['bet365.bet.ar', 'bet365'], ['betano'], ['betsson']];
  // Parlay: según el modelo, la línea más probable de cada jugador con cuota
  // justa de 1,40 o más, para aprovechar boosts.
  const PARLAY_MIN_FAIR = 1.40;
  const PARLAY_MIN_P = 0.38;
  const PARLAY_MIN_MIN = 24;            // minutos esperados: juega en serio
  const PARLAY_PER_PLAYER = 2;         // líneas por jugador, de estadísticas distintas
  const PARLAY_MAX = 6;                // se muestran de a 6: "Mostrar más jugadas" suma otras 6
  const PARLAY_NEAR = 0.93;            // "cerca": la casa paga al menos el 93% de la cuota justa
  const BOOST = 0.25;                  // booster: la cuota de la casa sube 25% (cuota × 1,25)
  // Normas del booster: es de Betano y sólo vale si la cuota original es 1,50 o más.
  const BOOST_BOOK = 'betano';
  const BOOST_MIN = 1.50;
  const boosted = (bk, price, mult) => (mult > 1 && bk === BOOST_BOOK && price >= BOOST_MIN ? price * mult : price);
  function bestEff(it, mult) {
    let eff = it.best_odds || 0;
    Object.entries(it.books || {}).forEach(([bk, pr]) => { const v = pr ? boosted(bk, pr, mult) : 0; if (v > eff) eff = v; });
    return eff;
  }
  const HL_MIN_ODDS = 1.40;
  const HL_PICK_MAX_ODDS = 2.50;       // jugadas con valor de Destacados: a cuota más alta perdían
  const HL_FIRM = 5;                   // "lo más firme" por partido
  // Íconos de las pestañas: estrella (destacados), líneas que se abren (sharp), eslabones (parlay).
  const HL_IC = {
    hl: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.8l2.7 5.8 6.3.7-4.7 4.3 1.3 6.2L12 16.7l-5.6 3.1 1.3-6.2L3 9.3l6.3-.7z" fill="currentColor"/></svg>',
    sharp: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h7l4-6h7M14 18h7M10 12l4 6"/></svg>',
    parlay: '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></svg>',
  };
  const pKey = (pid, mk, it) => `${pid}|${mk}|${it.line}|${it.side}`;
  const lab = (P, mk) => lbl(P.labels && P.labels[mk]) || PMK_SHORT[mk] || mk;
  const surnameOf = (n) => { const w = String(n || '').trim().split(/\s+/); return w.length > 1 ? w.slice(1).join(' ') : w[0]; };
  // Todas las líneas del partido: jugador, mercado y línea (over y under por separado).
  function propLines(P) {
    const out = [];
    (P.players || []).forEach((p) => Object.entries(p.markets || {}).forEach(([mk, items]) => items.forEach((it) => out.push({ p, mk, it, key: pKey(p.player_id, mk, it) }))));
    return out;
  }
  function propSharps(P) {
    const rows = [];
    propLines(P).forEach((r) => {
      if (r.p.doubt) return;
      const q = [];
      SHARP_BOOKS.forEach((ids) => {
        const id = ids.find((x) => r.it.books && r.it.books[x]);
        if (id) q.push({ book: id, price: r.it.books[id] });
      });
      if (q.length < 2) return;
      q.sort((a, b) => b.price - a.price);
      const hi = q[0], lo = q[q.length - 1];
      if (hi.price - lo.price < SHARP_GAP || lo.price >= SHARP_REST_MAX) return;
      rows.push({ ...r, q, gap: hi.price - lo.price, pgap: 1 / lo.price - 1 / hi.price });
    });
    rows.sort((a, b) => b.pgap - a.pgap);
    const seen = new Set(), out = [];
    out.allKeys = new Set(rows.map((r) => r.key));
    for (const r of rows) {
      if (seen.has(r.p.player_id)) continue;
      seen.add(r.p.player_id);
      out.push(r);
      if (out.length >= 3) break;
    }
    return out;
  }
  // El Parlay usa la probabilidad mezclada con el mercado, igual que el resto.
  // Entran las líneas con buenas chances que alguna casa paga cerca o por encima de
  // la cuota justa. Con el booster (se activa a mano: no todos los partidos lo
  // tienen) la cuota de la casa cuenta × 1,25 y aparecen jugadas nuevas.
  function propParlay(P, id) {
    const boost = state.hlBoost.has(id);
    const pick = (mult) => {
      const best = new Map();
      propLines(P).forEach((r) => {
        const { p, it } = r;
        if (p.doubt || (p.min != null && p.min < PARLAY_MIN_MIN) || it.unanchored || it.dubious) return;
        if (!(it.fair >= PARLAY_MIN_FAIR) || !(it.p >= PARLAY_MIN_P) || !it.best_odds) return;
        const eff = bestEff(it, mult);
        if (eff < PARLAY_MIN_FAIR || eff < it.fair * PARLAY_NEAR) return;
        // la más probable de cada jugador en cada mercado
        const k = p.player_id + '|' + r.mk;
        const b = best.get(k);
        if (!b || it.p > b.it.p) best.set(k, { ...r, mult, eff, ratio: eff / it.fair - 1 });
      });
      // por jugador: su línea más probable y, si tiene, otra de una estadística distinta
      // (puntos y rebotes sí; puntos y P+R no, porque comparten los puntos)
      const byPl = new Map();
      [...best.values()].sort((a, b) => b.it.p - a.it.p).forEach((r) => {
        const got = byPl.get(r.p.player_id) || [];
        const parts = LIVE_STATS[r.mk] || [r.mk];
        if (got.length >= PARLAY_PER_PLAYER) return;
        if (got.some((g) => (LIVE_STATS[g.mk] || [g.mk]).some((s) => parts.includes(s)))) return;
        got.push(r); byPl.set(r.p.player_id, got);
      });
      return [...byPl.values()].flat();
    };
    // Orden variado: se va eligiendo la de más chance, pero cada repetición de mercado,
    // de lado (más/menos), de equipo o de jugador le resta un poco, para que la
    // primera tanda no sea toda de lo mismo.
    const mix = (rows) => {
      const left = rows.slice(), out = [], n = { mk: {}, side: {}, team: {}, pl: {} };
      const score = (r) => r.it.p - 0.04 * (n.mk[r.mk] || 0) - 0.015 * (n.side[r.it.side] || 0)
        - 0.02 * (n.team[r.p.team] || 0) - 0.08 * (n.pl[r.p.player_id] || 0);
      while (left.length) {
        let bi = 0;
        for (let i = 1; i < left.length; i++) if (score(left[i]) > score(left[bi])) bi = i;
        const r = left.splice(bi, 1)[0];
        out.push(r);
        n.mk[r.mk] = (n.mk[r.mk] || 0) + 1; n.side[r.it.side] = (n.side[r.it.side] || 0) + 1;
        n.team[r.p.team] = (n.team[r.p.team] || 0) + 1; n.pl[r.p.player_id] = (n.pl[r.p.player_id] || 0) + 1;
      }
      return out;
    };
    const rows = pick(boost ? 1 + BOOST : 1);
    if (boost) {
      const base = new Set(pick(1).map((r) => r.key));
      rows.forEach((r) => { r.unlocked = !base.has(r.key); });
    }
    return mix(rows);
  }
  // Hasta 2 casas que pagan `fair` o más (de mayor a menor, con el multiplicador del
  // booster si corresponde). Si ninguna llega, la que más paga.
  function fairBooks(it, fair, mult = 1) {
    const seen = new Set();
    const all = Object.entries(it.books || {}).map(([bk, price]) => ({ book: bk, price, eff: boosted(bk, price, mult) }))
      .sort((a, b) => b.eff - a.eff)
      .filter((x) => { const n = book(x.book).name; if (seen.has(n)) return false; seen.add(n); return true; });
    const good = all.filter((x) => x.eff >= fair).slice(0, 2);
    if (good.length) return good;
    if (all.length) return all.slice(0, 1);
    return it.best_book && it.best_odds ? [{ book: it.best_book, price: it.best_odds, eff: boosted(it.best_book, it.best_odds, mult) }] : [];
  }
  // Hasta 3 jugadas con valor (cuota hasta 2,50) y 5 "lo más firme" (la más probable que paga 1,40 o más).
  function propHlItems(P) {
    const items = [];
    const pl = (id) => (P.players || []).find((x) => x.player_id === id) || {};
    if (P.frozen && P.hl_saved) {          // partido empezado: los que quedaron guardados antes del inicio
      return P.hl_saved.map((k) => ({ kind: k.kind === 'firme' ? 'sure' : 'pick',
        p: { ...pl(k.player_id), player_id: k.player_id, name: k.player, photo: k.photo, team: k.team },
        mk: k.market, it: k, key: pKey(k.player_id, k.market, k),
        sub: k.kind === 'firme' ? `${pct(k.p)} · lo más firme con cuota jugable` : `${pct(k.p)} · justa ${odd(k.fair)}`,
        ok: k.kind !== 'firme' || k.edge >= 0 }));
    }
    const top = (P.picks || []).filter((k) => (k.best_odds || 0) <= HL_PICK_MAX_ODDS).sort((a, b) => b.p - a.p).slice(0, 3);
    top.forEach((k) => items.push({ kind: 'pick', p: { ...pl(k.player_id), name: k.player, photo: k.photo, team: k.team },
      mk: k.market, it: k, key: pKey(k.player_id, k.market, k),
      sub: `${pct(k.p)} · justa ${odd(k.fair)}`, ok: true }));
    const seen = new Set(top.map((k) => k.player_id));
    const sure = propLines(P).filter(({ p, it }) => !p.doubt && !it.unanchored && !it.dubious && it.p >= 0.55 && it.best_odds >= HL_MIN_ODDS);
    sure.sort((a, b) => (b.it.p - a.it.p) || (b.it.best_odds - a.it.best_odds));
    let n = 0;
    for (const r of sure) {
      if (n >= HL_FIRM || seen.has(r.p.player_id)) continue;
      seen.add(r.p.player_id); n++;
      items.push({ kind: 'sure', ...r, sub: `${pct(r.it.p)} · lo más firme con cuota jugable`, ok: r.it.edge >= 0 });
    }
    return items;
  }
  function propsHl(g) {
    const P = state.props[g.game_id];
    if (!P || P.error || !(P.players || []).length) return '';
    const id = g.game_id;
    const mode = state.hlMode[id] || 'hl';
    const items = propHlItems(P);
    const sharps = propSharps(P);
    const allLegs = propParlay(P, id);
    const boostOn = mode === 'parlay' && state.hlBoost.has(id);
    const nUnl = allLegs.filter((r) => r.unlocked).length;
    const legs = allLegs.slice(0, state.parlayN[id] || PARLAY_MAX);
    const hlKeys = new Set(items.map((x) => x.key));
    const anim = state.hlSpin === id;
    if (anim) state.hlSpin = null;
    const who = (r, tags = '') => `
      <span class="nd-hl-who">
        <b>${esc(r.p.name)}${tags}</b>
        <em>${esc(lineTxt(r.it))} <i${PTAG[r.mk] ? ` style="--sc:${PTAG[r.mk][1]}"` : ''}>${esc(lab(P, r.mk))}</i></em>
      </span>`;
    const pic = (p) => `<span class="nd-hl-pic" style="--tc:${tcolor(p.team)}">${face(p)}${p.team && teamLogo(p.team) ? `<img class="nd-hl-tm" src="${teamLogo(p.team)}" alt="">` : ''}</span>`;
    // tip: globito (ventaja / sobre la justa) que va arriba de la primera casa de la fila
    const price = (bk, x, cls = '', was = null, tip = '') => `<span class="nd-hl-odd${cls}${tip ? ' has-tip' : ''}">${tip}${bookBadge(bk, true)}<b>${odd(x)}</b>${was != null ? `<s>${odd(was)}</s>` : ''}</span>`;
    const boxes = (list, one) => { const h = list.map(one).join(''); return list.length > 1 ? `<span class="nd-hl-ladder is-fair">${h}</span>` : h; };
    const TABS = [['hl', 'Destacados', items.length], ['sharp', 'Sharp', sharps.length], ['parlay', 'Parlay', allLegs.length]];
    const SUB = {
      hl: 'jugadas con valor y lo más firme de la simulación · se guardan en Resultados',
      sharp: 'una casa paga 0,75 o más arriba de otra que paga menos de 2,15 · no depende del modelo',
      parlay: 'la línea más probable de cada jugador que alguna casa paga cerca o por encima de la cuota justa',
    };
    let rows;
    if (mode === 'sharp') {
      rows = sharps.map((r, i) => `
        <div class="nd-hl-row">
          <span class="nd-hl-n">${i + 1}</span>${pic(r.p)}
          ${who(r, hlKeys.has(r.key) ? '<i class="nd-hl-chip is-hl">destacado</i>' : '')}
          <span class="nd-hl-end">
            <span class="nd-hl-stat is-gap"><b>+${r.gap.toFixed(2).replace('.', ',')}</b><small>diferencia</small></span>
            <span class="nd-hl-ladder">${r.q.map((x, j) => price(x.book, x.price, j === 0 ? ' is-top' : '')).join('')}</span>
          </span>
        </div>`).join('') || '<p class="nd-hl-none">Las casas pagan parecido en este partido: ninguna línea sharp.</p>';
    } else if (mode === 'parlay') {
      rows = legs.map((r, i) => {
        const ok = r.ratio >= 0;
        return `
        <div class="nd-hl-row is-leg${r.unlocked ? ' is-unlocked' : ''}">
          <span class="nd-hl-n">${i + 1}</span>${pic(r.p)}
          ${who(r, r.unlocked ? '<i class="nd-hl-chip is-boost">con booster</i>' : '')}
          <span class="nd-hl-end">
            <span class="nd-hl-stat is-p"><b>${pct(r.it.p)}</b><small>justa ${odd(r.it.fair)}</small></span>
            ${boxes(fairBooks(r.it, r.it.fair, r.mult), (x, j) => price(x.book, x.eff, x.eff >= r.it.fair ? ' is-ok' : '', x.eff > x.price ? x.price : null,
              j ? '' : `<em class="nd-hl-tip ${ok ? 'is-ok' : 'is-low'}" title="${ok ? 'La casa paga la cuota justa o más' : 'La casa paga un poco menos que la cuota justa'}${r.mult > 1 ? ' (con el booster)' : ''}"><u>${edgeTxt(r.ratio)}</u> ${ok ? 'sobre la justa' : 'bajo la justa'}</em>`))}
          </span>
        </div>`;
      }).join('') || `<p class="nd-hl-none">Ninguna línea con buenas chances tiene una cuota cerca de la justa en este partido.${boostOn ? '' : ' Si la casa ofrece booster, activalo arriba.'}</p>`;
      if (allLegs.length > legs.length) {
        rows += `<button class="nd-hl-more" data-parlaymore="${esc(id)}">Mostrar más jugadas<small>${legs.length} de ${allLegs.length}</small></button>`;
      }
    } else {
      rows = items.map((x, i) => `
        <div class="nd-hl-row is-${x.kind}">
          <span class="nd-hl-n">${i + 1}</span>${pic(x.p)}
          ${who(x, `<i class="nd-hl-chip is-${x.kind}">${x.kind === 'pick' ? 'valor' : 'firme'}</i>${sharps.allKeys.has(x.key) ? '<i class="nd-hl-chip is-sharp">sharp</i>' : ''}`)}
          <span class="nd-hl-end">
            <span class="nd-hl-stat is-p"><b>${pct(x.it.p)}</b><small>justa ${odd(x.it.fair)}</small></span>
            ${boxes(fairBooks(x.it, x.it.fair), (b, j) => price(b.book, b.price, x.ok && x.it.p * b.price - 1 > 0 ? ' is-ok' : '', null,
              j ? '' : `<em class="nd-hl-tip ${x.it.edge > 0.005 ? 'is-ok' : 'is-neg'}" title="Ventaja: cuánto paga de más la mejor cuota frente a la justa del modelo"><u>${edgeTxt(x.it.edge)}</u> ventaja</em>`))}
          </span>
        </div>`).join('') || '<p class="nd-hl-none">Sin jugadas con valor en este partido.</p>';
    }
    return `
      <section class="nd-hl is-${mode}${boostOn ? ' is-boost' : ''}${anim ? ' is-anim' : ''}">
        <header class="nd-hl-top">
          <div class="nd-hl-tabs" role="tablist" aria-label="Destacados del partido">
            ${TABS.map(([k, l, n]) => `<button role="tab" data-ndhl="${esc(id)}" data-mode="${k}" aria-selected="${mode === k}" class="is-${k}${mode === k ? ' is-on' : ''}">${HL_IC[k]}<span>${l}</span>${n ? `<i>${n}</i>` : ''}</button>`).join('')}
          </div>
          <div class="nd-hl-subrow">
            <p class="nd-hl-sub">${SUB[mode]}</p>
            <button class="nd-hl-shot" data-hlshot="${esc(id)}" ${state.shotBusy.has(id) ? 'disabled' : ''} title="Descarga una foto de esta lista para compartir">
              ${state.shotBusy.has(id) ? '<span class="nd-spin"></span>' : '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/></svg>'}<span>${state.shotBusy.has(id) ? 'Armando…' : 'Descargar foto'}</span>
            </button>
          </div>
          ${mode === 'parlay' ? `
          <div class="nd-hl-boostrow">
            <button class="nd-hl-boostbtn${boostOn ? ' is-on' : ''}" data-ndboost="${esc(id)}" role="switch" aria-checked="${boostOn}"
              title="No todos los partidos tienen booster: activalo sólo si Betano lo ofrece en este. Suma 25% a las cuotas de Betano de 1,50 o más"><span>Booster +25%</span><i class="nd-hl-sw" aria-hidden="true"></i></button>
            <small>${boostOn ? `Booster sumado a las cuotas de Betano de 1,50 o más${nUnl ? ` · ${nUnl} jugada${nUnl > 1 ? 's' : ''} que sólo entra${nUnl > 1 ? 'n' : ''} con booster` : ''}` : 'Activalo si Betano ofrece booster en este partido (cuota mínima 1,50): aparecen más jugadas'}</small>
          </div>` : ''}
        </header>
        <div class="nd-hl-rows">${rows}</div>
      </section>`;
  }

  // ── Foto para compartir: Destacados / Sharp / Parlay de un partido ───────
  // Se dibuja en un canvas propio (no una captura de pantalla): 2160 px de ancho.
  function shotRows(P, mode, id) {
    const all = (r, tag, stats, bk, price, good) => ({ p: r.p, line: `${lineTxt(r.it)} ${lab(P, r.mk)}`, tag, stats, bk, price, good });
    if (mode === 'sharp') {
      return propSharps(P).map((r) => all(r, null, [[`+${r.gap.toFixed(2).replace('.', ',')}`, 'diferencia', '#b45309'],
        [odd(r.q[r.q.length - 1].price), `en ${book(r.q[r.q.length - 1].book).name}`, '#64708a']], r.q[0].book, r.q[0].price, true));
    }
    if (mode === 'parlay') {
      return propParlay(P, id).slice(0, state.parlayN[id] || PARLAY_MAX).map((r) => {
        const ok = r.ratio >= 0;
        const rw = all(r, r.unlocked ? ['CON BOOSTER', '#1d428a'] : null, [[pct(r.it.p), 'probabilidad', '#0b1d40'], [odd(r.it.fair), 'justa', '#64708a'],
          [edgeTxt(r.ratio), ok ? 'sobre la justa' : 'bajo la justa', ok ? '#15803d' : '#b45309']], r.it.best_book, r.eff, ok);
        rw.books = fairBooks(r.it, r.it.fair, r.mult).map((x) => ({ bk: x.book, price: x.eff, good: x.eff >= r.it.fair }));
        return rw;
      });
    }
    return propHlItems(P).map((x) => {
      const rw = all(x, x.kind === 'pick' ? ['VALOR', '#1d428a'] : ['FIRME', '#15803d'],
        [[pct(x.it.p), 'probabilidad', '#0b1d40'], [edgeTxt(x.it.edge), 'ventaja', x.it.edge > 0.005 ? '#15803d' : '#64708a']],
        x.it.best_book, x.it.best_odds, x.ok && x.it.edge > 0.005);
      rw.books = fairBooks(x.it, x.it.fair).map((b) => ({ bk: b.book, price: b.price, good: x.ok && x.it.p * b.price - 1 > 0.005 }));
      return rw;
    });
  }
  async function shotHl(id) {
    const P = state.props[id];
    const g = (state.slate?.games || []).find((x) => String(x.game_id) === String(id));
    if (!P || !g || !window.CPShot) return;
    const mode = state.hlMode[id] || 'hl';
    const rows = shotRows(P, mode, id);
    if (!rows.length) return toast('No hay jugadas para la foto en esta pestaña.');
    const TITLE = { hl: 'Destacados', sharp: 'Sharp', parlay: 'Parlay' }[mode];
    const SUBT = { hl: 'Jugadas con valor y lo más firme de la simulación', sharp: 'Una casa paga mucho más que otra por la misma línea',
      parlay: state.hlBoost.has(id) ? 'Las líneas más probables, mezclando mercados y equipos · cuotas con booster +25%' : 'Las líneas más probables, mezclando mercados y equipos, con cuotas cerca de la justa' }[mode];
    const tip = g.tip_off ? new Date(g.tip_off) : null;
    const day = (tip || new Date()).toLocaleDateString('sv-SE');     // fecha local del partido, AAAA-MM-DD
    const name = await CPShot.make({
      theme: 'nba', league: 'assets/nba/logo-nba.svg', title: TITLE, subtitle: SUBT, sep: '@',
      teams: [{ label: g.away.tricode, logo: teamLogo(g.away.tricode) }, { label: g.home.tricode, logo: teamLogo(g.home.tricode) }],
      when: tip ? CPShot.whenTxt(tip) : '',
      rows: rows.map((r) => ({ name: r.p.name, line: r.line, tag: r.tag, stats: r.stats, book: book(r.bk).name || r.bk, bookLogo: book(r.bk).logo || CPShot.BOOK_LOGO[r.bk], bookColor: CPShot.BOOK_COLOR[r.bk],
        price: odd(r.price), good: r.good, photo: r.p.photo, photoFit: 'head', teamLogo: teamLogo(r.p.team), color: tcolor(r.p.team),
        books: (r.books || []).map((b) => ({ book: book(b.bk).name || b.bk, bookLogo: book(b.bk).logo || CPShot.BOOK_LOGO[b.bk], bookColor: CPShot.BOOK_COLOR[b.bk], price: odd(b.price), good: b.good })) })),
      footer: `Modelo propio · 20.000 simulaciones del partido · cuotas al ${CPShot.stamp()}`,
      filename: `cherrypick-${g.away.tricode}-${g.home.tricode}-${TITLE.toLowerCase()}-${day}.png`,
      shareTitle: `${TITLE} · ${g.away.tricode} @ ${g.home.tricode}`,
    });
    if (name) toast(`<b>Foto lista</b> ${esc(name)}`);
  }

  // ── vistas ──────────────────────────────────────────────────────────────
  function picksView() {
    const mk = state.market;
    if (mk === 'props') return propsPicksView();
    const picks = allPicks();
    const shown = shapePicks(picks);
    const pending = (state.slate?.games || []).length - allSims().length;
    const firm = firmList();
    const td = tdWatch();
    const dub = [];
    allSims().forEach((s) => s.players.forEach((p) => { const o = (p.odds || {})[mk]; if (o && o.dubious) dub.push({ s, p, mk, o }); }));
    return `
      ${hero(MK[mk], 'esta noche', 'Cada partido se juega 20.000 veces entero: ritmo, tiros, rebotes y asistencias de los dos equipos, repartidos entre sus jugadores. Una jugada es cuando la casa paga más de lo que vale.')}
      ${marketSeg()}
      ${pending > 0 ? `<div class="nd-busy"><span class="nd-spin"></span>Simulando ${pending} partido${pending > 1 ? 's' : ''}… la primera vez baja el historial de cada jugador (1–2 min).</div>` : ''}
      <section class="nd-block">
        <div class="nd-block-head"><b>Jugadas</b><small>${picks.length ? `${picks.length} con ventaja de +8% o más` : 'ninguna con ventaja por ahora'}</small></div>
        ${picks.length ? pickTools(picks.length, shown.length) + (shown.length ? `<div class="nd-tk-list">${shown.map(ticket).join('')}</div>` : capEmpty())
          : `<div class="nd-empty">${state.slate?.odds_age_s == null ? 'Pedí las cuotas para comparar con la simulación.' : `Con las cuotas de ahora ninguna paga más de lo que vale. Mirá "${mk === 'dd' ? 'Lo más firme' : 'Los más cerca'}" o los partidos.`}</div>`}
      </section>
      ${mk === 'dd' && firm.length ? `
      <section class="nd-block">
        <div class="nd-block-head"><b>Lo más firme</b><small>los doble-doble más probables que pagan ${odd(FIRM_MIN_ODDS)} o más · van a Resultados</small></div>
        <div class="nd-minis">${firm.map((r) => miniRow(r, 'dd', true)).join('')}</div>
      </section>` : ''}
      ${mk === 'td' && td.length ? `
      <section class="nd-block">
        <div class="nd-block-head"><b>Los más cerca</b><small>los triple-doble más probables esta noche, con o sin cuota</small></div>
        <div class="nd-minis">${td.map((r) => miniRow(r, 'td')).join('')}</div>
      </section>` : ''}
      ${dub.length ? `
      <section class="nd-block nd-block--dub">
        <div class="nd-block-head"><b>Dudosas</b><small>la casa paga mucho más de lo esperado, o el partido simulado y su historial no coinciden: cambió algo (rol, minutos, lesión)</small></div>
        <div class="nd-minis">${dub.map(({ s, p, mk, o }) => miniRow({ s, p, o }, mk)).join('')}</div>
      </section>` : ''}`;
  }

  function gameCard(g) {
    const sim = state.sims[g.game_id];
    const st = gameState(g);
    const open = state.open.has(g.game_id);
    const busy = state.busy.has(g.game_id);
    const ok = sim && !sim.error && sim.sim;
    const np = state.market === 'props' ? ((state.props[g.game_id] || {}).picks || []).length
      : ok ? (sim.picks || []).filter((k) => k.market === state.market).length : 0;
    const pHome = ok ? sim.sim.p_home : 0.5;
    const fav = pHome >= 0.5 ? 'home' : 'away';
    const [ca, ch] = pairColors(g.away.tricode, g.home.tricode);
    const row = (t, side) => {
      const live = st.live || st.past;
      const val = live ? (t.score ?? '') : ok ? Math.round(sim.sim.teams[side].pts) : '–';
      return `
      <span class="nd-gc-row${ok && fav === side ? ' is-fav' : ''}">
        ${logoImg(t)}
        <b>${esc(t.tricode)}</b>
        <em class="${live ? 'is-real' : ''}">${val}</em>
      </span>`;
    };
    return `
      <div class="nd-gc-wrap${open ? ' is-open' : ''}" style="--away:${ca};--home:${ch}">
        <div class="nd-gc" data-gc="${esc(g.game_id)}" style="--ph:${ok ? pHome.toFixed(3) : 0.5}" role="button" tabindex="0" aria-expanded="${open}">
          <span class="nd-gc-time">
            <b>${hhmm(g.tip_off)}</b>
            ${st.t ? `<small class="${st.live ? 'is-live' : st.past ? 'is-past' : 'is-soon'}">${esc(st.t)}</small>` : ''}
          </span>
          <span class="nd-gc-teams">${row(g.away, 'away')}${row(g.home, 'home')}</span>
          <span class="nd-gc-bar" title="${ok ? `Gana ${esc(g.home.tricode)} en ${pct(pHome)} de las simulaciones` : ''}"><i style="height:${Math.round(pHome * 100)}%"></i></span>
          <span class="nd-gc-tags">
            ${busy ? '<span class="nd-tag is-busy"><span class="nd-spin"></span>simulando</span>' : np ? `<span class="nd-tag is-picks"><b>${np}</b> jugada${np > 1 ? 's' : ''}</span>` : sim ? '<span class="nd-tag">sin jugadas</span>' : ''}
            ${injOf(g.game_id) ? `<span class="nd-tag is-inj" title="El parte de lesionados cambió desde la simulación">${INJ_IC}parte nuevo</span>` : ''}
            ${ok ? `<span class="nd-gc-win">gana ${esc((fav === 'home' ? g.home : g.away).tricode)} <b>${pct(Math.max(pHome, 1 - pHome))}</b></span>` : ''}
          </span>
          ${busy ? '<span class="nd-gc-prog"><i></i></span>' : ''}
        </div>
        ${open ? gamePanel(g, sim) : ''}
      </div>`;
  }

  // El partido simulado: marcador, quién gana, ritmo, alargue, paliza y totales.
  function scoreBlock(sim) {
    const S = sim.sim;
    if (!S) return '';
    const h = S.teams.home, a = S.teams.away;
    const [ca, ch] = pairColors(sim.away.tricode, sim.home.tricode);
    const tot = (k, f = (x) => Math.round(x)) => `<div><dt>${k === 'fg' ? '% campo' : k === 'pts' ? 'Puntos' : k === 'reb' ? 'Rebotes' : 'Asist.'}</dt><dd><span>${f(a[k])}</span><span>${f(h[k])}</span></dd></div>`;
    return `
      <div class="nd-score" style="--away:${ca};--home:${ch}">
        <div class="nd-score-head">Partido simulado <small>${(sim.n_sim || 20000).toLocaleString('es-AR')} veces</small></div>
        <div class="nd-score-main">
          <span class="nd-score-tm">${logoImg(sim.away)}<b>${esc(sim.away.tricode)}</b></span>
          <span class="nd-score-pts"><b>${Math.round(a.pts)}</b><i>–</i><b>${Math.round(h.pts)}</b></span>
          <span class="nd-score-tm">${logoImg(sim.home)}<b>${esc(sim.home.tricode)}</b></span>
        </div>
        <div class="nd-score-win"><span style="width:${Math.round((1 - S.p_home) * 100)}%">${pct(1 - S.p_home)}</span><span>${pct(S.p_home)}</span></div>
        <dl class="nd-score-facts">
          <div><dt>Ritmo</dt><dd>${Math.round(S.pace)} <small>posesiones</small></dd></div>
          <div><dt>Alargue</dt><dd>${pct(S.p_ot)}</dd></div>
          <div><dt>Paliza (20+)</dt><dd>${pct(S.p_blowout)}</dd></div>
        </dl>
        <dl class="nd-score-tot">
          ${tot('pts')}${tot('reb')}${tot('ast')}${tot('fg', (x) => `${(x * 100).toFixed(1).replace('.', ',')}%`)}
        </dl>
        <p class="nd-score-note">Los rebotes salen de los tiros que erra cada equipo y las asistencias de los que mete. Si el partido se define por mucho, los titulares juegan menos.</p>
      </div>`;
  }

  function playerRow(p, sim) {
    const mk = state.market, ot_mk = mk === 'dd' ? 'td' : 'dd';
    const o = (p.odds || {})[mk];
    const pm = p['p_' + mk], po = p['p_' + ot_mk];
    const isPick = (sim.picks || []).some((k) => k.player_id === p.player_id && k.market === mk);
    return `
      <div class="nd-pl${isPick ? ' is-pick' : ''}${p.doubt ? ' is-doubt' : ''}" style="--tc:${tcolor(p.team)}">
        ${face(p)}
        <span class="nd-pl-who">
          <b>${esc(p.name)}${p.doubt ? `<i class="nd-doubt">${esc(p.status || 'duda')}</i>` : ''}</b>
          <small>${statLine(p)}<span>${Math.round(p.min)}′</span></small>
        </span>
        <span class="nd-pl-p">
          <span class="nd-pl-pr"><b>${pctF(pm)}</b><small>${mk.toUpperCase()}</small></span>
          ${pBar(Math.min(1, pm * (mk === 'td' ? 4 : 1)), mk === 'td' ? 'is-td' : '')}
          <span class="nd-pl-sub">${po >= 0.005 ? `${ot_mk.toUpperCase()} ${pctF(po)}` : ''}</span>
        </span>
        <span class="nd-pl-odd">
          ${o ? `<span class="${o.edge >= 0.08 && !o.dubious ? 'is-ok' : o.dubious ? 'is-dub' : ''}">${bookBadge(o.best_book, true)}<b>${odd(o.best_price)}</b></span>` : '<span class="is-na">—</span>'}
        </span>
      </div>`;
  }

  function gamePanel(g, sim) {
    if (!sim) return `<div class="nd-gp"><div class="nd-busy"><span class="nd-spin"></span>Simulando…</div></div>`;
    if (sim.error) return `<div class="nd-gp"><div class="nd-busy is-err">${esc(sim.error)}</div></div>`;
    const side = state.team[g.game_id] || 'all';
    const list = sim.players.filter((p) => side === 'all' || p.side === side);
    if (state.market === 'td') list.sort((a, b) => b.p_td - a.p_td);
    return `
      <div class="nd-gp">
        <div class="nd-gp-top">
          <button class="nd-sim" data-sim="${esc(g.game_id)}" ${state.busy.has(g.game_id) ? 'disabled' : ''}>
            <span class="nd-sim-ic">${I.play}</span>
            <span><b>Simular partido</b><small>20.000 partidos completos con planteles, lesionados y cuotas de ahora</small></span>
          </button>
          ${gameState(g).live ? `<button class="nd-golive" data-golive="${esc(g.game_id)}" title="Lo que lleva cada jugador y una apuesta en vivo armada por el modelo">
            <span class="nd-golive-ic"><i></i></span>
            <span><b>Armate un live</b><small>estadísticas y apuesta en vivo</small></span>
          </button>` : ''}
          ${gameState(g).live || gameState(g).past ? '' : `<button class="nd-godds${state.oddsGame.has(g.game_id) ? ' is-busy' : ''}" data-godds="${esc(g.game_id)}" ${state.oddsGame.has(g.game_id) ? 'disabled' : ''} title="Pide las cuotas sólo de este partido: 1 pedido de ${apiName((state.slate || {}).usage)}">
            <span class="nd-godds-ic">${state.oddsGame.has(g.game_id) ? '<span class="nd-spin"></span>' : I.clock}</span>
            <span><b>${state.oddsGame.has(g.game_id) ? 'Pidiendo…' : 'Pedir cuotas'}</b><small>sólo de este partido · 1 pedido</small></span>
          </button>`}
        </div>
        ${scoreBlock(sim)}
        <div class="nd-gp-meta">${I.clock}Simulado ${esc(ago(sim.computed_at))} · ${sim.players.length} jugadores con 16+ minutos${sim.odds_age_s != null ? ` · cuotas de hace ${ageTxt(sim.odds_age_s)}` : ''}</div>
        <div class="nd-seg" role="tablist"${state.market === 'props' ? ' hidden' : ''}>
          ${[['all', 'Los dos'], ['away', sim.away.tricode], ['home', sim.home.tricode]].map(([k, l]) => `<button data-team="${esc(g.game_id)}" data-side="${k}" class="${side === k ? 'is-on' : ''}">${k !== 'all' ? logoImg(k === 'home' ? sim.home : sim.away) : ''}${esc(l)}</button>`).join('')}
        </div>
        ${state.market === 'props' ? propsHl(g) + propsPanel(g, sim) : `
        <div class="nd-pl-head"><span>Promedio simulado</span><span>Prob.</span><span>Cuota</span></div>
        <div class="nd-pl-list">${list.map((p) => playerRow(p, sim)).join('') || '<div class="nd-empty">Sin jugadores con minutos.</div>'}</div>`}
        ${sim.out?.length ? `<p class="nd-out"><b>Bajas:</b> ${sim.out.map((o) => `${esc(o.name)} (${esc(o.team)})`).join(', ')}</p>` : ''}
      </div>`;
  }

  // La jornada NBA es la fecha de ESPN (un partido de las 00:00 de acá es de esta noche).
  const slateDay = () => (state.slate?.date ? `${state.slate.date}T15:00:00` : null);
  function gamesView() {
    const games = state.slate?.games || [];
    const d = slateDay();
    return `
      ${hero('Partidos', 'de la noche', 'Puntos esperados de cada equipo; la barra muestra quién gana más veces. Tocá un partido para ver el partido simulado y a cada jugador.')}
      ${marketSeg()}
      ${games.length ? `
        <section class="nd-day">
          <div class="nd-day-head"><b>${esc(d ? dayLabel(d) : 'Esta noche')}</b><small>${games.length} partido${games.length > 1 ? 's' : ''} · puntos esperados</small></div>
          <div class="nd-gc-list">${games.map(gameCard).join('')}</div>
        </section>` : '<div class="nd-empty">No hay partidos cargados.</div>'}`;
  }

  const KIND = { pick: 'Jugada', firme: 'Lo más firme', dudosa: 'Dudosa' };
  // Resultados de los Destacados de líneas de jugador (valor y lo más firme de cada partido)
  function propResultsView() {
    const R = state.propResults;
    if (!R) return `${hero('Resultados', '', '')}${marketSeg()}<div class="nd-busy"><span class="nd-spin"></span>Cargando…</div>`;
    if (R.error) return `${hero('Resultados', '', '')}${marketSeg()}<div class="nd-busy is-err">${esc(R.error)}</div>`;
    const bk = R.by_kind || {};
    const fk = ['pick', 'firme', 'jugada', 'retirada'].includes(state.resKind) ? state.resKind : 'all';
    const sm = fk === 'all' ? (R.summary || {}) : (bk[fk] || {});
    const roiTxt = (x) => (x != null ? `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1).replace('.', ',')}%` : '—');
    const wl = (x) => (x && x.settled ? `${x.won}-${x.lost}` : '0-0');
    const all = R.picks || [];
    // Destacados = valor + firme; Jugadas = todas las de la lista Jugadas (valor destacado o no)
    const inK = (r, k) => (k === 'retirada' ? !!r.dropped : r.dropped ? false
      : k === 'all' ? r.kind !== 'jugada' : k === 'jugada' ? r.kind !== 'firme' : r.kind === k);
    const isLive = (r) => (!r.result && lvOf(r) ? 1 : 0);
    const rows = all.filter((r) => inK(r, fk)).sort((a, b) => isLive(b) - isLive(a));
    const nLive = rows.filter(isLive).length;
    const n = (k) => all.filter((r) => inK(r, k)).length;
    const chip = (k, label, rec) => `<button class="nd-rf is-${k}${fk === k ? ' is-on' : ''}" data-rkind="${k}">${label}<b>${rec}</b><i>${n(k)}</i></button>`;
    const num = (x) => String(Math.round(x * 10) / 10).replace('.', ',');
    return `
      ${hero('Resultados', '', 'Los Destacados de cada partido, guardados con la cuota de antes del inicio y cerrados con el box score de ESPN.')}
      ${marketSeg()}
      <div class="nd-rec">
        <div class="nd-rec-wl"><b class="w">${sm.won || 0}</b><i>–</i><b class="l">${sm.lost || 0}</b></div>
        <dl>
          <div><dt>ROI</dt><dd class="${(sm.roi || 0) >= 0 ? 'is-pos' : 'is-neg'}">${roiTxt(sm.roi)}</dd></div>
          <div><dt>Unidades</dt><dd class="${(sm.pnl || 0) >= 0 ? 'is-pos' : 'is-neg'}">${sm.settled ? `${sm.pnl >= 0 ? '+' : '−'}${num(Math.abs(sm.pnl))}` : '—'}</dd></div>
          <div><dt>Esperadas</dt><dd>${sm.settled ? num(sm.expected) : '—'}</dd></div>
          <div><dt>Abiertas</dt><dd>${sm.open || 0}</dd></div>
        </dl>
      </div>
      <p class="nd-rec-note">Destacados: hasta 3 jugadas con valor y 2 "lo más firme" por partido. "Todas las jugadas" suma el resto de la lista Jugadas. Hasta que empieza el partido se actualizan con las cuotas; después quedan fijas. Una unidad por jugada.</p>
      <div class="nd-rfs">
        ${chip('all', 'Destacados', wl(R.summary))}
        ${chip('pick', 'Valor', wl(bk.pick))}
        ${chip('firme', 'Lo más firme', wl(bk.firme))}
        ${chip('jugada', 'Todas las jugadas', wl(bk.jugada))}
        ${n('retirada') ? chip('retirada', 'Retiradas', wl(bk.retirada)) : ''}
      </div>
      ${nLive ? `<div class="nd-lv-head"><u></u><b>En juego</b><small>${nLive} jugada${nLive > 1 ? 's' : ''} · se actualiza sola cada 25 segundos</small></div>` : ''}
      <div class="nd-res-list">
        ${rows.map((r) => `
          <div class="nd-res is-${r.result || 'open'}${isLive(r) ? ' is-tracking' : ''}">
            <span class="nd-res-face">${face({ name: r.player, photo: r.photo || '' })}<span class="nd-res-tag">${r.result === 'win' ? 'G' : r.result === 'loss' ? 'P' : r.result === 'void' ? 'N' : '·'}</span></span>
            <span class="nd-res-who">
              <b>${esc(r.player)} · ${esc(lineTxt(r))} ${esc(lbl(r.label))}</b>
              <small><i class="nd-kind is-${r.kind === 'firme' ? 'firme' : 'pick'}">${r.kind === 'firme' ? 'Lo más firme' : r.kind === 'jugada' ? 'Jugada' : 'Destacada'}</i>${r.dropped ? '<i class="nd-kind is-late" title="La app la recomendó y después la sacó de la lista porque cambiaron las cuotas. Se sigue igual.">retirada antes del partido</i>' : ''}${r.late ? '<i class="nd-kind is-late" title="Se agregó con el partido ya empezado, con la cuota de antes del inicio">agregada tarde</i>' : ''}${esc(r.team)} · ${esc(new Date(r.tip_off).toLocaleDateString('es-AR', { day: 'numeric', month: 'short' }))}${r.stat ? ` · ${esc(r.stat)}` : ''}</small>
              ${liveBar(r)}
            </span>
            <span class="nd-res-odd">${bookBadge(r.book, true)}<b>${odd(r.price)}</b><small>${pct(r.p_model)}</small></span>
          </div>`).join('') || '<div class="nd-empty">Todavía no hay destacados guardados. Se guardan solos cuando la app calcula las líneas de un partido que no empezó.</div>'}
      </div>`;
  }
  function resultsView() {
    if (state.market === 'props') return propResultsView();
    const R = state.results;
    if (!R) return `${hero('Resultados', '', '')}<div class="nd-busy"><span class="nd-spin"></span>Cargando…</div>`;
    if (R.error) return `${hero('Resultados', '', '')}<div class="nd-busy is-err">${esc(R.error)}</div>`;
    const sm = R.summary || {};
    const bk = R.by_kind || {};
    const roiTxt = (x) => (x != null ? `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1).replace('.', ',')}%` : '—');
    const wl = (x) => (x && x.settled ? `${x.won}-${x.lost}` : '0-0');
    const all = (R.picks || []).filter((r) => r.market === state.market);
    const fk = ['jugada', 'retirada'].includes(state.resKind) ? 'all' : state.resKind;
    const rows = all.filter((r) => fk === 'all' ? r.kind !== 'dudosa' : fk === 'played' ? r.played : r.kind === fk);
    const n = (k) => all.filter((r) => (k === 'all' ? r.kind !== 'dudosa' : k === 'played' ? r.played : r.kind === k)).length;
    const chip = (k, label, rec) => `<button class="nd-rf is-${k}${fk === k ? ' is-on' : ''}" data-rkind="${k}">${k === 'played' ? I.check : ''}${label}<b>${rec}</b><i>${n(k)}</i></button>`;
    return `
      ${hero('Resultados', '', 'Todo lo que la app te mostró, con la cuota de antes del partido y cerrado con el box score de ESPN. En dorado, las que marcaste "La juego".')}
      ${marketSeg()}
      <div class="nd-rec">
        <div class="nd-rec-wl"><b class="w">${sm.won || 0}</b><i>–</i><b class="l">${sm.lost || 0}</b></div>
        <dl>
          <div><dt>ROI</dt><dd class="${(sm.roi || 0) >= 0 ? 'is-pos' : 'is-neg'}">${roiTxt(sm.roi)}</dd></div>
          <div><dt>Esperadas</dt><dd>${sm.expected != null ? String(sm.expected).replace('.', ',') : '—'}</dd></div>
          <div><dt>Abiertas</dt><dd>${sm.open || 0}</dd></div>
        </dl>
      </div>
      <p class="nd-rec-note">Récord de las recomendaciones: jugadas con valor y Lo más firme. Las Dudosas van aparte.</p>
      <div class="nd-rfs">
        ${chip('all', 'Recomendadas', wl(sm))}
        ${chip('pick', 'Jugadas', wl(bk.pick))}
        ${chip('firme', 'Lo más firme', wl(bk.firme))}
        ${chip('dudosa', 'Dudosas', wl(bk.dudosa))}
        ${chip('played', 'Las jugaste', wl(bk.played))}
      </div>
      <div class="nd-res-list">
        ${rows.map((r) => `
          <div class="nd-res is-${r.result || 'open'}${r.played ? ' is-played' : ''}">
            <span class="nd-res-face">${face({ name: r.player, photo: r.photo || '' })}<span class="nd-res-tag">${r.result === 'win' ? 'G' : r.result === 'loss' ? 'P' : r.result === 'void' ? 'N' : '·'}</span></span>
            <span class="nd-res-who">
              <b>${esc(r.player)} · ${MK[r.market]}</b>
              <small>${r.played ? `<i class="nd-kind is-played">${I.check}La jugaste</i>` : ''}<i class="nd-kind is-${esc(r.kind)}">${KIND[r.kind] || 'Jugada'}</i>${r.late ? '<i class="nd-kind is-late" title="La app lo calculó con el partido ya empezado: no cuenta igual que una recomendación previa">simulada tarde</i>' : ''}${esc(r.team)} · ${esc(new Date(r.tip_off).toLocaleDateString('es-AR', { day: 'numeric', month: 'short' }))}${r.line ? ` · ${esc(r.line)}` : ''}</small>
            </span>
            <span class="nd-res-odd">${bookBadge(r.book, true)}<b>${odd(r.price)}</b><small>${pct(r.p_model)}</small></span>
          </div>`).join('') || '<div class="nd-empty">No hay jugadas de este tipo.</div>'}
      </div>`;
  }

  // Barra fija de arriba: "Volver" (si se llegó a un partido desde una tarjeta) y las tres vistas.
  const VIEW_NAME = { picks: 'Jugadas', games: 'Partidos', results: 'Resultados' };
  function topBar() {
    const b = state.view === 'games' ? state.back : null;
    const back = b ? `<button type="button" class="nd-back" data-act="back" aria-label="Volver a ${VIEW_NAME[b.view]}">
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>
        <span>Volver<small> a ${VIEW_NAME[b.view]}</small></span></button>` : '';
    return `<div class="nd-bar${b ? ' has-back' : ''}">${back}${nav('top')}</div>`;
  }
  function nav(where) {
    const n = allPicks().length;
    const items = [
      ['picks', 'Jugadas', I.ticket, n],
      ['games', 'Partidos', I.ball, (state.slate?.games || []).length],
      ['results', 'Resultados', I.cup, null],
    ];
    return `<nav class="nd-nav nd-nav--${where}" aria-label="NBA">${items.map(([k, l, ic, c]) => `
      <button class="nd-nav-i${state.view === k ? ' is-on' : ''}" data-view="${k}">${ic}<span>${l}</span>${c ? `<i>${c}</i>` : ''}</button>`).join('')}</nav>`;
  }

  // En el celu, reemplazar el HTML bajo el dedo corta el scroll. Mientras hay un
  // toque o la página se sigue moviendo, el redibujo espera; y si nada cambió, no
  // se toca la página.
  const touch = { on: false, until: 0, timer: 0 };
  function wireTouch() {
    const main = document.querySelector('.app-main');
    if (!main || main.dataset.ndTouch) return;
    main.dataset.ndTouch = '1';
    const hold = (ms) => { touch.until = Date.now() + ms; };
    main.addEventListener('touchstart', () => { touch.on = true; }, { passive: true });
    const end = () => { touch.on = false; hold(450); };
    main.addEventListener('touchend', end, { passive: true });
    main.addEventListener('touchcancel', end, { passive: true });
    main.addEventListener('scroll', () => hold(250), { passive: true });
  }
  function render(force = false) {
    if (!force && (touch.on || Date.now() < touch.until)) {
      clearTimeout(touch.timer);
      touch.timer = setTimeout(() => render(), 200);
      return;
    }
    clearTimeout(touch.timer);
    const sec = document.getElementById('section-nbadd');
    if (!sec) return;
    let html;
    if (!state.slate) {
      html = state.error
        ? `<div class="nd-state is-err"><b>No se pudo cargar la NBA</b><span>${esc(state.error)}</span><button class="nd-btn" data-act="reload">Reintentar</button></div>`
        : '<div class="nd-state"><span class="nd-spin"></span><b>Cargando la noche de NBA…</b></div>';
    } else {
      const view = state.view === 'games' ? gamesView() : state.view === 'results' ? resultsView() : picksView();
      html = `${topBar()}<div class="nd-view nd-view--${state.view}">${view}</div>`;
    }
    if (html !== sec._ndHtml) {
      // Mismo lugar después de redibujar: si cambia el alto de lo que está arriba
      // (llegó una simulación, se cerró un partido) lo que se estaba mirando no se corre.
      const anchor = state.slate && sec._ndView === state.view ? grabAnchor(sec) : null;
      sec._ndView = state.slate ? state.view : null;
      sec.innerHTML = html; sec._ndHtml = html;
      if (anchor) holdAnchor(sec, anchor);
      countUp(sec); fillBars(sec);
      // Animación de entrada sólo al cambiar de vista o de mercado, no en cada refresco.
      // Si hay otro render mientras dura, sigue desde donde iba (retraso negativo).
      const key = state.slate ? `${state.view}|${state.market}` : '';
      const now = Date.now();
      if (key !== sec._ndKey) { sec._ndKey = key; sec._ndEnterAt = key ? now : 0; }
      const dt = now - (sec._ndEnterAt || 0);
      sec.classList.toggle('nd-entering', dt < 1100);
      sec.style.setProperty('--nd-t', `${-dt}ms`);
      // Partido recién abierto: el panel se despliega una vez.
      const was = sec._ndOpen || new Set();
      sec.querySelectorAll('.nd-gc-wrap').forEach((w) => {
        const c = w.querySelector('.nd-gc'), id = c && c.dataset.gc;
        if (id && state.open.has(id) && !was.has(id)) w.classList.add('is-opening');
      });
      sec._ndOpen = new Set(state.open);
    }
    let host = document.getElementById('nd-host');
    if (!host) {
      host = Object.assign(document.createElement('div'), { id: 'nd-host' });
      host.addEventListener('click', onClick);
      document.body.appendChild(host);
    }
    const hn = state.slate ? nav('bottom') : '';
    if (hn !== host._ndHtml) { host.innerHTML = hn; host._ndHtml = hn; }
    renderTop();
  }

  const scroller = () => document.querySelector('.app-main');
  // Ancla de scroll: la tarjeta más cercana al borde de arriba de la pantalla, identificada
  // por su atributo (partido, jugada…) y su orden entre las que comparten ese valor.
  const ANCHOR_SEL = '[data-gc], [data-open], [data-tk], [data-prop]';
  const anchorKey = (el) => ['gc', 'open', 'tk', 'prop'].map((k) => (el.dataset[k] != null ? `${k}=${el.dataset[k]}` : '')).join('|');
  function grabAnchor(sec) {
    const main = scroller();
    if (!main || main.scrollTop <= 0) return null;
    const top = main.getBoundingClientRect().top;
    const seen = {};
    let best = null;
    sec.querySelectorAll(ANCHOR_SEL).forEach((el) => {
      const key = anchorKey(el);
      const n = seen[key] = (seen[key] || 0) + 1;
      const y = el.getBoundingClientRect().top;
      // la primera que asoma en pantalla; si no hay, la última que quedó arriba
      const d = y >= top ? y - top : (top - y) + 100000;
      if (!best || d < best.d) best = { key, n, y, d };
    });
    return best;
  }
  function holdAnchor(sec, a) {
    const main = scroller();
    if (!main) return;
    let n = 0;
    for (const el of sec.querySelectorAll(ANCHOR_SEL)) {
      if (anchorKey(el) !== a.key || ++n !== a.n) continue;
      const dy = el.getBoundingClientRect().top - a.y;
      if (Math.abs(dy) > 1) main.scrollTop += dy;
      return;
    }
  }
  // Lleva una tarjeta justo debajo de la barra fija de arriba.
  function scrollToCard(el) {
    const main = scroller();
    if (!main) return el.scrollIntoView({ block: 'start' });
    const bar = document.querySelector('#section-nbadd .nd-bar');
    const edge = Math.max(main.getBoundingClientRect().top, bar ? bar.getBoundingClientRect().bottom : 0) + 8;
    main.scrollTop += el.getBoundingClientRect().top - edge;
  }
  // Abrir un partido desde una tarjeta: se recuerda la vista y el scroll para poder volver.
  function openGame(id) {
    const main = scroller();
    hideTip();
    if (state.view !== 'games') state.back = { view: state.view, y: main ? main.scrollTop : 0, wy: window.scrollY };
    state.open.add(id);
    state.view = 'games';
    render(true);
    // En el mismo cuadro que el redibujado: sin pasar por una posición intermedia.
    const place = () => {
      const el = document.querySelector(`#section-nbadd [data-gc="${CSS.escape(id)}"]`);
      if (el) scrollToCard(el);
      else if (main) main.scrollTop = 0;
    };
    place();
    requestAnimationFrame(place);
  }
  function goBack() {
    const b = state.back;
    if (!b) return go('picks');
    state.back = null;
    state.view = b.view;
    if (b.view === 'results') loadResults();
    render(true);
    const place = () => { const main = scroller(); if (main) main.scrollTop = b.y; window.scrollTo(0, b.wy || 0); };
    place();
    requestAnimationFrame(place);
  }
  function go(view) {
    hideTip();
    state.back = null;
    state.view = view;
    if (view === 'results') loadResults();
    render(true);
    const main = document.querySelector('.app-main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function onClick(e) {
    const t = e.target;
    const shot = t.closest('[data-hlshot]');
    if (shot) {
      const id = shot.dataset.hlshot;
      if (state.shotBusy.has(id)) return;
      state.shotBusy.add(id); render(true);
      shotHl(id).catch((e) => toast(`No se pudo armar la foto: ${esc(e.message)}`))
        .finally(() => { state.shotBusy.delete(id); render(true); });
      return;
    }
    const more = t.closest('[data-parlaymore]');
    if (more) {
      const id = more.dataset.parlaymore;
      state.parlayN[id] = (state.parlayN[id] || PARLAY_MAX) + PARLAY_MAX;
      return render(true);
    }
    const bo = t.closest('[data-ndboost]');
    if (bo) {
      const id = bo.dataset.ndboost;
      if (state.hlBoost.has(id)) state.hlBoost.delete(id); else state.hlBoost.add(id);
      return render(true);
    }
    const hl = t.closest('[data-ndhl]');
    if (hl) {
      const id = hl.dataset.ndhl;
      if ((state.hlMode[id] || 'hl') !== hl.dataset.mode) { state.hlMode[id] = hl.dataset.mode; state.hlSpin = id; }
      return render(true);
    }
    // "Mercado" en la tarjeta de línea: el toque muestra u oculta el cartelito (en el celu no hay hover)
    const wy = t.closest('[data-why]');
    if (wy) {
      e.stopPropagation();
      if (tip.key === wy.dataset.why && tip.shown && tip.byTap) hideTip(); else { showTip(wy); tip.byTap = true; }
      return;
    }
    const cp = t.closest('[data-cap]');
    if (cp) { state.oddsCap = Number(cp.dataset.cap) || 0; try { localStorage.setItem('nd-odds-cap', state.oddsCap); } catch (_) {} return render(true); }
    const so = t.closest('[data-sort]');
    if (so) { state.sortBy = so.dataset.sort; try { localStorage.setItem('nd-sort', state.sortBy); } catch (_) {} return render(true); }
    const pm = t.closest('[data-pmk]');
    if (pm) { state.propMk[pm.dataset.pmk] = pm.dataset.m; return render(true); }
    const pr = t.closest('[data-prop]');
    if (pr) { const k = pr.dataset.prop; if (state.propOpen.has(k)) state.propOpen.delete(k); else state.propOpen.add(k); return render(true); }
    const rk = t.closest('[data-rkind]');
    if (rk) { state.resKind = rk.dataset.rkind; return render(true); }
    const pl = t.closest('[data-play]');
    if (pl) { e.stopPropagation(); return togglePlayed(pl.dataset.play); }
    const fx = t.closest('[data-flipx]');
    if (fx) { e.stopPropagation(); return closeFlip(fx.dataset.flipx); }
    if (t.closest('[data-fxin]')) return;
    const tk = t.closest('[data-tk]');
    if (tk) return openFlip(tk.dataset.tk, tk);
    const m = t.closest('[data-mk]');
    if (m) {
      state.market = m.dataset.mk;
      try { localStorage.setItem('nd-market', state.market); } catch (_) { /* modo privado */ }
      return render(true);
    }
    const v = t.closest('[data-view]');
    if (v) return go(v.dataset.view);
    const act = t.closest('[data-act]');
    if (act) {
      if (act.dataset.act === 'back') return goBack();
      if (act.dataset.act === 'otop') { state.oddsOpen = !state.oddsOpen; state.injOpen = false; return render(true); }
      if (act.dataset.act === 'inj') { state.injOpen = !state.injOpen; state.oddsOpen = false; if (state.injOpen) loadInj(); return render(true); }
      if (act.dataset.act === 'inj-sim') { const ids = injGames().map((g) => g.game_id); if (ids.length) runQueue(ids, true); return render(true); }
      if (act.dataset.act === 'inj-odds') { state.injOpen = false; return askOdds(); }
      if (act.dataset.act === 'odds') { state.oddsOpen = false; return askOdds(); }
      if (act.dataset.act === 'reload') { state.error = null; render(); return loadSlate(); }
    }
    const gl = t.closest('[data-golive]');
    if (gl) return openLive(gl.dataset.golive);
    const go1 = t.closest('[data-godds]');
    if (go1) return askGameOdds(go1.dataset.godds);
    const sim = t.closest('[data-sim]');
    if (sim) return runQueue([sim.dataset.sim], true);
    const tm = t.closest('[data-team]');
    if (tm) { state.team[tm.dataset.team] = tm.dataset.side; return render(true); }
    const op = t.closest('[data-open]');
    if (op) return openGame(op.dataset.open);
    const gc = t.closest('[data-gc]');
    if (gc) {
      const id = gc.dataset.gc;
      if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
      render(true);
    }
  }

  // Cartelito flotante de "Mercado": aparece si el mouse se queda 1 segundo encima.
  const TIP_DELAY = 1000;
  const tip = { timer: 0, key: null, shown: false, byTap: false, x: 0, y: 0 };
  function tipBox() {
    let el = document.getElementById('nd-tip');
    if (!el) {
      el = Object.assign(document.createElement('div'), { id: 'nd-tip' });
      el.setAttribute('role', 'tooltip');
      document.body.appendChild(el);
    }
    return el;
  }
  function showTip(btn) {
    clearTimeout(tip.timer);
    const el = tipBox();
    el.innerHTML = `<b>${esc(btn.dataset.tipH)}</b><span>${esc(btn.dataset.tip)}</span>`;
    el.classList.add('is-on');
    // arriba del casillero, centrado; si no entra, abajo. Siempre dentro de la pantalla.
    const r = btn.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight, vw = document.documentElement.clientWidth;
    const left = Math.max(8, Math.min(vw - w - 8, r.left + r.width / 2 - w / 2));
    const above = r.top - h - 10 >= 8;
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(above ? r.top - h - 10 : r.bottom + 10)}px`;
    el.style.setProperty('--ax', `${Math.round(Math.max(14, Math.min(w - 14, r.left + r.width / 2 - left)))}px`);
    el.classList.toggle('is-below', !above);
    tip.key = btn.dataset.why; tip.shown = true;
  }
  function hideTip() {
    clearTimeout(tip.timer);
    tip.key = null; tip.shown = false; tip.byTap = false;
    const el = document.getElementById('nd-tip');
    if (el) el.classList.remove('is-on');
  }
  function wireTip(sec) {
    sec.addEventListener('mousemove', (e) => { tip.x = e.clientX; tip.y = e.clientY; }, { passive: true });
    sec.addEventListener('mouseover', (e) => {
      tip.x = e.clientX; tip.y = e.clientY;
      const btn = e.target.closest && e.target.closest('[data-why]');
      if (!btn || (e.relatedTarget && btn.contains(e.relatedTarget))) return;
      if (tip.shown && tip.key === btn.dataset.why) return;
      const key = btn.dataset.why;
      clearTimeout(tip.timer);
      tip.key = key;
      tip.timer = setTimeout(() => {
        // la página pudo redibujarse: se busca el casillero que sigue bajo el mouse
        const at = document.elementFromPoint(tip.x, tip.y);
        const now = at && at.closest && at.closest('[data-why]');
        if (now && now.dataset.why !== key) return;
        if (now) showTip(now);
      }, TIP_DELAY);
    });
    sec.addEventListener('mouseout', (e) => {
      const btn = e.target.closest && e.target.closest('[data-why]');
      if (!btn || (e.relatedTarget && btn.contains(e.relatedTarget))) return;
      if (tip.byTap) return;        // abierto con un toque: se cierra tocando afuera o al deslizar
      hideTip();
    });
    sec.addEventListener('focusout', (e) => { if (e.target.closest && e.target.closest('[data-why]')) hideTip(); });
    const main = document.querySelector('.app-main');
    if (main) main.addEventListener('scroll', hideTip, { passive: true });
    window.addEventListener('scroll', hideTip, { passive: true });
    document.addEventListener('click', (e) => { if (tip.shown && !(e.target.closest && e.target.closest('[data-why]'))) hideTip(); });
  }

  function wire() {
    const sec = document.getElementById('section-nbadd');
    if (!sec || sec.dataset.wired) return;
    sec.dataset.wired = '1';
    sec.addEventListener('click', onClick);
    wireTip(sec);
    sec.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-gc], [data-tk], [data-jc]')) { e.preventDefault(); onClick(e); }
    });
  }

  document.addEventListener('click', (e) => {
    // composedPath: el botón se redibuja al tocarlo, así que se mira el camino original del click
    if ((state.oddsOpen || state.injOpen) && !e.composedPath().some((n) => n.id === 'nd-otop')) { state.oddsOpen = false; state.injOpen = false; render(true); }
  });

  // ══ Armate un live ═══════════════════════════════════════════════════════
  // La simulación del partido, en vivo (lo que falta + lo que ya pasó), en una hoja
  // aparte con el mismo bloque de Destacados / Parlay que el partido simulado.
  const live = { gid: null, data: null, busy: false, error: null, tab: 'hl', timer: null };
  const LIVE_EVERY_S = 45;       // con la hoja abierta: marcador, cuotas y simulación nuevos cada 45 s
  const num1 = (x) => (Number.isInteger(x) ? String(x) : Number(x).toFixed(1).replace('.', ','));
  const LV_TAG = { dd: ['DD', '#1d428a'], td: ['TD', '#1d428a'] };
  function liveHost() {
    let el = document.getElementById('nd-live');
    if (!el) {
      el = document.createElement('div');
      el.id = 'nd-live';
      el.hidden = true;
      document.body.appendChild(el);
      el.addEventListener('click', (e) => {
        if (e.target === el || e.target.closest('[data-lvclose]')) return closeLive();
        if (e.target.closest('[data-lvreload]')) return loadLive();
        const tb = e.target.closest('[data-lvtab]');
        if (tb) { live.tab = tb.dataset.lvtab; drawLive(); }
      });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !el.hidden) closeLive(); });
    }
    return el;
  }
  function closeLive() {
    const el = liveHost();
    el.hidden = true; el.innerHTML = '';
    document.documentElement.classList.remove('nd-live-open');
    live.gid = null;
    clearInterval(live.timer); live.timer = null;
  }
  function openLive(gid) {
    live.gid = gid; live.data = null; live.error = null; live.tab = 'hl'; live.anim = true;
    document.documentElement.classList.add('nd-live-open');
    liveHost().hidden = false;
    loadLive();
    clearInterval(live.timer);
    live.timer = setInterval(() => {            // sola, mientras la hoja está abierta y a la vista
      const d = live.data;
      if (!live.gid || document.hidden || (d && d.state !== 'in')) return;
      loadLive();
    }, LIVE_EVERY_S * 1000);
  }
  async function loadLive() {
    if (live.busy || !live.gid) return;
    const gid = live.gid;
    live.busy = true; drawLive();
    try { live.data = await getJSON(`${API}/api/nba/dd/live/${encodeURIComponent(gid)}?t=${Date.now()}`, { cache: 'no-store' }); live.error = null; }
    catch (e) { live.error = e.message || 'No se pudo simular el partido en vivo.'; }
    live.busy = false;
    if (live.gid === gid) drawLive();
  }
  // Un renglón, igual que en Destacados / Parlay, más lo que lleva y lo que proyecta.
  function liveRow(c, i, mode) {
    const p = { name: c.player, team: c.team, photo: c.photo };
    const tag = c.tag ? [c.tag, '#0b1d40'] : PTAG[c.mk] || LV_TAG[c.mk];
    const lineS = c.line_txt || (c.side === 'yes' ? 'Sí' : `${c.side === 'over' ? 'Más de' : 'Menos de'} ${num1(c.line)}`);
    // mercado del partido: el escudo del equipo (o los dos, en la línea de puntos) en vez de la foto
    const gpic = c.game ? `<span class="nd-hl-pic nd-lv-gpic">${(c.team ? [c.team] : c.teams || []).map((t) => (teamLogo(t) ? `<img src="${teamLogo(t)}" alt="">` : '')).join('')}</span>` : '';
    const chip = mode === 'hl' ? `<i class="nd-hl-chip is-${c.kind}">${c.kind === 'pick' ? 'valor' : 'firme'}</i>`
      : mode === 'dub' ? '<i class="nd-hl-chip is-sharp">dudosa</i>' : '';
    let prog = '';
    if (c.now != null && c.line != null && c.proj != null) {
      const max = Math.max(c.line, c.proj || 0, c.now, 1) * 1.15;
      const w = (x) => `${Math.max(0, Math.min(100, (x / max) * 100)).toFixed(1)}%`;
      prog = `<span class="nd-lv-prog" title="Lleva ${num1(c.now)}, la simulación proyecta ${num1(c.proj)} y la línea es ${num1(c.line)}">
          <span class="nd-lv-bar"><i class="nd-lv-bar-proj" style="width:${w(c.proj)}"></i><i class="nd-lv-bar-now" style="width:${w(c.now)}"></i><i class="nd-lv-bar-line" style="left:${w(c.line)}"></i></span>
          <small>lleva <b>${num1(c.now)}</b> · proyecta <b>${num1(c.proj)}</b></small>
        </span>`;
    }
    const ok = c.ratio >= 0;
    const mid = mode === 'parlay'
      ? `<span class="nd-hl-boost${ok ? ' is-ok' : ''}" title="${ok ? 'La casa paga la cuota justa o más' : 'La casa paga un poco menos que la cuota justa'}"><b>${edgeTxt(c.ratio)}</b><small>${ok ? 'sobre la justa' : 'bajo la justa'}</small></span>`
      : `<span class="nd-hl-stat is-edge${c.edge > 0.005 ? '' : ' is-neg'}"><b>${edgeTxt(c.edge)}</b><small>ventaja</small></span>`;
    return `
      <div class="nd-hl-row is-${mode === 'parlay' ? 'leg' : c.kind || 'dub'}">
        <span class="nd-hl-n">${i + 1}</span>
        ${gpic || `<span class="nd-hl-pic" style="--tc:${tcolor(c.team)}">${face(p)}${teamLogo(c.team) ? `<img class="nd-hl-tm" src="${teamLogo(c.team)}" alt="">` : ''}</span>`}
        <span class="nd-hl-who">
          <b>${esc(c.player)}${chip}</b>
          <em>${esc(lineS)} <i${tag ? ` style="--sc:${tag[1]}"` : ''}>${esc(tag ? tag[0] : lbl(c.label))}</i></em>
          ${prog}
        </span>
        <span class="nd-hl-end">
          <span class="nd-hl-stat is-p"><b>${pct(c.p)}</b><small>justa ${odd(c.fair)}</small></span>
          ${mid}
          <span class="nd-hl-odd${c.price >= c.fair ? ' is-ok' : ''}">${bookBadge(c.book, true)}<b>${odd(c.price)}</b></span>
        </span>
      </div>`;
  }
  function drawLive() {
    const el = liveHost();
    const d = live.data;
    const g = ((state.slate || {}).games || []).find((x) => String(x.game_id) === String(live.gid)) || {};
    const home = (d && d.home) || g.home || {}, away = (d && d.away) || g.away || {};
    const sc = (d && d.score) || {};
    const [ca, ch] = pairColors(away.tricode, home.tricode);
    const S = d && d.sim;
    // Con qué se simuló: el ritmo del partido, la línea en vivo y el tiro de cada equipo
    const L = (d && d.live) || {};
    const M = L.market || {};
    const sgn = (x) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${num1(Math.abs(x))}`;
    const facts = [];
    if (L.pace_now != null) facts.push(['Ritmo de hoy', num1(L.pace_now), `previo ${num1(L.pace_pre)} · se usa ${num1(L.pace_used)}`]);
    if (S && M.total != null) facts.push(['Línea de puntos', num1(M.total), `simulación ${num1(Math.round((S.home_pts + S.away_pts) * 10) / 10)}`]);
    if (S && M.home_margin != null) facts.push([`Hándicap ${home.tricode || ''}`, sgn(-M.home_margin), `simulación ${sgn(-S.margin)}`]);
    if (L.teams && L.teams.away && L.teams.home) facts.push(['Tiro de campo', `${pct(L.teams.away.fg)} · ${pct(L.teams.home.fg)}`, `${away.tricode || ''} · ${home.tricode || ''}`]);
    const head = `
      <header class="nd-lv-head" style="--away:${ca};--home:${ch}">
        <div class="nd-lv-bar0">
          <span class="nd-lv-eyebrow"><span class="nd-lv-dot"></span>Armate un live</span>
          <span class="nd-lv-clock${d && d.halftime ? ' is-ht' : ''}">${d ? esc(d.halftime ? 'Entretiempo' : d.detail || '') : '…'}</span>
          <button class="nd-lv-x" data-lvclose aria-label="Cerrar">×</button>
        </div>
        <div class="nd-lv-score">
          <span class="nd-lv-team">${away.tricode ? logoImg(away) : ''}<i>${esc(away.tricode || '')}</i></span>
          <span class="nd-lv-pts"><b>${sc.away ?? '–'}</b><i>–</i><b>${sc.home ?? '–'}</b></span>
          <span class="nd-lv-team is-home"><i>${esc(home.tricode || '')}</i>${home.tricode ? logoImg(home) : ''}</span>
        </div>
        ${S ? `<div class="nd-lv-sim">
          <div class="nd-lv-sim-top"><span>Final simulado</span><b>${Math.round(S.away_pts)} – ${Math.round(S.home_pts)}</b><span>gana ${esc(S.p_home >= 0.5 ? home.tricode : away.tricode)} ${pct(Math.max(S.p_home, 1 - S.p_home))}</span></div>
          <div class="nd-lv-win"><i style="width:${Math.round((1 - S.p_home) * 100)}%"></i></div>
          ${facts.length ? `<dl class="nd-lv-facts" title="Datos en vivo con los que se armó la simulación">${facts.map(([k, v, s]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd><small>${esc(s)}</small></div>`).join('')}</dl>` : ''}
        </div>` : ''}
      </header>`;
    let body;
    if (!d && live.busy) body = '<div class="nd-lv-wait"><span class="nd-spin"></span><span>Leyendo el partido (ritmo, tiro, línea en vivo) y simulando 20.000 veces lo que falta…</span></div>';
    else if (live.error) body = `<div class="nd-lv-wait is-err">${esc(live.error)}</div>`;
    else {
      const hl = d.highlights || [], par = d.parlay || [], dub = d.dubious || [], pls = d.players || [];
      const tab = live.tab;
      const TABS = [['hl', 'Destacados', hl.length], ['parlay', 'Parlay', par.length], ['stats', 'Jugadores', pls.length]];
      const SUB = {
        hl: 'jugadas con valor y lo más firme de la simulación en vivo',
        parlay: 'la línea más probable de cada jugador que la casa paga cerca o por encima de la cuota justa',
        stats: 'lo que lleva cada jugador y, abajo, el final que proyecta la simulación',
      };
      let rows;
      if (tab === 'parlay') {
        const pr = par.reduce((a, c) => a * c.price, 1), pp = par.reduce((a, c) => a * c.p, 1);
        rows = par.map((c, i) => liveRow(c, i, 'parlay')).join('') || '<p class="nd-hl-none">Ahora ninguna línea con buenas chances se paga cerca de la justa.</p>';
        if (par.length >= 2) {
          rows += `<div class="nd-lv-total"><div><small>las ${par.length} juntas</small><b>${odd(pr)}</b></div><div><small>probabilidad</small><b>${pctF(pp)}</b></div><div><small>cuota justa</small><b>${odd(1 / pp)}</b></div></div>
            <p class="nd-lv-fine">Son del mismo partido: la probabilidad conjunta es aproximada. Elegí las que quieras combinar.</p>`;
        }
      } else if (tab === 'stats') {
        rows = `<div class="nd-lv-tbl"><table>
            <thead><tr><th>Jugador</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>3P</th><th>DD</th></tr></thead>
            <tbody>${pls.map((p) => `<tr><td><b>${esc(p.name)}</b><small>${esc(p.team)}${p.pf >= 4 ? ` · ${p.pf} faltas` : ''}</small></td>
              <td><b>${num1(p.min)}</b><small>+${num1(p.min_left)}</small></td>
              ${['pts', 'reb', 'ast', 'threes'].map((s) => `<td><b>${num1(p.now[s])}</b><small>${p.proj[s] == null ? '–' : num1(p.proj[s])}</small></td>`).join('')}
              <td class="${p.p_dd >= 0.5 ? 'is-hot' : ''}"><b>${pct(p.p_dd)}</b></td></tr>`).join('')}</tbody>
          </table></div>`;
      } else {
        rows = hl.map((c, i) => liveRow(c, i, 'hl')).join('') || '<p class="nd-hl-none">Sin jugadas con valor ahora mismo. Mirá Parlay o volvé a simular en unos minutos.</p>';
        if (dub.length) rows += `<details class="nd-lv-dub"><summary>Dudosas (${dub.length}) · la simulación se aleja mucho del mercado</summary>${dub.map((c, i) => liveRow(c, i, 'dub')).join('')}</details>`;
      }
      body = `
        ${(d.errors || []).map((n) => `<p class="nd-lv-note">${esc(n)}</p>`).join('')}
        ${S ? `<section class="nd-hl is-${tab === 'parlay' ? 'parlay' : 'hl'}">
          <header class="nd-hl-top">
            <div class="nd-hl-tabs" role="tablist" aria-label="Simulación en vivo">
              ${TABS.map(([k, l, n]) => `<button role="tab" data-lvtab="${k}" aria-selected="${tab === k}" class="is-${k === 'stats' ? 'hl' : k}${tab === k ? ' is-on' : ''}">${k === 'stats' ? '' : HL_IC[k]}<span>${l}</span>${n ? `<i>${n}</i>` : ''}</button>`).join('')}
            </div>
            <p class="nd-hl-sub">${SUB[tab]}</p>
          </header>
          <div class="nd-hl-rows">${rows}</div>
        </section>` : ''}`;
    }
    const when = d ? new Date(d.at * 1000).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' }) : '';
    const bks = d ? (d.odds_books || []).map((b) => esc(book(b).name || b)).join(', ') : '';
    const anim = live.anim; live.anim = false;       // la entrada se anima sólo al abrir, no en cada redibujo
    const oldBody = el.querySelector('.nd-lv-body');
    const keepY = oldBody ? oldBody.scrollTop : 0;    // al actualizarse sola no pierde dónde estabas leyendo
    el.innerHTML = `<div class="nd-lv${anim ? ' is-in' : ''}" role="dialog" aria-modal="true" aria-label="Armate un live">
        ${head}
        <div class="nd-lv-body">${body}</div>
        <footer class="nd-lv-ft">
          <small>${d ? `Simulado ${when}${d.left_min ? ` · quedan ${num1(d.left_min)} min` : ''}${bks ? ` · cuotas en vivo de ${bks}` : ''}${d.state === 'in' ? ` · se actualiza sola cada ${LIVE_EVERY_S} s` : ''}` : ''}</small>
          <button class="nd-lv-reload" data-lvreload ${live.busy ? 'disabled' : ''}>${live.busy ? '<span class="nd-spin"></span>' : ''}Simular de nuevo</button>
        </footer>
      </div>`;
    const nb = el.querySelector('.nd-lv-body');
    if (nb && keepY) nb.scrollTop = keepY;
  }

  window.loadNbaDD = function loadNbaDD() {
    wire();
    wireTouch();
    if (!state.loaded) { state.loaded = true; render(); loadSlate(); return; }
    render();
  };
})();
