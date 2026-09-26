/* Blackboard renderer.
 *
 * The lesson videos are rendered ahead of time; this cannot be. A student asking a
 * question will not wait 40 seconds for a render, so the board is driven by a stroke
 * PROTOCOL: the tutor emits drawing ops and the client animates them. That is also what
 * makes it read as a teacher rather than a slide — writing appears stroke by stroke, at
 * a human pace, while the sentence is still being spoken.
 *
 * All coordinates are normalised 0..1, so one command stream renders identically on a
 * phone, a laptop and a 75" smart board.
 */

/* ---- semantic tokens ----------------------------------------------------
 * Colour carries meaning and always the same meaning. Every role also has a glyph,
 * because colour alone fails on a washed-out projector, in greyscale, and for a
 * colour-blind student. Roles are the only sanctioned way to colour a line; raw hex
 * from the model is mapped back onto the nearest role.
 */
const cssVar = (n, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  return v || fallback;
};
export const ROLES = {
  term:     { color: cssVar('--role-term', '#1568d4'),     glyph: '◆', label: 'term' },
  explain:  { color: cssVar('--role-explain', '#16202b'),  glyph: '',  label: 'explanation' },
  example:  { color: cssVar('--role-example', '#c2620a'),  glyph: '✎', label: 'example' },
  tip:      { color: cssVar('--role-tip', '#0f7a4a'),      glyph: '➜', label: 'tip' },
  question: { color: cssVar('--role-question', '#c2185b'), glyph: '?', label: 'question' },
  trap:     { color: cssVar('--role-trap', '#c62828'),     glyph: '⚠', label: 'common mistake' },
  result:   { color: cssVar('--role-result', '#7b4bc9'),   glyph: '=', label: 'key result' },
};
const ROLE_OF_HEX = {
  '#7cc4ff': 'term', '#f2f0e6': 'explain', '#ffb060': 'example',
  '#7fd493': 'tip', '#ff9ec4': 'question', '#ff6b6b': 'trap', '#ffe66d': 'result',
};

const CHALK = '#16202b';
/* Canvas does NOT resolve CSS custom properties. `ctx.font = "24px var(--font-hand)"`
 * is an invalid font shorthand, so the assignment is silently DISCARDED and the context
 * keeps its default 10px sans-serif — which is what every board glyph was drawn at, at
 * every size, for as long as this file has existed. The variable is resolved to a real
 * family list here, once, and setFont refuses to fail quietly again. */
const FONT_FALLBACK = '"Kalam","Chalkboard SE","Bradley Hand",cursive';
let _fontStack = null;
function fontStack() {
  if (_fontStack === null) _fontStack = cssVar('--font-hand', FONT_FALLBACK) || FONT_FALLBACK;
  return _fontStack;
}
export function setFont(c, px) {
  c.font = `${px}px ${fontStack()}`;
  if (!c.font.startsWith(`${px}px`) && !c.font.includes(`${px}px`)) {
    c.font = `${px}px ${FONT_FALLBACK}`;          // the stack itself was unusable
  }
  return c.font;
}

// A hand does not draw straight lines. Perturbing every path by a small amount, with a
// fixed seed per op so a redraw is stable, is most of what separates "chalk" from "plot".
function jitter(pts, amount, seed) {
  let s = seed;
  const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff - 0.5;
  return pts.map(([x, y]) => [x + rnd() * amount, y + rnd() * amount]);
}

function lerpPts(a, b, n) {
  const out = [];
  for (let i = 0; i <= n; i++) out.push([a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n]);
  return out;
}

