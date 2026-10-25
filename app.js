// Base URL del backend. `window.location.hostname` viene VACÍO cuando la página
// se abre con doble clic (file://), lo que producía URLs del tipo "http://:8002"
// y rompía todos los fetch. Si no hay hostname, asumimos localhost.
const API = `http://${window.location.hostname || 'localhost'}:8002`;

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

// Argentine peso formatter — "1.234.567" style, no decimals, optional sign prefix.
const _MONEY_FMT = new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 });
function money(v, withSign = false) {
  if (v == null || isNaN(v)) return '—';
  const n = Math.round(Number(v));
  const abs = _MONEY_FMT.format(Math.abs(n));
  const sign = n < 0 ? '-' : (withSign && n > 0 ? '+' : '');
  return `${sign}$${abs}`;
}

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
  showSection('pitcher-props', null);
}

function goToLanding() {
  document.getElementById('page-app').style.display = 'none';
  document.getElementById('page-landing').classList.add('active');
}

function showSection(name, el) {
  document.querySelectorAll('.app-section').forEach(s => s.style.display = 'none');
  const target = document.getElementById('section-' + name);
  if (target) target.style.display = 'block';
  // Sync sidebar nav items and mobile bottom nav items
  document.querySelectorAll('.nav-item, .mbn-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll(`.nav-item[data-section="${name}"], .mbn-item[data-section="${name}"]`).forEach(n => n.classList.add('active'));

  // Lazy-load section data
  if (name === 'main-markets') loadMatchups();
  if (name === 'pitcher-props') loadPitchers();
  if (name === 'tracker') loadTracker();
  if (name === 'overview') loadOverview();
  if (name === 'todays-picks') loadTodaysPicks();
  if (name === 'matchup-tool') loadMatchupTool();
  if (name === 'altlines') loadAltLines();
  if (name === 'futbol' && window.loadFutbol) window.loadFutbol();
}

// ── MATCHUP TOOL ──────────────────────────────────────────────────────────────

let _matchupToolGames  = null;
const _cubeSimData     = {};   // game_id → sim result + ou_line
let _lineupStatusMap   = {};   // game_id → bool (shared across sections)

async function loadMatchupTool() {
  const sec = document.getElementById('section-matchup-tool');
  if (!sec || sec.dataset.built) return;
  sec.dataset.built = '1';

  sec.innerHTML = `
    <div class="mt-header">
      <div class="pp-title">SIMULADOR DE <span class="pp-title-accent">RUNS</span></div>
      <div class="pp-desc">Monte Carlo 10,000 partidos — modelo xFIP × xwOBA</div>
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
      grid.innerHTML = '<div class="sim-cube-status">No hay partidos programados hoy</div>';
      return;
    }

    grid.innerHTML = games.map(g => `
      <div class="sim-cube" id="cube-${g.game_id}" onclick="openNerdPanel(${g.game_id})">

        <!-- Cherry badge — hidden until pick qualifies -->
        <img src="cherry.png" class="sim-cube-cherry-badge" id="cherry-${g.game_id}" alt="pick" />

        <!-- Teams + pitchers -->
        <div class="sim-cube-top">
          <div class="sim-cube-teams">
            <div class="sim-cube-team-block">
              ${_teamLogoUrl(g.away_team) ? `<img src="${_teamLogoUrl(g.away_team)}" class="sim-cube-logo" alt="${g.away_team}" onerror="this.style.display='none'">` : ''}
              <span class="sim-cube-team">${g.away_team}</span>
            </div>
            <span class="sim-cube-at">@</span>
            <div class="sim-cube-team-block">
              <span class="sim-cube-team">${g.home_team}</span>
              ${_teamLogoUrl(g.home_team) ? `<img src="${_teamLogoUrl(g.home_team)}" class="sim-cube-logo" alt="${g.home_team}" onerror="this.style.display='none'">` : ''}
            </div>
          </div>
          <div class="sim-cube-pitchers">${g.away_pitcher.name} · ${g.home_pitcher.name}</div>
          <span class="sim-cube-lineup-status" id="lineup-status-${g.game_id}"></span>
        </div>

        <!-- Verdict (hidden until sim runs) -->
        <div class="sim-cube-verdict" id="verdict-${g.game_id}" aria-live="polite">
          <span class="sim-cube-direction" id="vdir-${g.game_id}"></span>
          <span class="sim-cube-total"    id="vtot-${g.game_id}"></span>
          <span class="sim-cube-prob"     id="vprob-${g.game_id}"></span>
        </div>

        <!-- Controls -->
        <div class="sim-cube-bottom" onclick="event.stopPropagation()">
          <div class="sim-cube-runs-row">
            <span class="sim-cube-ou-label">O/U LINE</span>
            <div class="runs-chips-wrap" id="runs-chips-${g.game_id}">
              ${_RUNS_PRESETS.map(v =>
                `<button class="runs-chip${v === 8.5 ? ' runs-chip--active' : ''}"
                  onclick="event.stopPropagation();selectRunsLine(this,${g.game_id})"
                  data-val="${v}">${v}</button>`
              ).join('')}
            </div>
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

    // Fetch lineup status in background and update badges
    apiFetch('/api/lineup-status').then(statusMap => {
      Object.assign(_lineupStatusMap, statusMap);
      for (const [gid, sides] of Object.entries(statusMap)) {
        // Run simulator cube badge (either side confirmed)
        const confirmed = (sides.home || sides.away);
        const el = document.getElementById(`lineup-status-${gid}`);
        if (el) {
          if (confirmed) {
            el.textContent = '● LINEUP LISTO';
            el.className = 'sim-cube-lineup-status sim-cube-lineup-status--ready';
          } else {
            el.textContent = '○ Sin lineup';
            el.className = 'sim-cube-lineup-status sim-cube-lineup-status--pending';
          }
        }
      }
    }).catch(() => {});

  } catch (e) {
    document.getElementById('sim-cube-grid').innerHTML =
      '<div class="sim-cube-status">Error al cargar los partidos</div>';
  }
}

async function runCubeSim(game_id) {
  const playBtn = document.getElementById(`play-${game_id}`);
  const activeChip = document.querySelector(`#runs-chips-${game_id} .runs-chip--active`);
  const ou_line = activeChip ? parseFloat(activeChip.dataset.val) : 8.5;
  if (isNaN(ou_line)) return;

  playBtn.disabled = true;
  playBtn.classList.add('sim-cube-play--loading');

  try {
    const d = await apiPost('/api/simulation/ou', { game_id, ou_line });
    _cubeSimData[game_id] = { ...d, ou_line };

    // Update lineup badge based on confirmed result from simulation
    const lsEl = document.getElementById(`lineup-status-${game_id}`);
    if (lsEl) {
      if (d.lineup_confirmed) {
        lsEl.textContent = '● LINEUP LISTO';
        lsEl.className = 'sim-cube-lineup-status sim-cube-lineup-status--ready';
      } else {
        lsEl.textContent = '○ Sin lineup';
        lsEl.className = 'sim-cube-lineup-status sim-cube-lineup-status--pending';
      }
    }

    const isOver    = d.over_prob >= d.under_prob;
    const edgeAbs   = Math.abs(d.edge_pct);
    const rec       = edgeAbs >= 0.05 ? (isOver ? 'OVER' : 'UNDER') : 'SKIP';
    const prob      = Math.round(Math.max(d.over_prob, d.under_prob) * 100);

    const vdir = document.getElementById(`vdir-${game_id}`);
    const vtot = document.getElementById(`vtot-${game_id}`);
    const vprob = document.getElementById(`vprob-${game_id}`);

    if (rec === 'SKIP') {
      vdir.textContent = 'SKIP';
      vdir.className   = 'sim-cube-direction sim-cube-direction--skip';
      vtot.textContent = '';
      vprob.textContent = '';
    } else {
      vdir.textContent = rec;
      vdir.className   = `sim-cube-direction sim-cube-direction--${rec.toLowerCase()}`;
      vtot.textContent = d.avg_total.toFixed(1);
      vprob.textContent = `${prob}%`;
    }

    const cube = document.getElementById(`cube-${game_id}`);
    cube.classList.add('sim-cube--result');

    // Show/hide cherry badge
    const cherry = document.getElementById(`cherry-${game_id}`);
    if (cherry) cherry.classList.toggle('sim-cube-cherry-badge--visible', rec !== 'SKIP');

    // Lock the card — replace controls with result summary + undo button
    const bottom = cube.querySelector('.sim-cube-bottom');
    const dirCls = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : 'skip';
    const bpHome = d.home_bullpen_xfip != null ? d.home_bullpen_xfip.toFixed(2) : '—';
    const bpAway = d.away_bullpen_xfip != null ? d.away_bullpen_xfip.toFixed(2) : '—';

    // Weather summary (compact chip, hidden on domes)
    let wxChipHTML = '';
    const wx = d.weather;
    if (wx && wx.roof !== 'dome') {
      const wLabel = (d.weather_detail || {}).wind_label || 'CROSS';
      const wCls   = wLabel === 'OUT' ? 'wx-out' : wLabel === 'IN' ? 'wx-in' : 'wx-cross';
      wxChipHTML = `
        <div class="sim-cube-locked-wx" title="${Math.round(wx.temp_f)}°F · ${Math.round(wx.wind_mph)} mph ${wLabel} · HR ${wx.humidity_pct}%">
          <span>${Math.round(wx.temp_f)}°</span>
          <span class="sim-cube-locked-bp-sep">·</span>
          <span class="${wCls}">💨 ${Math.round(wx.wind_mph)} ${wLabel}</span>
        </div>`;
    } else if (wx && wx.roof === 'dome') {
      wxChipHTML = `<div class="sim-cube-locked-wx"><span style="opacity:.6">🏟 DOME</span></div>`;
    }

    bottom.innerHTML = `
      <div class="sim-cube-locked-result" onclick="event.stopPropagation()">
        <div class="sim-cube-locked-verdict">
          <span class="sim-cube-locked-rec sim-cube-locked-rec--${dirCls}">${rec}</span>
          <span class="sim-cube-locked-line">O/U ${ou_line}</span>
          <span class="sim-cube-locked-prob">${Math.round(Math.max(d.over_prob, d.under_prob) * 100)}%</span>
        </div>
        <div class="sim-cube-locked-bp">
          <span title="Bullpen xFIP ${d.home_team}">${d.home_team} BP ${bpHome}</span>
          <span class="sim-cube-locked-bp-sep">·</span>
          <span title="Bullpen xFIP ${d.away_team}">${d.away_team} BP ${bpAway}</span>
        </div>
        ${wxChipHTML}
        <div class="sim-cube-locked-actions">
          <button class="sim-cube-edit-btn" onclick="editCubeSim(${game_id}, ${ou_line})">✏ editar</button>
          <button class="sim-cube-undo-btn" onclick="undoCubeSim(${game_id})">↩ undo</button>
        </div>
      </div>`;

    // Auto-save when model recommends OVER or UNDER (not SKIP)
    if (rec !== 'SKIP') {
      try {
        const wxSnap = d.weather || {};
        const wxLabel = (d.weather_detail || {}).wind_label || null;
        await apiPost('/api/matchup-picks', {
          game_id,
          ou_line,
          direction:    rec,
          edge_pct:     d.edge_pct,
          avg_total:    d.avg_total,
          confidence:   d.confidence,
          home_team:    d.home_team,
          away_team:    d.away_team,
          home_pitcher: d.home_pitcher,
          away_pitcher: d.away_pitcher,
          // Weather snapshot at save-time
          weather_factor:     d.weather_factor,
          weather_temp_f:     wxSnap.temp_f ?? null,
          weather_wind_mph:   wxSnap.wind_mph ?? null,
          weather_wind_label: wxLabel,
          weather_roof:       wxSnap.roof ?? null,
        });
        _showToast(`${rec} ${ou_line} — ${d.away_team} @ ${d.home_team} guardado`, 'success');
        const tpSec = document.getElementById('section-todays-picks');
        if (tpSec && tpSec.style.display !== 'none') loadTodaysPicks();
      } catch (err) {
        _showToast(`Error guardando simulación: ${err.message}`, 'error');
      }
    } else {
      _showToast(`SKIP — edge insuficiente (${(Math.abs(d.edge_pct) * 100).toFixed(1)}% < 5%)`, 'info');
    }

  } catch (e) {
    _showToast('Error en simulación: ' + e.message, 'error');
  } finally {
    playBtn.disabled = false;
    playBtn.classList.remove('sim-cube-play--loading');
  }
}

async function undoCubeSim(game_id) {
  // Delete pick from DB
  try { await fetch(`${API}/api/matchup-picks/${game_id}`, { method: 'DELETE' }); } catch (_) {}

  // Reset card state
  delete _cubeSimData[game_id];
  const cube = document.getElementById(`cube-${game_id}`);
  if (!cube) return;
  cube.classList.remove('sim-cube--result');

  // Clear verdict
  const vdir = document.getElementById(`vdir-${game_id}`);
  const vtot = document.getElementById(`vtot-${game_id}`);
  const vprob = document.getElementById(`vprob-${game_id}`);
  if (vdir) { vdir.textContent = ''; vdir.className = 'sim-cube-direction'; }
  if (vtot) vtot.textContent = '';
  if (vprob) vprob.textContent = '';

  // Hide cherry
  const cherry = document.getElementById(`cherry-${game_id}`);
  if (cherry) cherry.classList.remove('sim-cube-cherry-badge--visible');

  // Restore controls
  const bottom = cube.querySelector('.sim-cube-bottom');
  const activeVal = 8.5;
  bottom.innerHTML = `
    <div class="sim-cube-runs-row">
      <span class="sim-cube-ou-label">O/U LINE</span>
      <div class="runs-chips-wrap" id="runs-chips-${game_id}">
        ${_RUNS_PRESETS.map(v =>
          `<button class="runs-chip${v === activeVal ? ' runs-chip--active' : ''}"
            onclick="event.stopPropagation();selectRunsLine(this,${game_id})"
            data-val="${v}">${v}</button>`
        ).join('')}
      </div>
    </div>
    <button class="sim-cube-play" id="play-${game_id}"
            onclick="runCubeSim(${game_id})"
            aria-label="Simular">
      <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
        <path d="M8 5.14v13.72a1 1 0 0 0 1.5.86l10.5-6.86a1 1 0 0 0 0-1.72L9.5 4.28A1 1 0 0 0 8 5.14z"/>
      </svg>
    </button>`;

  const tpSec = document.getElementById('section-todays-picks');
  if (tpSec && tpSec.style.display !== 'none') loadTodaysPicks();
}

function editCubeSim(game_id, prev_ou_line) {
  // Unlock the card for re-simulation — keeps DB pick intact (re-sim will upsert)
  delete _cubeSimData[game_id];
  const cube = document.getElementById(`cube-${game_id}`);
  if (!cube) return;
  cube.classList.remove('sim-cube--result');

  // Clear verdict display
  const vdir  = document.getElementById(`vdir-${game_id}`);
  const vtot  = document.getElementById(`vtot-${game_id}`);
  const vprob = document.getElementById(`vprob-${game_id}`);
  if (vdir)  { vdir.textContent = ''; vdir.className = 'sim-cube-direction'; }
  if (vtot)  vtot.textContent  = '';
  if (vprob) vprob.textContent = '';

  const cherry = document.getElementById(`cherry-${game_id}`);
  if (cherry) cherry.classList.remove('sim-cube-cherry-badge--visible');

  // Restore chips with the previously simulated line active
  const activeVal = prev_ou_line;
  const bottom = cube.querySelector('.sim-cube-bottom');
  bottom.innerHTML = `
    <div class="sim-cube-runs-row">
      <span class="sim-cube-ou-label">O/U LINE</span>
      <div class="runs-chips-wrap" id="runs-chips-${game_id}">
        ${_RUNS_PRESETS.map(v =>
          `<button class="runs-chip${v === activeVal ? ' runs-chip--active' : ''}"
            onclick="event.stopPropagation();selectRunsLine(this,${game_id})"
            data-val="${v}">${v}</button>`
        ).join('')}
      </div>
    </div>
    <button class="sim-cube-play" id="play-${game_id}"
            onclick="runCubeSim(${game_id})"
            aria-label="Simular">
      <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">
        <path d="M8 5.14v13.72a1 1 0 0 0 1.5.86l10.5-6.86a1 1 0 0 0 0-1.72L9.5 4.28A1 1 0 0 0 8 5.14z"/>
      </svg>
    </button>`;
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

  // ── Weather chip (null if no forecast or unknown park) ──
  const wx     = d.weather;
  const wxDet  = d.weather_detail || {};
  const wxFac  = d.weather_factor != null ? d.weather_factor : 1;
  let wxRowHTML = '';
  if (wx) {
    // Arrow rotated to show wind direction the air is blowing TOWARD
    // (Open-Meteo dir is "from", so TOWARD = dir + 180°)
    const arrowRot  = (wx.wind_dir_deg + 180) % 360;
    const roofTag   = wx.roof === 'dome' ? ' · <span style="opacity:.75">DOME · weather ignored</span>'
                    : wx.roof === 'retractable' ? ' · <span style="opacity:.75">retractable</span>'
                    : '';
    const wLabel    = wxDet.wind_label || '—';
    const windColor = wLabel === 'OUT' ? 'var(--red-light)'
                    : wLabel === 'IN'  ? '#22c55e'
                    : 'var(--text-muted)';
    const factorPct = ((wxFac - 1) * 100);
    const factorStr = (factorPct >= 0 ? '+' : '') + factorPct.toFixed(1) + '%';
    const factorColor = factorPct > 0.5 ? 'var(--red-light)' : factorPct < -0.5 ? '#22c55e' : 'var(--text-muted)';
    const rainStr   = wx.precip_pct > 20 ? ` · ☔ ${wx.precip_pct}%` : '';
    wxRowHTML = `
        <div class="mt-context-row">
          <span class="mt-context-side" style="font-size:16px;letter-spacing:0">🌤</span>
          <span class="mt-context-detail">
            <strong>${Math.round(wx.temp_f)}°F</strong> ·
            <span style="display:inline-block;transform:rotate(${arrowRot}deg);color:${windColor}">➤</span>
            <strong style="color:${windColor}">${Math.round(wx.wind_mph)} mph ${wLabel}</strong> ·
            HR ${wx.humidity_pct}%${rainStr}${roofTag}
            · <strong style="color:${factorColor}">runs ${factorStr}</strong>
          </span>
        </div>`;
  }

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
        </div>${wxRowHTML}
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

  list.innerHTML = '<div class="loading-block">Cargando partidos de hoy…</div>';

  try {
    const games = await apiFetch('/api/games/today');

    if (!games.length) {
      list.innerHTML = '<div class="loading-block">No hay partidos programados hoy.</div>';
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
        ${isRecommended ? '<div class="recommended-badge">Pick Recomendado</div>' : ''}
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
    const [pitchers, picks, stats, lineupStatus] = await Promise.all([
      apiFetch('/api/pitchers/today').catch(() => []),
      apiFetch('/api/picks/today').catch(() => []),
      apiFetch('/api/stats/summary').catch(() => ({})),
      apiFetch('/api/lineup-status').catch(() => ({})),
    ]);
    Object.assign(_lineupStatusMap, lineupStatus);
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
const _KLINE_PRESETS = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5];

// Runs (O/U) preset chips — only .5 lines
const _RUNS_PRESETS = [4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5];

function selectRunsLine(chip, game_id) {
  const wrap = document.getElementById(`runs-chips-${game_id}`);
  if (!wrap) return;
  wrap.querySelectorAll('.runs-chip').forEach(c => c.classList.remove('runs-chip--active'));
  chip.classList.add('runs-chip--active');
}

function _klineEntry() {
  // pointerdown fires before blur on both mouse and touch; preventDefault
  // stops the input from losing focus so the chip tap registers in one go
  // on mobile. touchstart is a backup for older iOS Safari that doesn't
  // dispatch pointer events consistently inside overflow:hidden containers.
  const chips = _KLINE_PRESETS.map(v =>
    `<button type="button" class="kline-chip"
             onpointerdown="event.preventDefault(); _pickLine(this,${v})"
             ontouchstart="event.preventDefault(); _pickLine(this,${v})"
             onclick="event.preventDefault()">${v}</button>`
  ).join('');
  return `
    <div class="kline-entry">
      <div class="kline-field">
        <input type="text" inputmode="decimal" class="k-line-input" placeholder="5.5"
               onfocus="this.closest('.kline-entry').classList.add('open')"
               onblur="setTimeout(()=>{const e=this.closest('.kline-entry'); if(e && !e.contains(document.activeElement)) e.classList.remove('open');},250)">
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

// Mobile pitcher data store (safeId → {p, pick, avatarHtml, color, lineCell, modelCell, edgeCell, ciCell, confPct, resultCell})
const _pitcherStore = {};

// Returns true when the batting lineup that faces this pitcher is confirmed
function _pitcherLineupConfirmed(p) {
  // If already set by a K-line submission, trust it
  if (p.lineup_confirmed) return true;
  // Otherwise check the pre-loaded status map
  const sides = _lineupStatusMap[p.game_id];
  if (!sides) return false;
  // Home pitcher faces away batters, away pitcher faces home batters
  return p.home_away === 1 ? sides.away : sides.home;
}

function renderPropsTable(pitchers, picksMap = {}) {
  const tbody = document.getElementById('props-tbody');
  if (!tbody) return;

  if (!pitchers.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="loading-cell">No hay pitchers en el slate de hoy.</td></tr>';
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
      ? `<div class="pp-kline-set"><strong class="pp-kline-val">${num(pick.k_line, 1)}</strong><button class="pp-kline-edit-btn" onclick="editKLine(this)" title="Cambiar línea">✎</button></div>`
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
          <button type="button" class="pp-cancel-btn" onclick="cancelPick(this,event)" title="Scratched / injured">&#8630;</button>
          <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input" placeholder="Ks">
          <button class="tp-log-btn" onclick="submitResult(this)">LOG</button>
        </div>`;
    } else if (pick) {
      resultCell = `
        <div style="display:flex;gap:5px;align-items:center">
          <button type="button" class="pp-cancel-btn" onclick="cancelPick(this,event)" title="Undo pick">&#8630;</button>
          <span style="font-size:11px;color:var(--text-dim)">SKIP</span>
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
        <td>${_pitcherLineupConfirmed(p) ? '<span class="pp-lineup-badge">LINEUP</span>' : '<span style="color:var(--text-dim);font-size:11px">—</span>'}</td>
        <td>${p.recent_k_rate != null ? `<span class="pp-forma-val" title="K/9 últimas 3 salidas">${p.recent_k_rate.toFixed(1)}</span>` : '<span style="color:var(--text-dim);font-size:11px">—</span>'}</td>
        <td>${resultCell}</td>
      </tr>
    `;
  }).join('');

  // ── Render mobile pitcher cards ──
  _renderMobilePitcherCards(pitchers, picksMap);
}

function _renderMobilePitcherCards(pitchers, picksMap) {
  const grid = document.getElementById('props-mobile-cards');
  if (!grid) return;

  if (!pitchers.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;color:var(--text-muted);padding:24px 0;text-align:center">No hay pitchers hoy.</div>';
    return;
  }

  grid.innerHTML = pitchers.map(p => {
    const name      = p.name;
    const pick      = picksMap[name.toLowerCase()];
    const safeId    = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
    const color     = _avatarColor(name);
    const initials  = _initials(name);
    const photoUrl  = _headshotUrl(p.pitcher_id);
    const confPct   = ((p.confidence_level || 1) * 20);
    const rec       = pick ? (pick.recommendation || 'SKIP') : 'SKIP';
    const isTopPick = pick && (rec === 'OVER' || rec === 'UNDER') && (p.confidence_level || 0) >= 4;

    const avatarHtml = photoUrl
      ? `<img src="${photoUrl}" style="width:100%;height:100%;object-fit:cover;border-radius:50%"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
             alt="${name}" />
         <div class="pp-avatar" style="background:${color};display:none;position:absolute;inset:0;border-radius:50%;align-items:center;justify-content:center;font-size:14px;font-weight:700">${initials}</div>`
      : `<div class="pp-avatar" style="background:${color};position:absolute;inset:0;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700">${initials}</div>`;

    const oppLogo  = _teamLogoUrl(p.opponent);
    const oppHtml  = oppLogo
      ? `<img src="${oppLogo}" width="14" height="14" style="object-fit:contain;vertical-align:middle;margin-right:3px" onerror="this.style.display='none'">vs ${p.opponent}`
      : `vs ${p.opponent || '—'}`;

    const lineCell = pick
      ? `<span class="pmc-line-val">${num(pick.k_line, 1)}</span>`
      : _klineEntry();

    const cherryHtml = (rec === 'OVER' || rec === 'UNDER')
      ? `<img src="cherry.png" class="pmc-cherry" alt="${rec}" />`
      : '';

    // Store data for nerd drawer
    const edgeCell   = pick ? _edgePill(pick.edge) : '<span class="pp-edge-pill none">—</span>';
    const ciCell     = pick ? _ciBar(pick.ci_low, pick.ci_high, pick.model_proj) : _ciBar(null, null, null);
    _pitcherStore[safeId] = { p, pick, confPct, edgeCell, ciCell, rec, initials, color, photoUrl };

    return `
      <div class="pmc-card" id="pmc-${safeId}" data-pitcher="${name}" data-team="${p.team}">
        <div class="pmc-head" onclick="openPitcherNerd('${safeId}')">
          <div class="pmc-avatar-wrap" style="background:${color};position:relative">${avatarHtml}</div>
          <div class="pmc-info">
            <div class="pmc-name">${name}</div>
            <div class="pmc-opp">${oppHtml}</div>
          </div>
          ${cherryHtml}
          ${isTopPick ? '<span class="pmc-top-badge">TOP PICK</span>' : ''}
        </div>
        <div class="pmc-line" id="pmc-line-${safeId}">${lineCell}</div>
      </div>`;
  }).join('');
}

