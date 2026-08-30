/* Classroom app: pre-recorded lesson + live, grounded blackboard Q&A. */
import { Board, ROLES, setFont, describeBoard } from '/board.js';

const $ = (id) => document.getElementById(id);

/* ---- where the tutor lives ------------------------------------------------
 * On the web this is the page's own origin and nothing changes. Inside the Android
 * shell the page is served from the APK, so the lesson data, the media and the socket
 * all have to be pointed somewhere real — and that address changes (a tunnel hands out
 * a new one every restart), so it is settable at runtime rather than baked in at build
 * time. Nobody should need a rebuild to move a demo.
 */
const PACKAGED = location.protocol === 'file:' || location.protocol === 'capacitor:';
function apiBase() {
  const saved = (() => { try { return localStorage.getItem('saarthi.server'); }
                         catch { return null; } })();
  return (saved || (PACKAGED ? '' : location.origin)).replace(/\/$/, '');
}
const api = (path) => apiBase() + path;
function setApiBase(url) {
  try { localStorage.setItem('saarthi.server', (url || '').trim().replace(/\/$/, '')); }
  catch { /* no storage; this session only */ }
}
const stage = $('stage'), video = $('video');
const board = new Board($('board'));

let man = null, live = null, jumpTarget = null;
/* ---- notes ----------------------------------------------------------------
 * The transcript used to be a log: this session only, ordered by clock, duplicating
 * what was already on the board, and you could not do anything with an entry. That is
 * noise.
 *
 * These are notes instead. Every question keeps the board that was drawn for it, they
 * are stored per lesson and survive the tab closing, and tapping one puts that board
 * back — so a student who asked six things last week can find the third one and read
 * it again. The questions become the notebook.
 */
let notes = [];
let muted = false, retries = 0, retryTimer = 0;
let turn = 0, waitTimer = 0;            // the turn in flight, and its watchdog

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/* ---- conversational state -----------------------------------------------
 * The learner must always know what the system is doing. Latency without a signal
 * reads as a broken app; the same wait with "Thinking…" reads as a teacher pausing. */
const PHASES = { ready:'Ready', listening:'Listening…', thinking:'Thinking…',
                 speaking:'Speaking…', writing:'Writing…', playing:'Playing' };
function phase(p, extra) {
  $('phase').dataset.p = p;
  $('phasetext').textContent = (PHASES[p] || p) + (extra ? ` · ${extra}` : '');
}

/* ---- offline -------------------------------------------------------------
 * The recording is the part of the lesson that has to survive a bad connection, and it
 * is served as a plain file, so it keeps playing. What breaks is the tutor. Rather than
 * fail silently — a dead Ask button and no explanation — the app says which half is
 * down, keeps everything already on the board, and reconnects on its own.
 * Lesson manifests are cached so a reload on a flaky network still opens the lesson. */
const CACHE_KEY = 'saarthi.lessons.v1';

function cacheGet(key) {
  try { return JSON.parse(localStorage.getItem(CACHE_KEY) || '{}')[key] ?? null; }
  catch { return null; }
}
function cachePut(key, value) {
  try {
    const all = JSON.parse(localStorage.getItem(CACHE_KEY) || '{}');
    all[key] = value;
    localStorage.setItem(CACHE_KEY, JSON.stringify(all));
  } catch { /* private mode, or the quota is full; the app works without the cache */ }
}

async function getJSON(url, key) {
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    cachePut(key, data);
    return data;
  } catch (e) {
    const cached = cacheGet(key);
    if (cached) { net('cached', 'Showing a saved copy — you are offline.'); return cached; }
    throw e;
  }
}

/* One banner, three states, and it never lies about what still works. */
function net(state, message) {
  const bar = $('netbar');
  bar.dataset.state = state;
  bar.textContent = message || '';
  bar.hidden = !message;
  const offline = state === 'offline';
  for (const id of ['ask', 'qsend', 'mic']) $(id).disabled = offline;
}

function onlineAgain() {
  net('ok', '');
  if (stage.classList.contains('mode-live')) connect();
}
window.addEventListener('online', onlineAgain);
window.addEventListener('offline', () =>
  net('offline', 'Offline. The recording still plays and the board keeps what is on it — '
    + 'questions will work again when you reconnect.'));

/* ---- lessons ---- */
async function loadIndex() {
  const list = await getJSON(api('/api/lessons'), 'index');
  navLessons = list;
  $('lesson').innerHTML = list.map(l =>
    `<option value="${l.topic}">${l.topic} · ${l.language} · ${fmt(l.duration)}</option>`).join('');
  if (list.length) await loadLesson(list[0].topic);
  renderNav();
}

async function loadLesson(topic) {
  renderNotes();
  man = await getJSON(api(`/api/lesson/${encodeURIComponent(topic)}`), `lesson:${topic}`);
  restoreUser();
  loadNotes();                          // this lesson's notes, from whenever they were made                        // their notebook for this lesson, not a scratch buffer
  video.src = api(man.video);
  syncMicLang();
  phase('ready', man.topic);
  board.clearAll();
}

const beatAt = (t) =>
  [...(man?.beats ?? [])].reverse().find(b => b.start != null && t >= b.start) ?? null;

function nextSafeStop(t) {
  if (!man?.safe_stops?.length) return t;
  return man.safe_stops.find(s => s > t + 0.05) ?? t;
}

/* The board's text equivalent. Rebuilt from the ops, so it says what was written and
 * in what role — the same information the colour and the glyph carry for everyone
 * else — and it is what the canvas is labelled by. */
function narrateBoard() {
  $('boardalt').textContent = describeBoard(board.done);
}
board.onIdle = () => { narrateBoard(); drawLegend(); };

/* ---- legend: colour never carries meaning on its own ----------------------
 * It used to print all seven roles at all times, whatever was actually on the board —
 * a key to a map you might be looking at. Now it lists only the roles that are written
 * up there, with how many lines each has, and clicking one dims the rest. On a board
 * with eight lines that is the difference between "what does the red one say" and
 * having to read all of them. */
function drawLegend() {
  const counts = board.roleCounts();
  const keys = ['term', 'explain', 'example', 'tip', 'trap', 'result']
    .filter(k => counts[k]);
  $('legend').innerHTML = keys.map(k => `
    <button class="lgd" data-role="${k}" aria-pressed="${board.focusRole === k}"
            title="Show only the ${ROLES[k].label} lines">
      <i style="color:${ROLES[k].color}">${ROLES[k].glyph || '—'}</i>
      <span>${ROLES[k].label}</span><b>${counts[k]}</b>
    </button>`).join('');
  $('legend').hidden = !keys.length;
}

$('legend').addEventListener('click', (e) => {
  const btn = e.target.closest('.lgd');
  if (!btn) return;
  board.spotlight(btn.dataset.role);
  drawLegend();
});

/* ---- turning to the board ---- */
async function raiseHand() {
  if (stage.classList.contains('mode-live')) return;
  $('ask').disabled = true;
  phase('thinking', 'finishing the sentence');
  const target = nextSafeStop(video.currentTime);
  await new Promise(res => {
    const tick = () => {
      if (video.paused || video.currentTime >= target - 0.03) return res();
      requestAnimationFrame(tick);
    };
    tick();
  });

  wipe(); await sleep(160);
  video.pause();
  turn += 1;                            // a fresh hand-raise abandons any stale turn
  setLive(true);
  board.clearAll(); drawLegend();       // clears the tutor's layer only
  board.home(); showZoom(); setTool('pan');
  clampPip(); applyPip();
  await sleep(420);

  const b = beatAt(video.currentTime);
  setContext(b ? b.title : 'the lesson', false, 'Paused at');
  $('tutorline').dataset.empty = 'Ask a question and the answer appears here.';
  $('ask').disabled = false;
  phase('ready', 'ask your question');
  $('qtext').focus();
}

/* One switch for the mode, so the board's height and the chrome that competes with it
 * can never disagree. */
function setLive(on) {
  stage.classList.toggle('mode-live', on);
  stage.classList.toggle('mode-play', !on);
  document.body.classList.toggle('live', on);
  requestAnimationFrame(() => board.resize());
}

/* The server wraps the writing to a character budget, and that budget depends on how
 * wide the board is relative to its height — which is nothing like 16:9 once the chrome
 * has taken its rows, and changes again when the transcript opens. So the client is the
 * one that knows it, and says so. */
/* ---- the demo switch ------------------------------------------------------
 * Two independent axes — the model that decides what to say, and the thing that says
 * it. Kept separate on purpose: bundled as "three versions", a thumbs-down would not
 * say which half was disliked.
 *
 * Not every pairing exists. `voice=live` IS Gemini generating audio directly, so it
 * cannot be driven by another brain; that combination is disabled here and refused by
 * the server, rather than quietly falling back to something else and having the
 * comparison measure the wrong thing.
 */
function mode() {
  return { brain: $('brain').value, voice: $('voice').value };
}

function syncMode() {
  const { brain, voice } = mode();
  const liveOpt = [...$('voice').options].find(o => o.value === 'live');
  liveOpt.disabled = brain !== 'gemini';
  if (liveOpt.disabled && voice === 'live') $('voice').value = 'tts';
  liveOpt.textContent = liveOpt.disabled
    ? 'Live (Gemini only)' : 'Live (native audio)';
  if (live && live.readyState === WebSocket.OPEN)
    live.send(JSON.stringify({ type: 'mode', ...mode() }));
  const m = mode();
  phase('ready', `${m.brain} · ${m.voice}`);
}
$('brain').onchange = syncMode;
$('ears').onchange = () => {
  try { localStorage.setItem('saarthi.ears', $('ears').value); } catch { /* no storage */ }
  $('mic').disabled = !Speech.available();
};
try {
  const saved = localStorage.getItem('saarthi.ears');
  if (saved) $('ears').value = saved;
  else if (!NativeSpeech && !SR) $('ears').value = 'gemini';   // nothing else can hear
} catch { /* no storage */ }
$('voice').onchange = syncMode;

