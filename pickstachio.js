/* ════════════════════════════════════════════════════════════════════════════
   PICKSTACHIO — vista "Easy" del tablero NBA.

   Cada línea con valor es un "Pistacho". En vez de una tabla de números, un
   panel de picks: quién, qué apostar, a cuánto, una frase en criollo de por
   qué, alertas con íconos y cuatro métricas con su definición.

   Todo sale de las filas que ya devuelve el motor (all_edges / edges); acá no
   se calcula ninguna probabilidad nueva, sólo se traduce.
   ════════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, Number.isFinite(v) ? v : 0));

  const state = { market: 'all', live: false };

  // ── Íconos (trazo, heredan currentColor) ──────────────────────────────────
  const I = {
    nut: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.4c4.3 0 7.1 4.4 7.1 9.8 0 5.4-3 9.4-7.1 9.4s-7.1-4-7.1-9.4C4.9 6.8 7.7 2.4 12 2.4z" fill="#d9c7a0"/><path d="M12 4.6c2.8 1.7 3.9 4.6 3.9 7.7s-1.3 6.2-3.9 7.5c-2.6-1.3-3.9-4.4-3.9-7.5S9.2 6.3 12 4.6z" fill="#a6d86f"/><path d="M12 5.4v13.8" stroke="#5f8a37" stroke-width="1.1" stroke-linecap="round"/></svg>`,
    edge: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 15l4.5-4.5 3 3L17 7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M12.5 7H17v4.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    tail: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5c.6 2.6 3.8 4.2 3.8 8a3.8 3.8 0 01-7.6 0c0-1.7.9-2.9 1.8-3.8.2 1.4.9 2.2 1.8 2.6-.4-2.4.2-4.6.2-6.8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
    target: `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="10" r="3.6" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="10" r="1" fill="currentColor"/></svg>`,
    bolt: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11.2 2L4.5 11.2h4.6L8.4 18l7.1-9.6h-4.7z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
    warn: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3l7.5 13h-15z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 8v3.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="10" cy="13.9" r=".9" fill="currentColor"/></svg>`,
    check: `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l6 2.3v4.6c0 3.7-2.6 6.4-6 8.1-3.4-1.7-6-4.4-6-8.1V4.8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M7.2 10l2 2 3.6-3.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    info: `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10 9v4.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="10" cy="6.4" r=".95" fill="currentColor"/></svg>`,
  };

  // ── Definiciones (las mismas que muestra el glosario y el ⓘ) ──────────────
  const DEF = {
    score: 'Pickstachio Score (0–100): resume el pick. Pesa la ventaja sobre el libro (40 %), cuánto le gana la cola del jugador a la curva genérica del libro (25 %), qué tan seguido pega (20 %) y si el modelo y el mercado coinciden en el nivel del jugador (15 %).',
    edge: 'Ventaja: cuántos puntos de probabilidad más le da el modelo al hito que el libro (ya sin su comisión). Es el corazón del pick: sin ventaja no hay pistacho.',
    tail: 'Cola gorda: cuántas veces más probable es el hito con la distribución REAL del jugador que con la curva simétrica que el libro usa para todos. Más de 1× = el libro le achica la cola.',
    reach: 'Hito: qué tan lejos está la línea alternativa de la línea principal, medido en desvíos del propio jugador. Cuanto más lejos, más pesa la forma de su cola (y más paga).',
    boom: 'Explosividad: el sesgo a la derecha del jugador. Alta = tiene noches muy por encima de lo normal con más frecuencia de lo que dice una curva simétrica.',
    level: 'Nivel alineado: el modelo y el mercado coinciden en la mediana del jugador. Si no coincidieran, la ventaja vendría de discrepar en su nivel (lesiones, rol) y no de la forma de la cola.',
    stake: 'Stake: Kelly fraccionado (25 % del Kelly completo, tope 3 unidades). En líneas extremas es chico a propósito.',
    longshot: 'Longshot: cuota de 15 o más. Pega pocas veces; el valor se ve a la larga, no en una noche.',
  };

  const MARKET = {
    points: ['punto', 'puntos'], rebounds: ['rebote', 'rebotes'], assists: ['asistencia', 'asistencias'],
    threes: ['triple', 'triples'], pra: ['PRA', 'PRA'],
  };
  const MARKET_SHORT = { points: 'Puntos', rebounds: 'Rebotes', assists: 'Asistencias', threes: 'Triples', pra: 'Pts+Reb+Ast' };

  const american = (dec) => {
    const d = Number(dec);
    if (!(d > 1)) return '—';
    return d >= 2 ? `+${Math.round((d - 1) * 100)}` : `−${Math.round(100 / (d - 1))}`;
  };
  const oneIn = (p) => (p > 0 ? Math.max(1, Math.round(1 / p)) : null);

  // ── Métricas de un pick ───────────────────────────────────────────────────
  function metrics(r) {
    const over = String(r.side).toUpperCase() === 'OVER';
    const pm = Number(r.p_model), pf = Number(r.p_fair);
    const disc = pm - pf;
    const ratio = Number(r.curve_ratio);
    const sd = Number(r.sim_cv) * Number(r.sim_mean);
    const dist = sd > 0 ? (over ? 1 : -1) * (Number(r.point) - Number(r.main_line)) / sd : 0;
    const skew = Number(r.sim_skew) || 0;
    // Sólo el modo real ancla la mediana al mercado; en demo no hay nivel que alinear.
    const hasLevel = state.live && r.anchor_scale !== undefined && r.anchor_scale !== null;
    const level = hasLevel ? clamp(1 - Math.abs(Number(r.anchor_scale) - 1) / 0.25) : null;

    const parts = {
      edge: clamp(disc / 0.10),
      tail: clamp((ratio - 1) / 3),
      prob: clamp(pm / 0.35),
      level: level === null ? 0.8 : level,
    };
    const score = Math.round(100 * (0.40 * parts.edge + 0.25 * parts.tail + 0.20 * parts.prob + 0.15 * parts.level));
    const tier = score >= 70 ? 'gold' : score >= 50 ? 'open' : 'shell';
    return {
      over, pm, pf, disc, ratio, dist, skew, score, tier, level, hasLevel,
      bars: [
        { key: 'edge', icon: I.edge, label: 'Ventaja', value: `+${(disc * 100).toFixed(1)} pts`, fill: parts.edge, def: DEF.edge },
        { key: 'tail', icon: I.tail, label: 'Cola gorda', value: Number.isFinite(ratio) ? `${ratio.toFixed(1)}×` : '—', fill: parts.tail, def: DEF.tail },
        { key: 'reach', icon: I.target, label: 'Hito', value: `${dist >= 0 ? '+' : ''}${dist.toFixed(1)}σ`, fill: clamp(Math.abs(dist) / 3), def: DEF.reach },
        { key: 'boom', icon: I.bolt, label: 'Explosividad', value: skew.toFixed(2), fill: clamp(skew / 1.6), def: DEF.boom },
      ],
    };
  }

  // El lema de la casa: el valor está adentro de la cáscara; el libro la ve
  // cerrada, nosotros le vemos la grieta.
  const TIERS = {
    gold: { name: 'Pistacho Dorado', line: 'Se abre solo: la grieta en el precio se ve a simple vista.',
            coda: 'Grieta grande: de los que se pelan con los dedos.' },
    open: { name: 'Pistacho Abierto', line: 'La cáscara ya está partida: hay fruto en el precio.',
            coda: 'Grieta clara: un poco de uña y sale.' },
    shell: { name: 'Cáscara Dura', line: 'Hay fruto adentro, pero cuesta romperla.',
             coda: 'Cáscara dura: se abre pocas veces, pero cuando se abre rinde.' },
  };

  function alerts(r, m) {
    const out = [];
    if (m.ratio >= 2) out.push({ icon: I.tail, text: 'Cola gorda', tone: 'hot', def: DEF.tail });
    if (String(r.profile || '').toUpperCase().includes('BOOM')) out.push({ icon: I.bolt, text: 'Boom-or-bust', tone: 'hot', def: DEF.boom });
    if (Math.abs(m.dist) >= 1.5) out.push({ icon: I.target, text: 'Hito lejano', tone: 'cool', def: DEF.reach });
    if (m.hasLevel && r.level_ok !== false && m.level >= 0.6) out.push({ icon: I.check, text: 'Nivel alineado', tone: 'ok', def: DEF.level });
    if (Number(r.decimal_odds) >= 15) out.push({ icon: I.warn, text: 'Longshot', tone: 'warn', def: DEF.longshot });
    return out;
  }

  // "30+ puntos" / "8 o menos rebotes"
  function milestone(r, m) {
    const [one, many] = MARKET[r.market] || ['', String(r.market_label || '')];
    const p = Number(r.point);
    if (m.over) { const n = Math.floor(p) + 1; return `${n}+ ${n === 1 ? one : many}`; }
    const n = Math.ceil(p) - 1; return `${n} o menos ${n === 1 ? one : many}`;
  }

  // La descripción, en idioma pistacho: cuántas veces se "abre" (pega) según
  // nuestro modelo vs. cuántas cree el libro.
  function sentence(r, m) {
    const a = oneIn(m.pm), b = oneIn(m.pf);
    const first = String(r.player || '').split(' ')[0];
    if (!a || !b) return '';
    return `Este pistacho se abre <strong>1 de cada ${a}</strong> partidos: así de seguido vemos a `
      + `<strong>${esc(first)}</strong> llegar a <strong>${esc(milestone(r, m))}</strong>. `
      + `El libro lo ve cerrado y lo paga como si se abriera <strong>1 de cada ${b}</strong>. `
      + `<span class="pst-coda">${esc(TIERS[m.tier].coda)}</span>`;
  }

  function avatar(r) {
    const id = String(r.player_id || '');
    const initials = String(r.player || '?').split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
    const img = id.startsWith('ESPN-')
      ? `<img src="https://a.espncdn.com/i/headshots/nba/players/full/${esc(id.slice(5))}.png" alt=""
             loading="lazy" onerror="this.remove()">`
      : '';
    return `<div class="pst-avatar"><span>${esc(initials)}</span>${img}</div>`;
  }

  function ring(score) {
    const C = 2 * Math.PI * 22, off = C * (1 - score / 100);
    return `<div class="pst-ring" title="${esc(DEF.score)}">
      <svg viewBox="0 0 52 52" aria-hidden="true">
        <circle cx="26" cy="26" r="22" class="pst-ring-bg"/>
        <circle cx="26" cy="26" r="22" class="pst-ring-fg" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/>
      </svg>
      <div class="pst-ring-n">${score}</div>
    </div>`;
  }

  // Tarjeta compacta: lo esencial a la vista (quién, qué, a cuánto, score) y
  // el resto adentro de la cáscara, que se abre al tocarla.
  function card(r) {
    const m = metrics(r);
    const t = TIERS[m.tier];
    const chips = alerts(r, m).map(a =>
      `<span class="pst-chip pst-chip--${a.tone}" title="${esc(a.def)}">${a.icon}${esc(a.text)}</span>`).join('');
    const bars = m.bars.map(b => `
      <div class="pst-bar" title="${esc(b.def)}">
        <div class="pst-bar-top"><span class="pst-bar-label">${b.icon}${esc(b.label)}</span><span class="pst-bar-val">${esc(b.value)}</span></div>
        <div class="pst-bar-track"><div class="pst-bar-fill" style="width:${Math.round(b.fill * 100)}%"></div></div>
      </div>`).join('');
    return `
      <details class="pst-card pst-card--${m.tier}">
        <summary class="pst-sum">
          ${avatar(r)}
          <div class="pst-sum-main">
            <div class="pst-sum-top">
              <span class="pst-player">${esc(r.player)}</span>
              <span class="pst-tier-dot" title="${esc(t.name)}">${I.nut}</span>
            </div>
            <div class="pst-sum-bet">
              <span class="pst-side pst-side--${m.over ? 'over' : 'under'}">${m.over ? 'O' : 'U'}</span>
              <strong>${esc(Number(r.point).toFixed(1))}</strong>
              <span class="pst-sum-mkt">${esc(MARKET_SHORT[r.market] || r.market_label)}</span>
              <span class="pst-sum-odds">${american(r.decimal_odds)}</span>
            </div>
          </div>
          ${ring(m.score)}
          <span class="pst-chev" aria-hidden="true"></span>
        </summary>
        <div class="pst-body">
          <div class="pst-tier">
            <div><div class="pst-tier-name">${t.name}</div><div class="pst-tier-line">${t.line}</div></div>
          </div>
          <p class="pst-why">${sentence(r, m)}</p>
          ${chips ? `<div class="pst-chips">${chips}</div>` : ''}
          <div class="pst-bars">${bars}</div>
          <footer class="pst-foot">
            <span>${esc(r.team || '')} · ${esc(Number(r.decimal_odds).toFixed(2))} en ${esc(r.book || '')}</span>
            <span title="${esc(DEF.stake)}">Stake <strong>${Number(r.stake_units || 0).toFixed(2)}u</strong></span>
            <span>EV <strong class="pst-pos">+${Number(r.ev_pct).toFixed(0)}%</strong></span>
          </footer>
        </div>
      </details>`;
  }

  function glossary() {
    const rows = [
      [I.nut, 'Pistacho', 'Una línea alternativa donde el modelo le encuentra valor al precio del libro. El libro la ve cerrada; nosotros le vemos la grieta.'],
      [I.nut, 'Se abre', 'Que la apuesta pegue. "Se abre 1 de cada 4" = el modelo espera que pegue uno de cada cuatro partidos.'],
      [I.nut, 'Pickstachio Score', DEF.score],
      [I.edge, 'Ventaja', DEF.edge],
      [I.tail, 'Cola gorda', DEF.tail],
      [I.target, 'Hito', DEF.reach],
      [I.bolt, 'Explosividad', DEF.boom],
      [I.check, 'Nivel alineado', DEF.level],
      [I.warn, 'Longshot', DEF.longshot],
    ];
    return `<details class="pst-gloss">
      <summary>${I.info}¿Qué significa cada cosa?</summary>
      <dl>${rows.map(([ic, k, v]) => `<div><dt>${ic}${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
      <p class="pst-gloss-note">Pistacho Dorado ≥ 70 · Pistacho Abierto 50–69 · Cáscara Dura &lt; 50.
        Los porcentajes son estimaciones de un modelo, no garantías: apostá sólo lo que estés dispuesto a perder.</p>
    </details>`;
  }

  // ── El panel ──────────────────────────────────────────────────────────────

  function picksOf(d) {
    let rows = (d.edges && d.edges.length) ? d.edges.slice()
      : (d.all_edges || []).filter(r => r.level_ok !== false && Number(r.ev_pct) > 0)
        .sort((a, b) => b.ev_pct - a.ev_pct).slice(0, 12);
    rows = rows.map(r => ({ r, m: metrics(r) })).sort((a, b) => b.m.score - a.m.score);
    return rows;
  }

  function html(d) {
    state.live = d.source === 'live';
    const all = picksOf(d);
    if (!all.length) {
      return `<div class="pst-empty">${I.nut}<div><strong>Sin pistachos en este partido.</strong>
        <span>El motor no encontró líneas con valor. Probá otro partido o la vista Nerd para ver todo.</span></div></div>`;
    }
    const markets = [...new Set(all.map(x => x.r.market))];
    const shown = state.market === 'all' ? all : all.filter(x => x.r.market === state.market);
    const count = t => all.filter(x => x.m.tier === t).length;
    const chip = (k, label) => `<button class="pst-filter ${state.market === k ? 'pst-filter--on' : ''}"
        onclick="Pickstachio.filter('${esc(k)}')">${esc(label)}</button>`;
    return `
      <div class="pst-summary">
        <div class="pst-summary-counts">
          <span class="pst-count pst-count--gold">${I.nut}${count('gold')} dorados</span>
          <span class="pst-count pst-count--open">${count('open')} abiertos</span>
          <span class="pst-count pst-count--shell">${count('shell')} cáscaras</span>
        </div>
        <div class="pst-filters">${chip('all', 'Todos')}${markets.map(k => chip(k, MARKET_SHORT[k] || k)).join('')}</div>
      </div>
      <div class="pst-grid">
        ${shown.length ? shown.map(x => card(x.r)).join('')
          : '<div class="pst-empty-sm">Sin pistachos en ese mercado.</div>'}
      </div>
      ${glossary()}`;
  }

  window.Pickstachio = {
    html,
    filter(k) { state.market = k; if (typeof renderAltLines === 'function') renderAltLines(); },
    reset() { state.market = 'all'; },
    icon: I.nut,
  };
})();
