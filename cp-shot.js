/* CherryPick · foto para compartir (Destacados / Sharp / Parlay de un partido).
   Se dibuja en un canvas propio, no es una captura de pantalla: 2160 px de ancho.
   Lo usan la pestaña NBA (nba-dd.js) y la de Fútbol (futbol-tab.js).

   CPShot.make({
     theme: 'nba' | 'unl' | 'ucl' | …,      // fondo propio de la liga (THEMES)
     league: 'ruta del logo de la liga',    // para fondo oscuro
     title, subtitle,
     teams: [{ label, logo }, { label, logo }], sep: '@' | 'vs',
     when: 'JUE 12 NOV · 21:00 H',
     rows: [{ name, line, tag: [texto, color] | null, stats: [[valor, rótulo, color]],
              book, bookLogo, bookColor, price, good, photo, photoFit: 'head' | 'cover', teamLogo, color,
              books: [{ book, bookLogo, bookColor, price, good }] }],   // opcional: hasta 2 casas (reemplaza a la casa única)
     footer, filename, shareTitle,
   }) → Promise<nombre del archivo | null>                                         */
(function () {
  'use strict';
  const KO = '"Knockout", "Action NBA Cond", "Arial Narrow", sans-serif';
  const RB = 'Roboto, "Segoe UI", Arial, sans-serif';
  const CHERRY = 'assets/cherry-logo.svg';


  // Fondo propio de cada liga y deporte: los mismos fondos y tramas que usa la app.
  //   base: color o [arriba, medio, abajo] · layers: imágenes encima · shade: [arriba, abajo] de negro
  //   wedge: franja diagonal de color (sólo donde no hay imagen de fondo) · accent: borde de cada fila
  // fit de cada capa: 'cover' · 'top' / 'bottom' (a lo ancho, pegada a ese borde) · 'tile' · 'corner'
  const THEMES = {
    nba: { base: ['#12306b', '#0b1d40', '#061230'], layers: [{ src: 'assets/nba/bg-court-blue.png', fit: 'cover', alpha: .5 }],
      shade: [.1, .5], wedge: ['#c8102e', '#8a0b22'], accent: '#c8102e' },
    unl: { base: '#0e233a', layers: [{ src: 'assets/unl/pattern-dark.png', fit: 'tile', w: 560, alpha: 1 }],
      shade: [0, .35], accent: '#3ad6c5' },
    ucl: { base: '#06094a', layers: [{ src: 'assets/ucl/bg-tall.webp', fit: 'cover' }], shade: [0, .2], accent: '#2a6bff' },
    uel: { base: '#000', layers: [{ src: 'assets/uel/bg-page.webp', fit: 'top', w: 1.75, ax: 1 }], shade: [0, .1], accent: '#ff6900' },
    arg: { base: '#06004a', layers: [{ src: 'assets/lpf/waves-top.jpg', fit: 'top', w: 1.3, ax: .5 }, { src: 'assets/lpf/waves-bottom.jpg', fit: 'bottom', w: 1.3, ax: .5 }],
      shade: [.1, .1], accent: '#2fd3c9' },
    bra: { base: '#190277', layers: [{ src: 'assets/bra/hero-bg.webp', fit: 'top', w: 1, ax: 1, fade: .4 }], shade: [0, .3], accent: '#c6f000' },
    ita: { base: '#234386', layers: [{ src: 'assets/ita/bg-hero.webp', fit: 'top', w: 1, ax: .5, fade: .3 }], shade: [.2, .5], accent: '#14b7cb' },
    esp: { base: ['#14143a', '#0a0a2a', '#00001b'], glow: ['rgba(255,75,68,.4)', 0, .12, .7],
      layers: [{ src: 'assets/esp/pattern-corner.svg', fit: 'corner', w: 300, tint: '#ff4b44', alpha: .9 }], shade: [0, .2], accent: '#ff4b44' },
    cdb: { base: ['#1f4470', '#1b3a62', '#183456'], layers: [{ src: 'assets/cdb/bg-page.svg', fit: 'top', w: 1, ax: .5 }], shade: [0, .3], accent: '#e9a443' },
    eng: { base: ['#4a0a52', '#2d0633', '#16031a'], wedge: ['#e90052', '#9c0038'], shade: [0, .2], accent: '#e90052' },
  };

  const img = (src, cors) => new Promise((res) => {
    if (!src) return res(null);
    const im = new Image();
    if (cors) im.crossOrigin = 'anonymous';
    im.onload = () => res(im); im.onerror = () => res(null);
    im.src = src;
  });
  // Las imágenes de otro dominio se piden con CORS; si el servidor no lo permite, no se usan
  // (una sola imagen sin permiso impediría exportar el canvas).
  const remote = (src) => /^https?:/i.test(src || '') && !String(src).startsWith(location.origin);
  // Si la página ya mostró esa imagen sin CORS, el navegador reusa la copia guardada (sin el
  // permiso) y falla: se pide de nuevo salteando esa copia.
  const load = async (src) => {
    if (!remote(src)) return img(src, false);
    return (await img(src, true)) || img(`${src}${src.includes('?') ? '&' : '?'}cpshot=1`, true);
  };
  const iw = (im) => im.naturalWidth || im.width || 120;
  const ih = (im) => im.naturalHeight || im.height || 120;
  const initials = (n) => String(n || '').split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();

  async function make(o) {
    const rows = o.rows || [];
    if (!rows.length) return null;
    try { await Promise.all([document.fonts.load(`400 40px ${KO}`), document.fonts.load(`700 28px ${RB}`), document.fonts.load(`900 28px ${RB}`)]); } catch (_) { /* sigue con lo que haya */ }
    // Hasta 2 casas por fila (r.books); si no viene la lista, la casa única de la fila.
    const bkOf = rows.map((r) => (r.books && r.books.length ? r.books.slice(0, 2)
      : [{ book: r.book, bookLogo: r.bookLogo, bookColor: r.bookColor, price: r.price, good: r.good }]));
    const [cherry, league, lgA, lgB, ...rest] = await Promise.all([
      load(CHERRY), load(o.league), load(o.teams[0].logo), load(o.teams[1].logo),
      ...rows.map((r) => load(r.photo)), ...rows.map((r) => load(r.teamLogo)), ...bkOf.flat().map((b) => load(b.bookLogo))]);
    const faces = rest.slice(0, rows.length), tms = rest.slice(rows.length, rows.length * 2), bkIm = rest.slice(rows.length * 2);
    let bkN = 0;
    const bks = bkOf.map((list) => list.map(() => bkIm[bkN++]));
    const th = THEMES[o.theme] || THEMES.nba;
    const bgs = await Promise.all((th.layers || []).map((L) => load(L.src)));

    const W = 1080, S = 2, PAD = 44, HEAD = 318, RH = 164, GAP = 18, FOOT = 112;
    const H = HEAD + rows.length * (RH + GAP) - GAP + FOOT;
    const cv = document.createElement('canvas');
    cv.width = W * S; cv.height = H * S;
    const c = cv.getContext('2d');
    c.scale(S, S);
    const ls = (px) => { try { c.letterSpacing = `${px}px`; } catch (_) { /* navegador viejo */ } };
    const rr = (x, y, w, h, r) => { c.beginPath(); c.roundRect(x, y, w, h, r); };
    const txt = (s, x, y, font, color, align = 'left', sp = 0) => {
      c.font = font; c.fillStyle = color; c.textAlign = align; c.textBaseline = 'alphabetic'; ls(sp);
      c.fillText(s, x, y); ls(0);
    };
    const fit = (s, max, size, fam, w = 400, min = 18) => { let z = size; do { c.font = `${w} ${z}px ${fam}`; if (c.measureText(s).width <= max) break; z -= 1; } while (z > min); return `${w} ${z}px ${fam}`; };
    // dibuja la imagen entera dentro de la caja; devuelve el ancho que ocupó
    const contain = (im, x, y, w, h, alignLeft = false) => {
      if (!im) return 0;
      const k = Math.min(w / iw(im), h / ih(im)), dw = iw(im) * k, dh = ih(im) * k;
      c.drawImage(im, alignLeft ? x : x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
      return dw;
    };
    // marca: cereza + CHERRYPICK. Devuelve el ancho total.
    const brand = (x, y, size, right = false) => {
      c.font = `900 ${size}px ${RB}`; ls(1);
      const w1 = c.measureText('CHERRY').width, w2 = c.measureText('PICK').width; ls(0);
      const ic = size * 1.25, gap = size * .28, total = ic + gap + w1 + w2;
      const x0 = right ? x - total : x;
      if (cherry) c.drawImage(cherry, x0, y - size * .98, ic, ic);
      txt('CHERRY', x0 + ic + gap, y, `900 ${size}px ${RB}`, '#fff', 'left', 1);
      txt('PICK', x0 + ic + gap + w1, y, `900 ${size}px ${RB}`, '#ff4d5e', 'left', 1);
      return total;
    };

    // fondo propio de la liga
    if (Array.isArray(th.base)) {
      const g0 = c.createLinearGradient(0, 0, 0, H);
      g0.addColorStop(0, th.base[0]); g0.addColorStop(.42, th.base[1]); g0.addColorStop(1, th.base[2]);
      c.fillStyle = g0;
    } else c.fillStyle = th.base;
    c.fillRect(0, 0, W, H);
    const baseBot = Array.isArray(th.base) ? th.base[1] : th.base;
    (th.layers || []).forEach((L, i) => {
      const im = bgs[i];
      if (!im) return;
      const w0 = iw(im), h0 = ih(im);
      c.save(); c.globalAlpha = L.alpha == null ? 1 : L.alpha;
      if (L.fit === 'cover') {
        const k = Math.max(W / w0, H / h0);
        c.drawImage(im, (W - w0 * k) / 2, 0, w0 * k, h0 * k);
      } else if (L.fit === 'tile') {
        const tw0 = L.w || 400, th0 = tw0 * h0 / w0;
        for (let y = 0; y < H; y += th0) for (let x = 0; x < W; x += tw0) c.drawImage(im, x, y, tw0 + .5, th0 + .5);
      } else if (L.fit === 'corner') {
        const s0 = L.w || 260;
        let src = im;
        if (L.tint) {             // la forma, pintada del color de la liga
          const t = document.createElement('canvas'); t.width = s0 * S; t.height = s0 * S * h0 / w0;
          const tc2 = t.getContext('2d');
          tc2.drawImage(im, 0, 0, t.width, t.height);
          tc2.globalCompositeOperation = 'source-in'; tc2.fillStyle = L.tint; tc2.fillRect(0, 0, t.width, t.height);
          src = t;
        }
        c.drawImage(src, W - s0, 0, s0, s0 * h0 / w0);
      } else {                    // 'top' / 'bottom': a lo ancho, pegada al borde
        const dw = W * (L.w || 1), dh = dw * h0 / w0, x = (W - dw) * (L.ax == null ? .5 : L.ax);
        const y = L.fit === 'bottom' ? H - dh : 0;
        c.drawImage(im, x, y, dw, dh);
        if (L.fade) {             // se funde con el color de base para que no quede un corte
          c.globalAlpha = 1;
          const f0 = y + dh * (1 - L.fade), gf = c.createLinearGradient(0, f0, 0, y + dh);
          gf.addColorStop(0, 'rgba(0,0,0,0)'); gf.addColorStop(1, baseBot);
          c.fillStyle = gf; c.fillRect(0, f0, W, dh * L.fade + 1);
          c.fillStyle = baseBot; c.fillRect(0, y + dh, W, Math.max(0, H - y - dh));
        }
      }
      c.restore();
    });
    if (th.glow) {
      const [col, gx, gy, gr0] = th.glow, rg = c.createRadialGradient(W * gx, H * gy, 0, W * gx, H * gy, W * gr0);
      rg.addColorStop(0, col); rg.addColorStop(1, 'rgba(0,0,0,0)');
      c.fillStyle = rg; c.fillRect(0, 0, W, H);
    }
    if (th.shade) {               // oscurece hacia abajo: las tarjetas se despegan del fondo
      const gs = c.createLinearGradient(0, 0, 0, H);
      gs.addColorStop(0, `rgba(0,0,0,${th.shade[0]})`); gs.addColorStop(1, `rgba(0,0,0,${th.shade[1]})`);
      c.fillStyle = gs; c.fillRect(0, 0, W, H);
    }
    let gr;
    if (th.wedge) {
    c.save();
    c.beginPath(); c.moveTo(W * .62, 0); c.lineTo(W, 0); c.lineTo(W, HEAD - 26); c.lineTo(W * .5, HEAD - 26); c.closePath();
    gr = c.createLinearGradient(W * .5, 0, W, HEAD);
    gr.addColorStop(0, th.wedge[0]); gr.addColorStop(1, th.wedge[1]);
    c.fillStyle = gr; c.globalAlpha = .92; c.fill();
    c.globalAlpha = .1; c.strokeStyle = '#fff'; c.lineWidth = 3;
    c.beginPath(); c.arc(W - 90, 150, 150, 0, Math.PI * 2); c.stroke();
    c.beginPath(); c.arc(W - 90, 150, 60, 0, Math.PI * 2); c.stroke();
    c.restore();
    }

    // cabecera: logo de la liga | cereza CHERRYPICK
    let bx = PAD;
    if (league) {
      const dw = contain(league, PAD, 34, 150, 60, true);
      c.fillStyle = 'rgba(255,255,255,.35)'; c.fillRect(PAD + dw + 16, 42, 2, 44);
      bx = PAD + dw + 34;
    }
    brand(bx, 77, 30);
    txt(String(o.title || '').toUpperCase(), PAD, 178, `400 92px ${KO}`, '#fff', 'left', 3);
    txt(o.subtitle || '', PAD, 216, `500 24px ${RB}`, 'rgba(255,255,255,.82)');

    // partido
    const my = 236, mw = W - PAD * 2;
    rr(PAD, my, mw, 62, 16); c.fillStyle = 'rgba(4,12,32,.55)'; c.fill();
    c.font = `700 22px ${RB}`; ls(1);
    const whenTxt = String(o.when || '').toUpperCase(), ww = c.measureText(whenTxt).width; ls(0);
    txt(whenTxt, W - PAD - 20, my + 40, `700 22px ${RB}`, 'rgba(255,255,255,.85)', 'right', 1);
    const [A, B] = o.teams.map((t) => ({ ...t, label: String(t.label || '').toUpperCase() })), sep = o.sep || 'vs';
    const room = mw - ww - 40 - 16 - 54 * 2 - 60;          // lo que queda para los dos nombres
    let z = 40;
    const tw = (s) => { ls(2); const w = c.measureText(s).width; ls(0); return w; };
    do { c.font = `400 ${z}px ${KO}`; if (tw(A.label) + tw(B.label) <= room) break; z -= 1; } while (z > 22);
    const nf = `400 ${z}px ${KO}`;
    let x = PAD + 16;
    contain(lgA, x, my + 9, 44, 44); x += 54;
    txt(A.label, x, my + 31 + z * .33, nf, '#fff', 'left', 2); c.font = nf; x += tw(A.label) + 16;
    txt(sep, x, my + 41, `700 22px ${RB}`, 'rgba(255,255,255,.6)'); c.font = `700 22px ${RB}`; x += c.measureText(sep).width + 16;
    contain(lgB, x, my + 9, 44, 44); x += 54;
    txt(B.label, x, my + 31 + z * .33, nf, '#fff', 'left', 2);

    // filas
    rows.forEach((r, i) => {
      const y = HEAD + i * (RH + GAP), x0 = PAD, w = W - PAD * 2;
      c.save(); c.shadowColor = 'rgba(0,6,24,.5)'; c.shadowBlur = 26; c.shadowOffsetY = 10;
      rr(x0, y, w, RH, 24); c.fillStyle = '#fff'; c.fill(); c.restore();
      const tc = r.color || th.accent;
      c.save(); rr(x0, y, w, RH, 24); c.clip(); c.fillStyle = tc; c.fillRect(x0, y, 10, RH); c.restore();
      txt(String(i + 1), x0 + 46, y + RH / 2 + 15, `400 44px ${KO}`, '#c3cbdb', 'center');
      // foto
      const cx = x0 + 138, cy = y + RH / 2, R = 56;
      c.beginPath(); c.arc(cx, cy, R, 0, Math.PI * 2); c.fillStyle = '#e9edf5'; c.fill();
      c.save(); c.beginPath(); c.arc(cx, cy, R, 0, Math.PI * 2); c.clip();
      const f = faces[i];
      if (f && r.photoFit === 'cover') {
        const k = Math.max((R * 2) / iw(f), (R * 2) / ih(f)) * 1.04;
        c.drawImage(f, cx - iw(f) * k / 2, cy - R + 4, iw(f) * k, ih(f) * k);
      } else if (f) {
        const k = (R * 2.5) / iw(f);
        c.drawImage(f, cx - iw(f) * k / 2, cy - R + 2, iw(f) * k, ih(f) * k);
      } else txt(initials(r.name), cx, cy + 16, `400 46px ${KO}`, '#64708a', 'center');
      c.restore();
      c.beginPath(); c.arc(cx, cy, R, 0, Math.PI * 2); c.lineWidth = 5; c.strokeStyle = tc; c.stroke();
      if (tms[i]) {
        c.beginPath(); c.arc(cx + 42, cy + 40, 22, 0, Math.PI * 2); c.fillStyle = '#fff'; c.fill();
        c.lineWidth = 2; c.strokeStyle = '#dfe4ee'; c.stroke();
        contain(tms[i], cx + 25, cy + 23, 34, 34);
      }
      // cuota: hasta 2 casas, una caja por casa (apiladas si son dos). El color de la casa
      // con su banner, y la cuota en verde si paga más que la justa del modelo o en azul oscuro si no.
      const ow = 168, ox = x0 + w - ow - 20;
      const oy = y + 20, oh = RH - 40, band = 50;
      const two = bkOf[i].length > 1, bh1 = two ? (oh - 8) / 2 : oh, rad = two ? 14 : 18;
      bkOf[i].forEach((b, j) => {
        const im = bks[i][j], by = oy + j * (bh1 + 8);
        const bn = String(b.book || '').toLowerCase();
        const hasBk = !!(im || bn);
        const price = b.price || '—';
        let bcol = b.bookColor || '#3a4763';
        if (im) {                 // el color del tramo sale del propio banner
          try {
            const t = document.createElement('canvas'); t.width = 40; t.height = 12;
            const tx2 = t.getContext('2d'); tx2.drawImage(im, 0, 0, 40, 12);
            const d = tx2.getImageData(1, 6, 1, 1).data;
            if (d[3] > 200) bcol = `rgb(${d[0]},${d[1]},${d[2]})`;
          } catch (_) { /* queda el color de la casa */ }
        }
        c.save(); rr(ox, by, ow, bh1, rad); c.clip();
        c.fillStyle = b.good ? '#0f7a3d' : '#0b1d40'; c.fillRect(ox, by, ow, bh1);
        if (two) {                // casa a la izquierda, cuota a la derecha
          const bandW = 78;
          if (hasBk) { c.fillStyle = bcol; c.fillRect(ox, by, bandW, bh1); }
          if (im) {
            const k = Math.min((bandW - 12) / iw(im), (bh1 - 16) / ih(im)), bw = iw(im) * k, bh = ih(im) * k;
            c.drawImage(im, ox + (bandW - bw) / 2, by + (bh1 - bh) / 2, bw, bh);
          } else if (bn) {
            txt(bn, ox + bandW / 2, by + bh1 / 2 + 6, fit(bn, bandW - 10, 17, RB, 800, 10), '#fff', 'center');
          }
          c.restore();
          txt(price, ox + bandW + (ow - bandW) / 2, by + bh1 / 2 + 11, fit(price, ow - bandW - 10, 31, RB, 900, 20), '#fff', 'center');
        } else {                  // banner arriba, cuota abajo
          if (hasBk) { c.fillStyle = bcol; c.fillRect(ox, by, ow, band); }
          if (im) {
            const k = Math.min((ow - 16) / iw(im), (band - 8) / ih(im)), bw = iw(im) * k, bh = ih(im) * k;
            c.drawImage(im, ox + (ow - bw) / 2, by + (band - bh) / 2, bw, bh);
          } else if (bn) {
            txt(bn, ox + ow / 2, by + band / 2 + 8, fit(bn, ow - 20, 23, RB, 800, 12), '#fff', 'center');
          }
          c.restore();
          txt(price, ox + ow / 2, hasBk ? by + band + 53 : by + oh / 2 + 18, `900 ${hasBk ? 46 : 50}px ${RB}`, '#fff', 'center');
        }
      });
      // nombre y apuesta: todo el ancho hasta la cuota, la apuesta bien grande
      const tx = x0 + 216, tw2 = ox - 22 - tx;
      const name = String(r.name || '').toUpperCase();
      c.font = fit(name, tw2 - 8, 42, KO, 400, 24); ls(1.5);
      c.fillStyle = '#0b1d40'; c.textAlign = 'left'; c.fillText(name, tx, y + 54); ls(0);
      c.font = fit(r.line, tw2, 36, RB, 800, 22); c.fillStyle = '#1d428a'; c.fillText(r.line, tx, y + 96);
      // datos (probabilidad, justa, diferencia…): cartelitos chicos en una sola fila, debajo de
      // la apuesta. Primero la etiqueta de la jugada (VALOR, FIRME…), si tiene.
      const chips = (r.stats || []).map(([b, sm, col]) => ({ v: String(b), lab: String(sm).toUpperCase(), col }));
      const widths = (z) => {
        const lz = Math.round(z * .72), padX = Math.round(z * .62), gap = Math.round(z * .36);
        let tagW = 0;
        if (r.tag) { c.font = `800 ${lz + 1}px ${RB}`; ls(.6); tagW = c.measureText(r.tag[0]).width + padX * 2; ls(0); }
        const ws = chips.map((k) => {
          c.font = `900 ${z}px ${RB}`; const w1 = c.measureText(k.v).width;
          c.font = `700 ${lz}px ${RB}`; ls(.5); const w2 = c.measureText(k.lab).width; ls(0);
          return { w1, w: padX + w1 + gap + w2 + padX };
        });
        const sep = Math.round(z * .5);
        return { lz, padX, gap, sep, tagW, ws, total: tagW + ws.reduce((s, k) => s + k.w, 0) + sep * (ws.length - (r.tag ? 0 : 1)) };
      };
      let cz = 19, m = widths(cz);
      while (m.total > tw2 && cz > 12) { cz -= 1; m = widths(cz); }
      const ch = Math.round(cz * 1.75), cyTop = y + 114, base = cyTop + ch / 2 + cz * .36;
      let px = tx;
      if (r.tag) {
        rr(px, cyTop, m.tagW, ch, ch / 2); c.fillStyle = r.tag[1]; c.fill();
        txt(r.tag[0], px + m.tagW / 2, cyTop + ch / 2 + (m.lz + 1) * .36, `800 ${m.lz + 1}px ${RB}`, '#fff', 'center', .6);
        px += m.tagW + m.sep;
      }
      chips.forEach((k, j) => {
        const g = m.ws[j];
        rr(px, cyTop, g.w, ch, ch / 2); c.fillStyle = '#eef2f8'; c.fill();
        c.lineWidth = 1.5; c.strokeStyle = '#d5dcea'; c.stroke();
        txt(k.v, px + m.padX, base, `900 ${cz}px ${RB}`, k.col, 'left');
        txt(k.lab, px + m.padX + g.w1 + m.gap, base - 1, `700 ${m.lz}px ${RB}`, '#5d6a84', 'left', .5);
        px += g.w + m.sep;
      });
    });

    // pie
    const fy = H - FOOT + 36;
    c.fillStyle = 'rgba(255,255,255,.14)'; c.fillRect(PAD, fy, W - PAD * 2, 2);
    const bw = brand(W - PAD, fy + 42, 22, true);
    txt(o.footer || '', PAD, fy + 40, fit(o.footer || '', W - PAD * 2 - bw - 24, 20, RB, 500, 14), 'rgba(255,255,255,.72)');

    const blob = await new Promise((res) => cv.toBlob(res, 'image/png'));
    if (!blob) throw new Error('no se pudo generar la imagen');
    const name = String(o.filename || 'cherrypick.png').replace(/[^\w.\-]+/g, '-');
    // En el celular se abre el menú de compartir (WhatsApp, guardar…); en la compu se descarga.
    const file = new File([blob], name, { type: 'image/png' });
    if (matchMedia('(pointer: coarse)').matches && navigator.canShare && navigator.canShare({ files: [file] })) {
      try { await navigator.share({ files: [file], title: o.shareTitle || 'CherryPick' }); return name; }
      catch (e) { if (e && e.name === 'AbortError') return null; }
    }
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return name;
  }

  // color de la etiqueta de cada casa
  // banners oficiales (assets/books), por si la casa no trae logo en la lista de la app
  const BOOK_LOGO = { 'bet365.bet.ar': 'assets/books/bet365.png', bet365: 'assets/books/bet365.png', betano: 'assets/books/betano.png',
    betsson: 'assets/books/betsson.png', '1xbet': 'assets/books/1xbet.png' };
  const BOOK_COLOR = { betano: '#ff3c00', betsson: '#ff6600', '1xbet': '#1a5685', 'bet365.bet.ar': '#027b5b', bet365: '#027b5b', pinnacle: '#ff5500' };
  const stamp = () => new Date().toLocaleString('es-AR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
  const whenTxt = (d) => `${d.toLocaleDateString('es-AR', { weekday: 'short', day: 'numeric', month: 'short' })} · ${d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false })} h`;
  const slug = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '');

  window.CPShot = { make, THEMES, BOOK_LOGO, BOOK_COLOR, stamp, whenTxt, slug };
})();