function openPitcherNerd(safeId) {
  const d = _pitcherStore[safeId];
  if (!d) return;
  const { p, pick, confPct, edgeCell, ciCell, rec, initials, color, photoUrl } = d;

  const modelNum = pick && pick.model_proj != null ? num(pick.model_proj, 1) : '—';
  const kLine    = pick ? num(pick.k_line, 1) : '—';

  const dirArrow = rec === 'OVER' ? '↑' : rec === 'UNDER' ? '↓' : '';
  const dirClass = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : 'skip';

  let resultHtml = '';
  if (pick && pick.actual_ks != null) {
    const res  = pick.result || '';
    const rc   = res === 'W' ? 'win' : res === 'L' ? 'loss' : 'push';
    const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
    resultHtml = `<div style="text-align:center;margin-top:16px"><span class="tp-result-pill ${rc}">${icon} ${pick.actual_ks}K</span></div>`;
  } else if (pick && rec !== 'SKIP') {
    resultHtml = `
      <div class="tp-log-row" style="justify-content:center;margin-top:16px">
        <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input" placeholder="Ks">
        <button class="tp-log-btn" onclick="submitResultFromNerd('${safeId}',this)">LOG</button>
      </div>`;
  }

  const avatarInner = photoUrl
    ? `<img src="${photoUrl}" style="width:100%;height:100%;object-fit:cover;border-radius:50%" onerror="this.style.display='none'"><div class="pp-avatar" style="background:${color};display:none;position:absolute;inset:0;border-radius:50%;align-items:center;justify-content:center;font-size:18px;font-weight:700">${initials}</div>`
    : `<div class="pp-avatar" style="background:${color};position:absolute;inset:0;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700">${initials}</div>`;

  // Set data-pitcher/data-team on the drawer so submitLine can find it
  const drawerEl = document.getElementById('pitcher-nerd-drawer');
  drawerEl.dataset.pitcher = p.name;
  drawerEl.dataset.team    = p.team || '';

  const kLineCell = pick
    ? `<div style="font-family:var(--font-display);font-size:24px;color:var(--text);display:flex;align-items:center;justify-content:center;gap:8px">
         ${kLine}
         <button class="pp-kline-edit-btn" onclick="editKLineNerd('${safeId}')" title="Cambiar línea">✎</button>
       </div>`
    : `<div id="nerd-kline-entry">${_klineEntry()}</div>`;

  document.getElementById('pitcher-nerd-content').innerHTML = `
    <div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding-top:8px">
      <div style="width:52px;height:52px;border-radius:50%;overflow:hidden;flex-shrink:0;position:relative;background:${color}">${avatarInner}</div>
      <div>
        <div class="nerd-drawer-title" style="font-size:22px">${p.name}</div>
        <div class="nerd-drawer-subtitle">vs ${p.opponent || '—'}</div>
        <span class="${_lineupStatusMap[p.game_id] ? 'pn-lineup-badge pn-lineup-badge--ready' : 'pn-lineup-badge pn-lineup-badge--pending'}">${_lineupStatusMap[p.game_id] ? '● LINEUP LISTO' : '○ Sin lineup'}</span>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px">
      <div id="nerd-kline-cell" style="background:var(--bg3);border-radius:10px;padding:12px 8px;text-align:center">
        <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:4px">K LINE</div>
        ${kLineCell}
      </div>
      <div style="background:var(--bg3);border-radius:10px;padding:12px 8px;text-align:center">
        <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:4px">MODEL</div>
        <div style="font-family:var(--font-display);font-size:24px;color:var(--text)">${modelNum} ${dirArrow ? `<span class="pp-model-dir ${dirClass}" style="font-size:16px">${dirArrow}</span>` : ''}</div>
      </div>
      <div style="background:var(--bg3);border-radius:10px;padding:12px 8px;text-align:center">
        <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:4px">CONF.</div>
        <div style="font-family:var(--font-display);font-size:24px;color:var(--text)">${confPct}%</div>
      </div>
    </div>

    <div style="margin-bottom:12px">
      <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:6px">EDGE</div>
      ${edgeCell}
    </div>

    <div style="margin-bottom:12px">
      <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:6px">CONFIDENCE INTERVAL</div>
      ${ciCell}
    </div>

    <div style="margin-bottom:4px">
      <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:6px">PICK</div>
      <span class="pp-edge-pill ${dirClass}" style="font-size:14px;padding:5px 14px">${rec}</span>
    </div>

    <div style="margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div class="pn-stat-cell"><div class="pn-stat-label">K%</div><div class="pn-stat-val">${p.k_pct != null ? (p.k_pct*100).toFixed(1)+'%' : '—'}</div></div>
      <div class="pn-stat-cell"><div class="pn-stat-label">K-BB%</div><div class="pn-stat-val">${p.kbb_pct != null ? (p.kbb_pct*100).toFixed(1)+'%' : '—'}</div></div>
      <div class="pn-stat-cell"><div class="pn-stat-label">SwStr%</div><div class="pn-stat-val">${p.swstr_pct != null ? (p.swstr_pct*100).toFixed(1)+'%' : '—'}</div></div>
      <div class="pn-stat-cell"><div class="pn-stat-label">Put-Away%</div><div class="pn-stat-val">${p.putaway_pct != null ? (p.putaway_pct*100).toFixed(1)+'%' : '—'}</div></div>
      <div class="pn-stat-cell"><div class="pn-stat-label">OPP K% vs ${p.hand||'—'}</div><div class="pn-stat-val">${p.opp_k_pct != null ? (p.opp_k_pct*100).toFixed(1)+'%' : '—'}</div></div>
      <div class="pn-stat-cell"><div class="pn-stat-label">K/9 L3</div><div class="pn-stat-val">${p.recent_k_rate != null ? p.recent_k_rate.toFixed(1) : '—'}</div></div>
    </div>

    ${resultHtml}
  `;

  document.getElementById('pitcher-nerd-overlay').classList.add('open');
  document.getElementById('pitcher-nerd-drawer').classList.add('open');
}

function closePitcherNerd() {
  document.getElementById('pitcher-nerd-overlay').classList.remove('open');
  document.getElementById('pitcher-nerd-drawer').classList.remove('open');
}