function boardAspect() {
  return board.H > 0 ? +(board.W / board.H).toFixed(3) : 16 / 9;
}

function wipe() {
  const w = $('wipe');
  w.classList.remove('run'); void w.offsetWidth; w.classList.add('run');
}

async function resume() {
  turn += 1;                               // abandon anything in flight
  interrupt();
  settle(null);
  $('tutorline').textContent = '';
  $('jump').hidden = true;
  wipe(); await sleep(200);
  setLive(false);
  board.clearAll();
  clearTimeout(retryTimer); retries = 0;
  if (live) { live.close(); live = null; }
  await sleep(300);
  video.play();
  phase('playing');
}

/* ---- live tutor ---- */
function connect() {
  if (live && live.readyState === WebSocket.OPEN) return live;
  const base = apiBase() || location.origin;
  live = new WebSocket(base.replace(/^http/, 'ws') + '/ws');
  live.onopen = () => {
    retries = 0;
    clearTimeout(retryTimer);
    net('ok', '');
    const b = beatAt(video.currentTime);
    live.send(JSON.stringify({
      type: 'context', topic: man.topic, language: man.language, at: video.currentTime,
      beat: b ? { index: b.index, title: b.title, narration: b.narration } : null,
      beats: (man.beats || []).map(x => ({ index: x.index, title: x.title, start: x.start })),
      beats_full: (man.beats || []).map(x => ({ index: x.index, title: x.title, narration: x.narration })),
      facts: man.facts, in_scope: man.in_scope, out_of_scope: man.out_of_scope,
      answer_lang: micLang(), board_aspect: boardAspect(), ...mode(),
    }));
  };
  live.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    // A reply belongs to the question that asked for it. Playing the video, resuming,
    // or asking again abandons the turn in flight, and its late messages used to land
    // on the new one — which is how the board ended up stuck on "writing the answer".
    if (m.turn != null && m.turn !== turn) return;
    if (m.type === 'ops') {
      setWorking(false); phase('writing');
      board.home(); showZoom();          // the answer is written on the lesson page
      board.speed = 1;
      board.push(m.ops);
      if (notes.length) (notes[notes.length - 1].ops ||= []).push(...m.ops);
      // Keep the chalk with the voice. The board should be finished at about the moment
      // the tutor stops talking, not half a minute later.
      board.pace(12);
    }
    else if (m.type === 'audio') { phase('speaking'); $('stopbtn').hidden = false; playPCM(m.b64); }
    else if (m.type === 'audio_done') {
      $('stopbtn').hidden = true; settle('ready', 'ask a follow-up');
      saveNotes();                      // the answer is complete; keep it
    }
    else if (m.type === 'text') showTutor(m.text);
    else if (m.type === 'jump') offerJump(m);
    else if (m.type === 'error') { showTutor('⚠ ' + m.text); settle('ready', 'try again'); }
  };
  /* Reconnect, rather than leaving a dead socket behind a live-looking UI. The board
   * is client-side state, so nothing that has been written is lost; the reconnect only
   * has to re-send the lesson context before the next question. */
  live.onclose = () => {
    live = null;
    if (!stage.classList.contains('mode-live')) return;
    if (!navigator.onLine) {
      net('offline', 'Offline. The recording still plays and the board keeps what is on '
        + 'it — questions will work again when you reconnect.');
      return;
    }
    settle('ready', 'reconnecting');
    net('retry', 'Reconnecting to the tutor…');
    retries = Math.min(retries + 1, 5);
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      if (stage.classList.contains('mode-live')) connect();
    }, 400 * 2 ** retries);          // 0.8s, 1.6s, 3.2s … capped at ~13s
  };
  live.onerror = () => { /* onclose follows and owns the retry */ };
  return live;
}

/* Gemini Live returns raw 24 kHz s16 mono PCM, not a container, so it cannot go through
 * an <audio> element. Chunks are scheduled end-to-end on the Web Audio clock. */
const AudioCtx = window.AudioContext || window.webkitAudioContext;
let actx = null, playHead = 0, sources = [];

/* Barge-in. A student who has understood should not sit through the rest of the answer,
 * and one who is lost should be able to cut in. Every audio node is retained so it can
 * be stopped mid-sentence — without this the tutor talks over the next question, which
 * is the most irritating failure mode a voice tutor has. */
function interrupt(reason) {
  for (const src of sources) { try { src.stop(); } catch { /* already ended */ } }
  sources = [];
  playHead = 0;
  if (live && live.readyState === WebSocket.OPEN)
    live.send(JSON.stringify({ type: 'interrupt' }));
  $('stopbtn').hidden = true;
  if (reason) phase('ready', reason);
}
function playPCM(b64) {
  if (muted) return;                    // the writing continues; only the voice stops
  if (!actx) actx = new AudioCtx({ sampleRate: 24000 });
  if (actx.state === 'suspended') actx.resume();
  const raw = atob(b64), pcm = new Int16Array(raw.length / 2);
  for (let i = 0; i < pcm.length; i++)
    pcm[i] = (raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8)) << 16 >> 16;
  const buf = actx.createBuffer(1, pcm.length, 24000);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = actx.createBufferSource();
  src.buffer = buf; src.connect(actx.destination);
  playHead = Math.max(playHead, actx.currentTime + 0.06);
  src.start(playHead);
  playHead += buf.duration;
  sources.push(src);
  src.onended = () => { sources = sources.filter(x => x !== src); };
}

/* The transcript streams in cumulatively, so the caption is rewritten in place and
 * then LEFT there. It used to be cleared the moment the audio finished, which is the
 * exact moment a student who missed a word wants to read it. */
function showTutor(text) {
  const el = $('tutorline');
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
  setWorking(false);
  if (notes.length) { notes[notes.length - 1].a = text; renderNotes(); }
}

/* A pointer back into the recording is navigation, not teaching, so it is a control
 * and not chalk. It used to be both, which is why the board grew a stray line at the
 * bottom that duplicated the chip sitting over the video. */
function offerJump(m) {
  jumpTarget = m.at;
  const btn = $('jump');
  btn.textContent = `▶ Watch ${fmt(m.at)} · ${m.title}`;
  btn.title = m.why || m.title;
  btn.hidden = false;
}
$('jump').onclick = () => {
  if (jumpTarget == null) return;
  video.currentTime = jumpTarget; video.play();
  phase('playing', 'replaying that part');
};

/* ---- ask ----------------------------------------------------------------
 * The board carries the ANSWER and nothing else. It used to open every turn by writing
 * the question in chalk and then "soch raha hoon…" underneath, which read as filler and
 * left two throwaway lines stranded on an otherwise empty board. The question is now
 * set in type in the context bar, where it stays legible for the whole turn, and the
 * wait is shown as a skeleton exactly where the writing is about to appear. */
function setContext(text, working, label) {
  $('qlab').textContent = label || 'You asked';
  $('qlab').dataset.kind = label ? 'place' : 'question';
  $('qctext').textContent = text || '';
  $('qctext').title = text || '';
  $('qlab').hidden = !text;
  setWorking(working);
}
function setWorking(on) {
  $('working').hidden = !on;
  $('skeleton').hidden = !on;
}

/* One way out of the waiting state, and a deadline on it.
 *
 * The spinner used to be cleared in three places and set in one, so any path that did
 * not go through those three — a socket that closed mid-answer, an error with no
 * `audio_done` behind it, the student resuming the lesson while the tutor was still
 * thinking — left it spinning with no way back. Now every exit runs through here, and
 * a turn that produces nothing at all gives up out loud rather than hanging. */
function settle(state, extra) {
  clearTimeout(waitTimer); waitTimer = 0;
  setWorking(false);
  if (state) phase(state, extra);
}

function beginWait() {
  clearTimeout(waitTimer);
  waitTimer = setTimeout(() => {
    if (!board.done.length && !$('tutorline').textContent.trim()) {
      showTutor('⚠ The tutor did not answer that one. Ask again, or rephrase it.');
      settle('ready', 'no answer');
    } else {
      settle('ready', 'ask a follow-up');   // partial answer: keep what arrived
    }
  }, 30000);
}

/* Questions go straight to the tutor. There was a teacher mode here that held them in
 * a queue for release — it belonged to the shared-classroom framing and is noise for a
 * student sitting alone with their own tutor, so it is gone rather than hidden.
 */
function ask(q) {
  q = (q || '').trim();
  if (!q) return;
  send(q);
}

function send(q, image) {
  q = (q || '').trim();
  if (!q) return;
  interrupt();
  $('qtext').value = '';
  notes.push({ t: video.currentTime, at: new Date().toISOString(), q, a: '', ops: [],
                about: image ? 'about something you circled' : '' });
  renderNotes();
  board.clearAll(); drawLegend();
  $('jump').hidden = true;
  $('tutorline').textContent = '';
  $('tutorline').dataset.empty = 'Saarthi is working on it…';
  turn += 1;                               // anything still in flight is now stale
  setContext(q, true);                     // engage immediately, in type not in chalk
  phase('thinking');
  beginWait();
  const ws = connect();
  playHead = 0;
  const asked = turn;
  const fire = () => ws.send(JSON.stringify({ type: 'live_ask', text: q, turn: asked,
                                              answer_lang: micLang(),
                                              board_aspect: boardAspect(),
                                              image: image || null, ...mode() }));
  if (ws.readyState === WebSocket.OPEN) fire();
  else ws.addEventListener('open', fire, { once: true });
}

