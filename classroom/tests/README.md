# Classroom tests

Three suites, cheapest first. Run them in this order.

## 1 · Layout invariants — free, ~0.5s

```bash
python3 -m pytest classroom/tests/test_layout.py -q
```

Pure functions: `sanitise()` and `layout()`. Asserts the composition rules — nothing
overlaps, nothing leaves the board, text never crosses into the diagram gutter, the
video corner stays clear, a short answer is centred rather than flung to the edges,
underlines attach to their heading, long lines wrap without orphans. Every case runs at
both 16:9 and the ~3:1 the real board actually is.

Bounding boxes are measured against `board.js`, not guessed, so the two cannot drift.

## 2 · Rendered page — free, ~40s

```bash
bash classroom/run.sh 8756 &            # the app must be up
python3 classroom/tests/make_fixtures.py
bash classroom/tests/visual.sh 8756
```

Drives the real page in headless Chrome at twelve viewport-and-state combinations and runs
`selfCheck()` inside it: no two pieces of chrome share a pixel, the video and legend stay
inside the board, the page never scrolls sideways, the canvas has a real basis, the chalk
font actually applied, no line overruns its column, and the board's text equivalent
matches what is written on it. Leaves screenshots in `classroom/shots/`.

The states are worth knowing, because each one is a row that can appear and shove the
rest of the app: `landing` (playback, no board), `waiting` (skeleton, no ops),
`transcript` (drawer open), `teacher` (question queue), `offline` (connection banner),
`interactive` (the student's own marks).

Two of them were added after the layout they cover shipped broken, and both failures
were the same mistake — a fixed grid with conditional children:

* the connection banner was a fourth child of a three-row grid, so it took the flexible
  row from the stage;
* during playback four of the stage's five rows are `display:none`, so the board landed
  on row 1 (`auto`) instead of the flexible row and collapsed to **1440x0**, taking the
  lesson video with it. That is the blank white landing page.

Both are why the body and the stage are now flex columns. Before this, every case forced
board mode and nothing covered the page as it actually loads.

`10-interactive` is not a layout check: it drives the infinite canvas through its real
API and asserts the view round-trips, that zoom keeps the point under the cursor fixed,
that `clearAll()` between questions wipes the tutor's answer and never the student's
marks, and that the eraser hits what was aimed at. Its failures are reported alongside
the layout ones.

The font assertion is not paranoia: `ctx.font = "24px var(--font-hand)"` is an invalid
canvas font string that is *silently discarded*, and the board rendered every glyph at
the default 10px sans-serif until that was caught here.

## 3 · End to end — spends real quota, ~11s

```bash
SAARTHI_LIVE=1 python3 -m pytest classroom/tests/test_live.py -q
```

One real question over the real WebSocket against the real models. Asserts the tutor
both speaks and writes, that no internal token leaks into speech, that the server (not
the model) placed every op, and that the answer does not overlap itself.

`SAARTHI_TOPIC` and `SAARTHI_Q` override the lesson and the question — use them to
exercise the out-of-scope path, which used to answer in speech and leave the board
blank.


## Keyboard

Every control is reachable without a pointer.

Anywhere: `space` play/pause, `←`/`→` skip 10s, `H` raise hand, `T` transcript,
`M` mute the voice, `Q` teacher mode, `Esc` back to the lesson.

On the board: `V` move, `P` draw, `X` type, `E` erase, `I` add an image, `F` fit,
`+`/`−` zoom, `⌘Z` undo, and hold `space` to pan with any tool selected.

## A note on caching

The server sends `Cache-Control: no-store` for the app shell. Without it aiohttp's
static handler sends only ETag and Last-Modified, Chrome caches heuristically, and you
get a page built from new markup and old CSS — which looks like a catastrophic layout
bug and is not one. Lesson media under `/media/` is still cached normally.
