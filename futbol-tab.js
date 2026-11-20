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
  // Fotos de API-Football: se piden recortadas (sin el fondo blanco) al backend.
  const photoSrc = (u) => {
    const m = /media\.api-sports\.io\/football\/players\/(\d+)\.png/.exec(u || '');
    return m ? `${apiBase()}/api/futbol/photo/${m[1]}.png` : u;
  };

  // Nombres y logos oficiales (logos de ESPN, versión para fondo oscuro).
  const UNL_LOGO = 'assets/unl/logo.svg';
  const MEX_LOGO = 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/22.png';
  const MEX_LOGO_LT = 'https://a.espncdn.com/i/leaguelogos/soccer/500/22.png';   // para la placa blanca del banner
  const LEAGUES = [
    { id: 'arg', name: 'Liga Profesional de Fútbol', short: 'LPF', logo: 'assets/lpf/mark-2026.png' },
    { id: 'bra', name: 'Brasileirão Série A', short: 'Série A', logo: 'assets/bra/brasileirao-mark-sm.png' },
    { id: 'eng', name: 'Premier League', short: 'PL', logo: 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/23.png' },
    { id: 'ita', name: 'Serie A', short: 'Italia', logo: 'assets/ita/mark-seriea.png' },
    { id: 'esp', name: 'LaLiga EA Sports', short: 'LaLiga', logo: 'assets/esp/mark.svg' },
    { id: 'mex', name: 'Liga MX', short: 'Liga MX', logo: MEX_LOGO },
    { id: 'ucl', name: 'UEFA Champions League', short: 'Champions', logo: 'assets/ucl/starball.svg' },
    { id: 'uel', name: 'UEFA Europa League', short: 'Europa League', logo: 'assets/uel/mark.svg' },
    { id: 'cdb', name: 'Copa do Brasil', short: 'Copa do Brasil', logo: 'assets/cdb/trophy.png' },
    { id: 'unl', name: 'UEFA Nations League', short: 'Nations League', logo: UNL_LOGO },
  ];
  // Lockup de la LPF: escudo | LIGA / PROFESIONAL / DE FÚTBOL (como el oficial).
  const lpfLockup = (cls = '') => `<span class="fb-lpf-lockup ${cls}"><img src="assets/lpf/mark-2026.png" alt="LPF"><i></i><b>Liga<br>Profesional<br>de Fútbol</b></span>`;
  // Banners de cada liga (como las webs oficiales): grande arriba al filtrar, chico dentro del partido.
  const PL_LOGO = 'https://a.espncdn.com/i/leaguelogos/soccer/500-dark/23.png';
  const heroRef = (lg) => oddsRefreshBtn('lg', lg, 'Actualizar las cuotas de todos los partidos de la liga (1 pedido por partido)');
  // Fila de escudos de la Liga MX (los equipos de la jornada), como al pie de las placas de la liga.
  function mexCrests() {
    const seen = new Map();
    matches().filter(m => m.league === 'mex').forEach(m => ['home', 'away'].forEach(s => { const t = m.teams[s]; if (t && t.team_id && !seen.has(t.team_id)) seen.set(t.team_id, t.name); }));
    return seen.size ? `<span class="fb-mex-crests">${[...seen].map(([id, n]) => `<img src="${logo(id)}" alt="" title="${esc(n)}" loading="lazy" onerror="this.remove()">`).join('')}</span>` : '';
  }
  function leagueHero(lg) {
    if (lg === 'arg') return `<div class="fb-lpf-hero">${heroRef('arg')}${lpfLockup('is-lg')}<span class="fb-lpf-tag">Torneo Clausura 2026</span></div>`;
    if (lg === 'eng') return `<div class="fb-pl-hero">${heroRef('eng')}<img src="${PL_LOGO}" alt="Premier League"><b>Premier League</b></div>`;
    if (lg === 'ita') return `<div class="fb-ita-hero">${heroRef('ita')}<span class="fb-ita-tile"><img src="assets/ita/logo-seriea.png" alt="Serie A Enilive"></span><span class="fb-ita-txt"><em>Serie A Enilive</em><b>Serie A</b><small>Temporada 2026/27</small></span></div>`;
    if (lg === 'bra') return `<div class="fb-bra-hero">${heroRef('bra')}<span class="fb-bra-crest"><img src="assets/bra/brasileirao-mark.png?v=2" alt="Brasileirão"></span><span class="fb-bra-txt"><em>Série A</em><b class="fb-bra-word"><img src="assets/bra/brasileirao-word.png" alt="Brasileirão"></b></span></div>`;
    if (lg === 'ucl') return `<div class="fb-ucl-hero">${heroRef('ucl')}<img class="fb-ucl-logo" src="assets/ucl/logo-ucl.svg?v=2" width="441" height="419" alt="UEFA Champions League"><span class="fb-ucl-txt"><em>Fase liga · 2026/27</em><b>Champions League</b></span></div>`;
    if (lg === 'uel') return `<div class="fb-uel-hero">${heroRef('uel')}<img class="fb-uel-logo" src="assets/uel/logo-uel.svg?v=2" width="287" height="419" alt="UEFA Europa League"><span class="fb-uel-txt"><em>Fase liga · 2026/27</em><b>Europa League</b></span></div>`;
    if (lg === 'esp') return `<div class="fb-esp-hero">${heroRef('esp')}<img class="fb-esp-mark" src="assets/esp/mark.svg" width="84" height="77" alt=""><span class="fb-esp-txt"><em>Temporada 2026/27</em><b><img src="assets/esp/logotype-white.svg" width="135" height="32" alt="LALIGA"><i>EA Sports</i></b></span></div>`;
    if (lg === 'mex') return `<div class="fb-mex-hero">${heroRef('mex')}<img src="${MEX_LOGO_LT}" width="64" height="64" alt="Liga MX"><span class="fb-mex-txt"><em>Liga BBVA MX · <u>¡Nuevo!</u> Atajadas del arquero</em><b>Apertura 2026</b>${mexCrests()}</span></div>`;
    if (lg === 'all') {
      const n = matches().filter(m => !untilTxt(m.date, m.event_id).past).length;
      return `<div class="fb-all-hero"><img class="fb-all-mark" src="assets/cherry-logo.svg" width="120" height="120" alt=""><span class="fb-all-txt"><b>Cherry<em>Pick</em></b><small>Todas las ligas${n ? ` · ${n} partido${n > 1 ? 's' : ''}` : ''}</small></span></div>`;
    }
    if (lg === 'unl') return `<div class="fb-unl-hero">${heroRef('unl')}<img class="fb-unl-logo" src="${UNL_LOGO}" width="605" height="946" alt="UEFA Nations League"><span class="fb-unl-txt"><em>Selecciones · 2026/27</em><b>Nations League</b></span></div>`;
    if (lg === 'cdb') return `<div class="fb-cdb-hero">${heroRef('cdb')}<img class="fb-cdb-logo" src="assets/cdb/logo-stack.png" width="180" height="214" alt="Copa do Brasil"><span class="fb-cdb-txt"><em>Mata-mata · 2026</em><b>Copa do Brasil</b></span></div>`;
    return '';
  }
  function leagueBand(m) {
    const when = `<em>${esc(dayLabel(m.date))} · ${hhmm(m.date)}</em>`;
    if (m.league === 'arg') return `<div class="fb-lpf-band">${lpfLockup()}<span class="fb-lpf-sep"></span><b>Torneo Clausura 2026</b>${when}</div>`;
    if (m.league === 'eng') return `<div class="fb-pl-band"><img src="${PL_LOGO}" alt="Premier League"><b>Premier League</b><span>2026/27</span>${when}</div>`;
    if (m.league === 'ita') return `<div class="fb-ita-band"><span class="fb-ita-tile"><img src="assets/ita/logo-seriea.png" alt="Serie A Enilive"></span><span class="fb-ita-tag">Serie A Enilive</span><i>2026/27</i>${when}</div>`;
    if (m.league === 'uel') return `<div class="fb-uel-band"><img src="assets/uel/mark.svg" alt="UEFA Europa League"><b>Europa League</b><i>Fase liga</i>${when}</div>`;
    if (m.league === 'ucl') return `<div class="fb-ucl-band"><img src="assets/ucl/starball.svg" alt="UEFA Champions League"><b>Champions League</b><i>Fase liga</i>${when}</div>`;
    if (m.league === 'esp') return `<div class="fb-esp-band"><img src="assets/esp/mark.svg" width="84" height="77" alt=""><img class="fb-esp-band-word" src="assets/esp/logotype-white.svg" width="135" height="32" alt="LALIGA"><i>EA Sports · 2026/27</i>${when}</div>`;
    if (m.league === 'mex') return `<div class="fb-mex-band"><img src="${MEX_LOGO_LT}" width="30" height="30" alt="Liga MX"><b>Liga BBVA MX</b><i>Apertura 2026</i>${when}</div>`;
    if (m.league === 'unl') return `<div class="fb-unl-band"><img src="assets/unl/logo-small.svg" width="70" height="38" alt="UEFA Nations League"><i>Selecciones · 2026/27</i>${when}</div>`;
    if (m.league === 'cdb') return `<div class="fb-cdb-band"><img src="assets/cdb/logo-row.png" width="416" height="67" alt="Copa do Brasil"><i>Mata-mata</i>${when}</div>`;
    if (m.league === 'bra') return `<div class="fb-bra-band"><span class="fb-bra-crest"><img src="assets/bra/brasileirao-mark.png?v=2" alt="Brasileirão"></span><b>Campeonato Brasileiro</b><i>Série A</i>${when}</div>`;
    return '';
  }
  const leagueOf = (id) => LEAGUES.find(L => L.id === id) || { name: id, short: id, logo: '' };
  // Casas: las manda el backend desde futbol/books.json (ver fetchJornada);
  // esta lista es sólo el arranque antes de la primera respuesta.
  let BOOKS = [
    { id: 'bet365.bet.ar', name: 'bet365', logo: 'assets/books/bet365.png' },
    { id: 'betano', name: 'Betano', logo: 'assets/books/betano.png' },
    { id: 'betsson', name: 'Betsson' },
  ];
  const MAX_CHIPS = 4;     // en cada jugada: las casas con precio, de mejor a peor
  // Las líneas del cromo: remates 1+/2+/3+ y al arco 1+/2+.
  const CELLS = [
    { stat: 'shots', line: 0.5 }, { stat: 'shots', line: 1.5 }, { stat: 'shots', line: 2.5 },
    { stat: 'sot', line: 0.5 }, { stat: 'sot', line: 1.5 },
  ];
  const POS = { F: 'Delantero', M: 'Volante', D: 'Defensor', G: 'Arquero' };
  // Rol según el puesto real (ESPN): no todos los delanteros juegan igual.
  const ROLE = { ST: 'Centrodelantero', WG: 'Extremo', AM: 'Enganche', CM: 'Volante', FB: 'Lateral', CB: 'Central', GK: 'Arquero' };
  const roleName = (x) => ROLE[x.role] || POS[x.pos] || x.pos || '';
  // Badge corto del puesto (en el ticket, al lado del nombre), con color por línea.
  const ROLE_ABBR = { ST: 'DEL', WG: 'EXT', AM: 'ENG', CM: 'VOL', FB: 'LAT', CB: 'CEN', GK: 'ARQ' };
  // El 5 (mediocampista defensivo): el modelo lo trata como volante, el badge lo distingue.
  const POS_ABBR = { F: 'DEL', M: 'VOL', D: 'DEF', G: 'ARQ' };
  const posLine = (x) => ({ ST: 'F', WG: 'F', AM: 'M', CM: 'M', FB: 'D', CB: 'D', GK: 'G' })[x.role] || x.pos || '';
  // Rótulo de puesto: bloque del color de la línea con una mini cancha que
  // marca dónde juega (ataque arriba), y la sigla al lado.
  const POS_SPOT = { ST: [9, 2.6], WG: [3.6, 3.2], AM: [9, 4.6], CM: [9, 6.3], DM: [9, 7.9], FB: [3.6, 9], CB: [9, 9.4], GK: [9, 11.1] };
  const LINE_SPOT = { F: [9, 2.8], M: [9, 6.3], D: [9, 9.4], G: [9, 11.1] };
  const miniPitch = ([x, y]) => `<svg viewBox="0 0 18 13" aria-hidden="true"><rect x=".6" y=".6" width="16.8" height="11.8" rx="1.6" class="ln"/><path d="M.6 6.5h16.8M6 12.4V10.4h6v2M6 .6v2h6v-2" class="ln"/><circle cx="9" cy="6.5" r="1.7" class="ln"/><circle cx="${x}" cy="${y}" r="1.9" class="dot"/></svg>`;
  const posBadge = (x) => {
    const dm = x.dm && (x.role === 'CM' || (!x.role && x.pos === 'M'));
    const t = dm ? 'MCD' : (ROLE_ABBR[x.role] || POS_ABBR[x.pos]);
    if (!t) return '';
    const spot = dm ? POS_SPOT.DM : (POS_SPOT[x.role] || LINE_SPOT[posLine(x)] || [9, 6.3]);
    const line = dm ? 'DM' : posLine(x);
    return `<span class="fb-pos fb-pos--${esc(line)}" title="${esc(dm ? 'Mediocampista defensivo' : roleName(x))}"><i>${miniPitch(spot)}</i><em>${t}</em></span>`;
  };

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* modo privado */ } },
  };

  const state = {
    data: null, view: 'matches', league: 'all', team: 'all',
    fav: new Set(store.get('fb-fav', [])),   // partidos marcados con estrella
    xiSides: {},                             // partido → equipo que muestra la alineación
    photo: null,                              // Cargar alineación: {eid, busy} leyendo · {eid, draft, squad} revisando
    dtEdit: null, dtDraft: null, dtMenu: null, // revisión en curso (equipo a la vista, su borrador, menú abierto)
    fxOpen: new Set(),                       // partidos desplegados en las listas
    sim: { queue: [], running: null, t0: 0 }, // simulaciones en segundo plano
    sel: {},            // player_id → "stat|line" elegido en el cromo
    loading: false, error: null,
    mode: store.get('fb-mode', 'simple'),     // 'simple' | 'full'
    subOn: store.get('fb-sub', true),        // contar con Suplente+ / de Oro / Suplente
    slip: new Set(store.get('fb-slip', [])),  // claves de jugadas marcadas "La juego"
    slipOpen: false,
    slipExp: new Set(),                       // jugadas de la boleta con la barra abierta
    live: {},                                 // cómo va cada jugada de la boleta (ESPN en vivo)
    hlSharp: new Set(),                       // partidos con Destacados dado vuelta (jugadas sharp)
    hlParlay: new Set(),                      // partidos con Destacados en modo parlay
    hlBoost: new Set(),                       // partidos con el booster activado dentro de Parlay
    shotBusy: new Set(),                      // partidos con la foto armándose
    hlZap: {},                                // partido → cuándo empezó la descarga eléctrica (dura ZAP_MS)
    hlSpin: null,                             // partido que acaba de girar (anima una vez)
    results: null,      // /api/futbol/results: récord y picks liquidados
    open: new Set(),    // jugadores desplegados en "Quién remata"
    wsAll: false,       // mostrar todos los jugadores del partido
    back: 'matches',       // a dónde vuelve la flecha de un partido
  };

  // Tema de liga de la página: el partido abierto; si no, la liga elegida; si no,
  // el último partido desplegado en la lista visible (con "Todas").
  const THEMES = ['eng', 'arg', 'bra', 'ita', 'esp', 'mex', 'ucl', 'uel', 'cdb', 'unl'];
  function calcTheme() {
    const m = isSection(state.view) ? null : matchById(state.view);
    if (m) return m.league;
    if (state.league !== 'all') return state.league;
    const opened = ['recs', 'mine', 'matches'].includes(state.view)
      && state.lastOpen && state.fxOpen.has(state.lastOpen) ? matchById(state.lastOpen) : null;
    return opened ? opened.league : null;
  }

  // ── Formato ───────────────────────────────────────────────────────────────
  const plus = (line) => `${Math.round(line + 0.5)}+`;
  const statName = (s, n = 1) => (s === 'shots' ? (n === 1 ? 'remate' : 'remates') : 'al arco');
  const lineLabel = (stat, line) => {
    const n = Math.round(line + 0.5);
    return `+${n} ${n === 1 ? 'Remate' : 'Remates'}${stat === 'shots' ? '' : ' al arco'}`;
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
  const ITA_CRESTS = new Set(['ATA', 'BOL', 'CAG', 'COMO', 'FIO', 'FRO', 'GEN', 'INT', 'JUV', 'LAZ', 'LEC', 'MIL', 'MON', 'NAP', 'PAR', 'ROMA', 'SAS', 'TOR', 'UDI', 'VEN']);
  const crestById = {};          // equipo de la Serie A → escudo oficial (lo llena noteCrests)
  // Brasileirão: escudos oficiales del kit de la CBF (id de ESPN → sigla del archivo)
  const BRA_CRESTS = { 3458: 'CAP', 7632: 'CAM', 9967: 'BAH', 6086: 'BOT', 9318: 'CHA', 874: 'COR', 3456: 'CFC', 2022: 'CRU', 819: 'FLA', 3445: 'FLU',
    6273: 'GRE', 1936: 'INT', 9169: 'MIR', 2029: 'PAL', 6079: 'BRA', 4936: 'REMO', 2674: 'SAN', 2026: 'SAO', 3454: 'VAS', 3457: 'VIT' };
  Object.entries(BRA_CRESTS).forEach(([id, ab]) => { crestById[id] = `assets/bra/crests/${ab}.png`; });
  // LaLiga: escudos oficiales del sprite de laliga.com (id de ESPN → sigla del archivo)
  const ESP_CRESTS = { 96: 'ALA', 93: 'ATH', 1068: 'ATM', 83: 'BAR', 85: 'CEL', 90: 'DEP', 3751: 'ELC', 88: 'ESP', 2922: 'GET', 1538: 'LEV',
    99: 'MCF', 97: 'OSA', 87: 'RAC', 101: 'RAY', 244: 'BET', 86: 'RMA', 89: 'RSO', 243: 'SEV', 94: 'VAL', 102: 'VIL' };
  Object.entries(ESP_CRESTS).forEach(([id, ab]) => { crestById[id] = `assets/esp/crests/${ab}.png`; });
  function noteCrests(data) {
    (data && data.matches || []).forEach(m => {
      if (m.league !== 'ita') return;
      ['home', 'away'].forEach(sd => {
        const t = m.teams[sd];
        if (t && ITA_CRESTS.has(t.abbr)) crestById[t.team_id] = `assets/ita/crests/${t.abbr}.webp`;
      });
    });
    return data;
  }
  const logo = (id) => crestById[id] || `https://a.espncdn.com/i/teamlogos/soccer/500/${encodeURIComponent(id)}.png`;
  const bookName = (id) => (BOOKS.find(b => b.id === id) || { name: id }).name;
  // Logo de cada casa (recortes oficiales en assets/books; Betsson, en texto hasta tener su logo).
  const bookLogo = (id) => (BOOKS.find(b => b.id === id) || {}).logo;
  const bookBadge = (id, cls = '') => bookLogo(id)
    ? `<img class="fb-bk ${cls}" src="${bookLogo(id)}" alt="${esc(bookName(id))}" title="${esc(bookName(id))}">`
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
    refresh: ICON('refresh'),
    chart: ICON('chartLine'),
    coin: ICON('coin'),
    check: ICON('checkmark'),
    clock: ICON('clock'),
    starFill: ICON('starFill'),
    play: ICON('play'),
    spark: ICON('sparkles'),
    swap: ICON('swap'),
    tick: ICON('ticket'),
    pitch: ICON('pitch'),
    camera: ICON('camera'),
    tactics: ICON('tactics'),
    trophy: ICON('trophy'),
    back: ICON('chevronLeft'),
    star: ICON('star'),
  };

  // ── Datos ─────────────────────────────────────────────────────────────────
  async function fetchJornada(qs = '') {
    const r = await fetch(`${apiBase()}/api/futbol/jornada${qs}`);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* nada */ }
      throw new Error(msg);
    }
    const d = await r.json();
    if (d.odds && Array.isArray(d.odds.books) && d.odds.books.length) BOOKS = d.odds.books;
    return noteCrests(esNations(d));
  }
  // Selecciones: ESPN manda los nombres en inglés. Sólo para mostrar (el backend
  // sigue usando los de ESPN para cruzar con las casas).
  const NATIONS_ES = { Austria: 'Austria', Azerbaijan: 'Azerbaiyán', Belarus: 'Bielorrusia', Belgium: 'Bélgica', 'Bosnia-Herzegovina': 'Bosnia',
    Croatia: 'Croacia', Cyprus: 'Chipre', Czechia: 'Chequia', Denmark: 'Dinamarca', England: 'Inglaterra', 'Faroe Islands': 'Islas Feroe', Finland: 'Finlandia',
    France: 'Francia', Germany: 'Alemania', Greece: 'Grecia', Hungary: 'Hungría', Iceland: 'Islandia', Italy: 'Italia', Kazakhstan: 'Kazajistán',
    Latvia: 'Letonia', Liechtenstein: 'Liechten­stein', Lithuania: 'Lituania', Luxembourg: 'Luxemburgo', Moldova: 'Moldavia', Netherlands: 'Países Bajos', 'North Macedonia': 'Macedonia del Norte',
    'Northern Ireland': 'Irlanda del Norte', Norway: 'Noruega', Poland: 'Polonia', 'Republic of Ireland': 'Irlanda', Romania: 'Rumania', Scotland: 'Escocia',
    Slovakia: 'Eslovaquia', Slovenia: 'Eslovenia', Spain: 'España', Sweden: 'Suecia', Switzerland: 'Suiza', 'Türkiye': 'Turquía', Turkey: 'Turquía',
    Ukraine: 'Ucrania', Wales: 'Gales' };
  function esNations(d) {
    const es = (o, k) => { if (o && NATIONS_ES[o[k]]) o[k] = NATIONS_ES[o[k]]; };
    (d.matches || []).filter(m => m.league === 'unl').forEach(m => {
      es(m.teams && m.teams.home, 'name'); es(m.teams && m.teams.away, 'name');
      es(m.ev, 'home'); es(m.ev, 'away');
    });
    (d.picks || []).filter(k => k.league === 'unl').forEach(k => { es(k, 'team'); es(k, 'opponent'); es(k, 'home'); es(k, 'away'); });
    return d;
  }

  async function loadResults() {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/results?days=10`);
      if (!r.ok) return;
      const prev = state.results;
      const txt = await r.text();
      if (prev && txt === state.resultsTxt) return;   // nada nuevo: no tocar la página
      state.resultsTxt = txt;
      state.results = JSON.parse(txt);
      pruneSlip();
      // Aviso cuando se liquida algo mientras la pestaña está abierta.
      if (prev && state.results.settled_now) {
        const done = state.results.picks.filter(x => x.status !== 'open' && x.settled_at && Date.now() - new Date(x.settled_at).getTime() < 5 * 60000);
        done.slice(0, 3).forEach(x => toast(`${x.status === 'won' ? I.check : ''}<span><b>${x.status === 'won' ? 'Ganada' : x.status === 'lost' ? 'Perdida' : 'Nula'}</b>${esc(x.player)} · ${esc(lineLabel(x.stat, x.line))}</span>`));
      }
      if (!state.loading) bgRender();
    } catch (_) { /* sin red: queda lo anterior */ }
  }

  // Rendimiento: las jugadas por semana contra el modelo y contra toda la fecha.
  async function loadReport() {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/report?weeks=5`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      state.report = await r.json();
      state.reportErr = null;
    } catch (e) {
      if (!state.report) state.reportErr = 'No se pudo cargar el rendimiento.';
    }
    if (state.view === 'form' && !state.loading && !fmDrag) bgRender();
  }

  async function load(qs = '') {
    state.loading = true; state.error = null;
    render();
    try {
      state.data = await fetchJornada(qs);
      if (!isSection(state.view) && !matchById(state.view)) state.view = 'matches';
      const keys = new Set((state.data.picks || []).map(pickKey));
      pruneSlip(keys);
    } catch (e) {
      state.error = e.message || String(e);
    } finally {
      state.loading = false;
      render();
    }
    if (state.data) { setTimeout(checkLineups, 1500); loadResults(); }
    if (!qs && !followingRecalc) setTimeout(attachRecalc, 200);
  }
  // ¿Hay un recálculo corriendo en el servidor (p. ej. después de un F5)?
  async function attachRecalc() {
    if (followingRecalc) return;
    try {
      const st = await (await fetch(`${apiBase()}/api/futbol/status`)).json();
      if (st.job && st.job.kind) followRecalc(st.job);
    } catch (_) { /* sin red */ }
  }

  const matches = () => (state.data ? state.data.matches.slice().sort((a, b) => when(a.date) - when(b.date)) : []);
  const matchById = (id) => matches().find(m => m.event_id === id);

  // ── Riel (barra lateral en escritorio, tira en el teléfono) ───────────────
  function railItem(m) {
    const h = m.teams.home, a = m.teams.away;
    const on = state.view === m.event_id;
    const np = (state.data.picks || []).filter(k => k.event_id === m.event_id).length;
    const u = untilTxt(m.date, m.event_id);
    return `
      <button class="fb-rg lg-${m.league}${on ? ' is-selected' : ''}${u.past ? ' is-past' : ''}" data-ev="${esc(m.event_id)}" aria-pressed="${on}">
        <span class="fb-rg-time"><b>${hhmm(m.date)}</b><small class="${u.live ? 'is-live' : ''}">${u.live ? 'En vivo' : esc(dayLabel(m.date))}</small></span>
        <span class="fb-rg-teams">
          <span><img src="${logo(h.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">${esc(h.name)}</span>
          <span><img src="${logo(a.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">${esc(a.name)}</span>
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
      el.innerHTML = `<div class="fb-rail-empty">${state.error ? esc(state.error) : 'Trayendo partidos…'}</div>`;
      return;
    }
    const list = matches().filter(m => !untilTxt(m.date, m.event_id).past);
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
  const API_SHORT = { oddsnet: 'odds-api.net', oddspapi: 'OddsPapi', apifootball: 'API-Fútbol', theoddsapi: 'Odds API' };
  async function loadUsage(fresh = false) {
    try {
      state.usage = await (await fetch(`${apiBase()}/api/usage${fresh ? '?fresh=true' : ''}`)).json();
      renderTopTools();
    } catch (_) { /* sin red */ }
  }
  function usageList() {
    const u = state.usage || {};
    return ['oddsnet', 'oddspapi', 'apifootball', 'theoddsapi'].map(id => ({ id, ...(u[id] || {}) }))
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
    const oddsBusy = state.oddsBusy || state.sim.remote === 'odds';
    const busy = state.loading || state.sim.running || state.sim.remote || oddsBusy;
    const list = usageList();
    el.innerHTML = `
      <div class="fb-hud">
        <span class="fb-hud-odds${o.old ? ' is-old' : ''}${busy ? ' is-busy' : ''}" title="Jornada calculada ${esc(ago(state.data.generated_at))}">
          <i></i><span><small>${busy ? 'Calculando' : 'Cuotas'}</small><b>${busy ? (state.loading ? 'Recalculando…' : oddsBusy ? 'Pidiendo cuotas…' : 'Simulando…') : esc(o.t.replace('Cuotas de ', ''))}</b></span>
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
          ${ICON('ellipsis')}
        </button>
        ${state.menuOpen ? `
        <div class="fb-menu" role="menu">
          <div class="fb-menu-meta"><b>Jornada</b><span>Calculada ${esc(ago(state.data.generated_at))} · ${esc(o.t.toLowerCase())}</span></div>
          <button role="menuitem" data-act="refresh" ${state.loading ? 'disabled' : ''}>${I.refresh}<span><b>Recalcular la jornada</b><small>ESPN + 20.000 simulaciones por partido. Si las cuotas tienen más de 3 h, también las pide.</small></span></button>
          <button role="menuitem" data-act="odds" ${state.loading || oddsBusy ? 'disabled' : ''}>${I.coin}<span><b>${oddsBusy ? 'Pidiendo cuotas…' : 'Cuotas nuevas'}</b><small>bet365, Betano, Betsson y 1xBet. 1 pedido por casa, las 4 ligas juntas.</small></span></button>
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

  // "Cuotas nuevas": una sola a la vez, y no se repite si son recientes.
  async function refreshOdds() {
    if (state.oddsBusy) return;
    state.oddsBusy = true; render();
    toast(`${I.coin}<span><b>Pidiendo cuotas</b>bet365, Betano y Betsson · tarda alrededor de 1 minuto. Podés seguir usando la app.</span>`);
    try {
      const r = await fetch(`${apiBase()}/api/futbol/odds/refresh`, { method: 'POST' });
      const res = await r.json().catch(() => ({}));
      if (r.status === 409) { toast(`<span><b>Ya se están pidiendo cuotas</b>Esperá a que termine el pedido anterior.</span>`); return; }
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      const oi = res.odds || {};
      if (!oi.refreshed) {
        const age = oi.age_s == null ? '' : `${Math.max(1, Math.round(oi.age_s / 60))} min`;
        toast(`<span><b>No hizo falta pedir cuotas</b>Las que hay son de hace ${age}: pedir otras gastaría pedidos para lo mismo.</span>`);
        return;
      }
      state.data = await fetchJornada('');
      state.ver = null;
      const pk = res.picks || {};
      toast(`${I.check}<span><b>Cuotas nuevas</b>Jornada repreciada · ${pk.before ?? '–'} → ${pk.after ?? '–'} jugadas · ${oi.today}/${oi.cap} actualizaciones hoy</span>`);
    } catch (e) {
      toast(`<span><b>No se pudieron pedir cuotas</b>${esc(e.message || String(e))}</span>`);
    } finally {
      state.oddsBusy = false; render(); loadUsage(true);
    }
  }

  async function onTool(act) {
    if (state.loading) return;
    const o = (state.data && state.data.odds) || {};
    const cost = o.cost_per_refresh || 3, plan = o.plan_month || 250;
    if (act === 'odds') {
      const ok = await confirmDialog({
        title: 'Pedir cuotas nuevas',
        body: o.source === 'optic'
          ? `Se piden cuotas a OpticOdds para Betano y 1xBet (sin tope mensual) y a odds-api.net para bet365, que gasta unos <b>${cost} créditos</b> (1 por partido) de su plan mensual. Después se reprecian todas las jugadas con las probabilidades ya simuladas (tarda alrededor de 1 minuto).`
          : `Se piden cuotas a ${o.source === 'net' ? 'odds-api.net para bet365, Betano y Betsson (versiones de otros países)' : 'OddsPapi para bet365 AR, Betano y Betsson'}. Gasta unos <b>${cost} pedidos</b> (1 por partido) del plan de ${plan} por mes. Después se reprecian todas las jugadas con las probabilidades ya simuladas (tarda alrededor de 1 minuto).`,
        ok: o.source === 'optic' ? 'Sí, pedir cuotas' : `Sí, gastar ${cost}`,
      });
      if (ok) refreshOdds();
    } else {
      if (o.would_fetch) {
        const ok = await confirmDialog({
          title: 'Las cuotas están viejas',
          body: `Las cuotas guardadas tienen más de ${Math.round((o.ttl_s || 10800) / 3600)} h, así que recalcular también las vuelve a pedir: gasta <b>${cost} consultas</b> del plan de ${plan} por mes.`,
          ok: `Recalcular y gastar ${cost}`,
        });
        if (!ok) return;
      }
      startRecalc();
    }
  }

  // ── Recalcular la jornada: corre en el servidor y acá se sigue el avance.
  // Si se recarga la página (F5) o se abre desde otro dispositivo, la barra
  // se vuelve a enganchar sola con /api/futbol/status.
  async function startRecalc() {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/jornada/refresh`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      followRecalc((await r.json()).job);
    } catch (e) {
      toast(`<span><b>No se pudo recalcular</b>${esc(e.message || String(e))}</span>`);
    }
  }
  let followingRecalc = false;
  async function followRecalc(job) {
    if (followingRecalc) return;
    followingRecalc = true;
    state.loading = true; state.error = null;
    state.recalc = job && job.kind ? job : { pct: 0, text: 'Arrancando' };
    render();
    let err = null;
    try {
      for (;;) {
        await new Promise(res => setTimeout(res, 1000));
        let st;
        try { st = await (await fetch(`${apiBase()}/api/futbol/status`)).json(); } catch (_) { continue; }
        const jb = st.job || {};
        if (jb.kind) { state.recalc = jb; paintRecalc(); continue; }
        if (jb.error) err = jb.error;
        state.ver = st.version;
        break;
      }
    } finally {
      followingRecalc = false; state.recalc = null;
    }
    await load('');
    if (err) { state.error = `No se pudo recalcular la jornada: ${err}`; render(); }
    else toast(`${I.check}<span><b>Jornada recalculada</b>${(state.data && state.data.matches.length) || 0} partidos simulados</span>`);
  }
  function recalcBar() {
    const j = state.recalc || {};
    const pct = Math.max(0, Math.min(100, j.pct || 0));
    return `<div class="fb-busy fb-recalc"><span class="fb-spin"></span>
      <span class="fb-recalc-body"><span class="fb-recalc-top"><b>Recalculando la jornada</b><em data-recalc-pct>${Math.floor(pct)}%</em></span>
      <span class="fb-recalc-bar"><i data-recalc-bar style="transform:translateX(${pct - 100}%)"></i></span>
      <small data-recalc-text>${esc(j.text || '')}</small></span></div>`;
  }
  function paintRecalc() {
    const j = state.recalc || {};
    const pct = Math.max(0, Math.min(100, j.pct || 0));
    const bar = document.querySelector('[data-recalc-bar]');
    if (!bar) return render();
    bar.style.transform = `translateX(${pct - 100}%)`;
    document.querySelector('[data-recalc-pct]').textContent = `${Math.floor(pct)}%`;
    document.querySelector('[data-recalc-text]').textContent = j.text || '';
  }

  // ── Picks ─────────────────────────────────────────────────────────────────
  // Casas con precio para una línea, de mejor a peor (como mucho MAX_CHIPS).
  function quoted(books) {
    return BOOKS.map(b => ({ ...b, q: books && books[b.id] })).filter(b => b.q)
      .sort((a, b) => b.q.price - a.q.price).slice(0, MAX_CHIPS);
  }
  // Caja de cuota: si hay link a la casa (Betano: pestaña Jugadores; bet365:
  // "Rápidas de jugador"), la caja entera es el link. → [etiqueta, atributos]
  const SUSP_TIP = 'Cuando bajamos las cuotas, la casa tenía el partido suspendido (pasa al salir las formaciones). Es el último precio que mostró: confirmalo en la casa.';
  function bookLink(m, bookId, susp) {
    const url = m && m.book_links && m.book_links[bookId];
    const tip = susp ? SUSP_TIP : url ? `Abrir el partido en ${bookName(bookId)}` : '';
    return [url ? 'a' : 'span', `${url ? ` href="${esc(url)}" target="_blank" rel="noopener"` : ''}${tip ? ` title="${tip}"` : ''}`, url ? ' is-link' : ''];
  }
  function bookChips(l, minOdds, m) {
    const list = quoted(l.books);
    if (!list.length) return `<span class="fb-book fb-book--na">${bookBadge(BOOKS[0].id)}<b>—</b></span>`;
    return list.map(b => {
      const q = b.q;
      const good = q.price >= minOdds;
      const [tag, attrs, cls] = bookLink(m, b.id, q.susp);
      return `<${tag} class="fb-book${good ? ' fb-book--good' : ''}${q.susp ? ' is-susp' : ''}${cls}"${attrs}>${bookBadge(b.id)}<b>${odd(q.price)}</b>${q.susp ? '<i class="fb-susp">a confirmar</i>' : ''}</${tag}>`;
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
            ${photo ? `<img src="${esc(photoSrc(photo))}" alt="" loading="lazy" decoding="async" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${esc(initials(k.player))}'}))">` : `<span>${esc(initials(k.player))}</span>`}
            ${team ? `<img class="fb-face-crest" src="${logo(team.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}
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
          <span class="fb-pick-books">${bookChips(k, k.min_odds, m)}</span>
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
    return `<b>+${n}</b> ${n === 1 ? 'Remate' : 'Remates'}${k.stat === 'shots' ? '' : ' al arco'}`;
  }
  function verdict(p) {
    if (p >= 0.65) return { t: 'Muy probable', c: 'hi' };
    if (p >= 0.5) return { t: 'Probable', c: 'mid' };
    return { t: 'Valor', c: 'lo' };
  }
  const BALL_ON = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.6" fill="currentColor"/><path d="M10 6.2l3.1 2.3-1.2 3.6H8.1L6.9 8.5z" fill="#06090c"/></svg>';
  const BALL_OFF = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  function balls(p, key = '') {
    const n = Math.max(0, Math.min(10, Math.round(p * 10)));
    // Medidor de 10 segmentos inclinados (como una barra de energía): los
    // llenos en el color de la jugada, se cargan de izquierda a derecha.
    return `<button class="fb-meter" data-balls="${esc(key)}" aria-label="${n} de cada 10 — tocá para ver qué significa">${Array.from({ length: 10 }, (_, i) =>
      `<span class="${i < n ? 'on' : ''}" style="--i:${i}"></span>`).join('')}</button>`;
  }
  // Estado del partido según ESPN (lo que trajo el seguimiento en vivo de
  // cualquier jugada de ese partido): manda sobre la cuenta por reloj.
  function evLiveState(ev) {
    if (!ev) return null;
    let st = null;
    for (const [key, v] of Object.entries(state.live)) {
      if (!v || !v.state || !key.startsWith(`${ev}|`)) continue;
      if (v.state === 'post' || v.completed) return 'post';
      st = v.state;
    }
    return st;
  }
  function untilTxt(iso, ev) {
    const min = Math.round((when(iso) - Date.now()) / 60000);
    const es = evLiveState(ev);
    if (es === 'post') return { t: 'Terminado', live: false, past: true };
    if (es === 'in') return { t: 'En juego', live: true };
    // Sin dato de ESPN: por reloj. 110' quedaba corto (entretiempo largo,
    // descuento, inicio demorado) y daba por terminado un partido en juego.
    if (min <= -135) return { t: 'Terminado', live: false, past: true };
    if (min <= 0) return { t: 'En juego', live: true };
    if (min < 60) return { t: `Empieza en ${min} min`, soon: true };
    const h = Math.floor(min / 60), mm = min % 60;
    if (h < 24) return { t: `Empieza en ${h} h${mm ? ' ' + String(mm).padStart(2, '0') : ''}`, soon: h < 2 };
    return { t: `${dayLabel(iso)} ${hhmm(iso)}` };
  }
  // En vivo en los tickets: cómo va cada jugada (el mismo dato de ESPN que usa
  // la boleta). Un ticket con datos en vivo sigue a la vista hasta 4 h después
  // del inicio, así se ve el final aunque el partido dure más de lo previsto.
  const LIVE_KEEP_MS = 4 * 3600 * 1000;
  function tkLive(k) {
    const L = state.live[pickKey(k)];
    return L && L.state && L.state !== 'pre' ? L : null;
  }
  const tkGone = (k) => untilTxt(k.date, k.event_id).past && !(tkLive(k) && Date.now() - when(k.date) < LIVE_KEEP_MS);
  function tkLiveCls(L) {
    if (!L) return '';
    if (L.hit) return ' is-hit';
    return L.state === 'post' || L.completed ? ' is-over' : ' is-live';
  }
  // Barra de progreso que aparece al empezar el partido: el relleno verde avanza
  // con cada remate y debajo, en la punta del relleno, el número que lleva.
  // Ganada: la barra se va y queda el cartel "Ganada" en la esquina del ticket.
  function tkLiveBar(k, L) {
    if (L.hit) return '';
    const need = Math.round(k.line + 0.5);
    const total = Math.round(L.total || 0);
    const pct = Math.min(100, (total / need) * 100);
    const end = L.state === 'post' || L.completed;
    const clock = end ? 'Final' : (L.clock || L.detail || 'En juego');
    let note = '';
    if (L.playing === false) note = end ? 'No jugó' : 'Todavía no entró';
    else if (end) note = 'No llegó';
    return `<div class="fb-tk-prog" style="--p:${pct.toFixed(1)}%" title="${total} de ${need}">
      <div class="fb-tk-bar"><div class="fb-tk-track"><span class="fb-tk-fill"></span></div><b class="fb-tk-goal">${need}</b></div>
      <div class="fb-tk-scale">${total > 0 ? `<em class="fb-tk-cnt">${total}</em>` : ''}<small class="fb-tk-clock">${!end ? '<i class="fb-tk-dot"></i>' : ''}${esc(clock)}${note ? ` · ${note}` : ''}</small></div>
    </div>`;
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
    const L = tkLive(k);
    return `
      <article class="fb-tk fb-tk--${v.c}${on ? ' is-in' : ''}${tkLiveCls(L)}" data-tk="${esc(pickKey(k))}">
        ${L && L.hit ? '<span class="fb-tk-won">Ganada</span>' : ''}
        <div class="fb-tk-face">
          ${pl && pl.photo ? `<img src="${esc(photoSrc(pl.photo))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}
          <span>${esc(initials(k.player))}</span>
          ${team ? `<img class="fb-tk-crest" src="${logo(team.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}
        </div>
        <div class="fb-tk-main">
          <div class="fb-tk-who"><b>${esc(k.player)}</b>${posBadge(k)}${(a => a ? `<span class="fb-posw-chip" title="${esc(a.text)}"><i aria-hidden="true">!</i>Puesto raro</span>` : '')(posAlertFor(m, k.player_id))}</div>
          <div class="fb-tk-bet">${betSentence(k)}</div>
          <div class="fb-tk-odds">
            ${balls(k.p_model, pickKey(k))}
            <span class="fb-tk-n"><b>${n10}</b> de cada 10 · <em>${v.t}</em></span>
          </div>
          ${L ? tkLiveBar(k, L) : ''}
          ${state.ballOpen === pickKey(k) ? `<div class="fb-ball-tip">En <b>${n10} de cada 10</b> partidos simulados, ${esc(surname(k.player))} hace ${esc(lineLabel(k.stat, k.line).toLowerCase())}. La casa paga ${odd(best ? best.q.price : k.min_odds)}: ${best && best.q.price >= k.min_odds ? 'más de lo que vale según el modelo.' : 'todavía por debajo de lo que vale.'}</div>` : ''}
        </div>
        <div class="fb-tk-side">
          <div class="fb-tk-min"><small>Jugala desde</small><b>${odd(k.min_odds)}</b></div>
          <div class="fb-tk-books">${quoted(k.books).map(b => {
            const q = b.q;
            const isBest = best && q && b.id === best.id;
            const [tag, attrs, cls] = q ? bookLink(m, b.id, q.susp) : ['span', '', ''];
            return `<${tag} class="fb-bkc${q ? '' : ' is-na'}${isBest ? ' is-best' : ''}${q && q.price >= k.min_odds ? ' is-ok' : ''}${q && q.susp ? ' is-susp' : ''}${cls}"${attrs}>${isBest ? '<em>Mejor</em>' : ''}${bookBadge(b.id, 'is-sm')}<b>${q ? odd(q.price) : '—'}</b>${q && q.susp ? '<i class="fb-susp">a confirmar</i>' : ''}</${tag}>`;
          }).join('')}</div>
          <button class="fb-tk-add" data-slip="${esc(pickKey(k))}" aria-pressed="${on}">${on ? `${I.check}En mi boleta` : '+ La juego'}</button>
        </div>
      </article>`;
  }

  function simpleView() {
    const d = state.data;
    const lg = state.league;
    const list = (d.picks || []).filter(k => lg === 'all' || k.league === lg)
      .filter(k => !tkGone(k));
    // agrupado por partido, en orden de horario
    const byEv = new Map();
    list.slice().sort((a, b) => (when(a.date) - when(b.date)) || (b.p_model - a.p_model))
      .forEach(k => { if (!byEv.has(k.event_id)) byEv.set(k.event_id, []); byEv.get(k.event_id).push(k); });
    const next = [...byEv.values()][0];
    const nextU = next ? untilTxt(next[0].date, next[0].event_id) : null;
    const groups = [...byEv.entries()].map(([ev, ks], gi) => {
      const m = matchById(ev);
      const u = untilTxt(ks[0].date, ev);
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
  // Letra del resultado, como en NBA: G verde, P roja, N gris; con suplente la G lleva el trazo de la liga.
  const RES_TAG = { won: 'G', lost: 'P', void: 'N' };
  const RES_TITLE = { won: 'Ganada', lost: 'Perdida', void: 'Nula (no jugó)' };
  let resPhotos = new Map();                    // player_id → foto (de la jornada cargada)
  function resultRow(r) {
    const lg = LEAGUES.find(x => x.id === r.league);
    const unit = (n) => (r.stat === 'shots' ? (n === 1 ? 'remate' : 'remates') : 'al arco');
    // Promo de suplente: lo suyo + lo del que entró por él (cuenta para la jugada).
    const viaSub = r.status === 'won' && !!r.via_sub;
    const act = r.actual == null ? 'no jugó'
      : r.sub_player && r.sub_actual != null && r.own_actual != null
        ? `${Math.round(r.own_actual)} suyo${Math.round(r.own_actual) === 1 ? '' : 's'} + ${Math.round(r.sub_actual)} de ${surname(r.sub_player)} (entró por él) = ${Math.round(r.actual)} ${unit(Math.round(r.actual))}`
        : `${Math.round(r.actual)} ${unit(Math.round(r.actual))}`;
    const price = r.first_price ? odd(r.first_price) : '—';
    const pl = r.status === 'won' && r.first_price ? units(r.first_price - 1) : r.status === 'lost' ? units(-1) : '';
    return `
      <div class="fb-res fb-res--${r.status}${viaSub ? ' fb-res--sub' : ''}${r.sub_player ? ' has-sub' : ''} lg-${r.league}">
        <span class="fb-res-mark" title="${viaSub ? 'Ganada con suplente' : (RES_TITLE[r.status] || 'En juego')}">${resPhotos.get(String(r.player_id)) ? `<img src="${esc(photoSrc(resPhotos.get(String(r.player_id))))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}<span class="fb-res-ini">${esc(initials(r.player))}</span><span class="fb-res-tag">${RES_TAG[r.status] || '·'}</span></span>
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
    resPhotos = new Map();
    ((state.data && state.data.matches) || []).forEach(m => (m.players || []).forEach(p => {
      if (p.photo) resPhotos.set(String(p.player_id), p.photo);
    }));
    const sm = R.summary_recent || {};
    const clv = R.summary && R.summary.clv != null && R.summary.clv_n >= 5
      ? `<span title="Precio tomado contra el último precio antes del partido">Cuota tomada vs cierre: <b class="${R.summary.clv >= 0 ? 'up' : 'down'}">${R.summary.clv >= 0 ? '+' : ''}${(R.summary.clv * 100).toFixed(1).replace('.', ',')} %</b></span>` : '';
    return `
      <section class="fb-block fb-results">
        <div class="fb-block-head fb-res-head">
          <span class="fb-block-sub">Últimos 10 días · ${sm.won || 0} ganadas${sm.won_via_sub ? ` (<b class="fb-sub-count">${sm.won_via_sub} con suplente</b>)` : ''}, ${sm.lost || 0} perdidas${sm.void ? `, ${sm.void} nulas` : ''} · ${units(sm.units || 0)} a 1 u por pick ${clv ? '· ' + clv : ''}</span>
        </div>
        <div class="fb-res-list">${list.slice(0, state.resN || RES_PAGE).map(resultRow).join('')}</div>
        ${list.length > (state.resN || RES_PAGE) ? `<button class="fb-res-more" data-resmore>Mostrar más<small>${state.resN || RES_PAGE} de ${list.length}</small></button>` : ''}
        <p class="fb-note">Se liquida solo con ESPN cuando termina el partido. Nula = el jugador no entró (la casa devuelve la apuesta). Con suplente = bet365, Betano y Betsson suman lo del que entró por el titular (Suplente+, Suplente de Oro, Suplente). Las unidades usan la mejor cuota cuando apareció el pick.</p>
      </section>`;
  }

  // ── Mi boleta ─────────────────────────────────────────────────────────────
  // Agrupada por liga (cada grupo con el trazo y la letra de su liga). Cada
  // jugada: foto, nombre, la línea y una barra desplegable con los remates en
  // vivo (ESPN). Cuando cumple la línea se pinta de verde entero.
  const SLIP_KEEP_MS = 24 * 3600 * 1000;      // las terminadas quedan un día
  function pruneSlip(jornadaKeys) {
    if (!state.data) return;
    const live = jornadaKeys || new Set((state.data.picks || []).map(pickKey));
    const res = new Map(((state.results && state.results.picks) || []).map(r => [r.key, r]));
    const before = state.slip.size;
    state.slip = new Set([...state.slip].filter(k => {
      if (live.has(k)) return true;
      const r = res.get(k);
      const x = !r && (lineAsPick(k) || slipX[k]);   // agregada desde Sharp / Parlay
      if (x) return Date.now() - when(x.date) < SLIP_KEEP_MS;
      if (!r) return !state.results;            // sin resultados cargados todavía: no se toca
      return r.status !== 'withdrawn' && Date.now() - when(r.date) < SLIP_KEEP_MS;
    }));
    if (state.slip.size !== before) { store.set('fb-slip', [...state.slip]); savePrefs(); }
  }
  // Jugadas agregadas desde Sharp / Parlay: no son picks del modelo, así que se
  // arman con la línea del partido y se guardan acá para seguir mostrándolas
  // cuando el partido sale de la jornada.
  const slipX = store.get('fb-slip-x', {});
  function lineAsPick(key) {
    const [ev, pid, stat, line] = String(key).split('|');
    const m = state.data && matches().find(x => String(x.event_id) === ev);
    const p = m && (m.players || []).find(x => String(x.player_id) === pid);
    const l = p && (p.lines || []).find(x => x.stat === stat && Number(x.line) === Number(line));
    if (!l) return null;
    return { key, custom: true, event_id: m.event_id, league: m.league, date: m.date, match: `${m.teams.home.name} vs ${m.teams.away.name}`,
      player: p.player, player_id: p.player_id, team_id: p.team_id, stat: l.stat, line: l.line,
      p_model: l.p_model, fair_odds: l.fair_odds, min_odds: l.min_odds, best_book: l.best_book || null, best_price: l.best_price || null };
  }
  function toggleSlip(k) {
    if (state.slip.has(k)) { state.slip.delete(k); delete slipX[k]; }
    else {
      state.slip.add(k);
      const isPick = ((state.data && state.data.picks) || []).some(p => pickKey(p) === k);
      const x = !isPick && lineAsPick(k);
      if (x) slipX[k] = x;
    }
    store.set('fb-slip-x', slipX);
    store.set('fb-slip', [...state.slip]); savePrefs();
  }
  // Foto y equipo guardados al marcar: el partido sale de la jornada cuando empieza.
  const slipMeta = store.get('fb-slip-meta', {});
  function slipPicks() {
    const by = new Map();
    for (const r of (state.results && state.results.picks) || []) {
      if (!state.slip.has(r.key) || r.status === 'withdrawn') continue;
      by.set(r.key, { ...r, settled: r.status !== 'open' ? r.status : null, book: r.first_book });
    }
    for (const k of (state.data && state.data.picks) || []) {
      const key = pickKey(k);
      if (!state.slip.has(key)) continue;
      const prev = by.get(key) || {};
      by.set(key, { ...prev, ...k, key, settled: prev.settled || null, book: prev.book || k.best_book });
    }
    let xDirty = false;
    for (const key of state.slip) {
      if (by.has(key)) continue;
      const fresh = lineAsPick(key);
      if (fresh && JSON.stringify(slipX[key]) !== JSON.stringify(fresh)) { slipX[key] = fresh; xDirty = true; }
      const x = fresh || slipX[key];
      if (x) by.set(key, { ...x, key, settled: null, book: x.best_book });
    }
    for (const key of Object.keys(slipX)) if (!state.slip.has(key)) { delete slipX[key]; xDirty = true; }
    if (xDirty) store.set('fb-slip-x', slipX);
    let dirty = false;
    const out = [...by.values()].map(k => {
      const m = matchById(k.event_id);
      const pl = m ? m.players.find(x => x.player_id === k.player_id) : null;
      const team = m ? teamOf(m, k.team_id) : null;
      if (pl || team) {
        const meta = { photo: (pl && pl.photo) || null, team: (team && team.name) || null, crest: (team && team.team_id) || null };
        if (JSON.stringify(slipMeta[k.key]) !== JSON.stringify(meta)) { slipMeta[k.key] = meta; dirty = true; }
      }
      return { ...k, meta: slipMeta[k.key] || {} };
    }).sort((a, b) => (when(a.date) - when(b.date)) || String(a.player).localeCompare(String(b.player)));
    if (dirty) {
      for (const key of Object.keys(slipMeta)) if (!state.slip.has(key)) delete slipMeta[key];
      store.set('fb-slip-meta', slipMeta);
    }
    return out;
  }
  // Estado de una jugada: pre / live / hit (cumplió en vivo) / post / won / lost / void.
  function slipState(k) {
    if (k.settled) return k.settled;                       // liquidada al terminar
    const L = state.live[k.key];
    if (L && L.hit) return 'hit';
    if (L && L.state === 'in') return 'live';
    if (L && L.state === 'post') return 'post';
    return 'pre';
  }
  function slipBar(k, st) {
    const L = state.live[k.key] || {};
    const need = Math.round(k.line + 0.5);
    const unit = k.stat === 'shots' ? (need === 1 ? 'remate' : 'remates') : 'al arco';
    if (st === 'pre') {
      const u = untilTxt(k.date, k.event_id);
      return `<div class="fb-sl-live is-pre"><span class="fb-sl-pips">${Array.from({ length: need }, () => '<i></i>').join('')}</span>
        <span class="fb-sl-txt"><b>${esc(u.t)}</b><small>Jugala desde ${odd(k.min_odds)} · acá vas a ver los remates en vivo</small></span></div>`;
    }
    const final = !!k.settled && k.actual != null;
    const total = final ? k.actual : (L.total || 0);
    const shown = Math.max(need, Math.ceil(total));
    const pips = Array.from({ length: shown }, (_, i) => `<i class="${i < total ? 'on' : ''}${i >= need ? ' extra' : ''}"></i>`).join('');
    const clock = final || st === 'post' ? 'Final' : (L.clock || L.detail || 'En juego');
    let note = '';
    if (st === 'void') note = 'No jugó: la casa devuelve la apuesta';
    else if (L.state && L.playing === false) note = L.state === 'post' ? 'No jugó' : 'Todavía no entró';
    const head = st === 'hit' || st === 'won' ? `¡Cumplió! ${total} ${unit}`
      : st === 'lost' ? `No llegó: ${total} de ${need}` : st === 'void' ? 'Nula' : `${total} de ${need} ${unit}`;
    return `<div class="fb-sl-live"><span class="fb-sl-pips">${pips}</span>
      <span class="fb-sl-txt"><b>${head}</b><small>${esc(clock)}${note ? ` · ${note}` : ''}</small></span></div>`;
  }
  function slipRow(k) {
    const st = slipState(k);
    const open = state.slipExp.has(k.key) !== (st === 'live' || st === 'hit');   // en vivo: abierta salvo que la cierres
    const photo = k.meta.photo;
    const u = untilTxt(k.date, k.event_id);
    const tag = st === 'live' ? '<em class="fb-sl-tag is-live">En vivo</em>'
      : st === 'hit' || st === 'won' ? `<em class="fb-sl-tag is-hit">${I.check}Cumplió</em>`
      : st === 'lost' ? '<em class="fb-sl-tag is-lost">Perdida</em>' : st === 'void' ? '<em class="fb-sl-tag">Nula</em>' : '';
    return `
      <div class="fb-sl-pk is-${st}${open ? ' is-open' : ''}">
        <div class="fb-sl-row">
          <button class="fb-sl-main" data-slipgo="${esc(k.key)}" title="Ver la jugada y sus cuotas">
            <span class="fb-sl-face">${photo ? `<img src="${esc(photoSrc(photo))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}<span>${esc(initials(k.player))}</span>
              ${k.meta.crest ? `<img class="fb-sl-crest" src="${logo(k.meta.crest)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}</span>
            <span class="fb-sl-who">
              <span class="fb-sl-name"><b>${esc(k.player)}</b><em class="fb-sl-line">${esc(lineLabel(k.stat, k.line))}</em></span>
              <small>${tag}${esc(k.meta.team || '')}${k.meta.team ? ' · ' : ''}${u.live || u.past ? esc(k.match || '') : `${esc(dayLabel(k.date))} ${hhmm(k.date)}`}</small>
            </span>
          </button>
          <button class="fb-sl-tog" data-slipexp="${esc(k.key)}" aria-expanded="${open}" aria-label="${open ? 'Cerrar' : 'Ver remates'}">${ICON('chevronDown', 16)}</button>
          <button class="fb-sl-x" data-slip="${esc(k.key)}" aria-label="Sacar de la boleta">${ICON('xmark')}</button>
        </div>
        ${open ? slipBar(k, st) : ''}
      </div>`;
  }
  function slipHtml() {
    const ks = slipPicks();
    if (!ks.length) return '';
    const groups = new Map();
    ks.forEach(k => { if (!groups.has(k.league)) groups.set(k.league, []); groups.get(k.league).push(k); });
    const nLive = ks.filter(k => slipState(k) === 'live').length;
    const nHit = ks.filter(k => ['hit', 'won'].includes(slipState(k))).length;
    return `
      <aside class="fb-slip${state.slipOpen ? ' is-open' : ''}">
        <button class="fb-slip-bar" data-slipact="toggle" aria-expanded="${state.slipOpen}">
          <span class="fb-slip-count">${ks.length}</span>
          <span>Mi boleta</span>
          ${nLive ? `<span class="fb-slip-livedot">${nLive} en vivo</span>` : ''}
          ${nHit ? `<span class="fb-slip-hits">${I.check}${nHit}</span>` : ''}
          <small>${state.slipOpen ? 'Cerrar' : 'Ver'}</small>
        </button>
        <div class="fb-slip-body">
          ${[...groups.entries()].map(([lg, list]) => {
            const L = leagueOf(lg);
            return `
            <section class="fb-sl-lg lg-${esc(lg)}">
              <header class="fb-sl-pill">${L.logo ? `<img src="${esc(L.logo)}" alt="">` : ''}<b>${esc(L.name)}</b><span>${list.length}</span></header>
              <div class="fb-sl-list">${list.map(slipRow).join('')}</div>
            </section>`;
          }).join('')}
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
  // Redibuja sólo la boleta (sin perder dónde estaba el scroll de adentro).
  function renderSlip() {
    const host = document.getElementById('fb-slip-host');
    if (!host) return;
    const body = host.querySelector('.fb-slip-body');
    const top = body ? body.scrollTop : 0;
    host.innerHTML = slipHtml() + navBottom();
    const nb = host.querySelector('.fb-slip-body');
    if (nb) nb.scrollTop = top;
  }
  // En vivo: cada 30" mientras haya jugadas en juego (las de la boleta y los
  // tickets de la jornada, para el contador de remates de cada ticket).
  async function pollLive() {
    if (!futbolVisible() || !state.data) return;
    const now = Date.now();
    const inPlay = (k) => when(k.date) - now < 5 * 60000 && now - when(k.date) < 150 * 60000;
    const ks = state.slip.size ? slipPicks().filter(k => !k.settled && inPlay(k)) : [];
    const mine = new Set(ks.map(k => k.key));
    const tks = (state.data.picks || []).filter(k => inPlay(k) && !mine.has(pickKey(k)) && !(tkLive(k) || {}).completed);
    if (!ks.length && !tks.length) return;
    const rows = [
      ...ks.map(k => ({ key: k.key, league: k.league, event_id: k.event_id, player_id: k.player_id,
        stat: k.stat, line: k.line, mode: k.mode || 'normal', book: k.book || null })),
      ...tks.map(k => ({ key: pickKey(k), league: k.league, event_id: k.event_id, player_id: k.player_id,
        stat: k.stat, line: k.line, mode: k.mode || 'normal', book: k.best_book || null })),
    ].slice(0, 40);
    try {
      const r = await fetch(`${apiBase()}/api/futbol/live`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ picks: rows }),
      });
      if (!r.ok) return;
      const d = await r.json();
      const hitsBefore = new Set(Object.entries(state.live).filter(([, v]) => v.hit).map(([key]) => key));
      const sig = (v) => v ? [v.state, v.clock, v.total, v.hit, v.playing, v.subbed_out, v.completed, v.sub && v.sub.player].join('|') : '';
      const tkChanged = Object.entries(d.picks || {}).some(([key, v]) => !mine.has(key) && sig(state.live[key]) !== sig(v))
        || Object.entries(d.picks || {}).some(([key, v]) => mine.has(key) && document.querySelector(`[data-tk="${CSS.escape(key)}"]`) && sig(state.live[key]) !== sig(v));
      state.live = { ...state.live, ...(d.picks || {}) };
      ks.forEach(k => {
        const L = state.live[k.key];
        if (L && L.hit && !hitsBefore.has(k.key)) toast(`${I.check}<span><b>¡Cumplió!</b>${esc(k.player)} · ${esc(lineLabel(k.stat, k.line))}</span>`);
      });
      if (Object.values(d.picks || {}).some(v => v.completed)) loadResults();
      renderSlip();
      if (tkChanged) bgRender();
    } catch (_) { /* sin red: queda lo anterior */ }
  }
  // De la boleta a la jugada: el partido con sus cuotas, con la jugada marcada.
  function goToPick(key) {
    const ev = key.split('|')[0];
    if (!matchById(ev)) {
      toast('<span><b>El partido ya empezó</b>Sale de la jornada al empezar: lo que lleva está en tu boleta</span>');
      return;
    }
    state.slipOpen = false;
    go(ev);
    setTimeout(() => {
      const el = document.querySelector(`[data-tk="${CSS.escape(key)}"]`);
      if (!el) return;
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      el.classList.add('is-flash');
      setTimeout(() => el.classList.remove('is-flash'), 2200);
    }, 350);
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
            ${p.photo ? `<img src="${esc(photoSrc(p.photo))}" alt="" loading="lazy" decoding="async" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${esc(initials(p.player))}'}))">`
              : `<span>${esc(initials(p.player))}</span>`}
            <img class="fb-face-crest" src="${logo(team.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">
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
        <div class="fb-card-books">${anyBook ? bookChips(sel, sel.min_odds, m) :'<span class="fb-nobooks">Sin cuotas de las casas para este jugador</span>'}</div>`
        : `<div class="fb-card-sel fb-card-sel--na">Línea muy improbable (< 2 %)</div>`}
      </article>`;
  }

  // ════════════════════════════════════════════════════════════════════════
  //  Navegación: Jugadas · Partidos · Resultados (arriba en escritorio,
  //  abajo al alcance del pulgar en el teléfono).
  // ════════════════════════════════════════════════════════════════════════
  const SECTIONS = ['recs', 'mine', 'picks', 'matches', 'results', 'form'];
  const isSection = (v) => SECTIONS.includes(v);
  function navItems() {
    const d = state.data || {};
    const live = (d.picks || []).filter(k => !untilTxt(k.date, k.event_id).past).length;
    const sm = state.results && state.results.summary;
    const cur = isSection(state.view) ? state.view : state.back;
    const item = (id, icon, label, badge) => `
      <button class="fb-nav-i${cur === id ? ' is-on' : ''}" data-ev="${id}" aria-current="${cur === id ? 'page' : 'false'}">
        ${icon}<span>${label}</span>${badge != null && badge !== '' ? `<b>${badge}</b>` : ''}
      </button>`;
    const mine = myMatches().length;
    return item('picks', I.tick, 'Jugadas', live || '')
      + item('recs', I.spark, 'Recomendados', recommended().length || '')
      + item('matches', I.pitch, 'Partidos', matches().length || '')
      + item('results', I.trophy, 'Resultados', sm && sm.settled ? `${sm.won}–${sm.lost}` : '')
      + item('form', I.chart, 'Rendimiento', '')
      + `<button class="fb-nav-i fb-nav-fav fb-nav-star${cur === 'mine' ? ' is-on' : ''}" data-ev="mine" aria-current="${cur === 'mine' ? 'page' : 'false'}" aria-label="Favoritos: ${mine}" title="Favoritos">
        ${I.starFill}${mine ? `<b>${mine}</b>` : ''}</button>`;
  }
  const navTop = () => `<nav class="fb-nav" aria-label="Fútbol">${navItems()}</nav>`;
  const navBottom = () => (state.data ? `<nav class="fb-tabbar" aria-label="Fútbol">${navItems()}</nav>` : '');
  function leagueChips() {
    const lg = state.league;
    // Selector de ligas: un ícono grande por liga/copa; la elegida se abre y muestra su nombre.
    const tile = (id, name, img) => {
      const on = lg === id;
      return `<button type="button" role="tab" data-lg="${id}" class="fb-lgt lg-${id}${on ? ' is-on' : ''}" aria-selected="${on}" aria-label="${esc(name)}" title="${esc(name)}">
        <span class="fb-lgt-ic">${img}</span><span class="fb-lgt-name">${esc(name)}</span></button>`;
    };
    // Una sola línea: en el celu se arrastra con el dedo; en la compu, flechas a los costados.
    const chev = (d) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
    return `
      <div class="fb-lgwrap">
        <button type="button" class="fb-lgnav fb-lgnav--prev" data-lgnav="-1" aria-label="Ver ligas anteriores" tabindex="-1">${chev('M15 5.5 8.5 12l6.5 6.5')}</button>
        <div class="fb-lgbar" role="tablist" aria-label="Liga">
          ${tile('all', 'Todas las ligas', `<img src="assets/soccer-ball.png" alt="">`)}
          ${LEAGUES.map(L => tile(L.id, L.name, `<img src="${L.logo}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:'${esc(L.short)}'}))">`)).join('')}
        </div>
        <button type="button" class="fb-lgnav fb-lgnav--next" data-lgnav="1" aria-label="Ver más ligas" tabindex="-1">${chev('M9 5.5 15.5 12 9 18.5')}</button>
      </div>`;
  }

  // ── Barra de ligas: desplazamiento, flechas y animación al elegir ──────
  // render() reemplaza todo el HTML: se guarda dónde estaba la barra y, si se
  // acaba de elegir una liga, cuánto medía el nombre que se cierra.
  let lgAnim = null;
  function lgPick(id) {
    const bar = document.querySelector('#section-futbol .fb-lgbar');
    const prev = bar && bar.querySelector('.fb-lgt.is-on');
    const nm = prev && prev.querySelector('.fb-lgt-name');
    lgAnim = null;
    if (prev && prev.dataset.lg !== id && nm) {
      const cs = getComputedStyle(nm);
      lgAnim = { from: prev.dataset.lg, w: nm.getBoundingClientRect().width, ml: cs.marginLeft, pr: cs.paddingRight, at: Date.now() };
    } else if (!prev || prev.dataset.lg !== id) lgAnim = { from: null, at: Date.now() };
  }
  function lgArrows(bar) {
    const wrap = bar.parentElement;
    const max = bar.scrollWidth - bar.clientWidth;
    wrap.classList.toggle('can-prev', bar.scrollLeft > 4);
    wrap.classList.toggle('can-next', bar.scrollLeft < max - 4);
  }
  function lgBarAfter(scrollLeft) {
    const bar = document.querySelector('#section-futbol .fb-lgbar');
    if (!bar) return;
    if (scrollLeft != null) bar.scrollLeft = scrollLeft;
    bar.addEventListener('scroll', () => lgArrows(bar), { passive: true });
    if (window.ResizeObserver) new ResizeObserver(() => lgArrows(bar)).observe(bar);
    lgArrows(bar);
    const on = bar.querySelector('.fb-lgt.is-on');
    const a = lgAnim && Date.now() - lgAnim.at < 600 ? lgAnim : null;
    lgAnim = null;
    if (!a || !on) return;
    // La elegida queda a la vista (si quedó cortada en un costado).
    const br = bar.getBoundingClientRect(), r = on.getBoundingClientRect();
    if (r.left < br.left + 40 || r.right > br.right - 40) {
      bar.scrollTo({ left: bar.scrollLeft + (r.left + r.width / 2) - (br.left + br.width / 2), behavior: fxReduce() ? 'auto' : 'smooth' });
    }
    if (fxReduce()) return;
    // Se abre: el nombre crece desde el ícono, entra de costado y desenfocado,
    // y el ícono hace un pequeño rebote. La anterior se cierra a la vez.
    const nm = on.querySelector('.fb-lgt-name');
    const cs = getComputedStyle(nm);
    const w = nm.getBoundingClientRect().width;
    nm.animate([
      { maxWidth: '0px', marginLeft: '0px', paddingRight: '0px', opacity: 0, transform: 'translateX(-10px)', filter: 'blur(4px)' },
      { maxWidth: `${w}px`, marginLeft: cs.marginLeft, paddingRight: cs.paddingRight, opacity: 1, transform: 'none', filter: 'blur(0)' },
    ], { duration: 460, easing: EASE_OUT });
    on.querySelector('.fb-lgt-ic').animate([
      { transform: 'scale(.78) rotate(-8deg)' }, { transform: 'scale(1.12) rotate(3deg)', offset: .55 }, { transform: 'none' },
    ], { duration: 520, easing: EASE_OUT });
    on.animate([{ boxShadow: '0 0 0 0 transparent', backgroundColor: 'rgba(255,255,255,.05)' }, {}], { duration: 380, easing: EASE_OUT });
    const old = a.from && bar.querySelector(`.fb-lgt[data-lg="${CSS.escape(a.from)}"]`);
    const onm = old && old.querySelector('.fb-lgt-name');
    if (onm && a.w) {
      onm.animate([
        { maxWidth: `${a.w}px`, marginLeft: a.ml, paddingRight: a.pr, opacity: 1 },
        { maxWidth: '0px', marginLeft: '0px', paddingRight: '0px', opacity: 0 },
      ], { duration: 300, easing: EASE_IN_OUT });
    }
  }
  function lgScrollBy(dir) {
    const bar = document.querySelector('#section-futbol .fb-lgbar');
    if (bar) bar.scrollBy({ left: dir * Math.max(160, bar.clientWidth * 0.7), behavior: fxReduce() ? 'auto' : 'smooth' });
  }

  // ── Mis partidos: los marcados con estrella + los de la boleta ──────────
  function myMatches() {
    const slipEv = new Set([...state.slip].map(k => k.split('|')[0]));
    return matches().filter(m => (state.fav.has(m.event_id) || slipEv.has(m.event_id)) && !untilTxt(m.date, m.event_id).past);
  }
  function starBtn(id) {
    const on = state.fav.has(id);
    return `<button class="fb-star${on ? ' is-on' : ''}" data-fav="${esc(id)}" aria-pressed="${on}" title="${on ? 'Sacar de Favoritos' : 'Agregar a Favoritos'}">${on ? I.starFill : I.star}</button>`;
  }
  function mineView() {
    const mine = myMatches();
    const slipEv = new Set([...state.slip].map(k => k.split('|')[0]));
    if (!mine.length) {
      const soon = matches().filter(m => !untilTxt(m.date, m.event_id).past).slice(0, 8);
      return `
        <header class="fb-sec-head">
          <h2>Favo<em>ritos</em></h2>
          <p>Tocá la estrella ${ICON('star')} de un partido y queda guardado acá, con su simulación y sus jugadas. Los que tengan jugadas en tu boleta aparecen solos.</p>
        </header>
        <div class="fb-mine-empty">${I.star}<span>Todavía no marcaste ninguno. Estos son los próximos:</span></div>
        <div class="fb-fx-list">${soon.map(m => fixture(m)).join('')}</div>`;
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
  // "Qué cambió" con el último Simular: una franja fina debajo de los botones
  // (no estira nada). Las jugadas que entran/salen se despliegan aparte.
  // Queda guardado en el partido, así se ve en todos los dispositivos.
  function lastSimBlock(m) {
    const ls = m.last_sim;
    if (!ls || simState(m.event_id)) return '';
    const h = m.teams.home, a = m.teams.away;
    const t = ls.teams || {};
    const oi = ls.odds || {};
    const oage = oi.age_s == null ? '–' : (oi.age_s < 3600 ? `${Math.max(1, Math.round(oi.age_s / 60))} min` : `${Math.round(oi.age_s / 3600)} h`);
    const arrow = (b, a2) => (b === a2 ? `<b>${b}</b>` : `<s>${b}</s><i class="fb-chg2-to">→</i><b>${a2}</b>`);
    const shot = (x) => {
      if (!x) return '–';
      const d = x.after - x.before;
      return Math.abs(d) < 0.05 ? `<b>${num1(x.after)}</b>`
        : `<s>${num1(x.before)}</s><i class="fb-chg2-to">→</i><b>${num1(x.after)}</b><em class="${d > 0 ? 'is-up' : 'is-down'}">${d > 0 ? '+' : '−'}${num1(Math.abs(d))}</em>`;
    };
    const cell = (k, v) => `<span class="fb-chg2-cell"><small>${k}</small><span>${v}</span></span>`;
    const tag = ls.mode === 'skipped' ? 'Sin cambios' : ls.mode === 'repriced' ? 'Solo cuotas' : 'Cambios';
    const odds = oi.refreshed ? 'nuevas' : oi.reason === 'cap' ? `${oage} · tope del día` : oage;
    const cells = ls.mode === 'skipped'
      ? cell('Resultado', '<b>idéntico</b>') + cell('Formación', `<b>${esc(ls.lineup.after)}</b>`) + cell('Cuotas', `<b>${odds}</b>`)
      : cell('Formación', arrow(esc(ls.lineup.before), esc(ls.lineup.after)))
        + (ls.mode === 'simulated' ? cell(esc(h.abbr), shot((t.home || {}).shots)) + cell(esc(a.abbr), shot((t.away || {}).shots)) : '')
        + cell('Jugadas', arrow(ls.picks_before, ls.picks_after))
        + cell('Cuotas', `<b>${odds}</b>`);
    const n = (ls.added || []).length + (ls.removed || []).length + (ls.changed || []).length;
    const open = state.chgOpen && state.chgOpen.has(m.event_id);
    const pk = (k) => `<b>${esc(surname(k.player))}</b> ${esc(lineLabel(k.stat, k.line))}${k.price ? ` <span>${esc(bookName(k.book))} ${odd(k.price)}</span>` : ''}`;
    const list = !open || !n ? '' : `
        <ul class="fb-chg2-list">
          ${(ls.added || []).map(k => `<li class="is-add"><i>+</i>${pk(k)}</li>`).join('')}
          ${(ls.removed || []).map(k => `<li class="is-del"><i>−</i>${pk(k)}</li>`).join('')}
          ${(ls.changed || []).map(k => `<li class="is-mod"><i>≈</i>${pk(k)} <small>antes ${odd(k.price_before)}</small></li>`).join('')}
        </ul>`;
    return `
      <div class="fb-chg2 fb-chg2--${esc(ls.mode)} lg-${esc(m.league)}">
        <div class="fb-chg2-bar">
          <span class="fb-chg2-tag">${tag}<small>${esc(ago(ls.at))}</small></span>
          ${cells}
          <span class="fb-chg2-end">
            ${n ? `<button class="fb-chg2-more" data-chg="${esc(m.event_id)}" aria-expanded="${open}">${n} jugada${n === 1 ? '' : 's'} ${open ? '▴' : '▾'}</button>` : ''}
          </span>
        </div>${list}
      </div>`;
  }

  function simControl(m, big = false) {
    const st = simState(m.event_id);
    if (st === 'running') {
      return `<div class="fb-simrun${big ? ' is-big' : ''}" data-simprog="${esc(m.event_id)}">
        <i style="transform:translateX(-100%)"></i><span>${I.play}${state.sim.remote === m.event_id ? 'Simulando en otro dispositivo…' : 'Simulando…'} <b>0</b> de 20.000</span></div>`;
    }
    if (st === 'queued') {
      const n = state.sim.queue.indexOf(m.event_id) + 1;
      return `<div class="fb-simrun is-queued${big ? ' is-big' : ''}"><span>${I.clock}En cola (${n}) · empieza cuando termine la anterior</span></div>`;
    }
    return `<div class="fb-simrow${big ? ' is-big' : ''}">
      <button class="fb-simbtn${big ? '' : ' is-small'}" data-sim="${esc(m.event_id)}">${I.play}<span><b>Simular partido</b><small>20.000 veces con la formación y las cuotas guardadas</small></span></button>
      ${oddsRefreshBtn('ev', m.event_id, 'Actualizar las cuotas de este partido (1 pedido)')}
    </div>`;
  }
  // Botón sólo-ícono que trae cuotas nuevas de un partido (ev) o de una liga entera (lg).
  function oddsRefreshBtn(kind, id, title) {
    const busy = state.oddsScope === `${kind}:${id}`;
    return `<button class="fb-oddsref${busy ? ' is-busy' : ''}" data-oddsref="${kind}:${esc(id)}" title="${esc(title)}" aria-label="${esc(title)}"${state.oddsScope ? ' disabled' : ''}>${I.refresh}</button>`;
  }
  async function refreshOddsScope(key) {
    if (state.oddsScope) return;
    const [kind, id] = key.split(':');
    const lgName = kind === 'lg' ? (leagueOf(id) || {}).name : null;
    const m = kind === 'ev' ? matchById(id) : null;
    const label = kind === 'ev' ? (m ? `${m.teams.home.name} vs ${m.teams.away.name}` : 'el partido') : (lgName || 'la liga');
    state.oddsScope = key; render();
    try {
      const qs = kind === 'ev' ? `event_id=${encodeURIComponent(id)}` : `league=${encodeURIComponent(id)}`;
      const r = await fetch(`${apiBase()}/api/futbol/odds/scope?${qs}`, { method: 'POST' });
      const res = await r.json().catch(() => ({}));
      if (r.status === 409) { toast(`<span><b>Ya se están pidiendo cuotas</b>Esperá a que termine el pedido anterior.</span>`); return; }
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      const u = res.usage || {};
      const left = u.remaining != null ? ` · quedan ${u.remaining} de ${u.limit}` : '';
      if (!res.refreshed) {
        const why = res.reason === 'nofixture' ? `${({ net: 'odds-api.net', optic: 'OpticOdds' })[state.data && state.data.odds && state.data.odds.source] || 'OddsPapi'} no tiene este partido (o ya empezó).`
          : res.reason === 'month' ? 'No quedan pedidos suficientes en el plan del mes.' : 'No se pudieron pedir.';
        toast(`<span><b>Sin cuotas nuevas · ${esc(label)}</b>${why}${left}</span>`);
        return;
      }
      state.data = await fetchJornada('');
      state.ver = null;
      const pk = res.picks || {};
      toast(`${I.check}<span><b>Cuotas nuevas · ${esc(label)}</b>${res.matches} partido${res.matches === 1 ? '' : 's'} · ${res.requests} pedido${res.requests === 1 ? '' : 's'} · ${pk.scope ?? 0} jugada${pk.scope === 1 ? '' : 's'}${left}</span>`);
    } catch (e) {
      toast(`<span><b>No se pudieron pedir cuotas</b>${esc(e.message || String(e))}</span>`);
    } finally {
      state.oddsScope = null; render(); loadUsage(true);
    }
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
      if (i) i.style.transform = `translateX(${(n / 200 - 100).toFixed(1)}%)`;
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
      const oi = res.odds || {};
      const oage = oi.age_s == null ? '' : (oi.age_s < 3600 ? `${Math.max(1, Math.round(oi.age_s / 60))} min` : `${Math.round(oi.age_s / 3600)} h`);
      const otx = oi.refreshed ? ` · cuotas nuevas (${oi.today}/${oi.cap} hoy), jornada repreciada`
        : oi.reason === 'cap' ? ` · cuotas de hace ${oage}: ya se actualizaron ${oi.cap} veces hoy`
        : oage ? ` · cuotas de hace ${oage}` : '';
      if (res.mode === 'skipped') {
        toast(`<span><b>Nada nuevo para simular · ${esc(name)}</b>Misma formación (${esc((res.last_sim || {}).lineup?.after || '')}) y cuotas de hace ${oage || '–'}: el resultado sería idéntico. <u>Ver partido</u></span>`, id);
      } else if (res.mode === 'repriced') {
        toast(`${I.check}<span><b>Jugadas repreciadas · ${esc(name)}</b>La formación no cambió: no hacía falta simular. Con las cuotas nuevas: ${b.picks} → ${a.picks} jugada${a.picks === 1 ? '' : 's'}. <u>Ver partido</u></span>`, id);
      } else {
        toast(`${I.check}<span><b>Simulación lista · ${esc(name)}</b>${lu} · remates ${delta('home')} / ${delta('away')} · ${a.picks} jugada${a.picks === 1 ? '' : 's'}${otx} <u>Ver partido</u></span>`, id);
      }
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
        ${savesBlock(m)}
      </div>`;
  }
  // Atajadas del arquero (Liga MX): lo que ataja cada titular según los remates al
  // arco que la simulación le da al rival. Sin cuota de casa: se muestra la justa.
  function savesBlock(m) {
    const S = m.saves || [];
    if (!S.length) return '';
    const col = (k) => {
      const t = (m.teams || {})[k.side] || {};
      const pic = k.photo ? `<img class="fb-sv-face" src="${esc(photoSrc(k.photo))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : '';
      const rows = (k.lines || []).filter(l => l.line >= 1.5 && l.line <= 5.5).map(l => `
        <li><span>${Math.round(l.line + 0.5)}+</span><i><u style="width:${Math.round(l.p_over * 100)}%"></u></i><b>${Math.round(l.p_over * 100)}%</b><em title="Cuota justa del más de ${String(l.line).replace('.', ',')}; a partir de ahí conviene">${odd(l.fair_over)}</em></li>`).join('');
      return `
        <div class="fb-sv-col">
          <div class="fb-sv-who">${pic}<img class="fb-sv-crest" src="${logo(k.team_id)}" alt="" onerror="this.remove()">
            <span><b>${esc(k.player ? surname(k.player) : 'Arquero')}</b><small>${esc(t.abbr || k.team || '')} · ataja el ${Math.round(k.q * 100)}%</small></span>
            <strong>${num1(k.saves)}<small>atajadas</small></strong></div>
          <ul class="fb-sv-lines">${rows}</ul>
          <p class="fb-sv-note">El rival le remata ${num1(k.opp_sot)} al arco</p>
        </div>`;
    };
    return `
      <div class="fb-sv">
        <div class="fb-sv-head"><b>Atajadas del arquero</b><small>probabilidad y cuota justa · si ataja los 90 minutos</small></div>
        <div class="fb-sv-cols">${S.map(col).join('')}</div>
      </div>`;
  }
  // Lo mejor del partido según la simulación: las jugadas que pagan, lo más
  // seguro. El detalle completo, en el partido.
  // ── Jugadas sharp: las casas no se ponen de acuerdo. Una línea es sharp si
  // entre bet365, Betano y Betsson una paga 0,75 o más por encima de otra, y
  // alguna de las otras la paga por debajo de 2,15 (así no entran las exageradas).
  // Van primero las que más discrepan en probabilidad (1/cuota), así no ganan
  // siempre las improbables de cuota alta. No depende del modelo. Sólo titulares.
  const SHARP_GAP = 0.75;
  const SHARP_REST_MAX = 2.15;
  const SHARP_BOOKS = [['bet365', 'bet365.bet.ar'], ['betano'], ['betsson']];
  function hlStarter(m) {
    const offXI = new Set((m.official_squad || []).filter(s => s.starter).map(s => String(s.player_id)));
    const offTeams = new Set((m.official_squad || []).filter(s => s.starter).map(s => String(s.team_id)));
    return (p) => offTeams.has(String(p.team_id)) ? offXI.has(String(p.player_id))
      : (m.lineup_confirmed ? p.p_start >= 1 : p.p_start >= 0.6);
  }
  const lineKey = (pid, stat, line) => `${pid}|${stat}|${Number(line)}`;
  const RES_PAGE = 30;        // Resultados: de a 30 filas (dibujar las 100 de una trababa el toque en el celu)
  function sharpLines(m) {
    const starter = hlStarter(m);
    const rows = [];
    m.players.filter(starter).forEach(p => (p.lines || []).forEach(l => {
      const q = [];
      SHARP_BOOKS.forEach(ids => {
        const id = ids.find(x => l.books && l.books[x]);
        const b = id && l.books[id];
        if (b && b.price && !b.susp) q.push({ book: id, price: b.price });
      });
      if (q.length < 2) return;
      q.sort((a, b) => b.price - a.price);
      const hi = q[0], lo = q[q.length - 1];
      if (hi.price - lo.price < SHARP_GAP) return;
      // Tope: alguna otra casa tiene que pagarla por debajo de 2,15. Si todas
      // la pagan alto es una jugada improbable, no una cuota regalada.
      if (lo.price >= SHARP_REST_MAX) return;
      rows.push({ p, l, q, hi, lo, gap: hi.price - lo.price, pgap: 1 / lo.price - 1 / hi.price });
    }));
    rows.sort((a, b) => b.pgap - a.pgap);
    const seen = new Set(), out = [];
    out.allKeys = new Set(rows.map(r => lineKey(r.p.player_id, r.l.stat, r.l.line)));   // toda línea sharp, no sólo las 3
    for (const r of rows) {
      if (seen.has(r.p.player_id)) continue;
      seen.add(r.p.player_id);
      out.push(r);
      if (out.length >= 3) break;
    }
    return out;
  }
  // ── Modo parlay: atacantes que rematan, con buenas chances según el modelo
  // (la simulación), en la línea más probable que alguna casa paga cerca o por
  // encima de la cuota justa (1,40 o más).
  // Booster: no todos los partidos lo tienen, así que se activa a mano por
  // partido. Sube la cuota de la casa un 25% (cuota × 1,25) y con eso entran
  // jugadas que sin booster quedaban lejos de la justa.
  const PARLAY_MIN_FAIR = 1.40;
  const PARLAY_MIN_P = 0.38;           // el modelo le da al menos 38%
  const PARLAY_MIN_SHOTS = 0.9;        // remates esperados: trabajador ofensivo
  const PARLAY_MIN_MIN = 55;           // juega buena parte del partido
  const PARLAY_MAX = 6;
  const PARLAY_NEAR = 0.93;            // "cerca": la casa paga al menos el 93% de la justa
  const BOOST = 0.25;
  // Normas del booster: es de Betano y sólo vale si la cuota original es 1,50 o más.
  const BOOST_BOOK = 'betano';
  const BOOST_MIN = 1.50;
  const boosted = (bk, price, mult) => (mult > 1 && bk === BOOST_BOOK && price >= BOOST_MIN ? price * mult : price);
  // La mejor cuota de la línea contando el booster donde corresponde.
  function bestEff(l, mult) {
    let eff = l.best_price || 0;
    quoted(l.books).forEach(b => { const v = boosted(b.id, b.q.price, mult); if (v > eff) eff = v; });
    return eff;
  }
  const PARLAY_ROLES = new Set(['ST', 'WG', 'AM', 'CM']);
  function parlayLines(m) {
    const starter = hlStarter(m);
    const boost = state.hlBoost.has(m.event_id);
    const cands = m.players.filter(starter).filter(p => PARLAY_ROLES.has(p.role) && (p.shots || 0) >= PARLAY_MIN_SHOTS && (p.exp_minutes || 0) >= PARLAY_MIN_MIN);
    // La línea más probable de cada jugador que, con ese multiplicador, queda cerca o encima de la justa.
    const pick = (mult) => {
      const rows = [];
      cands.forEach(p => {
        let best = null;
        (p.lines || []).forEach(l => {
          if (!(l.fair_odds >= PARLAY_MIN_FAIR) || !(l.p_model >= PARLAY_MIN_P) || !l.best_price) return;
          const eff = bestEff(l, mult);
          if (eff < PARLAY_MIN_FAIR || eff < l.fair_odds * PARLAY_NEAR) return;
          if (!best || l.p_model > best.l.p_model) best = { p, l, mult, eff, ratio: eff / l.fair_odds - 1 };
        });
        if (best) rows.push(best);
      });
      return rows;
    };
    const rows = pick(boost ? 1 + BOOST : 1);
    if (boost) {
      const base = new Set(pick(1).map(r => lineKey(r.p.player_id, r.l.stat, r.l.line)));
      rows.forEach(r => { r.unlocked = !base.has(lineKey(r.p.player_id, r.l.stat, r.l.line)); });
    }
    // Sin cuotas de ninguna casa todavía: lo que dice el modelo, para ir mirando.
    const priced = cands.some(p => (p.lines || []).some(l => l.best_price));
    if (!rows.length && !priced) {
      cands.forEach(p => {
        let best = null;
        (p.lines || []).forEach(l => {
          if (!(l.fair_odds >= PARLAY_MIN_FAIR) || !(l.p_model >= PARLAY_MIN_P)) return;
          if (!best || l.p_model > best.l.p_model) best = { p, l, mult: 1, eff: null, ratio: null };
        });
        if (best) rows.push(best);
      });
    }
    rows.sort((a, b) => b.l.p_model - a.l.p_model);
    return rows.slice(0, PARLAY_MAX);
  }
  // Hasta 2 casas que pagan `fair` o más (de mayor a menor, con el multiplicador
  // del booster si corresponde). Si ninguna llega, la que más paga.
  function fairBooks(books, fair, mult = 1, best = null) {
    const all = quoted(books).map(b => ({ book: b.id, price: b.q.price, eff: boosted(b.id, b.q.price, mult) }))
      .sort((a, b) => b.eff - a.eff);
    const good = all.filter(x => x.eff >= fair).slice(0, 2);
    if (good.length) return good;
    if (all.length) return all.slice(0, 1);
    return best && best.book && best.price != null ? [{ book: best.book, price: best.price, eff: boosted(best.book, best.price, mult) }] : [];
  }
  // Redibuja sólo los bloques de Destacados de ese partido (sin rehacer la sección).
  function swapHl(id) {
    const m = matchById(id);
    const boxes = document.querySelectorAll(`.fb-hl[data-hlid="${CSS.escape(id)}"]`);
    if (!m || !boxes.length) { render(); return false; }
    refreshMarks(id);
    const html = highlights(m).trim();
    boxes.forEach(box => {
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      if (tmp.firstElementChild) box.replaceWith(tmp.firstElementChild);
      const fsec = document.getElementById('section-futbol'); if (fsec) fsec._fbHtml = null;   // la página ya no es la del último render
    });
    return true;
  }
  const ZAP_MS = 1600;
  const BOLT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.6 2.2 4.8 13.4h6l-1.4 8.4 8.8-11.2h-6z" fill="currentColor"/></svg>';
  // Borde electrocutado: el contorno del bloque, deformado con ruido que cambia rápido.
  const zapSvg = (id) => {
    const fid = `fbzap-${String(id).replace(/[^a-z0-9]/gi, '')}`;
    return `<svg class="fb-zap" aria-hidden="true">
        <defs><filter id="${fid}" x="-4%" y="-8%" width="108%" height="116%">
          <feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="1" seed="2">
            <animate attributeName="seed" values="2;9;4;12;6;8" dur="0.72s" calcMode="discrete" repeatCount="indefinite"/>
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" scale="8" xChannelSelector="R" yChannelSelector="G"/>
        </filter></defs>
        <g filter="url(#${fid})">
          <rect class="fb-zap-glow" rx="14" ry="14"/>
          <rect class="fb-zap-core" rx="14" ry="14"/>
        </g>
      </svg>`;
  };
  // Rombo cortado en diagonal: dos mitades corridas = dos casas que no coinciden.
  const KNIFE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fb-sh-a" d="M2.5 12 12 2.5l4.2 4.2-9.5 9.5Z" fill="currentColor"/><path class="fb-sh-b" d="M7.8 17.3 17.3 7.8l4.2 4.2-9.5 9.5Z" fill="currentColor"/></svg>';
  // Jugadores marcados en la cancha según la vista de Destacados de ese partido
  // (player_id → estilo): 'pick' en Destacados o en Sharp, 'gold' si la jugada
  // está en las dos, 'zap' en modo parlay.
  function hlMarks(m) {
    const out = new Map();
    if (!m || !m.players) return out;
    const put = (pid, k) => { const id = String(pid); if (out.get(id) !== 'gold') out.set(id, k); };
    if (state.hlParlay.has(m.event_id)) {
      parlayLines(m).forEach(r => put(r.p.player_id, 'zap'));
      return out;
    }
    const items = hlItems(m).filter(it => it.p);
    const sharps = sharpLines(m);
    if (state.hlSharp.has(m.event_id)) {
      const hlKeys = new Set(items.map(it => it.key));
      sharps.forEach(r => put(r.p.player_id, hlKeys.has(lineKey(r.p.player_id, r.l.stat, r.l.line)) ? 'gold' : 'pick'));
    } else {
      items.forEach(it => put(it.p.player_id, sharps.allKeys.has(it.key) ? 'gold' : 'pick'));
    }
    return out;
  }
  const MARK_TXT = { pick: 'destacado', gold: 'destacado y sharp', zap: 'en el parlay' };
  const markCls = (mk, pid) => { const k = mk && mk.get(String(pid)); return k ? ` is-mk-${k}` : ''; };
  const markTitle = (mk, pid) => { const k = mk && mk.get(String(pid)); return k ? ` · ${MARK_TXT[k]}` : ''; };
  // Al cambiar de vista en Destacados: se actualizan los anillos de la cancha
  // de ese partido sin redibujarla.
  function refreshMarks(id) {
    const m = matchById(id);
    if (!m) return;
    const mk = hlMarks(m);
    document.querySelectorAll(`.fb-xp[data-dtpitch="${CSS.escape(id)}"] .fb-xd[data-xpid]`).forEach(el => {
      el.classList.remove('is-mk-pick', 'is-mk-gold', 'is-mk-zap');
      const k = mk.get(el.dataset.xpid);
      if (k && !el.classList.contains('is-edit')) el.classList.add(`is-mk-${k}`);
    });
  }
  // Las jugadas de la vista Destacados: hasta 3 con valor y 2 "lo más firme".
  function hlItems(m) {
    const items = [];
    const pl = (id) => m.players.find(x => x.player_id === id);
    const mp = (state.data.picks || []).filter(k => k.event_id === m.event_id).sort((x, y) => (y.edge || 0) - (x.edge || 0));
    // Hasta 3 jugadas con valor (la tercera ocupa el lugar que antes era el total
    // de remates de un equipo); si no hay, ese lugar queda vacío.
    mp.slice(0, 3).forEach(k => items.push({ kind: 'pick', p: pl(k.player_id), team: teamOf(m, k.team_id),
      title: `${surname(k.player)} · ${lineLabel(k.stat, k.line)}`,
      key: lineKey(k.player_id, k.stat, k.line),
      sub: `${Math.round(k.p_model * 10)}/10 · jugala desde ${odd(k.min_odds)}`, book: k.best_book, price: k.best_price, ok: k.best_price >= k.min_odds,
      edge: k.best_price != null ? k.p_model * k.best_price - 1 : k.edge,
      books: k.books, fair: k.fair_odds, min: k.min_odds,
      pm: k.p_model, stat: k.stat, line: k.line, name: k.player }));
    // "Lo más firme": sólo líneas con una cuota que valga la pena jugar (nada de
    // 1,01): la más probable entre las que alguna casa paga al menos HL_MIN_ODDS.
    const HL_MIN_ODDS = 1.40;
    const sure = [];
    // Sólo titulares (o probables titulares sin formación): la probabilidad es
    // "si juega", y para un suplente eso la infla. Mismo corte que el backend.
    // Si ya llegó la oficial (aunque no se haya vuelto a simular), manda ella.
    const starter = hlStarter(m);
    m.players.filter(starter).forEach(p => p.lines.forEach(l => {
      if (l.p_model >= 0.55 && l.best_price != null && l.best_price >= HL_MIN_ODDS) sure.push({ p, l });
    }));
    sure.sort((x, y) => (y.l.p_model - x.l.p_model) || (y.l.best_price - x.l.best_price));
    const seenP = new Set(mp.map(k => k.player_id));     // un renglón por jugador
    const firm = [];
    for (const x of sure) {
      if (firm.length >= 2 || seenP.has(x.p.player_id)) continue;
      seenP.add(x.p.player_id);
      firm.push(x);
    }
    firm.forEach(({ p, l }) => items.push({ kind: 'sure', p, team: teamOf(m, p.team_id), title: `${surname(p.player)} · ${lineLabel(l.stat, l.line)}`,
        key: lineKey(p.player_id, l.stat, l.line),
        sub: `${Math.round(l.p_model * 10)}/10 · lo más firme con cuota jugable`, book: l.best_book, price: l.best_price, ok: l.best_price >= l.min_odds,
        edge: l.p_model * l.best_price - 1, books: l.books, fair: l.fair_odds, min: l.min_odds,
        pm: l.p_model, stat: l.stat, line: l.line, name: p.player }));
    return items;
  }
  function highlights(m) {
    const items = hlItems(m);
    const sharps = sharpLines(m);
    const parlayOn = state.hlParlay.has(m.event_id);
    if (!items.length && !sharps.length && !parlayOn && !m.players.length) return '';
    const hlKeys = new Set(items.map(it => it.key));
    const shKeys = sharps.allKeys;
    const face = (it) => `<span class="fb-hl-face">${it.p && it.p.photo ? `<img src="${esc(photoSrc(it.p.photo))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}<i>${esc(initials(it.p ? it.p.player : it.title))}</i>
          ${it.team ? `<img class="fb-hl-crest" src="${logo(it.team.team_id)}" alt="" onerror="this.remove()">` : ''}</span>`;
    const flipped = state.hlSharp.has(m.event_id) && !parlayOn;
    const spin = state.hlSpin === m.event_id;
    if (spin) state.hlSpin = null;
    // Mientras está abierto, el borde chisporrotea sin parar. Al redibujarse
    // (p. ej. al llegar la historia) sigue desde donde iba, sin saltos.
    const zap = parlayOn;
    const zapAge = zap && state.hlZap[m.event_id] ? (Date.now() - state.hlZap[m.event_id]) % ZAP_MS : 0;
    const rays = '<i class="fb-ray r1"></i><i class="fb-ray r2"></i><i class="fb-ray r3"></i><i class="fb-ray r4"></i><i class="fb-ray r5"></i>';
    const boostOn = parlayOn && state.hlBoost.has(m.event_id);
    const bolt = `<button class="fb-hl-bolt${parlayOn ? ' is-on' : ''}${boostOn ? ' is-boost' : ''}" data-hlparlay="${esc(m.event_id)}" aria-pressed="${parlayOn}"
        title="${parlayOn ? 'Volver a Destacados' : 'Modo parlay: atacantes que rematan seguido, con cuotas cerca o por encima de la justa'}">${rays}${BOLT}<span>${parlayOn ? 'Destacados' : 'Parlay'}</span></button>`;
    const knife = `<button class="fb-hl-knife${flipped ? ' is-on' : ''}${spin ? ' is-spin' : ''}" data-hlsharp="${esc(m.event_id)}" aria-pressed="${flipped}"
        title="${flipped ? 'Volver a Destacados' : 'Jugadas sharp: donde las casas no se ponen de acuerdo'}">${KNIFE}<span>${flipped ? 'Destacados' : 'Sharp'}</span></button>`;
    const busyShot = state.shotBusy.has(m.event_id);
    const shot = `<button class="fb-hl-shot" data-hlshot="${esc(m.event_id)}" ${busyShot ? 'disabled' : ''}
        title="Descarga una foto de esta lista para compartir"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/></svg><span>${busyShot ? 'Armando…' : 'Foto'}</span></button>`;
    // Botón redondo para sumar la jugada a Mi boleta (Sharp y Parlay).
    const addBtn = (p, l) => {
      const key = pickKey({ event_id: m.event_id, player_id: p.player_id, stat: l.stat, line: l.line });
      const on = state.slip.has(key);
      return `<button class="fb-hl-add${on ? ' is-on' : ''}" data-slip="${esc(key)}" aria-pressed="${on}" aria-label="${on ? 'Sacar de mi boleta' : 'Agregar a mi boleta'}" title="${on ? 'En mi boleta · tocá para sacarla' : 'Agregar a mi boleta'}">${on ? I.check
        : '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'}</button>`;
    };
    const front = `
        <div class="fb-hl-head"><b>Destacados</b>${knife}${bolt}${shot}<small>lo mejor de la simulación · todo en "Ver partido completo"</small></div>
        ${items.length ? items.map(it => `
          <div class="fb-hl-item fb-hl-item--${it.kind}${shKeys.has(it.key) ? ' is-sharp' : ''}">
            ${face(it)}
            <span class="fb-hl-txt"><b>${esc(it.title)}</b><small>${esc(it.sub)}</small></span>
            ${it.edge != null && isFinite(it.edge) ? `<span class="fb-hl-edge${it.edge > 0 ? '' : ' is-neg'}" title="Ventaja sobre la cuota: cuánto paga de más la mejor cuota frente a la justa del modelo"><b>${it.edge > 0 ? '+' : it.edge < 0 ? '−' : ''}${Math.abs(Math.round(it.edge * 100))}%</b><small>ventaja</small></span>` : ''}
            ${it.price != null && it.book ? (() => {
              const list = fairBooks(it.books, it.fair, 1, { book: it.book, price: it.price });
              const boxes = list.map(x => {
                const [tag, attrs, cls] = bookLink(m, x.book, false);
                return `<${tag} class="fb-hl-odd${x.price >= it.min ? ' is-ok' : ''}${cls}"${cls ? attrs : ` title="${esc(bookName(x.book))}"`}>${bookBadge(x.book, 'is-sm')}<b>${odd(x.price)}</b></${tag}>`;
              }).join('');
              return list.length > 1 ? `<span class="fb-hl-books fb-hl-books--fair">${boxes}</span>` : boxes;
            })() : ''}
          </div>`).join('') : '<p class="fb-hl-empty">Sin jugadas con valor en este partido.</p>'}`;
    const back = `
        <div class="fb-hl-head"><b>Sharp</b>${knife}${bolt}${shot}<small>una casa paga 0,75 o más por encima de otra que paga menos de 2,15 · no depende del modelo</small></div>
        ${sharps.length ? sharps.map(r => {
          const key = lineKey(r.p.player_id, r.l.stat, r.l.line);
          const team = teamOf(m, r.p.team_id);
          return `
          <div class="fb-hl-item fb-hl-item--sharp${hlKeys.has(key) ? ' is-sharp' : ''}">
            ${face({ p: r.p, team, title: r.p.player })}
            <span class="fb-hl-txt"><b>${esc(surname(r.p.player))} · ${esc(lineLabel(r.l.stat, r.l.line))}</b><small>${hlKeys.has(key) ? 'también en Destacados · ' : ''}modelo ${Math.round(r.l.p_model * 10)}/10</small></span>
            <span class="fb-hl-corner"><em class="fb-hl-chip fb-hl-chip--gap" title="Diferencia entre la cuota más alta y la más baja"><b>+${r.gap.toFixed(2).replace('.', ',')}</b> diferencia</em></span>
            ${addBtn(r.p, r.l)}
            <span class="fb-hl-books">${r.q.map((x, i) => {
              const [tag, attrs, cls] = bookLink(m, x.book, false);
              return `<${tag} class="fb-hl-odd${i === 0 ? ' is-top' : ''}${cls}"${cls ? attrs : ''}>${bookBadge(x.book, 'is-sm')}<b>${odd(x.price)}</b></${tag}>`;
            }).join('')}</span>
          </div>`;
        }).join('') : '<p class="fb-hl-empty">Ninguna jugada sharp en este partido: las casas pagan parecido.</p>'}`;
    let par = '';
    if (parlayOn) {
      const rows = parlayLines(m);
      const nUnl = rows.filter(r => r.unlocked).length;
      const sgn = (e) => `${e >= 0 ? '+' : '−'}${Math.abs(Math.round(e * 100))}%`;
      par = `
        <div class="fb-hl-head"><b>Parlay</b>${knife}${bolt}${shot}<small>según el modelo · atacantes que rematan · la casa paga cerca o por encima de la cuota justa</small></div>
        <div class="fb-hl-boostrow">
          <button class="fb-hl-boostbtn${boostOn ? ' is-on' : ''}" data-hlboost="${esc(m.event_id)}" role="switch" aria-checked="${boostOn}"
            title="No todos los partidos tienen booster: activalo sólo si Betano lo ofrece en este. Suma 25% a las cuotas de Betano de 1,50 o más"><span>Booster +25%</span><i class="fb-hl-sw" aria-hidden="true"></i></button>
          <small>${boostOn ? `Booster sumado a las cuotas de Betano de 1,50 o más${nUnl ? ` · ${nUnl} jugada${nUnl > 1 ? 's' : ''} que sólo entra${nUnl > 1 ? 'n' : ''} con booster` : ''}` : 'Activalo si Betano ofrece booster en este partido (cuota mínima 1,50): aparecen más jugadas'}</small>
        </div>
        ${rows.length ? rows.map(r => {
            const team = teamOf(m, r.p.team_id), l = r.l;
            const list = r.eff != null ? fairBooks(l.books, l.fair_odds, r.mult, { book: l.best_book, price: l.best_price }) : [];
            const boxes = list.map(x => {
              const [tag, attrs, cls] = bookLink(m, x.book, false);
              return `<${tag} class="fb-hl-odd${x.eff >= l.fair_odds ? ' is-ok' : ''}${cls}"${cls ? attrs : ` title="${esc(bookName(x.book))}${x.eff > x.price ? ` · sin booster ${odd(x.price)}` : ''}"`}>${bookBadge(x.book, 'is-sm')}<b>${odd(x.eff)}</b>${x.eff > x.price ? `<s>${odd(x.price)}</s>` : ''}</${tag}>`;
            }).join('');
            const price = !list.length ? '<span class="fb-hl-nobook">sin cuota de las casas</span>'
              : list.length > 1 ? `<span class="fb-hl-books fb-hl-books--fair">${boxes}</span>` : boxes;
            return `
          <div class="fb-hl-item fb-hl-item--parlay${r.unlocked ? ' is-unlocked' : ''}">
            ${face({ p: r.p, team, title: r.p.player })}
            <span class="fb-hl-txt"><b>${esc(surname(r.p.player))} · ${esc(lineLabel(l.stat, l.line))}</b><small>modelo ${Math.round(l.p_model * 100)}% · ${(r.p.shots || 0).toFixed(1).replace('.', ',')} remates esperados${r.unlocked ? ' · <em class="fb-hl-unl">con booster</em>' : ''}</small></span>
            <span class="fb-hl-corner">
              <em class="fb-hl-chip fb-hl-chip--fair" title="Cuota justa según el modelo">justa <b>${odd(l.fair_odds)}</b></em>
              ${r.ratio != null ? `<em class="fb-hl-chip fb-hl-chip--ratio${r.ratio >= 0 ? ' is-ok' : ''}" title="${r.ratio >= 0 ? 'La casa paga la cuota justa o más' : 'La casa paga un poco menos que la cuota justa'}${r.mult > 1 ? ' (con el booster)' : ''}"><b>${sgn(r.ratio)}</b> ${r.ratio >= 0 ? 'sobre la justa' : 'bajo la justa'}</em>` : ''}
            </span>
            ${price}
            ${addBtn(r.p, r.l)}
          </div>`;
          }).join('') : `<p class="fb-hl-empty">Ningún atacante con buenas chances tiene una cuota cerca de la justa en este partido.${boostOn ? '' : ' Si la casa ofrece booster, activalo arriba.'}</p>`}`;
    }
    return `
      <div class="fb-hl${flipped ? ' is-sharp-view' : ''}${parlayOn ? ' is-parlay-view' : ''}${boostOn ? ' is-boost' : ''}${spin ? ' is-spin' : ''}${zap ? ' is-zap' : ''}" data-hlid="${esc(m.event_id)}"${zap ? ` style="--zap-t:-${zapAge}ms"` : ''}>${parlayOn ? zapSvg(m.event_id) + par : flipped ? back : front}
      </div>`;
  }
  // ── Foto para compartir de Destacados / Sharp / Parlay (cp-shot.js) ──────
  function shotRows(m) {
    const row = (p, name, line, tag, stats, bk, price, good) => {
      const team = p ? teamOf(m, p.team_id) : null;
      return { name, line, tag, stats, book: bk ? bookName(bk) : '', bookLogo: bk ? (bookLogo(bk) || CPShot.BOOK_LOGO[bk]) : '', bookColor: bk ? CPShot.BOOK_COLOR[bk] : null,
        price: price != null ? odd(price) : '—', good, photo: p && p.photo ? photoSrc(p.photo) : '', photoFit: 'cover',
        teamLogo: team ? logo(team.team_id) : '' };
    };
    const sign = (e) => `${e > 0 ? '+' : e < 0 ? '−' : ''}${Math.abs(Math.round(e * 100))}%`;
    const shotBook = (bk, price, good) => ({ book: bookName(bk), bookLogo: bookLogo(bk) || CPShot.BOOK_LOGO[bk], bookColor: CPShot.BOOK_COLOR[bk], price: odd(price), good });
    if (state.hlParlay.has(m.event_id)) {
      return parlayLines(m).map(r => {
        const l = r.l, ok = r.ratio != null && r.ratio >= 0;
        const list = r.eff != null ? fairBooks(l.books, l.fair_odds, r.mult, { book: l.best_book, price: l.best_price }) : [];
        const rw = row(r.p, r.p.player, lineLabel(l.stat, l.line), r.unlocked ? ['CON BOOSTER', '#1d428a'] : null,
          [[pct(l.p_model), 'probabilidad', '#0b1d40'], [odd(l.fair_odds), 'justa', '#64708a'],
            r.ratio == null ? null : [sign(r.ratio), ok ? 'sobre la justa' : 'bajo la justa', ok ? '#15803d' : '#b45309']].filter(Boolean),
          l.best_book, r.eff != null ? r.eff : l.best_price, ok);
        if (list.length) rw.books = list.map(x => shotBook(x.book, x.eff, x.eff >= l.fair_odds));
        return rw;
      });
    }
    if (state.hlSharp.has(m.event_id)) {
      return sharpLines(m).map(r => row(r.p, r.p.player, lineLabel(r.l.stat, r.l.line), null,
        [[`+${r.gap.toFixed(2)}`, 'diferencia', '#b45309'], [odd(r.lo.price), `en ${bookName(r.lo.book)}`, '#64708a']],
        r.hi.book, r.hi.price, true));
    }
    return hlItems(m).map(it => {
      const rw = row(it.p, it.name || it.title, lineLabel(it.stat, it.line),
        it.kind === 'pick' ? ['VALOR', '#1d428a'] : ['FIRME', '#15803d'],
        [[pct(it.pm), 'probabilidad', '#0b1d40'],
          it.edge != null && isFinite(it.edge) ? [sign(it.edge), 'ventaja', it.edge > 0.005 ? '#15803d' : '#64708a'] : null].filter(Boolean),
        it.book, it.price, !!it.ok && it.edge > 0.005);
      const list = it.book && it.price != null ? fairBooks(it.books, it.fair, 1, { book: it.book, price: it.price }) : [];
      if (list.length) rw.books = list.map(x => shotBook(x.book, x.price, x.price >= it.min && it.pm * x.price - 1 > 0.005));
      return rw;
    });
  }
  async function shotHl(id) {
    const m = matchById(id);
    if (!m || !window.CPShot) return;
    const rows = shotRows(m);
    if (!rows.length) return toast('No hay jugadas para la foto en esta vista.', null, m.league);
    const mode = state.hlParlay.has(id) ? 'parlay' : state.hlSharp.has(id) ? 'sharp' : 'hl';
    const TITLE = { hl: 'Destacados', sharp: 'Sharp', parlay: 'Parlay' }[mode];
    const SUBT = { hl: 'Jugadas con valor y lo más firme de la simulación', sharp: 'Una casa paga mucho más que otra por la misma línea',
      parlay: state.hlBoost.has(id) ? 'Atacantes que rematan seguido · cuotas con booster +25%' : 'Atacantes que rematan seguido, con cuotas cerca de la justa' }[mode];
    const L = leagueOf(m.league), h = m.teams.home, a = m.teams.away, d = when(m.date);
    const name = await CPShot.make({
      theme: m.league, league: m.league === 'bra' ? 'assets/bra/brasileirao-mark.png?v=2' : L.logo,
      title: TITLE, subtitle: `${L.name} · ${SUBT}`, sep: 'vs',
      teams: [{ label: h.name, logo: logo(h.team_id) }, { label: a.name, logo: logo(a.team_id) }],
      when: CPShot.whenTxt(d), rows,
      footer: `Modelo propio · remates y tiros al arco simulados · cuotas al ${CPShot.stamp()}`,
      filename: `cherrypick-${CPShot.slug(h.abbr || h.name)}-${CPShot.slug(a.abbr || a.name)}-${TITLE.toLowerCase()}-${d.toLocaleDateString('sv-SE')}.png`,
      shareTitle: `${TITLE} · ${h.name} vs ${a.name}`,
    });
    if (name) toast(`<b>Foto lista</b> ${esc(name)}`, null, m.league);
  }
  function fxPanel(m) {
    return `
      <div class="fb-fxp">
        <div class="fb-fxp-top">
          ${simControl(m)}
          <button class="fb-fxp-open" data-ev="${esc(m.event_id)}">Ver partido completo ${I.back}</button>
        </div>
        ${lastSimBlock(m)}
        ${m.computed_at ? `<div class="fb-simage">${I.clock}Simulado ${esc(ago(m.computed_at))}${m.custom_lineup ? ' · con tu alineación' : m.lineup_confirmed ? ' · con formación confirmada' : ' · con formaciones probables'}</div>` : ''}
        <div class="fb-fxp-grid">
          ${simPreview(m)}
          <div class="fb-fxp-xi">${pitch(m)}</div>
        </div>
      </div>`;
  }

  // ── Recomendados: los partidos donde el modelo ve más valor, mirando a los
  // dos equipos (jugadas de los dos lados, volumen de remates, dominio).
  function recommended() {
    if (!state.data) return [];
    const ms = matches().filter(m => !untilTxt(m.date, m.event_id).past);
    const avg = {};
    ms.forEach(m => { (avg[m.league] ||= []).push(m.total.shots); });
    Object.keys(avg).forEach(k => { const v = avg[k]; avg[k] = v.reduce((a, b) => a + b, 0) / v.length; });
    return ms.map(m => {
      const ks = (state.data.picks || []).filter(k => k.event_id === m.event_id);
      const side = (tid) => ks.filter(k => k.team_id === tid);
      const hp = side(m.teams.home.team_id), ap = side(m.teams.away.team_id);
      const edge = ks.reduce((a, k) => a + Math.max(0, k.edge || 0), 0);
      const both = hp.length && ap.length;
      const extraShots = m.total.shots - (avg[m.league] || m.total.shots);
      const share = m.teams.home.shots / Math.max(0.1, m.teams.home.shots + m.teams.away.shots);
      const dom = Math.abs(share - 0.5);
      const score = edge * 10 + ks.length * 0.6 + (both ? 2.5 : 0) + Math.max(0, extraShots) / 2.5 + (dom > 0.12 ? 1 : 0);
      const reasons = [];
      if (both) reasons.push({ t: `Valor en los dos equipos: ${hp.length} ${m.teams.home.abbr} · ${ap.length} ${m.teams.away.abbr}`, c: 'both' });
      else if (ks.length) reasons.push({ t: `${ks.length} jugada${ks.length > 1 ? 's' : ''} con valor`, c: 'pick' });
      if (extraShots >= 1.5) reasons.push({ t: `Muchos remates: ${num1(m.total.shots)} esperados (+${num1(extraShots)} sobre la liga)`, c: 'vol' });
      if (dom > 0.12) { const t = share > 0.5 ? m.teams.home : m.teams.away; reasons.push({ t: `${t.name} domina: ${Math.round(Math.max(share, 1 - share) * 100)} % de los remates`, c: 'dom' }); }
      return { m, score, reasons };
    }).filter(r => r.score >= 1.5 && r.reasons.length).sort((a, b) => b.score - a.score).slice(0, 10);
  }
  function recsView() {
    const recs = recommended().filter(r => state.league === 'all' || r.m.league === state.league);
    return `
      ${leagueHero(state.league)}
      <header class="fb-sec-head">
        <h2>Recomen<em>dados</em></h2>
        <p>Los partidos donde la simulación encontró más valor: jugadas en los dos equipos, mucho volumen de remates o un equipo que domina.</p>
      </header>
      ${leagueChips()}
      ${recs.length ? `<div class="fb-fx-list">${recs.map(r => fixture(r.m, `<div class="fb-recs">${r.reasons.map(x => `<span class="fb-rec-chip fb-rec-chip--${x.c}">${esc(x.t)}</span>`).join('')}</div>`)).join('')}</div>`
        : '<div class="fb-empty">Por ahora ningún partido se destaca. Mirá todos en <b>Partidos</b>.</div>'}`;
  }

  // ── Partidos: tablero de duelos por día ─────────────────────────────────
  function fixture(m, extra = '') {
    const h = m.teams.home, a = m.teams.away;
    const u = untilTxt(m.date, m.event_id);
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
          <span class="fb-fx-row${fav === 'home' ? ' is-fav' : ''}"><img src="${logo(h.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'"><b title="${esc(h.name)}">${esc(m.league === 'ita' ? h.abbr : h.name)}</b><em>${num1(h.shots)}</em></span>
          <span class="fb-fx-row${fav === 'away' ? ' is-fav' : ''}"><img src="${logo(a.team_id)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'"><b title="${esc(a.name)}">${esc(m.league === 'ita' ? a.abbr : a.name)}</b><em>${num1(a.shots)}</em></span>
        </span>
        <span class="fb-fx-bar" aria-hidden="true"><i style="height:${Math.round(share * 100)}%"></i></span>
        <span class="fb-fx-tags">
          ${luPending(m) ? `<span class="fb-lu fb-lu--new" title="Salió la alineación oficial: tocá Simular">Titulares oficiales</span>` : m.lineup_confirmed ? `<span class="fb-lu fb-lu--ok">Titulares confirmados</span>` : '<span class="fb-lu">Titulares sin confirmar</span>'}
          ${np ? `<span class="fb-fx-np"><b>${np}</b> jugada${np > 1 ? 's' : ''}</span>` : ''}
          ${starBtn(m.event_id)}
        </span>
        ${simState(m.event_id) === 'running' ? `<span class="fb-fx-simbar" data-simprog="${esc(m.event_id)}"><i style="transform:translateX(-100%)"></i></span>` : ''}
      </div>
      ${extra}
      ${state.fxOpen.has(m.event_id) ? fxPanel(m) : ''}
      </div>`;
  }
  function matchesView() {
    const list = matches().filter(m => state.league === 'all' || m.league === state.league)
      .filter(m => !untilTxt(m.date, m.event_id).past);
    const days = new Map();
    list.forEach(m => { const k = dayKey(m.date); if (!days.has(k)) days.set(k, []); days.get(k).push(m); });
    return `
      ${leagueHero(state.league)}
      ${leagueChips()}
      ${[...days.values()].map(ms => `
        <section class="fb-day">
          <div class="fb-day-head"><b>${dayLabel(ms[0].date)}</b><small>${ms.length} partido${ms.length > 1 ? 's' : ''} · remates esperados</small></div>
          <div class="fb-fx-list">${ms.map(m => fixture(m)).join('')}</div>
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
      <header class="fb-sec-head"><h2><em>Historial</em></h2></header>
      ${head}
      ${leagueChips()}
      ${resultsBlock() || (sm && sm.settled ? '<div class="fb-empty">Nada liquidado en esta liga todavía.</div>' : '')}`;
  }

  // ── Rendimiento: ¿las jugadas rinden lo que esperaba el modelo? Y si no,
  //    ¿fue toda la fecha (los jugadores remataron menos) o lo que se elige?
  const pc = (x) => `${Math.round(x * 100)} %`;
  function dayRange(a, b) {
    const [, m1, d1] = a.split('-').map(Number), [, m2, d2] = b.split('-').map(Number);
    if (a === b) return `${d1}/${m1}`;
    return m1 === m2 ? `${d1}⁠–⁠${d2}/${m1}` : `${d1}/${m1}⁠–⁠${d2}/${m2}`;
  }
  const lgOf = (id) => LEAGUES.find(L => L.id === id) || { short: id };
  // Diferencia contra lo esperado: el color dice bueno o malo; gris si casi no se movió.
  function gapCell(g) {
    if (!g || !g.n) return '<td class="fb-fm-num fb-fm-gap"><span class="fb-fm-na">—</span></td>';
    const d = Math.round((g.rate - g.exp) * 100);
    const cls = d <= -3 ? 'down' : d >= 3 ? 'up' : '';
    const odd = g.z != null && Math.abs(g.z) >= 2;
    return `<td class="fb-fm-num fb-fm-gap"><b class="${cls}">${d > 0 ? '+' : d < 0 ? '−' : ''}${Math.abs(d)} ${Math.abs(d) === 1 ? 'pt' : 'pts'}</b><small>${odd ? 'fuera de lo normal' : 'dentro de lo normal'}</small></td>`;
  }
  // Barra: lo que pasó (relleno) contra lo que esperaba el modelo (marca).
  function gapBar(g) {
    if (!g || !g.n) return '<td class="fb-fm-barcell"></td>';
    const w = (x) => `${Math.min(100, (x / 0.7) * 100).toFixed(1)}%`;
    return `<td class="fb-fm-barcell"><span class="fb-fm-bar${g.rate < g.exp - 0.03 ? ' is-under' : ''}" role="img" aria-label="Ganadas ${pc(g.rate)}, esperado ${pc(g.exp)}"><i style="width:${w(g.rate)}"></i><em style="left:${w(g.exp)}"></em></span></td>`;
  }
  function formRow(label, sub, g, unitsVal, cls = '') {
    return `
      <tr class="${cls}">
        <th scope="row"><b>${label}</b>${sub ? `<small>${sub}</small>` : ''}</th>
        <td class="fb-fm-num"><b>${g && g.n ? pc(g.rate) : '—'}</b></td>
        <td class="fb-fm-num fb-fm-exp">${g && g.n ? pc(g.exp) : '—'}</td>
        ${gapBar(g)}
        ${gapCell(g)}
        <td class="fb-fm-num">${unitsVal == null ? '<span class="fb-fm-na">—</span>' : `<b class="${unitsVal >= 0 ? 'up' : 'down'}">${units(unitsVal)}</b>`}</td>
      </tr>`;
  }
  // ══ Rendimiento: rango de fechas a elección (calendario, atajos o fechas),
  //    finde contra entre semana, curva por día, calibración y mercados.
  //    Todo sale de R.rows (una fila por jugada) y R.field_days (toda la
  //    fecha por día y liga).
  const DOW = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const dowOf = (d) => new Date(`${d}T12:00:00Z`).getUTCDay();
  // La fecha de liga va de viernes a lunes; martes a jueves es entre semana (copas).
  const WK_DAYS = [5, 6, 0, 1];
  const isWk = (d) => WK_DAYS.includes(dowOf(d));
  const addDay = (d, n) => { const x = new Date(`${d}T12:00:00Z`); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
  const dm = (d) => { const [, m, dd] = d.split('-').map(Number); return `${dd}/${m}`; };
  const dayName = (d) => `${DOW[dowOf(d)]} ${dm(d)}`;
  const lineTxt = (r) => `${Math.round(r.ln + 0.5)}+ ${r.st === 'shots' ? 'remates' : 'al arco'}`;
  const isDone = (r) => r.s === 'won' || r.s === 'lost';
  const uOf = (r) => (r.s === 'won' ? r.o - 1 : -1);
  const sgn = (u) => (u == null ? '' : u > 0.05 ? 'up' : u < -0.05 ? 'down' : '');
  const uTxt = (u) => (u == null ? '—' : units(u));
  const uShort = (u) => `${u > 0.05 ? '+' : u < -0.05 ? '−' : ''}${Math.abs(u).toFixed(1).replace('.', ',')}`;
  const pcTxt = (x) => `${Math.round(x * 100)}%`;
  const pctSigned = (x) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(Math.round(x * 100))}%`;
  const odd2 = (o) => o.toFixed(2).replace('.', ',');
  const SPLIT_TXT = { all: 'Todos los días', wk: 'Finde', mid: 'Entre semana' };
  const SPLIT_SUB = { all: 'lun a dom', wk: 'vie a lun', mid: 'mar a jue' };
  const PRESETS = [['all', 'Todo'], ['7', '7 días'], ['14', '14 días'], ['30', '30 días'], ['lastwk', 'Último finde'], ['lastmid', 'Último entre semana']];
  state.fm = Object.assign({ pre: 'all', split: 'all', from: null, to: null, month: null }, store.get('fb-fm', {}));
  const fmSave = () => store.set('fb-fm', state.fm);
  const inLg = (lg) => state.league === 'all' || lg === state.league;
  const inSplit = (d, sp = state.fm.split) => sp === 'all' || (sp === 'wk') === isWk(d);
  let fmDays = [], fmCurveData = null, fmLastKey = '', fmDrag = null, fmDragged = false;

  // Ícono: una semanita (L M M J V S D) con los días del grupo encendidos.
  const weekGlyph = (g, cls = '') => `<span class="fb-wkg fb-wkg--${g} ${cls}" aria-hidden="true">${[1, 2, 3, 4, 5, 6, 0].map(d => `<i class="${WK_DAYS.includes(d) ? 'w' : 'm'}"></i>`).join('')}</span>`;

  function agg(rows) {
    const d = rows.filter(isDone), pr = d.filter(r => r.o);
    const n = d.length, won = d.filter(r => r.s === 'won').length;
    const exp = d.reduce((s, r) => s + r.p, 0), sd = Math.sqrt(d.reduce((s, r) => s + r.p * (1 - r.p), 0));
    const u = pr.reduce((s, r) => s + uOf(r), 0);
    return {
      n, won, lost: n - won, rate: n ? won / n : 0, exp: n ? exp / n : 0, z: sd > 0 ? (won - exp) / sd : null,
      units: pr.length ? u : null, eunits: pr.length ? pr.reduce((s, r) => s + r.p * r.o - 1, 0) : null,
      roi: pr.length ? u / pr.length : null,
      open: rows.filter(r => r.s === 'open').length, void: rows.filter(r => r.s === 'void').length,
    };
  }
  function byDay(rows) {
    const by = {};
    for (const r of rows) {
      if (!isDone(r)) continue;
      const x = by[r.d] || (by[r.d] = { u: 0, e: 0, n: 0, w: 0 });
      x.n++; if (r.s === 'won') x.w++;
      if (r.o) { x.u += uOf(r); x.e += r.p * r.o - 1; }
    }
    return by;
  }
  function fieldAgg(R, a, b, sp) {
    let n = 0, w = 0, e = 0, v = 0;
    for (const [d, lg, n1, w1, e1, v1] of R.field_days || []) {
      if (d < a || d > b || !inLg(lg) || !inSplit(d, sp)) continue;
      n += n1; w += w1; e += e1; v += v1;
    }
    return n ? { n, rate: w / n, exp: e / n, z: v > 0 ? (w - e) / Math.sqrt(v) : null } : { n: 0 };
  }
  // Todos los días, de la primera jugada a hoy (también los que no hubo nada).
  function allDays(R) {
    const ds = R.rows.map(r => r.d).sort();
    if (!ds.length) return [];
    const last = ds[ds.length - 1] > R.today ? ds[ds.length - 1] : R.today;
    const out = [];
    for (let d = ds[0]; d <= last && out.length < 800; d = addDay(d, 1)) out.push(d);
    return out;
  }
  // La última tanda (finde o entre semana) con algo liquidado.
  function lastBlock(rows, wk, lastDay) {
    const played = [...new Set(rows.filter(r => isDone(r) && inLg(r.lg)).map(r => r.d))].sort().filter(d => isWk(d) === wk);
    if (!played.length) return null;
    let to = played[played.length - 1], from = to;
    while (isWk(addDay(from, -1)) === wk) from = addDay(from, -1);
    while (isWk(addDay(to, 1)) === wk && addDay(to, 1) <= lastDay) to = addDay(to, 1);
    return [from, to];
  }
  function fmRange(R, days) {
    const F = state.fm, first = days[0], last = days[days.length - 1];
    const clamp = (d) => (d < first ? first : d > last ? last : d);
    const back = (n) => [clamp(addDay(last, -(n - 1))), last];
    if (F.pre === '7') return back(7);
    if (F.pre === '14') return back(14);
    if (F.pre === '30') return back(30);
    if (F.pre === 'lastwk') return lastBlock(R.rows, true, last) || [first, last];
    if (F.pre === 'lastmid') return lastBlock(R.rows, false, last) || [first, last];
    if (F.pre === 'custom' && F.from && F.to) {
      const a = clamp(F.from), b = clamp(F.to);
      return a <= b ? [a, b] : [b, a];
    }
    return [first, last];
  }
  function fmSetRange(a, b) {
    if (a > b) [a, b] = [b, a];
    state.fm = { ...state.fm, pre: 'custom', from: a, to: b };
    fmSave(); render();
  }
  const rangeTxt = (a, b) => (a === b ? dayName(a) : `${dayName(a)} – ${dayName(b)}`);

  function fmFinding(k, f, label) {
    if (!k.n) return `Nada liquidado ${label}. Probá otro rango o sacá algún filtro.`;
    if (k.n < 10) return `Hay ${k.n} jugadas liquidadas ${label}: pocas para sacar conclusiones, pero acá está cómo fueron.`;
    const g = k.rate - k.exp, ff = f && f.n >= 50 ? f.rate - f.exp : null;
    const odd = k.z != null && Math.abs(k.z) >= 2;
    const u = k.units != null ? ` Van ${units(k.units)}.` : '';
    if (g <= -0.05) {
      if (ff != null && ff <= -0.03) return `Rindió por debajo toda la fecha, no solo las jugadas: los jugadores remataron menos de lo previsto.${u}`;
      if (ff != null) return `La fecha salió como esperaba el modelo, pero las jugadas no${odd ? ', y más de lo que explica una mala racha' : ''}. Vale revisar qué se está eligiendo.${u}`;
      return `Las jugadas quedaron abajo de lo que esperaba el modelo${odd ? ', fuera de lo normal' : ', dentro de una mala racha normal'}.${u}`;
    }
    if (g >= 0.05) return `Las jugadas rinden más de lo que esperaba el modelo${odd ? ', bastante más' : ''}.${u}`;
    return `Las jugadas rinden lo que espera el modelo.${u}`;
  }

  // ── Controles: calendario a la izquierda; atajos, fechas y días a la derecha.
  function fmControls(R, days, a, b, rowsLg) {
    const F = state.fm;
    const pre = PRESETS.map(([id, t]) => `<button type="button" data-fmpre="${id}" class="${F.pre === id ? 'is-on' : ''}">${t}</button>`).join('');
    const split = ['all', 'wk', 'mid'].map(id => `
      <button type="button" data-fmsplit="${id}" class="fb-fm-sp fb-fm-sp--${id}${F.split === id ? ' is-on' : ''}" aria-pressed="${F.split === id}">
        ${weekGlyph(id)}<span><b>${SPLIT_TXT[id]}</b><small>${SPLIT_SUB[id]}</small></span>
      </button>`).join('');
    return `
      <section class="fb-fm-ctl" aria-label="Elegir qué mirar">
        ${fmCalendar(R, days, a, b, rowsLg)}
        <div class="fb-fm-side">
          <div class="fb-fm-lab">Rango</div>
          <div class="fb-fm-pre" role="group" aria-label="Rango">${pre}</div>
          <div class="fb-fm-dates">
            <label><span>Desde</span><input type="date" data-fmdate="from" value="${a}" min="${days[0]}" max="${days[days.length - 1]}"></label>
            <label><span>Hasta</span><input type="date" data-fmdate="to" value="${b}" min="${days[0]}" max="${days[days.length - 1]}"></label>
          </div>
          <div class="fb-fm-lab">Días</div>
          <div class="fb-fm-split" role="group" aria-label="Días">${split}</div>
        </div>
      </section>`;
  }
  // Calendario del mes: cada día teñido según lo que ganó (verde) o perdió (rojo).
  // Se arrastra para elegir un rango; un toque elige ese día; Shift+clic estira.
  function fmCalendar(R, days, a, b, rowsLg) {
    const first = days[0], last = days[days.length - 1];
    const months = [...new Set(days.map(d => d.slice(0, 7)))];
    let mon = state.fm.month && months.includes(state.fm.month) ? state.fm.month : b.slice(0, 7);
    if (!months.includes(mon)) mon = months[months.length - 1];
    const mi = months.indexOf(mon);
    const [yy, mm] = mon.split('-').map(Number);
    const start = `${mon}-01`;
    const nDays = new Date(Date.UTC(yy, mm, 0)).getUTCDate();
    const lead = (dowOf(start) + 6) % 7;
    const by = byDay(rowsLg);
    const max = Math.max(1, ...Object.values(by).map(x => Math.abs(x.u)));
    const head = [1, 2, 3, 4, 5, 6, 0].map(d => `<span class="${WK_DAYS.includes(d) ? 'is-wk' : 'is-mid'}">${DOW[d].slice(0, 2)}</span>`).join('');
    let cells = '';
    for (let i = 0; i < lead; i++) cells += '<span class="fb-cal-x"></span>';
    for (let k = 1; k <= nDays; k++) {
      const d = `${mon}-${String(k).padStart(2, '0')}`;
      if (d < first || d > last) { cells += `<span class="fb-cal-x is-out"><small>${k}</small></span>`; continue; }
      const x = by[d], on = d >= a && d <= b;
      const t = x ? Math.round(18 + (Math.abs(x.u) / max) * 62) : 0;
      const tip = x ? `${dayName(d)} · ${isWk(d) ? 'finde' : 'entre semana'}|${x.w} de ${x.n} ganadas · ${units(x.u)}` : `${dayName(d)}|Sin jugadas liquidadas`;
      const edge = on && (d === a ? ' is-a' : '') + (on && d === b ? ' is-b' : '');
      cells += `<button type="button" class="fb-cal-d${x ? (x.u >= 0 ? ' up' : ' down') : ''}${on ? ' is-on' : ''}${edge || ''}${inSplit(d) ? '' : ' is-off'}${d === R.today ? ' is-today' : ''}" data-d="${d}" style="--t:${t}%" data-tip="${esc(tip)}" aria-label="${esc(tip.replace('|', ': '))}" aria-pressed="${on}">
          <small>${k}</small>${x ? `<b>${uShort(x.u)}</b>` : ''}
        </button>`;
    }
    const nav = (dir, ok) => `<button type="button" class="fb-cal-nav" data-fmmon="${ok ? months[mi + dir] : ''}" ${ok ? '' : 'disabled'} aria-label="${dir < 0 ? 'Mes anterior' : 'Mes siguiente'}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${dir < 0 ? 'M14.5 6 8.5 12l6 6' : 'M9.5 6l6 6-6 6'}"/></svg></button>`;
    return `
      <div class="fb-cal">
        <div class="fb-cal-top">
          ${nav(-1, mi > 0)}
          <b>${MONTHS[mm - 1]} <span>${yy}</span></b>
          ${nav(1, mi < months.length - 1)}
        </div>
        <div class="fb-cal-head">${head}</div>
        <div class="fb-cal-grid" data-fmcal>${cells}</div>
        <p class="fb-cal-hint">Arrastrá sobre los días para elegir un rango</p>
      </div>`;
  }

  // ── Curva por día: lo acumulado contra lo que esperaba el modelo, sobre
  //    franjas que dicen si cada día fue de finde o entre semana.
  function fmCurve(rows, a, b) {
    const days = [];
    for (let d = a; d <= b && days.length < 800; d = addDay(d, 1)) days.push(d);
    const by = byDay(rows), D = days.length;
    const cum = [0], ex = [0];
    days.forEach(d => { const x = by[d]; cum.push(cum[cum.length - 1] + (x ? x.u : 0)); ex.push(ex[ex.length - 1] + (x ? x.e : 0)); });
    if (!Object.keys(by).length) return '';
    let lo = Math.min(0, ...cum, ...ex), hi = Math.max(0, ...cum, ...ex);
    const pad = Math.max(0.6, (hi - lo) * 0.1); lo -= pad; hi += pad;
    const W = 1000, H = 260;
    const X = (k) => (k / D) * W, Y = (v) => H - ((v - lo) / (hi - lo)) * H;
    // Cada día cierra en el centro de su franja (arriba de su número).
    const PX = (k) => (k === 0 ? 0 : X(k - 0.5));
    fmCurveData = { days, cum, ex, by, lo, hi };
    const line = (arr) => arr.map((v, k) => `${k ? 'L' : 'M'}${PX(k).toFixed(1)} ${Y(v).toFixed(1)}`).join('') + `L${W} ${Y(arr[arr.length - 1]).toFixed(1)}`;
    const y0 = Y(0);
    const area = `${line(cum)}L${W} ${y0.toFixed(1)}L0 ${y0.toFixed(1)}Z`;
    const bands = days.map((d, k) => `<rect x="${X(k).toFixed(1)}" y="0" width="${(W / D).toFixed(1)}" height="${H}" class="${isWk(d) ? 'bw' : 'bm'}${inSplit(d) ? '' : ' off'}"/>`).join('');
    const step = Math.max(1, Math.ceil(D / 14));
    const labs = days.map((d, k) => {
      if (k % step) return '';
      const ph = (k / step) % 2 ? ' ph-hide' : '';
      return `<span class="fb-fmc-x ${isWk(d) ? 'is-wk' : 'is-mid'}${ph}" style="left:${((k + 0.5) / D * 100).toFixed(2)}%"><b>${+d.slice(8)}</b>${DOW[dowOf(d)].slice(0, 2)}</span>`;
    }).join('');
    const dots = days.map((d, k) => by[d] ? `<i class="fb-fmc-pt ${by[d].u >= 0 ? 'up' : 'down'}" style="left:${(PX(k + 1) / W * 100).toFixed(2)}%;top:${(Y(cum[k + 1]) / H * 100).toFixed(2)}%"></i>` : '').join('');
    const pctY = (v) => `${(Y(v) / H * 100).toFixed(2)}%`;
    const tv = [hi - pad, 0, lo + pad].filter((v, i, s) => s.findIndex(w => Math.abs(w - v) < (hi - lo) * 0.12) === i);
    const tick = (v) => `<span class="fb-fmc-y" style="top:${pctY(v)}">${uShort(v)}</span>`;
    const end = cum[D], endE = ex[D];
    const close = Math.abs(Y(end) - Y(endE)) < 22;
    return `
      <div class="fb-fmc" data-fmcurve role="img" aria-label="Unidades acumuladas por día: ${units(end)}; el modelo esperaba ${units(endE)}">
        <div class="fb-fmc-legend">
          <span class="k-real"><i></i>Unidades acumuladas</span>
          <span class="k-exp"><i></i>Lo que esperaba el modelo</span>
          <span class="k-wk">${weekGlyph('wk', 'is-xs')}Finde</span>
          <span class="k-mid">${weekGlyph('mid', 'is-xs')}Entre semana</span>
        </div>
        <div class="fb-fmc-plot">
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <clipPath id="fbFmAbove"><rect x="0" y="0" width="${W}" height="${y0.toFixed(1)}"/></clipPath>
              <clipPath id="fbFmBelow"><rect x="0" y="${y0.toFixed(1)}" width="${W}" height="${(H - y0).toFixed(1)}"/></clipPath>
            </defs>
            <g class="bands">${bands}</g>
            ${tv.map(v => `<path d="M0 ${Y(v).toFixed(1)}H${W}" class="${v === 0 ? 'zero' : 'grid'}"/>`).join('')}
            <path d="${area}" class="area-up" clip-path="url(#fbFmAbove)"/>
            <path d="${area}" class="area-down" clip-path="url(#fbFmBelow)"/>
            <path d="${line(ex)}" class="exp"/>
            <path d="${line(cum)}" class="real"/>
          </svg>
          ${dots}
          ${tv.map(tick).join('')}
          <b class="fb-fmc-end ${sgn(end)}" style="top:${pctY(end)}">${units(end)}</b>
          ${close ? '' : `<b class="fb-fmc-endx" style="top:${pctY(endE)}">${units(endE)}</b>`}
          <i class="fb-fmc-hl" hidden></i>
        </div>
        <div class="fb-fmc-xs">${labs}</div>
      </div>`;
  }
  // Velocímetro del ROI: de −40 % a +40 %.
  function fmGauge(roi) {
    const v = roi == null ? 0 : Math.max(-0.4, Math.min(0.4, roi));
    const ang = (v / 0.4) * 90;
    const mood = roi == null ? 'Sin cuotas' : roi >= 0.1 ? 'Prendido fuego' : roi >= 0.02 ? 'Calentito' : roi > -0.02 ? 'Tibio' : roi > -0.1 ? 'Fresco' : 'Helado';
    return `
      <div class="fb-fmg" role="img" aria-label="Retorno por jugada ${roi == null ? 'sin datos' : pcTxt(roi)}: ${mood}">
        <svg viewBox="0 0 120 68" aria-hidden="true">
          <path d="M10 62a50 50 0 0 1 30-45.8" class="a-down"/>
          <path d="M44.5 14.5a50 50 0 0 1 31 0" class="a-mid"/>
          <path d="M80 16.2A50 50 0 0 1 110 62" class="a-up"/>
          <g class="needle" style="--a:${ang.toFixed(1)}deg"><path d="M60 62 58 58 60 20 62 58Z"/></g>
          <circle cx="60" cy="62" r="5" class="hub"/>
        </svg>
        <b class="${sgn(roi)}">${roi == null ? '—' : pctSigned(roi)}</b>
        <small>${mood}</small>
      </div>`;
  }
  // Forma: las últimas jugadas, la más nueva a la derecha.
  function fmForm(list) {
    const d = list.filter(isDone);
    const last = d.slice(-14);
    let cur = 0, curS = null;
    for (let i = d.length - 1; i >= 0; i--) { if (curS == null) curS = d[i].s; if (d[i].s !== curS) break; cur++; }
    let best = 0, run = 0;
    for (const r of d) { run = r.s === 'won' ? run + 1 : 0; best = Math.max(best, run); }
    return `
      <div class="fb-fmf">
        <small class="fb-fmf-k">Últimas ${last.length}</small>
        <div class="fb-fmf-balls">${last.map((r, i) => `<span class="fb-fmf-b ${r.s}" style="--i:${i}" data-tip="${esc(`${r.pl} · ${lineTxt(r)}|${r.s === 'won' ? 'Ganó' : 'Perdió'}${r.a != null ? ` (${r.a})` : ''}${r.o ? ` · ${odd2(r.o)}` : ''}|${dayName(r.d)} · ${r.m}`)}"></span>`).join('')}</div>
        <p>${curS ? `<b class="${curS === 'won' ? 'up' : 'down'}">${cur} ${curS === 'won' ? (cur === 1 ? 'ganada' : 'ganadas') : (cur === 1 ? 'perdida' : 'perdidas')}</b> seguidas` : ''}${best ? ` · mejor racha <b>${best}</b>` : ''}</p>
      </div>`;
  }
  // Mini curva para las tarjetas.
  function spark(list) {
    const pr = list.filter(r => isDone(r) && r.o);
    if (pr.length < 2) return '<svg class="fb-fms" viewBox="0 0 100 30"></svg>';
    let u = 0; const pts = [0, ...pr.map(r => (u += uOf(r)))];
    const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), sp = hi - lo || 1;
    const Y = (v) => 28 - ((v - lo) / sp) * 26;
    const d = pts.map((v, i) => `${i ? 'L' : 'M'}${(i / (pts.length - 1) * 100).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
    return `<svg class="fb-fms" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><path d="M0 ${Y(0).toFixed(1)}H100" class="z"/><path d="${d}" class="l"/></svg>`;
  }
  // ── Finde contra entre semana: dos lados de un marcador.
  function fmVersus(rowsR) {
    const W = rowsR.filter(r => isWk(r.d)), M = rowsR.filter(r => !isWk(r.d));
    const a = agg(W), b = agg(M);
    if (!a.n && !b.n) return '';
    const win = a.n && b.n && a.units != null && b.units != null && Math.abs(a.units - b.units) >= 0.5 ? (a.units > b.units ? 'wk' : 'mid') : null;
    const side = (id, g, list) => `
      <button type="button" class="fb-fmv-c fb-fmv-c--${id}${win === id ? ' is-win' : ''}${state.fm.split === id ? ' is-on' : ''}" data-fmsplit="${state.fm.split === id ? 'all' : id}" aria-pressed="${state.fm.split === id}">
        <span class="fb-fmv-t">${weekGlyph(id)}<b>${SPLIT_TXT[id]}</b><small>${SPLIT_SUB[id]}</small>${win === id ? '<em>Va ganando</em>' : ''}</span>
        ${g.n ? `
        <span class="fb-fmv-u ${sgn(g.units)}">${uTxt(g.units)}</span>
        ${spark(list)}
        <span class="fb-fmv-stats">
          <span><b>${g.won}–${g.lost}</b><small>récord</small></span>
          <span><b>${pcTxt(g.rate)}</b><small>ganadas (esp. ${pcTxt(g.exp)})</small></span>
          <span><b class="${sgn(g.roi)}">${g.roi == null ? '—' : pctSigned(g.roi)}</b><small>por jugada</small></span>
        </span>` : '<span class="fb-fmv-none">Nada liquidado en este rango.</span>'}
      </button>`;
    return `
      <section class="fb-fmk fb-fmv" aria-label="Finde contra entre semana">
        <header class="fb-fmk-h"><h3>Finde contra entre semana</h3><p>La fecha de liga (viernes a lunes) contra las noches de copa (martes a jueves). Tocá un lado para filtrar todo por él.</p></header>
        <div class="fb-fmv-row">${side('wk', a, W)}${side('mid', b, M)}</div>
      </section>`;
  }
  // ── ¿Calibra? Lo esperado contra lo que pasó, por franja de probabilidad.
  function fmCalib(rowsR) {
    const d = rowsR.filter(isDone);
    if (d.length < 12) return '';
    const bins = [];
    for (let lo = 0; lo < 1; lo += 0.05) {
      const b = d.filter(r => r.p >= lo && r.p < lo + 0.05);
      if (b.length >= 4) bins.push({ lo, ...agg(b) });
    }
    if (bins.length < 2) return '';
    const vals = bins.flatMap(b => [b.exp, b.rate, b.exp - Math.sqrt(b.exp * (1 - b.exp) / b.n), b.exp + Math.sqrt(b.exp * (1 - b.exp) / b.n)]);
    const A = Math.max(0, Math.floor(Math.min(...vals) * 10) / 10), B = Math.min(1, Math.ceil(Math.max(...vals) * 10) / 10);
    const P = (v) => ((v - A) / (B - A)) * 100;
    const maxN = Math.max(...bins.map(b => b.n));
    const ticks = []; for (let t = A; t <= B + 1e-9; t += 0.1) ticks.push(t);
    const dots = bins.map((b, i) => {
      const sd = Math.sqrt(b.exp * (1 - b.exp) / b.n);
      const sz = 10 + Math.sqrt(b.n / maxN) * 16;
      const tone = b.rate < b.exp - sd ? 'down' : b.rate > b.exp + sd ? 'up' : 'mid';
      const tip = `El modelo daba ${Math.round(b.lo * 100)}–${Math.round(b.lo * 100) + 5}%|Ganaron ${b.won} de ${b.n}: ${pcTxt(b.rate)}|Esperaba ${pcTxt(b.exp)}`;
      return `<i class="fb-fmcal-band" style="left:${P(b.exp)}%;bottom:${P(b.exp - sd)}%;height:${P(b.exp + sd) - P(b.exp - sd)}%"></i>
        <b class="fb-fmcal-dot ${tone}" style="left:${P(b.exp)}%;bottom:${P(b.rate)}%;--s:${sz.toFixed(0)}px;--i:${i}" data-tip="${esc(tip)}"></b>`;
    }).join('');
    return `
      <section class="fb-fmk fb-fmcal">
        <header class="fb-fmk-h"><h3>¿El modelo acierta?</h3><p>Cada pelota es una franja de probabilidad: a la derecha, lo que esperaba; arriba, lo que pasó. Sobre la diagonal es exacto; dentro de la franja gris, dentro de lo normal.</p></header>
        <div class="fb-fmcal-box">
          <div class="fb-fmcal-plot" role="img" aria-label="Calibración por franja de probabilidad">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              ${ticks.map(t => `<path d="M${P(t)} 0V100M0 ${100 - P(t)}H100" class="grid"/>`).join('')}
              <path d="M0 100 100 0" class="diag"/>
            </svg>
            ${dots}
            ${ticks.map(t => `<span class="fb-fmcal-tx" style="left:${P(t)}%">${Math.round(t * 100)}%</span><span class="fb-fmcal-ty" style="bottom:${P(t)}%">${Math.round(t * 100)}%</span>`).join('')}
          </div>
          <div class="fb-fmcal-ax"><span>Esperado →</span><span>↑ Pasó</span></div>
        </div>
      </section>`;
  }
  // ── Por mercado: aros de % ganadas con la marca de lo esperado.
  function fmMarkets(rowsR) {
    const M = [['shots', 0.5], ['shots', 1.5], ['shots', 2.5], ['sot', 0.5], ['sot', 1.5]];
    const tiles = M.map(([st, ln]) => {
      const g = agg(rowsR.filter(r => r.st === st && r.ln === ln));
      if (!g.n) return '';
      const ang = g.exp * 360;
      return `
        <div class="fb-fmm-t" data-tip="${esc(`${lineTxt({ st, ln })}|${g.won} de ${g.n} ganadas · esperaba ${pcTxt(g.exp)}|${uTxt(g.units)}`)}">
          <svg viewBox="0 0 42 42" class="fb-fmm-ring" aria-hidden="true">
            <circle cx="21" cy="21" r="15.915" class="trk"/>
            <circle cx="21" cy="21" r="15.915" class="fil ${g.rate < g.exp - 0.03 ? 'down' : g.rate > g.exp + 0.03 ? 'up' : ''}" style="--v:${(g.rate * 100).toFixed(1)}"/>
            <path d="M33.6 21H39.4" class="mk" style="transform:rotate(${ang.toFixed(1)}deg)"/>
          </svg>
          <b class="fb-fmm-pc">${pcTxt(g.rate)}</b>
          <span class="fb-fmm-l"><b>${Math.round(ln + 0.5)}+</b> ${st === 'shots' ? 'remates' : 'al arco'}</span>
          <small>${g.won} de ${g.n} · <em class="${sgn(g.units)}">${uTxt(g.units)}</em></small>
        </div>`;
    }).join('');
    if (!tiles.trim()) return '';
    return `
      <section class="fb-fmk fb-fmm">
        <header class="fb-fmk-h"><h3>Por mercado</h3><p>El aro es el % ganado; la marquita blanca, lo que esperaba el modelo.</p></header>
        <div class="fb-fmm-row">${tiles}</div>
      </section>`;
  }
  // ── Jugadas contra toda la fecha (¿fallaron las jugadas o la fecha?).
  function fmField(R, k, f, rowsR) {
    const lost = rowsR.filter(r => r.s === 'lost'), p45 = lost.filter(r => (r.min || 0) >= 45).length;
    const band = `${Math.round(R.band[0] * 100)} y ${Math.round(R.band[1] * 100)}%`;
    const bar = (g, lab, sub) => `
      <div class="fb-fmx-r">
        <span class="fb-fmx-n"><b>${lab}</b><small>${sub}</small></span>
        <span class="fb-fm-bar${g.rate < g.exp - 0.03 ? ' is-under' : ''}" role="img" aria-label="Ganadas ${pcTxt(g.rate)}, esperado ${pcTxt(g.exp)}"><i style="width:${Math.min(100, g.rate / 0.7 * 100).toFixed(1)}%"></i><em style="left:${Math.min(100, g.exp / 0.7 * 100).toFixed(1)}%"></em></span>
        <b class="${g.rate < g.exp - 0.03 ? 'down' : g.rate > g.exp + 0.03 ? 'up' : ''}">${pcTxt(g.rate)}</b>
        <small class="fb-fmx-e">esp. ${pcTxt(g.exp)}</small>
      </div>`;
    return `
      <section class="fb-fmk fb-fmx">
        <header class="fb-fmk-h"><h3>¿Las jugadas o la fecha?</h3><p>Toda la fecha son todos los jugadores que el modelo daba entre ${band}, fueran jugada o no. Si también quedan abajo, no fallaron las jugadas sino la fecha.</p></header>
        ${bar(k, 'Jugadas', `${k.n} liquidadas`)}
        ${f.n ? bar(f, 'Toda la fecha', `${f.n} líneas`) : `<p class="fb-note">Toda la fecha se registra desde el ${R.field_since ? dm(R.field_since) : '—'}: no hay datos en este rango.</p>`}
        <p class="fb-fmx-foot"><b>${p45} de ${lost.length}</b> perdidas jugaron 45' o más · ${!lost.length ? 'ninguna perdida' : p45 / lost.length >= 0.85 ? 'no fue la formación' : 'revisar formaciones'}</p>
      </section>`;
  }
  function fmWeeksTable(R) {
    if (!R.periods || !R.periods.length) return '';
    const band = `${Math.round(R.band[0] * 100)} y ${Math.round(R.band[1] * 100)} %`;
    const rows = R.periods.map((p, i) => {
      const r = dayRange(p.from, p.to);
      return formRow(`Jugadas ${r}`, `${p.picks.n} liquidadas${p.picks.void ? ` · ${p.picks.void} nulas` : ''}`, p.picks, p.picks.units, i === 0 ? 'is-now' : '')
        + (p.field.n ? formRow(`Toda la fecha ${r}`, `${p.field.n} líneas entre ${band} · ${p.field_events} partidos`, p.field, null, 'is-field') : '');
    }).join('');
    const T = R.total;
    return `
      <details class="fb-fmk fb-fmt">
        <summary><h3>Semana por semana</h3><span>La tabla completa, todas las ligas</span></summary>
        <div class="fb-fm-scroll">
          <table class="fb-fm-table">
            <thead><tr><th scope="col">Período</th><th scope="col" class="fb-fm-num">Ganadas</th><th scope="col" class="fb-fm-num">Esperado</th><th scope="col" class="fb-fm-barcell"><span class="fb-sr">Comparación</span></th><th scope="col" class="fb-fm-num fb-fm-gap">Diferencia</th><th scope="col" class="fb-fm-num">Unidades</th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot>${formRow('Todo el historial', `${T.picks.n} jugadas liquidadas`, T.picks, T.picks.units)}</tfoot>
          </table>
        </div>
      </details>`;
  }
  function formHead(text) {
    return `<header class="fb-sec-head fb-fm-head"><h2>Rendimiento</h2>${text ? `<p class="fb-fm-finding">${text}</p>` : ''}</header>`;
  }
  function formView() {
    const R = state.report;
    if (!R) {
      return formHead('') + (state.reportErr
        ? `<div class="fb-state fb-state--err"><b>${esc(state.reportErr)}</b><button class="fb-btn" data-act="report">Reintentar</button></div>`
        : loadingView('form'));
    }
    const all = (R.rows || []);
    if (!all.some(isDone)) return formHead('Todavía no hay jugadas liquidadas. Cuando terminen los partidos, acá vas a ver cómo rinden contra lo que esperaba el modelo.');
    fmDays = allDays(R);
    const [a, b] = fmRange(R, fmDays);
    const rowsLg = all.filter(r => inLg(r.lg));
    const rowsR = rowsLg.filter(r => r.d >= a && r.d <= b);       // rango + liga (sin finde/semana)
    const rows = rowsR.filter(r => inSplit(r.d));                  // + finde/semana
    const k = agg(rows), f = fieldAgg(R, a, b, state.fm.split);
    const sp = state.fm.split;
    const label = `${sp === 'wk' ? 'los findes' : sp === 'mid' ? 'entre semana' : ''}${sp !== 'all' ? ' ' : ''}${a === b ? `el ${dayName(a)}` : `del ${dm(a)} al ${dm(b)}`}`;
    const key = JSON.stringify([a, b, sp, state.league]);
    const anim = key !== fmLastKey;
    fmLastKey = key;
    requestAnimationFrame(() => fmAfter(anim));
    const hero = k.n ? `
      <section class="fb-fmhero">
        <div class="fb-fmhero-top">
          <div class="fb-fmhero-big">
            <small>${weekGlyph(sp)}${SPLIT_TXT[sp]} · ${esc(rangeTxt(a, b))}</small>
            <b class="${sgn(k.units)}" data-count="${k.units ?? ''}">${uTxt(k.units)}</b>
            <span><em>${k.won}–${k.lost}</em> · ${pcTxt(k.rate)} ganadas · el modelo esperaba ${pcTxt(k.exp)}${k.open ? ` · ${k.open} por jugarse` : ''}${k.void ? ` · ${k.void} nulas` : ''}</span>
          </div>
          ${fmGauge(k.roi)}
          ${fmForm(rows)}
        </div>
        ${fmCurve(rows, a, b)}
      </section>` : `<div class="fb-empty">Nada liquidado ${esc(label)}.</div>`;
    return `<div class="fb-fm${anim ? ' is-anim' : ''}">`
      + formHead(fmFinding(k, f, label))
      + leagueChips()
      + fmControls(R, fmDays, a, b, rowsLg)
      + hero
      + fmVersus(rowsR)
      + (k.n ? `<div class="fb-fm-grid">${fmCalib(rows)}${fmField(R, k, f, rows)}</div>` : '')
      + (k.n ? fmMarkets(rows) : '')
      + fmWeeksTable(R)
      + '</div>';
  }
  // Después de pintar: contar hasta el número grande (sólo si cambió el filtro).
  function fmAfter(anim) {
    const el = document.querySelector('#section-futbol .fb-fmhero-big [data-count]');
    if (!el || !anim || el.dataset.count === '' || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const to = +el.dataset.count, t0 = performance.now(), dur = 900;
    const step = (t) => {
      const x = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - x, 3);
      el.textContent = units(to * e);
      if (x < 1 && el.isConnected) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  // Globito: cualquier cosa con data-tip, y la curva con su día resaltado.
  function fmTipEl() {
    let t = document.getElementById('fb-fm-tip');
    if (!t) { t = Object.assign(document.createElement('div'), { id: 'fb-fm-tip', className: 'fb-fm-tip', hidden: true }); t.setAttribute('role', 'tooltip'); document.body.appendChild(t); }
    return t;
  }
  function fmTipShow(html, x, y) {
    const t = fmTipEl();
    t.innerHTML = html; t.hidden = false;
    const w = t.offsetWidth, h = t.offsetHeight;
    let L = x + 14, T = y + 16;
    if (L + w > innerWidth - 8) L = x - w - 14;
    if (T + h > innerHeight - 8) T = y - h - 12;
    t.style.left = `${Math.max(8, L)}px`; t.style.top = `${Math.max(8, T)}px`;
  }
  function fmTipHide() { const t = document.getElementById('fb-fm-tip'); if (t) t.hidden = true; }
  function onFmHover(e) {
    if (state.view !== 'form' || fmDrag) return;
    const tgt = e.target;
    const plot = tgt.closest && tgt.closest('[data-fmcurve] .fb-fmc-plot');
    if (plot && fmCurveData) {
      const { days, cum, ex, by } = fmCurveData, D = days.length;
      const rc = plot.getBoundingClientRect();
      const k = Math.max(0, Math.min(D - 1, Math.floor(((e.clientX - rc.left) / rc.width) * D)));
      const d = days[k], x = by[d];
      const hl = plot.querySelector('.fb-fmc-hl');
      hl.hidden = false;
      hl.style.left = `${(k / D) * 100}%`; hl.style.width = `${100 / D}%`;
      fmTipShow(`<b>${dayName(d)} · ${isWk(d) ? 'finde' : 'entre semana'}</b>${x ? `<span>${x.w} de ${x.n} ganadas · <em class="${sgn(x.u)}">${units(x.u)}</em></span>` : '<span>Sin jugadas liquidadas</span>'}<span>Acumulado <em class="${sgn(cum[k + 1])}">${units(cum[k + 1])}</em> · modelo ${units(ex[k + 1])}</span>`, e.clientX, e.clientY);
      return;
    }
    document.querySelectorAll('#section-futbol .fb-fmc-hl').forEach(x => { x.hidden = true; });
    const el = tgt.closest && tgt.closest('#section-futbol [data-tip]');
    if (!el) return fmTipHide();
    const [h, ...rest] = el.dataset.tip.split('|');
    fmTipShow(`<b>${esc(h)}</b>${rest.map(x => `<span>${esc(x)}</span>`).join('')}`, e.clientX, e.clientY);
  }
  // Calendario: arrastrar para elegir.
  function fmDayAt(box, x, y) {
    for (const c of box.querySelectorAll('.fb-cal-d')) {
      const r = c.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return c.dataset.d;
    }
    return null;
  }
  function fmPaint() {
    if (!fmDrag) return;
    const [lo, hi] = fmDrag.d0 <= fmDrag.d1 ? [fmDrag.d0, fmDrag.d1] : [fmDrag.d1, fmDrag.d0];
    fmDrag.box.querySelectorAll('.fb-cal-d').forEach(c => {
      const d = c.dataset.d;
      c.classList.toggle('is-on', d >= lo && d <= hi);
      c.classList.toggle('is-a', d === lo);
      c.classList.toggle('is-b', d === hi);
    });
  }
  function onFmDown(e) {
    const cell = e.target.closest('[data-fmcal] .fb-cal-d');
    if (!cell || e.button !== 0 || e.shiftKey) return;
    const box = cell.closest('[data-fmcal]');
    fmDrag = { box, d0: cell.dataset.d, d1: cell.dataset.d };
    fmDragged = false;
    try { box.setPointerCapture(e.pointerId); } catch (_) { /* sin captura */ }
    fmTipHide();
  }
  function onFmMove(e) {
    if (!fmDrag) return;
    const d = fmDayAt(fmDrag.box, e.clientX, e.clientY);
    if (d && d !== fmDrag.d1) { fmDrag.d1 = d; fmDragged = true; fmPaint(); }
  }
  function onFmUp() {
    if (!fmDrag) return;
    const { d0, d1 } = fmDrag;
    fmDrag = null;
    if (fmDragged) fmSetRange(d0, d1);
  }
  // Clics de la pantalla (devuelve true si los atendió).
  function fmClick(e, t) {
    const pre = t.closest('[data-fmpre]');
    if (pre) { state.fm = { ...state.fm, pre: pre.dataset.fmpre, month: null }; fmSave(); render(); return true; }
    const sp = t.closest('[data-fmsplit]');
    if (sp) { state.fm = { ...state.fm, split: sp.dataset.fmsplit }; fmSave(); render(); return true; }
    const mo = t.closest('[data-fmmon]');
    if (mo) { if (mo.dataset.fmmon) { state.fm = { ...state.fm, month: mo.dataset.fmmon }; fmSave(); render(); } return true; }
    const cell = t.closest('[data-fmcal] .fb-cal-d');
    if (cell) {
      if (fmDragged) { fmDragged = false; return true; }
      const d = cell.dataset.d;
      if (e.shiftKey && state.report) {
        const [a] = fmRange(state.report, fmDays);
        fmSetRange(a, d);
      } else fmSetRange(d, d);
      return true;
    }
    return false;
  }
  function onFmDate(e) {
    const inp = e.target.closest('[data-fmdate]');
    if (!inp || !inp.value || !state.report) return;
    const [a, b] = fmRange(state.report, fmDays);
    state.fm.month = null;
    if (inp.dataset.fmdate === 'from') fmSetRange(inp.value, b); else fmSetRange(a, inp.value);
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
          <span class="fb-ws-face">${p.photo ? `<img src="${esc(photoSrc(p.photo))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ''}<i>${esc(initials(p.player))}</i></span>
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
      const y = ln === 0 ? 88 : 66 - i * (54 / Math.max(1, outfield.length - 1));
      row.forEach((p, j) => out.push({ p, x: 6 + ((j + 0.5) / row.length) * 88, y, n: row.length }));
    });
    return out;
  }
  function xiDot({ p, x, y, n }, m, confirmed, mk) {
    const mp = m.players.find(q => q.player_id === p.player_id);
    const shots = p.line === 0 ? 'AR' : mp ? num1(mp.shots) : '–';
    const ps = Math.round((p.p_start || 0) * 100);
    return `
      <div class="fb-xd${p.doubt ? ' is-doubt' : ''}${mp && mp.shots >= 1.5 ? ' is-hot' : ''}${n >= 5 ? ' is-tight' : ''}${markCls(mk, p.player_id)}" data-xpid="${esc(p.player_id)}" style="left:${x.toFixed(1)}%;top:${y.toFixed(1)}%;--n:${n}"
           title="${esc(p.player)} · ${esc(p.pos || '')} · ${shots} remates esperados${confirmed ? '' : ` · titular ${ps}%`}${markTitle(mk, p.player_id)}">
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

  // ════════════════════════════════════════════════════════════════════
  //  Cargar alineación: una foto de la formación (placa del club, captura de
  //  la tele) → Claude la lee → se revisa en la cancha (tocar para cambiar un
  //  jugador, arrastrar para moverlo) → se confirma y cuenta como oficial.
  // ════════════════════════════════════════════════════════════════════
  const FORMATIONS = ['4-3-3', '4-4-2', '4-2-3-1', '4-1-4-1', '4-4-1-1', '4-3-1-2', '3-5-2', '3-4-3', '3-4-2-1', '5-3-2', '5-4-1'];
  const LOW_CONF = 0.7;                    // debajo de esto el jugador se marca para revisar
  // Puesto (estilo ESPN) según dónde está en la cancha: así el modelo sabe su rol.
  function lineFromY(y) { return y >= 80 ? 0 : y >= 60 ? 1 : y >= 49 ? 2 : y >= 37 ? 3 : y >= 23 ? 4 : 5; }
  function posFrom(line, x) {
    if (line === 0) return 'G';
    if (line === 1) return x < 28 ? 'LB' : x > 72 ? 'RB' : x < 50 ? 'CD-L' : 'CD-R';
    if (line === 2) return 'DM';
    if (line === 3) return x < 30 ? 'LM' : x > 70 ? 'RM' : 'CM';
    if (line === 4) return x < 35 ? 'AM-L' : x > 65 ? 'AM-R' : 'AM';
    return x < 30 ? 'LF' : x > 70 ? 'RF' : 'F';
  }
  const reviewing = (eid) => !!(state.photo && state.photo.eid === eid && state.photo.draft);
  const isEditing = (eid, side) => !!(state.dtEdit && state.dtEdit.eid === eid && state.dtEdit.side === side && state.dtDraft);
  // Jugadores del XI mostrado con su lugar en la cancha (x, y en %).
  function shownXI(m, side) {
    if (isEditing(m.event_id, side)) return { formation: state.dtDraft.formation, players: state.dtDraft.players, custom: true };
    const xi = (m.lineup || {})[side];
    if (!xi) return null;
    const placed = placeXI(xi).map(d => (m.league === 'eng' ? persp(d) : d));
    return { formation: xi.formation, players: placed.map(({ p, x, y, n }) => ({ ...p, x, y, n })), custom: false };
  }
  function applyFormation(draft, f, tilt) {
    const rows = f.split('-').map(Number);
    const ps = draft.players.slice();
    let gk = ps.find(p => p.line === 0) || ps.slice().sort((a, b) => b.y - a.y)[0];
    const out = ps.filter(p => p !== gk).sort((a, b) => (b.y - a.y) || (a.x - b.x));   // de atrás hacia adelante
    const lines = rows.length === 3 ? [1, 3, 5] : rows.length === 4 ? [1, 2, 4, 5] : [1, 2, 3, 4, 5];
    const res = [{ ...gk, line: 0, x: 50, y: 88, pos: 'G' }];
    let k = 0;
    rows.forEach((n, i) => {
      const row = out.slice(k, k + n).sort((a, b) => a.x - b.x); k += n;
      const y = 70 - i * (56 / Math.max(1, rows.length - 1));
      row.forEach((p, j) => {
        let x = 8 + ((j + 1) / (n + 1)) * 84;
        if (tilt) x = persp({ x, y }).x;
        res.push({ ...p, line: lines[i], x, y, pos: posFrom(lines[i], x), n });
      });
    });
    // Mismo objeto: el borrador de cada equipo se sigue editando al cambiar de pestaña.
    return Object.assign(draft, { formation: f, players: res });
  }
  // Dibujo según dónde quedaron los jugadores (defensa, 5, medio, enganche, delanteros).
  function formFromLines(players) {
    const n = [1, 2, 3, 4, 5].map(ln => players.filter(p => p.line === ln).length).filter(Boolean);
    return n.reduce((a, b) => a + b, 0) === 10 ? n.join('-') : null;
  }
  const isLow = (p) => p.known === false || (p.confidence != null && p.confidence < LOW_CONF);
  function photoTitle(p) {
    const seen = p.name_in_image && p.name_in_image !== p.player ? ` · en la foto: "${p.name_in_image}"` : '';
    const why = p.manual ? ' · cargado a mano (sin historia: se usa el promedio de su puesto)' : p.known === false ? ' · no lo encontré en el plantel' : isLow(p) ? ' · no estoy seguro' : '';
    return `${p.player}${seen}${why}${p.pos_from_image === false ? ' · puesto deducido' : ''}`;
  }
  function dtDot(p, m, editing, mk) {
    const mp = m.players.find(q => q.player_id === p.player_id);
    const shots = p.line === 0 ? 'AR' : mp ? num1(mp.shots) : '–';
    const mark = editing ? null : mk;          // mientras se edita, sin anillos
    return `
      <div class="fb-xd fb-xd--dt${editing ? ' is-edit' : ''}${mp && mp.shots >= 1.5 ? ' is-hot' : ''}${(p.n || 0) >= 5 ? ' is-tight' : ''}${isLow(p) ? ' is-low' : ''}${p.known === false ? ' is-unknown' : ''}${state.dtMenu === p.player_id && editing ? ' is-picked' : ''}${markCls(mark, p.player_id)}"
           data-dtp="${esc(p.player_id)}" data-xpid="${esc(p.player_id)}" style="left:${Number(p.x).toFixed(1)}%;top:${Number(p.y).toFixed(1)}%"
           title="${esc(photoTitle(p))}${editing ? ' · tocá para cambiarlo o arrastralo' : markTitle(mark, p.player_id)}">
        <span class="fb-xd-n">${isLow(p) ? '?' : shots}</span>
        <b>${esc(surname(p.player))}</b>
      </div>`;
  }
  // Para cambiar a un jugador: primero los suplentes que trajo la foto, después el plantel.
  function dtCandidates(m, side, current) {
    const tid = m.teams[side].team_id;
    const draft = state.dtDraft;
    const inXI = new Set(draft.players.map(p => p.player_id));
    const info = new Map(m.players.filter(p => p.team_id === tid).map(p => [p.player_id, p]));
    const pool = new Map();
    const add = (p, bench) => {
      if (!p || !p.player_id || pool.has(p.player_id) || inXI.has(p.player_id)) return;
      if (String(p.player_id).startsWith('foto:') && !bench) return;
      const mi = info.get(p.player_id) || {};
      pool.set(p.player_id, { player_id: p.player_id, player: mi.player || p.player, pos: mi.pos || p.pos || '', role: mi.role,
                              shots: mi.shots || 0, photo: mi.photo, bench, known: p.known !== false });
    };
    (draft.bench || []).forEach(p => add(p, true));
    (((state.photo || {}).squad || {})[side] || []).forEach(p => add(p, false));
    info.forEach(p => add(p, false));
    ((m.keepers || {})[side] || []).forEach(p => add({ ...p, pos: 'G' }, false));
    const cur = draft.players.find(p => p.player_id === current);
    const wantGK = cur && cur.line === 0;
    return [...pool.values()]
      .sort((a, b) => (b.bench - a.bench) || ((b.pos === 'G') === wantGK) - ((a.pos === 'G') === wantGK) || (b.shots || 0) - (a.shots || 0))
      .slice(0, 30);
  }
  function dtMenu(m, side) {
    const cur = state.dtDraft.players.find(p => p.player_id === state.dtMenu);
    if (!cur) return '';
    const list = dtCandidates(m, side, cur.player_id);
    const left = Math.min(70, Math.max(30, cur.x));
    const at = cur.y > 50 ? `bottom:${(100 - cur.y + 5).toFixed(1)}%` : `top:${(cur.y + 5).toFixed(1)}%`;
    return `
      <div class="fb-dt-menu" style="left:${left}%;${at}">
        <div class="fb-dt-menu-head"><b>${cur.name_in_image && cur.name_in_image !== cur.player ? `"${esc(cur.name_in_image)}" en la foto` : `Cambiar a ${esc(surname(cur.player))}`}</b><button data-dt="close" aria-label="Cerrar">${ICON('xmark')}</button></div>
        <div class="fb-dt-menu-list">
          ${list.length ? list.map(c => `
            <button class="fb-dt-opt" data-dtpick="${esc(c.player_id)}">
              <span class="fb-dt-face">${c.photo ? `<img src="${esc(photoSrc(c.photo))}" alt="" onerror="this.remove()">` : ''}<i>${esc(initials(c.player))}</i></span>
              <span class="fb-dt-opt-txt"><b>${esc(c.player)}</b><small>${c.bench ? 'En el banco · ' : ''}${esc(roleName(c) || (c.pos === 'G' ? 'Arquero' : 'Plantel'))}${c.shots ? ` · ${num1(c.shots)} rem.` : ''}</small></span>
            </button>`).join('') : '<p class="fb-dt-empty">No hay más jugadores del plantel.</p>'}
        </div>
        <form class="fb-dt-add" data-dtaddform>
          <input type="text" data-dtname placeholder="¿No está? Escribí el nombre" aria-label="Nombre del jugador que no está en la lista" autocomplete="off" maxlength="40">
          <button type="submit" data-dtadd>Poner</button>
        </form>
      </div>`;
  }
  // Cambiar al jugador marcado por otro. Si viene del banco, el que sale va al banco.
  function dtPick(pid) {
    const draft = state.dtDraft, cur = draft && draft.players.find(p => p.player_id === state.dtMenu);
    const m = cur && matchById(state.dtEdit.eid);
    const c = m && dtCandidates(m, state.dtEdit.side, cur.player_id).find(x => x.player_id === pid);
    if (cur && c) {
      const out = { player_id: cur.player_id, player: cur.player, known: cur.known, name_in_image: cur.name_in_image, confidence: cur.confidence };
      draft.bench = (draft.bench || []).filter(b => b.player_id !== c.player_id);
      if (c.bench && cur.known !== false) draft.bench.push(out);
      Object.assign(cur, { player_id: c.player_id, player: c.player, known: true, confidence: 1, name_in_image: null });
    }
    state.dtMenu = null;
  }
  // Un jugador que no está en ninguna fuente (juvenil, refuerzo nuevo): se carga
  // por nombre. El modelo no tiene su historia y usa el promedio de su puesto.
  function dtAddByName(name) {
    name = String(name || '').trim().replace(/\s+/g, ' ');
    const draft = state.dtDraft, cur = draft && draft.players.find(p => p.player_id === state.dtMenu);
    if (!name || !cur) return;
    const key = name.toLowerCase().normalize('NFD').replace(/\p{M}/gu, '').replace(/[^a-z0-9]+/g, '-');
    const pid = `foto:${key}`;
    if (draft.players.some(p => p.player_id === pid)) { toast(`<span><b>${esc(name)} ya está en el equipo</b></span>`); return; }
    draft.bench = (draft.bench || []).filter(b => b.player_id !== pid);
    if (cur.known !== false) draft.bench.push({ player_id: cur.player_id, player: cur.player, known: cur.known, confidence: cur.confidence });
    Object.assign(cur, { player_id: pid, player: name, known: true, manual: true, confidence: 1, name_in_image: null });
    state.dtMenu = null;
  }
  function benchList(m, d) {
    if (!d || !(d.bench || []).length) return `<p class="fb-ph-bench-empty">${d && d.bench_src ? 'No hay suplentes de partidos anteriores.' : 'La foto no trae suplentes.'}</p>`;
    const from = { historia: 'según los últimos partidos', oficial: 'oficial', tuyo: 'el tuyo' }[d.bench_src];
    return `<div class="fb-ph-bench"><b>Suplentes${from ? ` · ${from}` : ''}</b>${d.bench.map(p => `<span class="${isLow(p) ? 'is-low' : ''}" title="${esc(photoTitle(p))}">${esc(p.player)}</span>`).join('')}</div>`;
  }
  // Achica la foto en el teléfono antes de mandarla (más rápido, y Claude no necesita más).
  async function shrinkImage(file, max = 1600) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, ko) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => ko(new Error('No pude abrir la imagen')); i.src = url; });
      const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.round(img.naturalWidth * k); c.height = Math.round(img.naturalHeight * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      return c.toDataURL('image/jpeg', 0.88);
    } finally { URL.revokeObjectURL(url); }
  }
  function scrollToPitch(eid) {
    setTimeout(() => {
      const xp = document.querySelector(`[data-dtpitch="${CSS.escape(eid)}"]`);
      if (xp) xp.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 60);
  }
  // Pone en revisión un borrador del servidor (de la foto o del Modo DT).
  function openDraft(eid, res, mode, m0) {
    const m = matchById(eid) || m0;
    const draft = {};
    ['home', 'away'].forEach(sd => {
      const d = (res.draft || {})[sd];
      if (!d || !d.detected) return;
      const placed = placeXI({ players: d.players }).map(x => (m && m.league === 'eng' ? persp(x) : x));
      draft[sd] = { formation: d.formation || 'Libre', formation0: d.formation, bench: d.bench || [], bench_src: d.bench_src,
                    players: placed.map(({ p, x, y, n }) => ({ ...p, x, y, n })) };
    });
    state.photo = { eid, mode, draft, squad: res.squad || {}, notes: res.notes };
    const want = state.xiSides[eid];
    const sd = draft[want] ? want : (draft.home ? 'home' : 'away');
    state.xiSides[eid] = sd;
    state.dtEdit = { eid, side: sd }; state.dtDraft = draft[sd];
    return draft;
  }
  // Modo DT: la alineación que se ve ahora, para cambiarla a mano. El banco lo
  // arma el servidor (el oficial, o los suplentes de los últimos partidos).
  async function openDT(eid) {
    if (state.photo && (state.photo.busy || state.photo.saving)) return;
    state.photo = { eid, busy: true, mode: 'dt' };
    state.dtEdit = null; state.dtDraft = null; state.dtMenu = null;
    render();
    try {
      const r = await fetch(`${apiBase()}/api/futbol/lineup/dt?event_id=${encodeURIComponent(eid)}`);
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      openDraft(eid, res, 'dt');
    } catch (e) {
      state.photo = null;
      toast(`<span><b>No se pudo abrir el Modo DT</b>${esc(e.message || String(e))}</span>`);
    }
    render();
    scrollToPitch(eid);
  }
  async function readPhoto(eid, file) {
    if (!file || (state.photo && (state.photo.busy || state.photo.saving))) return;
    const m0 = matchById(eid);
    state.photo = { eid, busy: true, mode: 'foto' };
    state.dtEdit = null; state.dtDraft = null; state.dtMenu = null;
    render();
    try {
      const image = await shrinkImage(file);
      const r = await fetch(`${apiBase()}/api/futbol/lineup/photo`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event_id: eid, image }) });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      const draft = openDraft(eid, res, 'foto', m0);
      const low = Object.values(draft).reduce((n, d) => n + d.players.filter(isLow).length, 0);
      toast(`${I.check}<span><b>Foto leída</b>${low ? `Revisá ${low} jugador${low === 1 ? '' : 'es'} marcado${low === 1 ? '' : 's'} con "?" y confirmá` : 'Revisala en la cancha y confirmá'}</span>`);
    } catch (e) {
      state.photo = null;
      toast(`<span><b>No se pudo leer la foto</b>${esc(e.message || String(e))}</span>`);
    }
    render();
    scrollToPitch(eid);
  }
  async function confirmPhoto(eid) {
    const ph = state.photo;
    if (!reviewing(eid) || ph.saving) return;
    const sides = {};
    Object.entries(ph.draft).forEach(([sd, d]) => {
      const players = d.players.slice().sort((a, b) => (a.line === 0 ? -1 : b.line === 0 ? 1 : 0) || (b.y - a.y) || (a.x - b.x))
        .map(p => ({ player_id: p.player_id, player: p.player, pos: p.line === 0 ? 'G' : p.pos }));
      sides[sd] = { formation: (d.formation && d.formation !== 'Libre') ? d.formation : (formFromLines(d.players) || d.formation0), players,
                    bench: (d.bench || []).map(b => ({ player_id: b.player_id, player: b.player })) };
    });
    ph.saving = true; render();
    try {
      const r = await fetch(`${apiBase()}/api/futbol/lineup/photo/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event_id: eid, sides, src: ph.mode === 'dt' ? 'dt' : 'foto' }) });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      state.data = await fetchJornada('');
      state.ver = null;
      state.photo = null; state.dtEdit = null; state.dtDraft = null; state.dtMenu = null;
      const m = matchById(eid);
      const names = Object.keys(sides).map(sd => (m && m.teams[sd].abbr) || sd).join(' y ');
      toast(`${I.check}<span><b>Alineación cargada · ${esc(names)}</b>${ph.mode === 'dt' ? 'Tu alineación del Modo DT' : 'La de la foto'} cuenta como oficial. Tocá Simular partido para recalcular con ella</span>`, eid);
    } catch (e) {
      ph.saving = false;
      toast(`<span><b>No se pudo guardar la alineación</b>${esc(e.message || String(e))}</span>`);
    }
    render();
  }
  async function discardPhoto(eid) {
    try {
      const r = await fetch(`${apiBase()}/api/futbol/lineup/photo/discard?event_id=${encodeURIComponent(eid)}`, { method: 'POST' });
      const res = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(res.detail || `HTTP ${r.status}`);
      state.data = await fetchJornada('');
      state.ver = null;
      toast(`${I.refresh}<span><b>Se sacó tu alineación</b>${res.reverted ? 'Vuelve la probable: tocá Simular partido para recalcular' : 'Vuelve la de ESPN / PitchAPI'}</span>`, eid);
    } catch (e) {
      toast(`<span><b>No se pudo deshacer</b>${esc(e.message || String(e))}</span>`);
    }
    render();
  }
  const photoBtn = (eid, label = 'Cargar alineación', cls = 'fb-dt-toggle') => {
    const busy = state.photo && state.photo.eid === eid && state.photo.busy;
    return busy ? `<span class="${cls} is-busy" aria-live="polite">${I.refresh}${state.photo.mode === 'dt' ? 'Armando el banco…' : 'Leyendo la foto…'}</span>`
      : `<label class="${cls}">${I.camera}${esc(label)}<input type="file" accept="image/*" data-photo="${esc(eid)}" hidden></label>`;
  };
  const dtBtn = (eid) => (state.photo && state.photo.eid === eid && state.photo.busy) ? ''
    : `<button class="fb-dt-ico" data-dt="dt" data-dtev="${esc(eid)}" aria-label="Modo DT: armar la alineación a mano" title="Modo DT: armar la alineación a mano">${I.tactics}</button>`;
  const MANUAL_SRC = { foto: 'tu foto', dt: 'Modo DT' };
  const manualSrc = (m) => Object.values(m.lineup_sources || {}).find(v => MANUAL_SRC[v]) || null;
  // Puesto raro: titulares de la formación oficial que hoy están donde casi no
  // juegan (otro puesto u otro lado). Aviso para corregirla con una foto.
  const posAlertsOf = (m) => (m && Array.isArray(m.pos_alerts)) ? m.pos_alerts : [];
  const posAlertFor = (m, pid) => posAlertsOf(m).find(a => String(a.player_id) === String(pid));
  function posAlerts(m) {
    const al = posAlertsOf(m);
    if (!al.length) return '';
    return `
      <section class="fb-posw" aria-label="Puestos distintos a los habituales">
        <div class="fb-posw-head">
          <span class="fb-posw-ico" aria-hidden="true">!</span>
          <div><b>Revisá la formación</b><small>${al.length === 1 ? 'Un titular está' : `${al.length} titulares están`} en un lugar donde casi no juega${al.length === 1 ? '' : 'n'}. A veces las fuentes publican un dibujo y el equipo juega con otro.</small></div>
        </div>
        <ul>${al.map(a => `<li><b>${esc(a.player)}</b><span>${esc(a.text)}</span></li>`).join('')}</ul>
        <div class="fb-posw-acts">${photoBtn(m.event_id, 'Corregir con una foto', 'fb-posw-btn')}<button class="fb-posw-btn" data-dt="dt" data-dtev="${esc(m.event_id)}">${I.tactics}Corregir en Modo DT</button></div>
      </section>`;
  }
  // La foto cargada no coincide con lo que publicó ESPN o PitchAPI después.
  function photoConflicts(m) {
    const srcs = m.lineup_sources || {}, conf = m.lineup_conflicts || {};
    const rows = Object.entries(conf).filter(([tid]) => MANUAL_SRC[srcs[tid]]);
    if (!rows.length) return '';
    const team = (tid) => String(m.ev && m.ev.home_id) === String(tid) ? m.teams.home.name : m.teams.away.name;
    const items = [];
    rows.forEach(([tid, c]) => Object.entries(c).forEach(([src, d]) => {
      if (src === 'foto' || !d) return;
      const bits = [];
      if (d.formation && d.formation !== c.foto) bits.push(`juega ${esc(d.formation)} (en la tuya ${esc(c.foto || '?')})`);
      if ((d.in || []).length) bits.push(`con ${d.in.map(esc).join(', ')}`);
      if ((d.out || []).length) bits.push(`sin ${d.out.map(esc).join(', ')}`);
      items.push(`<li><b>${esc(team(tid))} · ${src === 'pitchapi' ? 'PitchAPI' : 'ESPN'}</b><span>${bits.join('; ')}</span></li>`);
    }));
    return `
      <div class="fb-posw fb-ph-conf">
        <div class="fb-posw-head"><span class="fb-posw-ico" aria-hidden="true">!</span>
          <div><b>Tu alineación no coincide con otra fuente</b><small>Sigue mandando la tuya. Si está mal, corregila o volvé a la de la fuente.</small></div></div>
        <ul>${items.join('')}</ul>
      </div>`;
  }
  function pitch(m) {
    const lu = m.lineup || {};
    if (!lu.home && !lu.away) return '';
    const ev = esc(m.event_id);
    const rv = reviewing(m.event_id);
    const ph = rv ? state.photo : null;
    const want = state.xiSides[m.event_id] || 'home';
    const side = (lu[want] || (ph && ph.draft[want])) ? want : (lu.home ? 'home' : 'away');
    const xi = lu[side] || { players: [] };
    const conf = !!(m.lineup_confirmed || m.lineup_official);
    const editing = isEditing(m.event_id, side);
    const shown = shownXI(m, side) || { formation: '', players: [], custom: false };
    const fromPhoto = manualSrc(m);
    const isDT = ph && ph.mode === 'dt';
    const mk = rv ? null : hlMarks(m);           // anillos de Destacados / Sharp / Parlay
    const tab = (sd) => {
      const t = m.teams[sd], x = lu[sd], d = ph && ph.draft[sd];
      return `<button class="fb-xt${sd === side ? ' is-on' : ''}" data-xi="${sd}" data-xiev="${ev}" aria-pressed="${sd === side}">
        <img src="${logo(t.team_id)}" alt="" onerror="this.style.visibility='hidden'"><span><b>${esc(t.name)}</b><small>${esc((d && d.formation) || (x && x.formation) || '—')}${d ? (ph.mode === 'dt' ? ' · DT' : ' · foto') : ''}</small></span></button>`;
    };
    const doubts = (xi.players || []).filter(p => p.doubt);
    const only = ph ? Object.keys(ph.draft) : [];
    const bar = rv ? `
        <div class="fb-dt-bar is-editing">
          <span class="fb-dt-badge">${isDT ? `${I.tactics}Modo DT` : `${I.camera}Foto · revisar`}</span>
          ${editing ? `<label class="fb-dt-form">Esquema
            <select data-dt-form="${ev}">${FORMATIONS.concat(FORMATIONS.includes(shown.formation) ? [] : [shown.formation || '']).filter(Boolean).map(f => `<option${f === shown.formation ? ' selected' : ''}>${esc(f)}</option>`).join('')}</select>
          </label>` : ''}
          <span class="fb-dt-sp"></span>
          <button class="fb-dt-btn" data-dt="cancel" data-dtev="${ev}">Cancelar</button>
          <button class="fb-dt-save" data-dt="confirm" data-dtev="${ev}"${ph.saving ? ' disabled' : ''}>${I.check}${ph.saving ? 'Guardando…' : 'Confirmar alineación'}</button>
        </div>
        <p class="fb-dt-help">${isDT ? 'Tocá un jugador para cambiarlo por uno del banco o del plantel, o arrastralo a otro lugar. Cambiá el esquema arriba. Al confirmar cuenta como alineación oficial.'
          : `${only.length === 1 ? `La foto trae sólo a <b>${esc(m.teams[only[0]].name)}</b>. ` : ''}Los marcados con <b>?</b> son dudosos: tocá un jugador para cambiarlo por otro del plantel, o arrastralo a su lugar. Al confirmar cuenta como alineación oficial.${ph.notes ? ` <i>${esc(ph.notes)}</i>` : ''}`}</p>`
      : `
        <div class="fb-dt-bar">
          ${photoBtn(m.event_id)}${dtBtn(m.event_id)}
          ${fromPhoto ? `<span class="fb-dt-mine">${I.check}${fromPhoto === 'dt' ? 'Armada en Modo DT' : 'Cargada desde tu foto'}</span>
            <button class="fb-dt-btn" data-dt="discard" data-dtev="${ev}">Deshacer</button>` : ''}
        </div>`;
    return `
      <section class="fb-block fb-xi2">
        <div class="fb-block-head">
          <h3>Alinea<em>ción</em></h3>
          ${rv ? `<span class="fb-flag fb-flag--lu">${isDT ? `${I.tactics}Modo DT` : `${I.camera}Leída de la foto`} · sin confirmar</span>`
            : fromPhoto ? `<span class="fb-flag fb-flag--ok">${I.check}Oficial · ${MANUAL_SRC[fromPhoto]}</span>`
            : conf ? `<span class="fb-flag fb-flag--ok">${I.check}Confirmada</span>`
                   : `<span class="fb-flag fb-flag--lu">${I.clock}Probable · la oficial sale 1 h antes</span>`}
        </div>
        ${bar}
        ${rv ? '' : photoConflicts(m)}
        <div class="fb-xt-row">${tab('home')}${tab('away')}</div>
        <div class="fb-xp-wrap">
        <div class="fb-xp${editing ? ' is-dt' : ''}" data-dtpitch="${ev}" data-dtside="${side}">
          ${pitchLines(m.league === 'eng')}
          <span class="fb-xp-form">${esc(shown.formation || '')}</span>
          ${shown.custom ? shown.players.map(p => dtDot(p, m, editing, mk)).join('')
            : placeXI(xi).map(d => xiDot(m.league === 'eng' ? persp(d) : d, m, conf, mk)).join('')}
        </div>
          ${editing && state.dtMenu ? dtMenu(m, side) : ''}
        </div>
        ${editing ? benchList(m, state.dtDraft) : rv ? `<p class="fb-note">La foto no trae a ${esc(m.teams[side].name)}: queda la alineación que había.</p>` : `<p class="fb-note">En cada círculo: <b>remates esperados</b>${conf ? '' : '. El % del globito es su chance de ser titular; el borde punteado marca las dudas'}.
          ${!conf && doubts.length ? ` Dudas: ${doubts.map(p => `<b>${esc(surname(p.player))}</b>`).join(', ')}.` : ''}
          ${conf ? `Formación oficial (${[...new Set(String(m.lineup_source || 'espn').split('+'))].map(s => ({ 'api-football': 'API-Football', pitchapi: 'PitchAPI', highlightly: 'Highlightly', foto: 'tu foto', dt: 'Modo DT' })[s] || 'ESPN').join(' + ')}).` : 'Armada con el esquema del último partido y quién jugó cada puesto en las últimas 5 fechas.'}</p>`}
      </section>`;
  }

  // ── Acciones de la revisión de la foto ──────────────────────────────────
  function dtAction(act, eid, side) {
    const m = matchById(eid);
    if (!m) return;
    if (act === 'edit') {                       // cambiar de equipo durante la revisión
      const d = reviewing(eid) ? state.photo.draft[side] : null;
      state.dtEdit = { eid, side }; state.dtDraft = d || null; state.dtMenu = null;
    } else if (act === 'cancel') {
      state.photo = null; state.dtEdit = null; state.dtDraft = null; state.dtMenu = null;
    } else if (act === 'confirm') {
      confirmPhoto(eid); return;
    } else if (act === 'discard') {
      discardPhoto(eid); return;
    } else if (act === 'dt') {
      openDT(eid); return;
    } else if (act === 'close') {
      state.dtMenu = null;
    }
    render();
  }
  // Arrastrar jugadores en la revisión de la foto. Con mouse se arrastra directo; con el dedo
  // hay que mantenerlo apretado un instante (así deslizar sigue scrolleando la
  // página). Un toque corto abre el menú para cambiarlo.
  const DT_HOLD_MS = 230;
  // El bloqueo del scroll se engancha solo mientras se arrastra: un listener
  // no pasivo fijo en todo el documento traba el scroll de toda la app.
  const blockTouch = (e) => { if (e.cancelable) e.preventDefault(); };
  let dtArmedOn = false;
  const setDtArmed = (on) => {
    if (on === dtArmedOn) return;
    dtArmedOn = on;
    if (on) document.addEventListener('touchmove', blockTouch, { passive: false });
    else document.removeEventListener('touchmove', blockTouch);
  };
  function onDtDown(e) {
    const dot = e.target.closest('.fb-xd.is-edit');
    if (!dot || !state.dtDraft) return;
    const xp = dot.closest('.fb-xp');
    const pid = dot.dataset.dtp;
    const pl = state.dtDraft.players.find(p => p.player_id === pid);
    if (!pl) return;
    const touch = e.pointerType !== 'mouse';
    if (!touch) e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    let moved = false, cancelled = false;
    let armed = !touch;
    const hold = touch ? setTimeout(() => {
      armed = true; setDtArmed(true);
      dot.classList.add('is-dragging');
      if (navigator.vibrate) navigator.vibrate(12);
    }, DT_HOLD_MS) : null;
    const move = (ev) => {
      const far = Math.hypot(ev.clientX - sx, ev.clientY - sy);
      if (!armed) { if (far > 8) { cancelled = true; done(); } return; }   // es un scroll
      if (!moved && far < 5) return;
      moved = true;
      dot.classList.add('is-dragging');
      const r = xp.getBoundingClientRect();
      pl.x = Math.max(5, Math.min(95, ((ev.clientX - r.left) / r.width) * 100));
      pl.y = Math.max(6, Math.min(94, ((ev.clientY - r.top) / r.height) * 100));
      dot.style.left = pl.x + '%'; dot.style.top = pl.y + '%';
    };
    const done = () => {
      clearTimeout(hold); setDtArmed(false);
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', cancel);
    };
    const cancel = () => { cancelled = true; done(); dot.classList.remove('is-dragging'); };
    const up = () => {
      done();
      if (cancelled) return;
      if (moved) {
        pl.line = lineFromY(pl.y); pl.pos = posFrom(pl.line, pl.x);
        state.dtDraft.formation = formFromLines(state.dtDraft.players) || state.dtDraft.formation;
        state.dtMenu = null;
      } else {
        state.dtMenu = state.dtMenu === pid ? null : pid;
      }
      render();
      // en el celular el menú aparece debajo de la cancha: lo traemos a la vista
      if (state.dtMenu && window.matchMedia('(max-width: 768px)').matches) {
        const mn = document.querySelector('.fb-dt-menu');
        if (mn) mn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', cancel);
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
        <button class="fb-back" data-ev="${esc(state.back)}">${I.back}${({ recs: 'Recomendados', mine: 'Favoritos', picks: 'Jugadas', results: 'Resultados' })[state.back] || 'Partidos'}</button>
        ${starBtn(m.event_id)}
        <span class="fb-kicker fb-kicker--lg"><img src="${leagueOf(m.league).logo}" alt="" onerror="this.remove()">${esc(leagueOf(m.league).name)} · ${esc(dateTxt)} · ${hhmm(m.date)}</span>
      </div>
      ${leagueBand(m)}
      ${duel(m)}
      ${simControl(m, true)}
      ${lastSimBlock(m)}
      <div class="fb-match-flags">
        ${luPending(m)
          ? `<span class="fb-flag fb-flag--new fb-flag--lu">${I.check}Salió la alineación oficial · tocá <b>Simular partido</b> para recalcular con ella</span>`
          : manualSrc(m)
          ? `<span class="fb-flag fb-flag--ok fb-flag--lu">${I.check}Usando tu alineación (${MANUAL_SRC[manualSrc(m)]})</span>`
          : m.lineup_confirmed
          ? `<span class="fb-flag fb-flag--ok fb-flag--lu">${I.check}Alineación oficial confirmada</span>`
          : `<span class="fb-flag fb-flag--lu">${I.clock}Alineación probable · la oficial sale 1 hora antes del partido</span>`}
        ${marketNote(m)}
        ${m.oddspapi_fixture ? '' : '<span class="fb-flag fb-flag--warn">Sin cuotas de las casas: guiate por "jugala desde"</span>'}
      </div>
      ${posAlerts(m)}
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

  // ── Cargando: el esqueleto de lo que viene (sin ruedita), con un pulso suave
  //    que recorre las tarjetas en orden. Sin animación si se pidió menos movimiento.
  function loadingView(kind = 'matches') {
    const pulse = (i) => `style="--i:${i}"`;
    if (kind === 'form') {
      return `
        <div class="fb-sk" role="status" aria-live="polite">
          <p class="fb-sk-msg">Calculando cómo rindieron las jugadas…</p>
          <div class="fb-sk-strip">${[0, 1, 2, 3].map(i => `<i ${pulse(i)}></i>`).join('')}</div>
          <div class="fb-sk-rows">${[0, 1, 2, 3, 4].map(i => `<i ${pulse(i + 4)}></i>`).join('')}</div>
        </div>`;
    }
    const card = (i) => `
      <div class="fb-sk-card" ${pulse(i)}>
        <i class="fb-sk-time"></i>
        <span><i class="fb-sk-line"></i><i class="fb-sk-line is-short"></i></span>
        <i class="fb-sk-num"></i>
      </div>`;
    return `
      <div class="fb-sk" role="status" aria-live="polite">
        <i class="fb-sk-title"></i>
        <div class="fb-sk-chips">${[0, 1, 2, 3, 4, 5].map(i => `<i ${pulse(i)}></i>`).join('')}</div>
        <p class="fb-sk-msg">Trayendo los partidos de la fecha…</p>
        <div class="fb-sk-grid">${Array.from({ length: 8 }, (_, i) => card(i)).join('')}</div>
      </div>`;
  }

  // Redibujos que vienen de las consultas periódicas: si hay un dedo en la
  // pantalla o la página se sigue moviendo, esperan (reemplazar el HTML bajo
  // el dedo corta el scroll).
  const touch = { on: false, until: 0, timer: 0 };
  {
    const hold = (ms) => { touch.until = Date.now() + ms; };
    const end = () => { touch.on = false; hold(450); };
    document.addEventListener('touchstart', () => { touch.on = true; }, { passive: true });
    document.addEventListener('touchend', end, { passive: true });
    document.addEventListener('touchcancel', end, { passive: true });
    document.addEventListener('scroll', () => hold(250), { passive: true, capture: true });
  }
  function bgRender() {
    clearTimeout(touch.timer);
    if (touch.on || Date.now() < touch.until) { touch.timer = setTimeout(bgRender, 200); return; }
    render();
  }

  // ── Render principal ──────────────────────────────────────────────────────
  function render() {
    renderRail();
    renderTopTools();
    const sec = document.getElementById('section-futbol');
    if (!sec) return;
    if (!state.data) {
      sec._fbHtml = null;
      sec.innerHTML = state.error
        ? `<div class="fb-state fb-state--err"><b>No se pudieron traer los partidos</b><span>${esc(state.error)}</span><button class="fb-btn" data-act="reload">Reintentar</button></div>`
        : loadingView();
      return;
    }
    const m = isSection(state.view) ? null : matchById(state.view);
    const theme = calcTheme();
    THEMES.forEach(id => document.body.classList.toggle(`lg-${id}`, theme === id));
    const view = m ? matchView(m)
      : state.view === 'recs' ? recsView()
      : state.view === 'mine' ? mineView()
      : state.view === 'matches' ? matchesView()
      : state.view === 'results' ? resultsView()
      : state.view === 'form' ? formView()
      : (state.mode === 'simple' ? simpleView() : picksView());
    const showMode = m || state.view === 'picks';
    const lgScroll = sec.querySelector('.fb-lgbar')?.scrollLeft;
    const html = `
      <div class="fb-top">${navTop()}${showMode ? modeSwitch() : ''}</div>
      ${state.recalc ? recalcBar() : state.loading ? `<div class="fb-busy fb-busy--load" role="status">Actualizando los partidos…<i class="fb-busy-line" aria-hidden="true"></i></div>` : ''}
      ${state.error ? `<div class="fb-busy fb-busy--err">${esc(state.error)}</div>` : ''}
      <div class="fb-view fb-view--${m ? 'match' : state.view}">${view}</div>
      `;
    // Si no cambió nada, no se reemplaza la página: rehacer cientos de filas e
    // imágenes por nada es lo que se siente como "lag" (sobre todo en el celu).
    if (html !== sec._fbHtml || !sec.firstElementChild) {
      sec.innerHTML = html; sec._fbHtml = html;
      lgBarAfter(lgScroll);
    }
    // Boleta y barra de abajo viven en <body>: un padre con transform rompe position:fixed.
    let host = document.getElementById('fb-slip-host');
    if (!host) {
      host = Object.assign(document.createElement('div'), { id: 'fb-slip-host' });
      host.addEventListener('click', onClick);
      document.body.appendChild(host);
    }
    renderSlip();
  }

  function go(view) {
    if (view !== state.view) { state.team = 'all'; state.wsAll = false; }
    if (isSection(state.view) && !isSection(view)) state.back = state.view;
    state.view = view;
    render();
    if (view === 'form') loadReport();
    const main = document.querySelector('.app-main');
    if (main) main.scrollTo({ top: 0, behavior: 'smooth' });
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const chip = document.querySelector(`.fb-mstrip-list [data-ev="${CSS.escape(view)}"]`);
    if (chip) chip.scrollIntoView({ inline: 'center', block: 'nearest' });
  }

  // Desplegar / retraer un partido. Al abrir: la tarjeta pasa a ancho completo,
  // las demás se deslizan a su lugar nuevo (FLIP) y el panel crece desde la
  // tarjeta. Al cerrar: el panel se pliega más rápido (70%) y recién ahí la
  // grilla vuelve a dos columnas, también deslizando, sin saltos.
  const FX_OPEN = 360, FX_CLOSE = 250, FX_MOVE = 320;
  const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)';
  const EASE_IN_OUT = 'cubic-bezier(0.65, 0, 0.35, 1)';
  const fxReduce = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fxWraps = () => [...document.querySelectorAll('#section-futbol .fb-fx-wrap')];
  const fxIdOf = (w) => w.querySelector('.fb-fx[data-fx]')?.dataset.fx;
  const fxPanelOf = (id) => document.querySelector(`#section-futbol .fb-fx[data-fx="${CSS.escape(id)}"]`)?.closest('.fb-fx-wrap')?.querySelector('.fb-fxp');
  let fxBusy = null;

  // Re-renderiza y anima cada tarjeta desde donde estaba hasta donde quedó.
  // Si el cambio también cambia el tema de liga (abrir un partido de Champions
  // con "Todas" pinta toda la página de Champions), en vez de deslizar hace un
  // fundido corto entre los dos looks: nada cambia de golpe.
  // `move` sets the timing of the slide, so the cards below a panel that is
  // opening keep pace with the panel.
  function fxFlip(mutate, after, move = { duration: FX_MOVE, easing: EASE_IN_OUT }) {
    const before = new Map(fxWraps().map(w => [fxIdOf(w), w.getBoundingClientRect()]));
    const oldTheme = THEMES.find(t => document.body.classList.contains(`lg-${t}`)) || null;
    mutate.state();
    if (calcTheme() !== oldTheme && document.startViewTransition && !fxReduce()) {
      const vt = document.startViewTransition(() => render());
      vt.updateCallbackDone.then(() => after && after());
      return;
    }
    render();
    after && after();
    fxWraps().forEach(w => {
      const a = before.get(fxIdOf(w));
      if (!a) return;
      const b = w.getBoundingClientRect();
      const dx = a.left - b.left, dy = a.top - b.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      w.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], move);
    });
  }

  function toggleFx(id, wrap) {
    if (fxBusy) { fxBusy.forEach(a => a.finish()); fxBusy = null; }   // un clic nuevo corta la animación en curso
    const reduce = fxReduce();
    if (state.fxOpen.has(id)) {
      // cleanup runs after the new layout is drawn. Undoing the closing
      // animations earlier brings the open panel back for one frame (and the
      // theme crossfade captures that frame).
      const close = (cleanup) => fxFlip({ state: () => {
        state.fxOpen.delete(id);
        if (state.lastOpen === id) state.lastOpen = [...state.fxOpen].pop() || null;
      } }, cleanup);
      const panel = wrap && wrap.querySelector('.fb-fxp');
      if (!panel || reduce) return close();
      // Instead of shrinking the height (which re-lays out the page every frame),
      // clip the card from the bottom and slide the cards below up with
      // transform. Both run on the GPU.
      const pr = panel.getBoundingClientRect(), h = pr.height;
      const wr = wrap.getBoundingClientRect();
      const r = getComputedStyle(wrap).borderRadius || '0px';
      const t = { duration: FX_CLOSE, easing: EASE_IN_OUT, fill: 'forwards' };
      const anims = [
        wrap.animate([{ clipPath: `inset(0 0 0 0 round ${r})` }, { clipPath: `inset(0 0 ${h}px 0 round ${r})` }], t),
        panel.animate([{ opacity: 1 }, { opacity: 0 }], t),
        ...fxWraps().filter(w => w !== wrap && w.getBoundingClientRect().top >= wr.bottom - 1)
          .map(w => w.animate([{ transform: 'none' }, { transform: `translateY(${-h}px)` }], t)),
      ];
      fxBusy = anims;
      // Old positions are measured while the cards are still shifted up; the
      // animations are cancelled only once the new layout is in place.
      anims[0].onfinish = () => { fxBusy = null; close(() => anims.forEach(a => a.cancel())); };
      return;
    }
    if (reduce) { state.fxOpen.add(id); state.lastOpen = id; render(); return; }
    fxFlip({ state: () => { state.fxOpen.add(id); state.lastOpen = id; } }, () => {
      const np = fxPanelOf(id);
      if (!np) return;
      // The panel already has its full size; the card is revealed downward with
      // clip-path while the cards below slide (in fxFlip) with the same timing.
      const w = np.closest('.fb-fx-wrap');
      const h = np.getBoundingClientRect().height;
      const r = getComputedStyle(w).borderRadius || '0px';
      const grow = w.animate([
        { clipPath: `inset(0 0 ${h}px 0 round ${r})` },
        { clipPath: `inset(0 0 0 0 round ${r})` },
      ], { duration: FX_OPEN, easing: EASE_OUT });
      np.animate([{ opacity: 0 }, { opacity: 1 }], { duration: FX_OPEN, easing: EASE_OUT });
      const kids = [...np.children].slice(0, 4).map((c, i) => c.animate([
        { opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' },
      ], { duration: FX_OPEN, delay: 60 + i * 60, easing: EASE_OUT, fill: 'backwards' }));
      fxBusy = [grow, ...kids];
      grow.onfinish = () => { fxBusy = null; };
    }, { duration: FX_OPEN, easing: EASE_OUT });
  }

  function onClick(e) {
    const t = e.target;
    if (t.closest('a.is-link[href]')) return;     // cuota que abre la casa: no abrir también el partido
    const chg = t.closest('[data-chg]');
    if (chg) {
      state.chgOpen = state.chgOpen || new Set();
      const id = chg.dataset.chg;
      if (state.chgOpen.has(id)) state.chgOpen.delete(id); else state.chgOpen.add(id);
      render(); return;
    }
    if (state.view === 'form' && fmClick(e, t)) return;
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
      toggleSlip(sl.dataset.slip);
      render();
      return;
    }
    const sg = t.closest('[data-slipgo]');
    if (sg) return goToPick(sg.dataset.slipgo);
    const sx = t.closest('[data-slipexp]');
    if (sx) {
      const k = sx.dataset.slipexp;
      if (state.slipExp.has(k)) state.slipExp.delete(k); else state.slipExp.add(k);
      return renderSlip();
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
    const sh = t.closest('[data-hlshot]');
    if (sh) {
      const id = sh.dataset.hlshot;
      if (state.shotBusy.has(id)) return;
      state.shotBusy.add(id); swapHl(id);
      shotHl(id).catch((e) => toast(`No se pudo armar la foto: ${esc(e.message)}`, null))
        .finally(() => { state.shotBusy.delete(id); swapHl(id); });
      return;
    }
    const hs = t.closest('[data-hlsharp]');
    if (hs) {
      const id = hs.dataset.hlsharp;
      if (state.hlParlay.has(id)) { state.hlParlay.delete(id); state.hlSharp.add(id); }
      else if (state.hlSharp.has(id)) state.hlSharp.delete(id); else state.hlSharp.add(id);
      state.hlSpin = id;
      // Sólo se redibuja este bloque: rehacer toda la sección recargaba fotos y
      // fondos y la pantalla titilaba en negro un instante.
      const box = hs.closest('.fb-hl');
      const m = matchById(id);
      refreshMarks(id);
      if (box && m) {
        const tmp = document.createElement('div');
        tmp.innerHTML = highlights(m).trim();
        if (tmp.firstElementChild) {
          box.replaceWith(tmp.firstElementChild);
          const fsec = document.getElementById('section-futbol'); if (fsec) fsec._fbHtml = null;
          const btn = document.querySelector(`.fb-hl [data-hlsharp="${CSS.escape(id)}"]`);
          if (btn) btn.focus({ preventScroll: true });
          return;
        }
      }
      render();
      return;
    }
    if (t.closest('[data-resmore]')) { state.resN = (state.resN || RES_PAGE) + 40; return render(); }
    const hb = t.closest('[data-hlboost]');
    if (hb) {
      const id = hb.dataset.hlboost;
      if (state.hlBoost.has(id)) state.hlBoost.delete(id); else state.hlBoost.add(id);
      if (swapHl(id)) {
        const btn = document.querySelector(`.fb-hl [data-hlboost="${CSS.escape(id)}"]`);
        if (btn) btn.focus({ preventScroll: true });
      }
      return;
    }
    const hp = t.closest('[data-hlparlay]');
    if (hp) {
      const id = hp.dataset.hlparlay;
      if (state.hlParlay.has(id)) state.hlParlay.delete(id);
      else { state.hlParlay.add(id); state.hlZap[id] = Date.now(); }
      state.hlSpin = id;
      if (swapHl(id)) {
        const btn = document.querySelector(`.fb-hl [data-hlparlay="${CSS.escape(id)}"]`);
        if (btn) btn.focus({ preventScroll: true });
      }
      return;
    }
    const bl = t.closest('[data-balls]');
    if (bl) { const k = bl.dataset.balls; state.ballOpen = state.ballOpen === k ? null : k; render(); return; }
    const fx = t.closest('[data-fx]');
    if (fx && !t.closest('[data-fav]')) { toggleFx(fx.dataset.fx, fx.closest('.fb-fx-wrap')); return; }
    if (t.closest('[data-photo]') || t.closest('label') && t.closest('label').querySelector('[data-photo]')) return;   // abre el selector de foto
    const dta = t.closest('[data-dt]');
    if (dta) {
      dtAction(dta.dataset.dt, dta.dataset.dtev || (state.dtEdit && state.dtEdit.eid), dta.dataset.dtside);
      return;
    }
    const dtp = t.closest('[data-dtpick]');
    if (dtp && state.dtDraft) { dtPick(dtp.dataset.dtpick); render(); return; }
    if (t.closest('[data-dtname]')) return;                 // escribiendo el nombre
    const xs = t.closest('[data-xi]');
    if (xs) { state.xiSides[xs.dataset.xiev] = xs.dataset.xi; if (reviewing(xs.dataset.xiev)) dtAction('edit', xs.dataset.xiev, xs.dataset.xi); else render(); return; }
    const oref = t.closest('[data-oddsref]');
    if (oref) { e.stopPropagation(); refreshOddsScope(oref.dataset.oddsref); return; }
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
      if (act.dataset.act === 'reload') load();
      else if (act.dataset.act === 'report') { state.reportErr = null; render(); loadReport(); }
      else onTool(act.dataset.act);
      return;
    }
    const lnav = t.closest('[data-lgnav]');
    if (lnav) { lgScrollBy(+lnav.dataset.lgnav); return; }
    const lg = t.closest('[data-lg]');
    if (lg) { lgPick(lg.dataset.lg); state.league = lg.dataset.lg; render(); return; }
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
    if (sec) sec.addEventListener('pointerdown', onDtDown);
    if (sec) sec.addEventListener('submit', (e) => {
      const f = e.target.closest('[data-dtaddform]');
      if (!f) return;
      e.preventDefault();
      dtAddByName(f.querySelector('[data-dtname]').value);
      render();
    });
    if (sec) {
      sec.addEventListener('pointerdown', onFmDown);
      sec.addEventListener('pointermove', (e) => { onFmMove(e); onFmHover(e); });
      sec.addEventListener('pointerup', onFmUp);
      sec.addEventListener('pointercancel', onFmUp);
      sec.addEventListener('pointerleave', () => { if (!fmDrag) fmTipHide(); });
      sec.addEventListener('change', onFmDate);
    }
    if (sec) sec.addEventListener('change', (e) => {
      const ph = e.target.closest('[data-photo]');
      if (ph) { const f = ph.files && ph.files[0]; ph.value = ''; readPhoto(ph.dataset.photo, f); return; }
      const f = e.target.closest('[data-dt-form]');
      if (!f || !state.dtDraft) return;
      const m = matchById(f.dataset.dtForm);
      state.dtDraft = applyFormation(state.dtDraft, f.value, m && m.league === 'eng');
      state.dtMenu = null;
      render();
    });
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
  const LINEUP_POLL_MS = 60 * 1000;
  let pollTimer = null;
  // Avisos con la identidad de cada liga: la del partido, o la que estás mirando.
  function toastLeague(evId) {
    const m = evId && matchById(evId);
    if (m) return m.league;
    if (state.league && state.league !== 'all') return state.league;
    const b = (document.body.className.match(/\blg-(arg|bra|eng|ita|esp|ucl|uel|cdb|unl)\b/) || [])[1];
    return b || null;
  }
  function toast(html, evId, lg = null) {
    let box = document.getElementById('fb-toasts');
    if (!box) { box = Object.assign(document.createElement('div'), { id: 'fb-toasts', className: 'fb-toasts' }); document.body.appendChild(box); }
    lg = lg || toastLeague(evId);
    const mark = lg === 'bra' ? `<img src="assets/bra/brasileirao-mark.png?v=2" alt="">` : lg ? `<img src="${leagueOf(lg).logo}" alt="">` : '';
    const t = Object.assign(document.createElement('div'), {
      className: 'fb-toast' + (lg ? ` fb-toast--lg lg-${lg}` : ' fb-toast--app') + (evId ? ' is-link' : ''),
      innerHTML: `<i class="fb-toast-mark">${mark}</i>${html}` });
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
      bgRender();
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
    if (st.job && st.job.kind) return followRecalc(st.job);   // recálculo empezado en otro dispositivo
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
        bgRender();
        toast(`${I.refresh}<span><b>Datos actualizados</b>Hubo una simulación o un cambio desde otro dispositivo</span>`);
      } catch (_) { /* reintenta en el próximo ciclo */ }
    }
    if (st.prefs_version != null && st.prefs_version !== state.prefsVersion) loadPrefs(false);
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => { checkLineups(); if (futbolVisible()) { loadResults(); if (state.view === 'form') loadReport(); } }, LINEUP_POLL_MS);
    setInterval(pollStatus, 20000);
    setInterval(pollLive, 30000);
    setTimeout(pollLive, 2500);
    loadUsage();
    setInterval(() => { if (futbolVisible()) loadUsage(); }, 120000);
    document.addEventListener('visibilitychange', () => { if (futbolVisible()) { checkLineups(); pollStatus(); } });
    loadPrefs(true);
  }

  // El tema de liga vive en <body>: se saca al ir a MLB / NBA.
  new MutationObserver(() => {
    if (document.body.dataset.sport !== 'futbol') ['eng', 'arg', 'bra', 'ita', 'esp', 'mex', 'ucl', 'uel', 'cdb', 'unl'].forEach(id => document.body.classList.remove(`lg-${id}`));
  }).observe(document.body, { attributes: true, attributeFilter: ['data-sport'] });

  window.loadFutbol = function loadFutbol() {
    wire();
    startPolling();
    if (state.data || state.loading) { render(); return; }
    load();
  };
  window.FB_BALL_ICON = I.ball;
})();

// Barra de ligas pegada arriba: le pone fondo solo cuando ya quedó pegada.
(() => {
  let raf = 0;
  let bar = null, sc = null, sticky = false, last = null;
  const tick = () => {
    raf = 0;
    if (!bar || !bar.isConnected) {            // la sección se redibujó: se busca de nuevo (y sólo entonces se lee el estilo)
      bar = document.querySelector('#section-futbol .fb-lgwrap');
      if (!bar) return;
      sc = bar.closest('.app-main');
      sticky = getComputedStyle(bar).position === 'sticky';
      last = null;
    }
    if (!sticky || !bar.offsetParent) return;
    const y = sc ? sc.scrollTop : window.scrollY;
    const stuck = y > 0 && bar.getBoundingClientRect().top - (sc ? sc.getBoundingClientRect().top : 0) <= 1;
    if (stuck !== last) { last = stuck; bar.classList.toggle('is-stuck', stuck); }
  };
  document.addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(tick); }, { passive: true, capture: true });
})();