$('mute').onclick = () => {
  muted = !muted;
  $('mute').setAttribute('aria-pressed', String(muted));
  $('mute').classList.toggle('on', muted);
  $('mute').textContent = muted ? 'Unmute voice' : 'Mute voice';
  if (muted) interrupt('voice muted');
};

/* ---- the recording, on the board -----------------------------------------
 * Fixed in the corner at a fixed size, the monitor was in the way as often as it was
 * useful: it covered the right-hand diagrams, and it was too small to actually re-watch
 * anything in. It is now the student's to place and size — dragged anywhere inside the
 * board, resized from its corner or by scrolling over it, and remembered.
 */
const PIP_KEY = 'saarthi.pip.v1';
const pip = { w: 18, top: 12, right: 16, left: null,
              ...(() => { try { return JSON.parse(localStorage.getItem(PIP_KEY) || '{}'); }
                          catch { return {}; } })() };

function applyPip() {
  const r = document.documentElement.style;
  r.setProperty('--pip-w', `${pip.w}%`);
  r.setProperty('--pip-top', `${pip.top}px`);
  if (pip.left == null) {
    r.setProperty('--pip-right', `${pip.right}px`);
    r.setProperty('--pip-left', 'auto');
  } else {
    r.setProperty('--pip-left', `${pip.left}px`);
    r.setProperty('--pip-right', 'auto');
  }
  $('vgrip').setAttribute('aria-valuenow', String(Math.round(pip.w)));
  try { localStorage.setItem(PIP_KEY, JSON.stringify(pip)); } catch { /* no storage */ }
}

function clampPip() {
  const bw = $('board').getBoundingClientRect();
  const w = (pip.w / 100) * bw.width, h = w * 9 / 16;
  pip.top = Math.max(4, Math.min(bw.height - h - 4, pip.top));
  if (pip.left != null) pip.left = Math.max(4, Math.min(bw.width - w - 4, pip.left));
  else pip.right = Math.max(4, Math.min(bw.width - w - 4, pip.right));
}

function resizePip(pct) {
  pip.w = Math.max(12, Math.min(46, pct));
  clampPip(); applyPip();
}

let pipDrag = null, pipResize = null;
$('videowrap').addEventListener('pointerdown', (e) => {
  if (!stage.classList.contains('mode-live')) return;
  const bw = $('board').getBoundingClientRect();
  const box = $('videowrap').getBoundingClientRect();
  e.stopPropagation();                       // never start a board stroke under it
  $('videowrap').setPointerCapture(e.pointerId);
  const corner = e.target.dataset?.corner;
  if (corner) {
    // Any corner resizes. The two on the left grow the video leftwards, so the edge
    // under the finger is the one that moves and the opposite edge stays put — which is
    // what dragging a corner is supposed to feel like.
    pipResize = { x: e.clientX, w: pip.w, bw: bw.width,
                  dir: corner.includes('w') ? -1 : 1,
                  anchorRight: corner.includes('w') ? box.right - bw.left : null };
  } else {
    // switch to left-anchored while dragging, so the maths is the same in both axes
    pip.left = box.left - bw.left;
    pipDrag = { x: e.clientX, y: e.clientY, left: pip.left, top: pip.top };
    $('videowrap').classList.add('dragging');
  }
});
$('videowrap').addEventListener('pointermove', (e) => {
  if (pipResize) {
    const delta = ((e.clientX - pipResize.x) / pipResize.bw) * 100 * pipResize.dir;
    resizePip(pipResize.w + delta);
    if (pipResize.anchorRight !== null) {
      // keep the right edge where it was while the left one follows the finger
      const w = (pip.w / 100) * pipResize.bw;
      pip.left = Math.max(4, pipResize.anchorRight - w);
      clampPip(); applyPip();
    }
  } else if (pipDrag) {
    pip.left = pipDrag.left + (e.clientX - pipDrag.x);
    pip.top = pipDrag.top + (e.clientY - pipDrag.y);
    clampPip(); applyPip();
  }
});
['pointerup', 'pointercancel'].forEach(ev => $('videowrap').addEventListener(ev, (e) => {
  pipDrag = pipResize = null;
  $('videowrap').classList.remove('dragging');
  if ($('videowrap').hasPointerCapture?.(e.pointerId))
    $('videowrap').releasePointerCapture(e.pointerId);
}));
$('videowrap').addEventListener('wheel', (e) => {
  if (!stage.classList.contains('mode-live')) return;
  e.preventDefault(); e.stopPropagation();   // the board must not pan under it
  resizePip(pip.w * (e.deltaY < 0 ? 1.06 : 1 / 1.06));
}, { passive: false });
$('vgrip').addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); resizePip(pip.w + 2); }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); resizePip(pip.w - 2); }
  e.stopPropagation();
});

/* ---- the board as a two-way surface --------------------------------------
 * Until now the board was a display: the tutor wrote, the student watched. A student
 * who wants to try the problem themselves, or show their working, or paste the
 * question from their book, had nowhere to do it. The canvas is infinite — the tutor's
 * answer occupies the first screenful and everything else is reached by panning — and
 * the student's marks live in their own layer, so a new question clears the answer and
 * never the student's work.
 */
let tool = 'pan';
const undoStack = [];
let drawing = null, panFrom = null, spaceHeld = false;
let selection = null, selecting = null;   // a region of the board, in world units

function setTool(t) {
  tool = t;
  for (const b of document.querySelectorAll('.tool[data-tool]')) {
    const on = b.dataset.tool === t;
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  }
  const cv = $('board');
  cv.className = `tool-${t}`;
}

// The zoom buttons are gone — pinch and scroll do this, on every device a student has
// already used, and five fewer controls is five fewer things to explain.
function showZoom() { /* no readout to update */ }

/* Pointer handling. One handler for every tool, because they differ only in what they
 * do with the same three events, and splitting them was how the pen ended up able to
 * draw while the board was panning. */
const boardXY = (e) => {
  const r = $('board').getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
};

$('board').addEventListener('pointerdown', (e) => {
  if (!stage.classList.contains('mode-live')) return;
  const [px, py] = boardXY(e);
  $('board').setPointerCapture(e.pointerId);
  if (tool === 'pan' || spaceHeld || e.button === 1) {
    panFrom = [e.clientX, e.clientY];
    $('board').classList.add('dragging');
  } else if (tool === 'pen') {
    drawing = { kind: 'stroke', pts: [board.toWorld(px, py)], weight: 3 };
  } else if (tool === 'erase') {
    const hit = board.pick(px, py);
    if (hit) { pushUndo({ undo: () => board.addUser(hit) }); board.removeUser(hit.id); }
  } else if (tool === 'text') {
    openTextEntry(px, py);
  } else if (tool === 'select') {
    const [u, v] = board.toWorld(px, py);
    selecting = { x0: u, y0: v, x1: u, y1: v };
    selection = null;
    hideSelBar();
  }
});

$('board').addEventListener('pointermove', (e) => {
  const [px, py] = boardXY(e);
  if (panFrom) {
    board.panBy(e.clientX - panFrom[0], e.clientY - panFrom[1]);
    panFrom = [e.clientX, e.clientY];
  } else if (selecting) {
    const [u, v] = board.toWorld(px, py);
    selecting.x1 = u; selecting.y1 = v;
    selection = selecting;
    drawSelection();
  } else if (drawing) {
    drawing.pts.push(board.toWorld(px, py));
    board.paint();                       // the in-progress stroke is drawn below
    const c = board.ctx, v = board.view;
    c.save();
    c.setTransform(devicePixelRatio || 1, 0, 0, devicePixelRatio || 1, 0, 0);
    c.strokeStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--ink').trim() || '#16202b';
    c.lineWidth = Math.max(1, 3 * v.z); c.lineCap = 'round'; c.lineJoin = 'round';
    c.beginPath();
    drawing.pts.forEach(([u, w], i) =>
      i ? c.lineTo(board.X(u), board.Y(w)) : c.moveTo(board.X(u), board.Y(w)));
    c.stroke();
    c.restore();
  }
});

function endPointer(e) {
  if (panFrom) { panFrom = null; $('board').classList.remove('dragging'); }
  if (selecting) {
    const tiny = Math.abs(board.X(selecting.x1) - board.X(selecting.x0)) < 12 ||
                 Math.abs(board.Y(selecting.y1) - board.Y(selecting.y0)) < 8;
    selecting = null;
    if (tiny) clearSelection(); else showSelBar();
  }
  if (drawing) {
    if (drawing.pts.length > 1) {
      const item = board.addUser(drawing);
      pushUndo({ undo: () => board.removeUser(item.id) });
    } else {
      board.paint();
    }
    drawing = null;
  }
  if (e && e.pointerId != null && $('board').hasPointerCapture?.(e.pointerId))
    $('board').releasePointerCapture(e.pointerId);
}
['pointerup', 'pointercancel', 'pointerleave'].forEach(ev =>
  $('board').addEventListener(ev, endPointer));