async function submitResultFromNerd(safeId, btn) {
  const d = _pitcherStore[safeId];
  if (!d) return;
  const nerdInput = btn.previousElementSibling;
  const actualKs  = parseFloat(nerdInput.value.replace(',', '.'));
  if (isNaN(actualKs) || actualKs < 0) { nerdInput.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';

  try {
    const today  = new Date().toLocaleDateString('en-CA');
    const result = await apiPost('/api/picks/result', { date: today, pitcher_name: d.p.name, actual_ks: actualKs });
    const res    = result.result || '';
    const rc     = res === 'W' ? 'win' : res === 'L' ? 'loss' : res === 'P' ? 'push' : 'pending';
    const icon   = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
    const label  = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    _showToast(`${d.p.name} — ${actualKs} Ks → ${label}`, res === 'W' ? 'success' : 'info');
    // Also update desktop table row if visible
    const row = document.getElementById(`prop-row-${safeId}`);
    if (row) row.querySelectorAll('td')[9].innerHTML = `<span class="tp-result-pill ${rc}">${icon} ${actualKs}K</span>`;
    closePitcherNerd();
  } catch (e) {
    btn.disabled    = false;
    btn.textContent = 'LOG';
    _showToast('Error: ' + e.message, 'error');
  }
}

// ── TODAY'S PICKS PAGE ────────────────────────────────────────────────────────

function _tpCardResultHtml(pick) {
  if (pick.actual_ks != null && pick.result) {
    const rc   = pick.result === 'W' ? 'win' : pick.result === 'L' ? 'loss' : 'push';
    const icon = pick.result === 'W' ? '✓' : pick.result === 'L' ? '✕' : '·';
    return `<span class="tp-result-pill ${rc}">${icon} ${pick.actual_ks}K</span>`;
  }
  const safeId = pick.pitcher_name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
  return `
    <div class="tp-log-row">
      <input type="text" inputmode="decimal" class="tp-ks-input"
             placeholder="Ks" data-pitcher="${pick.pitcher_name}" data-safeid="${safeId}">
      <button class="tp-log-btn" onclick="submitTodaysPick(this)">LOG</button>
    </div>`;
}

function _buildTpCards(actionable, matchupPicks) {
  const cardsGrid = document.getElementById('tp-cards-grid');
  if (!cardsGrid) return;

  cardsGrid.innerHTML = actionable.map(pick => {
    const name    = pick.pitcher_name;
    const safeId  = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
    const rec     = pick.recommendation;
    const recCls  = rec === 'OVER' ? 'over' : 'under';
    const photoUrl = _headshotUrl(pick.pitcher_id);
    const color    = _avatarColor(name);
    const initials = _initials(name);
    const teamLogo = _teamLogoUrl(pick.team);
    const oppLogo  = _teamLogoUrl(pick.opponent);
    const edgeSign = pick.edge >= 0 ? '+' : '';
    const edgeCls  = pick.edge >= 0 ? 'over' : 'under';

    const avatarHtml = photoUrl
      ? `<img src="${photoUrl}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
              alt="${name}">
         <div class="tp-row-avatar-fallback" style="background:${color};display:none">${initials}</div>`
      : `<div class="tp-row-avatar-fallback" style="background:${color}">${initials}</div>`;

    const teamLogoHtml = teamLogo
      ? `<img src="${teamLogo}" class="tp-inline-team-logo" alt="${pick.team}">`
      : '';
    const oppLogoHtml = oppLogo
      ? `<img src="${oppLogo}" class="tp-opp-logo" alt="${pick.opponent}"
              onerror="this.style.display='none'">`
      : '';

    const settled = pick.actual_ks != null && pick.result;
    let cardCls = settled
      ? (pick.result === 'W' ? 'tp-card--win' : pick.result === 'L' ? 'tp-card--loss' : 'tp-card--push')
      : `tp-card--${recCls}`;

    return `
      <div class="tp-card ${cardCls}" id="tpc-${safeId}">
        <div class="tp-card-head">
          <div class="tp-card-avatar">${avatarHtml}</div>
          <div class="tp-card-info">
            <div class="tp-card-name">${name}${teamLogoHtml}</div>
            <div class="tp-card-opp">
              <span class="tp-vs-label">vs</span>
              ${oppLogoHtml || `<span class="tp-dim">${pick.opponent || '—'}</span>`}
            </div>
          </div>
          <span class="tp-rec-pill ${recCls}">${rec === 'OVER' ? '▲' : '▼'} ${rec}</span>
        </div>
        <div class="tp-card-stats">
          <div class="tp-cs">
            <span class="tp-cs-lbl">línea</span>
            <span class="tp-cs-val">${num(pick.k_line, 1)}</span>
          </div>
          <div class="tp-cs">
            <span class="tp-cs-lbl">proy.</span>
            <span class="tp-cs-val">${pick.model_proj != null ? num(pick.model_proj, 1) : '—'}</span>
          </div>
          <div class="tp-cs">
            <span class="tp-cs-lbl">ventaja</span>
            <span class="tp-cs-val tp-edge ${edgeCls}">${edgeSign}${num(pick.edge, 1)}K</span>
          </div>
        </div>
        <div class="tp-card-foot" id="tpc-result-${safeId}">
          ${_tpCardResultHtml(pick)}
        </div>
      </div>`;
  }).join('');

  // Matchup cards
  let matchupSection = document.getElementById('tp-matchup-cards');
  if (matchupPicks.length) {
    if (!matchupSection) {
      matchupSection = document.createElement('div');
      matchupSection.id = 'tp-matchup-cards';
      matchupSection.className = 'tp-matchup-cards';
      cardsGrid.after(matchupSection);
    }
    matchupSection.innerHTML = `
      <div class="tp-section-divider">o/u picks — matchup simulator</div>
      ${matchupPicks.map(mp => {
        const dir    = mp.direction;
        const dirCls = dir === 'OVER' ? 'over' : 'under';
        const edgePct = (mp.edge_pct * 100).toFixed(1);
        const edgeSign = mp.edge_pct >= 0 ? '+' : '';
        const awayLogo = _teamLogoUrl(mp.away_team);
        const homeLogo = _teamLogoUrl(mp.home_team);
        const settled = mp.result;
        const mcCls = settled
          ? (mp.result === 'W' ? 'tp-matchup-card--win' : mp.result === 'L' ? 'tp-matchup-card--loss' : 'tp-matchup-card--push')
          : `tp-matchup-card--${dirCls}`;

        let resultHtml;
        if (mp.result) {
          const rc   = mp.result === 'W' ? 'win' : mp.result === 'L' ? 'loss' : 'push';
          const icon = mp.result === 'W' ? '✓' : mp.result === 'L' ? '✕' : '·';
          resultHtml = `
            <div class="tp-log-row">
              <span class="tp-result-pill ${rc}">${icon} ${mp.actual_total}</span>
              <button class="tp-edit-result-btn" onclick="editMatchupResult(this, ${mp.game_id}, ${mp.actual_total})" title="Editar resultado">✏</button>
            </div>`;
        } else {
          resultHtml = `
            <div class="tp-log-row">
              <input type="text" inputmode="decimal" class="tp-ks-input"
                     placeholder="Total" data-gameid="${mp.game_id}">
              <button class="tp-log-btn" onclick="submitMatchupResult(this)">LOG</button>
            </div>`;
        }

        return `
          <div class="tp-matchup-card ${mcCls}">
            <div class="tp-mc-head">
              <div class="tp-mc-teams">
                ${awayLogo ? `<img src="${awayLogo}" class="tp-opp-logo" alt="${mp.away_team}" onerror="this.style.display='none'">` : `<span>${mp.away_team}</span>`}
                <span class="tp-vs-label">@</span>
                ${homeLogo ? `<img src="${homeLogo}" class="tp-opp-logo" alt="${mp.home_team}" onerror="this.style.display='none'">` : `<span>${mp.home_team}</span>`}
              </div>
              <span class="tp-rec-pill ${dirCls}">${dir === 'OVER' ? '▲' : '▼'} ${dir}</span>
            </div>
            <div class="tp-mc-pitchers">${mp.away_pitcher || '?'} · ${mp.home_pitcher || '?'}</div>
            <div class="tp-mc-stats">
              <div class="tp-cs"><span class="tp-cs-lbl">línea</span><span class="tp-cs-val">${num(mp.ou_line, 1)}</span></div>
              <div class="tp-cs"><span class="tp-cs-lbl">sim prom</span><span class="tp-cs-val">${num(mp.avg_total, 1)}</span></div>
              <div class="tp-cs"><span class="tp-cs-lbl">ventaja</span><span class="tp-cs-val tp-edge ${dirCls}">${edgeSign}${edgePct}%</span></div>
            </div>
            <div class="tp-mc-foot">${resultHtml}</div>
          </div>`;
      }).join('')}`;
  } else if (matchupSection) {
    matchupSection.remove();
  }
}

async function loadTodaysPicks() {
  const tbody   = document.getElementById('tp-tbody');
  const slateEl = document.getElementById('tp-slate-text');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" class="loading-cell">Loading…</td></tr>';

  try {
    const [picks, matchupPicks] = await Promise.all([
      apiFetch('/api/picks/today').catch(() => []),
      apiFetch('/api/matchup-picks/today').catch(() => []),
    ]);

    const actionable = picks.filter(p => p.recommendation === 'OVER' || p.recommendation === 'UNDER');
    const totalPicks = actionable.length + matchupPicks.length;

    const today  = new Date();
    const dateStr = today.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase();
    if (slateEl) slateEl.textContent = `${dateStr}  ·  ${totalPicks} pick${totalPicks !== 1 ? 's' : ''} guardado${totalPicks !== 1 ? 's' : ''}`;

    if (!actionable.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="loading-cell" style="padding:40px 0;color:var(--text-dim)">Sin picks accionables aún — ingresá K-líneas en Análisis de Pitchers.</td></tr>';
    }

    if (actionable.length) {
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
    }

    // ── Matchup O/U picks section ─────────────────────────────────────────────
    let mpSection = document.getElementById('tp-matchup-section');
    if (matchupPicks.length) {
      if (!mpSection) {
        mpSection = document.createElement('div');
        mpSection.id = 'tp-matchup-section';
        tbody.closest('.table-wrap').after(mpSection);
      }
      mpSection.innerHTML = `
        <div class="tp-section-divider">O/U PICKS — MATCHUP SIMULATOR</div>
        <div class="table-wrap tp-table-wrap">
          <table class="tp-table">
            <thead>
              <tr>
                <th>PARTIDO</th>
                <th>PITCHERS</th>
                <th>LÍNEA</th>
                <th>PROM SIM</th>
                <th>EDGE</th>
                <th>PICK</th>
                <th>RESULTADO</th>
              </tr>
            </thead>
            <tbody>
              ${matchupPicks.map(mp => {
                const dir    = mp.direction;
                const dirCls = dir === 'OVER' ? 'over' : 'under';
                const edgePct = (mp.edge_pct * 100).toFixed(1);
                const edgeSign = mp.edge_pct >= 0 ? '+' : '';
                const awayLogo = _teamLogoUrl(mp.away_team);
                const homeLogo = _teamLogoUrl(mp.home_team);
                const teamsHtml = `
                  <div class="tp-opp-cell" style="gap:6px">
                    ${awayLogo ? `<img src="${awayLogo}" class="tp-opp-logo" alt="${mp.away_team}" onerror="this.style.display='none'">` : `<span>${mp.away_team}</span>`}
                    <span class="tp-vs-label">@</span>
                    ${homeLogo ? `<img src="${homeLogo}" class="tp-opp-logo" alt="${mp.home_team}" onerror="this.style.display='none'">` : `<span>${mp.home_team}</span>`}
                  </div>`;

                let resultCell;
                if (mp.result) {
                  const rc   = mp.result === 'W' ? 'win' : mp.result === 'L' ? 'loss' : 'push';
                  const icon = mp.result === 'W' ? '✓' : mp.result === 'L' ? '✕' : '·';
                  resultCell = `
                    <div class="tp-log-row">
                      <span class="tp-result-pill ${rc}">${icon} ${mp.actual_total}</span>
                      <button class="tp-edit-result-btn" onclick="editMatchupResult(this, ${mp.game_id}, ${mp.actual_total})" title="Editar resultado">✏</button>
                    </div>`;
                } else {
                  resultCell = `
                    <div class="tp-log-row">
                      <input type="text" inputmode="decimal" class="tp-ks-input"
                             placeholder="Total" data-gameid="${mp.game_id}">
                      <button class="tp-log-btn" onclick="submitMatchupResult(this)">LOG</button>
                    </div>`;
                }

                return `
                  <tr class="tp-row${mp.result === 'W' ? ' tp-row-win' : mp.result === 'L' ? ' tp-row-loss' : mp.result === 'P' ? ' tp-row-push' : ''}">
                    <td>${teamsHtml}</td>
                    <td style="font-size:11px;color:var(--text-muted)">${mp.away_pitcher || '?'} · ${mp.home_pitcher || '?'}</td>
                    <td><strong>${num(mp.ou_line, 1)}</strong></td>
                    <td>${num(mp.avg_total, 1)}</td>
                    <td><span class="tp-edge ${dirCls}">${edgeSign}${edgePct}%</span></td>
                    <td><span class="tp-rec-pill ${dirCls}">${dir === 'OVER' ? '▲' : '▼'} ${dir}</span></td>
                    <td>${resultCell}</td>
                  </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>`;
    } else if (mpSection) {
      mpSection.remove();
    }

    // Build mobile bet cards
    _buildTpCards(actionable, matchupPicks);

  } catch (e) {
    console.error(e);
    tbody.innerHTML = `<tr><td colspan="7" class="loading-cell">Error: ${e.message}</td></tr>`;
  }
}

async function submitMatchupResult(btn) {
  const input  = btn.previousElementSibling;
  const gameId = parseInt(input.dataset.gameid);
  const total  = parseFloat(input.value.replace(',', '.'));
  if (isNaN(total) || total < 0) { input.focus(); return; }

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  try {
    const result = await apiPost('/api/matchup-picks/result', { game_id: gameId, actual_total: total });
    const res  = result.result || '';
    const rc   = res === 'W' ? 'win' : res === 'L' ? 'loss' : 'push';
    const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
    const container = btn.closest('td') || btn.closest('.tp-mc-foot') || btn.closest('.tp-card-foot');
    const logRow = btn.closest('.tp-log-row');
    if (logRow) {
      logRow.innerHTML = `
        <span class="tp-result-pill ${rc}">${icon} ${total}</span>
        <button class="tp-edit-result-btn" onclick="editMatchupResult(this, ${gameId}, ${total})" title="Editar resultado">✏</button>`;
    } else if (container) {
      container.innerHTML = `
        <div class="tp-log-row">
          <span class="tp-result-pill ${rc}">${icon} ${total}</span>
          <button class="tp-edit-result-btn" onclick="editMatchupResult(this, ${gameId}, ${total})" title="Editar resultado">✏</button>
        </div>`;
    }
    const row = btn.closest('tr');
    if (row) {
      row.classList.remove('tp-row-win','tp-row-loss','tp-row-push');
      row.classList.add(`tp-row-${rc}`);
    }
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : 'PUSH';
    _showToast(`O/U ${total} → ${label}`, res === 'W' ? 'success' : 'info');
    // Refresh O/U tracker summary if visible
    const trkSec = document.getElementById('section-tracker');
    if (trkSec && trkSec.style.display !== 'none') {
      apiFetch('/api/matchup-picks/history').then(d => renderOUTrackerTable(d.picks, d.summary)).catch(() => {});
    }
  } catch (e) {
    btn.disabled    = false;
    btn.textContent = 'LOG';
    input.disabled  = false;
    _showToast('Error: ' + e.message, 'error');
  }
}

function editMatchupResult(btn, game_id, current_total) {
  // Replace the settled pill with a pre-filled input for re-logging
  const row = btn.closest('.tp-log-row');
  if (!row) return;
  row.innerHTML = `
    <input type="text" inputmode="decimal" class="tp-ks-input"
           placeholder="Total" data-gameid="${game_id}"
           value="${current_total}" style="width:60px">
    <button class="tp-log-btn" onclick="submitMatchupResult(this)">OK</button>`;
  // Focus and select existing value for quick editing
  const inp = row.querySelector('input');
  if (inp) { inp.focus(); inp.select(); }
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
    const pill = `<span class="tp-result-pill ${rc}">${icon} ${actualKs}K</span>`;

    const resultEl = document.getElementById(`tp-result-${safeId}`);
    if (resultEl) resultEl.innerHTML = pill;

    const row = document.getElementById(`tp-row-${safeId}`);
    if (row) {
      row.classList.remove('tp-row-win','tp-row-loss','tp-row-push');
      row.classList.add(`tp-row-${rc}`);
    }

    // Sync mobile card
    const card = document.getElementById(`tpc-${safeId}`);
    if (card) {
      card.className = card.className.replace(/tp-card--(over|under|win|loss|push)/, `tp-card--${rc}`);
      const foot = document.getElementById(`tpc-result-${safeId}`);
      if (foot) foot.innerHTML = pill;
    }

    // Re-render the desktop cube so the PnL row appears immediately
    const cube = document.getElementById(`tpcube-${safeId}`);
    if (cube && _lastTodaysPicks) {
      const idx = _lastTodaysPicks.findIndex(pp => pp.pitcher_name === name);
      if (idx >= 0) {
        const merged = { ...(_lastTodaysPicks[idx] || {}), ...result, actual_ks: actualKs };
        _lastTodaysPicks[idx] = merged;
        cube.outerHTML = _buildTpCubeCard(merged);
      }
    }

    const pnlStr = result.pnl_pesos != null ? ` · ${money(result.pnl_pesos, true)}` : '';
    _showToast(`${name} — ${actualKs} Ks → ${rl}${pnlStr}`, res === 'W' ? 'success' : 'info');
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
async function cancelPick(btn, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  console.log('[cancelPick] clicked', btn);
  const row  = btn.closest('tr');
  if (!row) { console.error('[cancelPick] no row found for btn', btn); return; }
  const name = row.dataset.pitcher;
  if (!name) { console.error('[cancelPick] no data-pitcher on row', row); return; }
  const today = new Date().toLocaleDateString('en-CA');
  console.log('[cancelPick] cancelling', name, 'on', today);

  btn.disabled = true;
  btn.textContent = '…';

  try {
    const res = await apiPost('/api/picks/cancel', { date: today, pitcher_name: name });
    console.log('[cancelPick] success', res);
    // Reset row back to empty state (line-entry)
    const cells = row.querySelectorAll('td');
    cells[2].innerHTML = _klineEntry();
    cells[3].innerHTML = '<span style="color:var(--text-dim)">—</span>';
    cells[4].innerHTML = '<span class="pp-edge-pill none">—</span>';
    cells[5].innerHTML = _ciBar(null, null, null);
    // cells[6]=CONFIANZA, cells[7]=LINEUP, cells[8]=FORMA — leave untouched
    cells[9].innerHTML = '<span style="color:var(--text-dim);font-size:11px">—</span>';
    // Remove cherry badge from pitcher cell
    const pitcherCell = cells[0].querySelector('.pp-pitcher-cell');
    if (pitcherCell) {
      const cherry = pitcherCell.querySelector('.pp-cherry-badge, .pp-cherry-spacer');
      if (cherry) {
        const spacer = document.createElement('span');
        spacer.className = 'pp-cherry-spacer';
        pitcherCell.replaceChild(spacer, cherry);
      }
    }
    row.classList.remove('pp-top-pick-row');
  } catch (e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = '↺';
    btn.style.borderColor = 'var(--red)';
  }
}

// Replace a settled k-line cell with the entry widget (pre-filled with current value)
function editKLine(btn) {
  const cell = btn.closest('td');
  const currentVal = cell.querySelector('.pp-kline-val')?.textContent?.trim() || '';
  cell.innerHTML = _klineEntry();
  const input = cell.querySelector('.k-line-input');
  if (currentVal) input.value = currentVal;
  input.focus();
}

// Edit k-line from inside the nerd drawer (mobile)
function editKLineNerd(safeId) {
  const cell = document.getElementById('nerd-kline-cell');
  if (!cell) return;
  const currentVal = _pitcherStore[safeId]?.pick?.k_line != null
    ? String(_pitcherStore[safeId].pick.k_line)
    : '';
  cell.innerHTML = `
    <div style="font-size:10px;color:var(--text-muted);letter-spacing:1px;margin-bottom:6px">K LINE</div>
    ${_klineEntry()}`;
  const input = cell.querySelector('.k-line-input');
  if (currentVal) input.value = currentVal;
  input.focus();
}

// Called when the → button in a K-line cell is clicked (works for table row and mobile card)
async function submitLine(btn) {
  // Works inside a <tr> (desktop) or a .pmc-card (mobile)
  const container = btn.closest('tr') || btn.closest('[data-pitcher]');
  const name  = container.dataset.pitcher;
  const team  = container.dataset.team;
  const input = container.querySelector('.k-line-input');
  // Spanish Android keyboards send a comma for the decimal separator;
  // parseFloat("4,5") returns 4, so "4.5" would save as 4.0. Normalize
  // commas to dots before parsing so both separators work on mobile.
  const raw   = (input.value || '').replace(',', '.').trim();
  const kLine = parseFloat(raw);

  if (isNaN(kLine) || kLine <= 0) { input.focus(); return; }
  // Echo the cleaned value back so the user sees what was parsed
  input.value = String(kLine);

  btn.disabled    = true;
  btn.textContent = '…';
  input.disabled  = true;

  try {
    const pick = await apiPost('/api/picks/lines', { pitcher_name: name, team, k_line: kLine });
    // Update desktop row
    const safeId = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
    const row = document.getElementById(`prop-row-${safeId}`);
    if (row) _applyPickToRow(row, pick);
    // Update mobile card line cell directly
    const mobileLineEl = document.getElementById(`pmc-line-${safeId}`);
    if (mobileLineEl) mobileLineEl.innerHTML = `<span class="pmc-line-val">${num(pick.k_line, 1)}</span>`;
    // Update pitcher store
    if (_pitcherStore[safeId]) {
      _pitcherStore[safeId].pick = pick;
      _pitcherStore[safeId].rec  = pick.recommendation || 'SKIP';
      _pitcherStore[safeId].edgeCell = _edgePill(pick.edge);
      _pitcherStore[safeId].ciCell   = _ciBar(pick.ci_low, pick.ci_high, pick.model_proj);
    }
    // Keep cube view in sync so switching to enhanced doesn't show stale state
    _lastPropsPicksMap[name.toLowerCase()] = pick;
    const cubeCard = document.querySelector(`#props-cubes-grid [data-pitcher="${name}"]`);
    if (cubeCard) {
      const idx = _lastPropsPitchers.findIndex(x => x.name === name);
      const p   = idx >= 0 ? _lastPropsPitchers[idx] : { name, team, pitcher_id: null };
      cubeCard.outerHTML = _buildCubeCard(p, pick);
    }
    if (pick.recommendation === 'OVER' || pick.recommendation === 'UNDER') {
      _showToast(`${name} → ${pick.recommendation} ${kLine} · guardado en el Tracker`, 'success');
    }
    // If nerd drawer is open for this pitcher, refresh it with updated data
    const drawerEl = document.getElementById('pitcher-nerd-drawer');
    if (drawerEl && drawerEl.classList.contains('open') && drawerEl.dataset.pitcher === name) {
      openPitcherNerd(safeId);
    }
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
    row.querySelectorAll('td')[9].innerHTML =
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
    const res   = result.result || '';
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    const pnl   = result.pnl_pesos != null ? ` · ${money(result.pnl_pesos, true)}` : '';
    _showToast(`${pitcherName} — ${actualKs} Ks → ${label}${pnl}`, res === 'W' ? 'success' : 'info');
    // Full reload so cards, bankroll totals, chart, and calibration all refresh
    // (cached _trackerKPicks would otherwise still show pending state).
    // Preserve current day index so the user stays on the day they just logged.
    const keepIdx = _trackerDayIdx;
    await loadTracker();
    if (_trackerDays && _trackerDays.length && keepIdx < _trackerDays.length) {
      _trackerDayIdx = keepIdx;
      _renderTrackerDay(_trackerDayIdx);
    }
  } catch (e) {
    console.error(e);
    btn.disabled    = false;
    btn.textContent = 'LOG';
    input.disabled  = false;
    input.style.borderColor = 'var(--red)';
    _showToast(`Error: ${e.message}`, 'error');
  }
}

// Inline editor for correcting a settled tracker result.
// Works in both the legacy <td>-based tracker table AND the current card layout
// (div-based). We look for the closest settled-result container.
function editTrackerResult(btn) {
  const host = btn.closest('td') || btn.parentElement;  // <td> (legacy) or card footer div
  const pitcherName  = btn.dataset.pitcher;
  const date         = btn.dataset.date;
  const currentKs    = btn.dataset.ks;
  if (!host) return;

  host.innerHTML = `
    <div style="display:flex;align-items:center;gap:5px">
      <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input"
             value="${currentKs}" placeholder="Ks"
             data-pitcher="${pitcherName}" data-date="${date}">
      <button class="tp-log-btn" onclick="saveTrackerEdit(this)">SAVE</button>
      <button class="tp-log-btn" style="background:var(--bg3);color:var(--text-muted)"
              onclick="loadTracker()">✕</button>
    </div>`;
  host.querySelector('input').focus();
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
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    _showToast(`${pitcherName} corregido → ${actualKs} Ks (${label})`, 'success');
    // Reload tracker — cleaner than DOM patching across two layouts
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
  cells[2].innerHTML = `<div class="pp-kline-set"><strong class="pp-kline-val">${num(pick.k_line, 1)}</strong><button class="pp-kline-edit-btn" onclick="editKLine(this)" title="Cambiar línea">✎</button></div>`;

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

  // col 7 – LINEUP badge
  cells[7].innerHTML = pick.lineup_confirmed
    ? '<span class="pp-lineup-badge">LINEUP</span>'
    : '<span style="color:var(--text-dim);font-size:11px">—</span>';

  // col 9 – RESULT  (col 8 is FORMA — leave it alone)
  if (rec !== 'SKIP') {
    cells[9].innerHTML = `
      <div class="tp-log-row">
        <button type="button" class="pp-cancel-btn" onclick="cancelPick(this,event)" title="Undo pick">&#8630;</button>
        <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input" placeholder="Ks">
        <button class="tp-log-btn" onclick="submitResult(this)">LOG</button>
      </div>`;
  } else {
    cells[9].innerHTML = `
      <div style="display:flex;gap:5px;align-items:center">
        <button type="button" class="pp-cancel-btn" onclick="cancelPick(this,event)" title="Undo pick">&#8630;</button>
        <span style="font-size:11px;color:var(--text-dim)">SKIP</span>
      </div>`;
  }

  // Mark as top pick row if warranted
  const isTop = (rec === 'OVER' || rec === 'UNDER') && (pick.confidence || 0) >= 4;
  row.classList.toggle('pp-top-pick-row', isTop);

  // Sync mobile card line cell
  const safeId = row.id.replace('prop-row-', '');
  const mobileLineEl = document.getElementById(`pmc-line-${safeId}`);
  if (mobileLineEl) {
    mobileLineEl.innerHTML = `<span class="pmc-line-val">${num(pick.k_line, 1)}</span>`;
  }
  // Update pitcher store for nerd drawer
  if (_pitcherStore[safeId]) {
    _pitcherStore[safeId].pick = pick;
    _pitcherStore[safeId].rec  = rec;
    _pitcherStore[safeId].edgeCell = _edgePill(pick.edge);
    _pitcherStore[safeId].ciCell   = _ciBar(pick.ci_low, pick.ci_high, pick.model_proj);
    _pitcherStore[safeId].confPct  = ((_pitcherStore[safeId].p.confidence_level || 1) * 20);
  }
}

// ── BETTING TRACKER ────────────────────────────────────────────────────────────

let trackerChart = null;
let _allPicks = [];
let _chartRange = 'all';

async function triggerRetrain() {
  const btn  = document.getElementById('retrain-btn');
  const text = document.getElementById('retrain-status-text');

  btn.disabled    = true;
  btn.textContent = '↻ Iniciando…';

  try {
    await apiPost('/api/model/retrain', {});
    text.textContent = 'Pipeline corriendo — puede tardar 10-30 min';
    text.style.color = '#f59e0b';
    btn.textContent  = '↻ Corriendo…';
    _showToast('Reentrenamiento iniciado. El modelo se actualizará automáticamente.', 'info');
    const poll = setInterval(async () => {
      try {
        const s = await apiFetch('/api/model/retrain/status');
        if (!s.running) {
          clearInterval(poll);
          btn.disabled    = false;
          if (s.error) {
            text.textContent = 'Error: ' + s.error.slice(0, 80);
            text.style.color = 'var(--red-light)';
            btn.textContent  = '↻ Reentrenar modelo';
            _showToast('Error en reentrenamiento: ' + s.error.slice(0, 60), 'error');
          } else {
            text.textContent = 'Modelo actualizado ' + (s.last || '');
            text.style.color = '#22c55e';
            btn.textContent  = '↻ Reentrenar modelo';
            _showToast('Modelo reentrenado exitosamente.', 'success');
          }
        }
      } catch (_) {}
    }, 15000);
  } catch (e) {
    btn.disabled    = false;
    btn.textContent = '↻ Reentrenar modelo';
    _showToast('Error: ' + e.message, 'error');
  }
}

// ── Tracker pagination state ────────────────────────────────────────────────
let _trackerKPicks  = [];   // all K-prop picks (filtered, no SKIP)
let _trackerOUPicks = [];   // all O/U picks
let _trackerDays    = [];   // sorted unique dates, newest first
let _trackerDayIdx  = 0;    // current page index

async function loadTracker() {
  const dayCards = document.getElementById('trk-day-cards');
  if (dayCards) dayCards.innerHTML = '<div class="loading-cell" style="padding:32px 0;text-align:center">Cargando…</div>';
  try {
    const [{ picks, summary }, ouData] = await Promise.all([
      apiFetch('/api/picks/history'),
      apiFetch('/api/matchup-picks/history').catch(() => ({ picks: [], summary: {} })),
    ]);
    renderPicksSummary(summary);
    _renderOUSummaryStrip(ouData.summary || {});

    // Store globally for pagination
    _trackerKPicks  = picks.filter(p => p.recommendation !== 'SKIP');
    _trackerOUPicks = ouData.picks || [];

    // Build sorted unique date list across both pick types
    const daySet = new Set([
      ..._trackerKPicks.map(p => p.date),
      ..._trackerOUPicks.map(p => p.date),
    ]);
    _trackerDays   = [...daySet].filter(Boolean).sort((a, b) => b.localeCompare(a));
    _trackerDayIdx = 0;

    // Show nav if more than one day
    const nav = document.getElementById('trk-day-nav');
    if (nav) nav.style.display = _trackerDays.length > 0 ? 'flex' : 'none';

    _renderTrackerDay(_trackerDayIdx);

    _allPicks   = picks;
    _chartRange = 'all';
    document.querySelectorAll('#chartRangeBtns .crb').forEach(b => {
      b.classList.toggle('crb--active', b.dataset.range === 'all');
    });
    renderPicksChart(picks);

    // Bundle A #12 — calibration panel (Brier + reliability bins)
    loadCalibration().catch(e => console.warn('calibration load:', e));
    // Auto-P&L — bankroll card (unit size + totals)
    loadBankroll().catch(e => console.warn('bankroll load:', e));
    loadOddsQuota().catch(e => console.warn('odds quota load:', e));
  } catch (e) {
    console.error(e);
    if (dayCards) dayCards.innerHTML = `<div class="loading-cell" style="padding:32px 0;text-align:center;color:var(--red)">Error: ${e.message}</div>`;
  }

  // Show next scheduled retrain
  try {
    const sched = await apiFetch('/api/model/retrain/schedule');
    const el = document.getElementById('retrain-schedule-text');
    if (el) {
      if (sched.next_run) {
        const dt  = new Date(sched.next_run);
        const fmt = dt.toLocaleDateString('es-AR', { weekday: 'long', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        el.textContent = `Próximo reentrenamiento automático: ${fmt} ART`;
      } else {
        el.textContent = 'Reentrenamiento automático: no programado';
      }
    }
  } catch (_) { /* non-critical */ }
}

function trackerNavDay(delta) {
  const next = _trackerDayIdx + delta;
  if (next < 0 || next >= _trackerDays.length) return;
  _trackerDayIdx = next;
  _renderTrackerDay(_trackerDayIdx);
}

function _renderTrackerDay(idx) {
  const container = document.getElementById('trk-day-cards');
  const labelEl   = document.getElementById('trk-day-label');
  const prevBtn   = document.getElementById('trk-prev');
  const nextBtn   = document.getElementById('trk-next');
  if (!container) return;

  if (!_trackerDays.length) {
    container.innerHTML = '<div class="loading-cell" style="padding:32px 0;text-align:center">Sin picks registrados aún.</div>';
    if (labelEl) labelEl.textContent = '—';
    return;
  }

  const day = _trackerDays[idx];

  // Count unsettled picks for THIS day and flag older days that still have pendings
  const kDayAll     = _trackerKPicks.filter(p => p.date === day);
  const pendingToday = kDayAll.filter(p => p.actual_ks == null).length;
  const olderPending = _trackerKPicks.filter(p => p.date < day && p.actual_ks == null).length;

  // Nav label — format date nicely + pending-count badge
  if (labelEl) {
    const d   = new Date(day + 'T12:00:00');
    const fmt = d.toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    let label = fmt.charAt(0).toUpperCase() + fmt.slice(1);
    if (pendingToday > 0) {
      label += ` · <span style="color:var(--red-light);font-weight:700">${pendingToday} pendiente${pendingToday > 1 ? 's' : ''}</span>`;
    }
    if (olderPending > 0 && idx < _trackerDays.length - 1) {
      label += ` <span style="color:var(--text-dim);font-size:10px;letter-spacing:1px;margin-left:6px">← ${olderPending} más</span>`;
    }
    labelEl.innerHTML = label;
  }
  if (prevBtn) prevBtn.disabled = idx >= _trackerDays.length - 1;
  if (nextBtn) nextBtn.disabled = idx <= 0;

  const kDay  = _trackerKPicks.filter(p => p.date === day);
  const ouDay = _trackerOUPicks.filter(p => p.date === day);
  const allDay = [...kDay, ...ouDay];

  let html = '';

  if (!allDay.length) {
    html = '<div class="loading-cell" style="padding:32px 0;text-align:center">Sin picks para este día.</div>';
    container.innerHTML = html;
    return;
  }

  // Day header: formatted date + total P/L
  const d      = new Date(day + 'T12:00:00');
  const dayNum = d.toLocaleDateString('es-AR', { day: 'numeric', month: 'short' }).toUpperCase();
  const wkday  = d.toLocaleDateString('es-AR', { weekday: 'long' });
  const wkCap  = wkday.charAt(0).toUpperCase() + wkday.slice(1);

  const settledPL  = allDay.filter(p => p.pnl_pesos != null).reduce((s, p) => s + p.pnl_pesos, 0);
  const hasPL      = allDay.some(p => p.pnl_pesos != null);
  const plCls      = settledPL > 0 ? 'ok' : settledPL < 0 ? 'loss' : '';
  const plStr      = hasPL ? (settledPL >= 0 ? '+' : '') + money(settledPL) : '—';
  const pendingCount = allDay.filter(p => p.actual_ks == null && p.result == null).length;

  html += `<div class="day-group">`;
  html += `<div class="day-head">
    <div class="day-date">${dayNum} <span>${wkCap}</span></div>
    <div class="day-pl">
      <span>${allDay.length} apuesta${allDay.length !== 1 ? 's' : ''}${pendingCount > 0 ? ` · <span style="color:var(--red-light)">${pendingCount} pend.</span>` : ''}</span>
      <span class="total ${plCls}">${plStr}</span>
    </div>
  </div>`;

  // K-prop rows
  html += kDay.map(p => _trkKPropCard(p)).join('');
  // O/U rows
  html += ouDay.map(p => _trkOUCard(p)).join('');

  html += `</div>`;

  container.innerHTML = html;
}

function _trkKPropCard(p) {
  const name    = p.pitcher_name || '—';
  const safeId  = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
  const rec     = p.recommendation || 'SKIP';
  const recCls  = rec === 'OVER' ? 'over' : rec === 'UNDER' ? 'under' : 'skip';
  const oddsStr = p.odds_decimal != null ? num(p.odds_decimal, 2) : '—';
  const kLine   = p.k_line != null ? num(p.k_line, 1) : '—';
  const matchup = `${p.team || '?'} vs ${p.opponent || '?'}`;
  const casino  = p.casino || '—';

  const settled = p.actual_ks != null && p.result;
  let resultHtml, payoutHtml;

  if (settled) {
    const rc   = p.result === 'W' ? 'win' : p.result === 'L' ? 'loss' : 'push';
    const icon = p.result === 'W' ? '✓' : p.result === 'L' ? '✕' : '·';
    const pnlCls = p.pnl_pesos != null ? (p.pnl_pesos > 0 ? 'ok' : p.pnl_pesos < 0 ? 'loss' : '') : 'dim';
    const pnlStr = p.pnl_pesos != null ? (p.pnl_pesos >= 0 ? '+' : '') + money(p.pnl_pesos) : '—';
    resultHtml = `
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span class="result-pill ${rc}">${icon} ${p.actual_ks}K</span>
        <button class="tracker-edit-btn" title="Corregir"
                data-pitcher="${name}" data-date="${p.date}" data-ks="${p.actual_ks}"
                onclick="editTrackerResult(this)">✎</button>
      </div>`;
    payoutHtml = `<span class="bet-payout ${pnlCls}">${pnlStr}</span>`;
  } else {
    resultHtml = `
      <div class="tp-log-row">
        <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input"
               placeholder="Ks" data-pitcher="${name}" data-date="${p.date}">
        <button class="tp-log-btn" onclick="logFromTracker(this)">LOG</button>
      </div>`;
    payoutHtml = `<span class="bet-payout dim">—</span>`;
  }

  return `
    <div class="bet-row" id="trkc-${safeId}">
      <div class="bet-pitcher">
        <span class="bet-name">${name}</span>
        <span class="bet-line"><span class="dir ${recCls}">${rec}</span> ${kLine} K · ${matchup}</span>
      </div>
      <div class="bet-cells">
        <div class="bet-cell"><span class="lbl">Cuota</span><span class="val">${oddsStr}</span></div>
        <div class="bet-cell"><span class="lbl">Proy.</span><span class="val">${p.model_proj != null ? num(p.model_proj, 1) : '—'}</span></div>
        <div class="bet-cell"><span class="lbl">Casino</span><span class="val">${casino}</span></div>
      </div>
      <div class="bet-result" id="trkc-foot-${safeId}">${resultHtml}</div>
      ${payoutHtml}
    </div>`;
}

function _trkOUCard(p) {
  const dir     = p.direction || '—';
  const dirCls  = dir === 'OVER' ? 'over' : dir === 'UNDER' ? 'under' : '';
  const edgePct = p.edge_pct != null ? p.edge_pct * 100 : null;
  const edgeSign = edgePct != null && edgePct >= 0 ? '+' : '';
  const settled  = !!p.result;
  const matchup  = `${p.away_team || '?'} @ ${p.home_team || '?'}`;

  let wxNote = '';
  if (p.weather_factor != null && p.weather_roof !== 'dome') {
    const wPct  = (p.weather_factor - 1) * 100;
    const wSign = wPct >= 0 ? '+' : '';
    wxNote = ` · runs ${wSign}${wPct.toFixed(1)}%`;
  }

  let resultHtml, payoutHtml;
  if (settled) {
    const rc   = p.result === 'W' ? 'win' : p.result === 'L' ? 'loss' : 'push';
    const icon = p.result === 'W' ? '✓' : p.result === 'L' ? '✕' : '·';
    const pnlCls = p.pnl_pesos != null ? (p.pnl_pesos > 0 ? 'ok' : p.pnl_pesos < 0 ? 'loss' : '') : 'dim';
    const pnlStr = p.pnl_pesos != null ? (p.pnl_pesos >= 0 ? '+' : '') + money(p.pnl_pesos) : '—';
    resultHtml = `
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span class="result-pill ${rc}">${icon} ${p.actual_total}</span>
        <button class="tp-edit-result-btn" onclick="editMatchupResult(this, ${p.game_id}, ${p.actual_total})" title="Editar">✏</button>
      </div>`;
    payoutHtml = `<span class="bet-payout ${pnlCls}">${pnlStr}</span>`;
  } else {
    resultHtml = `
      <div class="tp-log-row">
        <input type="text" inputmode="decimal" class="tp-ks-input"
               placeholder="Total" data-gameid="${p.game_id}" style="width:52px">
        <button class="tp-log-btn" onclick="submitMatchupResult(this)">LOG</button>
      </div>`;
    payoutHtml = `<span class="bet-payout dim">—</span>`;
  }

  return `
    <div class="bet-row">
      <div class="bet-pitcher">
        <span class="bet-name">${matchup}</span>
        <span class="bet-line"><span class="dir ${dirCls}">${dir}</span> ${num(p.ou_line, 1)} runs · ${p.away_pitcher || '?'} / ${p.home_pitcher || '?'}${wxNote}</span>
      </div>
      <div class="bet-cells">
        <div class="bet-cell"><span class="lbl">Línea</span><span class="val">${num(p.ou_line, 1)}</span></div>
        <div class="bet-cell"><span class="lbl">Sim</span><span class="val">${num(p.avg_total, 1)}</span></div>
        <div class="bet-cell"><span class="lbl">Edge</span><span class="val">${edgePct != null ? `${edgeSign}${edgePct.toFixed(1)}%` : '—'}</span></div>
      </div>
      <div class="bet-result">${resultHtml}</div>
      ${payoutHtml}
    </div>`;
}

function _renderOUSummaryStrip(summary) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  set('ou-record',  summary.record   || '0-0-0');
  set('ou-hitrate', summary.hit_rate != null ? (summary.hit_rate * 100).toFixed(1) + '%' : '—');
  set('ou-pending', (summary.total != null && summary.settled != null)
      ? (summary.total - summary.settled).toString() : '—');
  set('ou-avgedge', summary.avg_edge != null
      ? (summary.avg_edge >= 0 ? '+' : '') + summary.avg_edge.toFixed(1) + '%' : '—');
}

// ── Bundle A #12 — Brier + reliability diagram ─────────────────────────────
async function loadCalibration() {
  const section = document.getElementById('calibration-section');
  if (!section) return;
  try {
    const data = await apiFetch('/api/picks/calibration');
    if (!data || data.n === 0) {
      section.style.display = 'none';
      return;
    }
    section.style.display = 'block';

    // Header n-count
    const nEl = document.getElementById('calib-n');
    if (nEl) nEl.textContent = `(${data.n} picks)`;

    // Brier big number + color class
    const brierEl = document.getElementById('calib-brier-val');
    if (brierEl) {
      brierEl.textContent = data.brier.toFixed(3);
      brierEl.classList.remove('b-good', 'b-neu', 'b-bad');
      brierEl.classList.add(
        data.brier < 0.18 ? 'b-good' :
        data.brier < 0.22 ? 'b-neu'  :
                            'b-bad'
      );
    }

    const interpEl = document.getElementById('calib-interp');
    if (interpEl) interpEl.textContent = data.interpretation || '';

    // Platt + Isotonic + active method (Tier 1 #2)
    renderCalibrationCard(data);
    renderActiveMethodChip(data);

    // Reliability bars: dual-bar per bucket (predicted vs actual).
    // Tier 1 #4 — hide bins with n<10 (statistically unreliable; the 75-80%
    // bin showing "20% real" with n=5 was misleading the reading).
    const MIN_BIN_N = 10;
    const barsEl = document.getElementById('calib-bars');
    if (barsEl) {
      const allBins  = data.bins || [];
      const bigBins  = allBins.filter(b => b.n >= MIN_BIN_N);
      const hiddenN  = allBins.length - bigBins.length;
      if (!bigBins.length) {
        barsEl.innerHTML = `<div class="calib-empty">Sin buckets con n ≥ ${MIN_BIN_N}. Esperá a que se acumulen más picks settled.</div>`;
      } else {
        const rowsHtml = bigBins.map(b => {
          const label = `${Math.round(b.range_low*100)}-${Math.round(b.range_high*100)}%`;
          const predPct   = (b.avg_predicted * 100).toFixed(1);
          const actualPct = (b.avg_actual    * 100).toFixed(1);
          const gapPct    = (b.gap            * 100);
          const gapTxt    = (gapPct >= 0 ? '+' : '') + gapPct.toFixed(1) + 'pp';
          const gapCls    = Math.abs(gapPct) < 5 ? 'gap-ok' : gapPct < 0 ? 'gap-under' : 'gap-over';
          return `
            <div class="calib-bar-row">
              <div class="calib-bar-label">${label}<span class="calib-bar-n">n=${b.n}</span></div>
              <div class="calib-bar-pair">
                <div class="calib-bar calib-bar-pred" style="width:${predPct}%"   title="Predicho ${predPct}%">${predPct}%</div>
                <div class="calib-bar calib-bar-actual ${gapCls}" style="width:${actualPct}%" title="Real ${actualPct}%">${actualPct}%</div>
              </div>
              <div class="calib-bar-gap ${gapCls}">${gapTxt}</div>
            </div>`;
        }).join('');
        const footnote = hiddenN > 0
          ? `<div class="calib-hidden-note">${hiddenN} bin${hiddenN > 1 ? 's' : ''} oculto${hiddenN > 1 ? 's' : ''} por n &lt; ${MIN_BIN_N} (ruido estadístico).</div>`
          : '';
        barsEl.innerHTML = rowsHtml + footnote;
      }
    }
  } catch (e) {
    console.warn('loadCalibration failed:', e);
    section.style.display = 'none';
  }
}

// Tier 1 #2 — Renders the calibration card showing whichever method is active
// (Platt or Isotonic). Header label, params, and Brier metrics all swap based
// on data.active_method. The "off" case (no calibration applied) shows a
// neutral state. Reuses the existing `.calib-platt-*` CSS classes.
function renderCalibrationCard(data) {
  const labelEl  = document.getElementById('calib-platt-label');
  const statusEl = document.getElementById('calib-platt-status');
  const bodyEl   = document.getElementById('calib-platt-body');
  const btnEl    = document.getElementById('calib-platt-btn');
  if (!statusEl || !bodyEl || !btnEl) return;

  const active   = data.active_method || (data.platt ? 'platt' : 'off');
  const platt    = data.platt;
  const iso      = data.isotonic;

  // Pick the params bag matching the active method
  const params = active === 'isotonic' ? iso
               : active === 'platt'    ? platt
               : null;

  // Header label swaps per method
  if (labelEl) {
    labelEl.textContent = active === 'isotonic' ? 'Isotonic Regression'
                        : active === 'platt'    ? 'Platt Scaling'
                        : 'Calibración';
  }

  if (!params) {
    statusEl.textContent = active === 'off' ? 'Sin calibrar (forzado off)' : 'Sin calibrar';
    statusEl.className = 'calib-platt-status s-off';
    bodyEl.innerHTML = `
      <p class="calib-platt-desc">
        Auto-selecciona Platt (sigmoide) o Isotonic (monotónico flexible) según
        menor Brier. Ninguno está fitteado todavía — necesitás ≥30 picks settled.
      </p>
    `;
    btnEl.textContent = 'Calibrar ahora';
    return;
  }

  const fittedDate = params.fitted_at ? new Date(params.fitted_at) : null;
  const daysAgo = fittedDate ? Math.max(0, Math.floor((Date.now() - fittedDate.getTime()) / 86400000)) : null;
  const whenTxt = daysAgo === null ? '—' :
                  daysAgo === 0    ? 'hoy' :
                  daysAgo === 1    ? 'hace 1 día' :
                                     `hace ${daysAgo} días`;

  statusEl.textContent = `Activo · ${whenTxt}`;
  statusEl.className = 'calib-platt-status s-on';

  const brierBefore = params.brier_before != null ? params.brier_before.toFixed(4) : '—';
  const brierAfter  = params.brier_after  != null ? params.brier_after.toFixed(4)  : '—';
  const delta = (params.brier_before != null && params.brier_after != null)
    ? (params.brier_before - params.brier_after)
    : null;
  const deltaTxt = delta != null
    ? ((delta > 0 ? 'mejora ' : 'empeora ') + (Math.abs(delta) * 1000).toFixed(1) + '/1000')
    : '';

  // Render params depending on method.
  let paramsHtml;
  if (active === 'isotonic') {
    const bp = params.n_breakpoints ?? '?';
    paramsHtml = `
      <div class="calib-platt-params">
        <div class="calib-platt-param"><span class="calib-platt-k">breakpoints</span><span class="calib-platt-v">${bp}</span></div>
        <div class="calib-platt-param"><span class="calib-platt-k">n</span><span class="calib-platt-v">${params.n_samples}</span></div>
      </div>`;
  } else {
    paramsHtml = `
      <div class="calib-platt-params">
        <div class="calib-platt-param"><span class="calib-platt-k">A</span><span class="calib-platt-v">${(params.A >= 0 ? '+' : '') + params.A.toFixed(4)}</span></div>
        <div class="calib-platt-param"><span class="calib-platt-k">B</span><span class="calib-platt-v">${(params.B >= 0 ? '+' : '') + params.B.toFixed(4)}</span></div>
        <div class="calib-platt-param"><span class="calib-platt-k">n</span><span class="calib-platt-v">${params.n_samples}</span></div>
      </div>`;
  }

  bodyEl.innerHTML = `
    ${paramsHtml}
    <div class="calib-platt-brier">
      <span>Brier: <b>${brierBefore}</b> → <b>${brierAfter}</b></span>
      ${delta != null ? `<span class="calib-platt-delta ${delta > 0 ? 'd-good' : 'd-bad'}">${deltaTxt}</span>` : ''}
    </div>
  `;
  btnEl.textContent = 'Recalibrar ahora';
}

// Tier 1 #2 — UI chip showing which calibration is active (Platt vs Isotonic).
// Drops in next to the Platt card when both are fitted; the auto-selector
// picks the one with lower brier_after.
function renderActiveMethodChip(data) {
  const host = document.getElementById('calib-platt-status');
  if (!host) return;
  // Remove any old method indicator we appended last cycle
  const old = document.getElementById('calib-method-chip');
  if (old) old.remove();

  const active   = data.active_method;
  const setting  = data.method_setting;
  const platt    = data.platt;
  const iso      = data.isotonic;
  if (!active) return;

  // Decide what chip to show
  let label, cls, title;
  if (active === 'off') {
    label = 'Sin calibrar';
    cls   = 'cm-off';
    title = 'No hay calibración activa.';
  } else if (active === 'isotonic') {
    label = 'Isotonic activa';
    cls   = 'cm-iso';
    const bp = iso ? iso.n_breakpoints : '?';
    const ba = iso ? iso.brier_after : '—';
    const bp2 = platt ? platt.brier_after : '—';
    title = `Isotonic ganó por Brier (${ba} vs Platt ${bp2}). ${bp} breakpoints.`;
  } else {
    label = 'Platt activa';
    cls   = 'cm-platt';
    const ba = platt ? platt.brier_after : '—';
    const bb = iso ? iso.brier_after : '—';
    title = `Platt ganó por Brier (${ba} vs Isotonic ${bb}).`;
  }
  const settingTxt = setting === 'auto'
    ? '(auto)'
    : setting === 'off' ? '(forzado off)' : `(forzado ${setting})`;

  const chip = document.createElement('span');
  chip.id        = 'calib-method-chip';
  chip.className = `calib-method-chip ${cls}`;
  chip.title     = title;
  chip.innerHTML = `${label} <span class="cm-setting">${settingTxt}</span>`;
  host.parentElement.appendChild(chip);
}

async function recalibratePlatt() {
  const btn = document.getElementById('calib-platt-btn');
  const msg = document.getElementById('calib-platt-msg');
  if (btn) { btn.disabled = true; btn.textContent = 'Ajustando…'; }
  if (msg) { msg.textContent = ''; msg.className = 'calib-platt-msg'; }
  try {
    const r = await fetch(API + '/api/model/calibrate', { method: 'POST' });
    if (!r.ok) {
      let detail = r.statusText;
      try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
      throw new Error(detail);
    }
    const res = await r.json();
    if (msg) {
      msg.textContent = `Ajustado sobre ${res.platt.n_samples} picks · recomputados ${res.recomputed_rows} rows.`;
      msg.className = 'calib-platt-msg m-ok';
    }
    // Reload calibration so UI + new p_over values refresh
    await loadCalibration();
    // Also refresh pick cards since p_over changed
    if (typeof loadPicks === 'function') loadPicks();
  } catch (e) {
    if (msg) {
      msg.textContent = 'Error: ' + (e && e.message ? e.message : String(e));
      msg.className = 'calib-platt-msg m-err';
    }
  } finally {
    if (btn) { btn.disabled = false; }
  }
}

// Tanda 1 bonus — auto-generate the whole slate from market data.
// One click pulls consensus K-line + best-price odds for every probable
// starter today and creates picks. Skips pitchers that already have a
// pick (preserves user edits).
async function autoGenerateTodayPicks() {
  const btn   = document.getElementById('auto-gen-btn');
  const out   = document.getElementById('auto-gen-result');
  const force = window.event && window.event.shiftKey; // Shift-click = force regenerate
  if (!btn) return;
  const prev = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="auto-gen-icon">⏳</span><span class="auto-gen-label">Generando…</span>';
  if (out) {
    out.style.display = 'block';
    out.className     = 'auto-gen-result agr-loading';
    out.textContent   = 'Buscando líneas en el mercado…';
  }
  try {
    const url = `${API}/api/picks/lines/auto-generate${force ? '?force=true' : ''}`;
    const r   = await fetch(url, { method: 'POST' });
    if (!r.ok) {
      let detail = r.statusText;
      try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
      throw new Error(detail);
    }
    const data = await r.json();
    const ng   = data.n_generated || 0;
    const ns   = data.n_skipped   || 0;

    let summary;
    if (ng === 0 && ns === 0) {
      summary = 'No hay starters confirmados para hoy.';
    } else {
      const overs  = (data.generated || []).filter(g => g.rec === 'OVER').length;
      const unders = (data.generated || []).filter(g => g.rec === 'UNDER').length;
      const skips  = (data.generated || []).filter(g => g.rec === 'SKIP').length;
      summary = `<b>✓ ${ng} picks generados</b> (${overs} OVER · ${unders} UNDER · ${skips} SKIP)`;
      if (ns > 0) summary += ` · ${ns} salteados`;
    }

    // Optional drill-down for skipped pitchers (collapsed by default)
    let detail = '';
    if (ns > 0) {
      const items = (data.skipped || []).slice(0, 12).map(s =>
        `<li><span class="agr-name">${s.pitcher_name}</span><span class="agr-reason">${s.reason}</span></li>`
      ).join('');
      detail = `
        <details class="agr-details">
          <summary>Ver ${ns} salteados</summary>
          <ul class="agr-skipped">${items}</ul>
        </details>`;
    }

    if (out) {
      out.className = 'auto-gen-result ' + (ng > 0 ? 'agr-success' : 'agr-warn');
      out.innerHTML = `<div class="agr-summary">${summary}</div>${detail}`;
    }
    _showToast(`${ng} picks generados`, ng > 0 ? 'success' : 'info');

    // Refresh the pitcher props grid so new picks show
    if (typeof loadPicks === 'function') loadPicks();
    // Also refresh the quota chip since we burned some calls
    if (typeof loadOddsQuota === 'function') loadOddsQuota();
  } catch (e) {
    console.error(e);
    if (out) {
      out.className = 'auto-gen-result agr-error';
      out.textContent = `Error: ${e.message || 'No se pudo generar el slate'}`;
    }
    _showToast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = prev;
  }
}

// Tanda 1 — Odds API quota indicator. Pulled alongside bankroll on the
// Tracker page so the user always knows how many of the 500/mo free-tier
// requests are left without leaving the app.
async function loadOddsQuota() {
  const host = document.getElementById('odds-quota-chip');
  if (!host) return;
  try {
    const data = await apiFetch('/api/odds/quota');
    if (!data || !data.configured) {
      host.style.display = 'none';
      return;
    }
    host.style.display = '';
    const used = data.used != null ? data.used : '—';
    const remaining = data.remaining != null ? data.remaining : '—';
    let cls = 'oqc-ok';
    if (typeof remaining === 'number' && remaining < 50) cls = 'oqc-warn';
    if (typeof remaining === 'number' && remaining < 10) cls = 'oqc-crit';
    host.className = `odds-quota-chip ${cls}`;
    host.innerHTML = `
      <span class="oqc-lbl">Odds API</span>
      <span class="oqc-val">${remaining} restantes</span>
      <span class="oqc-sub">(${used} usados)</span>
    `;
  } catch (_) {
    host.style.display = 'none';
  }
}

// ── Auto-P&L — bankroll card ────────────────────────────────────────────────
async function loadBankroll() {
  try {
    const data = await apiFetch('/api/settings/bankroll');
    const bInp = document.getElementById('bankroll-total-input');
    const uInp = document.getElementById('bankroll-unit-input');
    const cInp = document.getElementById('bankroll-cap-input');
    const evInp = document.getElementById('bankroll-minev-input');
    const msInp = document.getElementById('bankroll-minstake-input');
    if (bInp) bInp.value = Math.round(data.bankroll_pesos  || 100000);
    if (uInp) uInp.value = Math.round(data.unit_size_pesos || 1000);
    if (cInp) cInp.value = ((data.max_stake_fraction || 0.03) * 100).toFixed(1);
    if (evInp) evInp.value = ((data.min_ev_pct ?? 0.05) * 100).toFixed(1);
    if (msInp) msInp.value = Math.round(data.min_stake_pesos ?? 2000);
    const ubInp = document.getElementById('bankroll-userbooks-input');
    if (ubInp) ubInp.value = data.user_books || '';

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('bankroll-staked', money(data.staked_pesos != null ? (data.staked_pesos - (data.staked_open_pesos || 0)) : null));
    set('bankroll-open',   money(data.staked_open_pesos));
    const roiEl = document.getElementById('bankroll-roi');
    if (roiEl) {
      if (data.pnl_roi_pct != null) {
        const sign = data.pnl_roi_pct > 0 ? '+' : '';
        roiEl.textContent = `${sign}${data.pnl_roi_pct.toFixed(1)}%`;
        roiEl.classList.remove('green', 'red', 'neu');
        roiEl.classList.add(data.pnl_roi_pct > 0 ? 'green' : data.pnl_roi_pct < 0 ? 'red' : 'neu');
      } else {
        roiEl.textContent = '—';
      }
    }
  } catch (e) {
    console.warn('loadBankroll failed:', e);
  }
}

async function saveBankrollUnit() {
  const bInp  = document.getElementById('bankroll-total-input');
  const uInp  = document.getElementById('bankroll-unit-input');
  const cInp  = document.getElementById('bankroll-cap-input');
  const evInp = document.getElementById('bankroll-minev-input');
  const msInp = document.getElementById('bankroll-minstake-input');
  const ubInp = document.getElementById('bankroll-userbooks-input');
  const msg   = document.getElementById('bankroll-msg');

  const bVal      = bInp  ? parseFloat(bInp.value)  : NaN;
  const uVal      = uInp  ? parseFloat(uInp.value)  : NaN;
  const cValPct   = cInp  ? parseFloat(cInp.value)  : NaN;  // user-facing %, converted below
  const evValPct  = evInp ? parseFloat(evInp.value) : NaN;  // user-facing %, converted below
  const minStake  = msInp ? parseFloat(msInp.value) : NaN;
  const userBooks = ubInp ? (ubInp.value || '').trim() : '';

  if (!(bVal > 0) || !(uVal > 0) || !(cValPct > 0) || !(evValPct >= 0) || !(minStake >= 0)) {
    if (msg) { msg.textContent = 'Revisá los valores (banca/unidad/cap > 0, EV-mín y stake-mín ≥ 0).'; msg.className = 'bankroll-msg m-err'; }
    return;
  }
  try {
    const r = await fetch(API + '/api/settings/bankroll', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bankroll_pesos:     bVal,
        unit_size_pesos:    uVal,
        max_stake_fraction: cValPct / 100,
        min_ev_pct:         evValPct / 100,
        min_stake_pesos:    minStake,
        user_books:         userBooks,
      }),
    });
    if (!r.ok) {
      let detail = r.statusText;
      try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch(_) {}
      throw new Error(detail);
    }
    const res = await r.json();
    if (msg) {
      const dem = res.demoted ? ` · ${res.demoted} degradados a SKIP` : '';
      msg.textContent = `Guardado · banca ${money(res.bankroll_pesos)} · ${res.stake_updated} stakes, ${res.pnl_updated} P&L recalculados${dem}.`;
      msg.className = 'bankroll-msg m-ok';
    }
    // Refresh summary + pick cards so new stake/pnl values + demotions appear
    loadTracker();
    if (typeof loadPicks === 'function') loadPicks();
  } catch (e) {
    if (msg) { msg.textContent = 'Error: ' + (e.message || String(e)); msg.className = 'bankroll-msg m-err'; }
  }
}