export class Board {
  constructor(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.queue = [];
    this.done = [];          // ops already fully drawn, repainted every frame
    this.active = null;
    this.t0 = 0;
    this.running = false;
    this.onIdle = null;
    // Global pace control. 1 = a teacher's speed; higher is used for
    // headless capture and for a 'show me the whole board' replay.
    this.speed = 1;
    this.W = 0; this.H = 0;          // never NaN: callers divide by these
    this.view = { x: 0, y: 0, z: 1 };  // pan in world units, uniform zoom
    this.user = [];                    // the student's own marks, never cleared by the tutor
    this.resize();
    // A ResizeObserver, not a window listener: the board also changes size when the
    // transcript drawer opens and when the stage swaps modes, neither of which resizes
    // the window. Measuring once at construction gave every font a stale basis.
    if (window.ResizeObserver) {
      new ResizeObserver(() => this.resize()).observe(this.cv);
    } else {
      window.addEventListener('resize', () => this.resize());
    }
  }

  resize() {
    let r = this.cv.getBoundingClientRect();
    if (!r.width || !r.height) {
      // The canvas has not been laid out yet — it is display:none behind mode-play, or
      // the grid has not run. Fall back to its container so the basis is never zero,
      // and let the observer correct it on the next frame.
      const p = this.cv.parentElement;
      r = p ? p.getBoundingClientRect() : r;
      if (!r.width || !r.height) return;
    }
    const dpr = window.devicePixelRatio || 1;
    if (Math.abs(this.W - r.width) < 1 && Math.abs(this.H - r.height) < 1) return;
    this.cv.width = r.width * dpr;
    this.cv.height = r.height * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = this.cv.width / dpr;
    this.H = this.cv.height / dpr;
    this.paint();
  }

  /* World space. The board is infinite: (0,0)–(1,1) is simply the first screenful,
   * and everything beyond it is reached by panning. Because the mapping is affine and
   * axis-aligned, every existing op keeps working unchanged — with the identity view
   * these reduce to exactly what they were. Widths scale with W and heights with H, as
   * they always did, so the board stays anisotropic in the way the ops expect while
   * zoom stays uniform. */
  X(u) { return (u - this.view.x) * this.W * this.view.z; }
  Y(v) { return (v - this.view.y) * this.H * this.view.z; }
  SW(k) { return k * this.W * this.view.z; }
  SH(k) { return k * this.H * this.view.z; }

  /* Screen pixels back to world units — what a pointer event means on an infinite
   * canvas that has been panned and zoomed. */
  toWorld(px, py) {
    return [px / (this.W * this.view.z) + this.view.x,
            py / (this.H * this.view.z) + this.view.y];
  }

  panBy(dxPx, dyPx) {
    this.view.x -= dxPx / (this.W * this.view.z);
    this.view.y -= dyPx / (this.H * this.view.z);
    this.paint();
  }

  /* Zoom about a point, so the thing under the cursor stays under the cursor. */
  zoomAt(px, py, factor) {
    const z = Math.min(4, Math.max(0.25, this.view.z * factor));
    if (z === this.view.z) return;
    const [wx, wy] = this.toWorld(px, py);
    this.view.z = z;
    this.view.x = wx - px / (this.W * z);
    this.view.y = wy - py / (this.H * z);
    this.paint();
  }

  /* Back to the page the tutor writes on. */
  home() { this.view = { x: 0, y: 0, z: 1 }; this.paint(); }

  /* Frame everything that has been drawn, the tutor's work and the student's alike. */
  fit() {
    const b = this.bounds();
    if (!b) return this.home();
    const pad = 0.06;
    const w = Math.max(0.2, b.x1 - b.x0 + pad * 2);
    const h = Math.max(0.2, b.y1 - b.y0 + pad * 2);
    this.view.z = Math.min(4, Math.max(0.25, Math.min(1 / w, 1 / h)));
    this.view.x = b.x0 - pad - (1 / this.view.z - w) / 2;
    this.view.y = b.y0 - pad - (1 / this.view.z - h) / 2;
    this.paint();
  }

  bounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const add = (u, v) => {
      x0 = Math.min(x0, u); y0 = Math.min(y0, v);
      x1 = Math.max(x1, u); y1 = Math.max(y1, v);
    };
    for (const o of this.done) {
      for (const k of ['at', 'from', 'to']) if (o[k]) add(o[k][0], o[k][1]);
    }
    for (const it of this.user) {
      if (it.kind === 'stroke') for (const [u, v] of it.pts) add(u, v);
      else if (it.at) { add(it.at[0], it.at[1]); add(it.at[0] + (it.w || 0.2), it.at[1] + (it.h || 0.1)); }
    }
    return Number.isFinite(x0) ? { x0, y0, x1, y1 } : null;
  }

  push(ops) {
    for (const op of [].concat(ops)) this.queue.push({ ...op, seed: (Math.random() * 1e9) | 0 });
    if (!this.running) this.start();
  }

  /* Write the queued answer in about `seconds`, whatever its length.
   *
   * A fixed 55ms per character is a believable hand, but it makes the board's pace a
   * function of how wordy the answer happened to be — and once the server started
   * wrapping long lines, a typical answer grew from four ops to nine and the writing
   * ran twenty-five seconds against a fifteen-second voice. The student hears the
   * answer finish, asks the next question, and clearAll() wipes a board that was still
   * half-written: from their side the board simply never draws.
   *
   * So the pace is set per answer instead. Bounded at both ends, because a teacher who
   * writes too fast is illegible and one who writes too slowly is what we just fixed.
   */
  pace(seconds = 12) {
    const at1 = [...this.queue, ...(this.active ? [this.active] : [])]
      .reduce((t, op) => t + this.durationOf({ ...op, speed: 1 }) * (this.speed || 1), 0);
    this.speed = Math.min(4, Math.max(0.8, at1 / (seconds * 1000)));
    if (this.active) this.active.dur = this.durationOf(this.active);
    return this.speed;
  }

  /* Render ops fully drawn, with no animation. Used to restore a board after a
   * resize or a reconnect, and for deterministic headless capture — the animated path
   * needs one frame per op, which a virtual-time browser will not always run. */
  pushInstant(ops) {
    for (const op of [].concat(ops)) {
      if (op.op === 'clear') { this.done = []; continue; }
      this.done.push({ ...op, seed: (Math.random() * 1e9) | 0 });
    }
    this.paint();
  }

  clearAll() {
    this.queue = []; this.done = []; this.active = null;
    this.focusRole = null;
    this.paint();
  }

  /* Dim everything that is not the role being asked about. A board with eight lines on
   * it is a lot to scan when you only want the one thing you got wrong. */
  spotlight(role) {
    this.focusRole = (this.focusRole === role) ? null : role;
    this.paint();
    return this.focusRole;
  }

  /* Which roles are actually written on the board, and how many lines each has. The
   * legend is built from this, so it is a key to what is there rather than a catalogue
   * of everything the system could in principle draw. */
  roleCounts() {
    const n = {};
    for (const o of this.done) {
      if (o.op !== 'write' || o.cont) continue;
      const r = Board.resolveRole(o);
      if (r) n[r] = (n[r] || 0) + 1;
    }
    return n;
  }

  start() {
    this.running = true;
    const step = (ts) => {
      if (!this.active) {
        if (!this.queue.length) {
          this.running = false;
          this.paint();
          if (this.onIdle) this.onIdle();
          return;
        }
        this.active = this.queue.shift();
        this.active.dur = this.durationOf(this.active);
        this.t0 = ts;
      }
      const p = Math.min(1, (ts - this.t0) / Math.max(1, this.active.dur));
      this.paint(this.active, p);
      if (p >= 1) {
        if (this.active.op === 'clear') this.done = [];
        else if (this.active.op === 'erase') this.done.push(this.active);
        else this.done.push(this.active);
        this.active = null;
      }
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // Pace is what sells it: a teacher writes at roughly 8-12 characters a second and
  // pauses between shapes. Instant text reads as a slide.
  durationOf(op) {
    const speed = (op.speed || 1) * (this.speed || 1);
    switch (op.op) {
      // A wrapped line is the same sentence continuing, not a new one being started,
      // so it does not pay the 250ms it takes to put chalk to board. Server-side
      // wrapping turned every long line into two or three ops and the preamble was
      // being charged for each of them.
      case 'write': return ((op.cont ? 0 : 250) + (op.text || '').length * 55) / speed;
      case 'draw': return (['sphere3d','dumbbell3d','axes3d'].includes(op.shape) ? 1100 : 700) / speed;
      case 'underline': return 380 / speed;
      case 'erase': return 450;
      case 'clear': return 350;
      case 'pause': return op.ms || 400;
      default: return 300;
    }
  }

  /* ---- the student's layer -------------------------------------------------
   * A board a student cannot write on is a slide. These items live in the same world
   * coordinates as the tutor's ops but in their own list, so `clearAll()` between
   * questions wipes the answer and leaves the student's working untouched. */
  addUser(item) {
    this.user.push({ id: (this._uid = (this._uid || 0) + 1), ...item });
    this.paint();
    if (this.onUserChange) this.onUserChange();
    return this.user[this.user.length - 1];
  }

  removeUser(id) {
    const i = this.user.findIndex(u => u.id === id);
    if (i < 0) return false;
    this.user.splice(i, 1);
    this.paint();
    if (this.onUserChange) this.onUserChange();
    return true;
  }

  clearUser() {
    this.user = [];
    this.paint();
    if (this.onUserChange) this.onUserChange();
  }

  /* Hit test in world units, tolerance given in screen pixels so it feels the same at
   * every zoom level. Topmost item wins, because that is the one you can see. */
  pick(px, py, tolPx = 10) {
    const [u, v] = this.toWorld(px, py);
    const tu = tolPx / (this.W * this.view.z), tv = tolPx / (this.H * this.view.z);
    for (let i = this.user.length - 1; i >= 0; i--) {
      const it = this.user[i];
      if (it.kind === 'stroke') {
        for (const [a, b] of it.pts) {
          if (Math.abs(a - u) < tu * 1.5 && Math.abs(b - v) < tv * 1.5) return it;
        }
      } else {
        const w = it.w || 0.2, h = it.h || 0.08;
        if (u >= it.at[0] - tu && u <= it.at[0] + w + tu &&
            v >= it.at[1] - h - tv && v <= it.at[1] + tv) return it;
      }
    }
    return null;
  }

  /* A region of the board as a PNG, for asking the tutor about it. Read from the
   * backing store rather than re-rendered, so what is sent is exactly what the student
   * highlighted — their own marks included. Capped on the long edge: past about a
   * thousand pixels the model gains nothing and the round trip gets slower. */
  crop(x0, y0, x1, y1, maxEdge = 1100) {
    const dpr = window.devicePixelRatio || 1;
    const sx = Math.min(this.X(x0), this.X(x1)) * dpr;
    const sy = Math.min(this.Y(y0), this.Y(y1)) * dpr;
    const sw = Math.abs(this.X(x1) - this.X(x0)) * dpr;
    const sh = Math.abs(this.Y(y1) - this.Y(y0)) * dpr;
    if (sw < 4 || sh < 4) return null;
    const k = Math.min(1, maxEdge / Math.max(sw, sh));
    const out = document.createElement('canvas');
    out.width = Math.round(sw * k); out.height = Math.round(sh * k);
    const oc = out.getContext('2d');
    oc.fillStyle = cssVar('--board', '#fbfcfd');
    oc.fillRect(0, 0, out.width, out.height);
    oc.drawImage(this.cv, sx, sy, sw, sh, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  }

  drawUser() {
    const c = this.ctx;
    for (const it of this.user) {
      c.save();
      if (it.kind === 'stroke') {
        c.strokeStyle = it.color || cssVar('--ink', '#16202b');
        c.lineWidth = Math.max(1, (it.weight || 3) * this.view.z);
        c.lineCap = 'round'; c.lineJoin = 'round';
        c.beginPath();
        it.pts.forEach(([u, v], i) =>
          i ? c.lineTo(this.X(u), this.Y(v)) : c.moveTo(this.X(u), this.Y(v)));
        c.stroke();
      } else if (it.kind === 'text') {
        c.fillStyle = it.color || cssVar('--ink', '#16202b');
        const size = this.SH(it.size || 0.04);
        setFont(c, size);
        c.textBaseline = 'alphabetic';
        it.text.split('\n').forEach((line, i) =>
          c.fillText(line, this.X(it.at[0]), this.Y(it.at[1]) + i * size * 1.3));
      } else if (it.kind === 'image' && it.img) {
        c.drawImage(it.img, this.X(it.at[0]), this.Y(it.at[1]),
                    this.SW(it.w), this.SH(it.h));
      }
      c.restore();
    }
  }

  paint(active, p) {
    // Self-healing basis. Every size and coordinate on the board is a fraction of W/H,
    // so painting against a stale or zero basis silently mis-renders everything. The
    // ResizeObserver covers the normal case; this covers the first paint, when the grid
    // may not have run yet, and any paint that beats the observer to it.
    if (!(this.W > 0 && this.H > 0)) this.resize();
    const c = this.ctx;
    c.save();
    c.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
    this.background();
    this.drawUser();                       // under the tutor: the tutor writes on top
    for (const op of this.done) this.drawOp(op, 1);
    if (active) this.drawOp(active, p);
    c.restore();
    if (this.onPaint) this.onPaint();       // overlays anchored in world coordinates
  }

  background() {
    const c = this.ctx;
    c.fillStyle = cssVar('--board', '#fbfcfd');
    c.fillRect(0, 0, this.W, this.H);
    // A dot grid rather than ruling. Ruling was drawn at a fixed pitch that never
    // matched the line height, so the writing sat off the lines; dots carry no
    // baseline to be off, and on an infinite canvas they are what makes a pan
    // legible as movement rather than as a redraw.
    const step = this.SH(0.06);
    if (step > 9) {
      c.fillStyle = cssVar('--board-line', '#e6ebf0');
      const ox = -this.X(0) % step, oy = -this.Y(0) % step;
      for (let x = ((ox % step) + step) % step; x < this.W; x += step)
        for (let y = ((oy % step) + step) % step; y < this.H; y += step)
          c.fillRect(x, y, 1.5, 1.5);
    }
  }

  static resolveRole(op) {
    let role = op.role;
    if (!role && op.color) role = ROLE_OF_HEX[String(op.color).toLowerCase()];
    if (!ROLES[role]) role = op.op === 'write' ? 'explain' : null;
    return role;
  }

  drawOp(op, p) {
    if (this.focusRole) {
      const r = op.op === 'write' ? Board.resolveRole(op) : null;
      this.ctx.globalAlpha = (r === this.focusRole) ? 1 : 0.16;
    } else {
      this.ctx.globalAlpha = 1;
    }
    const c = this.ctx;
    const role = Board.resolveRole(op);
    const tone = role ? ROLES[role].color : (op.color || CHALK);
    c.save();
    c.strokeStyle = tone;
    c.fillStyle = tone;
    c.lineWidth = (op.weight || 2.6) * this.view.z;
    c.lineCap = 'round';
    c.lineJoin = 'round';
    c.shadowBlur = 0;

    switch (op.op) {
      case 'write': this.write(op, p); break;
      case 'draw':
        if (['sphere3d', 'dumbbell3d', 'axes3d'].includes(op.shape)) this.proj3d(op, p);
        else this.shape(op, p);
        break;
      case 'underline': this.underline(op, p); break;
      case 'erase': this.erase(op, p); break;
      case 'sprite': this.sprite(op, p); break;
      default: break;
    }
    c.restore();
  }

  /* Text. Revealed left-to-right behind a moving chalk tip, word by word, with a small
   * baseline wobble per word. A clip-based reveal on a chalk font is indistinguishable
   * from stroke drawing at reading distance and needs no font outline data, which keeps
   * this dependency-free and offline-capable. */
  write(op, p) {
    const c = this.ctx;
    const size = this.SH(op.size || 0.045);
    setFont(c, size);
    c.textBaseline = 'alphabetic';
    const x = this.X(op.at[0]);
    const y = this.Y(op.at[1]);
    const role = Board.resolveRole(op);
    // A wrapped line carries its role's colour but not a second copy of its glyph.
    const glyph = (!op.cont && role && ROLES[role].glyph) ? ROLES[role].glyph + ' ' : '';
    const text = (op.cont ? '   ' : '') + glyph + (op.text || '');
    // Backstop for the server's character-budget wrap: if a line still overruns its
    // column — a long unbroken token, a script with wider advances — shrink to fit
    // rather than run across the diagram gutter.
    let total = c.measureText(text).width;
    const avail = this.SW(op.maxw || 0.88);
    if (total > avail) {
      setFont(c, size * Math.max(0.62, avail / total));
      total = c.measureText(text).width;
    }

    c.save();
    c.beginPath();
    c.rect(x - 4, y - size * 1.25, total * p + 6, size * 1.8);
    c.clip();
    // wobble: a couple of pixels of drift makes a straight baseline look written
    let cx = x;
    for (const word of text.split(/(\s+)/)) {
      const w = c.measureText(word).width;
      const dy = Math.sin((cx + op.seed) * 0.05) * size * 0.02;
      c.fillText(word, cx, y + dy);
      cx += w;
    }
    c.restore();

    if (p < 1) {                                   // the chalk tip
      c.save();
      c.globalAlpha = 0.9;
      c.beginPath();
      c.arc(x + total * p, y - size * 0.28, size * 0.07, 0, Math.PI * 2);
      c.fill();
      c.restore();
    }
  }

  shape(op, p) {
    const c = this.ctx;
    let pts;
    const a = op.from ? [this.X(op.from[0]), this.Y(op.from[1])] : null;
    const b = op.to ? [this.X(op.to[0]), this.Y(op.to[1])] : null;

    switch (op.shape) {
      case 'line':
      case 'arrow':
        pts = lerpPts(a, b, 16); break;
      case 'circle': {
        const cx = this.X(op.at[0]), cy = this.Y(op.at[1]);
        const r = this.SH(op.r || 0.06);
        pts = [];
        for (let i = 0; i <= 40; i++) {
          const th = i / 40 * Math.PI * 2;
          pts.push([cx + Math.cos(th) * r * 1.04, cy + Math.sin(th) * r]);
        }
        break;
      }
      case 'rect': {
        const x0 = this.X(op.from[0]), y0 = this.Y(op.from[1]);
        const x1 = this.X(op.to[0]), y1 = this.Y(op.to[1]);
        pts = [...lerpPts([x0, y0], [x1, y0], 6), ...lerpPts([x1, y0], [x1, y1], 6),
               ...lerpPts([x1, y1], [x0, y1], 6), ...lerpPts([x0, y1], [x0, y0], 6)];
        break;
      }
      case 'axes': {
        const x0 = this.X(op.at[0]), y0 = this.Y(op.at[1]);
        const w = this.SW(op.w || 0.3), h = this.SH(op.h || 0.22);
        pts = [...lerpPts([x0, y0], [x0 + w, y0], 10), ...lerpPts([x0, y0], [x0, y0 - h], 10)];
        break;
      }
      default: pts = a && b ? lerpPts(a, b, 12) : [];
    }

    pts = jitter(pts, (op.rough ?? 1.6), op.seed);
    const n = Math.max(2, Math.floor(pts.length * p));
    c.beginPath();
    c.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < n; i++) c.lineTo(pts[i][0], pts[i][1]);
    c.stroke();

    if (op.shape === 'arrow' && p > 0.85) {        // head last, as a hand would
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const hl = 12;
      c.beginPath();
      c.moveTo(b[0], b[1]);
      c.lineTo(b[0] - hl * Math.cos(ang - 0.4), b[1] - hl * Math.sin(ang - 0.4));
      c.moveTo(b[0], b[1]);
      c.lineTo(b[0] - hl * Math.cos(ang + 0.4), b[1] - hl * Math.sin(ang + 0.4));
      c.stroke();
    }
    if (op.label && p > 0.6) {
      const size = this.SH(0.03);
      setFont(c, size);
      const lx = op.labelAt ? this.X(op.labelAt[0]) : (a ? a[0] : this.X(op.at[0]));
      const ly = op.labelAt ? this.Y(op.labelAt[1]) : (a ? a[1] - 10 : this.Y(op.at[1]));
      c.globalAlpha = Math.min(1, (p - 0.6) / 0.4);
      c.fillText(op.label, lx, ly);
    }
  }

  /* A board is two-dimensional. Saying "look at the 3D sphere" while drawing a flat
   * circle is worse than saying nothing, so anything spatial is drawn in an explicit
   * projection: a sphere gets meridians and a latitude ellipse, an orbital gets
   * foreshortened lobes, axes get an isometric z. This is exactly how a teacher fakes
   * depth in chalk. */
  proj3d(op, p) {
    const c = this.ctx;
    const cx = this.X(op.at[0]), cy = this.Y(op.at[1]);
    const r = this.SH(op.r || 0.10);
    const ell = (rx, ry, rot, frac) => {
      c.beginPath();
      const n = Math.max(2, Math.floor(48 * frac));
      for (let i = 0; i <= n; i++) {
        const t = i / 48 * Math.PI * 2;
        const x = Math.cos(t) * rx, y = Math.sin(t) * ry;
        c.lineTo(cx + x * Math.cos(rot) - y * Math.sin(rot),
                 cy + x * Math.sin(rot) + y * Math.cos(rot));
      }
      c.stroke();
    };

    if (op.shape === 'sphere3d') {
      ell(r, r, 0, Math.min(1, p * 2));                       // outline
      if (p > 0.45) {
        c.globalAlpha = 0.55;
        ell(r * 0.42, r, 0, (p - 0.45) / 0.55);               // meridian
        ell(r, r * 0.34, 0, (p - 0.45) / 0.55);               // equator
        c.globalAlpha = 1;
      }
    } else if (op.shape === 'dumbbell3d') {
      // two foreshortened lobes meeting at the nucleus, plus the nodal plane as an
      // ellipse — the plane is what a flat circle can never show
      const lobe = (sign, frac) => {
        c.beginPath();
        const n = Math.max(2, Math.floor(40 * frac));
        for (let i = 0; i <= n; i++) {
          const t = -Math.PI / 2 + (i / 40) * Math.PI;
          const rr = r * Math.abs(Math.cos(t / 2));
          c.lineTo(cx + Math.sin(t) * rr * 0.75,
                   cy + sign * (r * 1.35) * (0.5 - Math.cos(t) * 0.5));
        }
        c.stroke();
      };
      lobe(-1, Math.min(1, p * 2)); lobe(1, Math.min(1, p * 2));
      if (p > 0.5) { c.globalAlpha = 0.6; ell(r * 1.25, r * 0.30, 0, (p - 0.5) / 0.5); c.globalAlpha = 1; }
    } else if (op.shape === 'axes3d') {
      const L = this.SH(op.r || 0.12);
      const seg = ([dx, dy], frac, lbl) => {
        c.beginPath(); c.moveTo(cx, cy);
        c.lineTo(cx + dx * L * frac, cy + dy * L * frac); c.stroke();
        if (frac > 0.9 && lbl) {
          setFont(c, this.SH(0.028));
          c.fillText(lbl, cx + dx * L * 1.12, cy + dy * L * 1.12);
        }
      };
      seg([1, 0], Math.min(1, p * 3), 'x');
      seg([0, -1], Math.min(1, Math.max(0, p * 3 - 1)), 'z');
      seg([-0.62, 0.5], Math.min(1, Math.max(0, p * 3 - 2)), 'y');   // isometric depth
    }
  }

  underline(op, p) {
    const c = this.ctx;
    const x0 = this.X(op.from[0]), x1 = this.X(op.to[0]), y = this.Y(op.from[1]);
    const pts = jitter(lerpPts([x0, y], [x1, y], 12), 1.4, op.seed);
    const n = Math.max(2, Math.floor(pts.length * p));
    c.strokeStyle = ROLES[op.role]?.color || ROLES.term.color;
    c.beginPath();
    c.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < n; i++) c.lineTo(pts[i][0], pts[i][1]);
    c.stroke();
  }

  // A duster smears rather than deletes; painting the background back with a soft edge
  // reads far more like a classroom than a hard rectangle clear.
  erase(op, p) {
    const c = this.ctx;
    const [x0, y0, x1, y1] = op.region;
    const X0 = this.X(x0), Y0 = this.Y(y0);
    const w = (this.X(x1) - X0) * p, h = this.Y(y1) - Y0;
    c.save();
    c.shadowBlur = 0;
    c.globalAlpha = 0.95;
    c.fillStyle = cssVar('--board', '#fbfcfd');
    c.fillRect(X0, Y0, w, h);
    c.restore();
  }

  sprite(op, p) {
    const img = Board._sprites?.[op.name];
    if (!img) return;
    const c = this.ctx;
    c.globalAlpha = p;
    const w = this.SW(op.w || 0.25);
    const h = w * (img.height / img.width);
    c.drawImage(img, this.X(op.at[0]), this.Y(op.at[1]), w, h);
  }
}

