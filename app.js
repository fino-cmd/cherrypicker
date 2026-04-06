const API = `http://${window.location.hostname}:8003`;

// ── MLB team abbreviation → numeric ID (for logo CDN) ─────────────────────────
const _TEAM_ID = {
  ARI:109, ATH:133, ATL:144, BAL:110, BOS:111, CHC:112, CHW:145, CIN:113,
  CLE:114, COL:115, DET:116, HOU:117, KC:118,  LAA:108, LAD:119, MIA:146,
  MIL:158, MIN:142, NYM:121, NYY:147, OAK:133, PHI:143, PIT:134, SD:135,
  SEA:136, SF:137,  STL:138, TB:139,  TEX:140, TOR:141, WSH:120,
  // common alternate abbreviations
  CWS:145, KCR:118, TBR:139, SDP:135, SFG:137, WSN:120,
};
function _teamLogoUrl(abbr) {
  const id = _TEAM_ID[String(abbr).toUpperCase()];
  return id ? `https://www.mlbstatic.com/team-logos/${id}.svg` : null;
}

// ── Search toggle ───────────────────────────────────────────────────────────────
function toggleSearch() {
  const panel = document.getElementById('topbar-search-panel');
  const input = document.getElementById('topbar-search-input');
  const isOpen = panel.classList.toggle('open');
  if (isOpen) {
    input.focus();
    // Close on outside click
    setTimeout(() => {
      document.addEventListener('click', function closeSearch(e) {
        if (!document.getElementById('topbar-search-wrap').contains(e.target)) {
          panel.classList.remove('open');
          input.value = '';
          document.removeEventListener('click', closeSearch);
        }
      });
    }, 0);
  } else {
    input.value = '';
  }
}

// ── Toast notifications ────────────────────────────────────────────────────────
function _showToast(msg, type = 'info') {
  let el = document.getElementById('app-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'app-toast';
    el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;padding:12px 18px;border-radius:8px;font-size:13px;font-weight:600;max-width:360px;box-shadow:0 4px 16px rgba(0,0,0,.4);transition:opacity .3s;pointer-events:none;';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.background = type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#3b82f6';
  el.style.color = '#fff';
  el.style.opacity = '1';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = '0'; }, 4000);
}

// ── Utilities ──────────────────────────────────────────────────────────────────

async function apiFetch(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`API ${path} → ${r.status}`);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`API POST ${path} → ${r.status}`);
  return r.json();
}

function pct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '—'; }
function num(v, d = 2) { return v != null ? Number(v).toFixed(d) : '—'; }
function dash(v) { return v != null ? v : '—'; }

function loading(id, msg = 'Loading…') {
  const el = document.getElementById(id);
  if (el) el.innerHTML = `<tr><td colspan="20" class="loading-cell">${msg}</td></tr>`;
}

function errRow(id, msg) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = `<tr><td colspan="20" class="error-cell">${msg}</td></tr>`;
}

// ── Page Navigation ────────────────────────────────────────────────────────────

function goToApp() {
  document.getElementById('page-landing').classList.remove('active');
  const app = document.getElementById('page-app');
  app.style.display = 'flex';
  showSection('pitcher-props', document.querySelector('.nav-item[data-section="pitcher-props"]'));
}

function goToLanding() {
  document.getElementById('page-app').style.display = 'none';
  document.getElementById('page-landing').classList.add('active');
}

function showSection(name, el) {
  document.querySelectorAll('.app-section').forEach(s => s.style.display = 'none');
  const target = document.getElementById('section-' + name);
  if (target) target.style.display = 'block';
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  if (el) el.classList.add('active');

  // Lazy-load section data
  if (name === 'main-markets') loadMatchups();
  if (name === 'pitcher-props') loadPitchers();
  if (name === 'tracker') loadTracker();
  if (name === 'overview') loadOverview();
  if (name === 'todays-picks') loadTodaysPicks();
  if (name === 'matchup-tool') loadMatchupTool();
}

// ── MATCHUP TOOL ──────────────────────────────────────────────────────────────

let _matchupToolGames = null;
const _cubeSimData    = {};   // game_id → sim result + ou_line

async function loadMatchupTool() {
  const sec = document.getElementById('section-matchup-tool');
  if (!sec || sec.dataset.built) return;
  sec.dataset.built = '1';

  sec.innerHTML = `
    <div class="mt-header">
      <div class="pp-title">MATCHUP <span class="pp-title-accent">SIMULATOR</span></div>
      <div class="pp-desc">Monte Carlo 10,000-game O/U — xFIP × xwOBA model</div>
    </div>
    <div id="sim-cube-grid" class="sim-cube-grid">
      <div class="sim-cube-status">Cargando partidos…</div>
    </div>

    <!-- Nerd-data bottom drawer -->
    <div id="nerd-overlay" class="nerd-overlay" onclick="closeNerdPanel()"></div>
    <div id="nerd-drawer" class="nerd-drawer" role="dialog" aria-modal="true" aria-label="Nerd data">
      <div class="nerd-drawer-pill" onclick="closeNerdPanel()" role="button" aria-label="Cerrar"></div>
      <div class="nerd-drawer-scroll" id="nerd-drawer-content"></div>
    </div>
  `;

  try {
    const games = await apiFetch('/api/games/today');
    _matchupToolGames = games;
    const grid = document.getElementById('sim-cube-grid');

    if (!games.length) {
      grid.innerHTML = '<div class="sim-cube-status">No hay partidos hoy</div>';
      return;
    }

    grid.innerHTML = games.map(g => `
      <div class="sim-cube" id="cube-${g.game_id}" onclick="openNerdPanel(${g.game_id})">

        <!-- ── Red header ── -->
        <div class="sim-cube-top">
          <img src="cherry.png" class="sim-cube-cherry" alt="" />
          <div class="sim-cube-teams">
            <span class="sim-cube-team">${g.away_team}</span>
            <span class="sim-cube-at">@</span>
            <span class="sim-cube-team">${g.home_team}</span>
          </div>
          <div class="sim-cube-pitchers">${g.away_pitcher.name} · ${g.home_pitcher.name}</div>
        </div>

        <!-- ── Verdict (hidden until sim runs) ── -->
        <div class="sim-cube-verdict" id="verdict-${g.game_id}" aria-live="polite">
          <span class="sim-cube-direction" id="vdir-${g.game_id}"></span>
          <span class="sim-cube-total"    id="vtot-${g.game_id}"></span>
          <span class="sim-cube-prob"     id="vprob-${g.game_id}"></span>
        </div>

        <!-- ── Controls ── -->
        <div class="sim-cube-bottom" onclick="event.stopPropagation()">
          <div class="sim-cube-ou-wrap">
            <label class="sim-cube-ou-label" for="ou-${g.game_id}">O/U</label>
            <input  class="sim-cube-ou-input" id="ou-${g.game_id}"
                    type="number" step="0.5" min="1" max="30" value="8.5"
                    aria-label="Línea Over/Under" />
          </div>
          <button class="sim-cube-play" id="play-${g.game_id}"
                  onclick="runCubeSim(${g.game_id})"
                  aria-label="Simular partido ${g.away_team} vs ${g.home_team}">
            <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22" aria-hidden="true">
              <path d="M8 5.14v13.72a1 1 0 0 0 1.5.86l10.5-6.86a1 1 0 0 0 0-1.72L9.5 4.28A1 1 0 0 0 8 5.14z"/>
            </svg>
          </button>
        </div>

      </div>
    `).join('');

  } catch (e) {
    document.getElementById('sim-cube-grid').innerHTML =
      '<div class="sim-cube-status">Error al cargar partidos</div>';
  }
}