function renderPicksSummary(s) {
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  const roi  = s.roi_pct  != null ? (s.roi_pct  > 0 ? '+' : '') + s.roi_pct.toFixed(1)  + '%' : '—';
  const edge = s.avg_edge != null ? (s.avg_edge > 0 ? '+' : '') + s.avg_edge.toFixed(2) + 'K' : '—';
  const hr   = s.hit_rate != null ? (s.hit_rate * 100).toFixed(1) + '%' : '—';

  // Summary bar
  set('picks-record',  s.record   || '0-0-0');
  set('picks-hitrate', hr);
  set('picks-roi',     roi);
  set('picks-avgedge', edge);

  // Auto-P&L tile
  const pnlEl = document.getElementById('picks-pnl-pesos');
  if (pnlEl) {
    if (s.pnl_pesos != null) {
      pnlEl.textContent = money(s.pnl_pesos, true);
      pnlEl.classList.remove('red', 'green', 'neu');
      pnlEl.classList.add(s.pnl_pesos > 0 ? 'green' : s.pnl_pesos < 0 ? 'red' : 'neu');
    } else {
      pnlEl.textContent = '—';
    }
  }

  // Calibration card
  set('stat-roi',    roi);
  set('stat-wins',   s.record ? s.record.split('-')[0] : '0');
  set('stat-losses', s.record ? s.record.split('-')[1] : '0');
  set('stat-clv',    edge);

  // Quality badge based on ROI
  const qualityEl = document.getElementById('stat-quality');
  if (qualityEl && s.roi_pct != null) {
    const r = s.roi_pct;
    let cls, label;
    if (r >= 10)      { cls = 'excellent'; label = 'Excelente'; }
    else if (r >= 3)  { cls = 'good';      label = 'Bueno';     }
    else if (r >= 0)  { cls = 'neutral';   label = 'Neutral';   }
    else              { cls = 'poor';      label = 'Bajo';      }
    qualityEl.className = `brier-badge ${cls}`;
    qualityEl.textContent = label;
  }
}

// Legacy stubs — rendering now handled by _renderTrackerDay
function renderPicksTable()      { /* replaced by _renderTrackerDay */ }
function renderOUTrackerTable()  { /* replaced by _renderTrackerDay */ }

function setChartRange(range) {
  _chartRange = range;
  document.querySelectorAll('#chartRangeBtns .crb').forEach(b => {
    b.classList.toggle('crb--active', b.dataset.range === range);
  });
  renderPicksChart(_allPicks);
}

function renderPicksChart(picks) {
  const ctx = document.getElementById('trackerChart');
  if (!ctx) return;

  // Date cutoff for selected range
  let cutoff = null;
  if (_chartRange !== 'all') {
    const days = { '7d': 7, '1m': 30, '3m': 90, '6m': 180, '1y': 365 }[_chartRange];
    cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
  }

  // Settled, non-SKIP picks in ascending date order, filtered by range
  const settled = [...picks]
    .filter(p => p.result && p.result !== 'SKIP' && p.result !== 'P')
    .reverse() // history API returns DESC
    .filter(p => {
      if (!cutoff) return true;
      const d = new Date(p.date);
      return d >= cutoff;
    });

  let cumProfit = 0;
  const labels = [], unitsData = [];

  settled.forEach(p => {
    const pnl = p.result === 'W' ? (100 / 110) : -1;
    cumProfit += pnl;
    labels.push(p.date);
    unitsData.push(parseFloat(cumProfit.toFixed(2)));
  });

  if (trackerChart) trackerChart.destroy();

  if (!labels.length) {
    trackerChart = null;
    return;
  }

  const grad = ctx.getContext('2d').createLinearGradient(0, 0, 0, ctx.height);
  grad.addColorStop(0,   'rgba(74,222,128,0.18)');
  grad.addColorStop(0.6, 'rgba(74,222,128,0.04)');
  grad.addColorStop(1,   'rgba(74,222,128,0)');

  trackerChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Unidades acumuladas',
          data: unitsData,
          borderColor: '#4ade80',
          backgroundColor: grad,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#4ade80',
          pointHoverBorderColor: '#fff',
          pointHoverBorderWidth: 2,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1a1e',
          borderColor: '#2a2a30',
          borderWidth: 1,
          titleColor: '#8a8a92',
          bodyColor: '#f5f5f7',
          bodyFont: { family: "'Bebas Neue'", size: 18 },
          titleFont: { family: "'DM Sans'", size: 11 },
          padding: 12,
          displayColors: false,
          callbacks: {
            title: items => items[0].label,
            label: item => {
              const v = item.parsed.y;
              return (v >= 0 ? '+' : '') + v.toFixed(2) + 'u';
            },
          },
        },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(255,255,255,0.03)', drawBorder: false },
          ticks: { color: '#4a4a50', font: { family: "'DM Sans'", size: 10 }, maxTicksLimit: 8 },
          border: { display: false },
        },
        y: {
          grid:  { color: 'rgba(255,255,255,0.03)', drawBorder: false },
          ticks: { color: '#4a4a50', font: { family: "'DM Sans'", size: 10 }, callback: v => (v >= 0 ? '+' : '') + v + 'u' },
          border: { display: false },
        },
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
  btn.textContent = 'Guardando…';

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
    alert('Error al guardar apuesta: ' + err.message);
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

// ════════════════════════════════════════════════════════════════════════════
// ENHANCED VIEW · CUBE-BASED RENDERING
// ════════════════════════════════════════════════════════════════════════════

// Cached data so the toggle can re-render without re-fetching.
let _lastPropsPitchers = [];
let _lastPropsPicksMap = {};
let _lastTodaysPicks   = [];

// ── localStorage-backed view mode (classic | enhanced) ─────────────────────
function _getViewMode(key) {
  try { return localStorage.getItem(key) || 'classic'; } catch (_) { return 'classic'; }
}
function _setViewMode(key, mode) {
  try { localStorage.setItem(key, mode); } catch (_) {}
}

// ── Shared cube-card fragment helpers ──────────────────────────────────────
// Both _buildCubeCard (editable props view) and _buildTpCubeCard (read-only
// today's-picks view) share avatar/matchup/projection/stake/pnl subtrees.
// Keeping them here makes the two builders behave identically when new
// columns (park badge, stake pesos, pnl) are added to picks.

function _cubeAvatarHtml(name, pitcherId) {
  const color    = _avatarColor(name);
  const initials = _initials(name);
  const photoUrl = _headshotUrl(pitcherId);
  return photoUrl
    ? `<img src="${photoUrl}" class="cube-avatar-img" alt="${name}"
           onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
       <div class="cube-avatar-fallback" style="background:${color};display:none">${initials}</div>`
    : `<div class="cube-avatar-fallback" style="background:${color}">${initials}</div>`;
}

function _cubeMatchupHtml(team, opponent) {
  const teamLogo = _teamLogoUrl(team);
  const oppLogo  = _teamLogoUrl(opponent);
  const teamImg  = teamLogo ? `<img src="${teamLogo}" alt="${team}" onerror="this.style.display='none'">` : `<span>${team||''}</span>`;
  const oppImg   = oppLogo  ? `<img src="${oppLogo}"  alt="${opponent}" onerror="this.style.display='none'">` : `<span>${opponent||''}</span>`;
  return `<div class="cube-matchup">${teamImg}<span class="cube-vs">vs</span>${oppImg}</div>`;
}

// Right-side pill: calibrated P(OVER) preferred, raw edge fallback.
function _cubeRightPill(pOver, edge) {
  if (pOver != null) {
    const favored = pOver >= 0.5 ? 'OVER' : 'UNDER';
    const pct     = Math.round((favored === 'OVER' ? pOver : 1 - pOver) * 100);
    const cls     = pct >= 60 ? 'pos' : pct <= 45 ? 'neg' : 'neu';
    const edgeLbl = edge != null ? (edge >= 0 ? '+' : '') + num(edge, 1) : '—';
    return `<span class="cube-edge ${cls}" title="Edge ${edgeLbl} K">${pct}% ${favored}</span>`;
  }
  if (edge != null) {
    return `<span class="cube-edge ${edge >= 0 ? 'pos' : 'neg'}">${edge >= 0 ? '+' : ''}${num(edge, 1)}</span>`;
  }
  return '';
}

// Bundle A #7 — park K-factor badge. Only emitted by the backend for fresh
// projections, so historical DB picks quietly skip it.
function _cubeParkBadge(parkDelta, parkFactor) {
  if (parkDelta == null || parkFactor == null || Math.abs(parkDelta) < 0.05) return '';
  const sign = parkDelta > 0 ? '+' : '';
  const cls  = parkDelta > 0 ? 'park-pos' : 'park-neg';
  return `<span class="cube-park-badge ${cls}" title="Factor K del parque: ${parkFactor} (100 = neutro)">PARK ${sign}${parkDelta.toFixed(1)}K</span>`;
}

function _cubeProjRow(projVal, pOver, edge, parkDelta, parkFactor) {
  if (projVal == null) return '';
  return `<div class="cube-proj-row">
            <span>Proy.</span>
            <span class="cube-proj-val">${num(projVal, 1)}</span>
            ${_cubeParkBadge(parkDelta, parkFactor)}
            ${_cubeRightPill(pOver, edge)}
          </div>`;
}

// Tanda 1 — market data row: user's actual cuota (when entered) + market reference.
// Three states:
//   1) user has manually entered cuota+casino → "★ Tuya {casino} {cuota}"
//   2) no user cuota yet, market reference exists → "⏳ Cargá tu cuota ↓" + "↗ Ref"
//   3) Sin nada → no renderiza
//
// Note: pick.casino/odds_decimal are the SOURCE OF TRUTH for "what the user
// chose to bet at". Auto-generate no longer auto-fills these (auto_fill_odds=False),
// so they're only set by manual entry.
function _cubeMarketRow(pick) {
  const userCasino  = pick && pick.casino;
  const userOdds    = pick && pick.odds_decimal;
  const hasUserBet  = !!(userCasino && userOdds && userOdds > 1);
  const marketBest  = pick && pick.market_best_casino;
  const marketOdds  = pick && pick.market_best_odds;
  const pConsensus  = pick && pick.p_over_market;
  const nBooks      = pick && pick.n_books_market;
  if (!hasUserBet && !marketBest && pConsensus == null) return '';

  const rec = pick.recommendation;

  // Direction-relative consensus probability
  let consensusPct = null;
  if (pConsensus != null) {
    const p = rec === 'UNDER' ? (1 - pConsensus) : pConsensus;
    consensusPct = (p * 100).toFixed(1);
  }
  const evMarket = pick.ev_market_pct;
  const evMarketCls = evMarket == null ? 'mk-neu'
                    : evMarket >= 0.05  ? 'mk-pos'
                    : evMarket >= 0     ? 'mk-warn'
                    :                     'mk-neg';
  const evMarketDisp = evMarket != null
    ? `${evMarket >= 0 ? '+' : ''}${(evMarket * 100).toFixed(1)}%`
    : '—';

  let userBestChip = '';
  let marketBestChip = '';

  if (hasUserBet) {
    // State 1: user has manually entered their cuota
    userBestChip = `
      <span class="cube-mk-chip cube-mk-best" title="Tu cuota cargada">
        <span class="cube-mk-lbl">★ Tuya</span>
        <span class="cube-mk-val">${userCasino}</span>
        <span class="cube-mk-odds">${num(userOdds, 2)}</span>
      </span>`;
    // Show market chip ONLY when market beats user by >1%
    const showMarket = marketBest && marketOdds != null
      && (userCasino !== marketBest && userOdds != null && marketOdds > userOdds + 0.01);
    if (showMarket) {
      const missedPct = ((marketOdds - userOdds) / userOdds * 100).toFixed(1);
      marketBestChip = `
        <span class="cube-mk-chip cube-mk-market" title="Mejor cuota en TODO el mercado · estás dejando ${missedPct}% en la mesa">
          <span class="cube-mk-lbl">↗ Mkt</span>
          <span class="cube-mk-val">${marketBest}</span>
          <span class="cube-mk-odds">${num(marketOdds, 2)}</span>
        </span>`;
    }
  } else if (marketBest && marketOdds != null) {
    // State 2: no user cuota yet — show waiting + market reference
    userBestChip = `
      <span class="cube-mk-chip cube-mk-nobook" title="Cargá la cuota que viste en bet365/Betano abajo. La app valida con simulación.">
        <span class="cube-mk-lbl">⏳ Cargá</span>
        <span class="cube-mk-val">tu cuota ↓</span>
      </span>`;
    marketBestChip = `
      <span class="cube-mk-chip cube-mk-market" title="Cuota óptima del mercado global — referencia para comparar">
        <span class="cube-mk-lbl">↗ Óptima</span>
        <span class="cube-mk-val">${marketBest}</span>
        <span class="cube-mk-odds">${num(marketOdds, 2)}</span>
      </span>`;
  }

  const consensusChip = (consensusPct != null)
    ? `<span class="cube-mk-chip" title="Consenso del mercado de-vigeado, ponderado por sharpness · ${nBooks || 0} libros">
         <span class="cube-mk-lbl">Mercado</span>
         <span class="cube-mk-val">${consensusPct}% ${rec || ''}</span>
         <span class="cube-mk-n">n=${nBooks || 0}</span>
       </span>`
    : '';

  const evMarketChip = evMarket != null
    ? `<span class="cube-mk-chip cube-mk-ev ${evMarketCls}" title="EV calculado contra el consenso del mercado (vs EV vs modelo)">
         <span class="cube-mk-lbl">EV mkt</span>
         <span class="cube-mk-val">${evMarketDisp}</span>
       </span>`
    : '';

  return `<div class="cube-market-row">${userBestChip}${marketBestChip}${consensusChip}${evMarketChip}</div>`;
}

