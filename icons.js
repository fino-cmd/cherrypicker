/* Íconos de la app — un solo set, estilo Apple (SF Symbols, peso regular).
   Todos en grilla de 24, trazo 1.75, puntas y uniones redondeadas, color
   heredado (currentColor). Los "fill" son la variante llena para estados
   activos (estrella marcada, play, resultado).
   Uso: ICON('star')  ·  ICON('star', 16)  ·  ICON('star', 16, 'mi-clase')
   Sin tamaño, el ícono mide 1em (o lo que diga el CSS del contenedor).
   Marcas y logos (cereza, pistacho, escudos, "Remates") NO van acá. */
(function (root) {
  const F = 'fill="currentColor" stroke="none"';
  const P = {
    // ── navegación y acciones ──
    arrowRight: '<path d="M4.5 12h15M13 5.5l6.5 6.5-6.5 6.5"/>',
    arrowUp: '<path d="M12 19.5v-15M5.5 11 12 4.5 18.5 11"/>',
    arrowDown: '<path d="M12 4.5v15M5.5 13l6.5 6.5 6.5-6.5"/>',
    arrowUpRight: '<path d="M6.5 17.5l11-11M8.5 6.5h9v9"/>',
    chevronLeft: '<path d="M15 5.5 8.5 12l6.5 6.5"/>',
    chevronRight: '<path d="M9 5.5l6.5 6.5L9 18.5"/>',
    chevronDown: '<path d="M5.5 9 12 15.5 18.5 9"/>',
    xmark: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    checkmark: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    minus: '<path d="M6 12h12"/>',
    plus: '<path d="M12 5.5v13M5.5 12h13"/>',
    dot: `<circle cx="12" cy="12" r="3" ${F}/>`,
    ellipsis: `<circle cx="5.5" cy="12" r="1.7" ${F}/><circle cx="12" cy="12" r="1.7" ${F}/><circle cx="18.5" cy="12" r="1.7" ${F}/>`,
    refresh: '<path d="M20 12a8 8 0 1 1-2.35-5.65L20 8.5"/><path d="M20 3.5v5h-5"/>',
    undo: '<path d="M9 14.5 4.5 10 9 5.5"/><path d="M4.5 10h10a5 5 0 0 1 0 10H11"/>',
    pencil: '<path d="M14.5 5.5l4 4L8 20H4v-4z"/><path d="M12.5 7.5l4 4"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.3 15.3 20 20"/>',
    bell: '<path d="M6 10a6 6 0 0 1 12 0c0 5.5 2.2 7.5 2.2 7.5H3.8S6 15.5 6 10z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    play: `<path d="M8 5.3v13.4a1 1 0 0 0 1.5.86l10.8-6.7a1 1 0 0 0 0-1.72L9.5 4.44A1 1 0 0 0 8 5.3z" ${F}/>`,
    swap: '<path d="M4 8h15.5M16 4.5 19.5 8 16 11.5M20 16H4.5M8 12.5 4.5 16 8 19.5"/>',
    speaker: `<path d="M3.5 9.5h3.2L11.5 5.5v13l-4.8-4H3.5z" ${F}/><path class="nba-sound-on" d="M15 9a4.2 4.2 0 0 1 0 6M17.8 6.3a8 8 0 0 1 0 11.4"/><path class="nba-sound-off" d="m15.5 9.5 5 5m0-5-5 5"/>`,

    // ── estado / información ──
    info: `<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r="1.05" ${F}/>`,
    warn: `<path d="M10.3 4.3a2 2 0 0 1 3.4 0l7.5 13a2 2 0 0 1-1.7 3H4.5a2 2 0 0 1-1.7-3z"/><path d="M12 9.5v4"/><circle cx="12" cy="16.7" r="1" ${F}/>`,
    checkCircle: `<circle cx="12" cy="12" r="9.5" ${F}/><path d="m8 12.3 2.8 2.8 5.3-5.6" stroke="#fff" stroke-width="2"/>`,
    xCircle: `<circle cx="12" cy="12" r="9.5" ${F}/><path d="m9 9 6 6m0-6-6 6" stroke="#fff" stroke-width="2"/>`,
    minusCircle: `<circle cx="12" cy="12" r="9.5" ${F}/><path d="M8 12h8" stroke="#fff" stroke-width="2"/>`,
    shieldCheck: '<path d="M12 3l7 2.7v5.6c0 4.3-2.9 7.6-7 9.7-4.1-2.1-7-5.4-7-9.7V5.7z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.3 2"/>',
    star: '<path d="M12 3.6l2.6 5.3 5.8.85-4.2 4.1 1 5.8L12 16.9l-5.2 2.75 1-5.8-4.2-4.1 5.8-.85z"/>',
    starFill: `<path d="M12 3.6l2.6 5.3 5.8.85-4.2 4.1 1 5.8L12 16.9l-5.2 2.75 1-5.8-4.2-4.1 5.8-.85z" fill="currentColor"/>`,
    sparkles: `<path d="M10 3.5l1.7 4.8 4.8 1.7-4.8 1.7L10 16.5l-1.7-4.8L3.5 10l4.8-1.7z" fill="currentColor"/><path d="M18 14.5l.7 2 2 .8-2 .7-.7 2-.8-2-2-.7 2-.8z" fill="currentColor" stroke-width="1.2"/>`,
    triUp: '<path d="M12 7 18.5 17h-13z" fill="currentColor"/>',
    triDown: '<path d="M12 17 5.5 7h13z" fill="currentColor"/>',

    // ── datos y apuestas ──
    chartLine: '<path d="M4 4v16h16"/><path d="m7.5 15 3.5-4 3 2.5 5.5-6.5"/>',
    chartBar: `<rect x="4" y="13" width="4" height="7" rx="1.3" ${F}/><rect x="10" y="8.5" width="4" height="11.5" rx="1.3" ${F}/><rect x="16" y="4" width="4" height="16" rx="1.3" ${F}/>`,
    trendUp: '<path d="M3.5 17.5 9 12l3.5 3.5L20 8"/><path d="M14.5 8H20v5.5"/>',
    list: '<rect x="3.5" y="4" width="17" height="16" rx="3"/><path d="M8 9h8M8 12.5h8M8 16h5"/>',
    coin: '<circle cx="12" cy="12" r="9"/><path d="M14.7 9.4c-.5-.9-1.5-1.5-2.7-1.5-1.6 0-2.7.8-2.7 2 0 2.8 5.6 1.5 5.6 4.3 0 1.2-1.2 2-2.9 2-1.3 0-2.4-.6-2.9-1.6M12 6.3v1.6M12 16.2v1.5"/>',
    ticket: '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h14a1.5 1.5 0 0 1 1.5 1.5V10a2 2 0 0 0 0 4v2.5A1.5 1.5 0 0 1 19 18H5a1.5 1.5 0 0 1-1.5-1.5V14a2 2 0 0 0 0-4z"/><path d="M14.5 7v10" stroke-dasharray="1.6 2.2"/>',
    trophy: '<path d="M7.5 4h9v5.5a4.5 4.5 0 0 1-9 0z"/><path d="M7.5 6h-3v1a3.5 3.5 0 0 0 3.3 3.5M16.5 6h3v1a3.5 3.5 0 0 1-3.3 3.5M12 14v3.5M8.5 20h7"/>',
    target: `<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4" ${F}/>`,
    bolt: '<path d="M13.5 2.8 5.5 13.5h5.8l-.8 7.7 8-10.7h-5.8z"/>',
    flame: '<path d="M12 3c.8 3.2 4.8 5.1 4.8 10a4.8 4.8 0 0 1-9.6 0c0-2.2 1.1-3.7 2.3-4.8.3 1.8 1.1 2.8 2.3 3.3-.5-3 .2-5.8.2-8.5z"/>',
    bellCurve: '<path d="M3 19.5h18"/><path d="M4 18.5c3.2 0 4.4-12 8-12s4.8 12 8 12"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9.5 3v3M14.5 3v3M9.5 18v3M14.5 18v3M3 9.5h3M3 14.5h3M18 9.5h3M18 14.5h3"/>',

    // ── deportes y clima ──
    soccerball: `<circle cx="12" cy="12" r="9"/><path d="M12 8.6l3.23 2.35L14 14.75h-4l-1.23-3.8z" fill="currentColor" stroke-width="1.2"/><path d="M12 8.6V3.2M15.23 10.95l5.1-1.65M14 14.75l3.15 4.35M10 14.75 6.85 19.1M8.77 10.95 3.67 9.3"/>`,
    basketball: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18M5.7 5.7c2.4 1.8 3.6 4 3.6 6.3s-1.2 4.5-3.6 6.3M18.3 5.7c-2.4 1.8-3.6 4-3.6 6.3s1.2 4.5 3.6 6.3"/>',
    baseball: '<circle cx="12" cy="12" r="9"/><path d="M7 4.6c2.4 2 3.4 4.6 3.4 7.4s-1 5.4-3.4 7.4M17 4.6c-2.4 2-3.4 4.6-3.4 7.4s1 5.4 3.4 7.4"/>',
    diamond: `<path d="M12 3.5 20.5 12 12 20.5 3.5 12z"/><circle cx="12" cy="12" r="1.6" ${F}/>`,
    pitch: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 5v14"/><circle cx="12" cy="12" r="2.6"/>',
    camera: '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.2l1.5-2h5.6l1.5 2h2.2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z"/><circle cx="12" cy="13" r="3.3"/>',
    tactics: '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M7 7l3 3M10 7l-3 3"/><circle cx="16" cy="16.5" r="1.8"/><path d="M8.5 15.5c2.5 0 3.5-4 6-5.5M12.8 9.6l2-.4.3 2"/>',
    wind: '<path d="M3 8.5h10a2.5 2.5 0 1 0-2.5-2.5M3 12.5h14.5a2.5 2.5 0 1 1-2.5 2.5M3 16.5h7"/>',
    dome: '<path d="M3 19h18"/><path d="M4.5 19a7.5 7.5 0 0 1 15 0"/><path d="M12 11.5V19M8.2 12.8 9.6 19M15.8 12.8 14.4 19"/>',
    umbrella: '<path d="M3 12a9 9 0 0 1 18 0z"/><path d="M12 12v6a2 2 0 0 1-4 0"/>',
    cloudSun: '<path d="M8.5 3.5v1.3M3.5 8.5h1.3M5 5l.9.9M12 5l-.9.9"/><path d="M5.7 11.3a3.3 3.3 0 1 1 5.9-2.8"/><path d="M8.5 20a3.5 3.5 0 0 1-.4-7 4.8 4.8 0 0 1 9.2 1.3A2.9 2.9 0 0 1 17 20z"/>',
  };

  function ICON(name, size, cls) {
    const body = P[name];
    if (!body) return '';
    const dim = size ? ` width="${size}" height="${size}"` : '';
    return `<svg class="ic ic-${name}${cls ? ' ' + cls : ''}" viewBox="0 0 24 24"${dim} fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  }
  // Ganado / perdido / pendiente, para las pastillas de resultado.
  ICON.result = (r, size) => ICON(r === 'W' ? 'checkmark' : r === 'L' ? 'xmark' : 'dot', size, 'ic-res');
  ICON.names = Object.keys(P);
  root.ICON = ICON;
})(typeof window !== 'undefined' ? window : globalThis);