/* ---- ask about a piece of the board --------------------------------------
 * "What does this line mean?" is the commonest question a student has, and until now
 * they had to retype the line to ask it. Highlighting a region and asking about *that*
 * sends the model the pixels along with the question, so the answer is about the thing
 * under the box — a formula, a diagram, a number, or the student's own working — and
 * not about the lesson in general.
 */
function selRectPx() {
  if (!selection) return null;
  const x0 = Math.min(board.X(selection.x0), board.X(selection.x1));
  const x1 = Math.max(board.X(selection.x0), board.X(selection.x1));
  const y0 = Math.min(board.Y(selection.y0), board.Y(selection.y1));
  const y1 = Math.max(board.Y(selection.y0), board.Y(selection.y1));
  return { x0, y0, w: x1 - x0, h: y1 - y0 };
}

function drawSelection() {
  const r = selRectPx();
  const box = $('selbox');
  if (!r) { box.hidden = true; return; }
  Object.assign(box.style, { left: `${r.x0}px`, top: `${r.y0}px`,
                             width: `${r.w}px`, height: `${r.h}px` });
  box.hidden = false;
  if (!$('selbar').hidden) placeSelBar(r);
}
// the box is anchored in world coordinates, so it must follow a pan or a zoom
board.onPaint = () => { if (selection) drawSelection(); };

function placeSelBar(r) {
  const bar = $('selbar'), bw = $('board').getBoundingClientRect();
  const w = bar.offsetWidth || 380, h = bar.offsetHeight || 46;
  const below = r.y0 + r.h + 10 + h < bw.height;
  bar.style.top = `${below ? r.y0 + r.h + 10 : Math.max(6, r.y0 - h - 10)}px`;
  bar.style.left = `${Math.max(6, Math.min(bw.width - w - 6, r.x0 + r.w / 2 - w / 2))}px`;
}

function showSelBar() {
  const r = selRectPx();
  if (!r) return;
  $('selbar').hidden = false;
  placeSelBar(r);
  $('selq').value = '';
}
function hideSelBar() { $('selbar').hidden = true; }
function clearSelection() {
  selection = null; selecting = null;
  $('selbox').hidden = true;
  hideSelBar();
}

/* The crop is taken before anything is cleared, because asking is what clears it. */
function askAboutSelection(question) {
  if (!selection) return;
  const shot = board.crop(selection.x0, selection.y0, selection.x1, selection.y1);
  clearSelection();
  setTool('pan');
  send(question, shot);
}
$('selexplain').onclick = () =>
  askAboutSelection('Board par jo maine highlight kiya hai, wo samjhao.');
$('selq').onkeydown = (e) => {
  e.stopPropagation();
  if (e.key === 'Enter' && $('selq').value.trim()) askAboutSelection($('selq').value.trim());
  if (e.key === 'Escape') clearSelection();
};
$('selclose').onclick = clearSelection;

/* The same push-to-talk as the main dock, aimed at the selection instead. */
async function selMicStop(send_it) {
  $('selmic').classList.remove('mic-on');
  if (Speech.chosen() === 'gemini') phase('thinking', 'hearing you out');
  try {
    await Speech.stop(micLang(), (text) => { $('selq').value = text; });
  } catch (e) {
    showTutor('⚠ ' + e.message);
  }
  const q = $('selq').value.trim();
  if (send_it && q) askAboutSelection(q);
}
['mousedown', 'touchstart'].forEach(ev => $('selmic').addEventListener(ev, async (e) => {
  e.preventDefault();
  if (!Speech.available()) {
    showTutor('⚠ No speech recognition on this device — type your question instead.');
    return;
  }
  interrupt();
  $('selmic').classList.add('mic-on');
  phase('listening', micLang());
  try {
    await Speech.start(micLang(), (text) => { $('selq').value = text; });
  } catch (err) {
    showTutor('⚠ mic: ' + err.message);
    selMicStop(false);
  }
}));
['mouseup', 'mouseleave', 'touchend'].forEach(ev => $('selmic').addEventListener(ev, (e) => {
  if (!$('selmic').classList.contains('mic-on')) return;
  e.preventDefault(); selMicStop(true);
}));

/* Trackpad conventions: scroll pans, pinch (which arrives as ctrl+wheel) zooms. */
$('board').addEventListener('wheel', (e) => {
  if (!stage.classList.contains('mode-live')) return;
  e.preventDefault();
  const [px, py] = boardXY(e);
  if (e.ctrlKey || e.metaKey) board.zoomAt(px, py, e.deltaY < 0 ? 1.08 : 1 / 1.08);
  else board.panBy(-e.deltaX, -e.deltaY);
  showZoom();
}, { passive: false });

/* Text goes through a real textarea so the caret, IME and Indic keyboards behave the
 * way they do everywhere else; it is committed to the canvas when it loses focus. */
function openTextEntry(px, py) {
  const ta = $('textentry');
  const size = board.SH(0.04);
  ta.style.left = `${px}px`;
  ta.style.top = `${py - size}px`;
  ta.style.fontSize = `${size}px`;
  ta.value = '';
  ta.hidden = false;
  ta.dataset.at = JSON.stringify(board.toWorld(px, py));
  ta.focus();
}
$('textentry').addEventListener('blur', () => {
  const ta = $('textentry');
  if (ta.hidden) return;
  const text = ta.value.trim();
  ta.hidden = true;
  if (!text) return;
  const item = board.addUser({ kind: 'text', text, at: JSON.parse(ta.dataset.at),
                               size: 0.04 });
  pushUndo({ undo: () => board.removeUser(item.id) });
});
$('textentry').addEventListener('keydown', (e) => {
  e.stopPropagation();                         // never let shortcuts eat the typing
  if (e.key === 'Escape') { $('textentry').value = ''; $('textentry').blur(); }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('textentry').blur(); }
});

/* Images. Scaled to a comfortable share of the board and dropped where you are
 * looking, not at some fixed origin off-screen. */
$('pickimg').onclick = () => $('imgfile').click();
$('imgfile').onchange = (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      const w = 0.32, h = w * (img.height / img.width) * (board.W / board.H);
      const at = board.toWorld(board.W * 0.32, board.H * 0.3);
      const item = board.addUser({ kind: 'image', img, at, w, h, src: reader.result });
      pushUndo({ undo: () => board.removeUser(item.id) });
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
  e.target.value = '';                         // the same file can be added twice
};

function pushUndo(entry) { undoStack.push(entry); if (undoStack.length > 60) undoStack.shift(); }
$('undo').onclick = () => { const e = undoStack.pop(); if (e) e.undo(); };
document.querySelectorAll('.tool[data-tool]').forEach(b =>
  b.onclick = () => setTool(b.dataset.tool));

/* The student's work survives a reload — it is their notebook, not a scratch buffer. */
function userKey() { return `saarthi.marks.${man?.topic || 'lesson'}`; }
function saveUser() {
  try {
    localStorage.setItem(userKey(), JSON.stringify(board.user.map(
      ({ img, ...rest }) => rest)));
  } catch { /* quota or private mode; the board still works for this session */ }
}
function restoreUser() {
  try {
    const raw = JSON.parse(localStorage.getItem(userKey()) || '[]');
    board.user = [];
    for (const it of raw) {
      if (it.kind === 'image' && it.src) {
        const img = new Image();
        img.onload = () => board.paint();
        img.src = it.src;
        board.user.push({ ...it, img });
      } else board.user.push(it);
    }
    board.paint();
  } catch { /* nothing saved, or it is unreadable; start clean */ }
}
board.onUserChange = saveUser;

/* ---- transcript ---- */
const esc = (t) => String(t).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function notesKey() { return `saarthi.notes.${man?.topic || 'lesson'}`; }

function saveNotes() {
  try {
    // Board ops are kept with each note — they are what makes an entry re-openable
    // rather than just re-readable. Images inside them are dropped; a note is the
    // tutor's writing, not an archive of everything on the canvas.
    localStorage.setItem(notesKey(), JSON.stringify(notes.slice(-80)));
  } catch { /* quota or private mode; this session still works */ }
}

function loadNotes() {
  try { notes = JSON.parse(localStorage.getItem(notesKey()) || '[]'); }
  catch { notes = []; }
  renderNotes();
}

/* Put an old answer back on the board. The student's own marks stay where they are —
 * this replaces the tutor's layer only, the same as asking a new question does. */
function openNote(i) {
  const n = notes[i];
  if (!n?.ops?.length) return;
  if (!stage.classList.contains('mode-live')) { setLive(true); drawLegend(); }
  board.clearAll();
  board.home(); showZoom();
  board.pushInstant(n.ops);
  narrateBoard(); drawLegend();
  setContext(n.q, false, 'You asked');
  $('tutorline').textContent = n.a || '';
  phase('ready', 'from your notes');
  for (const el of document.querySelectorAll('.turn')) el.classList.remove('open');
  document.querySelector(`.turn[data-i="${i}"]`)?.classList.add('open');
}

$('noteslist').addEventListener('click', (e) => {
  const card = e.target.closest('.turn');
  if (card) openNote(+card.dataset.i);
});