// Tanda 1 — manual-odds entry block. Renders below the market row when:
//   • The pick is open (not settled W/L/P)
//   • Recommendation is OVER or UNDER (skip pure-SKIP picks)
//   • The user hasn't entered their cuota yet (pick.casino + odds_decimal null)
// User pastes the cuota they see in their actual book; submitManualOdds()
// hits /api/picks/evaluate-bet which runs the simulation and renders the
// verdict panel (replacing this entry form).
function _cubeManualEntryRow(pick) {
  if (!pick || pick.actual_ks != null || pick.result) return '';
  if (pick.recommendation === 'SKIP') return '';
  // Render when user hasn't manually entered their cuota yet
  const hasUserBet = !!(pick.casino && pick.odds_decimal && pick.odds_decimal > 1);
  if (hasUserBet) return '';

  const safeId = (pick.pitcher_name || '').replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
  return `
    <div class="cube-manual-entry" data-pitcher="${pick.pitcher_name}" data-team="${pick.team || ''}" data-kline="${pick.k_line}" data-rec="${pick.recommendation || ''}">
      <div class="cme-title">¿Qué cuota viste en tu casino?</div>
      <div class="cme-fields">
        <input type="text" class="cme-odds" id="cme-odds-${safeId}"
               placeholder="1.85 ó -129" inputmode="decimal" autocomplete="off">
        <input type="text" class="cme-casino" id="cme-casino-${safeId}"
               placeholder="bet365" autocomplete="off">
        <button class="cme-eval-btn" onclick="submitManualOdds(this)">Evaluar</button>
      </div>
      <div class="cme-hint">Aceptá decimal (1.85) o American (-129 / +175). El veredicto sale después de evaluar.</div>
    </div>`;
}

// Convert American odds (-129 / +175) → decimal (1.78 / 2.75).
// Returns null on invalid input. Decimals pass through unchanged.
function _toDecimalOdds(raw) {
  const s = (raw || '').toString().trim().replace(',', '.');
  if (!s) return null;
  // Decimal: positive number > 1 (e.g., 1.85, 2.40)
  const dec = parseFloat(s);
  if (!isFinite(dec)) return null;
  if (s.startsWith('+') || s.startsWith('-')) {
    // American format
    const am = dec; // already includes sign
    if (am > 0) return Math.round((1 + am / 100) * 1000) / 1000;
    if (am < 0) return Math.round((1 + 100 / Math.abs(am)) * 1000) / 1000;
    return null;
  }
  // No sign — assume decimal, but reject obvious American positives like "175"
  if (dec >= 100) {
    // Treat as American positive
    return Math.round((1 + dec / 100) * 1000) / 1000;
  }
  if (dec > 1.01 && dec < 100) return dec;
  return null;
}

async function submitManualOdds(btn) {
  const card = btn.closest('.cube-manual-entry');
  if (!card) return;
  const pitcher = card.dataset.pitcher;
  const team    = card.dataset.team;
  const kLine   = parseFloat(card.dataset.kline);
  const oddsInp = card.querySelector('.cme-odds');
  const casInp  = card.querySelector('.cme-casino');
  const oddsRaw = oddsInp.value;
  const casino  = (casInp.value || '').trim();

  const oddsDec = _toDecimalOdds(oddsRaw);
  if (!oddsDec || oddsDec <= 1) {
    oddsInp.style.borderColor = 'var(--red)';
    _showToast('Cuota inválida. Aceptá decimal (1.85) o American (-129).', 'error');
    return;
  }
  if (!casino) {
    casInp.style.borderColor = 'var(--red)';
    _showToast('Falta el casino', 'error');
    return;
  }
  if (!isFinite(kLine) || kLine <= 0) {
    _showToast('Pick base sin línea de K', 'error');
    return;
  }

  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    // Step 1 — read-only validation: market reference + simulation
    const evalResult = await apiPost('/api/picks/evaluate-bet', {
      pitcher_name: pitcher,
      team:         team,
      k_line:       kLine,
      odds_decimal: oddsDec,
      casino:       casino,
      n_sim_bets:   100,
    });

    // Step 2 — render the validation panel inside the cube (replaces the entry form)
    _renderValidationPanel(card, evalResult);

    // Step 3 — also persist via /api/picks/lines so the cube state syncs
    // (so refresh shows star+cuota+EV instead of "Sin acceso")
    await apiPost('/api/picks/lines', {
      pitcher_name: pitcher,
      team:         team,
      k_line:       kLine,
      odds_decimal: oddsDec,
      casino:       casino,
    });
  } catch (e) {
    console.error(e);
    _showToast(`Error: ${e.message || 'no se pudo evaluar'}`, 'error');
    btn.disabled    = false;
    btn.textContent = 'Evaluar';
  }
}

