/* ════════════════════════════════════════════════════════════════════════════
   La animación de simulación de cada partido NBA: una cancha de básquet.

   Mientras el motor corre en el backend, la cabecera muestra media cancha vista
   desde arriba: tiros en parábola desde distintos puntos hacia el aro, la red
   que se sacude con los que entran, y abajo un marcador de LEDs donde cada tiro
   suma su resultado a la distribución. Encima del marcador, la curva simétrica
   del libro; las columnas de la cola que la superan se encienden en pistacho.
   Un reloj de posesión cuenta el tiempo real que lleva la simulación.

   Sonido (WebAudio, sin archivos): un pad cálido que respira —sube, se
   desvanece y vuelve— con un latido grave suave; cuando un tiro cae en la
   cola por encima del libro, el pad se abre y brilla un instante.

   Al terminar, la cancha se disuelve en el RESULTADO REAL: la distribución del
   mejor pick que encontró el motor (media, mediana, desvío y sesgo reales)
   contra la Normal del libro, con la línea alt marcada y las dos
   probabilidades. El pad da una última respiración larga y se apaga.

   Los tiros son ilustrativos (el cómputo real ocurre en el servidor); el reloj
   y el cuadro final no: salen del tiempo transcurrido y del payload.
   ════════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const MOTION_OK = !(window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const SOUND_KEY = 'al-sound';
  const BINS = 31;
  const ROWS = 13;
  const GREEN = '166, 216, 111';   // verde pistacho
  // El tablero necesita unos segundos para contar su historia (la pila, la
  // curva fantasma, la cola que se enciende) aunque el motor termine antes.
  const MIN_BOARD_MS = 3500;

  // ── Utilidades ────────────────────────────────────────────────────────────
  const hex = (h, a = 1) => {
    const s = String(h || '5a6b7a').replace('#', '');
    const n = parseInt(s.length === 3 ? s.split('').map(c => c + c).join('') : s, 16) || 0x5a6b7a;
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  };
  // Colores de equipo demasiado oscuros se pierden sobre el fondo.
  const lift = (h) => {
    const s = String(h || '5a6b7a').replace('#', '');
    const n = parseInt(s, 16) || 0x5a6b7a;
    let r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    if (lum < 90) {
      const k = 90 / Math.max(lum, 1);
      r = Math.min(255, r * k + 40); g = Math.min(255, g * k + 40); b = Math.min(255, b * k + 40);
    }
    return ((1 << 24) + (Math.round(r) << 16) + (Math.round(g) << 8) + Math.round(b)).toString(16).slice(1);
  };
  const normPdf = (x, m, s) => Math.exp(-0.5 * ((x - m) / s) ** 2) / (s * Math.sqrt(2 * Math.PI));
  // Inversa de la Normal estándar (Acklam). Sirve para recuperar el sigma del
  // libro a partir de p_curve.
  function normInv(p) {
    const a = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269, -30.6647980661472, 2.50662827745924];
    const b = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197, -13.2806815528857];
    const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373, 4.37466414146497, 2.93816398269878];
    const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];
    const pl = 0.02425;
    if (p <= 0 || p >= 1) return p <= 0 ? -8 : 8;
    if (p < pl) {
      const q = Math.sqrt(-2 * Math.log(p));
      return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }
    if (p > 1 - pl) return -normInv(1 - p);
    const q = p - 0.5, r = q * q;
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
  }
  // log Γ (Lanczos) para la densidad Gamma desplazada.
  function lgamma(z) {
    const g = 7, cf = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
      -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
    if (z < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * z)) - lgamma(1 - z);
    z -= 1;
    let x = cf[0];
    for (let i = 1; i < g + 2; i++) x += cf[i] / (z + i);
    const t = z + g + 0.5;
    return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(x);
  }
  // Densidad con la media, el desvío y el sesgo del jugador: Gamma desplazada
  // (los tres momentos exactos); Normal si casi no hay sesgo.
  function shapePdf(mean, sd, skew) {
    if (!(skew > 0.08)) return x => normPdf(x, mean, sd);
    const k = 4 / (skew * skew), th = sd * skew / 2, loc = mean - k * th, lg = lgamma(k);
    return x => {
      const y = (x - loc) / th;
      return y <= 0 ? 0 : Math.exp((k - 1) * Math.log(y) - y - lg) / th;
    };
  }

  // ── Audio: un pad que respira ─────────────────────────────────────────────
  // Nada de notas sueltas: un acorde cálido (La mayor abierto) detrás de un
  // filtro pasa-bajos, cuyo volumen sube y se desvanece en ciclos de ~2,6 s,
  // con un latido grave muy suave en cada pico ("palpitante"). Cuando una
  // tirada cae en la cola por encima del libro, el filtro se abre un instante:
  // el pad brilla en vez de sonar una nota.
  const Audio = {
    ctx: null, master: null, pad: null, on: (() => {
      try { return localStorage.getItem(SOUND_KEY) !== 'off'; } catch (_) { return true; }
    })(),
    lastShimmer: 0,
    // El AudioContext sólo puede nacer de un gesto del usuario: el clic en la
    // tarjeta lo es. Sin gesto (p. ej. "Simular todos" ya en marcha) se reusa.
    ensure() {
      if (!this.on) return null;
      try {
        if (!this.ctx) {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (!Ctx) return null;
          this.ctx = new Ctx();
          this.master = this.ctx.createGain();
          this.master.gain.value = 0.8;
          this.master.connect(this.ctx.destination);
        }
        if (this.ctx.state === 'suspended') this.ctx.resume();
        return this.ctx;
      } catch (_) { return null; }
    },
    setOn(v) {
      this.on = !!v;
      try { localStorage.setItem(SOUND_KEY, v ? 'on' : 'off'); } catch (_) { }
      if (this.master) this.master.gain.setTargetAtTime(v ? 0.8 : 0, this.ctx.currentTime, 0.15);
    },
    _padStart() {
      const ctx = this.ensure(); if (!ctx) return;
      const t = ctx.currentTime;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass'; filter.frequency.value = 520; filter.Q.value = 0.7;
      const breath = ctx.createGain(); breath.gain.value = 0.0001;
      filter.connect(breath).connect(this.master);
      // A2 · E3 · A3 · C#4, cada voz con un gemelo apenas desafinado (chorus).
      const oscs = [];
      [110, 164.81, 220, 277.18].forEach((f, i) => {
        [-4, 4].forEach(cents => {
          const o = ctx.createOscillator();
          o.type = i === 0 ? 'sine' : 'triangle';
          o.frequency.value = f; o.detune.value = cents;
          const g = ctx.createGain(); g.gain.value = i === 0 ? 0.5 : 0.22;
          o.connect(g).connect(filter); o.start(t); oscs.push(o);
        });
      });
      this.pad = { filter, breath, oscs, users: 0, timer: null };
      const pulse = () => {
        if (!this.pad || !this.ctx) return;
        const n = this.ctx.currentTime, g = this.pad.breath.gain;
        // Sube suave (1,1 s) y se desvanece (1,5 s): fade-in / fade-out.
        g.cancelScheduledValues(n);
        g.setValueAtTime(Math.max(0.0001, g.value), n);
        g.linearRampToValueAtTime(0.06, n + 1.1);
        g.exponentialRampToValueAtTime(0.004, n + 2.6);
        // Latido: dos golpes graves y redondos en el pico de la respiración.
        this._thump(1.0, 0.05); this._thump(1.24, 0.03);
      };
      pulse();
      this.pad.timer = setInterval(pulse, 2600);
    },
    _thump(at, gain) {
      const ctx = this.ctx; if (!ctx || !this.on) return;
      const t = ctx.currentTime + at;
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine';
      o.frequency.setValueAtTime(72, t); o.frequency.exponentialRampToValueAtTime(48, t + 0.25);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(gain, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
      o.connect(g).connect(this.master);
      o.start(t); o.stop(t + 0.45);
    },
    padUse(d) {
      if (d > 0 && !this.pad) this._padStart();
      if (!this.pad) return;
      this.pad.users = Math.max(0, this.pad.users + d);
      if (this.pad.users === 0) this._padStop(1.8);
    },
    _padStop(fade) {
      const p = this.pad; if (!p) return;
      this.pad = null;
      clearInterval(p.timer);
      const t = this.ctx.currentTime;
      p.breath.gain.cancelScheduledValues(t);
      p.breath.gain.setValueAtTime(Math.max(0.0001, p.breath.gain.value), t);
      p.breath.gain.exponentialRampToValueAtTime(0.0001, t + fade);
      p.oscs.forEach(o => o.stop(t + fade + 0.1));
    },
    // La cola que le gana al libro: el pad se abre y brilla un instante.
    shimmer() {
      if (!this.pad || !this.on) return;
      const now = performance.now();
      if (now - this.lastShimmer < 400) return;
      this.lastShimmer = now;
      const f = this.pad.filter.frequency, t = this.ctx.currentTime;
      f.cancelScheduledValues(t);
      f.setValueAtTime(f.value, t);
      f.linearRampToValueAtTime(1500, t + 0.35);
      f.setTargetAtTime(520, t + 0.4, 0.6);
    },
    // Resolución: una última respiración larga y abierta que se apaga sola.
    resolve(hasEdge) {
      if (!this.ctx || !this.on) return;
      // Un pad propio para la despedida: el compartido puede seguir sonando
      // para otra tarjeta que todavía simula.
      const shared = this.pad;
      this.pad = null;
      this._padStart();
      const p = this.pad;
      this.pad = shared;
      if (!p) return;
      const t = this.ctx.currentTime;
      clearInterval(p.timer);
      p.filter.frequency.setTargetAtTime(hasEdge ? 1300 : 420, t, 0.4);
      p.breath.gain.cancelScheduledValues(t);
      p.breath.gain.setValueAtTime(Math.max(0.0001, p.breath.gain.value), t);
      p.breath.gain.linearRampToValueAtTime(hasEdge ? 0.07 : 0.035, t + 0.9);
      p.breath.gain.exponentialRampToValueAtTime(0.0001, t + 4.2);
      if (hasEdge) this._thump(0.85, 0.05);
      p.oscs.forEach(o => o.stop(t + 4.4));
    },
  };

  // ── La cancha: cada tiro es un partido simulado ──────────────────────────
  // Media cancha vista desde arriba, con el aro a la derecha. Los tiros salen
  // de distintos puntos, vuelan en parábola (la sombra marca el piso), y cada
  // uno suma su resultado a la distribución que se arma abajo como un marcador
  // de LEDs. Encima, la curva simétrica del libro; las columnas de la cola que
  // la superan se encienden en pistacho. El reloj de posesión muestra el
  // tiempo REAL que lleva la simulación en el servidor.
  const ORANGE = '#e97a2c';
  class Galton {
    constructor(canvas) {
      this.c = canvas;
      this.x = canvas.getContext('2d');
      this.home = lift(canvas.dataset.home);
      this.away = lift(canvas.dataset.away);
      this.mode = 'idle';
      this.final = null;
      this.reset();
    }
    reset() {
      this.bins = new Array(BINS).fill(0);
      this.parts = [];
      this.fx = [];
      this.landed = 0;
      this.glow = new Array(BINS).fill(0);
      this.spawnAcc = 0;
    }
    size() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = this.c.clientWidth || 300, h = this.c.clientHeight || 169;
      if (this.c.width !== Math.round(w * dpr) || this.c.height !== Math.round(h * dpr)) {
        this.c.width = Math.round(w * dpr); this.c.height = Math.round(h * dpr);
      }
      this.x.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.w = w; this.h = h;
      // Zona útil: a la derecha del texto de la cabecera (data-plot-left).
      this.L = w * (Number(this.c.dataset.plotLeft) || 0);
      this.stripH = Math.max(34, h * 0.24);            // marcador de LEDs abajo
      this.courtB = h - this.stripH - 6;                // borde inferior de la cancha
      // Media cancha NBA real (47 × 50 pies) encuadrada como una toma de TV:
      // llena el ancho del sector y muestra ~32 pies de alto (los laterales
      // quedan fuera de cuadro). Línea de fondo a la derecha.
      this.ft = Math.min((w - this.L - 12) / 47, this.courtB / 32);
      this.baseX = w - 10;
      this.midX = this.baseX - 47 * this.ft;
      this.hoopX = this.baseX - 5.25 * this.ft;
      this.hoopY = this.courtB / 2;
      this.R3 = 23.75 * this.ft;
      this.binW = (w - this.L - 20) / BINS;
    }
    // Un tiro = un partido simulado. Su valor sale de una mezcla de regímenes
    // (12 % "boom" que empuja a la derecha): la tesis en miniatura.
    spawn() {
      const boom = Math.random() < 0.12;
      const pr = boom ? 0.74 : 0.47;
      let k = 0;
      for (let r = 0; r < ROWS; r++) if (Math.random() < pr) k++;
      const bin = Math.round(k / ROWS * (BINS - 1));
      const th = Math.PI * (0.58 + Math.random() * 0.84);          // de arriba a abajo, por la izquierda
      const d = this.R3 * (0.28 + Math.random() * 0.82);
      let sx = this.hoopX + Math.cos(th) * d;
      let sy = this.hoopY + Math.sin(th) * d;
      sx = Math.max(this.midX + 8, Math.min(this.hoopX - 18, sx));
      sy = Math.max(8, Math.min(this.courtB - 8, sy));
      this.parts.push({
        sx, sy, bin, boom, t: 0,
        dur: 0.55 + Math.hypot(this.hoopX - sx, this.hoopY - sy) / 420,
        make: Math.random() < (boom ? 0.75 : 0.5),
      });
    }
    book() {
      // La curva del libro: Normal en la mediana del marcador, sigma del IQR.
      const n = this.bins.reduce((a, b) => a + b, 0);
      if (n < 30) return null;
      const q = (p) => { let acc = 0; for (let i = 0; i < BINS; i++) { acc += this.bins[i]; if (acc >= p * n) return i; } return BINS - 1; };
      const med = q(0.5), s = Math.max(1.2, (q(0.75) - q(0.25)) / 1.349);
      return { med, s, n };
    }
    frame(dt) {
      const { x, w, h } = this;
      this.size();
      x.clearRect(0, 0, w, h);
      this.backdrop(this.mode === 'simulating' ? 1 : 0.55);
      if (this.mode === 'final' || this.mode === 'resolving') this.drawFinal();
      if (this.mode !== 'final') this.drawBoard(dt);
    }
    backdrop(alpha) {
      const { x, w, h } = this;
      const g = x.createLinearGradient(0, 0, w, h);
      g.addColorStop(0, hex(this.away, 0.22 * alpha));
      g.addColorStop(0.5, 'rgba(10, 15, 22, 0)');
      g.addColorStop(1, hex(this.home, 0.22 * alpha));
      x.fillStyle = '#070a06'; x.fillRect(0, 0, w, h);
      x.fillStyle = g; x.fillRect(0, 0, w, h);
    }
    drawCourt() {
      const { x, L } = this, B = this.courtB, f = this.ft, hx = this.hoopX, hy = this.hoopY;
      const top = hy - 25 * f, bot = hy + 25 * f, mid = this.midX, base = this.baseX;
      // Parquet: tablas finas y una luz cálida sobre el aro.
      x.save();
      x.beginPath(); x.rect(L, 0, this.w - L, B); x.clip();
      x.fillStyle = '#120e0a'; x.fillRect(L, 0, this.w - L, B);
      x.strokeStyle = 'rgba(255, 220, 170, 0.035)'; x.lineWidth = 1;
      for (let y = 3; y < B; y += Math.max(5, f * 1.6)) { x.beginPath(); x.moveTo(L, y + .5); x.lineTo(this.w, y + .5); x.stroke(); }
      const spot = x.createRadialGradient(hx, hy, 4, hx, hy, this.R3 * 1.3);
      spot.addColorStop(0, 'rgba(255, 196, 120, 0.14)'); spot.addColorStop(1, 'rgba(255, 196, 120, 0)');
      x.fillStyle = spot; x.fillRect(L, 0, this.w - L, B);
      // Pintura (16 × 19 pies) con el color del local
      x.fillStyle = hex(this.home, 0.3);
      x.fillRect(base - 19 * f, hy - 8 * f, 19 * f, 16 * f);
      x.strokeStyle = 'rgba(240, 235, 225, 0.6)'; x.lineWidth = 1.2;
      x.strokeRect(mid, top, base - mid, bot - top);                                        // perímetro
      x.strokeRect(base - 19 * f, hy - 8 * f, 19 * f, 16 * f);                              // pintura
      x.beginPath(); x.arc(base - 19 * f, hy, 6 * f, Math.PI / 2, Math.PI * 1.5); x.stroke(); // tiro libre
      x.setLineDash([3, 4]);
      x.beginPath(); x.arc(base - 19 * f, hy, 6 * f, -Math.PI / 2, Math.PI / 2); x.stroke();
      x.setLineDash([]);
      // Triple: rectas en las esquinas (a 22 pies) y arco de 23,75
      const cy = 22 * f, ang = Math.asin(cy / this.R3), cornerX = hx - Math.cos(ang) * this.R3;
      x.beginPath(); x.moveTo(base, hy - cy); x.lineTo(cornerX, hy - cy);
      x.arc(hx, hy, this.R3, Math.PI + ang, Math.PI - ang, true);
      x.lineTo(base, hy + cy); x.stroke();
      // Mitad de cancha y círculo central
      x.beginPath(); x.arc(mid, hy, 6 * f, -Math.PI / 2, Math.PI / 2); x.stroke();
      x.fillStyle = hex(this.away, 0.35);
      x.beginPath(); x.arc(mid, hy, 2 * f, -Math.PI / 2, Math.PI / 2); x.fill();
      x.restore();
      // Tablero y aro
      x.strokeStyle = 'rgba(255,255,255,.85)'; x.lineWidth = 2;
      x.beginPath(); x.moveTo(base - 4 * f, hy - 3 * f); x.lineTo(base - 4 * f, hy + 3 * f); x.stroke();
      x.strokeStyle = ORANGE; x.lineWidth = 1.8;
      x.beginPath(); x.arc(hx, hy, Math.max(4, .75 * f), 0, Math.PI * 2); x.stroke();
    }
    drawBoard(dt) {
      const { x } = this;
      const fade = this.mode === 'resolving' ? Math.max(0, 1 - this.resolveT * 1.6) : 1;
      if (fade <= 0) return;
      x.save(); x.globalAlpha = fade;
      this.drawCourt();

      if (this.mode === 'simulating') {
        this.spawnAcc += dt * 34;
        while (this.spawnAcc > 1 && this.parts.length < 48) { this.spawn(); this.spawnAcc -= 1; }
      }
      const book = this.book();
      const alive = [];
      for (const p of this.parts) {
        p.t += dt / p.dur;
        if (p.t >= 1) {
          this.bins[p.bin] += 1; this.landed += 1;
          this.fx.push({ kind: p.make ? 'swish' : 'clank', t: 0 });
          const tail = !!(book && p.bin > book.med + book.s
            && this.bins[p.bin] / book.n > normPdf(p.bin, book.med, book.s) * 1.12);
          if (tail) { this.glow[p.bin] = 1; Audio.shimmer(); }
          continue;
        }
        this.ball(p);
        alive.push(p);
      }
      this.parts = alive;
      this.drawFx(dt);
      this.drawStrip(dt, book);
      this.drawClock();
      x.restore();
    }
    ball(p) {
      const { x } = this;
      const t = p.t, e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const gx = p.sx + (this.hoopX - p.sx) * e, gy = p.sy + (this.hoopY - p.sy) * e;
      const dist = Math.hypot(this.hoopX - p.sx, this.hoopY - p.sy);
      const z = 4 * t * (1 - t) * (10 + dist * 0.3);                   // altura del arco
      // sombra en el piso
      x.fillStyle = 'rgba(0,0,0,.38)';
      x.beginPath(); x.ellipse(gx + z * .18, gy + 2, 3.4, 1.7, 0, 0, Math.PI * 2); x.fill();
      // pelota
      const r = Math.max(3.4, this.ft * .62) + z * 0.05, bx = gx, by = gy - z;
      x.fillStyle = p.boom ? `rgb(${GREEN})` : ORANGE;
      x.beginPath(); x.arc(bx, by, r, 0, Math.PI * 2); x.fill();
      x.strokeStyle = 'rgba(20, 10, 4, .55)'; x.lineWidth = .8;
      x.beginPath(); x.moveTo(bx - r, by); x.lineTo(bx + r, by); x.stroke();
      x.beginPath(); x.arc(bx, by, r * .62, -Math.PI / 2, Math.PI / 2); x.stroke();
    }
    drawFx(dt) {
      const { x } = this, hx = this.hoopX, hy = this.hoopY;
      this.fx = this.fx.filter(f => (f.t += dt * 2.4) < 1);
      for (const f of this.fx) {
        const a = 1 - f.t;
        if (f.kind === 'swish') {
          x.strokeStyle = `rgba(${GREEN}, ${a * .9})`; x.lineWidth = 1.6;
          x.beginPath(); x.arc(hx, hy, 6.5 + f.t * 12, 0, Math.PI * 2); x.stroke();
          // la red que se sacude
          x.strokeStyle = `rgba(255,255,255,${a * .7})`; x.lineWidth = 1;
          for (let i = -2; i <= 2; i++) {
            x.beginPath(); x.moveTo(hx + i * 2.4, hy + 5); x.lineTo(hx + i * 1.6 + Math.sin(f.t * 18 + i) * 1.4, hy + 13); x.stroke();
          }
        } else {
          x.fillStyle = `rgba(255, 210, 160, ${a * .8})`;
          x.beginPath(); x.arc(hx - 7, hy - 4, 1.6 + f.t * 3, 0, Math.PI * 2); x.fill();
        }
      }
    }
    drawStrip(dt, book) {
      // Marcador de LEDs: la distribución que se va armando, tiro a tiro.
      const { x, w, h, L } = this, top = h - this.stripH, bottom = h - 8, H = bottom - top - 4;
      const left = L + 10, cw = this.binW;
      x.fillStyle = 'rgba(0,0,0,.55)'; x.fillRect(L, top - 4, w - L, h - top + 4);
      const max = Math.max(6, ...this.bins);
      const cells = Math.max(6, Math.floor(H / 4));
      for (let i = 0; i < BINS; i++) {
        const lit = Math.round(this.bins[i] / max * cells);
        const isTail = book && i > book.med + book.s
          && this.bins[i] / book.n > normPdf(i, book.med, book.s) * 1.12;
        this.glow[i] = Math.max(isTail ? 0.35 : 0, this.glow[i] - dt * 1.8);
        const col = i / (BINS - 1) < 0.5 ? this.away : this.home;
        for (let c = 0; c < cells; c++) {
          const on = c < lit;
          x.fillStyle = !on ? 'rgba(255,255,255,.04)'
            : isTail ? `rgba(${GREEN}, ${0.6 + this.glow[i] * 0.4})` : hex(col, 0.85);
          x.fillRect(left + i * cw + 1, bottom - (c + 1) * 4 + 1, Math.max(1, cw - 2), 3);
        }
      }
      if (book) {
        x.setLineDash([3, 3]); x.strokeStyle = 'rgba(255,255,255,0.6)'; x.lineWidth = 1.1;
        x.beginPath();
        for (let i = 0; i <= 120; i++) {
          const b = i / 120 * (BINS - 1);
          const y = bottom - normPdf(b, book.med, book.s) * book.n / max * cells * 4;
          const px = left + (b + 0.5) * cw;
          i ? x.lineTo(px, Math.max(top, y)) : x.moveTo(px, Math.max(top, y));
        }
        x.stroke(); x.setLineDash([]);
      }
    }
    drawClock() {
      // Reloj de posesión: el tiempo real que lleva la simulación.
      if (this.mode !== 'simulating') return;
      const { x, w } = this;
      const secs = Math.floor((performance.now() - (this.startedAt || performance.now())) / 1000);
      const txt = String(secs).padStart(2, '0');
      const bw = 44, bh = 30, bx = w - bw - 12, by = 8;
      x.fillStyle = '#0b0b0b'; x.fillRect(bx, by, bw, bh);
      x.strokeStyle = 'rgba(255,255,255,.12)'; x.strokeRect(bx + .5, by + .5, bw - 1, bh - 1);
      x.font = `900 24px 'Big Shoulders Display', 'Bebas Neue', sans-serif`;
      x.textAlign = 'center'; x.textBaseline = 'middle';
      x.fillStyle = 'rgba(255, 70, 50, .18)'; x.fillText('88', bx + bw / 2, by + bh / 2 + 1);
      x.fillStyle = '#ff5a3c'; x.shadowColor = 'rgba(255, 70, 50, .8)'; x.shadowBlur = 8;
      x.fillText(txt, bx + bw / 2, by + bh / 2 + 1);
      x.shadowBlur = 0;
      x.font = `700 8px 'Archivo', sans-serif`; x.fillStyle = 'rgba(255,255,255,.55)';
      x.fillText('SIM', bx + bw / 2, by + bh + 7);
      x.textAlign = 'start'; x.textBaseline = 'alphabetic';
    }
    // ── Cuadro final: la distribución real del mejor pick ───────────────────
    setFinal(row) {
      if (!row || !(Number(row.sim_cv) > 0) || !(Number(row.sim_mean) > 0)) { this.final = null; return; }
      const mean = Number(row.sim_mean), sd = Number(row.sim_cv) * mean;
      const skew = Math.max(0, Math.min(3, Number(row.sim_skew) || 0));
      const point = Number(row.point), main = Number(row.main_line);
      const over = String(row.side).toUpperCase() === 'OVER';
      const pc = Math.min(0.999, Math.max(0.001, Number(row.p_curve)));
      // σ del libro desde p_curve: P_libro(over) = 1 − Φ((punto − línea)/σ).
      const z = normInv(over ? 1 - pc : pc);
      const sBook = Math.abs(z) > 0.05 && point !== main ? Math.abs((point - main) / z) : sd;
      const lo = Math.max(0, Math.min(main, mean) - 2.6 * sd);
      const hi = Math.max(point + 0.6 * sd, mean + 3.4 * sd);
      this.final = {
        pdf: shapePdf(mean, sd, skew), main, sBook, point, over, lo, hi,
        player: row.player, label: `${row.market_label} ${over ? 'OVER' : 'UNDER'} ${point}`,
        pm: Number(row.p_model), pf: Number(row.p_fair), ev: Number(row.ev_pct),
      };
    }
    drawFinal() {
      const f = this.final, { x, w } = this;
      if (!f) return;
      const reveal = this.mode === 'resolving' ? Math.min(1, Math.max(0, (this.resolveT - 0.2) / 0.8)) : 1;
      const ease = 1 - Math.pow(1 - reveal, 3);
      const top = 34, base = this.h - 26, H = base - top;
      // data-plot-left: fracción del ancho que queda libre a la izquierda
      // (la cabecera del partido pone su texto ahí). Los rótulos van a la derecha.
      const L = w * (Number(this.c.dataset.plotLeft) || 0);
      const right = L > 0;
      const X = v => (v - f.lo) / (f.hi - f.lo) * (w - 20 - L) + 10 + L;
      const N = 160, xs = [], ym = [], yb = [];
      let mx = 0;
      for (let i = 0; i <= N; i++) {
        const v = f.lo + (f.hi - f.lo) * i / N;
        xs.push(v); ym.push(f.pdf(v)); yb.push(normPdf(v, f.main, f.sBook));
        mx = Math.max(mx, ym[i], yb[i]);
      }
      const Y = d => base - d / mx * H;
      x.save();
      x.beginPath(); x.rect(0, 0, L + 10 + (w - 20 - L) * ease, this.h); x.clip();
      // Área del modelo con los colores de los equipos
      const g = x.createLinearGradient(0, 0, w, 0);
      g.addColorStop(0, hex(this.away, 0.5)); g.addColorStop(1, hex(this.home, 0.5));
      x.fillStyle = g;
      x.beginPath(); x.moveTo(X(xs[0]), base);
      xs.forEach((v, i) => x.lineTo(X(v), Y(ym[i])));
      x.lineTo(X(xs[N]), base); x.closePath(); x.fill();
      // Región ganadora del pick, en verde
      x.fillStyle = `rgba(${GREEN}, 0.6)`;
      x.beginPath();
      let started = false;
      xs.forEach((v, i) => {
        const inside = f.over ? v >= f.point : v <= f.point;
        if (!inside) return;
        if (!started) { x.moveTo(X(v), base); started = true; }
        x.lineTo(X(v), Y(ym[i]));
      });
      if (started) {
        const edgeX = f.over ? X(xs[N]) : X(f.point);
        x.lineTo(edgeX, base); x.closePath(); x.fill();
      }
      // Contorno del modelo
      x.strokeStyle = 'rgba(235,240,245,0.9)'; x.lineWidth = 1.4;
      x.beginPath(); xs.forEach((v, i) => i ? x.lineTo(X(v), Y(ym[i])) : x.moveTo(X(v), Y(ym[i]))); x.stroke();
      // Curva del libro
      x.setLineDash([4, 3]); x.strokeStyle = 'rgba(255,255,255,0.6)'; x.lineWidth = 1.2;
      x.beginPath(); xs.forEach((v, i) => i ? x.lineTo(X(v), Y(yb[i])) : x.moveTo(X(v), Y(yb[i]))); x.stroke();
      x.setLineDash([]);
      // Línea alt
      x.strokeStyle = `rgba(${GREEN}, 1)`; x.lineWidth = 1.5;
      x.beginPath(); x.moveTo(X(f.point), top - 4); x.lineTo(X(f.point), base); x.stroke();
      x.restore();
      // Rótulos
      x.save(); x.globalAlpha = ease;
      x.font = '600 11px system-ui, sans-serif'; x.fillStyle = '#fff';
      x.shadowColor = 'rgba(0,0,0,.9)'; x.shadowBlur = 6;
      const tx = (t) => right ? w - 10 - x.measureText(t).width : 10;
      const who = String(f.player || '').slice(0, 26);
      x.fillText(who, tx(who), 16);
      x.font = '10px system-ui, sans-serif'; x.fillStyle = 'rgba(235,240,245,.85)';
      x.fillText(f.label, tx(f.label), 29);
      const txt = `modelo ${(f.pm * 100).toFixed(1)}% · libro ${(f.pf * 100).toFixed(1)}%`;
      x.fillStyle = `rgba(${GREEN}, 1)`; x.font = '600 10px system-ui, sans-serif';
      const tw = x.measureText(txt).width;
      const lx = Math.min(w - tw - 8, Math.max(8, X(f.point) + 6));
      x.fillText(txt, lx, top + 10);
      x.restore();
    }
  }

  // ── Orquestación: una animación por tarjeta, un solo bucle ────────────────
  const boards = new Map();   // canvas → Galton
  let raf = 0, last = 0;

  function loop(t) {
    const dt = Math.min(0.05, (t - (last || t)) / 1000);
    last = t;
    let busy = false;
    boards.forEach((b, c) => {
      if (!c.isConnected) {
        // La cabecera se redibujó: el canvas viejo suelta su parte del sonido.
        if (b.mode === 'simulating') Audio.padUse(-1);
        clearTimeout(b.pending);
        if (resizer) resizer.unobserve(c);
        boards.delete(c);
        return;
      }
      if (b.mode === 'resolving') {
        b.resolveT += dt / 1.4;
        if (b.resolveT >= 1) b.mode = 'final';
      }
      if (b.mode === 'simulating' || b.mode === 'resolving') { b.frame(dt); busy = true; }
    });
    raf = busy ? requestAnimationFrame(loop) : 0;
  }
  const kick = () => { if (!raf) { last = 0; raf = requestAnimationFrame(loop); } };
  // Un cuadro quieto (inicio o resultado) se dibuja una vez; si el lienzo
  // cambia de tamaño (se armó la página, rotó el teléfono, cambió la ventana)
  // hay que redibujarlo o queda estirado.
  const resizer = window.ResizeObserver ? new ResizeObserver(entries => {
    entries.forEach(en => {
      const b = boards.get(en.target);
      if (b && !en.target.isConnected) { resizer.unobserve(en.target); boards.delete(en.target); return; }
      if (b && (b.mode === 'final' || b.mode === 'idle') && en.contentRect.width > 0) b.frame(0);
    });
  }) : null;
  const get = (canvas) => {
    if (!canvas) return null;
    let b = boards.get(canvas);
    if (!b) {
      b = new Galton(canvas);
      boards.set(canvas, b);
      if (resizer) resizer.observe(canvas);
    }
    return b;
  };

  window.AlGalton = {
    /** Cuadro estático: pila tenue (idle) o el resultado real (done). */
    paint(canvas, row) {
      const b = get(canvas); if (!b) return;
      if (row) { b.setFinal(row); b.mode = b.final ? 'final' : 'idle'; }
      else b.mode = 'idle';
      if (b.mode === 'idle') {
        // Una pila de muestra para que la tarjeta no esté vacía.
        b.reset();
        b.size();
        for (let i = 0; i < 260; i++) { b.spawn(); const p = b.parts.pop(); b.bins[p.bin] += 1; }
      }
      b.frame(0);
    },
    start(canvas) {
      const b = get(canvas); if (!b) return;
      b.reset(); b.final = null;
      if (!MOTION_OK) { b.mode = 'idle'; b.frame(0); return; }
      b.mode = 'simulating';
      b.startedAt = performance.now();
      clearTimeout(b.pending);
      Audio.ensure(); Audio.padUse(+1);
      kick();
    },
    /** Termina: se disuelve hacia el resultado real (o se apaga si no hay). */
    finish(canvas, row) {
      const b = get(canvas); if (!b) return;
      const shown = performance.now() - (b.startedAt || 0);
      if (b.mode === 'simulating' && !document.hidden && MOTION_OK && shown < MIN_BOARD_MS) {
        clearTimeout(b.pending);
        b.pending = setTimeout(() => this.finish(canvas, row), MIN_BOARD_MS - shown);
        return;
      }
      const wasRunning = b.mode === 'simulating';
      if (wasRunning) Audio.padUse(-1);
      b.setFinal(row);
      // Pestaña en segundo plano: no hay cuadros que animar, el resultado se
      // dibuja directo (si no, la tarjeta volvería a la vista a medio fundir).
      if (!MOTION_OK || !wasRunning || document.hidden) {
        b.mode = b.final ? 'final' : 'idle'; b.frame(0); return;
      }
      b.mode = 'resolving'; b.resolveT = 0;
      Audio.resolve(!!b.final && Number(row && row.ev_pct) > 0);
      if (!b.final) { b.mode = 'idle'; b.frame(0); return; }
      kick();
    },
    stop(canvas) {
      const b = get(canvas); if (!b) return;
      clearTimeout(b.pending);
      if (b.mode === 'simulating') Audio.padUse(-1);
      b.mode = 'idle'; b.frame(0);
    },
    soundOn: () => Audio.on,
    setSound(v) { Audio.setOn(v); if (v) Audio.ensure(); },
    unlock() { Audio.ensure(); },
  };
})();