async function runCubeSim(game_id) {
  const playBtn = document.getElementById(`play-${game_id}`);
  const ouInput = document.getElementById(`ou-${game_id}`);
  const ou_line = parseFloat(ouInput?.value ?? 8.5);
  if (isNaN(ou_line)) return;

  playBtn.disabled = true;
  playBtn.classList.add('sim-cube-play--loading');

  try {
    const d = await apiPost('/api/simulation/ou', { game_id, ou_line });
    _cubeSimData[game_id] = { ...d, ou_line };

    const isOver   = d.over_prob >= d.under_prob;
    const direction = isOver ? 'OVER' : 'UNDER';
    const prob      = Math.round(Math.max(d.over_prob, d.under_prob) * 100);

    document.getElementById(`vdir-${game_id}`).textContent  = direction;
    document.getElementById(`vdir-${game_id}`).className    =
      `sim-cube-direction sim-cube-direction--${direction.toLowerCase()}`;
    document.getElementById(`vtot-${game_id}`).textContent  = d.avg_total.toFixed(1);
    document.getElementById(`vprob-${game_id}`).textContent = `${prob}%`;

    const cube = document.getElementById(`cube-${game_id}`);
    cube.classList.add('sim-cube--result');

  } catch (e) {
    _showToast('Error en simulación: ' + e.message, 'error');
  } finally {
    playBtn.disabled = false;
    playBtn.classList.remove('sim-cube-play--loading');
  }
}

function openNerdPanel(game_id) {
  const data = _cubeSimData[game_id];
  if (!data) return;   // no sim yet — nothing to show

  const d        = data;
  const ou_line  = d.ou_line;
  const fmtKPct  = v => v != null ? (v * 100).toFixed(1) + '%' : '—';

  const overPct  = Math.round(d.over_prob  * 100);
  const underPct = Math.round(d.under_prob * 100);
  const edgeAbs  = Math.abs(d.edge_pct * 100).toFixed(1);
  const edgeDir  = d.edge_pct >= 0 ? 'OVER' : 'UNDER';
  const edgeSign = d.edge_pct >= 0 ? '+' : '-';
  const barClass = overPct > 55 ? 'mt-bar-green' : overPct < 45 ? 'mt-bar-red' : 'mt-bar-yellow';

  const parkColor = d.park_factor >= 105 ? 'var(--red-light)' : d.park_factor <= 95 ? '#22c55e' : 'var(--text-muted)';
  const parkLabel = d.park_factor >= 105 ? 'hitter-friendly' : d.park_factor <= 95 ? 'pitcher-friendly' : 'neutral';

  const maxCount = Math.max(...d.histogram.map(b => b.count), 1);
  const histHTML = d.histogram.map(b => {
    const w  = Math.round((b.count / maxCount) * 100);
    const hl = ou_line >= b.lo && ou_line <= b.hi;
    return `<div class="mt-hist-row${hl ? ' mt-hist-highlight' : ''}">
      <span class="mt-hist-bucket">${b.bucket}</span>
      <div class="mt-hist-bar-wrap"><div class="mt-hist-bar${hl ? ' mt-hist-bar-hl' : ''}" style="width:${w}%"></div></div>
      <span class="mt-hist-count">${b.count.toLocaleString()}</span>
    </div>`;
  }).join('');

  let lineupHTML = '';
  if (d.lineup_confirmed) {
    const rows = Array.from({length: 9}, (_, i) => {
      const h = (d.home_lineup[i] || {}).name || '—';
      const a = (d.away_lineup[i] || {}).name || '—';
      return `<div class="mt-lineup-row">
        <span class="mt-lineup-num">${i + 1}</span>
        <span class="mt-lineup-name">${h}</span>
        <span class="mt-lineup-name mt-lineup-away">${a}</span>
      </div>`;
    }).join('');
    lineupHTML = `<div class="mt-lineup-display">
      <div class="mt-lineup-header"><span></span><span>${d.home_team}</span><span>${d.away_team}</span></div>
      ${rows}
    </div>`;
  }

  document.getElementById('nerd-drawer-content').innerHTML = `
    <div class="nerd-drawer-title">${d.away_team} @ ${d.home_team}</div>
    <div class="nerd-drawer-subtitle">${d.away_pitcher} vs ${d.home_pitcher} · ${d.park_name}</div>

    <div class="mt-stats-row" style="margin-bottom:16px">
      <div class="ov-card mt-stat-card"><div class="ov-label">${d.home_team} RUNS</div><div class="ov-val red">${num(d.exp_runs_home)}</div></div>
      <div class="ov-card mt-stat-card"><div class="ov-label">TOTAL PROM</div><div class="ov-val red">${num(d.avg_total)}</div></div>
      <div class="ov-card mt-stat-card"><div class="ov-label">${d.away_team} RUNS</div><div class="ov-val red">${num(d.exp_runs_away)}</div></div>
    </div>

    <div class="mt-prob-card" style="margin-bottom:16px">
      <div class="mt-prob-labels">
        <span class="mt-prob-label-over">OVER ${overPct}%</span>
        <span class="mt-prob-label-under">UNDER ${underPct}%</span>
      </div>
      <div class="mt-prob-track">
        <div class="mt-prob-bar ${barClass}" style="width:${overPct}%"></div>
      </div>
      <div class="mt-edge-text">${edgeSign}${edgeAbs}% EDGE → ${edgeDir}</div>
    </div>

    <div class="ov-card mt-context-card" style="margin-bottom:16px">
      <div class="mt-label" style="margin-bottom:10px">MATCHUP CONTEXT</div>
      <div class="mt-context-rows">
        <div class="mt-context-row">
          <span class="mt-context-side">${d.home_team}</span>
          <span class="mt-context-detail">${d.away_pitcher} <span class="mt-hand-badge">${d.away_pitcher_hand}HP</span> — K%: <strong>${fmtKPct(d.home_k_pct_vs_hand)}</strong></span>
        </div>
        <div class="mt-context-row">
          <span class="mt-context-side">${d.away_team}</span>
          <span class="mt-context-detail">${d.home_pitcher} <span class="mt-hand-badge">${d.home_pitcher_hand}HP</span> — K%: <strong>${fmtKPct(d.away_k_pct_vs_hand)}</strong></span>
        </div>
        <div class="mt-context-row">
          <span class="mt-context-side" style="font-size:16px;letter-spacing:0">🏟</span>
          <span class="mt-context-detail"><strong>${d.park_name}</strong> · PF: <strong style="color:${parkColor}">${d.park_factor}</strong> <span style="color:${parkColor}">${parkLabel}</span></span>
        </div>
      </div>
    </div>

    <div class="mt-hist-card" style="margin-bottom:16px">
      <div class="mt-hist-title">DISTRIBUCIÓN DE CARRERAS TOTALES</div>
      <div class="mt-histogram">${histHTML}</div>
    </div>

    <div class="mt-footer-note" style="color:${d.lineup_confirmed ? '#22c55e' : ''}">
      ${d.lineup_confirmed
        ? `✅ Lineup confirmado (${(d.home_lineup || []).length} bateadores)`
        : '⚠️ Stats de temporada — lineup no confirmado'}
    </div>
    ${lineupHTML}
  `;

  const overlay = document.getElementById('nerd-overlay');
  const drawer  = document.getElementById('nerd-drawer');
  overlay.classList.add('nerd-overlay--on');
  drawer.classList.add('nerd-drawer--open');
}

