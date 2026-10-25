/* ════════════════════════════════════════════════════════════════════════════
   FÚTBOL — modelo de remates y remates al arco (paquete futbol/).

   Lee GET /api/futbol/jornada (data/futbol/jornada.json) y lo muestra:
     · riel con los partidos de la jornada, agrupados por liga;
     · Picks: las líneas jugables cuya mejor cuota paga la CUOTA MÍNIMA;
     · por partido: remates y al arco esperados de cada equipo (rango 10–90 %)
       y los jugadores como cromos con su probabilidad 1+/2+/3+ y 1+/2+.
   Acá no se calcula ninguna probabilidad: sólo se traduce lo que da el modelo.
   ════════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const apiBase = () => window.FB_API_BASE || (typeof API !== 'undefined' ? API : 'http://localhost:8002');

  // Nombres y logos oficiales (logos de ESPN, versión para fondo oscuro).
  const LEAGUES = [
    { id: 'arg', name: 'Liga Profesional de Fútbol', short: 'LPF', logo: 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/1.png' },
    { id: 'bra', name: 'Brasileirão Série A', short: 'Série A', logo: 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/85.png' },
    { id: 'eng', name: 'Premier League', short: 'PL', logo: 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/23.png' },
  ];
  // Lockup de la LPF: escudo | LIGA / PROFESIONAL / DE FÚTBOL (como el oficial).
  const lpfLockup = (cls = '') => `<span class="fb-lpf-lockup ${cls}"><img src="https://a.espncdn.com/i/leaguelogos/soccer/500-dark/1.png" alt="LPF"><i></i><b>Liga<br>Profesional<br>de Fútbol</b></span>`;
  const leagueOf = (id) => LEAGUES.find(L => L.id === id) || { name: id, short: id, logo: '' };
  const BOOKS = [
    { id: 'bet365.bet.ar', name: 'bet365' },
    { id: 'betano', name: 'Betano' },
    { id: 'betsson', name: 'Betsson' },
  ];
  // Las líneas del cromo: remates 1+/2+/3+ y al arco 1+/2+.
  const CELLS = [
    { stat: 'shots', line: 0.5 }, { stat: 'shots', line: 1.5 }, { stat: 'shots', line: 2.5 },
    { stat: 'sot', line: 0.5 }, { stat: 'sot', line: 1.5 },
  ];
  const POS = { F: 'Delantero', M: 'Volante', D: 'Defensor', G: 'Arquero' };
  // Rol según el puesto real (ESPN): no todos los delanteros juegan igual.
  const ROLE = { ST: 'Centrodelantero', WG: 'Extremo', AM: 'Enganche', CM: 'Volante', FB: 'Lateral', CB: 'Central', GK: 'Arquero' };
  const roleName = (x) => ROLE[x.role] || POS[x.pos] || x.pos || '';

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* modo privado */ } },
  };

  const state = {
    data: null, view: 'mine', league: 'all', team: 'all',
    fav: new Set(store.get('fb-fav', [])),   // partidos marcados con estrella
    xiSides: {},                             // partido → equipo que muestra la alineación
    fxOpen: new Set(),                       // partidos desplegados en las listas
    sim: { queue: [], running: null, t0: 0 }, // simulaciones en segundo plano
    sel: {},            // player_id → "stat|line" elegido en el cromo
    loading: false, error: null,
    mode: store.get('fb-mode', 'simple'),     // 'simple' | 'full'
    subOn: store.get('fb-sub', true),        // contar con Suplente+ / de Oro / Suplente
    slip: new Set(store.get('fb-slip', [])),  // claves de jugadas marcadas "La juego"
    slipOpen: false,
    results: null,      // /api/futbol/results: récord y picks liquidados
    open: new Set(),    // jugadores desplegados en "Quién remata"
    wsAll: false,       // mostrar todos los jugadores del partido
    back: 'mine',       // a dónde vuelve la flecha de un partido
  };

  // ── Formato ───────────────────────────────────────────────────────────────
  const plus = (line) => `${Math.round(line + 0.5)}+`;
  const statName = (s, n = 1) => (s === 'shots' ? (n === 1 ? 'remate' : 'remates') : 'al arco');
  const lineLabel = (stat, line) => {
    const n = Math.round(line + 0.5);
    return stat === 'shots' ? `${n}+ ${n === 1 ? 'remate' : 'remates'}` : `${n}+ al arco`;
  };
  const pct = (p) => `${Math.round(p * 100)}%`;
  const odd = (o) => (o == null ? '—' : Number(o).toFixed(2));
  const num1 = (v) => Number(v).toFixed(1).replace('.', ',');
  const when = (iso) => new Date(iso.length === 17 ? iso.replace('Z', ':00Z') : iso);
  const hhmm = (iso) => when(iso).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false });
  const dayKey = (iso) => when(iso).toLocaleDateString('en-CA');
  function dayLabel(iso) {
    const d = when(iso), today = new Date();
    const k = dayKey(iso), tk = today.toLocaleDateString('en-CA');
    const tm = new Date(today); tm.setDate(today.getDate() + 1);
    if (k === tk) return 'Hoy';
    if (k === tm.toLocaleDateString('en-CA')) return 'Mañana';
    const s = d.toLocaleDateString('es-AR', { weekday: 'short', day: 'numeric' });
    return s.charAt(0).toUpperCase() + s.slice(1).replace('.', '');
  }
  const logo = (id) => `https://a.espncdn.com/i/teamlogos/soccer/500/${encodeURIComponent(id)}.png`;
  const bookName = (id) => (BOOKS.find(b => b.id === id) || { name: id }).name;
  // Logo de cada casa (recortes oficiales en assets/books; Betsson, en texto hasta tener su logo).
  const BOOK_LOGO = { 'bet365.bet.ar': 'assets/books/bet365.png', betano: 'assets/books/betano.png' };
  const bookBadge = (id, cls = '') => BOOK_LOGO[id]
    ? `<img class="fb-bk ${cls}" src="${BOOK_LOGO[id]}" alt="${esc(bookName(id))}" title="${esc(bookName(id))}">`
    : `<span class="fb-bk fb-bk--txt fb-bk--${esc(String(id).replace(/[^a-z0-9]/gi, ''))} ${cls}" title="${esc(bookName(id))}">${esc(bookName(id))}</span>`;
  const initials = (n) => String(n).split(/\s+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  function surname(full) {
    const w = String(full).trim().split(/\s+/);
    return w.length > 1 ? w.slice(1).join(' ') : w[0];
  }
  function ago(iso) {
    const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 1) return 'recién';
    if (m < 60) return `hace ${m} min`;
    const h = Math.round(m / 60);
    return h < 48 ? `hace ${h} h` : `hace ${Math.round(h / 24)} días`;
  }

  const teamOf = (m, tid) => (m.teams.home.team_id === tid ? m.teams.home : m.teams.away);
  const lineOf = (p, stat, line) => p.lines.find(l => l.stat === stat && Math.abs(l.line - line) < 1e-6);
  const pays = (l) => l && l.best_price != null && l.best_price >= l.min_odds;

  // ── Íconos ────────────────────────────────────────────────────────────────
  const I = {
    ball: `<svg class="fb-mark" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" fill="#eef3f7"/><path d="M24 15.5l7.4 5.4-2.8 8.7h-9.2l-2.8-8.7z" fill="#0b1620"/><path d="M24 15.5V7.2M31.4 20.9l7.8-2.6M28.6 29.6l4.8 6.7M19.4 29.6l-4.8 6.7M16.6 20.9l-7.8-2.6" stroke="#0b1620" stroke-width="2" stroke-linecap="round"/><path d="M19 6.4l5 .8 5-.8M40.8 15.6l1 5-1.8 4.6M37.5 37.6l-4.4 2.6-5-.4M14.9 40.2l-4.4-2.6-1.8-4.6M6.2 25.2l-1-4.6 1.8-5" stroke="#0b1620" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" fill="none"/><circle cx="24" cy="24" r="19" fill="none" stroke="#5cc8ff" stroke-width="2"/></svg>`,
    refresh: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 10a6 6 0 11-1.8-4.3M16 3.5v3.2h-3.2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    coin: `<svg viewBox="0 0 20 20" aria-hidden="true"><ellipse cx="10" cy="6" rx="6" ry="2.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M4 6v4c0 1.4 2.7 2.6 6 2.6s6-1.2 6-2.6V6M4 10v4c0 1.4 2.7 2.6 6 2.6s6-1.2 6-2.6v-4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`,
    check: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 10.5l3.2 3.2L15 6.8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    clock: `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10 6v4.3l2.8 1.7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    starFill: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.6l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 7.9l5-.7z" fill="currentColor" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>`,
    play: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6.5 4.5v11l9-5.5z" fill="currentColor"/></svg>`,
    swap: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 7h11l-3-3M16 13H5l3 3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    tick: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 6.5h13v3a2 2 0 000 4v3h-13v-3a2 2 0 000-4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M12 6.5v10" stroke="currentColor" stroke-width="1.4" stroke-dasharray="1.6 1.8"/></svg>`,
    pitch: `<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="4" width="15" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10 4v12" stroke="currentColor" stroke-width="1.4"/><circle cx="10" cy="10" r="2.2" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>`,
    trophy: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5h8v4a4 4 0 01-8 0zM6 5H3.5v1.2A2.8 2.8 0 006.3 9M14 5h2.5v1.2A2.8 2.8 0 0113.7 9M10 11.5v3M7 16.5h6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    back: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12 4.5L6.5 10l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    star: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.6l2.2 4.6 5 .7-3.6 3.5.9 5-4.5-2.4-4.5 2.4.9-5L2.8 7.9l5-.7z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
  };

  // ── Datos ─────────────────────────────────────────────────────────────────
  async function fetchJornada(qs = '') {
    const r = await fetch(`${apiBase()}/api/futbol/jornada${qs}`);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* nada */ }
      throw new Error(msg);
    }
    return r.json();
  }

  async function loadResults() {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/results?days=10`);
      if (!r.ok) return;
      const prev = state.results;
      state.results = await r.json();
      // Aviso cuando se liquida algo mientras la pestaña está abierta.
      if (prev && state.results.settled_now) {
        const done = state.results.picks.filter(x => x.status !== 'open' && x.settled_at && Date.now() - new Date(x.settled_at).getTime() < 5 * 60000);
        done.slice(0, 3).forEach(x => toast(`${x.status === 'won' ? I.check : ''}<span><b>${x.status === 'won' ? 'Ganada' : x.status === 'lost' ? 'Perdida' : 'Nula'}</b>${esc(x.player)} · ${esc(lineLabel(x.stat, x.line))}</span>`));
      }
      if (!state.loading) render();
    } catch (_) { /* sin red: queda lo anterior */ }
  }

  async function load(qs = '') {
    state.loading = true; state.error = null;
    render();
    try {
      state.data = await fetchJornada(qs);
      if (!isSection(state.view) && !matchById(state.view)) state.view = 'mine';
      const keys = new Set((state.data.picks || []).map(pickKey));
      state.slip = new Set([...state.slip].filter(k => keys.has(k)));
      store.set('fb-slip', [...state.slip]); savePrefs();
    } catch (e) {
      state.error = e.message || String(e);
    } finally {
      state.loading = false;
      render();
    }
    if (state.data) { setTimeout(checkLineups, 1500); loadResults(); }
  }

  const matches = () => (state.data ? state.data.matches.slice().sort((a, b) => when(a.date) - when(b.date)) : []);
  const matchById = (id) => matches().find(m => m.event_id === id);

  // ── Riel (barra lateral en escritorio, tira en el teléfono) ───────────────
  function railItem(m) {
    const h = m.teams.home, a = m.teams.away;
    const on = state.view === m.event_id;
    const np = (state.data.picks || []).filter(k => k.event_id === m.event_id).length;
    const u = untilTxt(m.date);
    return `
      <button class="fb-rg lg-${m.league}${on ? ' is-selected' : ''}${u.past ? ' is-past' : ''}" data-ev="${esc(m.event_id)}" aria-pressed="${on}">
        <span class="fb-rg-time"><b>${hhmm(m.date)}</b><small class="${u.live ? 'is-live' : ''}">${u.live ? 'En vivo' : esc(dayLabel(m.date))}</small></span>
        <span class="fb-rg-teams">
          <span><img src="${logo(h.team_id)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">${esc(h.name)}</span>
          <span><img src="${logo(a.team_id)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">${esc(a.name)}</span>
        </span>
        <span class="fb-rg-meta">
          ${state.fav.has(m.event_id) ? `<i class="fb-rg-fav" title="En Favoritos">${I.starFill}</i>` : ''}
          ${np ? `<i class="fb-rg-np" title="${np} jugada${np > 1 ? 's' : ''}">${np}</i>` : ''}
          ${m.lineup_confirmed || m.lineup_official ? `<i class="fb-rg-xi${luPending(m) ? ' is-new' : ''}" title="${luPending(m) ? 'Salió la alineación oficial: falta simular' : 'Alineación oficial'}">${I.check}</i>` : ''}
        </span>
      </button>`;
  }

  function railGroups(list) {
    return LEAGUES.map(L => {
      const ms = list.filter(m => m.league === L.id);
      if (!ms.length) return '';
      return `
        <div class="fb-rail-league lg-${L.id}">
          <div class="fb-rail-lhead"><img src="${L.logo}" alt="" onerror="this.remove()"><span>${esc(L.name)}</span><small>${ms.length}</small></div>
          ${ms.map(railItem).join('')}
        </div>`;
    }).join('');
  }

  function renderRail() {
    const el = document.getElementById('fb-rail');
    if (!el) return;
    if (!state.data) {
      el.innerHTML = `<div class="fb-rail-empty">${state.error ? esc(state.error) : 'Cargando jornada…'}</div>`;
      return;
    }
    const list = matches().filter(m => !untilTxt(m.date).past);
    el.innerHTML = `
      <div class="fb-rail-head"><span>Jornada</span><small>${list.length} partidos · ${(state.data.picks || []).length} jugadas</small></div>
      <div class="fb-rail-list">${railGroups(list)}</div>`;
  }

  function mobileStrip() {
    if (!state.data) return '';
    const list = matches().filter(m => state.league === 'all' || m.league === state.league);
    const nPicks = (state.data.picks || []).length;
    return `
      <div class="fb-mstrip">
        <div class="fb-mstrip-seg" role="tablist">
          <button data-lg="all" class="${state.league === 'all' ? 'is-on' : ''}">Todas</button>
          ${LEAGUES.map(L => `<button data-lg="${L.id}" class="${state.league === L.id ? 'is-on' : ''}">${L.short}</button>`).join('')}
        </div>
        <div class="fb-mstrip-list">
          <button class="fb-rail-picks${state.view === 'picks' ? ' is-selected' : ''}" data-ev="picks"><span>${I.star}Picks</span><b>${nPicks}</b></button>
          ${list.map(railItem).join('')}
        </div>
      </div>`;
  }

  // ── Barra superior: estado y menú de herramientas ───────────────────────
  function oddsAgeTxt() {
    const d = state.data;
    const ages = d && d.odds ? Object.values(d.odds.age_s).filter(v => v != null) : [];
    if (!ages.length) return { t: 'Sin cuotas', old: true };
    const a = Math.max(...ages);
    return { t: a < 3600 ? `Cuotas de hace ${Math.max(1, Math.round(a / 60))} min` : `Cuotas de hace ${Math.round(a / 3600)} h`, old: a >= ((d.odds.ttl_s) || 10800) };
  }
  // Créditos de las APIs (OddsPapi, API-Football, The Odds API)
  const API_SHORT = { oddspapi: 'OddsPapi', apifootball: 'API-Fútbol', theoddsapi: 'Odds API' };
  async function loadUsage(fresh = false) {
    try {
      state.usage = await (await fetch(`${apiBase()}/api/usage${fresh ? '?fresh=true' : ''}`)).json();
      renderTopTools();
    } catch (_) { /* sin red */ }
  }
  function usageList() {
    const u = state.usage || {};
    return ['oddspapi', 'apifootball', 'theoddsapi'].map(id => ({ id, ...(u[id] || {}) }))
      .filter(a => a.limit != null && a.used != null);
  }
  const level = (a) => { const r = a.used / Math.max(1, a.limit); return r >= 0.9 ? 'is-red' : r >= 0.7 ? 'is-amber' : ''; };
  function creditsPop() {
    const list = usageList();
    return `
      <div class="fb-credits-pop" role="dialog" aria-label="Créditos de las APIs">
        <div class="fb-credits-head"><b>Créditos</b><button data-credits-refresh="1">${I.refresh}Actualizar</button></div>
        ${list.map(a => `
          <div class="fb-credit ${level(a)}">
            <div class="fb-credit-top"><b>${esc(a.name)}</b><span><strong>${a.used}</strong> de ${a.limit} · ${esc(a.period)}</span></div>
            <i><u style="width:${Math.min(100, (a.used / Math.max(1, a.limit)) * 100).toFixed(1)}%"></u></i>
            <small>${esc(a.use || '')}${a.exact ? '' : ` · contadas por la app${a.since ? ` desde el ${esc(a.since.slice(8, 10))}/${esc(a.since.slice(5, 7))}` : ''}`}</small>
          </div>`).join('') || '<p class="fb-credit-empty">Sin datos de consumo todavía.</p>'}
      </div>`;
  }
  function renderTopTools() {
    const el = document.getElementById('fb-topbar-tools');
    if (!el) return;
    if (!state.data) { el.innerHTML = ''; return; }
    const o = oddsAgeTxt();
    const busy = state.loading || state.sim.running || state.sim.remote;
    const list = usageList();
    el.innerHTML = `
      <div class="fb-hud">
        <span class="fb-hud-odds${o.old ? ' is-old' : ''}${busy ? ' is-busy' : ''}" title="Jornada calculada ${esc(ago(state.data.generated_at))}">
          <i></i><span><small>${busy ? 'Calculando' : 'Cuotas'}</small><b>${busy ? (state.loading ? 'Recalculando…' : 'Simulando…') : esc(o.t.replace('Cuotas de ', ''))}</b></span>
        </span>
        ${list.length ? `
        <button class="fb-hud-credits${state.creditsOpen ? ' is-on' : ''}" data-credits="1" aria-expanded="${!!state.creditsOpen}" aria-label="Créditos de las APIs">
          ${list.map(a => `
            <span class="fb-cr ${level(a)}" title="${esc(a.name)}: ${a.used} de ${a.limit} (${esc(a.period)})">
              <small>${esc(API_SHORT[a.id] || a.name)}</small>
              <span><b>${a.used}</b><em>/${a.limit}</em></span>
              <i><u style="width:${Math.min(100, (a.used / Math.max(1, a.limit)) * 100).toFixed(1)}%"></u></i>
            </span>`).join('')}
        </button>` : ''}
        ${state.creditsOpen ? creditsPop() : ''}
      </div>
      <div class="fb-menu-wrap">
        <button class="fb-menu-btn${state.menuOpen ? ' is-on' : ''}" data-menu="1" aria-expanded="${!!state.menuOpen}" aria-label="Herramientas">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="4.5" cy="10" r="1.7" fill="currentColor"/><circle cx="10" cy="10" r="1.7" fill="currentColor"/><circle cx="15.5" cy="10" r="1.7" fill="currentColor"/></svg>
        </button>
        ${state.menuOpen ? `
        <div class="fb-menu" role="menu">
          <div class="fb-menu-meta"><b>Jornada</b><span>Calculada ${esc(ago(state.data.generated_at))} · ${esc(o.t.toLowerCase())}</span></div>
          <button role="menuitem" data-act="refresh" ${state.loading ? 'disabled' : ''}>${I.refresh}<span><b>Recalcular la jornada</b><small>ESPN + 20.000 simulaciones por partido. Si las cuotas tienen más de 3 h, también las pide.</small></span></button>
          <button role="menuitem" data-act="odds" ${state.loading ? 'disabled' : ''}>${I.coin}<span><b>Cuotas nuevas</b><small>bet365, Betano y Betsson. Gasta 3 consultas de OddsPapi.</small></span></button>
          <button role="menuitem" data-act="sync">${I.swap}<span><b>Sincronizar ahora</b><small>Trae lo último que hiciste en otro dispositivo.</small></span></button>
        </div>` : ''}
      </div>`;
  }

  // ── Herramientas: recalcular / cuotas nuevas ──────────────────────────────
  function toolsHtml() {
    const d = state.data;
    const gen = d ? `Calculado ${ago(d.generated_at)}` : '';
    const ages = d && d.odds ? Object.values(d.odds.age_s).filter(v => v != null) : [];
    const oddsAge = ages.length ? Math.max(...ages) : null;
    const oddsTxt = oddsAge == null ? 'sin cuotas guardadas'
      : `cuotas de hace ${oddsAge < 3600 ? Math.round(oddsAge / 60) + ' min' : Math.round(oddsAge / 3600) + ' h'}`;
    return `
      <div class="fb-tools">
        <div class="fb-tools-meta">${esc(gen)} · ${esc(oddsTxt)}</div>
        <div class="fb-tools-btns">
          <button class="fb-btn" data-act="refresh" ${state.loading ? 'disabled' : ''}>${I.refresh}Recalcular</button>
          <button class="fb-btn fb-btn--odds" data-act="odds" ${state.loading ? 'disabled' : ''}>${I.coin}Cuotas nuevas</button>
        </div>
      </div>`;
  }

  function confirmDialog({ title, body, ok }) {
    return new Promise(res => {
      const wrap = document.createElement('div');
      wrap.className = 'fb-modal';
      wrap.innerHTML = `
        <div class="fb-modal-card" role="dialog" aria-modal="true" aria-labelledby="fb-modal-t">
          <div class="fb-modal-t" id="fb-modal-t">${esc(title)}</div>
          <div class="fb-modal-b">${body}</div>
          <div class="fb-modal-btns">
            <button class="fb-btn" data-r="0">Cancelar</button>
            <button class="fb-btn fb-btn--primary" data-r="1">${esc(ok)}</button>
          </div>
        </div>`;
      const done = (v) => { wrap.remove(); document.removeEventListener('keydown', onKey); res(v); };
      const onKey = (e) => { if (e.key === 'Escape') done(false); };
      wrap.addEventListener('click', e => {
        if (e.target === wrap) return done(false);
        const b = e.target.closest('[data-r]');
        if (b) done(b.dataset.r === '1');
      });
      document.addEventListener('keydown', onKey);
      document.body.appendChild(wrap);
      wrap.querySelector('[data-r="1"]').focus();
    });
  }

  async function onTool(act) {
    if (state.loading) return;
    const o = (state.data && state.data.odds) || {};
    const cost = o.cost_per_refresh || 3, plan = o.plan_month || 250;
    if (act === 'odds') {
      const ok = await confirmDialog({
        title: 'Pedir cuotas nuevas',
        body: `Se piden cuotas a OddsPapi para bet365 AR, Betano y Betsson. Gasta <b>${cost} consultas</b> del plan de ${plan} por mes. Después se recalcula la jornada (tarda un par de minutos).`,
        ok: `Sí, gastar ${cost}`,
      });
      if (ok) load('?refresh_odds=true').then(() => loadUsage(true));
    } else {
      if (o.would_fetch) {
        const ok = await confirmDialog({
          title: 'Las cuotas están viejas',
          body: `Las cuotas guardadas tienen más de ${Math.round((o.ttl_s || 10800) / 3600)} h, así que recalcular también las vuelve a pedir: gasta <b>${cost} consultas</b> del plan de ${plan} por mes.`,
          ok: `Recalcular y gastar ${cost}`,
        });
        if (!ok) return;
      }
      load('?refresh=true');
    }
  }

  // ── Picks ─────────────────────────────────────────────────────────────────
  function bookChips(l, minOdds) {
    return BOOKS.map(b => {
      const q = l.books && l.books[b.id];
      if (!q) return `<span class="fb-book fb-book--na">${bookBadge(b.id)}<b>—</b></span>`;
      const good = q.price >= minOdds;
      return `<span class="fb-book${good ? ' fb-book--good' : ''}">${bookBadge(b.id)}<b>${odd(q.price)}</b></span>`;
    }).join('');
  }

  function pickRow(k) {
    const m = matchById(k.event_id);
    const team = m ? teamOf(m, k.team_id) : null;
    const p = m ? m.players.find(x => x.player_id === k.player_id) : null;
    const photo = p && p.photo;
    const L = LEAGUES.find(x => x.id === k.league);
    return `
      <article class="fb-pick" data-ev="${esc(k.event_id)}">
        <div class="fb-pick-who">
          <div class="fb-face fb-face--sm">
            ${photo ? `<img src="${esc(photo)}" alt="" loading="lazy">` : `<span>${esc(initials(k.player))}</span>`}
            ${team ? `<img class="fb-face-crest" src="${logo(team.team_id)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
          </div>
          <div class="fb-pick-name">
            <b>${esc(k.player)}</b>
            <small>${esc(team ? team.name : '')} · ${esc(roleName(k))} · ${k.exp_minutes}'</small>
            <small class="fb-pick-match">${esc(L ? L.short : '')} · ${dayLabel(k.date)} ${hhmm(k.date)} · ${esc(k.match)}${k.lineup ? '' : ' · <em>formación estimada</em>'}</small>
          </div>
        </div>
        <div class="fb-pick-bet">
          <span class="fb-pick-line">${esc(lineLabel(k.stat, k.line))}</span>
          <span class="fb-pick-prob"><b>${pct(k.p_model)}</b><small>prob.</small></span>
        </div>
        <div class="fb-pick-odds">
          <span class="fb-pick-min"><small>Cuota mínima</small><b>${odd(k.min_odds)}</b><i>justa ${odd(k.fair_odds)}</i></span>
          <span class="fb-pick-books">${bookChips(k, k.min_odds)}</span>
        </div>
      </article>`;
  }

  function picksBlock(list, { title, empty }) {
    const sorted = list.slice().sort((a, b) => (when(a.date) - when(b.date)) || (b.p_model - a.p_model));
    return `
      <section class="fb-block">
        <div class="fb-block-head">
          <h3>${title}</h3>
          <span class="fb-block-sub">${list.length ? `${list.length} · probabilidad entre 30 % y 85 % y una casa paga la cuota mínima` : ''}</span>
        </div>
        ${list.length ? `<div class="fb-picks">${sorted.map(pickRow).join('')}</div>`
          : `<div class="fb-empty">${empty}</div>`}
      </section>`;
  }

  function picksView() {
    const d = state.data;
    const lg = state.league;
    const list = (d.picks || []).filter(k => lg === 'all' || k.league === lg);
    return `
      <header class="fb-page-head">
        <div>
          <div class="fb-kicker">Remates · Liga Profesional, Brasileirão y Premier</div>
          <h2 class="fb-title">Picks de la <em>jornada</em></h2>
          <p class="fb-lede">La probabilidad que da el modelo y la <b>cuota mínima</b> que hace falta para que la apuesta tenga sentido (cuota justa + ${Math.round((d.min_edge || 0.05) * 100)} %). En verde, las casas que la pagan. Si tu casa no está (Betwarrior), jugala sólo si te dan la mínima o más.</p>
        </div>
      </header>
      ${leagueChips()}
      ${picksBlock(list, { title: 'Por horario', empty: 'Ningún pick en esta liga: ninguna casa paga la cuota mínima en líneas jugables. Mirá los partidos igual: la cuota mínima sirve para cualquier casa.' })}`;
  }

  // ── Vista simple: "la agenda" ─────────────────────────────────────────────
  // Lo que hay que apostar, ordenado por horario. Cada jugada se lee en una
  // frase, con 10 pelotas = "de cada 10 partidos así, pasa en N", la cuota
  // desde la que conviene y qué casa ya la paga.
  // Alineación oficial publicada pero el partido todavía no se re-simuló con ella.
  const luPending = (m) => !!(m && m.lineup_official && !m.lineup_confirmed);
  const pickKey = (k) => `${k.event_id}|${k.player_id}|${k.stat}|${k.line}`;
  function betSentence(k) {
    const n = Math.round(k.line + 0.5);
    if (k.stat === 'shots') return n === 1 ? 'Remata al menos <b>1</b> vez' : `Remata <b>${n}</b> o más veces`;
    return n === 1 ? 'Patea <b>1</b> al arco' : `Patea <b>${n}</b> o más al arco`;
  }
  function verdict(p) {
    if (p >= 0.65) return { t: 'Muy probable', c: 'hi' };
    if (p >= 0.5) return { t: 'Probable', c: 'mid' };
    return { t: 'Paga por encima de lo esperado', c: 'lo' };
  }
  const BALL_ON = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.6" fill="currentColor"/><path d="M10 6.2l3.1 2.3-1.2 3.6H8.1L6.9 8.5z" fill="#06090c"/></svg>';
  const BALL_OFF = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  function balls(p, key = '') {
    const n = Math.max(0, Math.min(10, Math.round(p * 10)));
    return `<button class="fb-balls" data-balls="${esc(key)}" aria-label="${n} de cada 10 — tocá para ver qué significa">${Array.from({ length: 10 }, (_, i) =>
      `<i class="${i < n ? 'on' : ''}" style="--i:${i}">${i < n ? BALL_ON : BALL_OFF}</i>`).join('')}</button>`;
  }
  function untilTxt(iso) {
    const min = Math.round((when(iso) - Date.now()) / 60000);
    if (min <= -110) return { t: 'Terminado', live: false, past: true };
    if (min <= 0) return { t: 'En juego', live: true };
    if (min < 60) return { t: `Empieza en ${min} min`, soon: true };
    const h = Math.floor(min / 60), mm = min % 60;
    if (h < 24) return { t: `Empieza en ${h} h${mm ? ' ' + String(mm).padStart(2, '0') : ''}`, soon: h < 2 };
    return { t: `${dayLabel(iso)} ${hhmm(iso)}` };
  }
  function bestBooks(k) {
    return BOOKS.map(b => ({ ...b, q: k.books && k.books[b.id] }))
      .filter(b => b.q).sort((a, b) => b.q.price - a.q.price);
  }

  function ticket(k) {
    const m = matchById(k.event_id);
    const team = m ? teamOf(m, k.team_id) : null;
    const pl = m ? m.players.find(x => x.player_id === k.player_id) : null;
    const v = verdict(k.p_model);
    const on = state.slip.has(pickKey(k));
    const books = bestBooks(k);
    const best = books[0];
    const n10 = Math.round(k.p_model * 10);
    return `
      <article class="fb-tk fb-tk--${v.c}${on ? ' is-in' : ''}">
        <div class="fb-tk-face">
          ${pl && pl.photo ? `<img src="${esc(pl.photo)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
          <span>${esc(initials(k.player))}</span>
          ${team ? `<img class="fb-tk-crest" src="${logo(team.team_id)}" alt="" loading="lazy" onerror="this.remove()">` : ''}
        </div>
        <div class="fb-tk-main">
          <div class="fb-tk-who"><b>${esc(k.player)}</b><small>${esc(team ? team.name : '')}${k.lineup ? '' : ' · alineación probable'}</small></div>
          <div class="fb-tk-bet">${betSentence(k)}</div>
          <div class="fb-tk-odds">
            ${balls(k.p_model, pickKey(k))}
            <span class="fb-tk-n"><b>${n10}</b> de cada 10 · <em>${v.t}</em></span>
          </div>
          ${state.ballOpen === pickKey(k) ? `<div class="fb-ball-tip">En <b>${n10} de cada 10</b> partidos simulados, ${esc(surname(k.player))} ${betSentence(k).replace(/<\/?b>/g, '').toLowerCase()}. La casa paga ${odd(best ? best.q.price : k.min_odds)}: ${best && best.q.price >= k.min_odds ? 'más de lo que vale según el modelo.' : 'todavía por debajo de lo que vale.'}</div>` : ''}
        </div>
        <div class="fb-tk-side">
          <div class="fb-tk-min"><small>Jugala desde</small><b>${odd(k.min_odds)}</b></div>
          <div class="fb-tk-books">${BOOKS.map(b => {
            const q = k.books && k.books[b.id];
            const isBest = best && q && b.id === best.id;
            return `<span class="fb-bkc${q ? '' : ' is-na'}${isBest ? ' is-best' : ''}${q && q.price >= k.min_odds ? ' is-ok' : ''}">${isBest ? '<em>Mejor</em>' : ''}${bookBadge(b.id, 'is-sm')}<b>${q ? odd(q.price) : '—'}</b></span>`;
          }).join('')}</div>
          <button class="fb-tk-add" data-slip="${esc(pickKey(k))}" aria-pressed="${on}">${on ? `${I.check}En mi boleta` : '+ La juego'}</button>
        </div>
      </article>`;
  }

  function simpleView() {
    const d = state.data;
    const lg = state.league;
    const list = (d.picks || []).filter(k => lg === 'all' || k.league === lg)
      .filter(k => !untilTxt(k.date).past);
    // agrupado por partido, en orden de horario
    const byEv = new Map();
    list.slice().sort((a, b) => (when(a.date) - when(b.date)) || (b.p_model - a.p_model))
      .forEach(k => { if (!byEv.has(k.event_id)) byEv.set(k.event_id, []); byEv.get(k.event_id).push(k); });
    const next = [...byEv.values()][0];
    const nextU = next ? untilTxt(next[0].date) : null;
    const groups = [...byEv.entries()].map(([ev, ks], gi) => {
      const m = matchById(ev);
      const u = untilTxt(ks[0].date);
      const h = m && m.teams.home, a = m && m.teams.away;
      return `
        <section class="fb-ag lg-${ks[0].league}" style="--d:${gi}">
          <button class="fb-ag-head" data-ev="${esc(ev)}" title="Ver el partido completo">
            <span class="fb-ag-time"><b>${hhmm(ks[0].date)}</b><small>${dayLabel(ks[0].date)}</small></span>
            <span class="fb-ag-match">
              ${h ? `<img src="${logo(h.team_id)}" alt="" onerror="this.style.visibility='hidden'">` : ''}<b>${esc(h ? h.name : ks[0].match)}</b>
              <i>vs</i>
              ${a ? `<img src="${logo(a.team_id)}" alt="" onerror="this.style.visibility='hidden'">` : ''}<b>${esc(a ? a.name : '')}</b>
            </span>
            ${state.league === 'all' ? `<img class="fb-ag-lg" src="${leagueOf(ks[0].league).logo}" alt="${esc(leagueOf(ks[0].league).short)}" title="${esc(leagueOf(ks[0].league).name)}">` : ''}
            <span class="fb-ag-when${u.live ? ' is-live' : u.soon ? ' is-soon' : ''}">${esc(u.t)}</span>
          </button>
          <div class="fb-ag-list">${ks.map(ticket).join('')}</div>
        </section>`;
    }).join('');
    return `
      <header class="fb-hero">
        <div class="fb-hero-big"><b>${list.length}</b><span>jugada${list.length === 1 ? '' : 's'}<br>en la jornada</span></div>
        <div class="fb-hero-facts">
          ${next ? `<div><small>Lo próximo</small><b>${esc(next[0].match)}</b><span class="${nextU.live ? 'is-live' : nextU.soon ? 'is-soon' : ''}">${esc(nextU.t)}</span></div>` : ''}
        </div>
        <p class="fb-hero-how">Cada jugada dice qué pasa, cuántas veces de cada 10 pasa según el modelo y <b>desde qué cuota conviene</b>. Si tu casa paga eso o más, va. En verde, la casa que ya la paga.</p>
      </header>
      ${leagueChips()}
      ${groups || `<div class="fb-empty">No hay jugadas en esta liga: ninguna casa paga lo que hace falta. Mirá los partidos igual en <b>Partidos</b>.</div>`}`;
  }

  // ── Resultados: récord y picks liquidados (GANADA / PERDIDA / NULA) ──────
  const units = (u) => `${u > 0 ? '+' : u < 0 ? '−' : ''}${Math.abs(u).toFixed(1).replace('.', ',')} u`;
  function recordFact() {
    const sm = state.results && state.results.summary;
    if (!sm || !sm.settled) {
      return `<div><small>Récord</small><b>—</b><span>${sm && sm.open ? `${sm.open} en juego o por jugarse` : 'se completa solo cuando terminan los partidos'}</span></div>`;
    }
    return `<div class="fb-rec"><small>Récord</small><b><em class="w">${sm.won}</em>–<em class="l">${sm.lost}</em></b>
      <span class="${sm.units >= 0 ? 'up' : 'down'}">${units(sm.units)}</span><span> · el modelo esperaba ${String(sm.expected_won).replace('.', ',')}</span></div>`;
  }
  const STAMP = { won: 'Ganada', lost: 'Perdida', void: 'Nula' };
  function resultRow(r) {
    const lg = LEAGUES.find(x => x.id === r.league);
    const act = r.actual == null ? 'no jugó' : `${Math.round(r.actual)} ${r.stat === 'shots' ? (Math.round(r.actual) === 1 ? 'remate' : 'remates') : 'al arco'}`;
    const price = r.first_price ? odd(r.first_price) : '—';
    const pl = r.status === 'won' && r.first_price ? units(r.first_price - 1) : r.status === 'lost' ? units(-1) : '';
    return `
      <div class="fb-res fb-res--${r.status} lg-${r.league}">
        <span class="fb-res-stamp">${STAMP[r.status] || 'En juego'}</span>
        <div class="fb-res-who"><b>${esc(r.player)}</b><small>${esc(lg ? lg.short : '')} · ${dayLabel(r.date)} · ${esc(r.match)}</small></div>
        <div class="fb-res-bet">${esc(lineLabel(r.stat, r.line))}<small>${r.status === 'open' ? 'esperando el final' : act}</small></div>
        <div class="fb-res-odd"><b>${price}</b>${r.first_book ? bookBadge(r.first_book, 'is-sm') : ''}</div>
        <div class="fb-res-pl">${pl}</div>
      </div>`;
  }
  function resultsBlock() {
    const R = state.results;
    if (!R || !R.picks || !R.picks.length) return '';
    const now = Date.now();
    // Lo que ya empezó: liquidado o esperando el final.
    const list = R.picks.filter(r => r.status !== 'open' || when(r.date).getTime() < now - 110 * 60000)
      .filter(r => state.league === 'all' || r.league === state.league);
    if (!list.length) return '';
    const sm = R.summary_recent || {};
    const clv = R.summary && R.summary.clv != null && R.summary.clv_n >= 5
      ? `<span title="Precio tomado contra el último precio antes del partido">Cuota tomada vs cierre: <b class="${R.summary.clv >= 0 ? 'up' : 'down'}">${R.summary.clv >= 0 ? '+' : ''}${(R.summary.clv * 100).toFixed(1).replace('.', ',')} %</b></span>` : '';
    return `
      <section class="fb-block fb-results">
        <div class="fb-block-head fb-res-head">
          <span class="fb-block-sub">Últimos 10 días · ${sm.won || 0} ganadas, ${sm.lost || 0} perdidas${sm.void ? `, ${sm.void} nulas` : ''} · ${units(sm.units || 0)} a 1 u por pick ${clv ? '· ' + clv : ''}</span>
        </div>
        <div class="fb-res-list">${list.map(resultRow).join('')}</div>
        <p class="fb-note">Se liquida solo con ESPN cuando termina el partido. Nula = el jugador no entró (la casa devuelve la apuesta). Las unidades usan la mejor cuota cuando apareció el pick.</p>
      </section>`;
  }

  // ── Mi boleta: las jugadas marcadas, para copiar ─────────────────────────
  function slipPicks() {
    return (state.data?.picks || []).filter(k => state.slip.has(pickKey(k)))
      .sort((a, b) => when(a.date) - when(b.date));
  }
  function slipHtml() {
    const ks = slipPicks();
    if (!ks.length) return '';
    return `
      <aside class="fb-slip${state.slipOpen ? ' is-open' : ''}">
        <button class="fb-slip-bar" data-slipact="toggle" aria-expanded="${state.slipOpen}">
          <span class="fb-slip-count">${ks.length}</span>
          <span>Mi boleta</span>
          <small>${state.slipOpen ? 'Cerrar' : 'Ver'}</small>
        </button>
        <div class="fb-slip-body">
          ${ks.map(k => `
            <div class="fb-slip-row">
              <div><b>${esc(k.player)}</b><small>${esc(lineLabel(k.stat, k.line))} · ${hhmm(k.date)} ${esc(k.match)}</small></div>
              <span>desde <b>${odd(k.min_odds)}</b></span>
              <button data-slip="${esc(pickKey(k))}" aria-label="Sacar">×</button>
            </div>`).join('')}
          <div class="fb-slip-btns">
            <button class="fb-btn" data-slipact="clear">Vaciar</button>
            <button class="fb-btn fb-btn--primary" data-slipact="copy">Copiar boleta</button>
          </div>
        </div>
      </aside>`;
  }
  function slipText() {
    return slipPicks().map(k => `${k.player} — ${lineLabel(k.stat, k.line)} — desde ${odd(k.min_odds)} (${hhmm(k.date)} ${k.match})`).join('\n');
  }
  function modeSwitch() {
    return `
      <div class="fb-mode" role="tablist" aria-label="Vista">
        <button data-mode="simple" class="${state.mode === 'simple' ? 'is-on' : ''}" role="tab" aria-selected="${state.mode === 'simple'}">Simple</button>
        <button data-mode="full" class="${state.mode === 'full' ? 'is-on' : ''}" role="tab" aria-selected="${state.mode === 'full'}">Completo</button>
      </div>`;
  }

  // ── Partido ───────────────────────────────────────────────────────────────
  function rangeBar(mean, lo, hi, max) {
    const x = (v) => Math.max(0, Math.min(100, (v / max) * 100));
    return `
      <div class="fb-range" role="img" aria-label="Rango 10–90 %: ${lo} a ${hi}">
        <span class="fb-range-band" style="left:${x(lo)}%;width:${Math.max(1, x(hi) - x(lo))}%"></span>
        <span class="fb-range-mean" style="left:${x(mean)}%"></span>
      </div>`;
  }

  function teamPanel(t, side, maxShots, maxSot) {
    const so = Object.entries(t.shots_over || {}).map(([l, p]) => [Number(l), p]).sort((a, b) => a[0] - b[0]);
    const ao = Object.entries(t.sot_over || {}).map(([l, p]) => [Number(l), p]).sort((a, b) => a[0] - b[0]);
    const chips = (arr) => arr.map(([l, p]) => `<span title="Más de ${l}: ${pct(p)}"><small>+${String(l).replace('.', ',')}</small>${pct(p)}</span>`).join('');
    return `
      <div class="fb-team fb-team--${side}">
        <div class="fb-team-id">
          <img src="${logo(t.team_id)}" alt="" onerror="this.style.visibility='hidden'">
          <div><b>${esc(t.name)}</b><small>${side === 'home' ? 'Local' : 'Visitante'}</small></div>
        </div>
        <div class="fb-team-stats">
          <div class="fb-tstat">
            <small>Remates</small>
            <b>${num1(t.shots)}</b>
            <i>${t.shots_range[0]}–${t.shots_range[1]}</i>
            ${rangeBar(t.shots, t.shots_range[0], t.shots_range[1], maxShots)}
          </div>
          <div class="fb-tstat">
            <small>Al arco</small>
            <b>${num1(t.sot)}</b>
            <i>${t.sot_range[0]}–${t.sot_range[1]}</i>
            ${rangeBar(t.sot, t.sot_range[0], t.sot_range[1], maxSot)}
          </div>
        </div>
        <details class="fb-team-lines">
          <summary>Líneas del equipo</summary>
          <div class="fb-tl"><em>Remates</em>${chips(so)}</div>
          <div class="fb-tl"><em>Al arco</em>${chips(ao)}</div>
        </details>
      </div>`;
  }

  // Línea que muestra el cromo de entrada: la que paga la mínima, si no la
  // primera con cuotas de las casas, si no 1+ remate.
  function defaultKey(p) {
    const ls = CELLS.map(c => lineOf(p, c.stat, c.line)).filter(Boolean);
    const l = ls.find(pays) || ls.find(x => Object.keys(x.books || {}).length);
    return l ? `${l.stat}|${l.line}` : 'shots|0.5';
  }

  function card(p, m) {
    const team = teamOf(m, p.team_id);
    const key = state.sel[p.player_id] || defaultKey(p);
    const [sStat, sLine] = key.split('|');
    const sel = lineOf(p, sStat, Number(sLine));
    const cell = (c) => {
      const l = lineOf(p, c.stat, c.line);
      const on = sStat === c.stat && Math.abs(Number(sLine) - c.line) < 1e-6;
      if (!l) return `<button class="fb-cell fb-cell--na" disabled><small>${plus(c.line)}</small><b>—</b></button>`;
      const heat = Math.round(l.p_model * 100);
      return `<button class="fb-cell${on ? ' is-on' : ''}${pays(l) ? ' fb-cell--pays' : ''}" data-pl="${esc(p.player_id)}" data-k="${c.stat}|${c.line}"
                style="--heat:${heat}%" aria-pressed="${on}" title="${esc(lineLabel(c.stat, c.line))}: ${pct(l.p_model)}">
                <small>${plus(c.line)}</small><b>${heat}<i>%</i></b></button>`;
    };
    const anyBook = p.lines.some(l => Object.keys(l.books || {}).length);
    return `
      <article class="fb-card${(state.data.picks || []).some(k => k.event_id === m.event_id && k.player_id === p.player_id) ? ' fb-card--pick' : ''}">
        <div class="fb-card-top">
          <div class="fb-face">
            ${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${esc(initials(p.player))}'}))">`
              : `<span>${esc(initials(p.player))}</span>`}
            <img class="fb-face-crest" src="${logo(team.team_id)}" alt="" loading="lazy" onerror="this.remove()">
          </div>
          <div class="fb-card-id">
            <b title="${esc(p.player)}">${esc(surname(p.player))}</b>
            <small>${esc(p.player.split(/\s+/).length > 1 ? p.player.split(/\s+/)[0] : '')}</small>
            <span class="fb-card-tags">
              <span class="fb-tag">${esc(p.pos)}</span>
              <span class="fb-tag">${esc(team.abbr)}</span>
              <span class="fb-tag fb-tag--min" title="Minutos esperados">${I.clock}${p.exp_minutes}'</span>
            </span>
          </div>
          <div class="fb-card-exp" title="Remates esperados / al arco esperados">
            <b>${num1(p.shots)}</b><small>rem.</small>
            <b>${num1(p.sot)}</b><small>arco</small>
          </div>
        </div>
        <div class="fb-grid">
          <div class="fb-grid-row"><em>Remates</em>${CELLS.filter(c => c.stat === 'shots').map(cell).join('')}</div>
          <div class="fb-grid-row"><em>Al arco</em>${CELLS.filter(c => c.stat === 'sot').map(cell).join('')}<span class="fb-cell fb-cell--blank"></span></div>
        </div>
        ${sel ? `
        <div class="fb-card-sel">
          <div class="fb-card-sel-l">
            <small>${esc(lineLabel(sel.stat, sel.line))}</small>
            <span><i>justa</i> ${odd(sel.fair_odds)}</span>
          </div>
          <div class="fb-card-min"><small>Cuota mínima</small><b>${odd(sel.min_odds)}</b></div>
        </div>
        <div class="fb-card-books">${anyBook ? bookChips(sel, sel.min_odds) : '<span class="fb-nobooks">Sin cuotas de las casas para este jugador</span>'}</div>`
        : `<div class="fb-card-sel fb-card-sel--na">Línea muy improbable (< 2 %)</div>`}
      </article>`;
  }

  // ════════════════════════════════════════════════════════════════════════
  //  Navegación: Jugadas · Partidos · Resultados (arriba en escritorio,
  //  abajo al alcance del pulgar en el teléfono).
  // ════════════════════════════════════════════════════════════════════════
  const SECTIONS = ['mine', 'picks', 'matches', 'results'];
  const isSection = (v) => SECTIONS.includes(v);
  function navItems() {
    const d = state.data || {};
    const live = (d.picks || []).filter(k => !untilTxt(k.date).past).length;
    const sm = state.results && state.results.summary;
    const cur = isSection(state.view) ? state.view : state.back;
    const item = (id, icon, label, badge) => `
      <button class="fb-nav-i${cur === id ? ' is-on' : ''}" data-ev="${id}" aria-current="${cur === id ? 'page' : 'false'}">
        ${icon}<span>${label}</span>${badge != null && badge !== '' ? `<b>${badge}</b>` : ''}
      </button>`;
    const mine = myMatches().length;
    return `<button class="fb-nav-i fb-nav-fav${cur === 'mine' ? ' is-on' : ''}" data-ev="mine" aria-current="${cur === 'mine' ? 'page' : 'false'}" aria-label="Favoritos: ${mine}">
        <span class="fb-fav-ct">${I.starFill}<b>${mine}</b></span><span>Favoritos</span></button>`
      + item('picks', I.tick, 'Jugadas', live || '')
      + item('matches', I.pitch, 'Partidos', matches().length || '')
      + item('results', I.trophy, 'Resultados', sm && sm.settled ? `${sm.won}–${sm.lost}` : '');
  }
  const navTop = () => `<nav class="fb-nav" aria-label="Fútbol">${navItems()}</nav>`;
  const navBottom = () => (state.data ? `<nav class="fb-tabbar" aria-label="Fútbol">${navItems()}</nav>` : '');
  function leagueChips() {
    const lg = state.league;
    return `
      <div class="fb-chips" role="tablist" aria-label="Liga">
        <button data-lg="all" class="${lg === 'all' ? 'is-on' : ''}">Todas</button>
        ${LEAGUES.map(L => `<button data-lg="${L.id}" class="fb-chip-lg lg-${L.id}${lg === L.id ? ' is-on' : ''}"><img src="${L.logo}" alt="" onerror="this.remove()"><span class="fb-chip-l">${L.name}</span><span class="fb-chip-s">${L.short}</span></button>`).join('')}
      </div>`;
  }

  // ── Mis partidos: los marcados con estrella + los de la boleta ──────────
  function myMatches() {
    const slipEv = new Set([...state.slip].map(k => k.split('|')[0]));
    return matches().filter(m => (state.fav.has(m.event_id) || slipEv.has(m.event_id)) && !untilTxt(m.date).past);
  }
  function starBtn(id) {
    const on = state.fav.has(id);
    return `<button class="fb-star${on ? ' is-on' : ''}" data-fav="${esc(id)}" aria-pressed="${on}" title="${on ? 'Sacar de Favoritos' : 'Agregar a Favoritos'}">${on ? I.starFill : I.star}</button>`;
  }
  function mineView() {
    const mine = myMatches();
    const slipEv = new Set([...state.slip].map(k => k.split('|')[0]));
    if (!mine.length) {
      const soon = matches().filter(m => !untilTxt(m.date).past).slice(0, 8);
      return `
        <header class="fb-sec-head">
          <h2>Favo<em>ritos</em></h2>
          <p>Tocá la estrella ☆ de un partido y queda guardado acá, con su simulación y sus jugadas. Los que tengan jugadas en tu boleta aparecen solos.</p>
        </header>
        <div class="fb-mine-empty">${I.star}<span>Todavía no marcaste ninguno. Estos son los próximos:</span></div>
        <div class="fb-fx-list">${soon.map(fixture).join('')}</div>`;
    }
    return `
      <header class="fb-sec-head">
        <h2>Favo<em>ritos</em></h2>
        <p>${mine.length} partido${mine.length > 1 ? 's' : ''} guardado${mine.length > 1 ? 's' : ''}. Entrá a uno y tocá <b>Simular partido</b> para recalcularlo con la última formación.</p>
      </header>
      <div class="fb-fx-list">${mine.map(m => fixture(m).replace('class="fb-fx ', `class="fb-fx ${slipEv.has(m.event_id) ? 'in-slip ' : ''}`)).join('')}</div>`;
  }

  // ── Simular partido: en segundo plano, con cola ─────────────────────────
  // No bloquea la app: la tarjeta del partido muestra su barra de progreso,
  // se puede seguir navegando y encolar otros; al terminar avisa.
  const simState = (id) => (state.sim.running === id || state.sim.remote === id) ? 'running' : state.sim.queue.includes(id) ? 'queued' : null;
  function simControl(m, big = false) {
    const st = simState(m.event_id);
    if (st === 'running') {
      return `<div class="fb-simrun${big ? ' is-big' : ''}" data-simprog="${esc(m.event_id)}">
        <i style="width:0%"></i><span>${I.play}${state.sim.remote === m.event_id ? 'Simulando en otro dispositivo…' : 'Simulando…'} <b>0</b> de 20.000</span></div>`;
    }
    if (st === 'queued') {
      const n = state.sim.queue.indexOf(m.event_id) + 1;
      return `<div class="fb-simrun is-queued${big ? ' is-big' : ''}"><span>${I.clock}En cola (${n}) · empieza cuando termine la anterior</span></div>`;
    }
    return `<button class="fb-simbtn${big ? '' : ' is-small'}" data-sim="${esc(m.event_id)}">${I.play}<span><b>Simular partido</b><small>20.000 veces con la formación más reciente · seguís usando la app</small></span></button>`;
  }
  function simulateMatch(id) {
    if (!matchById(id) || simState(id)) return;
    state.ver = null;                      // la jornada va a cambiar por nosotros: no avisar
    state.sim.queue.push(id);
    render();
    runSimQueue();
  }
  function simProgressLoop() {
    if (!state.sim.running && !state.sim.remote) return;
    const s = (performance.now() - (state.sim.running ? state.sim.t0 : state.sim.remoteT0)) / 1000;
    const n = Math.round(20000 * (1 - Math.exp(-s / 9)) * 0.97);
    document.querySelectorAll('[data-simprog]').forEach(el => {
      const b = el.querySelector('b'), i = el.querySelector('i');
      if (b) b.textContent = n.toLocaleString('es-AR');
      if (i) i.style.width = `${(n / 200).toFixed(1)}%`;
    });
    requestAnimationFrame(simProgressLoop);
  }
  async function runSimQueue() {
    if (state.sim.running || !state.sim.queue.length) return;
    const id = state.sim.queue.shift();
    const m = matchById(id);
    state.sim.running = id;
    state.sim.t0 = performance.now();
    render();
    requestAnimationFrame(simProgressLoop);
    const name = m ? `${m.teams.home.abbr} vs ${m.teams.away.abbr}` : 'el partido';
    try {
      let r, res = {};
      for (let tries = 0; tries < 20; tries++) {         // si el servidor está ocupado, espera y reintenta
        r = await fetch(`${apiBase()}/api/futbol/simulate?event_id=${encodeURIComponent(id)}`);
        res = await r.json().catch(() => ({}));
        if (r.status !== 409) break;
        await new Promise(ok => setTimeout(ok, 5000));
      }
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      state.data = await fetchJornada('');
      state.ver = null;
      const b = res.before, a = res.after;
      const delta = (side) => {
        const d = a.teams[side].shots - b.teams[side].shots;
        return Math.abs(d) < 0.05 ? 'igual' : `${d > 0 ? '+' : '−'}${num1(Math.abs(d))}`;
      };
      const lu = a.lineup_confirmed ? 'con la formación confirmada' : 'con las formaciones probables';
      toast(`${I.check}<span><b>Simulación lista · ${esc(name)}</b>${lu} · remates ${delta('home')} / ${delta('away')} · ${a.picks} jugada${a.picks === 1 ? '' : 's'} <u>Ver partido</u></span>`, id);
    } catch (e) {
      toast(`<span><b>No se pudo simular ${esc(name)}</b>${esc(e.message || String(e))}</span>`);
    } finally {
      state.sim.running = null;
      render();
      runSimQueue();
    }
  }

  // ── Partido desplegado en la lista: simular, vista previa y alineación ──
  function simPreview(m) {
    const h = m.teams.home, a = m.teams.away;
    const lines = [8.5, 10.5, 12.5, 14.5];
    const bar = (p) => `<i style="width:${Math.round(p * 100)}%"></i><b>${Math.round(p * 100)}%</b>`;
    const top = (t) => m.players.filter(p => p.team_id === t.team_id).slice(0, 3)
      .map(p => `<li><span>${esc(surname(p.player))}</span><b>${num1(p.shots)}</b></li>`).join('');
    return `
      <div class="fb-prev">
        <div class="fb-prev-head"><b>Vista previa de la simulación</b><small>de cada 100 partidos así</small></div>
        <div class="fb-prev-rows">
          <div class="fb-prev-teams"><span></span><span><img src="${logo(h.team_id)}" alt="" onerror="this.remove()">${esc(h.abbr)}</span><span><img src="${logo(a.team_id)}" alt="" onerror="this.remove()">${esc(a.abbr)}</span></div>
          ${lines.map(l => `
            <div class="fb-prev-row">
              <small>Más de ${String(l).replace('.', ',')}</small>
              <span class="fb-prev-bar">${bar((h.shots_over || {})[String(l)] || 0)}</span>
              <span class="fb-prev-bar is-away">${bar((a.shots_over || {})[String(l)] || 0)}</span>
            </div>`).join('')}
        </div>
        <div class="fb-prev-top">
          <div><small>Más remates · ${esc(h.abbr)}</small><ol>${top(h)}</ol></div>
          <div><small>Más remates · ${esc(a.abbr)}</small><ol>${top(a)}</ol></div>
        </div>
        ${highlights(m)}
      </div>`;
  }
  // Lo mejor del partido según la simulación: las jugadas que pagan, lo más
  // seguro y el total de remates más parejo. El detalle completo, en el partido.
  function highlights(m) {
    const items = [];
    const pl = (id) => m.players.find(x => x.player_id === id);
    const mp = (state.data.picks || []).filter(k => k.event_id === m.event_id).sort((x, y) => (y.edge || 0) - (x.edge || 0));
    mp.slice(0, 2).forEach(k => items.push({ kind: 'pick', p: pl(k.player_id), team: teamOf(m, k.team_id),
      title: `${surname(k.player)} · ${lineLabel(k.stat, k.line)}`,
      sub: `${Math.round(k.p_model * 10)}/10 · jugala desde ${odd(k.min_odds)}`, book: k.best_book, price: k.best_price, ok: k.best_price >= k.min_odds }));
    const sure = [];
    m.players.forEach(p => p.lines.forEach(l => { if (l.p_model >= 0.78 && l.p_model <= 0.95) sure.push({ p, l }); }));
    sure.sort((x, y) => (y.l.line - x.l.line) || (y.l.p_model - x.l.p_model));
    sure.filter(x => !mp.some(k => k.player_id === x.p.player_id && k.stat === x.l.stat && k.line === x.l.line)).slice(0, 2)
      .forEach(({ p, l }) => items.push({ kind: 'sure', p, team: teamOf(m, p.team_id), title: `${surname(p.player)} · ${lineLabel(l.stat, l.line)}`,
        sub: `${Math.round(l.p_model * 10)}/10 · lo más firme del partido`, book: l.best_book, price: l.best_price, ok: l.best_price != null && l.best_price >= l.min_odds }));
    const tl = [];
    ['home', 'away'].forEach(sd => Object.entries(m.teams[sd].shots_over || {}).forEach(([ln, pr]) => tl.push({ t: m.teams[sd], ln, pr })));
    const even = tl.sort((x, y) => Math.abs(x.pr - 0.6) - Math.abs(y.pr - 0.6))[0];
    if (even) items.push({ kind: 'team', team: even.t, title: `${even.t.name} · más de ${String(even.ln).replace('.', ',')} remates`, sub: `${Math.round(even.pr * 100)} de cada 100 partidos` });
    if (!items.length) return '';
    const face = (it) => it.kind === 'team'
      ? `<span class="fb-hl-face is-team"><img src="${logo(it.team.team_id)}" alt="" onerror="this.remove()"></span>`
      : `<span class="fb-hl-face">${it.p && it.p.photo ? `<img src="${esc(it.p.photo)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<i>${esc(initials(it.p ? it.p.player : it.title))}</i>
          ${it.team ? `<img class="fb-hl-crest" src="${logo(it.team.team_id)}" alt="" onerror="this.remove()">` : ''}</span>`;
    return `
      <div class="fb-hl">
        <div class="fb-hl-head"><b>Destacados</b><small>lo mejor de la simulación · todo en "Ver partido completo"</small></div>
        ${items.map(it => `
          <div class="fb-hl-item fb-hl-item--${it.kind}">
            ${face(it)}
            <span class="fb-hl-txt"><b>${esc(it.title)}</b><small>${esc(it.sub)}</small></span>
            ${it.price != null && it.book ? `<span class="fb-hl-odd${it.ok ? ' is-ok' : ''}" title="Mejor cuota: ${esc(bookName(it.book))}">${bookBadge(it.book, 'is-sm')}<b>${odd(it.price)}</b></span>` : ''}
          </div>`).join('')}
      </div>`;
  }
  function fxPanel(m) {
    return `
      <div class="fb-fxp">
        <div class="fb-fxp-top">
          ${simControl(m)}
          <button class="fb-fxp-open" data-ev="${esc(m.event_id)}">Ver partido completo ${I.back}</button>
        </div>
        ${m.computed_at ? `<div class="fb-simage">${I.clock}Simulado ${esc(ago(m.computed_at))}${m.lineup_confirmed ? ' · con formación confirmada' : ' · con formaciones probables'}</div>` : ''}
        <div class="fb-fxp-grid">
          ${simPreview(m)}
          <div class="fb-fxp-xi">${pitch(m)}</div>
        </div>
      </div>`;
  }

  // ── Partidos: tablero de duelos por día ─────────────────────────────────
  function fixture(m) {
    const h = m.teams.home, a = m.teams.away;
    const u = untilTxt(m.date);
    const share = h.shots / Math.max(0.1, h.shots + a.shots);
    const np = (state.data.picks || []).filter(k => k.event_id === m.event_id).length;
    const fav = share >= 0.5 ? 'home' : 'away';
    return `
      <div class="fb-fx-wrap lg-${m.league}${state.fxOpen.has(m.event_id) ? ' is-open' : ''}">
      <div class="fb-fx lg-${m.league}${simState(m.event_id) ? ' is-sim' : ''}" data-fx="${esc(m.event_id)}" role="button" tabindex="0" aria-expanded="${state.fxOpen.has(m.event_id)}">
        <span class="fb-fx-time">
          <b>${hhmm(m.date)}</b>
          ${u.live || u.soon ? `<small class="${u.live ? 'is-live' : 'is-soon'}">${u.live ? 'En juego' : esc(u.t.replace('Empieza en ', 'en '))}</small>` : ''}
          <img class="fb-fx-lg" src="${leagueOf(m.league).logo}" alt="${esc(leagueOf(m.league).short)}" title="${esc(leagueOf(m.league).name)}">
        </span>
        <span class="fb-fx-teams">
          <span class="fb-fx-row${fav === 'home' ? ' is-fav' : ''}"><img src="${logo(h.team_id)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"><b>${esc(h.name)}</b><em>${num1(h.shots)}</em></span>
          <span class="fb-fx-row${fav === 'away' ? ' is-fav' : ''}"><img src="${logo(a.team_id)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"><b>${esc(a.name)}</b><em>${num1(a.shots)}</em></span>
        </span>
        <span class="fb-fx-bar" aria-hidden="true"><i style="height:${Math.round(share * 100)}%"></i></span>
        <span class="fb-fx-tags">
          ${luPending(m) ? `<span class="fb-lu fb-lu--new" title="Salió la alineación oficial: tocá Simular">${I.check}XI nuevo</span>` : m.lineup_confirmed ? `<span class="fb-lu fb-lu--ok">${I.check}XI</span>` : '<span class="fb-lu">Est.</span>'}
          ${np ? `<span class="fb-fx-np"><b>${np}</b> jugada${np > 1 ? 's' : ''}</span>` : ''}
          ${starBtn(m.event_id)}
        </span>
        ${simState(m.event_id) === 'running' ? `<span class="fb-fx-simbar" data-simprog="${esc(m.event_id)}"><i style="width:0%"></i></span>` : ''}
      </div>
      ${state.fxOpen.has(m.event_id) ? fxPanel(m) : ''}
      </div>`;
  }
  function matchesView() {
    const list = matches().filter(m => state.league === 'all' || m.league === state.league)
      .filter(m => !untilTxt(m.date).past);
    const days = new Map();
    list.forEach(m => { const k = dayKey(m.date); if (!days.has(k)) days.set(k, []); days.get(k).push(m); });
    return `
      ${state.league === 'arg' ? `<div class="fb-lpf-hero">${lpfLockup('is-lg')}<span class="fb-lpf-tag">Torneo Clausura 2026</span></div>` : ''}
      <header class="fb-sec-head">
        <h2>Partidos de la <em>fecha</em></h2>
        <p>Remates esperados de cada equipo. La barra muestra quién va a rematar más.</p>
      </header>
      ${leagueChips()}
      ${[...days.values()].map(ms => `
        <section class="fb-day">
          <div class="fb-day-head"><b>${dayLabel(ms[0].date)}</b><small>${ms.length} partido${ms.length > 1 ? 's' : ''} · remates esperados</small></div>
          <div class="fb-fx-list">${ms.map(fixture).join('')}</div>
        </section>`).join('') || '<div class="fb-empty">No hay partidos en esta liga en los próximos días.</div>'}`;
  }

  // ── Resultados: récord grande + lista ───────────────────────────────────
  function resultsView() {
    const R = state.results;
    const sm = R && R.summary;
    const head = !sm || !sm.settled ? `
        <div class="fb-rec-hero is-empty">
          <b>—</b>
          <p>${sm && sm.open ? `${sm.open} jugada${sm.open > 1 ? 's' : ''} esperando su partido.` : 'Todavía no hay jugadas registradas.'} El récord se arma solo: cuando termina cada partido, ESPN dice cuántos remates hizo cada jugador.</p>
        </div>` : `
        <div class="fb-rec-hero">
          <div class="fb-rec-wl"><b class="w">${sm.won}</b><i>–</i><b class="l">${sm.lost}</b></div>
          <div class="fb-rec-facts">
            <div><small>Unidades</small><b class="${sm.units >= 0 ? 'up' : 'down'}">${units(sm.units)}</b><span>apostando 1 u por jugada</span></div>
            <div><small>El modelo esperaba</small><b>${String(sm.expected_won).replace('.', ',')}</b><span>ganadas de ${sm.settled}</span></div>
            ${sm.clv != null && sm.clv_n >= 5 ? `<div><small>Precio vs cierre</small><b class="${sm.clv >= 0 ? 'up' : 'down'}">${sm.clv >= 0 ? '+' : ''}${(sm.clv * 100).toFixed(1).replace('.', ',')} %</b><span>si es positivo, el modelo encuentra valor</span></div>` : ''}
          </div>
        </div>`;
    return `
      <header class="fb-sec-head"><h2>Cómo <em>vamos</em></h2></header>
      ${head}
      ${leagueChips()}
      ${resultsBlock() || (sm && sm.settled ? '<div class="fb-empty">Nada liquidado en esta liga todavía.</div>' : '')}`;
  }

  // ── Partido: el duelo ───────────────────────────────────────────────────
  function marketNote(m) {
    const mk = m.market;
    if (!mk || !mk.active) return '';
    const parts = ['home', 'away'].map((side, i) => {
      const d = mk.shot_mult[i] - 1;
      return Math.abs(d) >= 0.02 ? `${esc(m.teams[side].name)} ${d > 0 ? '+' : '−'}${Math.round(Math.abs(d) * 100)} %` : '';
    }).filter(Boolean);
    return parts.length ? `<span class="fb-flag" title="Ajuste por el 1X2 y los goles del partido">Mercado del partido: ${parts.join(' · ')} remates</span>` : '';
  }
  function duel(m) {
    const h = m.teams.home, a = m.teams.away;
    const share = h.shots / Math.max(0.1, h.shots + a.shots);
    const sshare = h.sot / Math.max(0.1, h.sot + a.sot);
    const team = (t, side) => `
      <div class="fb-duel-team fb-duel-team--${side}">
        <img src="${logo(t.team_id)}" alt="" onerror="this.style.visibility='hidden'">
        <b class="fb-duel-full">${esc(t.name)}</b><b class="fb-duel-abbr">${esc(t.abbr)}</b>
        <small>${side === 'home' ? 'Local' : 'Visitante'}</small>
      </div>`;
    return `
      <section class="fb-duel">
        ${team(h, 'home')}
        <div class="fb-duel-mid">
          <small>Remates esperados</small>
          <div class="fb-duel-nums"><b>${num1(h.shots)}</b><i></i><b>${num1(a.shots)}</b></div>
          <div class="fb-duel-bar" aria-hidden="true"><span style="width:${(share * 100).toFixed(1)}%"></span></div>
          <div class="fb-duel-range"><span>${h.shots_range[0]}–${h.shots_range[1]}</span><span>8 de cada 10 partidos</span><span>${a.shots_range[0]}–${a.shots_range[1]}</span></div>
          <small class="fb-duel-k2">Al arco</small>
          <div class="fb-duel-nums fb-duel-nums--sm"><b>${num1(h.sot)}</b><i></i><b>${num1(a.sot)}</b></div>
          <div class="fb-duel-bar fb-duel-bar--sm" aria-hidden="true"><span style="width:${(sshare * 100).toFixed(1)}%"></span></div>
        </div>
        ${team(a, 'away')}
      </section>`;
  }

  // ── Quién remata: ranking simple que se despliega ───────────────────────
  const LADDER = [
    { stat: 'shots', line: 0.5 }, { stat: 'shots', line: 1.5 }, { stat: 'shots', line: 2.5 },
    { stat: 'sot', line: 0.5 }, { stat: 'sot', line: 1.5 },
  ];
  function shortLine(stat, line) {
    const n = Math.round(line + 0.5);
    return stat === 'shots' ? `${n}+ rem.` : `${n}+ al arco`;
  }
  function wsRow(p, m, max, pickKeys) {
    const team = teamOf(m, p.team_id);
    const open = state.open.has(p.player_id);
    const l1 = lineOf(p, 'shots', 0.5);
    const hasPick = pickKeys.has(p.player_id);
    const rung = (c) => {
      const l = lineOf(p, c.stat, c.line);
      if (!l) return `<div class="fb-rung is-na"><small>${shortLine(c.stat, c.line)}</small><b>—</b></div>`;
      const n10 = Math.round(l.p_model * 10);
      const best = l.best_price != null ? `<span class="${pays(l) ? 'ok' : ''}">${pays(l) ? I.check : ''}${bookBadge(l.best_book, 'is-sm')} ${odd(l.best_price)}</span>` : '<span class="na">sin cuota</span>';
      return `
        <div class="fb-rung${pays(l) ? ' is-pays' : ''}">
          <small>${shortLine(c.stat, c.line)}</small>
          <b>${n10}<i>/10</i></b>
          <em>desde ${odd(l.min_odds)}</em>
          ${best}
        </div>`;
    };
    return `
      <div class="fb-ws${open ? ' is-open' : ''}${hasPick ? ' has-pick' : ''}">
        <button class="fb-ws-row" data-wsp="${esc(p.player_id)}" aria-expanded="${open}">
          <span class="fb-ws-face">${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<i>${esc(initials(p.player))}</i></span>
          <span class="fb-ws-id">
            <b>${esc(p.player)}${hasPick ? `<em class="fb-ws-pk">jugada</em>` : ''}</b>
            <small><img src="${logo(team.team_id)}" alt="" onerror="this.remove()">${esc(roleName(p))} · ${p.exp_minutes}'</small>
          </span>
          <span class="fb-ws-bar"><i style="width:${Math.max(4, (p.shots / max) * 100).toFixed(0)}%"></i></span>
          <span class="fb-ws-num"><b>${num1(p.shots)}</b><small>${l1 ? `${Math.round(l1.p_model * 10)}/10 rem.` : ''}</small></span>
        </button>
        ${open ? `<div class="fb-ws-more">${LADDER.map(rung).join('')}</div>` : ''}
      </div>`;
  }
  function whoShoots(m) {
    const h = m.teams.home, a = m.teams.away;
    const all = m.players.filter(p => state.team === 'all' || p.team_id === state.team);
    const list = state.wsAll ? all : all.slice(0, 12);
    const max = Math.max(0.5, ...all.map(p => p.shots));
    const pickKeys = new Set((state.data.picks || []).filter(k => k.event_id === m.event_id).map(k => k.player_id));
    const seg = `
      <div class="fb-seg">
        <button data-tm="all" class="${state.team === 'all' ? 'is-on' : ''}">Todos</button>
        <button data-tm="${esc(h.team_id)}" class="${state.team === h.team_id ? 'is-on' : ''}">${esc(h.abbr)}</button>
        <button data-tm="${esc(a.team_id)}" class="${state.team === a.team_id ? 'is-on' : ''}">${esc(a.abbr)}</button>
      </div>`;
    if (state.mode === 'full') {
      return `
        <section class="fb-block">
          <div class="fb-block-head"><h3>Jugadores</h3>${seg}</div>
          <p class="fb-note">Tocá una probabilidad para ver su cuota justa, la <b>cuota mínima</b> y lo que paga cada casa. En verde: la casa paga la mínima.</p>
          <div class="fb-cards">${all.map(p => card(p, m)).join('')}</div>
        </section>`;
    }
    return `
      <section class="fb-block fb-wsb">
        <div class="fb-block-head"><h3>Top <em>rematadores</em></h3>${seg}</div>
        <p class="fb-note">Ordenados por remates esperados. Tocá un jugador para ver cada línea: cuántas veces de cada 10 pasa y <b>desde qué cuota</b> conviene.</p>
        <div class="fb-ws-list">${list.map(p => wsRow(p, m, max, pickKeys)).join('')}</div>
        ${all.length > list.length || state.wsAll ? `<button class="fb-more" data-wsall="1">${state.wsAll ? 'Ver menos' : `Ver los ${all.length}`}</button>` : ''}
      </section>`;
  }

  // ── Alineación: un equipo por vez, cancha vertical atacando hacia arriba ─
  // Orden dentro de una línea, de izquierda a derecha del propio equipo.
  function lateral(pos) {
    const a = String(pos || '').toUpperCase();
    if (/-L$/.test(a)) return -1;
    if (/-R$/.test(a)) return 1;
    if (a.startsWith('L')) return -2;
    if (a.startsWith('R')) return 2;
    return 0;
  }
  // x: 0 izquierda … 100 derecha · y: 0 arriba (ataque) … 100 abajo (arco propio)
  function placeXI(xi) {
    const lines = [...new Set(xi.players.map(p => p.line))].sort((a, b) => a - b);
    const outfield = lines.filter(l => l !== 0);
    const out = [];
    lines.forEach(ln => {
      const row = xi.players.filter(p => p.line === ln)
        .sort((a, b) => lateral(a.pos) - lateral(b.pos) || (a.place || 0) - (b.place || 0));
      const i = outfield.indexOf(ln);
      const y = ln === 0 ? 88 : 70 - i * (56 / Math.max(1, outfield.length - 1));
      row.forEach((p, j) => out.push({ p, x: 8 + ((j + 1) / (row.length + 1)) * 84, y, n: row.length }));
    });
    return out;
  }
  function xiDot({ p, x, y, n }, m, confirmed) {
    const mp = m.players.find(q => q.player_id === p.player_id);
    const shots = p.line === 0 ? 'AR' : mp ? num1(mp.shots) : '–';
    const ps = Math.round((p.p_start || 0) * 100);
    return `
      <div class="fb-xd${p.doubt ? ' is-doubt' : ''}${mp && mp.shots >= 1.5 ? ' is-hot' : ''}${n >= 5 ? ' is-tight' : ''}" style="left:${x.toFixed(1)}%;top:${y.toFixed(1)}%"
           title="${esc(p.player)} · ${esc(p.pos || '')} · ${shots} remates esperados${confirmed ? '' : ` · titular ${ps}%`}">
        <span class="fb-xd-n">${shots}</span>
        <b>${esc(surname(p.player))}</b>
        ${!confirmed && ps < 90 ? `<small>${ps}%</small>` : ''}
      </div>`;
  }
  // Cancha dibujada en el mismo sistema que los jugadores (0–100 de ancho,
  // 0–130 de alto). En la Premier va en perspectiva: más angosta arriba, y a
  // los jugadores se los acomoda con la misma cuenta, así nunca quedan afuera.
  // margen lateral arriba (perspectiva): más suave en el teléfono para que los nombres entren
  const pTop = () => (window.innerWidth < 769 ? 8 : 18);
  const pxy = (x, y, tilt) => {                      // x 0–100 dentro de la cancha, y 0–130
    const P = pTop(), t = y / 130, l = tilt ? P - (P - 2) * t : 2, r = tilt ? 100 - l : 98;
    return [(l + (x / 100) * (r - l)).toFixed(2), y.toFixed(2)];
  };
  function pitchLines(tilt) {
    const pt = (x, y) => pxy(x, y, tilt).join(' ');
    const box = (x0, x1, y0, y1) => `M${pt(x0, y0)}L${pt(x0, y1)}L${pt(x1, y1)}L${pt(x1, y0)}`;
    const arc = (y, up) => { const [ax, ay] = pxy(38, y, tilt), [bx] = pxy(62, y, tilt); return `M${ax} ${ay}A${((bx - ax) / 2).toFixed(2)} 8 0 0 ${up ? 1 : 0} ${bx} ${ay}`; };
    return `
          <svg class="fb-xp-lines" viewBox="0 0 100 130" preserveAspectRatio="none" aria-hidden="true">
            <path d="M${pt(0, 3)}L${pt(100, 3)}L${pt(100, 127)}L${pt(0, 127)}Z" />
            <path d="${box(29, 71, 3, 17)}" /><path d="${box(40, 60, 3, 8)}" />
            <path d="${box(29, 71, 127, 113)}" /><path d="${box(40, 60, 127, 122)}" />
            <path d="${arc(17, false)}" /><path d="${arc(113, true)}" />
          </svg>`;
  }
  // Un jugador ubicado sobre la cancha rectangular → sobre la cancha en perspectiva.
  function persp(d) {
    const y130 = (d.y / 100) * 130;
    const xin = ((d.x - 2) / 96) * 100;
    const [x] = pxy(xin, y130, true);
    return { ...d, x: Number(x) };
  }

  function pitch(m) {
    const lu = m.lineup || {};
    if (!lu.home && !lu.away) return '';
    const want = state.xiSides[m.event_id] || 'home';
    const side = lu[want] ? want : (lu.home ? 'home' : 'away');
    const xi = lu[side];
    const conf = !!(m.lineup_confirmed || m.lineup_official);
    const tab = (sd) => {
      const t = m.teams[sd], x = lu[sd];
      return `<button class="fb-xt${sd === side ? ' is-on' : ''}" data-xi="${sd}" data-xiev="${esc(m.event_id)}" aria-pressed="${sd === side}">
        <img src="${logo(t.team_id)}" alt="" onerror="this.style.visibility='hidden'"><span><b>${esc(t.name)}</b><small>${esc((x && x.formation) || '—')}</small></span></button>`;
    };
    const doubts = xi.players.filter(p => p.doubt);
    return `
      <section class="fb-block fb-xi2">
        <div class="fb-block-head">
          <h3>Alinea<em>ción</em></h3>
          ${conf ? `<span class="fb-flag fb-flag--ok">${I.check}Confirmada</span>`
                 : `<span class="fb-flag fb-flag--lu">${I.clock}Probable · la oficial sale 1 h antes</span>`}
        </div>
        <div class="fb-xt-row">${tab('home')}${tab('away')}</div>
        <div class="fb-xp">
          ${pitchLines(m.league === 'eng')}
          <span class="fb-xp-form">${esc(xi.formation || '')}</span>
          ${placeXI(xi).map(d => xiDot(m.league === 'eng' ? persp(d) : d, m, conf)).join('')}
        </div>
        <p class="fb-note">En cada círculo: <b>remates esperados</b>${conf ? '' : '. El % de abajo es su chance de ser titular; el borde punteado marca las dudas'}.
          ${doubts.length ? ` Dudas: ${doubts.map(p => `<b>${esc(surname(p.player))}</b>`).join(', ')}.` : ''}
          ${conf ? 'Formación oficial (ESPN).' : 'Armada con el esquema del último partido y quién jugó cada puesto en las últimas 5 fechas.'}</p>
      </section>`;
  }

  function matchView(m) {
    const a = m.teams.away, h = m.teams.home;
    const mpicks = (state.data.picks || []).filter(k => k.event_id === m.event_id);
    const d = when(m.date);
    const dateTxt = d.toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' });
    const so = (t) => Object.entries(t.shots_over || {}).map(([l, p]) => [Number(l), p]).sort((x, y) => x[0] - y[0]);
    const ao = (t) => Object.entries(t.sot_over || {}).map(([l, p]) => [Number(l), p]).sort((x, y) => x[0] - y[0]);
    const chips = (arr) => arr.map(([l, p]) => `<span><small>+${String(l).replace('.', ',')}</small>${Math.round(p * 10)}/10</span>`).join('');
    return `
      <div class="fb-mv-top">
        <button class="fb-back" data-ev="${esc(state.back)}">${I.back}${({ mine: 'Favoritos', picks: 'Jugadas', results: 'Resultados' })[state.back] || 'Partidos'}</button>
        ${starBtn(m.event_id)}
        <span class="fb-kicker fb-kicker--lg"><img src="${leagueOf(m.league).logo}" alt="" onerror="this.remove()">${esc(leagueOf(m.league).name)} · ${esc(dateTxt)} · ${hhmm(m.date)}</span>
      </div>
      ${m.league === 'arg' ? `<div class="fb-lpf-band">${lpfLockup()}<span class="fb-lpf-sep"></span><b>Torneo Clausura 2026</b><em>${esc(dayLabel(m.date))} · ${hhmm(m.date)}</em></div>` : ''}
      ${duel(m)}
      ${simControl(m, true)}
      ${m.computed_at ? `<div class="fb-simage">${I.clock}Simulado ${esc(ago(m.computed_at))}${m.lineup_confirmed ? ' · con formación confirmada' : ' · con formaciones probables'}</div>` : ''}
      <div class="fb-match-flags">
        ${luPending(m)
          ? `<span class="fb-flag fb-flag--new fb-flag--lu">${I.check}Salió la alineación oficial · tocá <b>Simular partido</b> para recalcular con ella</span>`
          : m.lineup_confirmed
          ? `<span class="fb-flag fb-flag--ok fb-flag--lu">${I.check}Alineación oficial confirmada</span>`
          : `<span class="fb-flag fb-flag--lu">${I.clock}Alineación probable · la oficial sale 1 hora antes del partido</span>`}
        ${marketNote(m)}
        ${m.oddspapi_fixture ? '' : '<span class="fb-flag fb-flag--warn">Sin cuotas de las casas: guiate por "jugala desde"</span>'}
      </div>
      ${mpicks.length ? `
      <section class="fb-block">
        <div class="fb-block-head"><h3>Jugadas del <em>partido</em></h3><span class="fb-block-sub">${mpicks.length}</span></div>
        <div class="fb-ag-list">${mpicks.slice().sort((x, y) => y.p_model - x.p_model).map(ticket).join('')}</div>
      </section>` : ''}
      ${whoShoots(m)}
      ${pitch(m)}
      <details class="fb-block fb-totals">
        <summary><h3>Totales de cada equipo</h3><span>más de N remates · de cada 10 partidos</span></summary>
        ${[h, a].map(t => `
          <div class="fb-tot">
            <b><img src="${logo(t.team_id)}" alt="" onerror="this.remove()">${esc(t.name)}</b>
            <div class="fb-tl"><em>Remates</em>${chips(so(t))}</div>
            <div class="fb-tl"><em>Al arco</em>${chips(ao(t))}</div>
          </div>`).join('')}
      </details>`;
  }

  // ── Render principal ──────────────────────────────────────────────────────
  function render() {
    renderRail();
    renderTopTools();
    const sec = document.getElementById('section-futbol');
    if (!sec) return;
    if (!state.data) {
      sec.innerHTML = state.error
        ? `<div class="fb-state fb-state--err"><b>No se pudo cargar la jornada</b><span>${esc(state.error)}</span><button class="fb-btn" data-act="reload">Reintentar</button></div>`
        : `<div class="fb-state"><span class="fb-spin"></span><b>Cargando jornada…</b></div>`;
      return;
    }
    const m = isSection(state.view) ? null : matchById(state.view);
    // Tema de liga: el partido abierto, el último desplegado en la lista, o la liga filtrada.
    // Tema: el partido abierto; si no, la liga elegida; si no, el partido desplegado en la lista visible.
    const opened = !m && state.league === 'all' && ['mine', 'matches'].includes(state.view)
      && state.lastOpen && state.fxOpen.has(state.lastOpen) ? matchById(state.lastOpen) : null;
    const theme = m ? m.league : state.league !== 'all' ? state.league : opened ? opened.league : null;
    ['eng', 'arg', 'bra'].forEach(id => document.body.classList.toggle(`lg-${id}`, theme === id));
    const view = m ? matchView(m)
      : state.view === 'mine' ? mineView()
      : state.view === 'matches' ? matchesView()
      : state.view === 'results' ? resultsView()
      : (state.mode === 'simple' ? simpleView() : picksView());
    const showMode = m || state.view === 'picks';
    sec.innerHTML = `
      <div class="fb-top">${navTop()}${showMode ? modeSwitch() : ''}</div>
      ${state.loading ? `<div class="fb-busy"><span class="fb-spin"></span>Recalculando la jornada (ESPN + 20.000 simulaciones por partido)… puede tardar un par de minutos.</div>` : ''}
      ${state.error ? `<div class="fb-busy fb-busy--err">${esc(state.error)}</div>` : ''}
      <div class="fb-view fb-view--${m ? 'match' : state.view}">${view}</div>
      `;
    // Boleta y barra de abajo viven en <body>: un padre con transform rompe position:fixed.
    let host = document.getElementById('fb-slip-host');
    if (!host) {
      host = Object.assign(document.createElement('div'), { id: 'fb-slip-host' });
      host.addEventListener('click', onClick);
      document.body.appendChild(host);
    }
    host.innerHTML = slipHtml() + navBottom();
  }

  function go(view) {
    if (view !== state.view) { state.team = 'all'; state.wsAll = false; }
    if (isSection(state.view) && !isSection(view)) state.back = state.view;
    state.view = view;
    render();
    const main = document.querySelector('.app-main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const chip = document.querySelector(`.fb-mstrip-list [data-ev="${CSS.escape(view)}"]`);
    if (chip) chip.scrollIntoView({ inline: 'center', block: 'nearest' });
  }

  function onClick(e) {
    const t = e.target;
    if (t.closest('[data-menu]')) { state.menuOpen = !state.menuOpen; state.creditsOpen = false; renderTopTools(); return; }
    if (t.closest('[data-credits-refresh]')) { loadUsage(true); return; }
    if (t.closest('[data-credits]')) { state.creditsOpen = !state.creditsOpen; state.menuOpen = false; renderTopTools(); if (state.creditsOpen) loadUsage(); return; }
    const ma = t.closest('.fb-menu [data-act]');
    if (ma) {
      state.menuOpen = false; renderTopTools();
      if (ma.dataset.act === 'sync') { state.ver = -1; pollStatus(); loadPrefs(false); toast(`${I.swap}<span><b>Sincronizando</b>Trayendo lo último del servidor</span>`); }
      else onTool(ma.dataset.act);
      return;
    }
    const md = t.closest('[data-mode]');
    if (md) { state.mode = md.dataset.mode; store.set('fb-mode', state.mode); render(); return; }
    const sl = t.closest('[data-slip]');
    if (sl) {
      const k = sl.dataset.slip;
      if (state.slip.has(k)) state.slip.delete(k); else state.slip.add(k);
      store.set('fb-slip', [...state.slip]); savePrefs();
      render();
      return;
    }
    const sa = t.closest('[data-slipact]');
    if (sa) {
      const a = sa.dataset.slipact;
      if (a === 'toggle') state.slipOpen = !state.slipOpen;
      if (a === 'clear') { state.slip.clear(); store.set('fb-slip', []); state.slipOpen = false; savePrefs(); }
      if (a === 'copy') {
        const txt = slipText();
        (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject())
          .then(() => toast(`${I.check}<span><b>Boleta copiada</b>Pegala donde quieras</span>`))
          .catch(() => window.prompt('Copiá la boleta:', txt));
        return;
      }
      render();
      return;
    }
    const fv = t.closest('[data-fav]');
    if (fv) { const id = fv.dataset.fav; if (state.fav.has(id)) state.fav.delete(id); else state.fav.add(id); store.set('fb-fav', [...state.fav]); savePrefs(); render(); return; }
    const bl = t.closest('[data-balls]');
    if (bl) { const k = bl.dataset.balls; state.ballOpen = state.ballOpen === k ? null : k; render(); return; }
    const fx = t.closest('[data-fx]');
    if (fx && !t.closest('[data-fav]')) { const id = fx.dataset.fx; if (state.fxOpen.has(id)) { state.fxOpen.delete(id); if (state.lastOpen === id) state.lastOpen = [...state.fxOpen].pop() || null; } else { state.fxOpen.add(id); state.lastOpen = id; } render(); return; }
    const xs = t.closest('[data-xi]');
    if (xs) { state.xiSides[xs.dataset.xiev] = xs.dataset.xi; render(); return; }
    const sim = t.closest('[data-sim]');
    if (sim) { simulateMatch(sim.dataset.sim); return; }
    if (t.closest('[data-subtoggle]')) { state.subOn = !state.subOn; store.set('fb-sub', state.subOn); render(); return; }
    const wsp = t.closest('[data-wsp]');
    if (wsp) { const id = wsp.dataset.wsp; if (state.open.has(id)) state.open.delete(id); else state.open.add(id); render(); return; }
    if (t.closest('[data-wsall]')) { state.wsAll = !state.wsAll; render(); return; }
    const cell = t.closest('.fb-cell[data-k]');
    if (cell) { state.sel[cell.dataset.pl] = cell.dataset.k; render(); return; }
    const act = t.closest('[data-act]');
    if (act) {
      if (act.dataset.act === 'reload') load(); else onTool(act.dataset.act);
      return;
    }
    const lg = t.closest('[data-lg]');
    if (lg) { state.league = lg.dataset.lg; render(); return; }
    const tm = t.closest('[data-tm]');
    if (tm) { state.team = tm.dataset.tm; render(); return; }
    if (t.closest('details, summary')) return;
    const ev = t.closest('[data-ev]');
    if (ev) go(ev.dataset.ev);
  }

  let wired = false;
  function wire() {
    if (wired) return;
    wired = true;
    const sec = document.getElementById('section-futbol');
    const rail = document.getElementById('fb-rail');
    if (sec) sec.addEventListener('click', onClick);
    if (rail) rail.addEventListener('click', onClick);
    const top = document.getElementById('fb-topbar-tools');
    if (top) top.addEventListener('click', onClick);
    // Cerrar el menú tocando afuera o con Esc
    document.addEventListener('click', (e) => {
      if (state.menuOpen && !e.target.closest('.fb-menu-wrap')) { state.menuOpen = false; renderTopTools(); }
      if (state.creditsOpen && !e.target.closest('.fb-hud')) { state.creditsOpen = false; renderTopTools(); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && (state.menuOpen || state.creditsOpen)) { state.menuOpen = false; state.creditsOpen = false; renderTopTools(); } });
  }

  // ── Formaciones: chequeo cada 3 min mientras la pestaña está a la vista ──
  const LINEUP_POLL_MS = 3 * 60 * 1000;
  let pollTimer = null;
  function toast(html, evId) {
    let box = document.getElementById('fb-toasts');
    if (!box) { box = Object.assign(document.createElement('div'), { id: 'fb-toasts', className: 'fb-toasts' }); document.body.appendChild(box); }
    const t = Object.assign(document.createElement('div'), { className: 'fb-toast' + (evId ? ' is-link' : ''), innerHTML: html });
    if (evId) t.addEventListener('click', () => { if (document.body.dataset.sport === 'futbol') go(evId); t.remove(); });
    box.appendChild(t);
    setTimeout(() => t.classList.add('is-out'), evId ? 9000 : 7000);
    setTimeout(() => t.remove(), evId ? 9600 : 7600);
  }
  const futbolVisible = () => document.body.dataset.sport === 'futbol' && document.visibilityState === 'visible';
  function anySoon() {
    const now = Date.now();
    return (state.data?.matches || []).some(m => {
      if (m.lineup_confirmed || m.lineup_official) return false;
      const h = (new Date(m.date).getTime() - now) / 3600000;
      return h >= -0.5 && h <= 2;
    });
  }
  async function checkLineups() {
    if (!futbolVisible() || !state.data || state.loading || !anySoon()) return;
    try {
      const r = await fetch(`${apiBase()}/api/futbol/lineups/check`);
      if (!r.ok) return;
      const res = await r.json();
      if (!res.updated || !res.updated.length) return;
      state.data = await fetchJornada('');
      render();
      res.updated.forEach(u => toast(`${I.check}<span><b>Salió la alineación oficial</b>${esc(u.match)} · tocá Simular partido para recalcular <u>Ver partido</u></span>`, u.event_id));
    } catch (_) { /* sin red: se reintenta en el próximo ciclo */ }
  }
  // ── Sincronización entre dispositivos (PC ↔ celular) ─────────────────────
  // La jornada y las preferencias viven en el servidor. Cada 20 s (y al volver
  // a la app) se pregunta una versión liviana: si cambió, se recarga.
  async function savePrefs() {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/prefs`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fav: [...state.fav], slip: [...state.slip] }),
      });
      const d = await r.json();
      state.prefsVersion = d.version;
    } catch (_) { /* sin red: queda guardado en este dispositivo */ }
  }
  async function loadPrefs(merge) {
    try {
      const d = await (await fetch(`${apiBase()}/api/futbol/prefs`)).json();
      if (merge) {                                   // primera vez: junta lo de este dispositivo con lo del servidor
        const fav = new Set([...state.fav, ...d.fav]), slip = new Set([...state.slip, ...d.slip]);
        const changed = fav.size !== d.fav.length || slip.size !== d.slip.length;
        state.fav = fav; state.slip = slip;
        if (changed) await savePrefs(); else state.prefsVersion = d.version;
      } else {                                       // otro dispositivo cambió algo: manda el servidor
        state.fav = new Set(d.fav); state.slip = new Set(d.slip); state.prefsVersion = d.version;
      }
      store.set('fb-fav', [...state.fav]); store.set('fb-slip', [...state.slip]);
      if (!state.loading) render();
    } catch (_) { /* sin red */ }
  }
  async function pollStatus() {
    if (!futbolVisible() || !state.data || state.loading) return;
    let st;
    try { st = await (await fetch(`${apiBase()}/api/futbol/status`)).json(); } catch (_) { return; }
    // ¿Otro dispositivo está simulando?
    const remote = st.running && st.running !== state.sim.running ? st.running : null;
    if (remote !== state.sim.remote) {
      state.sim.remote = remote;
      state.sim.remoteT0 = performance.now() - Math.max(0, (st.now - (st.running_since || st.now)) * 1000);
      render();
      if (remote) requestAnimationFrame(simProgressLoop);
    }
    // ¿Cambió la jornada (simulación, formación, recálculo hecho en otro lado)?
    if (state.ver == null) state.ver = st.version;
    else if (st.version !== state.ver && !state.sim.running) {
      state.ver = st.version;
      try {
        state.data = await fetchJornada('');
        render();
        toast(`${I.refresh}<span><b>Datos actualizados</b>Hubo una simulación o un cambio desde otro dispositivo</span>`);
      } catch (_) { /* reintenta en el próximo ciclo */ }
    }
    if (st.prefs_version != null && st.prefs_version !== state.prefsVersion) loadPrefs(false);
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => { checkLineups(); if (futbolVisible()) loadResults(); }, LINEUP_POLL_MS);
    setInterval(pollStatus, 20000);
    loadUsage();
    setInterval(() => { if (futbolVisible()) loadUsage(); }, 120000);
    document.addEventListener('visibilitychange', () => { if (futbolVisible()) { checkLineups(); pollStatus(); } });
    loadPrefs(true);
  }

  // El tema de liga vive en <body>: se saca al ir a MLB / NBA.
  new MutationObserver(() => {
    if (document.body.dataset.sport !== 'futbol') ['eng', 'arg', 'bra'].forEach(id => document.body.classList.remove(`lg-${id}`));
  }).observe(document.body, { attributes: true, attributeFilter: ['data-sport'] });

  window.loadFutbol = function loadFutbol() {
    wire();
    startPolling();
    if (state.data || state.loading) { render(); return; }
    load();
  };
  window.FB_BALL_ICON = I.ball;
})();
