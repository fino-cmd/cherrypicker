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

  const state = { market: 'all', live: false, teams: {}, cards: [] };

  // ── Íconos (trazo, heredan currentColor) ──────────────────────────────────
  const I = {
    nut: `<svg class="pst-mark" viewBox="0 0 48 48" aria-hidden="true"><g transform="rotate(28 24 26)"><path d="M24 25.5c-5-3.6-6.6-10.6-4.6-17 1.3-4.2 7.9-4.2 9.2 0 2 6.4.4 13.4-4.6 17z" fill="#9FCF5F"/><path d="M22 23.8c-3.4-3.7-4.4-9.6-2.6-14.9" stroke="#8C4E6C" stroke-width="2.4" stroke-linecap="round" fill="none"/><path d="M25.8 9.2c1.1 2.7 1.3 6.1.6 9.2" stroke="#D6F0A9" stroke-width="1.5" stroke-linecap="round" fill="none"/><path d="M24 45c-8.6 0-13.4-7.4-13.4-16.4 0-8.2 3.3-15.6 7.8-19.4 1 6.6 2.9 12.3 5.6 16.4z" fill="#EBD8AB"/><path d="M24 45c8.6 0 13.4-7.4 13.4-16.4 0-8.2-3.3-15.6-7.8-19.4-1 6.6-2.9 12.3-5.6 16.4z" fill="#D1B37A"/><path d="M18.4 9.2c1 6.6 2.9 12.3 5.6 16.4 2.7-4.1 4.6-9.8 5.6-16.4" stroke="#7B6038" stroke-width="1.1" stroke-linejoin="round" fill="none"/><path d="M24 25.6V45" stroke="#B4955A" stroke-width=".9"/><path d="M14.3 22.5c-.8 2.2-1.1 4.5-1.1 6.8 0 4.6 1.6 8.4 4.6 10.7" stroke="#F7EDD2" stroke-width="1.4" stroke-linecap="round" fill="none"/></g></svg>`,
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
    blend: 'La probabilidad del pick mezcla la simulación (40 %) con el precio justo del libro (60 %): elegir el mayor EV entre cientos de líneas favorece justo las que el motor sobreestimó, y mezclar con el mercado lo corrige. Los recomendados además exigen ≥ 15 % de probabilidad, cuota ≤ 10 y una sola línea por jugador y mercado (máx. 2 por jugador).',
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
    // El score y la categoría los calcula el motor (altlines/tiers.py) y se
    // guardan en el registro; acá sólo se recalculan si faltan.
    const score = Number.isFinite(Number(r.pst_score)) && r.pst_score !== null
      ? Number(r.pst_score)
      : Math.round(100 * (0.40 * parts.edge + 0.25 * parts.tail + 0.20 * parts.prob + 0.15 * parts.level));
    const tier = ['gold', 'open', 'shell'].includes(r.pst_tier) ? r.pst_tier
      : score >= 70 ? 'gold' : score >= 50 ? 'open' : 'shell';
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

  // ── El cubo: un cromo de colección ────────────────────────────────────────
  // Frente: el color sólido del equipo con trama de imprenta, el logo como
  // marca de agua, la foto recortada del jugador y la apuesta en una franja
  // negra. Dorso (se gira al tocarlo): por qué es un pistacho.
  const MKT_ABBR = { points: 'PTS', rebounds: 'REB', assists: 'AST', threes: '3PM', pra: 'PRA' };
  const SILHOUETTE = `<svg class="pst-ghost" viewBox="0 0 200 200" aria-hidden="true"><path d="M100 30c19 0 33 15 33 35s-14 38-33 38-33-18-33-38 14-35 33-35zM34 200c2-42 28-74 66-74s64 32 66 74z"/></svg>`;

  function photo(r) {
    const id = String(r.player_id || '');
    if (!id.startsWith('ESPN-')) return SILHOUETTE;
    return `<img class="pst-photo" alt="" loading="lazy" decoding="async"
      src="https://a.espncdn.com/i/headshots/nba/players/full/${esc(id.slice(5))}.png"
      onerror="this.replaceWith(Object.assign(document.createElement('span'),{innerHTML:Pickstachio.ghost}))">`;
  }

  // El frente del cromo (se usa en la grilla y, en grande, en la ficha).
  function front(r, m) {
    const t = TIERS[m.tier];
    const team = state.teams[String(r.team || '').toUpperCase()] || {};
    const name = String(r.player || '').trim().split(/\s+/);
    const first = name.length > 1 ? name.slice(0, -1).join(' ') : '';
    const last = name[name.length - 1] || '';
    const longLast = last.length > 7 ? ' pst-last--long' : '';
    return `
      <div class="pst-face pst-front">
        ${team.logo ? `<img class="pst-logo" src="${esc(team.logo)}" alt="" loading="lazy">` : ''}
        <div class="pst-photo-wrap">${photo(r)}</div>
        <header class="pst-name">
          <span class="pst-first">${esc(first)}</span>
          <span class="pst-last${longLast}">${esc(last)}</span>
          <span class="pst-team">${esc(r.team || '')} · ${esc(MKT_ABBR[r.market] || r.market)}</span>
        </header>
        <div class="pst-stamp" title="${esc(DEF.score)}">
          <b>${m.score}</b><small>${esc(t.name.split(' ').pop())}</small>
        </div>
        <footer class="pst-band">
          <div class="pst-line">
            <span class="pst-ou">${m.over ? 'OVER' : 'UNDER'}</span>
            <b>${esc(Number(r.point).toFixed(1))}</b>
            <span class="pst-mkt">${esc(MKT_ABBR[r.market] || r.market)}</span>
          </div>
          <div class="pst-price"><b>${american(r.decimal_odds)}</b><span>${esc(r.book || '')}</span></div>
        </footer>
        ${resultRibbon(r)}
        <span class="pst-sheen" aria-hidden="true"></span>
      </div>`;
  }

  const cap = (t) => t ? t[0].toUpperCase() + t.slice(1) : t;

  // La ficha que se abre al lado del cromo: por qué es un pistacho.
  function sheet(r, m) {
    const t = TIERS[m.tier];
    const engine = r.p_engine != null ? Number(r.p_engine) : null;
    const flags = alerts(r, m).map(a => `<li title="${esc(a.def)}">${a.icon}${esc(a.text)}</li>`).join('');
    const rows = m.bars.map(b => `
      <div class="pst-stat">
        <dt>${b.icon}${esc(b.label)}</dt><dd>${esc(b.value)}</dd>
        <span class="pst-stat-bar"><i style="width:${Math.round(b.fill * 100)}%"></i></span>
        <p>${esc(cap(b.def.split(': ').slice(1).join(': ') || b.def))}</p>
      </div>`).join('');
    return `
      <div class="pst-sheet-head">
        <div>
          <div class="pst-sheet-tier">${esc(t.name)}</div>
          <div class="pst-sheet-line">${esc(t.line)}</div>
        </div>
        <div class="pst-sheet-score" title="${esc(DEF.score)}"><b>${m.score}</b><small>/100</small></div>
      </div>
      <div class="pst-sheet-bet">
        <span>${m.over ? 'OVER' : 'UNDER'} <b>${esc(Number(r.point).toFixed(1))}</b> ${esc(MARKET_SHORT[r.market] || r.market_label || '')}</span>
        <span><b>${american(r.decimal_odds)}</b> en ${esc(r.book || '')} · ${esc(Number(r.decimal_odds).toFixed(2))}</span>
      </div>
      ${OUTCOME[r.outcome] ? `<div class="pst-sheet-result pst-sheet-result--${OUTCOME[r.outcome].cls}">
        <b>${OUTCOME[r.outcome].label}</b>
        <span>${r.actual != null ? `Terminó con ${esc(Number(r.actual))} ${esc(MARKET_SHORT[r.market] || '')} (línea ${esc(Number(r.point).toFixed(1))})` : 'El jugador no jugó: apuesta anulada'}</span>
        <span>${r.pnl != null ? `${Number(r.pnl) > 0 ? '+' : ''}${Number(r.pnl).toFixed(2)}u` : ''}</span>
      </div>` : ''}
      <p class="pst-sheet-why">${sentence(r, m)}</p>
      ${flags ? `<ul class="pst-flags">${flags}</ul>` : ''}
      <dl class="pst-stats">${rows}</dl>
      <div class="pst-sheet-foot">
        <span title="${esc(DEF.stake)}">Stake sugerido <b>${Number(r.stake_units || 0).toFixed(2)}u</b></span>
        <span>EV <b>+${Number(r.ev_pct).toFixed(0)}%</b></span>
        <span title="Pick = el motor mezclado con el mercado; Motor = la simulación sola; Libro = el precio justo sin margen">
          Pick <b>${(m.pm * 100).toFixed(1)}%</b>${engine != null && Math.abs(engine - m.pm) >= 0.001
          ? ` · Motor <b>${(engine * 100).toFixed(1)}%</b>` : ''} · Libro <b>${(m.pf * 100).toFixed(1)}%</b></span>
      </div>`;
  }

  // Resultado ya liquidado: verde si se ganó, rojo si se perdió.
  const OUTCOME = {
    win: { cls: 'won', label: 'Ganada' }, loss: { cls: 'lost', label: 'Perdida' },
    push: { cls: 'push', label: 'Push' }, void: { cls: 'void', label: 'Anulada' },
  };
  function resultRibbon(r) {
    const o = OUTCOME[r.outcome];
    if (!o) return '';
    const act = r.actual != null ? `${Number(r.actual)} ${MKT_ABBR[r.market] || ''}` : 'no jugó';
    return `<div class="pst-result pst-result--${o.cls}"><b>${o.label}</b><span>${esc(act)}</span></div>`;
  }

  function cube(r, i) {
    const m = metrics(r);
    const team = state.teams[String(r.team || '').toUpperCase()] || {};
    const color = '#' + String(team.color || '2b3a22').replace('#', '');
    state.cards[i] = { r, m, color };
    const label = `${r.player}: ${m.over ? 'over' : 'under'} ${r.point} ${MARKET_SHORT[r.market] || ''}, ${american(r.decimal_odds)}. Score ${m.score}.`;
    const oc = OUTCOME[r.outcome];
    return `
      <div class="pst-cube pst-cube--${m.tier}${oc ? ' pst-cube--' + oc.cls : ''}" style="--team:${esc(color)};--i:${i}" data-idx="${i}"
           role="button" tabindex="0" aria-haspopup="dialog" aria-label="${esc(label)} Abrir la ficha."
           onclick="Pickstachio.open(this)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();Pickstachio.open(this);}"
           onpointermove="Pickstachio.tilt(this, event)" onpointerleave="Pickstachio.tilt(this)">
        <div class="pst-cube-in">${front(r, m)}</div>
      </div>`;
  }

  // ── La ficha: el cromo viaja al centro y se abre su explicación ──────────
  const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let openState = null;

  function open(el) {
    if (openState) return;
    const data = state.cards[Number(el.dataset.idx)];
    if (!data) return;
    const { r, m, color } = data;
    const modal = document.createElement('div');
    modal.className = `pst-modal pst-cube--${m.tier}`;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', `Ficha de ${r.player}`);
    modal.style.setProperty('--team', color);
    modal.innerHTML = `
      <div class="pst-modal-bg" onclick="Pickstachio.close()"></div>
      <div class="pst-modal-body">
        <div class="pst-modal-card">${front(r, m)}</div>
        <aside class="pst-sheet">
          <button class="pst-sheet-close" onclick="Pickstachio.close()" aria-label="Cerrar la ficha">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8m0-8-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
          </button>
          ${sheet(r, m)}
        </aside>
      </div>`;
    document.body.appendChild(modal);
    document.body.classList.add('pst-modal-open');
    const card = modal.querySelector('.pst-modal-card');
    const bg = modal.querySelector('.pst-modal-bg');
    const side = modal.querySelector('.pst-sheet');
    openState = { el, modal };
    el.classList.add('is-lifted');
    el.style.transform = '';

    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    openState.onKey = onKey;
    modal.querySelector('.pst-sheet-close').focus({ preventScroll: true });

    if (REDUCED || !card.animate) return;
    const a = el.getBoundingClientRect(), b = card.getBoundingClientRect();
    const dx = (a.left + a.width / 2) - (b.left + b.width / 2);
    const dy = (a.top + a.height / 2) - (b.top + b.height / 2);
    const s = a.width / b.width;
    // El cromo se levanta de la mesa, viaja y se asienta con un leve giro.
    card.animate([
      { transform: `translate(${dx}px, ${dy}px) scale(${s})`, boxShadow: '0 0 0 rgba(0,0,0,0)' },
      { transform: `translate(${dx * 0.45}px, ${dy * 0.45 - 30}px) scale(${(s + 1) / 2 * 1.04}) rotate(-3deg)`, offset: 0.45 },
      { transform: 'translate(0, 0) scale(1) rotate(0deg)' },
    ], { duration: 560, easing: 'cubic-bezier(.2, .8, .2, 1)' });
    bg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, easing: 'ease-out' });
    side.animate([
      { opacity: 0, transform: 'translateX(-28px)', clipPath: 'inset(0 100% 0 0 round 14px)' },
      { opacity: 1, transform: 'none', clipPath: 'inset(0 0 0 0 round 14px)' },
    ], { duration: 460, delay: 260, easing: 'cubic-bezier(.2, .8, .2, 1)', fill: 'backwards' });
  }

  async function close() {
    const st = openState;
    if (!st || st.closing) return;
    st.closing = true;
    document.removeEventListener('keydown', st.onKey);
    const { el, modal } = st;
    const card = modal.querySelector('.pst-modal-card');
    const bg = modal.querySelector('.pst-modal-bg');
    const side = modal.querySelector('.pst-sheet');
    const wait = (anim, ms) => Promise.race([anim.finished.catch(() => {}), new Promise(res => setTimeout(res, ms))]);
    if (!REDUCED && card.animate && el.isConnected) {
      const a = el.getBoundingClientRect(), b = card.getBoundingClientRect();
      const dx = (a.left + a.width / 2) - (b.left + b.width / 2);
      const dy = (a.top + a.height / 2) - (b.top + b.height / 2);
      const s = a.width / b.width;
      side.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(-20px)' }],
                   { duration: 200, fill: 'forwards' });
      bg.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 380, delay: 120, fill: 'forwards' });
      await wait(card.animate([
        { transform: 'none' },
        { transform: `translate(${dx}px, ${dy}px) scale(${s})` },
      ], { duration: 420, delay: 90, easing: 'cubic-bezier(.5, 0, .2, 1)', fill: 'forwards' }), 900);
    }
    modal.remove();
    document.body.classList.remove('pst-modal-open');
    el.classList.remove('is-lifted');
    if (el.isConnected) el.focus({ preventScroll: true });
    openState = null;
  }

  // Inclinación 3D siguiendo el puntero, con el reflejo holográfico.
  const HOVER = window.matchMedia && window.matchMedia('(hover: hover)').matches;
  function tilt(el, e) {
    if (!HOVER || REDUCED || openState) return;
    const inner = el.querySelector('.pst-cube-in');
    if (!e) { inner.style.transform = ''; el.style.removeProperty('--mx'); return; }
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    inner.style.transform = `rotateX(${(0.5 - py) * 10}deg) rotateY(${(px - 0.5) * 12}deg)`;
    el.style.setProperty('--mx', `${Math.round(px * 100)}%`);
  }

  function glossary() {
    const rows = [
      [I.nut, 'Pistacho', 'Una línea alternativa donde el modelo le encuentra valor al precio del libro. El libro la ve cerrada; nosotros le vemos la grieta.'],
      [I.nut, 'Se abre', 'Que la apuesta pegue. "Se abre 1 de cada 4" = el modelo espera que pegue uno de cada cuatro partidos.'],
      [I.nut, 'Pickstachio Score', DEF.score],
      [I.edge, 'Probabilidad del pick', DEF.blend],
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

  /** `teams`: tricode → { color, logo } (lo arma app.js desde la pizarra). */
  function html(d, teams) {
    state.live = d.source === 'live';
    state.teams = teams || {};
    state.cards = [];
    const all = picksOf(d);
    if (!all.length) {
      return `<div class="pst-empty"><b>Sin pistachos en este partido.</b>
        El motor no encontró líneas con valor. Probá otro partido o la vista Nerd para ver todo.</div>`;
    }
    const markets = [...new Set(all.map(x => x.r.market))];
    const shown = state.market === 'all' ? all : all.filter(x => x.r.market === state.market);
    const count = t => all.filter(x => x.m.tier === t).length;
    const tab = (k, label) => `<button class="pst-tab ${state.market === k ? 'pst-tab--on' : ''}"
        onclick="Pickstachio.filter('${esc(k)}')">${esc(label)}</button>`;
    return `
      <div class="pst-bar-top-row">
        <nav class="pst-tabs" aria-label="Mercado">${tab('all', 'Todos')}${markets.map(k => tab(k, MARKET_SHORT[k] || k)).join('')}</nav>
        <p class="pst-tally"><b>${count('gold')}</b> dorados <i>/</i> <b>${count('open')}</b> abiertos <i>/</i> <b>${count('shell')}</b> cáscaras
          <span>· tocá un cromo para abrir su ficha</span></p>
      </div>
      <div class="pst-grid">
        ${shown.length ? shown.map((x, i) => cube(x.r, i)).join('')
          : '<div class="pst-empty">Sin pistachos en ese mercado.</div>'}
      </div>
      ${glossary()}`;
  }

  window.Pickstachio = {
    html,
    filter(k) { state.market = k; if (typeof renderAltLines === 'function') renderAltLines(); },
    reset() { state.market = 'all'; },
    open,
    close,
    tilt,
    ghost: SILHOUETTE,
    icon: I.nut,
  };
})();