function closeNerdPanel() {
  document.getElementById('nerd-overlay').classList.remove('nerd-overlay--on');
  document.getElementById('nerd-drawer').classList.remove('nerd-drawer--open');
}

// ── MAIN MARKETS ──────────────────────────────────────────────────────────────

async function loadMatchups() {
  const list = document.getElementById('matchup-list');
  const tableBody = document.getElementById('market-table-body');
  if (!list) return;

  list.innerHTML = '<div class="loading-block">Loading today\'s games…</div>';

  try {
    const games = await apiFetch('/api/games/today');

    if (!games.length) {
      list.innerHTML = '<div class="loading-block">No games scheduled today.</div>';
      return;
    }

    // Fetch all simulations in parallel
    const sims = await Promise.all(
      games.map(g =>
        apiFetch(`/api/simulate/${g.game_id}`).catch(() => null)
      )
    );

    list.innerHTML = '';
    const tableRows = [];

    games.forEach((game, i) => {
      const sim = sims[i];
      const homeProb = sim ? Math.round(sim.home_win_prob * 100) : null;
      const awayProb = sim ? Math.round(sim.away_win_prob * 100) : null;
      const edge     = sim ? ((Math.max(homeProb, awayProb) / 100) - 0.5238).toFixed(3) : null; // implied vs -110
      const favTeam  = (homeProb != null && homeProb >= awayProb) ? game.home_team : game.away_team;
      const edgePct  = edge ? (parseFloat(edge) * 100).toFixed(1) : null;
      const isRecommended = edgePct && parseFloat(edgePct) >= 3;

      const card = document.createElement('div');
      card.className = 'matchup-card' + (isRecommended ? ' recommended' : '');
      card.innerHTML = `
        ${isRecommended ? '<div class="recommended-badge">Recommended Pick</div>' : ''}
        <div class="matchup-teams">
          <div class="team-side left">
            <img src="https://www.mlbstatic.com/team-logos/${game.away_team_id}.svg"
                 class="team-logo" alt="${game.away_team}"
                 onerror="this.style.display='none'" />
            <div>
              <span class="team-abbr">${game.away_team}</span>
              <div class="pitcher-info">
                ${game.away_pitcher.name !== 'TBD' ? game.away_pitcher.name : '<span class="tbd">TBD</span>'}
              </div>
            </div>
          </div>
          <div class="vs-divider">VS</div>
          <div class="team-side right">
            <div style="text-align:right">
              <span class="team-abbr">${game.home_team}</span>
              <div class="pitcher-info right-align">
                ${game.home_pitcher.name !== 'TBD' ? game.home_pitcher.name : '<span class="tbd">TBD</span>'}
              </div>
            </div>
            <img src="https://www.mlbstatic.com/team-logos/${game.home_team_id}.svg"
                 class="team-logo" alt="${game.home_team}"
                 onerror="this.style.display='none'" />
          </div>
        </div>
        ${sim ? `
          <div class="win-prob-row">
            <span>${game.away_team} ${awayProb}%</span>
            <span>${game.home_team} ${homeProb}%</span>
          </div>
          <div class="win-prob-bar"><div class="bar-fill" style="width:${homeProb}%"></div></div>
          <div class="edge-btn">
            ${edgePct ? `+${edgePct}% Edge on ${favTeam} ML` : `Total: ${sim.expected_total} runs · ${sim.confidence} confidence`}
          </div>
        ` : `
          <div class="edge-btn edge-btn--muted">Simulation unavailable</div>
        `}
      `;
      list.appendChild(card);

      // Market comparison table row
      tableRows.push(`
        <tr>
          <td>${game.away_team} vs ${game.home_team}</td>
          <td>—</td>
          <td>—</td>
          <td>—</td>
          <td class="model-price">${sim ? '$' + num(1 / sim.home_win_prob) : '—'}</td>
        </tr>
      `);
    });

    if (tableBody) tableBody.innerHTML = tableRows.join('');

  } catch (e) {
    console.error(e);
    list.innerHTML = `<div class="error-block">Failed to load games: ${e.message}</div>`;
  }
}

// ── PITCHER PROPS ─────────────────────────────────────────────────────────────

// Palette for avatar backgrounds (consistent per pitcher name)
const _avatarColors = ['#7c3aed','#0ea5e9','#059669','#d97706','#e11d48','#6366f1','#14b8a6','#f59e0b'];
function _avatarColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffffff;
  return _avatarColors[Math.abs(h) % _avatarColors.length];
}
function _initials(name) {
  return name.split(' ').filter(Boolean).map(w => w[0].toUpperCase()).slice(0, 2).join('');
}

// Build a visual CI bar; dot positioned at model_proj within [ci_low, ci_high]
function _ciBar(ciLow, ciHigh, modelProj) {
  if (ciLow == null || ciHigh == null) return '<span style="color:var(--text-dim)">—</span>';
  const range = ciHigh - ciLow || 1;
  const pos   = Math.min(100, Math.max(0, ((modelProj ?? ciLow) - ciLow) / range * 100));
  return `
    <div class="pp-ci-wrap">
      <span class="pp-ci-num">${num(ciLow,1)}</span>
      <div class="pp-ci-track">
        <div class="pp-ci-fill" style="width:100%"></div>
        <div class="pp-ci-dot" style="left:${pos.toFixed(1)}%"></div>
      </div>
      <span class="pp-ci-num">${num(ciHigh,1)}</span>
    </div>`;
}