function renderNotes() {
  if (!notes.length) {
    $('noteslist').innerHTML =
      '<p class="empty">Ask something and it is kept here — the question, the answer, '
      + 'and the board that went with it. Tap any note to put it back on the board.</p>';
    return;
  }
  // Two clocks, because they answer different questions: where in the lesson it was
  // asked (useful as notes) and when it was asked (which is why every row used to read
  // the same 0:13 — the lesson is paused, so its position does not move).
  const day = (iso) => {
    const d = new Date(iso), now = new Date();
    const same = d.toDateString() === now.toDateString();
    return same ? 'Today' : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  };
  // newest first: the thing you just asked is the thing you are most likely to want
  $('noteslist').innerHTML = notes.map((n, i) => ({ n, i })).reverse().map(({ n, i }) => `
    <div class="turn${n.ops?.length ? ' openable' : ''}" data-i="${i}"
         ${n.ops?.length ? 'role="button" tabindex="0"' : ''}>
      <div class="meta">${day(n.at)} · at ${fmt(n.t)} in the lesson${n.about ? ` · ${n.about}` : ''}</div>
      <div class="q">${esc(n.q)}</div>
      ${n.a ? `<div class="a">${esc(n.a)}</div>` : ''}
      ${n.ops?.length ? '<div class="reopen">Show on board</div>' : ''}
    </div>`).join('');
}
function notifyBoardSize() {
  if (live && live.readyState === WebSocket.OPEN)
    live.send(JSON.stringify({ type: 'board', aspect: boardAspect() }));
}

/* ---- left rail ------------------------------------------------------------
 * Two things a student moves between: which lesson, and their own notes. Collapsed it
 * keeps the icons, because a menu that vanishes entirely leaves no way back.
 */
function renderNav() {
  const list = $('navlessons');
  list.innerHTML = (navLessons || []).map(l => `
    <li><button class="navitem${l.topic === man?.topic ? ' on' : ''}"
                data-topic="${l.topic}" title="${l.topic}">
      <i>▸</i><span>${l.topic}<small>${l.language} · ${fmt(l.duration)}</small></span>
    </button></li>`).join('');
}

let navLessons = [];

$('navlessons').addEventListener('click', (e) => {
  const b = e.target.closest('[data-topic]');
  if (!b) return;
  $('lesson').value = b.dataset.topic;
  loadLesson(b.dataset.topic).then(renderNav);
});

function setNavCollapsed(collapsed) {
  document.body.classList.toggle('nav-collapsed', collapsed);
  $('navtoggle').setAttribute('aria-expanded', String(!collapsed));
  $('navtoggle').title = collapsed ? 'Expand the menu' : 'Collapse the menu';
  try { localStorage.setItem('saarthi.nav', collapsed ? '1' : '0'); } catch { /* none */ }
  setTimeout(() => board.resize(), 260);
}
$('navtoggle').onclick = () =>
  setNavCollapsed(!document.body.classList.contains('nav-collapsed'));
try { setNavCollapsed(localStorage.getItem('saarthi.nav') === '1'); } catch { /* none */ }

$('navnotes').onclick = () => setNotes($('notes').hidden);
$('navhome').onclick = () => goHome();
$('home').onclick = () => goHome();

/* The brand is the way out of wherever you are: off the board, back to the lesson. */
function goHome() {
  if (stage.classList.contains('mode-live')) resume();
  setNotes(false);
}

function setNotes(open) {
  // The drawer is a real column, so the board must be told to re-measure — otherwise
  // the canvas keeps its old width and the composition sits off-centre.
  $('notes').hidden = !open;
  $('shell').classList.toggle('notes-open', open);
  $('notesbtn').setAttribute('aria-expanded', String(open));
  document.documentElement.style.setProperty('--transcript-w', open ? '340px' : '0px');
  setTimeout(() => { board.resize(); notifyBoardSize(); }, 340);
}
$('notesbtn').onclick = () => setNotes($('notes').hidden);
$('noteslist').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const card = e.target.closest('.turn');
  if (card) { e.preventDefault(); openNote(+card.dataset.i); }
});
$('closenotes').onclick = () => setNotes(false);
$('export').onclick = () => {
  const body = notes.map(n =>
    `[${new Date(n.at).toLocaleString()} · at ${fmt(n.t)} in the lesson]\n`
    + `Q: ${n.q}\nA: ${n.a}\n`).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([`${man?.topic} — transcript\n\n${body}`],
    { type: 'text/plain' }));
  a.download = `${man?.topic || 'lesson'}-transcript.txt`;
  a.click();
};

/* ---- voice ----------------------------------------------------------------
 * Raising a hand and asking out loud, in your own language, is the product — not a
 * convenience on top of a text box. So there are two speech backends behind one
 * interface, because the browser's and Android's are different APIs entirely:
 *
 *   web      Web Speech API. Chrome only, and absent from Android WebView.
 *   native   Android's own recogniser, via a Capacitor plugin. Same language codes,
 *            free, and available where the Web Speech API does not exist at all.
 *
 * Everything else — push to talk, barge-in, the language picker, the selection mic —
 * talks to `Speech` and never learns which one is running.
 */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const NativeSpeech = window.Capacitor?.Plugins?.SpeechRecognition;

/* The model-based ear. Captures raw PCM and writes the WAV itself rather than using
 * MediaRecorder, whose container and codec vary by platform — Gemini takes wav
 * everywhere, so there is nothing left to negotiate. No partial results: the transcript
 * arrives once, after the student stops speaking, which is the price of a model that
 * can actually handle "nodal plane" inside a Hindi sentence. */
const ModelEars = {
  _stream: null, _ctx: null, _node: null, _chunks: [], _rate: 16000,

  async start() {
    this._chunks = [];
    this._stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this._ctx = new Ctx();
    const src = this._ctx.createMediaStreamSource(this._stream);
    const node = this._ctx.createScriptProcessor(4096, 1, 1);
    this._rate = this._ctx.sampleRate;
    node.onaudioprocess = (e) => {
      this._chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    src.connect(node); node.connect(this._ctx.destination);
    this._node = node;
  },

  _wav() {
    const total = this._chunks.reduce((n, c) => n + c.length, 0);
    const flat = new Float32Array(total);
    let at = 0;
    for (const c of this._chunks) { flat.set(c, at); at += c.length; }
    // 16 kHz is plenty for speech and a quarter the upload of 48 kHz
    const ratio = this._rate / 16000;
    const n = Math.floor(flat.length / ratio);
    const buf = new ArrayBuffer(44 + n * 2);
    const view = new DataView(buf);
    const tag = (off, str) => [...str].forEach((ch, i) => view.setUint8(off + i, ch.charCodeAt(0)));
    tag(0, 'RIFF'); view.setUint32(4, 36 + n * 2, true); tag(8, 'WAVEfmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    tag(36, 'data'); view.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const v = Math.max(-1, Math.min(1, flat[Math.floor(i * ratio)]));
      view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    }
    let bin = '';
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000)
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return { url: 'data:audio/wav;base64,' + btoa(bin), seconds: n / 16000 };
  },

  async stop(lang) {
    try { this._node?.disconnect(); } catch { /* already gone */ }
    try { this._stream?.getTracks().forEach(t => t.stop()); } catch { /* gone */ }
    try { await this._ctx?.close(); } catch { /* gone */ }
    const { url, seconds } = this._wav();
    this._chunks = [];
    if (seconds < 0.3) return '';            // a tap, not a question
    const r = await fetch(api('/api/transcribe'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio: url, language: lang, topic: man?.topic }) });
    if (!r.ok) throw new Error(`transcription failed (${r.status})`);
    return (await r.json()).text || '';
  },
};

const Speech = {
  backend: NativeSpeech ? 'native' : (SR ? 'web' : null),
  _rec: null,
  _handles: [],
  onerror: null,
  onend: null,

  available() { return this.backend !== null || this.chosen() === 'gemini'; },

  /* device | gemini. The device recogniser is free, instant and streams as you speak;
   * the model handles code-switching the device recogniser mangles. */
  chosen() { return $('ears') ? $('ears').value : 'device'; },

  async start(lang, onText) {
    this._mode = this.chosen();
    if (this._mode === 'gemini') { await ModelEars.start(); return; }
    if (this.backend === 'native') {
      const perm = await NativeSpeech.requestPermissions();
      if (perm?.speechRecognition !== 'granted')
        throw new Error('microphone permission was refused');
      // partialResults stream as the student speaks, which is what fills the box live
      // rather than dumping the whole sentence in when they stop.
      this._handles.push(await NativeSpeech.addListener('partialResults',
        (d) => onText((d.matches || []).join(' ').trim())));
      await NativeSpeech.start({ language: lang, partialResults: true, popup: false });
      return;
    }
    if (!SR) throw new Error('this browser has no speech recognition');
    const rec = new SR();
    this._rec = rec;
    rec.lang = lang; rec.interimResults = true; rec.continuous = true;
    let settled = '';
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) settled += t + ' '; else interim += t;
      }
      onText((settled + interim).trim());
    };
    rec.onerror = (e) => { if (this.onerror) this.onerror(e.error); };
    rec.onend = () => { if (this.onend) this.onend(); };
    try { rec.start(); } catch { /* already listening */ }
  },

  async stop(lang, onText) {
    if (this._mode === 'gemini') {
      const text = await ModelEars.stop(lang);
      if (text && onText) onText(text);
      return;
    }
    if (this.backend === 'native') {
      for (const h of this._handles) { try { await h.remove(); } catch { /* gone */ } }
      this._handles = [];
      try { await NativeSpeech.stop(); } catch { /* not listening */ }
      return;
    }
    if (this._rec) { try { this._rec.stop(); } catch { /* stopped */ } this._rec = null; }
  },
};
function micLang() { return $('miclang').value; }
function syncMicLang() {
  const want = man?.language === 'gu' ? 'gu-IN' : 'hi-IN';
  if ([...$('miclang').options].some(o => o.value === want)) $('miclang').value = want;
}
async function startMic() {
  interrupt();                    // pressing the mic cuts in, as in a real room
  if (!Speech.available()) {
    showTutor('⚠ No speech recognition on this device — type your question instead.');
    return;
  }
  Speech.onerror = (err) => {
    showTutor('⚠ mic: ' + err + (String(err).includes('not-allowed')
      ? ' — allow microphone access' : ''));
    stopMic(true);
  };
  Speech.onend = () => { if ($('mic').classList.contains('on')) stopMic(true); };
  $('mic').classList.add('on');
  phase('listening', micLang());
  try {
    await Speech.start(micLang(), (text) => { $('qtext').value = text; });
  } catch (e) {
    showTutor('⚠ mic: ' + e.message);
    stopMic(true);
  }
}