// Render the 3-section validation result panel: market reference, your bet,
// simulation distribution, verdict. Replaces the input form within the cube.
function _renderValidationPanel(hostEl, data) {
  const m       = data.market || {};
  const u       = data.user   || {};
  const sim     = data.simulation;
  const verdict = data.verdict;

  // Verdict styling
  const verdictCfg = {
    'STRONG_BET': { cls: 'cvp-strong', icon: '✅', label: 'APOSTÁ FUERTE' },
    'BET':        { cls: 'cvp-good',   icon: '✅', label: 'APOSTABLE' },
    'MARGINAL':   { cls: 'cvp-marg',   icon: '🟡', label: 'MARGINAL' },
    'NO':         { cls: 'cvp-no',     icon: '❌', label: 'NO APOSTAR' },
  }[verdict] || { cls: 'cvp-marg', icon: '·', label: verdict || '—' };

  const moneyFmt = (v) => v == null ? '—' : money(v, true);
  const pct      = (v, d=1) => v == null ? '—' : `${v >= 0 ? '+' : ''}${(v*100).toFixed(d)}%`;
  const num2     = (v)    => v == null ? '—' : Number(v).toFixed(2);
  const num4     = (v)    => v == null ? '—' : Number(v).toFixed(4);
  const ppDelta  = (v)    => v == null ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(1)}pp`;

  // Profit-band visual: shows where the median + 90% CI lands relative to break-even.
  let simBlock = '';
  if (sim) {
    const p5 = sim.p5_pnl, p95 = sim.p95_pnl, med = sim.median_pnl, exp = sim.expected_pnl;
    // Compute relative bar widths for a simple horizontal range bar centered at 0
    const range = Math.max(Math.abs(p5), Math.abs(p95), 1);
    const leftPct  = ((Math.min(p5, 0) + range) / (2 * range)) * 100;
    const rightPct = ((Math.max(p95, 0) + range) / (2 * range)) * 100;
    const medPct   = ((med + range) / (2 * range)) * 100;
    const zeroPct  = (range / (2 * range)) * 100; // == 50%
    const profitProb = sim.p_profit != null ? (sim.p_profit * 100).toFixed(1) : '—';
    simBlock = `
      <div class="cvp-section">
        <div class="cvp-sec-title">SIMULACIÓN — ${sim.n_bets} apuestas a este edge</div>
        <div class="cvp-sim-grid">
          <div class="cvp-kv">
            <span class="cvp-k">P(profit)</span>
            <span class="cvp-v ${sim.p_profit > 0.5 ? 'cvp-pos' : 'cvp-neg'}">${profitProb}%</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Profit esperado</span>
            <span class="cvp-v ${exp > 0 ? 'cvp-pos' : 'cvp-neg'}">${moneyFmt(exp)}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Mediana</span>
            <span class="cvp-v">${moneyFmt(med)}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Stake/apuesta</span>
            <span class="cvp-v">${moneyFmt(sim.stake_pesos)}</span>
          </div>
        </div>
        <div class="cvp-band-wrap">
          <div class="cvp-band">
            <div class="cvp-band-zero" style="left:${zeroPct}%"></div>
            <div class="cvp-band-fill"
                 style="left:${leftPct}%; width:${rightPct - leftPct}%"></div>
            <div class="cvp-band-median" style="left:${medPct}%"></div>
          </div>
          <div class="cvp-band-labels">
            <span class="cvp-band-lbl-left">Peor 5% ${moneyFmt(p5)}</span>
            <span class="cvp-band-lbl-zero">$0</span>
            <span class="cvp-band-lbl-right">Mejor 5% ${moneyFmt(p95)}</span>
          </div>
        </div>
      </div>`;
  }

  hostEl.outerHTML = `
    <div class="cube-validation-panel ${verdictCfg.cls}">
      <div class="cvp-section">
        <div class="cvp-sec-title">MERCADO — referencia</div>
        <div class="cvp-sim-grid">
          <div class="cvp-kv">
            <span class="cvp-k">Cuota óptima</span>
            <span class="cvp-v cvp-money-fmt">${num2(m.best_odds)}</span>
            <span class="cvp-sub">${m.best_casino || '—'}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Fair (sin vig)</span>
            <span class="cvp-v">${num2(m.fair_price)}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">P(${data.direction || ''}) consenso</span>
            <span class="cvp-v">${m.p_consensus != null ? (m.p_consensus*100).toFixed(1) + '%' : '—'}</span>
            <span class="cvp-sub">n=${m.n_books || 0}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Línea</span>
            <span class="cvp-v">${data.k_line != null ? num(data.k_line, 1) : '—'}</span>
          </div>
        </div>
      </div>

      <div class="cvp-section">
        <div class="cvp-sec-title">TU APUESTA</div>
        <div class="cvp-sim-grid">
          <div class="cvp-kv">
            <span class="cvp-k">Tu cuota</span>
            <span class="cvp-v">${num2(u.odds)}</span>
            <span class="cvp-sub">${u.casino || '—'}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Edge sobre fair</span>
            <span class="cvp-v ${u.edge_vs_fair_pp >= 0 ? 'cvp-pos' : 'cvp-neg'}">${ppDelta(u.edge_vs_fair_pp)}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">EV por unidad</span>
            <span class="cvp-v ${u.ev_pct >= 0 ? 'cvp-pos' : 'cvp-neg'}">${pct(u.ev_pct)}</span>
          </div>
          <div class="cvp-kv">
            <span class="cvp-k">Stake (Kelly)</span>
            <span class="cvp-v">${moneyFmt(u.stake_pesos)}</span>
          </div>
        </div>
      </div>

      ${simBlock}

      <div class="cvp-verdict">
        <span class="cvp-verdict-icon">${verdictCfg.icon}</span>
        <span class="cvp-verdict-label">${verdictCfg.label}</span>
        <span class="cvp-verdict-msg">${data.verdict_msg || ''}</span>
      </div>

      <div class="cvp-actions">
        <button class="cvp-redo-btn" onclick="_resetValidationPanel(this)">Re-evaluar otra cuota</button>
      </div>
    </div>`;
}

// Reset the result panel back to the input form so user can try a different cuota
// without reloading the page. We just re-render the cube from cached pick data.
function _resetValidationPanel(btn) {
  const panel = btn.closest('.cube-validation-panel');
  if (!panel) return;
  // Easiest: refresh the picks view so the cube re-renders with current state
  if (typeof loadPicks === 'function') loadPicks();
  if (typeof loadTodaysPicks === 'function') loadTodaysPicks();
}

// Bundle A #3 — EV / Quarter-Kelly / stake in pesos (when bankroll unit is set).
// kellyUnits is misnamed — it's actually the kelly fraction-of-bankroll
// (e.g. 0.007 = 0.7% of bankroll). We display it as a % so the value is
// directly readable ("0.7% banca") instead of the cryptic "0.01u".
function _cubeStakeRow(evPct, kellyUnits, stakePesos) {
  if (evPct == null || kellyUnits == null) return '';
  const evPctDisp = (evPct * 100).toFixed(1);
  const evSign    = evPct >= 0 ? '+' : '';
  const evCls     = evPct >= 0.02 ? 'ev-pos'
                  : evPct >= 0     ? 'ev-neu'
                  : 'ev-neg';
  // kellyUnits stores the fraction (0.007 = 0.7% of bankroll). Display as %.
  const kellyPct  = kellyUnits * 100;
  const kellyDisp = kellyUnits > 0
    ? `${kellyPct < 1 ? kellyPct.toFixed(2) : kellyPct.toFixed(1)}%`
    : 'skip';
  // money() already returns "$3.600" — don't add another $ label.
  const stakeMoney = stakePesos != null && stakePesos > 0
    ? `<span class="cube-stake-sep">·</span>
       <span class="cube-stake-label">Apuesta</span>
       <span class="cube-stake-val cube-stake-money">${money(stakePesos)}</span>`
    : '';
  return `
    <div class="cube-stake ${evCls}" title="EV por unidad · Quarter-Kelly como % de banca · Stake en pesos (capado al tope %)">
      <span class="cube-stake-label">EV</span>
      <span class="cube-stake-val">${evSign}${evPctDisp}%</span>
      <span class="cube-stake-sep">·</span>
      <span class="cube-stake-label">Kelly</span>
      <span class="cube-stake-val">${kellyDisp}</span>
      ${stakeMoney}
    </div>`;
}

// Auto-P&L row — realized profit/loss in pesos (only when pick is settled).
function _cubePnlRow(pnlPesos, result) {
  if (pnlPesos == null || !result) return '';
  const cls = result === 'W' ? 'pnl-win'
            : result === 'L' ? 'pnl-loss'
            : 'pnl-push';
  const label = result === 'W' ? 'Ganaste' : result === 'L' ? 'Perdiste' : 'Empate';
  return `
    <div class="cube-pnl ${cls}">
      <span class="cube-pnl-label">${label}</span>
      <span class="cube-pnl-val">${money(pnlPesos, true)}</span>
    </div>`;
}

// Settled-result pill shared by both action rows.
function _cubeResultPill(actualKs, result) {
  const rc   = result === 'W' ? 'win' : result === 'L' ? 'loss' : 'push';
  const icon = result === 'W' ? '✓'   : result === 'L' ? '✕'    : '·';
  return `<span class="tp-result-pill ${rc}">${icon} ${actualKs}K</span>`;
}

// ── Build a single Pitcher-Props cube ──────────────────────────────────────
function _buildCubeCard(p, pick) {
  const name   = p.name;
  const safeId = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
  const rec    = pick ? (pick.recommendation || 'SKIP') : 'SKIP';
  const isHot  = rec === 'OVER' || rec === 'UNDER';
  // Chip semantics:
  //   • Before pick:  DATOS N/5  (signal completeness — how many features we have)
  //   • After  pick:  CONF  N/5  (model confidence incl. tree-variance proxy)
  const confLabel = (pick && pick.confidence != null)
    ? `CONF ${pick.confidence}/5`
    : `DATOS ${p.confidence_level || 1}/5`;

  const cherryHtml = isHot
    ? `<img src="cherry.png" class="cube-cherry" alt="${rec}" title="${rec}">`
    : '';
  const dirBadge = isHot
    ? `<span class="cube-dir-badge">${rec === 'OVER' ? '▲' : '▼'} ${rec}</span>`
    : '';

  // K-LINE cell — either a settled value or an entry form
  const klineCell = pick
    ? `<div class="cube-cell">
         <div class="cube-cell-label">Línea de Ponches</div>
         <div class="cube-cell-val">
           ${num(pick.k_line, 1)}
           <button class="cube-cell-edit" onclick="editCubeKLine(this)" title="Cambiar línea">✎</button>
         </div>
       </div>`
    : `<div class="cube-cell cube-kline-cell">
         <div class="cube-cell-label">Línea de Ponches</div>
         <input type="text" inputmode="decimal" class="cube-cell-input cube-kline-input" placeholder="5.5"
                onfocus="this.closest('.cube-cell').classList.add('open')"
                onblur="setTimeout(()=>{const c=this.closest('.cube-cell');if(c&&!c.contains(document.activeElement))c.classList.remove('open')},250)"
                onkeydown="if(event.key==='Enter'){event.preventDefault();submitCubeLine(this.closest('.cube-card'))}">
         <div class="cube-kline-chips">
           ${_KLINE_PRESETS.map(v =>
             `<button type="button" class="cube-kline-chip"
                      onpointerdown="event.preventDefault();_pickCubeLine(this,${v})"
                      ontouchstart="event.preventDefault();_pickCubeLine(this,${v})"
                      onclick="event.preventDefault()">${v}</button>`
           ).join('')}
         </div>
       </div>`;

  // ODDS cell — always editable; pre-filled when pick exists and has odds.
  // Submits on Enter, Tab/blur, or any field exit. Escape reverts to original.
  const oddsVal = pick && pick.odds_decimal != null ? num(pick.odds_decimal, 2) : '';
  const oddsCell = `
    <div class="cube-cell">
      <div class="cube-cell-label">Cuota</div>
      <input type="text" inputmode="decimal" class="cube-cell-input cube-odds-input"
             placeholder="1.91" value="${oddsVal}" data-orig-odds="${oddsVal}"
             onblur="submitCubeOdds(this)"
             onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}else if(event.key==='Escape'){event.preventDefault();this.value=this.dataset.origOdds||'';this.blur()}">
    </div>`;

  // Model projection + probability/edge pill — shown whenever we have a projection,
  // not only after a pick is logged (empty cubes preview the model's call).
  const projVal = pick && pick.model_proj != null ? pick.model_proj
                : (p.model_projection ?? null);
  const edgeVal = pick && pick.edge    != null ? pick.edge    : (p.edge_pct ?? null);
  const pOver   = pick && pick.p_over  != null ? pick.p_over  : (p.p_over   ?? null);
  const projRow = _cubeProjRow(projVal, pOver, edgeVal, p.park_k_delta, p.park_k_factor);

  // Casino / "Mejor cuota" — editable.
  const casinoStr = pick && pick.casino ? pick.casino : '';
  const casinoRow = `
    <div class="cube-casino">
      <span class="cube-casino-label">Mejor cuota</span>
      <span class="cube-casino-name" id="cube-casino-${safeId}">${casinoStr || '—'}</span>
      <button class="cube-casino-edit" onclick="editCubeCasino(this)" title="Editar casino">✎</button>
    </div>`;

  const marketRow      = pick ? _cubeMarketRow(pick)      : '';
  const manualEntryRow = pick ? _cubeManualEntryRow(pick) : '';
  const stakeRow       = pick ? _cubeStakeRow(pick.ev_pct, pick.kelly_units, pick.stake_pesos) : '';
  const pnlRow         = pick ? _cubePnlRow(pick.pnl_pesos, pick.result) : '';

  // Actions row (result / cancel)
  let actions = '';
  if (pick && pick.actual_ks != null && pick.result) {
    actions = `<div class="cube-actions">${_cubeResultPill(pick.actual_ks, pick.result)}</div>`;
  } else if (pick && rec !== 'SKIP') {
    actions = `
      <div class="cube-actions">
        <button type="button" class="cube-cancel-btn" onclick="cancelCubePick(this,event)" title="Deshacer pick">↷</button>
        <input type="text" inputmode="decimal" class="result-ks-input tp-ks-input cube-ks-input" placeholder="Ks"
               onkeydown="if(event.key==='Enter'){event.preventDefault();submitCubeResult(this.nextElementSibling)}">
        <button class="tp-log-btn" onclick="submitCubeResult(this)">LOG</button>
      </div>`;
  } else if (pick) {
    actions = `
      <div class="cube-actions">
        <button type="button" class="cube-cancel-btn" onclick="cancelCubePick(this,event)" title="Deshacer">↷</button>
        <span style="flex:1;text-align:center;font-size:11px;color:var(--text-dim);align-self:center">SKIP</span>
      </div>`;
  }

  const overCls = rec === 'OVER' ? 'cube--over' : rec === 'UNDER' ? 'cube--under' : '';

  return `
    <div class="cube-card ${overCls}" id="cube-${safeId}" data-pitcher="${name}" data-team="${p.team || ''}" data-safeid="${safeId}">
      ${dirBadge}
      <span class="cube-conf-chip">${confLabel}</span>
      <div class="cube-avatar-wrap">
        ${cherryHtml}
        ${_cubeAvatarHtml(name, p.pitcher_id)}
      </div>
      <div class="cube-name">${name}</div>
      ${_cubeMatchupHtml(p.team, p.opponent)}
      ${projRow}
      <div class="cube-cells">
        ${klineCell}
        ${oddsCell}
      </div>
      ${casinoRow}
      ${marketRow}
      ${manualEntryRow}
      ${stakeRow}
      ${pnlRow}
      ${actions}
    </div>`;
}

function renderPropsCubes(pitchers, picksMap = {}) {
  const grid = document.getElementById('props-cubes-grid');
  if (!grid) return;
  _lastPropsPitchers = pitchers;
  _lastPropsPicksMap = picksMap;
  if (!pitchers.length) {
    grid.innerHTML = '<div class="cube-empty">No hay pitchers en el slate de hoy.</div>';
    return;
  }
  // Sort: actionable (OVER/UNDER) first, then remaining by confidence
  const ordered = pitchers.slice().sort((a, b) => {
    const pa = picksMap[a.name.toLowerCase()];
    const pb = picksMap[b.name.toLowerCase()];
    const aHot = pa && (pa.recommendation === 'OVER' || pa.recommendation === 'UNDER') ? 1 : 0;
    const bHot = pb && (pb.recommendation === 'OVER' || pb.recommendation === 'UNDER') ? 1 : 0;
    if (aHot !== bHot) return bHot - aHot;
    return (b.confidence_level || 0) - (a.confidence_level || 0);
  });
  grid.innerHTML = ordered.map(p => _buildCubeCard(p, picksMap[p.name.toLowerCase()])).join('');
}

// ── Cube actions ───────────────────────────────────────────────────────────

function _pickCubeLine(chip, val) {
  const card  = chip.closest('.cube-card');
  const input = card.querySelector('.cube-kline-input');
  if (input) input.value = val;
  submitCubeLine(card);
}

async function submitCubeLine(cardOrBtn) {
  const card  = cardOrBtn.classList && cardOrBtn.classList.contains('cube-card')
                ? cardOrBtn : cardOrBtn.closest('.cube-card');
  if (!card) return;
  const name  = card.dataset.pitcher;
  const team  = card.dataset.team;
  const input = card.querySelector('.cube-kline-input');
  if (!input) return;
  const raw   = (input.value || '').replace(',', '.').trim();
  const kLine = parseFloat(raw);
  if (isNaN(kLine) || kLine <= 0) { input.focus(); return; }
  input.disabled = true;

  try {
    const pick = await apiPost('/api/picks/lines', { pitcher_name: name, team, k_line: kLine });
    // Sync into master maps
    _lastPropsPicksMap[name.toLowerCase()] = pick;
    // Re-render just this cube
    const idx = _lastPropsPitchers.findIndex(x => x.name === name);
    const p   = idx >= 0 ? _lastPropsPitchers[idx] : { name, team, pitcher_id: null };
    card.outerHTML = _buildCubeCard(p, pick);
    // Also keep classic table in sync
    const row = document.getElementById(`prop-row-${card.dataset.safeid}`);
    if (row) _applyPickToRow(row, pick);
    if (pick.recommendation === 'OVER' || pick.recommendation === 'UNDER') {
      _showToast(`${name} → ${pick.recommendation} ${kLine} · guardado`, 'success');
    }
  } catch (e) {
    console.error(e);
    input.disabled = false;
    input.style.borderColor = 'var(--red)';
    _showToast(`Error: ${e.message}`, 'error');
  }
}

async function submitCubeOdds(input) {
  const card = input.closest('.cube-card');
  if (!card) return;
  const name = card.dataset.pitcher;
  const raw  = (input.value || '').replace(',', '.').trim();
  const existing = _lastPropsPicksMap[name.toLowerCase()];

  // No pick yet — give feedback instead of silent no-op
  if (!existing) {
    if (raw) _showToast('Primero cargá la línea (K-Line)', 'info');
    return;
  }
  // Empty blur — keep the pre-existing odds, nothing to do
  if (!raw) return;

  const odds = parseFloat(raw);
  if (isNaN(odds) || odds <= 1) {
    input.style.borderColor = 'var(--red)';
    _showToast('Cuota inválida (debe ser decimal > 1)', 'error');
    return;
  }
  // Same value — no-op so an Enter→blur doesn't re-submit
  if (existing.odds_decimal != null && Math.abs(existing.odds_decimal - odds) < 0.0001) return;

  // Guard against double-submits from overlapping blur/Enter events
  if (input.dataset.submitting === '1') return;
  input.dataset.submitting = '1';
  input.disabled = true;
  input.style.borderColor = '';

  try {
    const pick = await apiPost('/api/picks/lines', {
      pitcher_name: name,
      team:         card.dataset.team,
      k_line:       existing.k_line,
      odds_decimal: odds,
    });
    _lastPropsPicksMap[name.toLowerCase()] = pick;

    // Re-render the cube so the freshly computed EV / Kelly / stake rows show up
    const idx = _lastPropsPitchers.findIndex(x => x.name === name);
    const p   = idx >= 0 ? _lastPropsPitchers[idx] : { name, team: card.dataset.team, pitcher_id: null };
    card.outerHTML = _buildCubeCard(p, pick);
    // Classic table sync too
    const row = document.getElementById(`prop-row-${card.dataset.safeid}`);
    if (row) _applyPickToRow(row, pick);

    const evTxt = pick.ev_pct != null ? ` · EV ${(pick.ev_pct*100).toFixed(1)}%` : '';
    const stkTxt = pick.stake_pesos ? ` · ${money(pick.stake_pesos)}` : '';
    _showToast(`${name} @ ${odds.toFixed(2)}${evTxt}${stkTxt}`, 'success');
  } catch (e) {
    console.error(e);
    input.disabled = false;
    input.dataset.submitting = '0';
    input.style.borderColor = 'var(--red)';
    _showToast(`Error: ${e.message}`, 'error');
  }
}

function editCubeCasino(btn) {
  const card = btn.closest('.cube-card');
  const name = card.dataset.pitcher;
  const safeId = card.dataset.safeid;
  const nameEl = document.getElementById(`cube-casino-${safeId}`);
  const current = nameEl && nameEl.textContent !== '—' ? nameEl.textContent : '';
  const val = prompt('¿Casino con mejor cuota?', current);
  if (val == null) return;
  const casino = val.trim();
  const existing = _lastPropsPicksMap[name.toLowerCase()];
  if (!existing) {
    _showToast('Primero ingresá la línea de K', 'info');
    return;
  }
  apiPost('/api/picks/lines', {
    pitcher_name: name,
    team:         card.dataset.team,
    k_line:       existing.k_line,
    casino:       casino || null,
  }).then(pick => {
    _lastPropsPicksMap[name.toLowerCase()] = pick;
    if (nameEl) nameEl.textContent = casino || '—';
  }).catch(e => {
    console.error(e);
    _showToast(`Error: ${e.message}`, 'error');
  });
}

function editCubeKLine(btn) {
  const card = btn.closest('.cube-card');
  const name = card.dataset.pitcher;
  const existing = _lastPropsPicksMap[name.toLowerCase()];
  // Rebuild cube without pick so entry form reappears
  const p = _lastPropsPitchers.find(x => x.name === name);
  if (!p) return;
  // Temporarily treat as no-pick while editing
  card.outerHTML = _buildCubeCard(p, null);
  const fresh = document.getElementById(`cube-${card.dataset.safeid}`);
  if (fresh && existing) {
    const input = fresh.querySelector('.cube-kline-input');
    if (input) { input.value = existing.k_line; input.focus(); input.closest('.cube-cell').classList.add('open'); }
  }
}

async function submitCubeResult(btn) {
  const card   = btn.closest('.cube-card');
  const name   = card.dataset.pitcher;
  const input  = card.querySelector('.cube-ks-input');
  const actual = parseFloat((input.value || '').replace(',', '.'));
  if (isNaN(actual) || actual < 0) { input.focus(); return; }

  btn.disabled = true;
  btn.textContent = '…';
  input.disabled = true;

  const today = new Date().toLocaleDateString('en-CA');
  try {
    const result = await apiPost('/api/picks/result', {
      date: today, pitcher_name: name, actual_ks: actual,
    });
    // Update stored pick + re-render this cube
    const stored = _lastPropsPicksMap[name.toLowerCase()] || {};
    stored.actual_ks = actual;
    stored.result    = result.result;
    _lastPropsPicksMap[name.toLowerCase()] = stored;
    const p = _lastPropsPitchers.find(x => x.name === name);
    card.outerHTML = _buildCubeCard(p || { name, team: card.dataset.team }, stored);

    const res   = result.result || '';
    const label = res === 'W' ? 'WIN' : res === 'L' ? 'LOSS' : res === 'P' ? 'PUSH' : '?';
    _showToast(`${name} — ${actual} Ks → ${label}`, res === 'W' ? 'success' : 'info');

    // Keep classic table in sync (if present)
    const row = document.getElementById(`prop-row-${card.dataset.safeid}`);
    if (row) {
      const rc   = res === 'W' ? 'win' : res === 'L' ? 'loss' : res === 'P' ? 'push' : 'pending';
      const icon = res === 'W' ? '✓' : res === 'L' ? '✕' : '·';
      const cells = row.querySelectorAll('td');
      if (cells[9]) cells[9].innerHTML = `<span class="tp-result-pill ${rc}">${icon} ${actual}K</span>`;
    }
  } catch (e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = 'LOG';
    input.disabled = false;
    _showToast(`Error: ${e.message}`, 'error');
  }
}

async function cancelCubePick(btn, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const card = btn.closest('.cube-card');
  if (!card) return;
  const name = card.dataset.pitcher;
  btn.disabled = true;
  btn.textContent = '…';
  const today = new Date().toLocaleDateString('en-CA');
  try {
    await apiPost('/api/picks/cancel', { date: today, pitcher_name: name });
    delete _lastPropsPicksMap[name.toLowerCase()];
    const p = _lastPropsPitchers.find(x => x.name === name);
    if (p) card.outerHTML = _buildCubeCard(p, null);
    // Keep classic table in sync
    const row = document.getElementById(`prop-row-${card.dataset.safeid}`);
    if (row) {
      const cells = row.querySelectorAll('td');
      cells[2].innerHTML = _klineEntry();
      cells[3].innerHTML = '<span style="color:var(--text-dim)">—</span>';
      cells[4].innerHTML = '<span class="pp-edge-pill none">—</span>';
      cells[5].innerHTML = _ciBar(null, null, null);
      cells[9].innerHTML = '<span style="color:var(--text-dim);font-size:11px">—</span>';
      const pitcherCell = cells[0].querySelector('.pp-pitcher-cell');
      if (pitcherCell) {
        const cherry = pitcherCell.querySelector('.pp-cherry-badge, .pp-cherry-spacer');
        if (cherry) {
          const spacer = document.createElement('span');
          spacer.className = 'pp-cherry-spacer';
          pitcherCell.replaceChild(spacer, cherry);
        }
      }
      row.classList.remove('pp-top-pick-row');
    }
  } catch (e) {
    console.error(e);
    btn.disabled = false;
    btn.textContent = '↷';
  }
}

// ── Today's-Picks cube (OVER/UNDER only; read-only line, editable result) ──
function _buildTpCubeCard(pick) {
  const name   = pick.pitcher_name;
  const safeId = name.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
  const rec    = pick.recommendation;

  const oddsStr   = pick.odds_decimal != null ? num(pick.odds_decimal, 2) : '—';
  const casinoStr = pick.casino || '';
  const confLabel = pick.confidence != null ? `CONF ${pick.confidence}/5` : '';

  const actions = (pick.actual_ks != null && pick.result)
    ? `<div class="cube-actions">${_cubeResultPill(pick.actual_ks, pick.result)}</div>`
    : `<div class="cube-actions">
         <input type="text" inputmode="decimal" class="tp-ks-input cube-ks-input"
                placeholder="Ks" data-pitcher="${name}" data-safeid="${safeId}"
                onkeydown="if(event.key==='Enter'){event.preventDefault();submitTodaysPick(this.nextElementSibling)}">
         <button class="tp-log-btn" onclick="submitTodaysPick(this)">LOG</button>
       </div>`;

  const projRow        = _cubeProjRow(pick.model_proj, pick.p_over, pick.edge, pick.park_k_delta, pick.park_k_factor);
  const marketRow      = _cubeMarketRow(pick);
  const manualEntryRow = _cubeManualEntryRow(pick);
  const stakeRow       = _cubeStakeRow(pick.ev_pct, pick.kelly_units, pick.stake_pesos);
  const pnlRow         = _cubePnlRow(pick.pnl_pesos, pick.result);

  return `
    <div class="cube-card cube--${rec.toLowerCase()}" id="tpcube-${safeId}" data-pitcher="${name}" data-safeid="${safeId}">
      <span class="cube-dir-badge">${rec === 'OVER' ? '▲' : '▼'} ${rec}</span>
      ${confLabel ? `<span class="cube-conf-chip">${confLabel}</span>` : ''}
      <div class="cube-avatar-wrap">
        <img src="cherry.png" class="cube-cherry" alt="${rec}">
        ${_cubeAvatarHtml(name, pick.pitcher_id)}
      </div>
      <div class="cube-name">${name}</div>
      ${_cubeMatchupHtml(pick.team, pick.opponent)}
      ${projRow}
      <div class="cube-cells">
        <div class="cube-cell">
          <div class="cube-cell-label">Línea de Ponches</div>
          <div class="cube-cell-val">${num(pick.k_line, 1)}</div>
        </div>
        <div class="cube-cell">
          <div class="cube-cell-label">Cuota</div>
          <div class="cube-cell-val">${oddsStr}</div>
        </div>
      </div>
      <div class="cube-casino">
        <span class="cube-casino-label">Mejor cuota</span>
        <span class="cube-casino-name">${casinoStr || '—'}</span>
      </div>
      ${marketRow}
      ${manualEntryRow}
      ${stakeRow}
      ${pnlRow}
      ${actions}
    </div>`;
}

function renderTodaysCubes(picks) {
  const grid = document.getElementById('tp-cubes-grid');
  if (!grid) return;
  _lastTodaysPicks = picks;
  const actionable = picks.filter(p => p.recommendation === 'OVER' || p.recommendation === 'UNDER');
  // Always refresh the summary so empty-state handling propagates (e.g., when
  // the slate clears, the previous summary card hides).
  renderCasinoDepositSummary(actionable);
  if (!actionable.length) {
    grid.innerHTML = '<div class="cube-empty">Sin picks accionables aún.</div>';
    return;
  }
  grid.innerHTML = actionable.map(_buildTpCubeCard).join('');
}

// Sums today's recommended stakes by casino so the user knows how much to
// fund each sportsbook before placing the slate. Picks without a casino set
// (`casino` null/empty) get bucketed into "Sin asignar" so they're visible
// but flagged. Settled picks (W/L/P) are excluded — money is already moved.
function renderCasinoDepositSummary(picks) {
  const card     = document.getElementById('casino-deposit-card');
  const rowsEl   = document.getElementById('cdc-rows');
  const totalEl  = document.getElementById('cdc-total-val');
  const footerEl = document.getElementById('cdc-footer');
  if (!card || !rowsEl || !totalEl) return;

  // Only unsettled picks with a positive stake count (logged W/L means money
  // has already moved). SKIPs naturally fall out — they have stake_pesos null.
  const open = (picks || []).filter(p =>
    !p.result &&
    p.stake_pesos != null && p.stake_pesos > 0 &&
    (p.recommendation === 'OVER' || p.recommendation === 'UNDER')
  );

  if (!open.length) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  // Group: { casinoLabel: { total, n_picks, picks: [...], hasOdds: bool } }
  const groups = new Map();
  let unassignedN = 0;
  for (const p of open) {
    const key = (p.casino || '').trim() || '__UNASSIGNED__';
    if (key === '__UNASSIGNED__') unassignedN++;
    const g = groups.get(key) || { total: 0, n_picks: 0, picks: [], hasOdds: false };
    g.total += Number(p.stake_pesos) || 0;
    g.n_picks += 1;
    g.picks.push(p);
    if (p.odds_decimal != null) g.hasOdds = true;
    groups.set(key, g);
  }

  // Sort: assigned casinos by total desc, "unassigned" always last
  const entries = [...groups.entries()].sort((a, b) => {
    if (a[0] === '__UNASSIGNED__') return 1;
    if (b[0] === '__UNASSIGNED__') return -1;
    return b[1].total - a[1].total;
  });

  const grandTotal = open.reduce((sum, p) => sum + (Number(p.stake_pesos) || 0), 0);
  totalEl.textContent = money(grandTotal);

  rowsEl.innerHTML = entries.map(([key, g]) => {
    const isUnassigned = key === '__UNASSIGNED__';
    const label = isUnassigned ? 'Sin casino asignado' : key;
    const cls   = isUnassigned ? 'cdc-row cdc-row--warn' : 'cdc-row';
    const pickList = g.picks.map(p => {
      const dir = p.recommendation === 'OVER' ? '▲' : '▼';
      const odds = p.odds_decimal != null ? num(p.odds_decimal, 2) : '—';
      return `<span class="cdc-pick-chip" title="${p.pitcher_name} · ${dir} ${num(p.k_line, 1)} · ${odds}">
        ${dir} ${p.pitcher_name.split(' ').pop()} <span class="cdc-pick-stake">${money(p.stake_pesos)}</span>
      </span>`;
    }).join('');
    return `
      <div class="${cls}">
        <div class="cdc-row-head">
          <div class="cdc-row-name">${label}</div>
          <div class="cdc-row-meta">
            <span class="cdc-row-n">${g.n_picks} pick${g.n_picks > 1 ? 's' : ''}</span>
            <b class="cdc-row-total">${money(g.total)}</b>
          </div>
        </div>
        <div class="cdc-row-picks">${pickList}</div>
      </div>`;
  }).join('');

  if (unassignedN > 0) {
    footerEl.innerHTML = `
      <span class="cdc-warn-icon">⚠</span>
      ${unassignedN} pick${unassignedN > 1 ? 's' : ''} sin casino asignado.
      Cargá el casino con "Mejor cuota" en cada cubo para que sumen acá.
    `;
    footerEl.style.display = '';
  } else {
    footerEl.style.display = 'none';
    footerEl.innerHTML = '';
  }
}

// ── View-toggle handlers ───────────────────────────────────────────────────
function togglePropsView() {
  const current = _getViewMode('pp-view-mode');
  const next    = current === 'enhanced' ? 'classic' : 'enhanced';
  _setViewMode('pp-view-mode', next);
  _applyPropsViewMode(next);
}

function _applyPropsViewMode(mode) {
  const btn       = document.getElementById('pp-view-toggle');
  const cubeGrid  = document.getElementById('props-cubes-grid');
  const mobileCards = document.getElementById('props-mobile-cards');
  const tableWrap = document.querySelector('#section-pitcher-props .pp-table-wrap');
  if (!btn || !cubeGrid) return;
  if (mode === 'enhanced') {
    btn.classList.add('active');
    cubeGrid.style.display  = '';
    if (tableWrap)  tableWrap.style.display   = 'none';
    if (mobileCards) mobileCards.style.display = 'none';
    // Render now that it's visible
    renderPropsCubes(_lastPropsPitchers, _lastPropsPicksMap);
  } else {
    btn.classList.remove('active');
    cubeGrid.style.display  = 'none';
    if (tableWrap)  tableWrap.style.display   = '';
    if (mobileCards) mobileCards.style.display = '';
  }
}

function toggleTodaysView() {
  const current = _getViewMode('tp-view-mode');
  const next    = current === 'enhanced' ? 'classic' : 'enhanced';
  _setViewMode('tp-view-mode', next);
  _applyTodaysViewMode(next);
}

function _applyTodaysViewMode(mode) {
  const btn       = document.getElementById('tp-view-toggle');
  const cubeGrid  = document.getElementById('tp-cubes-grid');
  const cardsGrid = document.getElementById('tp-cards-grid');
  const tableWrap = document.querySelector('#section-todays-picks .tp-table-wrap');
  if (!btn || !cubeGrid) return;
  if (mode === 'enhanced') {
    btn.classList.add('active');
    cubeGrid.style.display = '';
    if (tableWrap)  tableWrap.style.display  = 'none';
    if (cardsGrid)  cardsGrid.style.display  = 'none';
    renderTodaysCubes(_lastTodaysPicks);
  } else {
    btn.classList.remove('active');
    cubeGrid.style.display = 'none';
    if (tableWrap)  tableWrap.style.display  = '';
    if (cardsGrid)  cardsGrid.style.display  = '';
  }
}

// ── Hook: always populate cubes whenever data reloads ──────────────────────
// Wrap the existing renderers so both classic + enhanced stay in sync.
(function _hookCubeRenderers() {
  const origRenderProps = typeof renderPropsTable === 'function' ? renderPropsTable : null;
  if (origRenderProps) {
    window.renderPropsTable = function (pitchers, picksMap) {
      _lastPropsPitchers = pitchers || [];
      _lastPropsPicksMap = picksMap || {};
      origRenderProps(pitchers, picksMap);
      renderPropsCubes(_lastPropsPitchers, _lastPropsPicksMap);
    };
  }
  const origLoadTodays = typeof loadTodaysPicks === 'function' ? loadTodaysPicks : null;
  if (origLoadTodays) {
    window.loadTodaysPicks = async function () {
      await origLoadTodays();
      // After the classic table renders, populate cubes from the fresh data.
      try {
        const picks = await apiFetch('/api/picks/today').catch(() => []);
        renderTodaysCubes(picks);
      } catch (_) {}
    };
  }
})();

// ── Restore saved view modes on DOM ready ──────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Apply saved view modes on first paint (delay so the app sections exist)
  setTimeout(() => {
    _applyPropsViewMode(_getViewMode('pp-view-mode'));
    _applyTodaysViewMode(_getViewMode('tp-view-mode'));
  }, 0);

  // Default date picker for retro pick form → yesterday (most common use case)
  const rpDate = document.querySelector('#retro-pick-form [name="rp_date"]');
  if (rpDate && !rpDate.value) {
    const y = new Date(); y.setDate(y.getDate() - 1);
    rpDate.value = y.toLocaleDateString('en-CA');
  }
});

// ══════════════════════════════════════════════════════════════════════════
// RETRO PITCHER-PICK FORM (Tracker page)
// ══════════════════════════════════════════════════════════════════════════

function toggleRetroForm() {
  const form = document.getElementById('retro-pick-form');
  const btn  = document.getElementById('retro-toggle-btn');
  if (!form) return;
  const isOpen = form.style.display !== 'none';
  form.style.display = isOpen ? 'none' : '';
  if (btn) btn.textContent = isOpen ? '+ nuevo' : '× cerrar';
}

async function submitRetroPick(ev) {
  ev.preventDefault();
  const form = ev.target;
  const btn  = form.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = 'Guardando…';

  const f = new FormData(form);
  const payload = {
    date:           f.get('rp_date'),
    pitcher_name:   (f.get('rp_pitcher') || '').trim(),
    team:           (f.get('rp_team') || '').trim().toUpperCase(),
    opponent:       (f.get('rp_opp')  || '').trim().toUpperCase(),
    k_line:         parseFloat(f.get('rp_kline')),
    recommendation: f.get('rp_rec'),
    actual_ks:      f.get('rp_actual') ? parseFloat(f.get('rp_actual')) : null,
    odds_decimal:   f.get('rp_odds')   ? parseFloat(f.get('rp_odds'))   : null,
    casino:         (f.get('rp_casino') || '').trim() || null,
    units:          f.get('rp_units')  ? parseFloat(f.get('rp_units'))  : 1.0,
    notes:          (f.get('rp_notes') || '').trim() || null,
  };

  // Basic client validation
  if (!payload.date || !payload.pitcher_name || !payload.team || isNaN(payload.k_line) || !payload.recommendation) {
    _showToast('Completá fecha, pitcher, equipo, k-línea y pick', 'error');
    btn.disabled = false; btn.textContent = 'Guardar';
    return;
  }

  try {
    const res = await apiPost('/api/picks/manual', payload);
    const resultLabel = res.result === 'W' ? 'WIN'
                      : res.result === 'L' ? 'LOSS'
                      : res.result === 'P' ? 'PUSH'
                      : 'pendiente';
    _showToast(`${payload.pitcher_name} — ${payload.date} → ${resultLabel}`, res.result === 'W' ? 'success' : 'info');
    form.reset();
    // Reset date picker to yesterday after submit
    const y = new Date(); y.setDate(y.getDate() - 1);
    form.querySelector('[name="rp_date"]').value = y.toLocaleDateString('en-CA');
    // Refresh tracker UI so new pick shows up
    if (typeof loadTracker === 'function') await loadTracker();
  } catch (e) {
    console.error(e);
    _showToast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Guardar';
  }
}


/* ═══════════════════════════════════════════════════════════════════════════
   SPORT TABS — MLB (modelo de ponches) vs NBA (motor AltLines)
   ══════════════════════════════════════════════════════════════════════════
   Todo lo de aquí abajo es aditivo y autónomo: no toca ninguna función del
   modelo MLB. Las secciones y los ítems de navegación se separan por el
   atributo data-sport (los que no lo llevan son MLB).
   ═══════════════════════════════════════════════════════════════════════════ */

const SPORT_DEFAULT_SECTION = { mlb: 'pitcher-props', nba: 'altlines', futbol: 'futbol' };

function currentSport() {
  return document.body.dataset.sport || 'mlb';
}

/**
 * Cambia de deporte (pestaña MLB / NBA).
 *  · marca la pestaña activa
 *  · muestra solo los ítems de navegación del deporte
 *  · abre la sección por defecto de ese deporte (showSection oculta el resto)
 */
function showSport(sport) {
  const from = currentSport();
  const tab = document.getElementById('sport-tab-' + sport);
  if (from === sport) { _applySport(sport); return; }
  _sportMorph(sport, tab, () => _applySport(sport));
}

function _applySport(sport) {
  document.body.dataset.sport = sport;

  document.querySelectorAll('.sport-tab').forEach(t => {
    const on = t.dataset.sport === sport;
    t.classList.toggle('sport-tab--active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });

  document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
    const s = item.dataset.sport || 'mlb';
    item.style.display = (s === sport) ? '' : 'none';
  });

  // Cierra el buscador de pitchers: no aplica en NBA
  const searchWrap = document.getElementById('topbar-search-wrap');
  if (searchWrap) searchWrap.style.display = (sport === 'mlb') ? '' : 'none';

  showSection(SPORT_DEFAULT_SECTION[sport] || 'pitcher-props', null);
}

/**
 * La app "se convierte": un círculo del color de la app destino crece desde
 * la pestaña tocada hasta cubrir la pantalla, aparece su logo, se aplica el
 * cambio por detrás y el velo se funde revelando la página ya convertida.
 * Con prefers-reduced-motion es un fundido corto.
 */
const _PST_ICON = `<svg class="pst-mark" viewBox="0 0 48 48" aria-hidden="true"><g transform="rotate(28 24 26)"><path d="M24 25.5c-5-3.6-6.6-10.6-4.6-17 1.3-4.2 7.9-4.2 9.2 0 2 6.4.4 13.4-4.6 17z" fill="#9FCF5F"/><path d="M22 23.8c-3.4-3.7-4.4-9.6-2.6-14.9" stroke="#8C4E6C" stroke-width="2.4" stroke-linecap="round" fill="none"/><path d="M25.8 9.2c1.1 2.7 1.3 6.1.6 9.2" stroke="#D6F0A9" stroke-width="1.5" stroke-linecap="round" fill="none"/><path d="M24 45c-8.6 0-13.4-7.4-13.4-16.4 0-8.2 3.3-15.6 7.8-19.4 1 6.6 2.9 12.3 5.6 16.4z" fill="#EBD8AB"/><path d="M24 45c8.6 0 13.4-7.4 13.4-16.4 0-8.2-3.3-15.6-7.8-19.4-1 6.6-2.9 12.3-5.6 16.4z" fill="#D1B37A"/><path d="M18.4 9.2c1 6.6 2.9 12.3 5.6 16.4 2.7-4.1 4.6-9.8 5.6-16.4" stroke="#7B6038" stroke-width="1.1" stroke-linejoin="round" fill="none"/><path d="M24 25.6V45" stroke="#B4955A" stroke-width=".9"/><path d="M14.3 22.5c-.8 2.2-1.1 4.5-1.1 6.8 0 4.6 1.6 8.4 4.6 10.7" stroke="#F7EDD2" stroke-width="1.4" stroke-linecap="round" fill="none"/></g></svg>`;
let _sportMorphing = false;

async function _sportMorph(sport, origin, apply) {
  if (_sportMorphing) return;
  // Si la página no está pintando (pestaña de fondo, ventana tapada) las
  // animaciones no avanzan y sus promesas nunca se resuelven: cada espera tiene
  // un tope, y el cambio de deporte se aplica pase lo que pase.
  const settle = (anim, ms) => Promise.race([
    anim.finished.catch(() => {}), new Promise(res => setTimeout(res, ms))]);
  let applied = false;
  const doApply = () => { if (!applied) { applied = true; apply(); } };
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const veil = document.createElement('div');
  veil.className = `sport-morph sport-morph--${sport}`;
  veil.innerHTML = sport === 'nba'
    ? `<div class="sport-morph-logo">${_PST_ICON}<div>PICK<span>STACHIO</span></div></div>`
    : sport === 'futbol'
    ? `<div class="sport-morph-logo">${window.FB_BALL_ICON || ''}<div>RE<span>MATES</span></div></div>`
    : `<div class="sport-morph-logo"><img src="cherries.png" alt=""><div>CHERRY<span>PICK</span></div></div>`;
  document.body.appendChild(veil);
  const logo = veil.querySelector('.sport-morph-logo');
  const main = document.querySelector('.app-main');
  _sportMorphing = true;
  try {
    if (reduced || !veil.animate || document.hidden) {
      veil.style.opacity = '0';
      await settle(veil.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 150, fill: 'forwards' }), 400);
      doApply();
      await settle(veil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' }), 500);
      return;
    }
    const r = origin ? origin.getBoundingClientRect() : { left: innerWidth / 2, top: 0, width: 0, height: 0 };
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const grow = veil.animate(
      [{ clipPath: `circle(0px at ${x}px ${y}px)` }, { clipPath: `circle(${R}px at ${x}px ${y}px)` }],
      { duration: 620, easing: 'cubic-bezier(.76,0,.24,1)', fill: 'forwards' });
    logo.animate([{ opacity: 0, transform: 'scale(.92)', filter: 'blur(6px)' },
                  { opacity: 1, transform: 'scale(1)', filter: 'blur(0)' }],
                 { duration: 520, delay: 280, easing: 'ease-out', fill: 'forwards' });
    await settle(grow, 1100);
    doApply();
    if (main) { main.classList.remove('app-main-fadein'); void main.offsetWidth; main.classList.add('app-main-fadein'); }
    await new Promise(res => setTimeout(res, 380));
    logo.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(1.06)' }],
                 { duration: 420, easing: 'ease-in', fill: 'forwards' });
    await settle(veil.animate([{ opacity: 1 }, { opacity: 0 }],
                              { duration: 520, delay: 120, easing: 'ease-out', fill: 'forwards' }), 1100);
  } finally {
    doApply();
    veil.remove();
    _sportMorphing = false;
  }
}

// ── NBA · Alt Lines (motor AltLines: Monte Carlo de colas largas) ───────────

// Estado del tablero visible: o un partido, o la liga entera fusionada.
let _altLinesData = null;
const _alFilters = { market: 'all', side: 'all', minEv: 0, search: '', touched: false };

// ── Estado "por partido" ────────────────────────────────────────────────────
// Simular la liga entera de una son ~15 s de CPU; un partido son ~3 s. La
// elección no es cosmética: es lo que hace usable el motor. Y como el RNG se
// siembra por (jugador, mercado), simular un partido devuelve EXACTAMENTE los
// mismos números que el tablero global — sólo menos trabajo.
// La animación de cada tarjeta ("La cola canta") vive en altlines-galton.js:
// tablero de Galton sonoro mientras simula, y el resultado real al terminar.
// Con prefers-reduced-motion se queda quieta (el pulso y el contador avisan).
const AL_N_ITER = 20000;

let _alSlate = null;            // pizarra: calendario + props disponibles
let _alGameResults = {};        // game_id -> payload del motor
let _alSelected = null;         // game_id abierto en el detalle
let _alLeague = null;           // tablero fusionado de todos los simulados
let _alScope = 'game';          // 'game' | 'league'
let _alDate = null;             // fecha pedida (null = la de hoy)
let _alSimAll = false;          // "Simular todos" en curso
let _alBusy = false;            // hay una simulación en vuelo (evita dobles clicks)
// 'easy' = panel de picks (Pickstachio) · 'nerd' = tablas y números
let _alView = (() => {
  try { return localStorage.getItem('al-view') === 'nerd' ? 'nerd' : 'easy'; }
  catch (_) { return 'easy'; }
})();
// true = probabilidades pasadas por la calibración del backtest walk-forward
// (data/altlines/tail_calibration.json) · false = el motor crudo.
let _alCalibrated = (() => {
  try { return localStorage.getItem('al-calibrated') !== 'raw'; }
  catch (_) { return true; }
})();
// 'demo' = datos sintéticos (gratis) · 'live' = ESPN + The Odds API (gasta créditos)
let _alSource = (() => {
  try { return localStorage.getItem('al-source') === 'live' ? 'live' : 'demo'; }
  catch (_) { return 'demo'; }
})();

/**
 * El brief pide "el top 5% de discrepancias", no las 138 líneas.
 * Por defecto abrimos el tablero EXACTAMENTE en el top reportado por el motor
 * (EV mínimo = el peor EV de la lista ya filtrada) y dejamos que el usuario
 * baje el umbral para explorar el resto.
 */
function _alTopEv() {
  const evs = ((_altLinesData && _altLinesData.edges) || [])
    .map(r => Number(r.ev_pct)).filter(Number.isFinite);
  return evs.length ? Math.floor(Math.min(...evs)) : 0;
}

function _alDefaultFilters() {
  if (_alFilters.touched) return;
  _alFilters.minEv = _alTopEv();
}

function altLinesQuick(mode) {
  _alFilters.touched = true;
  _alFilters.minEv = (mode === 'all') ? 0 : _alTopEv();
  const inp = document.getElementById('al-f-ev');
  if (inp) inp.value = _alFilters.minEv;
  renderAltLinesTable();
}

const _alEsc = (s) => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const _alNum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);

async function loadAltLines() {
  const sec = document.getElementById('section-altlines');
  if (!sec) return;

  // El armazón se arma una sola vez: el riel (barra lateral), las herramientas
  // de la barra superior y el contenido. Rehacerlo en cada clic reiniciaría
  // la animación del partido que se está simulando.
  if (!sec.dataset.built) {
    sec.innerHTML = _alShellHtml();
    const rail = document.getElementById('nba-rail');
    if (rail) rail.innerHTML = _alRailHtml();
    const tools = document.getElementById('nba-tools');
    if (tools) tools.innerHTML = _alToolsHtml();
    sec.dataset.built = '1';
  }
  _alSyncSourceBtns();
  _alSyncSoundBtn();
  await loadSlate();
}

// ── Armazón ─────────────────────────────────────────────────────────────────
// Navegar Pickstachio es elegir un partido: por eso la barra lateral es el
// riel de partidos de la noche, la barra superior lleva los ajustes (datos,
// créditos, sonido) y el contenido es el partido elegido.

function _alShellHtml() {
  return `
    <div class="pst-mstrip" aria-label="Partidos de la noche">
      <div class="pst-mstrip-top">
        ${_alDateNavHtml(true)}
        ${_alSourceSegHtml()}
        ${_alSoundBtnHtml()}
      </div>
      <div class="pst-mstrip-list js-rail-list"></div>
    </div>
    <div class="pst-hero-host" id="pst-hero-host"></div>
    <div id="al-detail"></div>
    <div class="al-status" id="al-status" hidden></div>`;
}

function _alRailHtml() {
  return `
    ${_alDateNavHtml(false)}
    <div class="rail-head">
      <span class="rail-label">Partidos</span>
      <span class="rail-meta js-rail-meta"></span>
    </div>
    <div class="rail-list js-rail-list">
      <div class="rail-empty">Consultando el calendario…</div>
    </div>
    <div class="rail-foot">
      <button class="rail-league js-rail-league" onclick="altLinesScope('league')" hidden></button>
      <button class="rail-simall js-simall" onclick="simulateAllGames()">Simular todos</button>
    </div>`;
}

function _alToolsHtml() {
  return `
    ${_alSourceSegHtml()}
    <span class="nba-credits js-credits" hidden></span>
    ${_alSoundBtnHtml()}`;
}

function _alDateNavHtml(compact) {
  return `
    <div class="rail-date ${compact ? 'rail-date--compact' : ''}">
      <button class="rail-arrow" title="Día anterior" aria-label="Día anterior" onclick="altLinesShiftDate(-1)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <button class="rail-day js-rail-day" onclick="altLinesToday()" title="Volver a hoy">
        <span class="rail-dow">—</span><b class="rail-dnum">—</b><span class="rail-mon">—</span>
      </button>
      <button class="rail-arrow" title="Día siguiente" aria-label="Día siguiente" onclick="altLinesShiftDate(1)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
    </div>`;
}

function _alSourceSegHtml() {
  return `
    <div class="nba-seg js-source" role="group" aria-label="Origen de los datos">
      <button data-src="demo" onclick="altLinesSource('demo')"
              title="Jugadores y cuotas sintéticos: gratis, para ver el motor funcionar">Demo</button>
      <button data-src="live" onclick="altLinesSource('live')"
              title="Logs reales (ESPN) y bandejas Alt reales de cada casa (The Odds API). Gasta créditos.">Real</button>
    </div>`;
}

function _alSoundBtnHtml() {
  return `
    <button class="nba-sound js-sound" onclick="altLinesToggleSound()" aria-pressed="false"
            title="Sonido de la simulación">
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3.5 7.5h3l4-3.5v12l-4-3.5h-3z" fill="currentColor"/>
        <path class="nba-sound-on" d="M13.4 7.2a4 4 0 0 1 0 5.6M15.6 5a7 7 0 0 1 0 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <path class="nba-sound-off" d="m13.5 7.5 5 5m0-5-5 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
    </button>`;
}

async function loadSlate(date = _alDate, opts = {}) {
  document.querySelectorAll('.js-rail-list').forEach(el => {
    el.innerHTML = `<div class="rail-empty">Consultando el calendario…</div>`;
  });
  try {
    const q = new URLSearchParams();
    if (date) q.set('date', date);
    if (opts.refresh) q.set('refresh', 'true');
    // La flecha que tocó el usuario: si el día pedido no tiene partidos, el
    // backend busca en ESA dirección (y no en la contraria, que haría que "‹"
    // pareciera roto en los huecos del calendario).
    if (opts.direction) q.set('direction', String(opts.direction));
    q.set('source', _alSource);
    const res = await fetch(`${API}/api/altlines/slate?${q}`);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) { }
      throw new Error(detail);
    }
    _alSlate = await res.json();
    // El backend puede haber corrido la fecha al día más cercano con partidos.
    _alDate = _alSlate.date;
    _alHydrateFromStore();
    _alWatchResults();
    if (_alSelected && !(_alSlate.games || []).some(g => g.game_id === _alSelected)) {
      _alSelected = null;
    }
    renderSlate();
    renderAltLines();
    // Los partidos ya empezados con picks: sus resultados, en segundo plano.
    (_alSlate.games || []).filter(_alNeedsResults)
      .forEach(g => { if (_alGameResults[g.game_id]) _alAttachResults(g.game_id); });
  } catch (e) {
    console.error('AltLines slate fetch failed', e);
    document.querySelectorAll('.js-rail-list').forEach(el => {
      el.innerHTML = `<div class="rail-empty rail-empty--error">Sin calendario.</div>`;
    });
    const st = document.getElementById('al-status');
    if (st) {
      st.hidden = false;
      st.className = 'al-status al-status--error';
      st.innerHTML = `No se pudo leer el calendario NBA: <strong>${_alEsc(e.message)}</strong><br>
        ${_alFetchHint()}<br>Backend esperado en <code>${_alEsc(API)}</code>.`;
    }
  }
}

function altLinesShiftDate(delta) {
  const base = _alDate ? new Date(`${_alDate}T12:00:00`) : new Date();
  base.setDate(base.getDate() + delta);
  const iso = `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, '0')}-${String(base.getDate()).padStart(2, '0')}`;
  _alDate = iso;
  loadSlate(iso, { direction: delta < 0 ? -1 : 1 });
}

function altLinesToday() {
  _alDate = null;
  loadSlate(null);
}

function _alTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '—'
    : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function _alStatusLabel(g) {
  const st = String((g && g.status) || '');
  // El estado llega de ESPN en inglés; lo decimos en castellano y, si el
  // partido ya arrancó, conservamos el detalle del propio calendario.
  const english = { Scheduled: 'Programado', 'In Progress': 'En vivo', Final: 'Final' };
  if (english[st]) return english[st];
  if (g && g.state === 'post') return 'Final';
  if (g && g.state === 'in') return st || 'En vivo';
  return st || 'Programado';
}

// Mejor EV que puede llegar al tablero (excluye las líneas con nivel dudoso).
function _alBestEv(res) {
  const rows = ((res && res.all_edges) || []).filter(r => r.level_ok !== false);
  return rows.length ? Math.max(...rows.map(r => Number(r.ev_pct))) : null;
}

// ── Riel: una fila por partido ──────────────────────────────────────────────

function _alGame(gid) {
  return ((_alSlate && _alSlate.games) || []).find(g => g.game_id === gid) || null;
}

function _alCanSim(g) {
  return !!(g && (g.props || {}).has_props);
}

function _alRowState(g) {
  if (_alGameResults[g.game_id]) return 'done';
  const prev = document.querySelector(`.rail-game[data-gid="${_alEsc(g.game_id)}"]`);
  if (prev && prev.dataset.state === 'simulating') return 'simulating';
  return _alCanSim(g) ? 'idle' : 'noprops';
}

function _alRowStateHtml(g, state) {
  if (state === 'simulating') {
    return `<span class="rail-state rail-state--sim"><i></i><span class="js-elapsed">0s</span></span>`;
  }
  if (state === 'done') {
    const res = _alGameResults[g.game_id];
    const rec = res && res.results && res.results.top;
    if (rec && (rec.win + rec.loss + rec.push) > 0) {
      const cls = rec.pnl > 0 ? 'won' : rec.pnl < 0 ? 'lost' : 'push';
      return `<span class="rail-state rail-state--rec rail-state--${cls}"
        title="Picks recomendados: ${rec.win} ganados, ${rec.loss} perdidos${rec.push ? `, ${rec.push} push` : ''} · ${rec.pnl > 0 ? '+' : ''}${rec.pnl}u">
        <b>${rec.win}-${rec.loss}</b></span>`;
    }
    const n = ((res && res.edges) || []).length;
    const best = _alBestEv(res);
    if (g.state === 'in') {
      return `<span class="rail-state rail-state--live" title="En juego: los resultados se actualizan solos"><i></i>Vivo</span>`;
    }
    return `<span class="rail-state rail-state--done" title="${n} pistachos · mejor EV ${best === null ? '—' : best.toFixed(0) + '%'}">
      <b>${n}</b><small>pist.</small></span>`;
  }
  if (state === 'error') return `<span class="rail-state rail-state--err">Error</span>`;
  if (state === 'loading') return `<span class="rail-state rail-state--sim"><i></i></span>`;
  if (state === 'noprops') {
    return `<span class="rail-state rail-state--off">${g.state === 'post' ? 'Final' : 'Sin cuotas'}</span>`;
  }
  return `<span class="rail-state rail-state--idle">Simular</span>`;
}

function _alRailRowHtml(g) {
  const state = _alRowState(g);
  const sel = _alScope === 'game' && _alSelected === g.game_id;
  const team = t => `
    <span class="rail-team">
      ${t && t.logo ? `<img src="${_alEsc(t.logo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">` : '<span class="rail-nologo"></span>'}
      <b>${_alEsc((t && t.tricode) || '—')}</b>
    </span>`;
  const away = '#' + String((g.away && g.away.color) || '333').replace('#', '');
  const home = '#' + String((g.home && g.home.color) || '333').replace('#', '');
  return `
    <button class="rail-game ${sel ? 'is-selected' : ''}" data-gid="${_alEsc(g.game_id)}" data-state="${state}"
            style="--away:${_alEsc(away)};--home:${_alEsc(home)}"
            ${state === 'noprops' ? 'disabled' : ''}
            onclick="simulateGame('${_alEsc(g.game_id)}')"
            aria-label="${_alEsc(`${(g.away || {}).tricode} en ${(g.home || {}).tricode}, ${_alTime(g.tip_off)}`)}">
      <span class="rail-teams">${team(g.away)}${team(g.home)}</span>
      <span class="rail-when">${_alEsc(_alTime(g.tip_off))}<small>${_alEsc(_alStatusLabel(g))}</small></span>
      <span class="js-rail-state">${_alRowStateHtml(g, state)}</span>
    </button>`;
}

function _alRows(gid) {
  return document.querySelectorAll(`.rail-game[data-gid="${_alEsc(gid)}"]`);
}

function _alMarkSelected() {
  document.querySelectorAll('.rail-game').forEach(r => {
    r.classList.toggle('is-selected', _alScope === 'game' && r.dataset.gid === _alSelected);
  });
  document.querySelectorAll('.js-rail-league').forEach(b => {
    b.classList.toggle('is-selected', _alScope === 'league');
  });
}

function renderSlate() {
  if (!_alSlate) return;
  const games = _alSlate.games || [];
  const rows = games.length
    ? games.map(_alRailRowHtml).join('')
    : `<div class="rail-empty">No hay partidos esa fecha.</div>`;
  document.querySelectorAll('.js-rail-list').forEach(el => { el.innerHTML = rows; });

  // Fecha: "MAR 20 OCT"
  const d = _alSlate.date ? new Date(`${_alSlate.date}T12:00:00`) : null;
  document.querySelectorAll('.js-rail-day').forEach(b => {
    if (!d || isNaN(d)) return;
    b.querySelector('.rail-dow').textContent = d.toLocaleDateString('es', { weekday: 'short' }).replace('.', '');
    b.querySelector('.rail-dnum').textContent = String(d.getDate());
    b.querySelector('.rail-mon').textContent = d.toLocaleDateString('es', { month: 'short' }).replace('.', '');
    b.title = _alSlate.auto_shifted
      ? `Pediste ${_alSlate.requested_date} y no había partidos: es el día más cercano. Tocá para volver a hoy.`
      : 'Volver a hoy';
  });
  document.querySelectorAll('.js-rail-meta').forEach(el => { el.textContent = _alRailMeta(); });

  _alSyncSourceBtns();
  _alSyncSoundBtn();
  _alSyncCredits();
  _alUpdateSimAllBtn();
  _alRenderHero();
}

function _alRailMeta() {
  if (!_alSlate) return '';
  const n = _alSlate.n_games || 0;
  const with_ = _alSlate.games_with_props || 0;
  const base = `${n} partido${n === 1 ? '' : 's'}`;
  return with_ < n ? `${base} · ${with_} con cuotas` : base;
}

// Créditos que quedan en The Odds API: el dato más fresco entre la pizarra y
// los partidos simulados (cada llamada paga devuelve el saldo en sus headers).
function _alQuota() {
  let best = null;
  const take = (q) => { if (q && q.remaining != null && (!best || q.ts > best.ts)) best = q; };
  take(_alSlate && _alSlate.quota);
  Object.values(_alGameResults).forEach(r => take(r.live && r.live.quota));
  return best ? best.remaining : null;
}

function _alSyncCredits() {
  const q = _alQuota();
  document.querySelectorAll('.js-credits').forEach(el => {
    const show = _alSource === 'live';
    el.hidden = !show;
    el.innerHTML = q != null ? `<b>${_alEsc(q)}</b> créditos` : `~${(_alSlate && _alSlate.credits_per_game) || 5} créd./partido`;
    el.title = 'Créditos de The Odds API (plan compartido con MLB)';
  });
}

function altLinesSource(src) {
  if (src === _alSource || _alSimAll || _alBusy) return;
  _alSource = src;
  try { localStorage.setItem('al-source', src); } catch (_) { }
  _alGameResults = {};
  _alLeague = null;
  _alSelected = null;
  _alScope = 'game';
  _altLinesData = null;
  _alFilters.touched = false;
  const det = document.getElementById('al-detail');
  if (det) det.innerHTML = '';
  _alSyncSourceBtns();
  loadSlate(_alDate);
}

function _alSyncSourceBtns() {
  document.querySelectorAll('.js-source button').forEach(b => {
    const on = b.dataset.src === _alSource;
    b.classList.toggle('is-on', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function _alUpdateSimAllBtn() {
  const withProps = ((_alSlate && _alSlate.games) || []).filter(_alCanSim);
  const done = withProps.filter(g => _alGameResults[g.game_id]).length;
  document.querySelectorAll('.js-simall').forEach(btn => {
    if (_alSimAll) {
      btn.textContent = `Detener · ${done}/${withProps.length}`;
      btn.classList.add('is-running');
    } else {
      btn.textContent = done === withProps.length && withProps.length
        ? `Todos simulados · ${done}`
        : `Simular todos · ${withProps.length}`;
      btn.classList.remove('is-running');
    }
    btn.disabled = !withProps.length;
  });
  const n = Object.keys(_alGameResults).length;
  document.querySelectorAll('.js-rail-league').forEach(b => {
    b.hidden = n < 1;
    b.innerHTML = `<span>Toda la liga</span><small>${n} simulado${n === 1 ? '' : 's'}</small>`;
  });
  _alMarkSelected();
}

// El pick que la cabecera dibuja al terminar: el primero del top del motor o,
// si el top quedó vacío, la mejor línea elegible.
function _alBestRow(res) {
  if (!res) return null;
  if (res.edges && res.edges.length) return res.edges[0];
  const rows = (res.all_edges || []).filter(r => r.level_ok !== false);
  return rows.length ? rows.reduce((a, b) => (Number(b.ev_pct) > Number(a.ev_pct) ? b : a)) : null;
}

function altLinesToggleSound() {
  if (!window.AlGalton) return;
  AlGalton.setSound(!AlGalton.soundOn());
  _alSyncSoundBtn();
}

function _alSyncSoundBtn() {
  if (!window.AlGalton) return;
  const on = AlGalton.soundOn();
  document.querySelectorAll('.js-sound').forEach(b => {
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.classList.toggle('is-on', on);
    b.title = on ? 'Sonido activado' : 'Sonido apagado';
  });
}

// ── Cabecera del partido: el tablero de Galton en grande ────────────────────

function _alHeroCanvas() {
  return document.querySelector('#pst-hero-host .pst-hero-canvas');
}

function _alRenderHero() {
  const host = document.getElementById('pst-hero-host');
  if (!host) return;
  const g = _alScope === 'game' && _alSelected ? _alGame(_alSelected) : null;
  const sim = g && [..._alRows(g.game_id)].some(r => r.dataset.state === 'simulating');
  const res = g ? _alGameResults[g.game_id] : null;
  const away = g ? String((g.away && g.away.color) || '2b3a22') : '1a2414';
  const home = g ? String((g.home && g.home.color) || '2b3a22') : '6e9b3f';

  let info;
  if (_alScope === 'league') {
    const n = Object.keys(_alGameResults).length;
    info = `
      <div class="pst-hero-kicker">Toda la liga</div>
      <div class="pst-hero-title">${n} partido${n === 1 ? '' : 's'} <span>simulado${n === 1 ? '' : 's'}</span></div>
      <div class="pst-hero-sub">Los mejores pistachos de la noche, juntos.</div>`;
  } else if (g) {
    const logo = t => t && t.logo ? `<img src="${_alEsc(t.logo)}" alt="" onerror="this.remove()">` : '';
    info = `
      <div class="pst-hero-kicker">${_alEsc(_alTime(g.tip_off))} · ${_alEsc(_alStatusLabel(g))}${g.venue ? ' · ' + _alEsc(g.venue) : ''}</div>
      <div class="pst-hero-title pst-hero-title--match">
        ${logo(g.away)}<span>${_alEsc(g.away.tricode)}</span><i>@</i><span>${_alEsc(g.home.tricode)}</span>${logo(g.home)}
      </div>
      <div class="pst-hero-sub js-hero-status">${sim ? 'Simulando 20.000 partidos por jugador… <b class="js-elapsed">0s</b>'
        : res ? _alHeroResultText(res)
          : _alCanSim(g) ? 'Tocá <b>Simular</b> para abrir los pistachos de este partido.' : 'Este partido no tiene cuotas para simular.'}</div>
      ${!sim && !res && _alCanSim(g) ? `<button class="pst-hero-cta" onclick="simulateGame('${_alEsc(g.game_id)}')">Simular partido</button>` : ''}
      ${!sim && res && !res.from_log ? `<button class="pst-hero-cta pst-hero-cta--ghost" onclick="resimulateGame('${_alEsc(g.game_id)}')">Re-simular</button>` : ''}`;
  } else {
    const n = (_alSlate && _alSlate.games_with_props) || 0;
    info = `
      <div class="pst-hero-kicker">${_alSlate && _alSlate.date
        ? _alEsc(new Date(`${_alSlate.date}T12:00:00`).toLocaleDateString('es', { weekday: 'long', day: 'numeric', month: 'long' }))
          + ' · ' + _alEsc(_alRailMeta())
        : 'NBA · Alt Lines'}</div>
      <div class="pst-hero-title">Elegí un <span>partido</span></div>
      <div class="pst-hero-sub">El libro cotiza las líneas extremas con una curva genérica igual para todos.
        Simulamos la distribución real de cada jugador y te mostramos dónde su cola le gana al precio.</div>
      ${n ? `<button class="pst-hero-cta" onclick="simulateAllGames()">Simular los ${n}</button>` : ''}`;
  }

  host.innerHTML = `
    <section class="pst-hero ${g ? 'pst-hero--game' : ''}" style="--away:#${_alEsc(away.replace('#', ''))};--home:#${_alEsc(home.replace('#', ''))}">
      <canvas class="pst-hero-canvas" aria-hidden="true" data-away="${_alEsc(away)}" data-home="${_alEsc(home)}"
              data-plot-left="${window.innerWidth > 768 ? '0.42' : '0'}"></canvas>
      <div class="pst-hero-info">${info}</div>
    </section>`;

  const cv = _alHeroCanvas();
  if (!cv || !window.AlGalton) return;
  if (sim) AlGalton.start(cv);
  else AlGalton.paint(cv, res ? _alBestRow(res) : null);
}

/**
 * Cambia el estado de un partido EN SITIO: sus filas del riel y, si es el
 * partido abierto, la cabecera (que es donde corre la animación).
 */
function _alSetCardState(gid, state, msg) {
  const g = _alGame(gid);
  _alRows(gid).forEach(r => {
    r.dataset.state = state;
    const slot = r.querySelector('.js-rail-state');
    if (slot && g) slot.innerHTML = _alRowStateHtml(g, state);
    if (state === 'error' && msg) r.title = msg;
  });
  if (!(_alScope === 'game' && _alSelected === gid)) return;
  const cv = _alHeroCanvas();
  const status = document.querySelector('#pst-hero-host .js-hero-status');
  if (state === 'simulating') {
    _alRenderHero();
  } else if (state === 'done') {
    if (cv && window.AlGalton) AlGalton.finish(cv, _alBestRow(_alGameResults[gid]));
    const res = _alGameResults[gid];
    if (status) status.innerHTML = _alHeroResultText(res);
    document.querySelector('#pst-hero-host .pst-hero-cta')?.remove();
  } else {
    if (cv && window.AlGalton) AlGalton.stop(cv);
    if (status) status.textContent = state === 'error' ? `No se pudo simular: ${msg || 'error'}` : '';
  }
}

// ── Simulación de un partido ────────────────────────────────────────────────

async function simulateGame(gid, opts = {}) {
  const done = _alGameResults[gid];
  if (done && !opts.refresh) {                  // ya simulado: sólo abrirlo
    _alSelectGame(gid);
    return done;
  }
  if ([..._alRows(gid)].some(r => r.dataset.state === 'simulating')) {
    if (!opts.silent) { _alSelected = gid; _alScope = 'game'; _alMarkSelected(); _alRenderHero(); }
    return null;
  }

  // Partido terminado con picks guardados: se lee del registro, al instante.
  // No hay nada que simular, así que tampoco hay animación.
  if (_alFromLog(_alGame(gid))) return _alOpenFromLog(gid, opts);

  // Abrir el partido apenas se toca: la cabecera muestra su simulación.
  if (!opts.silent) {
    _alSelected = gid;
    _alScope = 'game';
    _altLinesData = null;
    _alMarkSelected();
    renderAltLines();
  }
  const t0 = Date.now();
  _alSetCardState(gid, 'simulating');
  const tick = setInterval(() => {
    const txt = `${((Date.now() - t0) / 1000).toFixed(0)}s`;
    _alRows(gid).forEach(r => { const el = r.querySelector('.js-elapsed'); if (el) el.textContent = txt; });
    if (_alSelected === gid) {
      const el = document.querySelector('#pst-hero-host .js-elapsed');
      if (el) el.textContent = txt;
    }
  }, 250);

  try {
    const q = new URLSearchParams({ game: gid, n_iter: AL_N_ITER });
    if (_alDate) q.set('date', _alDate);
    if (opts.refresh) q.set('refresh', 'true');
    if (opts.refreshOdds) q.set('refresh_odds', 'true');
    q.set('source', _alSource);
    q.set('calibrated', _alCalibrated ? 'true' : 'false');
    const res = await fetch(`${API}/api/altlines/edges?${q}`);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) { }
      throw new Error(detail);
    }
    _alGameResults[gid] = await res.json();
    _alSimSave(gid);
    if (_alSource === 'live') _alAttachResults(gid);     // en segundo plano
    _alLeague = null;                           // el tablero global quedó viejo
    _alSetCardState(gid, 'done');
    if (!opts.silent) _alSelectGame(gid);
    return _alGameResults[gid];
  } catch (e) {
    console.error('AltLines game fetch failed', e);
    _alSetCardState(gid, 'error', e.message);
    if (!opts.silent) {
      const st = document.getElementById('al-status');
      if (st) {
        st.hidden = false;
        st.className = 'al-status al-status--error';
        st.innerHTML = `No se pudo simular <code>${_alEsc(gid)}</code>:
          <strong>${_alEsc(e.message)}</strong><br>${_alFetchHint()}`;
      }
    }
    return null;
  } finally {
    clearInterval(tick);
    _alUpdateSimAllBtn();
  }
}

async function simulateAllGames() {
  if (_alSimAll) { _alSimAll = false; _alUpdateSimAllBtn(); return; }
  const pending = (_alSlate && _alSlate.games || [])
    .filter(g => (g.props || {}).has_props && !_alGameResults[g.game_id]);
  if (!pending.length) {
    if (typeof _showToast === 'function') _showToast('Todos los partidos ya están simulados', 'ok');
    _alLeague = null;
    _alScope = 'league';
    renderAltLines();
    return;
  }
  if (_alSource === 'live') {
    const cost = pending.length * (_alSlate.credits_per_game || 5);
    const q = _alQuota();
    const msg = `Esto consulta las cuotas reales de ${pending.length} partido(s): hasta ~${cost} créditos de The Odds API`
      + (q != null ? ` (quedan ${q}).` : '.') + ' Los partidos consultados en los últimos 20 min no se vuelven a cobrar. ¿Seguir?';
    if (!window.confirm(msg)) return;
  }
  _alSimAll = true;
  _alUpdateSimAllBtn();
  for (const g of pending) {
    if (!_alSimAll) break;
    await simulateGame(g.game_id, { silent: true });
  }
  _alSimAll = false;
  _alUpdateSimAllBtn();
  // Al terminar, mostramos el tablero de toda la liga fusionado.
  _alLeague = null;
  _alScope = 'league';
  _alFilters.touched = false;
  renderAltLines();
  if (typeof _showToast === 'function') {
    _showToast(`Simulados ${Object.keys(_alGameResults).length} partidos`, 'ok');
  }
}

function _alSelectGame(gid) {
  _alSelected = gid;
  _alScope = 'game';
  _alFilters.touched = false;
  if (window.Pickstachio) Pickstachio.reset();
  _altLinesData = _alGameResults[gid] || null;
  _alDefaultFilters();
  _alMarkSelected();
  _alRenderHero();
  renderAltLines();
}

function altLinesScope(scope) {
  _alScope = scope;
  _alLeague = null;
  _alFilters.touched = false;
  if (scope === 'league') {
    _altLinesData = _alMergeGames();
  } else {
    // Si nunca se "abrió" un partido (p. ej. se usó Simular todos), abrimos el
    // primero que haya resultados: así "Partido" siempre muestra algo.
    if (!_alSelected || !_alGameResults[_alSelected]) {
      _alSelected = Object.keys(_alGameResults)[0] || null;
    }
    _altLinesData = _alSelected ? _alGameResults[_alSelected] : null;
  }
  _alDefaultFilters();
  _alMarkSelected();
  _alRenderHero();
  renderAltLines();
}


/**
 * Diagnóstico del fallo de red más común: la página abierta con file://
 * (doble clic) en vez de servida por HTTP. Ahí el hostname viene vacío.
 */
function _alFetchHint() {
  if (!window.location.hostname) {
    return `Estás abriendo <code>index.html</code> con doble clic (protocolo <code>file://</code>).
      Algunos navegadores bloquean los pedidos al backend desde ahí: serví la carpeta
      (<code>python -m http.server 5500</code> o el <code>start_cherrypick.bat</code>) y entrá por
      <code>http://localhost:5500/index.html</code>.`;
  }
  return `Verificá que el backend esté corriendo: <code>python -m uvicorn main:app --port 8002</code>
    ejecutado dentro de <code>backend\\</code>.`;
}