// Build edge pill
function _edgePill(edge) {
  if (edge == null) return '<span class="pp-edge-pill none">—</span>';
  const cls = edge >= 0 ? 'pos' : 'neg';
  return `<span class="pp-edge-pill ${cls}">${edge >= 0 ? '+' : ''}${num(edge,1)}</span>`;
}

async function loadPitchers() {
  loading('props-tbody', 'Loading pitcher data…');
  try {
    const [pitchers, picks, stats] = await Promise.all([
      apiFetch('/api/pitchers/today').catch(() => []),
      apiFetch('/api/picks/today').catch(() => []),
      apiFetch('/api/stats/summary').catch(() => ({})),
    ]);
    const picksMap = {};
    picks.forEach(p => { picksMap[p.pitcher_name.toLowerCase()] = p; });
    renderPropsTable(pitchers, picksMap);
    _updatePropsHeader(pitchers, picksMap);
    _updateInsightCards(pitchers, picksMap, stats);
  } catch (e) {
    console.error(e);
    errRow('props-tbody', `Error: ${e.message}`);
  }
}

// MLB official headshot URL (falls back to initials on error)
function _headshotUrl(pitcherId) {
  if (!pitcherId) return null;
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:silo:current.png/w_180,q_auto:best/v1/people/${pitcherId}/headshot/67/current`;
}

// K-line preset chips (common half-integer values)
const _KLINE_PRESETS = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0];

function _klineEntry() {
  const chips = _KLINE_PRESETS.map(v =>
    `<button class="kline-chip" onmousedown="event.preventDefault(); _pickLine(this,${v})">${v}</button>`
  ).join('');
  return `
    <div class="kline-entry">
      <div class="kline-field">
        <input type="text" inputmode="decimal" class="k-line-input" placeholder="5.5"
               onfocus="this.closest('.kline-entry').classList.add('open')"
               onblur="setTimeout(()=>this.closest('.kline-entry').classList.remove('open'),200)">
        <button class="line-submit-btn" onclick="submitLine(this)">&#8594;</button>
      </div>
      <div class="kline-presets">${chips}</div>
    </div>`;
}

function _pickLine(chipBtn, val) {
  const entry = chipBtn.closest('.kline-entry');
  entry.querySelector('.k-line-input').value = val;
  submitLine(entry.querySelector('.line-submit-btn'));
}

function renderPropsTable(pitchers, picksMap = {}) {
  const tbody = document.getElementById('props-tbody');
  if (!tbody) return;

  if (!pitchers.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="loading-cell">No pitcher data available today.</td></tr>';
    return;
  }

  tbody.innerHTML = pitchers.map(p => {
    const name     = p.name;
    const pick     = picksMap[name.toLowerCase()];
    const confPct  = ((p.confidence_level || 1) * 20);
    const safeId   = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
    const color    = _avatarColor(name);
    const initials = _initials(name);
    const photoUrl = _headshotUrl(p.pitcher_id);

    // Determine rec
    const rec = pick ? (pick.recommendation || 'SKIP') : 'SKIP';
    const isTopPick = pick && (rec === 'OVER' || rec === 'UNDER') && (p.confidence_level || 0) >= 4;

    // Avatar: real photo with initials fallback; background color always visible behind circular clip
    const avatarHtml = photoUrl
      ? `<img src="${photoUrl}" class="pp-avatar-img"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
             alt="${name}" />
         <div class="pp-avatar" style="background:${color};display:none">${initials}</div>`
      : `<div class="pp-avatar" style="background:${color}">${initials}</div>`;

    // Team / opp logos
    const teamLogo = _teamLogoUrl(p.team);
    const oppLogo  = _teamLogoUrl(p.opponent);
    const teamLogoHtml = teamLogo
      ? `<img src="${teamLogo}" class="tp-inline-team-logo" alt="${p.team || ''}" onerror="this.style.display='none'">`
      : '';
    const oppLogoHtml = oppLogo
      ? `<img src="${oppLogo}" class="tp-opp-logo" alt="${p.opponent || ''}" onerror="this.style.display='none'">`
      : '';
    const oppCell = `<div class="tp-opp-cell"><span class="tp-vs-label">vs</span>${oppLogoHtml || `<span class="tp-dim">${p.opponent || '—'}</span>`}</div>`;

    // Cherry badge
    const cherryBadge = (rec === 'OVER' || rec === 'UNDER')
      ? `<img src="cherry.png" class="pp-cherry-badge pp-cherry-badge--${rec.toLowerCase()}" alt="${rec}" title="${rec}" />`
      : '<span class="pp-cherry-spacer"></span>';

    // K-LINE cell
    const lineCell = pick
      ? `<strong class="pp-kline-val">${num(pick.k_line, 1)}</strong>`
      : _klineEntry();

    // MODEL cell
    let modelCell;
    if (pick && pick.model_proj != null) {
      const dirArrow = rec === 'OVER' ? '↑' : rec === 'UNDER' ? '↓' : '';
      const dirLabel = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : '';
      const dirClass = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : '';
      modelCell = `<div class="pp-model-cell">
        <div class="pp-model-top">
          <span class="pp-model-num">${num(pick.model_proj, 1)}</span>
          ${dirArrow ? `<span class="pp-model-dir ${dirClass}">${dirArrow}</span>` : ''}
        </div>
        ${dirLabel ? `<div class="pp-model-label ${dirClass}">${dirLabel}</div>` : ''}
      </div>`;
    } else {
      modelCell = '<span style="color:var(--text-dim)">—</span>';
    }

    // EDGE / CI cells
    const edgeCell = pick ? _edgePill(pick.edge) : '<span class="pp-edge-pill none">—</span>';
    const ciCell   = pick ? _ciBar(pick.ci_low, pick.ci_high, pick.model_proj) : _ciBar(null, null, null);

    // RESULT cell
    let resultCell;
    if (pick && pick.actual_ks != null) {
      const res  = pick.result || '';
      const rc   = res === 'W' ? 'win' : res === 'L' ? 'loss' : 'push';
      const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
      resultCell = `<span class="tp-result-pill ${rc}">${icon} ${pick.actual_ks}K</span>`;
    } else if (pick && rec !== 'SKIP') {
      resultCell = `
        <div class="tp-log-row">
          <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input" placeholder="Ks">
          <button class="tp-log-btn" onclick="submitResult(this)">LOG</button>
          <button class="pp-cancel-btn" onclick="cancelPick(this)" title="Scratched / injured">&#8630;</button>
        </div>`;
    } else if (pick) {
      resultCell = `
        <div style="display:flex;gap:5px;align-items:center">
          <span style="font-size:11px;color:var(--text-dim)">SKIP</span>
          <button class="pp-cancel-btn" onclick="cancelPick(this)" title="Undo pick">&#8630;</button>
        </div>`;
    } else {
      resultCell = `<span style="color:var(--text-dim);font-size:11px">—</span>`;
    }

    return `
      <tr id="prop-row-${safeId}" data-pitcher="${name}" data-team="${p.team}"
          class="${isTopPick ? 'pp-top-pick-row' : ''}">
        <td>
          <div class="pp-pitcher-cell">
            ${cherryBadge}
            <div class="pp-avatar-wrap" style="background:${color}">${avatarHtml}</div>
            <div class="pp-pitcher-info">
              <div class="pp-pitcher-name">
                ${name}${p.data_incomplete ? ' <span class="incomplete-flag" title="No training data">*</span>' : ''}
                ${teamLogoHtml}
              </div>
            </div>
            ${isTopPick ? '<span class="pp-top-pick-badge">TOP<br/>PICK</span>' : ''}
          </div>
        </td>
        <td>${oppCell}</td>
        <td>${lineCell}</td>
        <td>${modelCell}</td>
        <td>${edgeCell}</td>
        <td>${ciCell}</td>
        <td><span class="pp-conf-pct">${confPct}%</span></td>
        <td><span style="color:var(--text-muted);font-size:12px">—</span></td>
        <td>${resultCell}</td>
      </tr>
    `;
  }).join('');
}


// ── TODAY'S PICKS PAGE ────────────────────────────────────────────────────────

async function loadTodaysPicks() {
  const tbody   = document.getElementById('tp-tbody');
  const slateEl = document.getElementById('tp-slate-text');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Loading…</td></tr>';

  try {
    const picks = await apiFetch('/api/picks/today').catch(() => []);

    const actionable = picks.filter(p => p.recommendation === 'OVER' || p.recommendation === 'UNDER');

    const today  = new Date();
    const dateStr = today.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();
    if (slateEl) slateEl.textContent = `${dateStr}  ·  ${actionable.length} actionable pick${actionable.length !== 1 ? 's' : ''}`;

    if (!actionable.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="loading-cell" style="padding:40px 0;color:var(--text-dim)">no actionable picks yet — submit k-lines in pitcher deep dive.</td></tr>';
      return;
    }

    tbody.innerHTML = actionable.map(pick => {
      const name    = pick.pitcher_name;
      const safeId  = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
      const rec     = pick.recommendation;
      const recCls  = rec === 'OVER' ? 'over' : 'under';
      const photoUrl = _headshotUrl(pick.pitcher_id);
      const color    = _avatarColor(name);
      const initials = _initials(name);

      const teamLogo = _teamLogoUrl(pick.team);
      const oppLogo  = _teamLogoUrl(pick.opponent);
      const avatarHtml = photoUrl
        ? `<img src="${photoUrl}" class="tp-row-avatar-img" alt="${name}"
               onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
           <div class="tp-row-avatar-fallback" style="background:${color};display:none">${initials}</div>`
        : `<div class="tp-row-avatar-fallback" style="background:${color}">${initials}</div>`;
      const teamLogoHtml = teamLogo
        ? `<img src="${teamLogo}" class="tp-inline-team-logo" alt="${pick.team}">`
        : '';

      const edgeSign = pick.edge >= 0 ? '+' : '';
      const edgeCls  = pick.edge >= 0 ? 'over' : 'under';

      let resultCell;
      if (pick.actual_ks != null && pick.result) {
        const rc   = pick.result === 'W' ? 'win' : pick.result === 'L' ? 'loss' : 'push';
        const icon = pick.result === 'W' ? '✓' : pick.result === 'L' ? '✕' : '·';
        resultCell = `<span class="tp-result-pill ${rc}">${icon} ${pick.actual_ks}K</span>`;
      } else {
        resultCell = `
          <div class="tp-log-row">
            <input type="text" inputmode="decimal" class="tp-ks-input"
                   placeholder="Ks" data-pitcher="${name}" data-safeid="${safeId}">
            <button class="tp-log-btn" onclick="submitTodaysPick(this)">LOG</button>
          </div>`;
      }

      const rowSettled = pick.actual_ks != null && pick.result;
      const rowCls = rowSettled
        ? (pick.result === 'W' ? 'tp-row-win' : pick.result === 'L' ? 'tp-row-loss' : 'tp-row-push')
        : '';

      return `
        <tr class="tp-row ${rowCls}" id="tp-row-${safeId}">
          <td>
            <div class="tp-pitcher-cell">
              <div class="tp-row-avatar">${avatarHtml}</div>
              <div>
                <div class="tp-row-name">${name} ${teamLogoHtml}</div>
              </div>
            </div>
          </td>
          <td>
            <div class="tp-opp-cell">
              <span class="tp-vs-label">vs</span>
              ${oppLogo
                ? `<img src="${oppLogo}" class="tp-opp-logo" alt="${pick.opponent}"
                       onerror="this.outerHTML='<span class=tp-dim>${pick.opponent||'—'}</span>'">`
                : `<span class="tp-dim">${pick.opponent || '—'}</span>`}
            </div>
          </td>
          <td><strong>${num(pick.k_line, 1)}</strong></td>
          <td>${pick.model_proj != null ? num(pick.model_proj, 1) : '—'}</td>
          <td><span class="tp-edge ${edgeCls}">${edgeSign}${num(pick.edge, 1)}</span></td>
          <td><span class="tp-rec-pill ${recCls}">${rec === 'OVER' ? '▲' : '▼'} ${rec}</span></td>
          <td id="tp-result-${safeId}">${resultCell}</td>
        </tr>`;
    }).join('');

  } catch (e) {
    console.error(e);
    tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">Error: ${e.message}</td></tr>`;
  }
}