Board._sprites = {};
export function registerSprite(name, img) { Board._sprites[name] = img; }

/* What a diagram means, in words. A projection of a sphere is not self-describing, and
 * a student using a screen reader is exactly the student who cannot infer it from the
 * strokes. These read as a teacher would say them out loud, not as shape names. */
const SHAPE_ALT = {
  sphere3d: 'a sphere, drawn with a meridian and an equator to show it is round in '
    + 'every direction',
  dumbbell3d: 'a dumbbell: two lobes on opposite sides of the nucleus, with the nodal '
    + 'plane between them seen edge-on',
  axes3d: 'x, y and z axes drawn in isometric projection',
  circle: 'a circle', rect: 'a rectangle', line: 'a line',
  arrow: 'an arrow', axes: 'a pair of axes',
};

/* A text equivalent of the board, rebuilt from the ops rather than from the pixels.
 * The canvas is labelled by this, so the board's content is available to a screen
 * reader in the order it was written, with each line's role named — the same
 * information the colour and the glyph carry for everyone else. */
export function describeBoard(ops) {
  const parts = [];
  for (const op of ops) {
    if (op.op === 'write') {
      const text = String(op.text || '').trim();
      if (!text) continue;
      if (op.cont && parts.length) { parts[parts.length - 1] += ' ' + text; continue; }
      const role = Board.resolveRole(op);
      const label = role && ROLES[role] ? ROLES[role].label : '';
      parts.push(label && label !== 'explanation' ? `${label}: ${text}` : text);
    } else if (op.op === 'draw') {
      const alt = SHAPE_ALT[op.shape];
      if (alt) parts.push(`Diagram — ${alt}${op.label ? `, labelled ${op.label}` : ''}.`);
    }
  }
  if (!parts.length) return 'The board is empty.';
  return `Board, ${parts.length} item${parts.length > 1 ? 's' : ''}. `
    + parts.map(p => (/[.!?]$/.test(p) ? p : p + '.')).join(' ');
}
