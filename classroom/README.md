# Saarthi Classroom

Pre-recorded lesson + live blackboard Q&A. One web core; the same build runs in a
browser, inside a mobile WebView shell, and full-screen on a smart board.

```bash
bash classroom/run.sh 8756       # uses the project venv; run from anywhere
open http://localhost:8756
```

`python classroom/server/app.py` will fail — that is the system Python and the deps live
in `../micro-lectures/.venv`. The server also expects the repo root as its working
directory, which `run.sh` handles.

Try it without a model in the loop:

| URL | What it shows |
|---|---|
| `/?demo=1` | the scripted board, animated at teacher pace |
| `/?demo=1&still=1` | the same board rendered instantly |
| `/?demo=1&speed=6` | faster, for reviewing pacing |
| `/?demo=1&still=1&ops=/_tutor_ops.json` | a captured real answer on the real renderer |

## Why the board is a protocol, not a video

The lessons are Manim renders at roughly **6 seconds of render per second of video**. A
student who asks a question cannot wait 40 seconds. So the board is a stroke protocol:
the tutor emits ops, the client animates them at a human pace.

```json
{"op":"write","text":"angular nodes = l","at":[0.06,0.27],"color":"#ffe66d"}
{"op":"draw","shape":"circle","at":[0.62,0.36],"r":0.10}
{"op":"underline","from":[0.06,0.30],"to":[0.35,0.30]}
{"op":"erase","region":[0,0.6,1,1]}
```

Coordinates are normalised 0..1, which is what makes one command stream render
correctly on a phone and on a 75" board with no per-device code.

## Pausing without cutting a word

`export_manifest.py` ships every caption-cue boundary as `safe_stops`. "Raise hand" runs
on to the next one before pausing, so the lesson never stops mid-word. Those are the
same boundaries the burned captions use, so the pause always lands where a sentence ends.

## Where accuracy comes from

The recorded lessons are gated: every on-screen fact traces to `fact_sheet.md`. A live
model answering freely would break that guarantee in front of a classroom, so:

* the manifest carries the lesson's verified facts, in-scope and out-of-scope lists;
* the prompt is assembled **server-side** — the client cannot edit the grounding;
* the tutor is told to quote fact-sheet numbers exactly and to refuse out-of-scope
  questions with one line of orientation rather than improvising;
* `sanitise()` clamps every op into the drawable area and drops or repairs malformed
  ones, so a bad op cannot blank the board mid-answer.

Verified behaviour: an in-scope question returns the correct 2s-vs-2p distinction (F18);
`"sp3 hybridisation ka bond angle?"` is declined and correctly attributed to Chemical
Bonding.

## Layout

```
classroom/
  lessons/*.json      manifests (hb/export_manifest.py, one per topic)
  server/app.py       static + media + /ws tutor relay; holds the API key
  web/board.js        the renderer
  web/app.js          player, safe-stop pause, live session
  web/demo.js         scripted board, no model
```

## Built vs not

**Working:** manifests for all four lessons · player with chunk-accurate pause/resume ·
board renderer · scripted demo · text Q&A grounded on the fact sheet · op sanitiser ·
mobile/smart-board responsive layout.

**Not yet:** voice. The mic streams opus to `/ws` and the server acknowledges it, but the
Gemini Live bidirectional session is not wired — the text path exercises the whole board
pipeline, which was the part that had to be proven first. Also not done: a live talking
avatar (HeyGen streaming is the option), and offline caching for classrooms with poor
connectivity.