async function submitTodaysPick(btn) {
  const input    = btn.previousElementSibling;
  const name     = input.dataset.pitcher;
  const safeId   = input.dataset.safeid;
  const actualKs = parseFloat(input.value.replace(',', '.'));

  if (isNaN(actualKs) || actualKs < 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  const today = new Date().toLocaleDateString('en-CA');

  try {
    const result = await apiPost('/api/picks/result', {
      date: today, pitcher_name: name, actual_ks: actualKs,
    });
    const res = result.result || '';
    const rc  = res === 'W' ? 'win' : res === 'L' ? 'loss' : 'push';
    const rl  = res === 'W' ? 'WIN'  : res === 'L' ? 'LOSS'  : 'PUSH';

    const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
    const resultEl = document.getElementById(`tp-result-${safeId}`);
    if (resultEl) resultEl.innerHTML = `<span class="tp-result-pill ${rc}">${icon} ${actualKs}K</span>`;

    const row = document.getElementById(`tp-row-${safeId}`);
    if (row) {
      row.classList.remove('tp-row-win','tp-row-loss','tp-row-push');
      row.classList.add(`tp-row-${rc}`);
    }

    _showToast(`${name} — ${actualKs} Ks → ${rl}`, res === 'W' ? 'success' : 'info');
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = 'LOG';
    input.disabled  = false;
    _showToast(`Error: ${e.message}`, 'error');
  }
}

// Update header stat boxes after render
function _updatePropsHeader(pitchers, picksMap) {
  const picks = Object.values(picksMap);
  const actionable = picks.filter(p => p.recommendation === 'OVER' || p.recommendation === 'UNDER');
  const edges = actionable.map(p => p.edge).filter(e => e != null);
  const avgEdge = edges.length ? edges.reduce((a, b) => a + Math.abs(b), 0) / edges.length : null;

  // Top pick: highest confidence pick
  const topPick = actionable.reduce((best, p) => {
    return (!best || (p.confidence || 0) > (best.confidence || 0)) ? p : best;
  }, null);
  const topPct = topPick ? ((topPick.confidence || 3) * 20) : null;

  // Slate info: today + count
  const today = new Date();
  const dateStr = today.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();
  const el = id => document.getElementById(id);
  if (el('pp-slate-text')) el('pp-slate-text').textContent = `${dateStr}  ·  ${pitchers.length} pitchers on the slate`;
  if (el('pp-avg-edge'))   el('pp-avg-edge').textContent  = avgEdge != null ? `+${avgEdge.toFixed(1)}` : '—';
  if (el('pp-top-pick'))   el('pp-top-pick').textContent  = topPct  != null ? `${topPct.toFixed(1)}%` : '—';
  if (el('pp-last-updated-time')) el('pp-last-updated-time').textContent = 'JUST NOW';
}

// Update bottom insight cards
function _updateInsightCards(pitchers, picksMap, stats = {}) {
  const picks  = Object.values(picksMap);
  const overs  = picks.filter(p => p.recommendation === 'OVER').length;
  const unders = picks.filter(p => p.recommendation === 'UNDER').length;
  const total  = overs + unders || 1;
  const overPct = Math.round(overs / total * 100);

  // Top edge pick for market alert
  const top = picks.sort((a, b) => Math.abs(b.edge || 0) - Math.abs(a.edge || 0))[0];
  const el = id => document.getElementById(id);
  if (top && el('pp-alert-name')) {
    el('pp-alert-name').textContent = top.pitcher_name || '—';
    el('pp-alert-move').textContent = top.edge != null
      ? `Edge: ${top.edge >= 0 ? '+' : ''}${num(top.edge, 1)} Ks vs line ${num(top.k_line, 1)}`
      : 'No line submitted yet';
  }

  if (el('pp-vol-fill')) el('pp-vol-fill').style.width = overPct + '%';
  if (el('pp-vol-pct'))  el('pp-vol-pct').textContent  = total > 1 ? overPct + '%' : '—';

  // Historical accuracy from stats summary
  if (el('pp-acc-val')) {
    const hr = stats.hit_rate != null ? (stats.hit_rate * 100).toFixed(1) + '%' : '—';
    el('pp-acc-val').textContent = hr;
  }
}

// TOP PICKS filter toggle
let _showTopOnly = false;
function toggleTopPicks(btn) {
  _showTopOnly = !_showTopOnly;
  if (btn) btn.classList.toggle('active', _showTopOnly);
  document.querySelectorAll('#props-tbody tr').forEach(row => {
    row.classList.toggle('pp-hidden', _showTopOnly && !row.classList.contains('pp-top-pick-row'));
  });
}

// Cancel a pick (pitcher scratched or injured)
async function cancelPick(btn) {
  const row  = btn.closest('tr');
  const name = row.dataset.pitcher;
  const today = new Date().toLocaleDateString('en-CA');

  btn.disabled = true;
  btn.textContent = '…';

  try {
    await apiPost('/api/picks/cancel', { date: today, pitcher_name: name });
    // Reset row back to empty state (line-entry)
    const cells = row.querySelectorAll('td');
    cells[2].innerHTML = _klineEntry();
    cells[3].innerHTML = '<span style="color:var(--text-dim)">—</span>';
    cells[4].innerHTML = '<span class="pp-edge-pill none">—</span>';
    cells[5].innerHTML = _ciBar(null, null, null);
    cells[8].innerHTML = '<button class="tp-log-btn" style="opacity:.4" disabled>—</button>';
    row.classList.remove('pp-top-pick-row');
  } catch (e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = '↺';
    btn.style.borderColor = 'var(--red)';
  }
}

// Called when the → button in a K-line cell is clicked
async function submitLine(btn) {
  const row   = btn.closest('tr');
  const name  = row.dataset.pitcher;
  const team  = row.dataset.team;
  const input = row.querySelector('.k-line-input');
  const kLine = parseFloat(input.value);

  if (isNaN(kLine) || kLine <= 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  try {
    const pick = await apiPost('/api/picks/lines', { pitcher_name: name, team, k_line: kLine });
    _applyPickToRow(row, pick);
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = '→';
    input.disabled  = false;
    input.style.borderColor = 'var(--red)';
    _showToast(`Error: ${e.message}`, 'error');
  }
}

// Called when the LOG button in a result cell is clicked
async function submitResult(btn) {
  const row      = btn.closest('tr');
  const name     = row.dataset.pitcher;
  const input    = row.querySelector('.result-ks-input');
  const actualKs = parseFloat(input.value.replace(',', '.'));

  if (isNaN(actualKs) || actualKs < 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  const today = new Date().toLocaleDateString('en-CA');

  try {
    const result = await apiPost('/api/picks/result', {
      date:         today,
      pitcher_name: name,
      actual_ks:    actualKs,
    });
    const res = result.result || '';
    const rc   = res === 'W' ? 'win' : res === 'L' ? 'loss' : res === 'P' ? 'push' : 'pending';
    const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
    row.querySelectorAll('td')[8].innerHTML =
      `<span class="tp-result-pill ${rc}">${icon} ${actualKs}K</span>`;
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    _showToast(`${name} — ${actualKs} Ks → ${label}`, res === 'W' ? 'success' : 'info');
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = 'LOG';
    input.disabled  = false;
    input.style.borderColor = 'var(--red)';
    _showToast(`Error: ${e.message}`, 'error');
  }
}

// Log result directly from the Bet Tracker (for picks from previous days)
async function logFromTracker(btn) {
  const input       = btn.previousElementSibling;
  const pitcherName = input.dataset.pitcher;
  const date        = input.dataset.date;
  const actualKs    = parseFloat(input.value.replace(',', '.'));

  if (isNaN(actualKs) || actualKs < 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  try {
    const result = await apiPost('/api/picks/result', {
      date,
      pitcher_name: pitcherName,
      actual_ks:    actualKs,
    });
    const res = result.result || '';
    const rc  = res === 'W' ? 'win' : res === 'L' ? 'loss' : res === 'P' ? 'push' : 'pending';
    const td  = btn.closest('td');
    td.innerHTML = `${actualKs} Ks &nbsp;<span class="result-badge ${rc}">${res || '?'}</span>`;
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = 'LOG';
    input.disabled  = false;
    input.style.borderColor = 'var(--red)';
  }
}

// Inline editor for correcting a settled tracker result
function editTrackerResult(btn) {
  const td           = btn.closest('td');
  const pitcherName  = btn.dataset.pitcher;
  const date         = btn.dataset.date;
  const currentKs    = btn.dataset.ks;

  td.innerHTML = `
    <div style="display:flex;align-items:center;gap:5px">
      <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input"
             value="${currentKs}" placeholder="Ks"
             data-pitcher="${pitcherName}" data-date="${date}">
      <button class="tp-log-btn" onclick="saveTrackerEdit(this)">SAVE</button>
      <button class="tp-log-btn" style="background:var(--bg3);color:var(--text-muted)"
              onclick="loadTracker()">✕</button>
    </div>`;
  td.querySelector('input').focus();
}

async function saveTrackerEdit(btn) {
  const input       = btn.previousElementSibling;
  const pitcherName = input.dataset.pitcher;
  const date        = input.dataset.date;
  const actualKs    = parseFloat(input.value.replace(',', '.'));

  if (isNaN(actualKs) || actualKs < 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  try {
    const result = await apiPost('/api/picks/result', {
      date,
      pitcher_name: pitcherName,
      actual_ks:    actualKs,
    });
    const res = result.result || '';
    const rc  = res === 'W' ? 'win' : res === 'L' ? 'loss' : res === 'P' ? 'push' : 'pending';
    const td  = btn.closest('td');
    td.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px">
        ${actualKs} Ks &nbsp;<span class="result-badge ${rc}">${res || '?'}</span>
        <button class="tracker-edit-btn" title="Corregir resultado"
                data-pitcher="${pitcherName}" data-date="${date}" data-ks="${actualKs}"
                onclick="editTrackerResult(this)">✎</button>
      </div>`;
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    _showToast(`${pitcherName} corregido → ${actualKs} Ks (${label})`, 'success');
    // Reload summary stats
    loadTracker();
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = 'SAVE';
    input.disabled  = false;
    _showToast(`Error: ${e.message}`, 'error');
  }
}

// Update a props-table row in place after a successful /api/picks/lines POST
function _applyPickToRow(row, pick) {
  const cells = row.querySelectorAll('td');
  const rec   = pick.recommendation || 'SKIP';

  // col 2 – K LINE
  cells[2].innerHTML = `<strong class="pp-kline-val">${num(pick.k_line, 1)}</strong>`;

  // col 3 – MODEL
  const dirClass = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : 'skip';
  const dirArrow = rec === 'OVER' ? '↑' : rec === 'UNDER' ? '↓' : '';
  const dirLabel = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : '';
  cells[3].innerHTML = pick.model_proj != null
    ? `<div class="pp-model-cell">
         <div class="pp-model-top">
           <span class="pp-model-num">${num(pick.model_proj, 1)}</span>
           ${dirArrow ? `<span class="pp-model-dir ${dirClass}">${dirArrow}</span>` : ''}
         </div>
         ${dirLabel ? `<div class="pp-model-label ${dirClass}">${dirLabel}</div>` : ''}
       </div>`
    : '<span style="color:var(--text-dim)">—</span>';

  // cherry badge — flex sibling before avatar (col 0)
  const pitcherCell = cells[0].querySelector('.pp-pitcher-cell');
  if (pitcherCell) {
    const existing = pitcherCell.querySelector('.pp-cherry-badge, .pp-cherry-spacer');
    const newEl = document.createElement(rec === 'OVER' || rec === 'UNDER' ? 'img' : 'span');
    if (rec === 'OVER' || rec === 'UNDER') {
      newEl.src = 'cherry.png';
      newEl.className = `pp-cherry-badge pp-cherry-badge--${rec.toLowerCase()}`;
      newEl.alt = rec;
      newEl.title = rec;
    } else {
      newEl.className = 'pp-cherry-spacer';
    }
    if (existing) pitcherCell.replaceChild(newEl, existing);
    else pitcherCell.prepend(newEl);
  }

  // col 4 – EDGE
  cells[4].innerHTML = _edgePill(pick.edge);

  // col 5 – CI
  cells[5].innerHTML = _ciBar(pick.ci_low, pick.ci_high, pick.model_proj);

  // col 8 – RESULT
  if (rec !== 'SKIP') {
    cells[8].innerHTML = `
      <div class="tp-log-row">
        <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input" placeholder="Ks">
        <button class="tp-log-btn" onclick="submitResult(this)">LOG</button>
        <button class="pp-cancel-btn" onclick="cancelPick(this)" title="Undo pick">&#8630;</button>
      </div>`;
  } else {
    cells[8].innerHTML = `
      <div style="display:flex;gap:5px;align-items:center">
        <button class="tp-log-btn" style="opacity:.4" disabled>SKIP</button>
        <button class="pp-cancel-btn" onclick="cancelPick(this)" title="Undo pick">&#8630;</button>
      </div>`;
  }

  // Mark as top pick row if warranted
  const isTop = (rec === 'OVER' || rec === 'UNDER') && (pick.confidence || 0) >= 4;
  row.classList.toggle('pp-top-pick-row', isTop);
}

// ── BETTING TRACKER ────────────────────────────────────────────────────────────

let trackerChart = null;

async function loadTracker() {
  loading('tracker-tbody', 'Loading pick history…');
  try {
    const { picks, summary } = await apiFetch('/api/picks/history');
    renderPicksSummary(summary);
    renderPicksTable(picks);
    renderPicksChart(picks);
  } catch (e) {
    console.error(e);
    errRow('tracker-tbody', `Error: ${e.message}`);
  }
}

function renderPicksSummary(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  const roi    = s.roi_pct  != null ? (s.roi_pct  > 0 ? '+' : '') + s.roi_pct.toFixed(1)  + '%' : '—';
  const edge   = s.avg_edge != null ? (s.avg_edge > 0 ? '+' : '') + s.avg_edge.toFixed(2) + 'K' : '—';
  const hr     = s.hit_rate != null ? (s.hit_rate * 100).toFixed(1) + '%' : '—';

  // Summary bar
  set('picks-record',  s.record   || '0-0-0');
  set('picks-hitrate', hr);
  set('picks-roi',     roi);
  set('picks-avgedge', edge);

  // Calibration card (brier box)
  set('stat-roi',    roi);
  set('stat-wins',   s.record ? s.record.split('-')[0] : '0');
  set('stat-losses', s.record ? s.record.split('-')[1] : '0');
  set('stat-clv',    edge);
}

function renderPicksTable(picks) {
  const tbody = document.getElementById('tracker-tbody');
  if (!tbody) return;

  if (!picks.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="loading-cell">No picks yet. Submit K lines from Pitcher Props.</td></tr>';
    return;
  }

  tbody.innerHTML = picks.map(p => {
    const edge = p.edge != null ? parseFloat(p.edge) : null;
    const edgeCell = edge != null
      ? `<span class="${edge >= 0 ? 'edge-pos' : 'edge-neg'}">${edge >= 0 ? '+' : ''}${edge.toFixed(2)}</span>`
      : '—';

    const rec = (p.recommendation || '').toLowerCase();
    const recCell = p.recommendation
      ? `<span class="rec-badge ${rec}">${p.recommendation}</span>`
      : '—';

    let resultCell;
    const res = p.result;
    if (res && res !== 'SKIP') {
      const rc = res === 'W' ? 'win' : res === 'L' ? 'loss' : 'push';
      resultCell = `
        <div style="display:flex;align-items:center;gap:6px">
          ${p.actual_ks != null ? p.actual_ks + ' Ks &nbsp;' : ''}<span class="result-badge ${rc}">${res}</span>
          <button class="tracker-edit-btn" title="Corregir resultado"
                  data-pitcher="${p.pitcher_name}" data-date="${p.date}" data-ks="${p.actual_ks ?? ''}"
                  onclick="editTrackerResult(this)">✎</button>
        </div>`;
    } else if (res === 'SKIP') {
      resultCell = `${p.actual_ks != null ? p.actual_ks + ' Ks &nbsp;' : ''}<span class="result-badge pending">SKIP</span>`;
    } else {
      // Pending — show inline log entry so user can enter result after midnight
      resultCell = `
        <div class="result-entry">
          <input type="text" inputmode="decimal" class="result-ks-input" placeholder="Ks"
                 data-pitcher="${p.pitcher_name}" data-date="${p.date}">
          <button class="pp-analyze-btn" onclick="logFromTracker(this)">LOG</button>
        </div>`;
    }

    const ci = (p.ci_low != null && p.ci_high != null)
      ? `[${p.ci_low}–${p.ci_high}]`
      : '—';

    return `
      <tr>
        <td><div class="game-cell">${p.pitcher_name}<div class="game-date">${p.date}</div></div></td>
        <td>${p.team  || '—'}</td>
        <td>${p.opponent || '—'}</td>
        <td>${p.k_line    != null ? p.k_line    : '—'}</td>
        <td>${p.model_proj != null ? p.model_proj : '—'}</td>
        <td>${edgeCell}</td>
        <td>${recCell}</td>
        <td style="font-size:12px;color:var(--text-muted)">${ci}</td>
        <td>${p.confidence != null ? p.confidence + '/5' : '—'}</td>
        <td>${resultCell}</td>
      </tr>
    `;
  }).join('');
}

function renderPicksChart(picks) {
  const ctx = document.getElementById('trackerChart');
  if (!ctx) return;

  // Settled, non-SKIP picks in ascending date order
  const settled = [...picks]
    .filter(p => p.result && p.result !== 'SKIP' && p.result !== 'P')
    .reverse(); // history API returns DESC

  let cumProfit = 0;
  const labels = [], roiData = [];

  settled.forEach((p, i) => {
    cumProfit += p.result === 'W' ? (100 / 110) : -1;
    labels.push(p.date);
    roiData.push(parseFloat((cumProfit / (i + 1) * 100).toFixed(2)));
  });

  if (trackerChart) trackerChart.destroy();

  if (!labels.length) {
    // Nothing settled yet — show empty state inside canvas area
    trackerChart = null;
    return;
  }

  trackerChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'ROI %',
        data: roiData,
        borderColor: '#e03040',
        backgroundColor: 'rgba(224,48,64,0.08)',
        tension: 0.4, fill: true,
        pointBackgroundColor: '#e03040', pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#888', font: { size: 11 } } },
        tooltip: { mode: 'index', intersect: false },
      },
      scales: {
        x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
        y: { ticks: { color: '#888', font: { size: 11 }, callback: v => v + '%' }, grid: { color: 'rgba(255,255,255,0.06)' } },
      },
    },
  });
}

// ── ADD BET FORM ──────────────────────────────────────────────────────────────

async function submitBet(e) {
  e.preventDefault();
  const form = e.target;
  const btn  = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = 'Saving…';

  const payload = {
    date:         form.bet_date.value,
    game:         form.bet_game.value,
    market:       form.bet_market.value,
    odds_decimal: parseFloat(form.bet_odds.value),
    result:       form.bet_result.value || null,
    clv:          form.bet_clv.value ? parseFloat(form.bet_clv.value) : null,
    units:        parseFloat(form.bet_units.value),
    notes:        form.bet_notes.value || null,
  };

  try {
    await apiPost('/api/tracker', payload);
    form.reset();
    await loadTracker(); // refresh table + chart
  } catch (err) {
    alert('Error saving bet: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Add Bet';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('bet-form');
  if (form) form.addEventListener('submit', submitBet);
});

// ── OVERVIEW ──────────────────────────────────────────────────────────────────

async function loadOverview() {
  try {
    const [games, stats] = await Promise.all([
      apiFetch('/api/games/today').catch(() => []),
      apiFetch('/api/stats/summary').catch(() => ({})),
    ]);

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('ov-games',  games.length || '—');
    set('ov-roi',    stats.roi_pct  != null ? (stats.roi_pct  > 0 ? '+' : '') + stats.roi_pct.toFixed(1)  + '%' : '—');
    set('ov-wins',   stats.wins   ?? '—');
    set('ov-losses', stats.losses ?? '—');
  } catch (e) {
    console.error('Overview load error:', e);
  }
}

// ── CHARTS (static overview chart) ───────────────────────────────────────────

function chartOpts() {
  return {
    responsive: true,
    plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
    scales: {
      x: { ticks: { color: '#555', font: { size: 11 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
      y: { ticks: { color: '#888', font: { size: 11 }, callback: v => v + '%' }, grid: { color: 'rgba(255,255,255,0.06)' } },
    },
  };
}

function initOverviewChart() {
  const ctx = document.getElementById('overviewChart');
  if (!ctx || ctx._chart) return;
  ctx._chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep'],
      datasets: [{
        label: 'ROI %',
        data: [8, 28, 76, 79, 89, 127],
        borderColor: '#e03040',
        backgroundColor: 'rgba(224,48,64,0.1)',
        tension: 0.4, fill: true,
        pointBackgroundColor: '#e03040', pointRadius: 4,
      }],
    },
    options: chartOpts(),
  });
}