async function stopMic(auto) {
  $('mic').classList.remove('on');
  // The model ear has nothing to show until the upload comes back, so say so — silence
  // after letting go of the button reads as a dropped question.
  if (Speech.chosen() === 'gemini') phase('thinking', 'hearing you out');
  try {
    await Speech.stop(micLang(), (text) => { $('qtext').value = text; });
  } catch (e) {
    showTutor('⚠ ' + e.message);
  }
  const q = $('qtext').value.trim();
  if (q && !auto) ask(q); else phase('ready');
}
['mousedown', 'touchstart'].forEach(ev =>
  $('mic').addEventListener(ev, (e) => { e.preventDefault(); startMic(); }));
['mouseup', 'mouseleave', 'touchend'].forEach(ev =>
  $('mic').addEventListener(ev, (e) => {
    if (!$('mic').classList.contains('on')) return;
    e.preventDefault(); stopMic(false);
  }));

/* ---- wiring ---- */
$('play').onclick = () => (video.paused ? video.play() : video.pause());
video.addEventListener('play', () => {
  // Starting the recording is a decision to stop listening to the answer.
  if (waitTimer || !$('stopbtn').hidden) { turn += 1; interrupt(); settle(null); }
});
const nudge = (by) => {
  if (!video.duration) return;
  video.currentTime = Math.min(video.duration, Math.max(0, video.currentTime + by));
};
$('back10').onclick = () => nudge(-10);
$('fwd10').onclick = () => nudge(10);
function setPlayIcon(playing) {
  $('play').textContent = playing ? '❚❚' : '▶';
  $('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
}
video.onplay = () => {
  setPlayIcon(true);
  if (!stage.classList.contains('mode-live')) phase('playing');
};
video.onpause = () => setPlayIcon(false);
video.onloadedmetadata = () => { $('dur').textContent = fmt(video.duration || 0); };
video.ontimeupdate = () => {
  $('pos').textContent = fmt(video.currentTime);
  if (video.duration) $('seek').value = (video.currentTime / video.duration) * 1000;
};

/* The shortcut list used to be printed along the bottom of the app as grey text, which
 * read as debug output. It is the same information, behind a control, on request. */
function showKeys(open) {
  $('keyspop').hidden = !open;
  $('keys').setAttribute('aria-expanded', String(open));
}
$('keys').onclick = (e) => { e.stopPropagation(); showKeys($('keyspop').hidden); };
document.addEventListener('click', (e) => {
  if (!$('keyspop').hidden && !$('keyspop').contains(e.target)) showKeys(false);
});
$('seek').oninput = (e) => { if (video.duration) video.currentTime = e.target.value / 1000 * video.duration; };
$('lesson').onchange = (e) => loadLesson(e.target.value);
$('ask').onclick = raiseHand;
$('resume').onclick = resume;
$('back').onclick = resume;
$('qsend').onclick = () => ask($('qtext').value);
$('stopbtn').onclick = () => interrupt('stopped');
$('chips').addEventListener('click', (e) => {
  const say = e.target.dataset?.say;
  if (!say) return;
  const last = notes.length ? notes[notes.length - 1].q : '';
  ask(last ? `${say} (mera pichhla sawaal tha: ${last})` : say);
});
$('qtext').onkeydown = (e) => { if (e.key === 'Enter') ask($('qtext').value); };

// keyboard: every control reachable without a pointer
document.addEventListener('keyup', (e) => { if (e.code === 'Space') spaceHeld = false; });
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT'
      || e.target.tagName === 'TEXTAREA') return;
  const onBoard = stage.classList.contains('mode-live');
  if (onBoard) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
      e.preventDefault(); $('undo').click(); return;
    }
    const pick = { v: 'pan', p: 'pen', x: 'text', e: 'erase',
                   s: 'select' }[e.key.toLowerCase()];
    if (pick && !e.metaKey && !e.ctrlKey) { setTool(pick); return; }
    if (e.key.toLowerCase() === 'i') { $('pickimg').click(); return; }
    // space pans while any tool is held, as in every canvas app
    if (e.code === 'Space') { spaceHeld = true; e.preventDefault(); return; }
  }
  if (e.code === 'Space') { e.preventDefault(); $('play').click(); }
  else if (e.key.toLowerCase() === 'h') raiseHand();
  else if (e.key.toLowerCase() === 't') $('notesbtn').click();
  else if (e.key.toLowerCase() === 'm') $('mute').click();
  else if (e.key === 'Escape') {
    if (!$('keyspop').hidden) showKeys(false);
    else if (stage.classList.contains('mode-live')) resume();
  }
  else if (e.key === 'ArrowLeft') nudge(-10);
  else if (e.key === 'ArrowRight') nudge(10);
});

/* ---- visual fixtures ----------------------------------------------------
 * ?ops=<url>            render a captured answer on the real renderer
 * ?demo=<url>&state=…   put the whole live view into a named state and hold it
 * Everything is driven through the same code paths the tutor uses, so a screenshot
 * taken here is evidence about the shipping layout and not about a mock. */