function _alApplyFilters(rows) {
  return rows.filter(r => {
    if (_alFilters.market !== 'all' && r.market !== _alFilters.market) return false;
    if (_alFilters.side !== 'all' && String(r.side).toLowerCase() !== _alFilters.side) return false;
    if (Number(r.ev_pct) < _alFilters.minEv) return false;
    if (_alFilters.search) {
      const hay = `${r.player} ${r.team} ${r.market_label}`.toLowerCase();
      if (!hay.includes(_alFilters.search.toLowerCase())) return false;
    }
    return true;
  });
}

function _alRowHtml(r) {
  const prof = String(r.profile || '').toLowerCase();
  const profClass = prof.includes('boom') ? 'al-badge--boom'
    : prof.includes('volatile') ? 'al-badge--volatile' : 'al-badge--steady';
  const sideClass = String(r.side).toUpperCase() === 'OVER' ? 'al-side--over' : 'al-side--under';
  const oc = r.outcome === 'win' ? 'al-row--won' : r.outcome === 'loss' ? 'al-row--lost' : '';
  return `
    <tr class="${oc}" ${r.outcome ? `title="${r.outcome === 'win' ? 'Ganada' : r.outcome === 'loss' ? 'Perdida' : r.outcome}${r.actual != null ? ` · terminó en ${r.actual}` : ''}"` : ''}>
      <td class="al-td-player">
        <div class="al-player">${_alEsc(r.player)}</div>
        <div class="al-team">${_alEsc(r.team || '')}</div>
      </td>
      <td>${_alEsc(r.market_label)}</td>
      <td><span class="al-side ${sideClass}">${_alEsc(r.side)}</span> <strong>${_alNum(r.point, 1)}</strong></td>
      <td class="al-td-num">${_alNum(r.decimal_odds)}</td>
      <td class="al-td-num al-em">${(r.p_model * 100).toFixed(1)}%</td>
      <td class="al-td-num">${(r.p_fair * 100).toFixed(1)}%</td>
      <td class="al-td-num al-em">${_alNum(r.discrepancy * 100, 1)}%</td>
      <td class="al-td-num ${Number(r.ev_pct) > 0 ? 'al-pos' : 'al-neg'}">${_alNum(r.ev_pct, 1)}%</td>
      <td class="al-td-num">${_alNum(r.curve_ratio, 2)}×</td>
      <td><span class="al-badge ${profClass}">${_alEsc(r.profile)}</span></td>
      <td class="al-td-book">${_alEsc(r.book)}</td>
      <td class="al-td-num">${_alNum(r.stake_units, 2)}u</td>
    </tr>`;
}

// ── Detalle: el tablero del partido elegido (o de la liga fusionada) ────────

function renderAltLines() {
  const host = document.getElementById('al-detail');
  if (!host) return;
  // `_altLinesData` es SIEMPRE el tablero que se está viendo: la tabla, los KPIs
  // y el filtro por defecto leen de ahí. Si no se reasigna también en el modo
  // liga, la tabla sigue mostrando el partido anterior y los números no cierran.
  if (_alScope === 'league') {
    _alLeague = _alLeague || _alMergeGames();
    _altLinesData = _alLeague;
  }
  const d = _altLinesData;
  if (!d) {
    const busy = _alSelected && [..._alRows(_alSelected)].some(r => r.dataset.state === 'simulating');
    host.innerHTML = busy ? `<div class="pst-wait">Abriendo pistachos…</div>` : '';
    return;
  }
  if (!_alFilters.touched) _alFilters.minEv = _alTopEv();
  if (_alView === 'easy' && window.Pickstachio) {
    host.innerHTML = _alDetailHeaderHtml(d) + Pickstachio.html(d, _alTeams());
    return;
  }
  host.innerHTML = _alDetailHeaderHtml(d) + _alBoardHtml(d);
  renderAltLinesTable();
}

// tricode → { color, logo } de los equipos de la pizarra (para los cromos).
function _alTeams() {
  const out = {};
  ((_alSlate && _alSlate.games) || []).forEach(g => {
    [g.home, g.away].forEach(t => { if (t && t.tricode) out[t.tricode] = { color: t.color, logo: t.logo }; });
  });
  return out;
}

function altLinesView(v) {
  if (v === _alView) return;
  _alView = v;
  try { localStorage.setItem('al-view', v); } catch (_) { }
  renderAltLines();
}

function _alViewTabs() {
  return `<div class="al-view" role="group" aria-label="Vista">
      <button class="al-view-btn ${_alView === 'easy' ? 'al-view-btn--on' : ''}" onclick="altLinesView('easy')"
              title="Panel de picks: qué apostar y por qué, en criollo">Easy</button>
      <button class="al-view-btn ${_alView === 'nerd' ? 'al-view-btn--on' : ''}" onclick="altLinesView('nerd')"
              title="Todas las líneas, probabilidades, curvas y perfiles">Nerd</button>
    </div>`;
}

function _alDetailHeaderHtml(d) {
  const c = d.counts || {};
  const src = d.source === 'demo' ? 'Demo' : d.source === 'live' ? 'Real' : 'CSV';
  return `
    <div class="al-detail-head">
      <div class="al-head-tabs">${_alViewTabs()}${_alModelTabs()}</div>
      <div class="al-meta">
        <span class="al-meta-src al-meta-src--${_alEsc(d.source || 'csv')}">${src}</span>
        <span class="al-meta-src">${d.params && d.params.calibrated ? 'Calibrado' : 'Crudo'}</span>
        ${c.player_markets ?? '—'} jugador-mercado · ${c.lines_priced ?? '—'} líneas ·
        ${_alNum(d.params && d.params.n_iter, 0)} iteraciones${d.elapsed_s != null ? ` · ${_alNum(d.elapsed_s, 1)}s` : ''}${d.cached ? ' · en caché' : ''}
        <button class="al-refresh" onclick="resimulateGame()" ${_alScope === 'game' && _alSelected ? '' : 'hidden'}
                title="Volver a correr el motor sobre este partido (no gasta créditos)">Re-simular</button>
        <button class="al-refresh" onclick="refreshAltLines()"
                title="${_alSource === 'live' ? 'Pedir cuotas nuevas (gasta créditos) y re-simular' : 'Releer el calendario y re-simular'}">Actualizar</button>
      </div>
    </div>`;
}

function _alBoardHtml(d) {
  const c = d.counts || {};
  const marketOpts = (d.markets || []).map(m =>
    `<option value="${_alEsc(m.key)}" ${_alFilters.market === m.key ? 'selected' : ''}>
      ${_alEsc(m.label)}</option>`).join('');

  return `
    <div class="al-kpis">
      <div class="al-kpi"><div class="al-kpi-label">Jugador-mercado</div><div class="al-kpi-value">${c.player_markets ?? '—'}</div></div>
      <div class="al-kpi"><div class="al-kpi-label">Líneas cotizadas</div><div class="al-kpi-value">${c.lines_priced ?? '—'}</div></div>
      <div class="al-kpi"><div class="al-kpi-label">Top ${_alNum(d.params && d.params.top_pct, 0)}% reportado</div><div class="al-kpi-value">${c.lines_reported ?? '—'}</div></div>
      <div class="al-kpi"><div class="al-kpi-label">Mejor EV</div><div class="al-kpi-value al-pos">${_alBestEv(d) !== null ? _alNum(_alBestEv(d), 0) + '%' : '—'}</div></div>
      <div class="al-kpi"><div class="al-kpi-label">De-vig</div><div class="al-kpi-value al-kpi-value--sm">${_alEsc((d.params && d.params.devig_method) || '—')}</div></div>
    </div>

    <div class="al-controls">
      <label class="al-ctl">Mercado
        <select id="al-f-market" onchange="altLinesFilter('market', this.value)">
          <option value="all">Todos</option>${marketOpts}
        </select>
      </label>
      <label class="al-ctl">Lado
        <select id="al-f-side" onchange="altLinesFilter('side', this.value)">
          <option value="all">Ambos</option>
          <option value="over">Over</option>
          <option value="under">Under</option>
        </select>
      </label>
      <label class="al-ctl">EV mínimo %
        <input id="al-f-ev" type="number" step="5" min="0" value="${_alFilters.minEv}"
               oninput="altLinesFilter('minEv', this.value)" />
      </label>
      <label class="al-ctl al-ctl--grow">Buscar jugador
        <input id="al-f-search" type="text" placeholder="p. ej. Costa"
               oninput="altLinesFilter('search', this.value)" />
      </label>
      <button class="al-quick" onclick="altLinesQuick('top')">Solo top 5%</button>
      <button class="al-quick" onclick="altLinesQuick('all')">Ver todo</button>
    </div>

    <div class="al-panel">
      <div class="al-panel-head">
        <div class="al-panel-title">Tablero de edges</div>
        <div class="al-panel-count" id="al-count"></div>
      </div>
      <div class="al-table-scroll">
        <table class="al-table">
          <thead>
            <tr>
              <th>Jugador</th><th>Mercado</th><th>Línea</th><th>Cuota</th>
              <th title="Probabilidad según nuestra distribución simulada">P modelo</th>
              <th title="Probabilidad implícita del libro, sin vig">P justa</th>
              <th title="P modelo − P justa: cuán mal está el precio">Discrepancia</th>
              <th title="EV por unidad apostada, neto de vig">EV</th>
              <th title="P modelo ÷ curva simétrica que usa el libro">Curva</th>
              <th>Perfil</th><th>Casa</th><th>Stake</th>
            </tr>
          </thead>
          <tbody id="al-tbody"></tbody>
        </table>
      </div>
      <div class="al-footnote">
        <strong>Cómo leerlo:</strong> <em>P modelo</em> sale de ${_alNum(d.params && d.params.n_iter, 0)}
        simulaciones por jugador-mercado;
        <em>P justa</em> es la probabilidad del libro una vez quitado el margen (vig).
        <em>Discrepancia</em> = la brecha que buscamos. <em>Curva</em> &gt; 1× significa que la curva
        simétrica del libro infravalora esa cola. Se descartan automáticamente las cuotas por encima de
        <strong>${_alNum((d.params && d.params.max_odds) || 50, 0)}</strong> (longshots impagables donde manda el error del modelo)
        y el stake es <strong>Kelly fraccionado</strong> (25% del Kelly completo, tope 3u).
      </div>
    </div>

    ${_alProfilesHtml(d.profiles)}`;
}

/**
 * Fusiona los tableros de todos los partidos ya simulados.
 *
 * Ojo con la semántica: el "top 5%" se calcula sobre las líneas con EV > 0 del
 * universo entero, igual que el backend en un run de liga completa. Como cada
 * partido simulado trae TODAS sus líneas en `all_edges`, la unión reproduce
 * exactamente el tablero global — sin pagar los ~15 s de un run de liga.
 */
function _alMergeGames() {
  const parts = Object.values(_alGameResults);
  if (!parts.length) return null;
  const all = [].concat(...parts.map(p => p.all_edges || []));
  all.sort((a, b) => b.ev_pct - a.ev_pct);
  // Los recomendados de la liga son la unión de los de cada partido: el motor
  // ya los eligió con sus filtros (probabilidad, cuota) y sin correlación
  // (ver ev.select_board). Recalcularlos acá desde all_edges los rompería.
  const top = [].concat(...parts.map(p => p.edges || [])).sort((a, b) => b.ev_pct - a.ev_pct);
  const first = parts[0];
  return {
    source: first.source,
    date: first.date,
    slate_source: first.slate_source,
    params: first.params,
    markets: first.markets,
    game: null,
    cached: false,
    elapsed_s: null,
    counts: {
      player_markets: parts.reduce((s, p) => s + (((p.counts || {}).player_markets) || 0), 0),
      games_of_history: parts.reduce((s, p) => s + (((p.counts || {}).games_of_history) || 0), 0),
      lines_priced: all.length,
      lines_reported: top.length,
      games: parts.length,
    },
    books: [...new Set(all.map(r => r.book))].sort(),
    profiles: [].concat(...parts.map(p => p.profiles || []))
      .sort((a, b) => b.mean_minus_median - a.mean_minus_median).slice(0, 12),
    edges: top,
    all_edges: all,
  };
}

function _alProfilesHtml(profiles) {
  if (!profiles || !profiles.length) return '';
  const rows = profiles.map(p => `
    <tr>
      <td>${_alEsc(p.player_name)}</td>
      <td>${_alEsc(p.market_label)}</td>
      <td class="al-td-num">${_alNum(p.sim_mean, 1)}</td>
      <td class="al-td-num">${_alNum(p.sim_median, 1)}</td>
      <td class="al-td-num al-em">+${_alNum(p.mean_minus_median, 2)}</td>
      <td class="al-td-num">${_alNum(p.sim_skew, 2)}</td>
      <td class="al-td-num">${_alNum(p.sim_cv, 2)}</td>
      <td>${_alEsc(p.profile)}</td>
    </tr>`).join('');
  return `
    <div class="al-panel">
      <div class="al-panel-head">
        <div class="al-panel-title">Perfiles de distribución</div>
        <div class="al-panel-count">media &gt; mediana ⇒ la curva del libro subestima la cola derecha</div>
      </div>
      <div class="al-table-scroll">
        <table class="al-table">
          <thead><tr>
            <th>Jugador</th><th>Mercado</th><th>Media</th><th>Mediana</th>
            <th>media−mediana</th><th>Sesgo</th><th>CV</th><th>Perfil</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
}

function renderAltLinesTable() {
  const d = _altLinesData;
  const tbody = document.getElementById('al-tbody');
  if (!d || !tbody) return;

  const rows = _alApplyFilters(d.all_edges || []).sort((a, b) => b.ev_pct - a.ev_pct);
  tbody.innerHTML = rows.length
    ? rows.slice(0, 200).map(_alRowHtml).join('')
    : `<tr><td colspan="12" class="loading-cell">Sin líneas que cumplan los filtros.</td></tr>`;

  const cnt = document.getElementById('al-count');
  if (cnt) cnt.textContent = `${rows.length} de ${(d.all_edges || []).length} líneas`;
}

function altLinesFilter(key, value) {
  _alFilters.touched = true;
  _alFilters[key] = (key === 'minEv') ? (Number(value) || 0) : value;
  renderAltLinesTable();
}

async function refreshAltLines() {
  // Refresh de verdad: se tira todo y se vuelve a pedir la pizarra (que a su
  // vez reevalúa el calendario) más el partido que estaba abierto.
  const keep = _alSelected;
  if (_alSource === 'live' && keep && !window.confirm(
      `Actualizar vuelve a pedir las cuotas reales de este partido (~${(_alSlate && _alSlate.credits_per_game) || 5} créditos). ¿Seguir?`)) {
    return;
  }
  _alGameResults = {};
  _alLeague = null;
  _altLinesData = null;
  _alFilters.touched = false;
  await loadSlate(_alDate, { refresh: true });
  if (keep && _alSlate && (_alSlate.games || []).some(g => g.game_id === keep)) {
    await simulateGame(keep, { refresh: true, refreshOdds: _alSource === 'live' });
  }
  if (typeof _showToast === 'function') _showToast('Pizarra y tablero actualizados', 'ok');
}

/**
 * Vuelve a correr el motor sobre el partido abierto. No pide cuotas nuevas
 * (eso es "Actualizar"), así que en modo Real no gasta créditos. El motor es
 * determinístico por (jugador, mercado): con los mismos datos y el mismo
 * modelo da lo mismo; lo que cambia el resultado es el modelo (calibrado o
 * crudo) o datos nuevos.
 */
// ── Memoria de lo simulado (navegador) ──────────────────────────────────────
// Una simulación se ve UNA vez: el resultado queda guardado en este
// navegador (24 h) y, al volver a entrar, el partido se abre al instante y
// sin animación. Sólo "Re-simular" (o cambiar de modelo) vuelve a correrla.
const AL_SIM_STORE = 'al-sims-v1';
const AL_SIM_TTL_MS = 24 * 3600 * 1000;
const AL_SIM_MAX = 16;

function _alSimKey(gid) {
  return `${_alSource}|${_alDate || ''}|${gid}|${_alCalibrated ? 'cal' : 'raw'}`;
}

function _alSimStoreRead() {
  try { return JSON.parse(localStorage.getItem(AL_SIM_STORE) || '{}') || {}; }
  catch (_) { return {}; }
}

function _alSimSave(gid) {
  const d = _alGameResults[gid];
  if (!d) return;
  const all = _alSimStoreRead();
  all[_alSimKey(gid)] = { ts: Date.now(), d };
  // Se quedan los más nuevos; si el navegador no tiene lugar, se tiran los
  // más viejos hasta que entre.
  let keys = Object.keys(all).sort((a, b) => all[b].ts - all[a].ts);
  keys.slice(AL_SIM_MAX).forEach(k => delete all[k]);
  keys = keys.slice(0, AL_SIM_MAX);
  while (keys.length) {
    try { localStorage.setItem(AL_SIM_STORE, JSON.stringify(all)); return; }
    catch (_) { delete all[keys.pop()]; }
  }
}

function _alSimLoad(gid) {
  const hit = _alSimStoreRead()[_alSimKey(gid)];
  if (!hit || Date.now() - hit.ts > AL_SIM_TTL_MS) return null;
  return hit.d;
}

function _alSimForget(gid) {
  const all = _alSimStoreRead();
  delete all[_alSimKey(gid)];
  try { localStorage.setItem(AL_SIM_STORE, JSON.stringify(all)); } catch (_) { }
}

// Al cargar la pizarra: lo que ya se simuló vuelve de la memoria.
function _alHydrateFromStore() {
  ((_alSlate && _alSlate.games) || []).forEach(g => {
    if (_alGameResults[g.game_id]) return;
    const d = _alSimLoad(g.game_id);
    if (d) _alGameResults[g.game_id] = d;
  });
}

// ── Resultados en vivo ──────────────────────────────────────────────────────
// Mientras un partido con picks se juega (o recién terminó y falta liquidar),
// se piden sus resultados cada 90 s, sin tocar nada.
const AL_RESULTS_EVERY_MS = 90 * 1000;
let _alResultsTimer = null;

function _alNeedsResults(g) {
  if (!g || _alSource !== 'live') return false;
  const d = _alGameResults[g.game_id];
  if (!d && !(g.logged > 0)) return false;
  const rec = d && d.results && d.results.top;
  const pending = !rec || rec.pending > 0 || (rec.win + rec.loss + rec.push + rec.void) === 0;
  const started = g.state === 'in' || g.state === 'post'
    || (g.tip_off && Date.now() > new Date(g.tip_off).getTime());
  return started && pending;
}

function _alWatchResults() {
  clearInterval(_alResultsTimer);
  _alResultsTimer = setInterval(() => {
    if (document.hidden || currentSport() !== 'nba') return;
    ((_alSlate && _alSlate.games) || []).filter(_alNeedsResults)
      .forEach(g => { if (_alGameResults[g.game_id]) _alAttachResults(g.game_id); });
  }, AL_RESULTS_EVERY_MS);
}

// ── Resultados: lo que pasó con los picks guardados ─────────────────────────

// Un partido se abre desde el registro (y no simulando) cuando es modo Real,
// tiene picks guardados y ya no hay cuotas que pedir.
function _alFromLog(g) {
  return !!(g && _alSource === 'live' && (g.logged || 0) > 0
    && (g.state === 'post' || !g.has_odds_event));
}

function _alResultKey(r) {
  return `${r.player_id}|${r.market}|${String(r.side).toUpperCase()}|${Number(r.point)}`;
}

// Pide al backend el resultado de los picks de un partido (liquida contra
// ESPN si ya terminó) y lo pega en las líneas que se están mostrando.
async function _alAttachResults(gid) {
  try {
    const r = await fetch(`${API}/api/altlines/results?game=${encodeURIComponent(gid)}`);
    if (!r.ok) return;
    const j = await r.json();
    const d = _alGameResults[gid];
    if (!d || !j || !j.results) return;
    d.results = j.summary;
    const fresh = [];
    const mark = (rows, top) => (rows || []).forEach(row => {
      const hit = j.results[_alResultKey(row)];
      if (!hit || !hit.outcome) return;
      if (top && row.outcome !== hit.outcome) fresh.push(_alResultKey(row));
      Object.assign(row, { outcome: hit.outcome, actual: hit.actual, pnl: hit.pnl });
    });
    mark(d.edges, true); mark(d.all_edges, false);
    _alSimSave(gid);
    if (!j.settled) return;
    if (fresh.length && window.Pickstachio) {
      Pickstachio.flash(fresh);
      const rec = j.summary && j.summary.top;
      const g = _alGame(gid);
      if (rec && g && typeof _showToast === 'function') {
        _showToast(`${g.away.tricode} @ ${g.home.tricode}: ${rec.win} ganados · ${rec.loss} perdidos · `
          + `${rec.pnl > 0 ? '+' : ''}${_alNum(rec.pnl, 2)}u`, rec.pnl >= 0 ? 'ok' : 'error');
      }
    }
    _alRows(gid).forEach(row => {
      const slot = row.querySelector('.js-rail-state');
      const g = _alGame(gid);
      if (slot && g) slot.innerHTML = _alRowStateHtml(g, 'done');
    });
    if (_alSelected === gid && _alScope === 'game') { _alRenderHero(); renderAltLines(); }
  } catch (_) { /* sin resultados: no es un error */ }
}

function _alHeroResultText(res) {
  const rec = res && res.results && res.results.top;
  const n = ((res && res.edges) || []).length;
  if (rec && (rec.win + rec.loss + rec.push) > 0) {
    const pnl = `${rec.pnl > 0 ? '+' : ''}${_alNum(rec.pnl, 2)}u`;
    return `Resultado de los recomendados: <b class="pst-res-w">${rec.win} ganados</b> · `
      + `<b class="pst-res-l">${rec.loss} perdidos</b>${rec.push ? ` · ${rec.push} push` : ''}`
      + `${rec.pending ? ` · ${rec.pending} pendientes` : ''} · <b class="${rec.pnl >= 0 ? 'pst-res-w' : 'pst-res-l'}">${pnl}</b>`;
  }
  if (res && res.from_log) return `${n} pistachos guardados · resultado pendiente de ESPN`;
  return `${n} pistachos abiertos · el mejor, dibujado contra la curva del libro`;
}

async function _alOpenFromLog(gid, opts = {}) {
  const g0 = _alGame(gid);
  if (!opts.silent) {
    _alSelected = gid;
    _alScope = 'game';
    _alMarkSelected();
  }
  _alSetCardState(gid, 'loading');
  try {
    const r = await fetch(`${API}/api/altlines/results?game=${encodeURIComponent(gid)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (!j.board) throw new Error('No hay picks guardados de este partido.');
    j.board.game = g0;
    _alGameResults[gid] = j.board;
    _alSimSave(gid);
    _alLeague = null;
    _alSetCardState(gid, 'done');
    if (!opts.silent) _alSelectGame(gid);
    return j.board;
  } catch (e) {
    _alSetCardState(gid, 'error', e.message);
    return null;
  } finally {
    _alUpdateSimAllBtn();
  }
}

function resimulateGame(gid = _alSelected) {
  if (!gid) return;
  delete _alGameResults[gid];
  _alSimForget(gid);
  _alLeague = null;
  return simulateGame(gid, { refresh: true });
}

function altLinesCalibration(on) {
  if (on === _alCalibrated || _alSimAll) return;
  _alCalibrated = on;
  try { localStorage.setItem('al-calibrated', on ? 'cal' : 'raw'); } catch (_) { }
  // Lo ya simulado se hizo con el otro modelo: se descarta y se re-simula el
  // partido abierto (el resto se recalcula cuando lo abras).
  const keep = _alScope === 'game' ? _alSelected : null;
  _alGameResults = {};
  _alLeague = null;
  _altLinesData = null;
  // Lo que ya se simuló con este modelo vuelve de la memoria del navegador.
  _alHydrateFromStore();
  renderSlate();
  if (keep && _alGameResults[keep]) _alSelectGame(keep);
  else if (keep) resimulateGame(keep);
  else renderAltLines();
}

function _alModelTabs() {
  return `<div class="al-view al-model" role="group" aria-label="Modelo">
      <button class="al-view-btn ${_alCalibrated ? 'al-view-btn--on' : ''}" onclick="altLinesCalibration(true)"
              title="Probabilidades corregidas con el backtest walk-forward de la temporada 2025-26">Calibrado</button>
      <button class="al-view-btn ${!_alCalibrated ? 'al-view-btn--on' : ''}" onclick="altLinesCalibration(false)"
              title="El motor sin corrección">Crudo</button>
    </div>`;
}

/* Estado inicial de las pestañas de deporte.
   Deliberadamente NO llama a showSection: la app ya abre en "Análisis Pitchers"
   por su cuenta y volver a invocarlo dispararía una segunda carga. Aquí solo
   marcamos el deporte activo y ocultamos la navegación que no corresponde. */
document.addEventListener('DOMContentLoaded', () => {
  if (!document.body.dataset.sport) document.body.dataset.sport = 'mlb';
  const sport = document.body.dataset.sport;
  document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
    const s = item.dataset.sport || 'mlb';
    item.style.display = (s === sport) ? '' : 'none';
  });
  document.querySelectorAll('.sport-tab').forEach(t => {
    const on = t.dataset.sport === sport;
    t.classList.toggle('sport-tab--active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
});