async function fixture(qs) {
  setLive(true);
  drawLegend();
  board.speed = 1e6;                                   // no typing animation in a still
  const state = qs.get('state') || 'answered';
  const q = qs.get('q') || 'यह s orbital और p orbital में क्या फर्क है?';

  if (state === 'waiting') {
    setContext(q, true);
    $('tutorline').dataset.empty = 'Saarthi is working on it…';
    phase('thinking');
    await report();
    return;
  }
  setContext(q, false);
  const ops = await (await fetch(qs.get('demo') || qs.get('ops'))).json();

  if (qs.has('live')) {
    /* The whole thing, end to end, in a browser: connect, ask the real tutor, and
     * report what actually reached the board. The other fixtures feed the renderer
     * captured ops, so any break BETWEEN the socket and board.push() — a message
     * filtered by the wrong turn id, a throw in the handler — would leave every one of
     * them green and the board blank. Costs one real answer. */
    const bad = [];
    const seen = [];
    board.clearAll();
    board.speed = 8;
    let clock = 0;
    window.requestAnimationFrame = (fn) => setTimeout(() => fn(clock += 16), 1);

    const ws = connect();
    const realHandler = ws.onmessage;
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      seen.push(`${m.type}${m.turn != null ? '#' + m.turn : ''}`);
      return realHandler(ev);
    };
    await new Promise(r => (ws.readyState === WebSocket.OPEN ? r()
                                                             : ws.addEventListener('open', r, { once: true })));
    ask(qs.get('q') || 's orbital aur p orbital mein kya farq hai?');
    const asked = turn;

    await new Promise(r => setTimeout(r, 30000));
    const kinds = seen.reduce((a, k) => (a[k.split('#')[0]] = (a[k.split('#')[0]] || 0) + 1, a), {});
    if (!kinds.ops) bad.push('the server never sent any board ops');
    if (!kinds.audio) bad.push('the server never sent any audio');
    if (!board.done.length && !board.queue.length)
      bad.push('ops arrived but nothing reached the board');
    if (board.done.length === 0 && board.queue.length > 0)
      bad.push('ops were queued but the frame loop never drew them');
    const turns = [...new Set(seen.filter(k => k.includes('#')).map(k => k.split('#')[1]))];
    if (turns.length && !turns.includes(String(asked)))
      bad.push(`replies are stamped ${turns} but the client is on turn ${asked}`);
    document.body.dataset.interact = JSON.stringify({ fail: bad,
      diag: `asked=turn${asked} clientTurn=${turn} drawn=${board.done.length} `
        + `queued=${board.queue.length} msgs=${JSON.stringify(kinds)} `
        + `caption=${$('tutorline').textContent.slice(0, 40)!==''}` });
    await report();
    return;
  }
  if (qs.has('animate')) {
    // The ANIMATED path — the one the tutor actually uses. Every other fixture calls
    // pushInstant, so a break in push()/start()/the frame loop would render a blank
    // board while every check still passed.
    //
    // requestAnimationFrame does not fire under a virtual-time budget once the page
    // goes idle, so the loop is driven from a timer instead. That is not a workaround
    // for the test: it is the only way to exercise the real loop headlessly at all,
    // and the loop cannot tell the difference — it only ever asked for a timestamp.
    const bad = [];
    board.clearAll();
    const c = board.ctx;
    const ink = () => {
      const d = c.getImageData(0, 0, $('board').width, $('board').height).data;
      let n = 0;
      for (let i = 0; i < d.length; i += 4 * 37) {
        if (d[i] < 235 || d[i + 1] < 235 || d[i + 2] < 235) n++;
      }
      return n;
    };
    const blank = ink();
    let clock = 0;
    // A synthetic clock, so "how long would this take a real browser" is measured from
    // the loop's own timestamps rather than from how fast headless happens to run.
    window.requestAnimationFrame = (fn) => setTimeout(() => fn(clock += 16), 1);

    let midInk = 0;
    board.push(ops);
    const paced = board.pace(12);
    const budget = [...board.queue].reduce((t, o) => t + board.durationOf(o), 0);
    if (budget > 15000) bad.push(`the answer would take ${Math.round(budget / 1000)}s to write`);
    if (budget < 4000) bad.push(`the answer would be scribbled in ${Math.round(budget / 1000)}s`);
    if (!board.running) bad.push('push() did not start the frame loop');
    setTimeout(() => { midInk = ink(); }, 260);      // part-way through the writing

    const how = await new Promise((res) => {
      const t = setTimeout(() => res('timeout'), 30000);
      board.onIdle = () => { clearTimeout(t); res('idle'); };
    });
    const after = ink();
    if (how === 'timeout') bad.push('the board never finished drawing');
    if (board.done.length !== ops.length)
      bad.push(`drew ${board.done.length} of ${ops.length} ops`);
    if (board.queue.length) bad.push(`${board.queue.length} ops left unqueued`);
    if (after <= blank + 20) bad.push(`nothing was drawn: ${blank} ink before, ${after} after`);
    if (midInk <= blank + 5) bad.push('nothing was on the board part-way through');
    if (midInk >= after) bad.push('the board did not build up progressively');
    narrateBoard(); drawLegend();
    if ($('boardalt').textContent.trim() === 'The board is empty.')
      bad.push('the board reports itself empty after drawing');
    showTutor('s orbital gol hota hai, p dumbbell shape ka.');
    document.body.dataset.interact = JSON.stringify({ fail: bad,
      diag: `ink ${blank}->${midInk}->${after} done=${board.done.length} `
        + `writes in ${(budget / 1000).toFixed(1)}s at speed ${paced.toFixed(2)}` });
    await report();
    return;
  }
  board.pushInstant(ops);
  narrateBoard(); drawLegend();
  showTutor(qs.get('say') || 's orbital har taraf se gol hota hai — spherically '
    + 'symmetric. p orbital dumbbell shape ka hota hai, do lobes ke saath, aur unke '
    + 'beech mein ek nodal plane hota hai jahan electron milne ki probability zero hai.');
  offerJump({ at: 83, title: 's orbital: har taraf se gol', beat: 3 });
  $('stopbtn').hidden = state !== 'speaking';
  phase(state === 'speaking' ? 'speaking' : 'ready', 'ask a follow-up');
  if (qs.has('draw')) {
    // Exercise the infinite canvas through its real API and assert the invariants that
    // make it usable: the view round-trips, zoom keeps the point under the cursor, the
    // student's marks survive a new question, and the eraser hits what you aimed at.
    const bad = [];
    const near = (a, b, t, what) => { if (Math.abs(a - b) > t) bad.push(`${what}: ${a} vs ${b}`); };

    board.home();
    const [u0, v0] = board.toWorld(300, 200);
    near(board.X(u0), 300, 0.01, 'X/toWorld round-trip');
    near(board.Y(v0), 200, 0.01, 'Y/toWorld round-trip');

    board.panBy(-120, -80);
    const [u1, v1] = board.toWorld(300, 200);
    near(board.X(u1), 300, 0.01, 'round-trip after pan');
    if (!(u1 > u0)) bad.push('panning left did not move the world right');

    board.home();
    board.zoomAt(400, 300, 2);
    const [uz, vz] = board.toWorld(400, 300);
    const [ru, rv] = board.toWorld(400, 300);
    near(board.X(uz), 400, 0.01, 'zoom anchor x');
    near(board.Y(vz), 300, 0.01, 'zoom anchor y');
    if (Math.abs(board.view.z - 2) > 1e-6) bad.push(`zoom is ${board.view.z}, expected 2`);
    board.home();
    if (board.view.z !== 1) bad.push('home did not reset zoom');

    const stroke = board.addUser({ kind: 'stroke', weight: 3,
      pts: [[0.20, 0.30], [0.26, 0.34], [0.32, 0.30], [0.38, 0.36]] });
    const label = board.addUser({ kind: 'text', text: 'my working', at: [0.20, 0.50],
                                  size: 0.04 });
    if (board.user.length !== 2) bad.push(`user layer has ${board.user.length} items`);

    const hit = board.pick(board.X(0.26), board.Y(0.34), 12);
    if (!hit || hit.id !== stroke.id) bad.push('the eraser did not hit the stroke');
    if (board.pick(board.X(0.90), board.Y(0.90), 12)) bad.push('the eraser hit empty board');

    const ops = await (await fetch(qs.get('demo'))).json();
    board.pushInstant(ops);
    board.clearAll();                    // what happens between questions
    if (board.user.length !== 2)
      bad.push('a new question wiped the student\'s marks');
    if (board.done.length !== 0) bad.push('clearAll left tutor ops behind');
    board.pushInstant(ops);
    board.removeUser(label.id);
    if (board.user.length !== 1) bad.push('removeUser did nothing');

    setContext(q, false);
    showTutor('Tumhara working sahi hai — ab nodal plane count karo.');
    setTool('pen');
    document.body.dataset.interact = JSON.stringify({ fail: bad });
    await report();
    return;
  }
  if (qs.has('select')) {
    // Highlight a region the way a drag would, then assert the crop is real and that
    // the action bar is placed inside the board rather than off the edge of it.
    const bad = [];
    setTool('select');
    selection = { x0: 0.055, y0: 0.24, x1: 0.62, y1: 0.35 };
    drawSelection(); showSelBar();
    const shot = board.crop(selection.x0, selection.y0, selection.x1, selection.y1);
    if (!shot || !shot.startsWith('data:image/png;base64,'))
      bad.push('the crop is not a png data url');
    if (shot && shot.length < 800) bad.push(`the crop is only ${shot.length} bytes`);
    const bw = $('board').getBoundingClientRect(), br = $('selbar').getBoundingClientRect();
    if (br.left < bw.left - 1 || br.right > bw.right + 1 ||
        br.top < bw.top - 1 || br.bottom > bw.bottom + 1)
      bad.push('the ask bar is outside the board');
    const sb = $('selbox').getBoundingClientRect();
    if (sb.width < 20 || sb.height < 10) bad.push('the selection box has no size');
    // it must follow the board, not sit where the screen used to be
    const before = $('selbox').getBoundingClientRect().left;
    board.panBy(-90, 0);
    if (Math.abs(($('selbox').getBoundingClientRect().left - before) + 90) > 2)
      bad.push('the selection did not follow the pan');
    board.home(); drawSelection();
    document.body.dataset.interact = JSON.stringify({ fail: bad });
    await report();
    return;
  }
  if (qs.has('offline')) {
    net('offline', 'Offline. The recording still plays and the board keeps what is on '
      + 'it — questions will work again when you reconnect.');
  }
  if (qs.has('notes')) {
    notes = [
      { t: 83, at: new Date(2026, 0, 1, 10, 4).toISOString(), q,
        a: 'Dono ka shape alag hai: s spherical, p dumbbell.', ops },
      { t: 96, at: new Date(2026, 0, 1, 10, 6).toISOString(),
        q: 'Nodal plane kya hota hai?', a: 'Wahan electron milne ki probability zero hai.',
        ops: [] },
    ];
    renderNotes();
    setNotes(true);
  }
  await report();                                      // screenshot barrier
}

/* Layout assertions that run in the real page against the real renderer. Screenshots
 * catch what someone happens to look at; this catches the things that were shipped
 * broken precisely because nobody looked — a chrome element sitting on top of another,
 * writing wider than its column, or a canvas font that never applied. */
async function report() {
  // Race the frame barrier against a timer. Under a virtual-time budget a page with a
  // running CSS animation can stop advancing rAF, and the checks would never be
  // written — a test that silently reports nothing is worse than one that fails.
  await new Promise(r => {
    let done = false;
    const fire = () => { if (!done) { done = true; r(); } };
    requestAnimationFrame(() => requestAnimationFrame(fire));
    setTimeout(fire, 400);
  });
  let out;
  try { out = selfCheck(); }
  catch (e) { out = { fail: [`selfCheck threw: ${e && e.message}`], board: '?', ops: -1 }; }
  document.body.dataset.checks = JSON.stringify(out);
  document.body.dataset.ready = '1';
}

function selfCheck() {
  const fail = [];
  board.resize();
  const R = (id) => { const e = $(id); if (!e || e.hidden || !e.offsetParent) return null;
                      const r = e.getBoundingClientRect();
                      return r.width && r.height ? r : null; };
  // 1 · no two pieces of chrome may share a pixel
  const ids = ['netbar', 'ctxbar', 'caption', 'chips', 'dock', 'notes',
               'videowrap', 'legend', 'transport', 'tools', 'selbar', 'nav'];
  const rects = ids.map(i => [i, R(i)]).filter(([, r]) => r);
  for (let i = 0; i < rects.length; i++)
    for (let j = i + 1; j < rects.length; j++) {
      const [an, a] = rects[i], [bn, b] = rects[j];
      const inBoard = (n) => n === 'videowrap' || n === 'legend' || n === 'transport'
                           || n === 'tools' || n === 'selbar';
      if (an === 'nav' || bn === 'nav') continue;   // its own column, beside the shell
      if (inBoard(an) || inBoard(bn)) continue;       // these live inside the board box
      if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom)
        fail.push(`${an} overlaps ${bn}`);
    }
  // 2 · the video and the legend must stay inside the board, and clear of each other
  const bw = $('board').getBoundingClientRect();
  for (const id of ['videowrap', 'legend', 'transport', 'tools', 'selbar']) {
    const r = R(id); if (!r) continue;
    if (r.left < bw.left - 1 || r.right > bw.right + 1 ||
        r.top < bw.top - 1 || r.bottom > bw.bottom + 1) fail.push(`${id} escapes the board`);
  }
  const hit = (a, b) => a && b && a.left < b.right && b.left < a.right &&
                        a.top < b.bottom && b.top < a.bottom;
  const v = R('videowrap'), lg = R('legend'), tr = R('transport');
  if (hit(v, lg)) fail.push('video overlaps legend');
  if (hit(tr, lg)) fail.push('transport overlaps legend');
  const tl = R('tools');
  if (hit(tl, lg)) fail.push('the tool rail overlaps the legend');
  if (hit(tl, v)) fail.push('the tool rail overlaps the video');
  if (hit(tl, tr)) fail.push('the tool rail overlaps the transport');
  if (!stage.classList.contains('mode-live') && tl)
    fail.push('the tool rail is showing during playback');
  if (stage.classList.contains('mode-live') && !tl)
    fail.push('the tool rail is missing on the board');
  if (stage.classList.contains('mode-live') && tr)
    fail.push('the transport is showing while the board is up');
  // 3 · while the recording is playing it must actually be visible: the video fills
  //     the board box, and no part of the board UI is showing over it
  if (!stage.classList.contains('mode-live')) {
    const v = R('videowrap'), bw = $('board').getBoundingClientRect();
    if (!v) fail.push('the lesson video is not rendered during playback');
    else if (Math.abs(v.width - bw.width) > 2 || Math.abs(v.height - bw.height) > 2)
      fail.push(`video ${Math.round(v.width)}x${Math.round(v.height)} does not fill `
        + `the board ${Math.round(bw.width)}x${Math.round(bw.height)}`);
    const vid = $('video').getBoundingClientRect();
    if (vid.width < 10 || vid.height < 10)
      fail.push(`the <video> element is ${Math.round(vid.width)}x${Math.round(vid.height)}`);
    if (!$('video').getAttribute('src')) fail.push('the video has no source');
    for (const id of ['ctxbar', 'caption', 'chips', 'dock'])
      if (R(id)) fail.push(`${id} is showing during playback`);
    if (!R('transport')) fail.push('the transport is missing during playback');
    if (getComputedStyle($('boardalt')).position !== 'absolute')
      fail.push('the board text equivalent is rendering visibly');
    if (R('legend')) fail.push('the legend is showing with nothing on the board');
  }
  // 4 · the board must have a real basis; a zero or NaN one silently mis-sizes every
  //     font and every coordinate on it
  if (!(board.W > 0 && board.H > 0)) fail.push(`board basis is ${board.W}x${board.H}`);
  if (!(board.W / board.H > 0.5 && board.W / board.H < 6)) fail.push('board aspect is absurd');
  // 5 · nothing may scroll the page sideways
  if (document.documentElement.scrollWidth > innerWidth + 1) fail.push('page scrolls sideways');
  // 6 · the canvas font must actually have applied — assigning an invalid font string
  //     is a silent no-op that leaves every glyph at 10px sans-serif
  const c = $('board').getContext('2d');
  setFont(c, 24);
  if (!/^24px/.test(c.font) || /sans-serif$/.test(c.font)) fail.push(`canvas font: ${c.font}`);
  // 7 · the board must have a text equivalent, and it must match what is on it
  const alt = $('boardalt').textContent.trim();
  if (!alt) fail.push('the board has no text equivalent');
  const written = board.done.filter(o => o.op === 'write' && !o.cont).length;
  if (written && !alt.includes(String(board.done.find(o => o.op === 'write')?.text || '')))
    fail.push('the text equivalent does not match the board');
  if ($('board').getAttribute('aria-labelledby') !== 'boardalt')
    fail.push('the canvas is not labelled by its text equivalent');
  // 8 · writing must stay inside its column and inside the board
  for (const o of board.done) {
    if (o.op !== 'write') continue;
    setFont(c, (o.size || 0.045) * board.H);
    const w = c.measureText(o.text).width / board.W;
    if (o.at[0] + w > (o.maxw ? o.at[0] + o.maxw : 0.95) + 0.005)
      fail.push(`"${o.text.slice(0, 24)}" overruns its column`);
    if (o.at[1] > 0.99 || o.at[1] < 0.02) fail.push(`"${o.text.slice(0, 24)}" off the board`);
  }
  return { board: `${Math.round(board.W)}x${Math.round(board.H)}`,
           font: c.font, ops: board.done.length, alt: alt.slice(0, 60), fail };
}

/* A probe the CDP driver installs, so a real browser session can be inspected from
 * outside: raise a hand, ask the real tutor, and report exactly what reached the board.
 * Real network and real wall-clock timing, which the headless virtual-time fixtures
 * could never give — they report nothing because virtual time does not wait on I/O. */
window.__probe = () => {
  // Local only. It asks a real question, which spends real quota, so it must not be
  // reachable on a deployed preview link.
  if (!['localhost', '127.0.0.1'].includes(location.hostname)) return 'local only';
  const state = { done: false, msgs: {}, turnAsked: null, turnsSeen: [], err: null };
  window.__probeState = () => ({ ...state, drawn: board.done.length,
                                 queued: board.queue.length, running: board.running,
                                 caption: $('tutorline').textContent.slice(0, 50),
                                 clientTurn: turn });
  window.addEventListener('error', e => { state.err = String(e.message); });
  (async () => {
    try {
      await raiseHand();
      const ws = connect();
      await new Promise(r => (ws.readyState === WebSocket.OPEN ? r()
                              : ws.addEventListener('open', r, { once: true })));
      const inner = ws.onmessage;
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        state.msgs[m.type] = (state.msgs[m.type] || 0) + 1;
        if (m.turn != null && !state.turnsSeen.includes(m.turn)) state.turnsSeen.push(m.turn);
        if (m.type === 'audio_done') setTimeout(() => { state.done = true; }, 6000);
        try { return inner(ev); } catch (e) { state.err = 'handler threw: ' + e.message; }
      };
      ask('s orbital aur p orbital mein kya farq hai?');
      state.turnAsked = turn;
    } catch (e) { state.err = 'probe threw: ' + e.message; state.done = true; }
  })();
};

/* ---- Android shell ---------------------------------------------------------
 * Packaged, the page has no origin to talk to, so the first screen asks for one and
 * checks it before letting the app start — a wrong address should fail here, with an
 * explanation, rather than as a blank lesson list ten seconds later.
 */
async function reachable(url) {
  try {
    const r = await fetch(url.replace(/\/$/, '') + '/api/lessons', { method: 'GET' });
    if (r.status === 401) return 'That server is passcode-protected — open it in a browser first.';
    if (!r.ok) return `The server answered ${r.status}.`;
    const list = await r.json();
    return Array.isArray(list) && list.length ? null : 'That server has no lessons on it.';
  } catch {
    return 'Could not reach that address. Check the URL and that the server is running.';
  }
}

function showSetup(force) {
  if (!PACKAGED && !force) return false;
  if (apiBase() && !force) return false;
  $('setup').hidden = false;
  $('setupurl').value = apiBase();
  return true;
}

$('setupform').onsubmit = async (e) => {
  e.preventDefault();
  const url = $('setupurl').value.trim();
  const err = $('setuperr');
  err.hidden = true;
  if (!/^https?:\/\//i.test(url)) {
    err.textContent = 'Start the address with https://'; err.hidden = false; return;
  }
  const problem = await reachable(url);
  if (problem) { err.textContent = problem; err.hidden = false; return; }
  setApiBase(url);
  $('setup').hidden = true;
  location.reload();
};
$('serverbtn').onclick = () => showSetup(true);

if (PACKAGED) {
  $('serverbtn').hidden = false;
  // Voice runs through Android's own recogniser here, not the Web Speech API.
  if (!Speech.available()) {
    $('mic').disabled = true;
    $('mic').title = 'Switch ears to Gemini to ask out loud on this device';
  }
}

if (showSetup(false)) throw new Error('awaiting server address');

loadIndex().then(() => {
  const qs = new URLSearchParams(location.search);
  // ?brain=/&voice= preselect the combination, so a blind A/B can be driven from links
  if (qs.get('brain')) $('brain').value = qs.get('brain');
  if (qs.get('voice')) $('voice').value = qs.get('voice');
  syncMode();
  if (qs.has('speed')) board.speed = parseFloat(qs.get('speed'));
  if (qs.has('ops') && !qs.has('demo')) {
    setLive(true);
    drawLegend();
    fetch(qs.get('ops')).then(r => r.json())
      .then(o => qs.has('still') ? board.pushInstant(o) : board.push(o));
    return;
  }
  if (qs.has('check')) return report();      // run the checks on the page as it lands
  if (qs.has('demo'))
    return fixture(qs).catch(async (e) => {
      // A fixture that dies silently makes the suite report "never reported", which
      // says nothing about why. Surface the error as a failed check instead.
      document.body.dataset.checks = JSON.stringify(
        { board: '?', ops: -1, fail: [`fixture threw: ${e && (e.stack || e.message)}`] });
      document.body.dataset.ready = '1';
    });
  document.body.dataset.ready = '1';
}).catch(e => phase('ready', 'load failed'));
