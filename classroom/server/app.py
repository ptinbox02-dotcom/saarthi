#!/usr/bin/env python3
"""Classroom server: serves the lesson, and relays the live tutor.

Two jobs.

1. Static + media + manifests, so the same build artefacts the factory already produces
   are what the app consumes. Nothing is duplicated for the product.

2. A WebSocket relay to Gemini. The API key never reaches the browser — a classroom
   tablet is the last place to ship a credential — and putting the model behind the
   server is also where the accuracy gate lives: the prompt is assembled here, from the
   lesson's verified fact sheet, not from anything the client can edit.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from aiohttp import WSMsgType, web
except ModuleNotFoundError:                # almost always the system Python
    raise SystemExit(
        "aiohttp is missing — you are probably on the system Python.\n"
        "Run it with the project venv instead:\n"
        "    bash classroom/run.sh 8756")

ROOT = Path(__file__).resolve().parent.parent          # classroom/
REPO = ROOT.parent                                     # ml-gemini-hybrid/
LESSONS = ROOT / "lessons"
WEB = ROOT / "web"
MEDIA = (ROOT / "media").resolve()

sys.path.insert(0, str(REPO / "hb"))
from gclient import JUDGE, TTS_CHAIN, client, parse_json, retry   # noqa: E402
from google.genai import types                         # noqa: E402

# Native-audio Live model: speaks the answer instead of us synthesising it afterwards,
# which is what makes the Q&A feel live rather than transactional.
LIVE_MODEL = "gemini-3.1-flash-live-preview"

# The board vocabulary the model is allowed to emit. Deliberately small: a constrained
# protocol is far more reliable to generate than free-form SVG, and every op maps to
# something the renderer can animate at a human pace.
BOARD_TOOL = {
    "name": "draw",
    "description": ("Write and draw on the classroom blackboard, as a teacher would "
                    "while explaining. Call this as you speak, in small steps."),
    "parameters": {
        "type": "object",
        "properties": {
            "ops": {
                "type": "array",
                "description": "Board operations, in the order they should appear.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string",
                               "enum": ["write", "draw", "underline", "erase", "pause"]},
                        "text": {"type": "string"},
                        "shape": {"type": "string",
                                  "enum": ["line", "arrow", "circle", "rect", "axes",
                                           "sphere3d", "dumbbell3d", "axes3d"]},
                        "role": {"type": "string",
                                 "enum": ["term", "explain", "example", "tip",
                                          "question", "trap", "result"]},
                        "at": {"type": "array", "items": {"type": "number"}},
                        "from": {"type": "array", "items": {"type": "number"}},
                        "to": {"type": "array", "items": {"type": "number"}},
                        "r": {"type": "number"},
                        "size": {"type": "number"},
                        "color": {"type": "string"},
                        "label": {"type": "string"},
                        "region": {"type": "array", "items": {"type": "number"}},
                        "ms": {"type": "number"},
                    },
                    "required": ["op"],
                },
            }
        },
        "required": ["ops"],
    },
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string",
                "description": "What the tutor says aloud, 2-4 sentences."},
        "ops": {"type": "array", "items": BOARD_TOOL["parameters"]["properties"]["ops"]["items"]},
        "jump_beat": {"type": "integer",
                      "description": "Index of the beat in this lesson that best shows "
                                     "this answer, or -1 if none is relevant."},
    },
    "required": ["say", "ops", "jump_beat"],
}

SYSTEM = """You are Saarthi, a warm Indian JEE tutor answering a student who paused a
recorded lesson to ask a question. Reply in the SAME language mix as the lesson
({lang_name}), technical terms in English.

You are teaching at a blackboard. For every answer you MUST call `draw` with the board
operations that a teacher would put up while explaining — the key term, the number, a
quick diagram, the one-line answer. Keep the board sparse: six to twelve ops, not a wall
of text. Coordinates are 0..1 with (0,0) top-left; keep x within 0.04..0.92 and y within
0.10..0.88 so nothing runs off the board.

ACCURACY RULE — this matters more than fluency. The recorded lesson is gated so that
every on-screen fact traces to a verified fact sheet. Your answers must hold the same
line:
* Prefer the verified facts below. Quote their numbers exactly.
* If the question falls outside the lesson's scope, say so plainly and give one
  sentence of orientation — do not improvise detail.
* Never invent a numeric value. If you do not have it, say you will come back to it.

WHERE THE STUDENT IS
Lesson: {topic}   Beat: {beat_title}
Beats in this lesson (index · title): {beat_list}
Just heard: {narration}
On screen: {onscreen}

VERIFIED FACTS
{facts}

IN SCOPE: {in_scope}
OUT OF SCOPE (name only, never derive): {out_of_scope}

Return JSON: `say` is what you speak aloud (2-4 sentences), `ops` are the board
operations, `jump_beat` is the beat to rewatch. ALWAYS fill all three. The board supports
the voice, it does not replace it — a silent board is a failed answer.

Set `jump_beat` to the beat index whose recording best demonstrates your answer, so the
student can rewatch exactly that moment. Use -1 when nothing in the lesson shows it.

Palette: white for working, #ffe66d for the key result, #fc6255 for a warning or a
common mistake. Do not use other colours."""


def build_prompt(ctx: dict) -> str:
    b = ctx.get("beat") or {}
    facts = "\n".join(f"- [{k}] {v['claim']}: {v['detail']}"
                      for k, v in (ctx.get("facts") or {}).items())
    beat_list = " | ".join(f"{b['index']}·{b['title']}" for b in (ctx.get("beats") or []))
    return SYSTEM.format(
        beat_list=beat_list or "—",
        lang_name="Gujarati mixed with English (Gujlish)"
        if ctx.get("language") == "gu" else "Hindi mixed with English (Hinglish)",
        topic=ctx.get("topic", ""), beat_title=b.get("title", "—"),
        narration=(b.get("narration") or "")[:900],
        onscreen=(b.get("onscreen") or "")[:400],
        facts=facts or "(none supplied)",
        in_scope="; ".join(ctx.get("in_scope") or []) or "—",
        out_of_scope="; ".join(ctx.get("out_of_scope") or []) or "—",
    )


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    ctx: dict = {}
    c = shared_client()

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        kind = data.get("type")

        if kind == "context":
            ctx = data
            await ws.send_json({"type": "status", "text": "tutor ready"})

        elif kind == "interrupt":
            # the student cut in; nothing to cancel server-side yet, but it is recorded
            audit(ctx.get("topic", "unknown"), {"event": "interrupt"})

        elif kind == "mode":
            # the demo switch; validated here so an impossible pairing is refused once
            for k in ("brain", "voice"):
                if data.get(k):
                    ctx[k] = data[k]

        elif kind == "board":
            # the board changed size — the wrap budget depends on its aspect ratio
            ctx["board_aspect"] = float(data.get("aspect") or 16 / 9)

        elif kind == "live_ask":
            if data.get("answer_lang"):
                ctx["answer_lang"] = data["answer_lang"]
            if data.get("board_aspect"):
                ctx["board_aspect"] = float(data["board_aspect"])
            q = data.get("text", "")
            # Every reply carries the id of the question that caused it. Without this a
            # late `audio_done` from an abandoned turn cleared the spinner belonging to
            # the turn after it — or never arrived, and left it spinning for good.
            turn = data.get("turn")
            if not spend_ask():
                await ws.send_json({
                    "type": "error", "turn": turn,
                    "text": ("Today's question budget for this preview is used up. "
                             "The recording still plays.")})
                await ws.send_json({"type": "audio_done", "turn": turn})
                continue
            shot = decode_shot(data.get("image"))
            try:
                await ws.send_json({"type": "status", "text": "tutor speaking…",
                                    "turn": turn})
                brain = ctx.get("brain") or "gemini"
                voice = ctx.get("voice") or "live"
                bad = combo_error(brain, voice)
                if bad:
                    await ws.send_json({"type": "error", "text": bad, "turn": turn})
                    await ws.send_json({"type": "audio_done", "turn": turn})
                    continue
                runner = run_live if voice == "live" else functools.partial(
                    run_spoken, brain=brain, voice=voice)
                said, leaked, n_ops, jump, usage = await runner(ws, ctx, q, turn, shot)
                await ws.send_json({"type": "status", "text": "answered", "turn": turn})
                audit(ctx.get("topic", "unknown"), {
                    "event": "answer", "lang": ctx.get("answer_lang"),
                    "at": round(float(ctx.get("at") or 0), 1),
                    "beat": (ctx.get("beat") or {}).get("index"),
                    "q": q, "a": said, "leaked_tokens": leaked,
                    "board_ops": n_ops, "referred_beat": jump,
                    "brain": usage.get("brain", "gemini"),
                    "voice": usage.get("voice", "live"),
                    "tokens": {k: v for k, v in usage.items()
                               if k.startswith(("live_", "board_", "tts_"))},
                })
            except Exception as e:                      # noqa: BLE001
                await ws.send_json({"type": "error", "text": str(e)[:220], "turn": turn})
                audit(ctx.get("topic", "unknown"),
                      {"event": "error", "q": q, "error": str(e)[:200]})

        elif kind == "ask":
            q = data.get("text", "")
            try:
                # Structured output rather than a tool call: Gemini returns a
                # function call INSTEAD of text, so the tool-call version drew a board
                # and said nothing. One JSON object gives both, every time.
                r = c.models.generate_content(
                    model=JUDGE,
                    contents=f"{build_prompt(ctx)}\n\nSTUDENT ASKS: {q}",
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ANSWER_SCHEMA,
                        temperature=0.4,
                    ),
                )
                ans = parse_json(r.text)
                spoken = (ans.get("say") or "").strip()
                ops = ans.get("ops") or []
                ops = sanitise(ops)
                if ops:
                    await ws.send_json({"type": "ops", "ops": ops, "turn": turn})

                # Resolve the model's beat choice to a real timestamp here, not in the
                # browser: the manifest is the authority on where each beat starts.
                jb = ans.get("jump_beat", -1)
                beat = next((b for b in (ctx.get("beats") or [])
                             if b.get("index") == jb and b.get("start") is not None), None)
                if beat:
                    await ws.send_json({"type": "jump", "at": beat["start"],
                                        "beat": jb, "title": beat["title"]})
                await ws.send_json({"type": "text", "role": "tutor",
                                    "text": spoken or "(no spoken answer returned)"})
                await ws.send_json({"type": "status", "text": "answered"})
            except Exception as e:                      # noqa: BLE001
                await ws.send_json({"type": "error", "text": str(e)[:200]})

        elif kind == "audio":
            # Voice arrives here as opus chunks. Wiring these to Gemini Live's
            # bidirectional session is the next step; text Q&A already exercises the
            # full board path, which is the part that had to be proven first.
            await ws.send_json({"type": "status", "text": "voice: not yet wired"})

        elif kind == "audio_end":
            pass

    return ws


# ---- layout ---------------------------------------------------------------
# Coordinates from the model cannot be trusted: it has no idea what is already on the
# board, so two answers (or a heading and a reference line) land on the same spot and
# overlap into mush. Positions are therefore ASSIGNED here from a zone template, and the
# model's x/y are discarded. Text stacks down the left column, diagrams take the right,
# the common-mistake line owns the bottom. That also gives every answer the same
# deliberate composition instead of scattering to the corners.
ZONES = {
    "text_x": 0.06,          # left margin of the working column
    "text_right": 0.66,      # working column ends here; diagrams start after the gutter
    "top": 0.17,             # below the legend
    "bottom": 0.93,
    "line_h": 0.078,
    "gap": 1.15,             # blank lines between the working and the trap / tip band
}

# Diagram positions AND scale, keyed by how many there are. The layout owns the radius
# as well as the position, because the two cannot be chosen independently: three
# diagrams at the size that suits one of them overlap each other and slide up under the
# lesson video. Fixed slots also stop the fourth diagram walking off the bottom.
# Each entry is (x, y, r); the band runs from 0.26 (clear of the video) to 0.88.
DIAGRAM_SLOTS = {
    1: [(0.80, 0.57, 0.150)],
    2: [(0.80, 0.42, 0.095), (0.80, 0.72, 0.095)],
    3: [(0.80, 0.36, 0.065), (0.80, 0.57, 0.065), (0.80, 0.78, 0.065)],
}
MAX_DIAGRAMS = 3


def _wrap(text: str, size: float, width: float, aspect: float = 16 / 9) -> list:
    """Split into lines that fit `width` (normalised) at font `size` (fraction of H).

    The board is drawn in normalised coordinates but the font is sized against the
    canvas height, so the character budget depends on the aspect ratio — and the board
    is nowhere near 16:9 once the chrome has taken its rows, so the client reports the
    real one. 0.52 em is the measured average advance of the chalk stack over Latin and
    Devanagari copy; the renderer shrinks to fit as a backstop, so an underestimate here
    costs nothing but a short line.
    """
    per_char = size * 0.52 / max(1.2, aspect)
    budget = max(8, int(width / per_char))
    lines, cur = [], ""
    for w in str(text).split():
        cand = f"{cur} {w}".strip()
        if len(cand) > budget and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    # An orphan — one short word alone on the last line — reads as a mistake on a board.
    # Let the previous line run up to a tenth over budget to take it back.
    if len(lines) > 1 and len(lines[-1]) <= 8:
        merged = f"{lines[-2]} {lines[-1]}"
        if len(merged) <= budget * 1.10:
            lines[-2:] = [merged]
    return lines


def layout(ops: list, aspect: float = 16 / 9, start_row: int | None = None) -> list:
    """Assign every position on the board, from the role and the running order.

    The model is told not to send coordinates, and whatever it does send is discarded
    here, so overlap stops being something to detect and repair after the fact. Two
    properties matter and both are enforced by construction:

    * nothing collides — text stacks down a measured column, diagrams take reserved
      slots to its right, and a trap or a tip sits in its own band underneath;
    * a short answer does not look abandoned — the block is centred in the writing
      area rather than pinned to a fixed top and a fixed bottom, which is what left
      two lines marooned at opposite ends of an empty board.

    `start_row` switches this into streaming mode: ops arrive in batches while the model
    is still writing, so the total height is not yet known and the composition cannot be
    centred. It anchors to the top instead and each batch continues below the last —
    which is how a person fills a board anyway.
    """
    streaming = start_row is not None
    z = ZONES
    text_w = z["text_right"] - z["text_x"]
    big = ("term", "result")

    # --- pass 1: wrap first, so the true line count is known before anything is placed
    body, deferred, diagrams, order = [], [], [], []
    for o in ops:
        o = dict(o)
        order.append(o)
        if o["op"] == "draw":
            diagrams.append(o)
        elif o["op"] in ("write",):
            role = o.get("role")
            # sized to be read from the back of a room, not from a laptop
            o["size"] = 0.058 if role in big else 0.045
            o["_lines"] = _wrap(o.get("text", ""), o["size"], text_w, aspect)
            (deferred if role in ("trap", "tip") else body).append(o)

    def block_rows(group):
        return sum(len(o["_lines"]) * (1.2 if o.get("role") in big else 1.0) for o in group)

    rows = block_rows(body) + (block_rows(deferred) + z["gap"] if deferred else 0)
    avail = z["bottom"] - z["top"]
    if streaming:
        line_h = z["line_h"]
        y = z["top"] + start_row * line_h
    else:
        line_h = min(z["line_h"], avail / rows) if rows else z["line_h"]
        # centre instead of stretching: a two-line answer should read as a two-line
        # answer, not as two lines flung to opposite edges
        y = z["top"] + max(0.0, (avail - rows * line_h)) / 2

    # Beyond three, a board stops being a board. The extras are dropped rather than
    # shrunk further, and the drop is logged so it is never silent.
    if len(diagrams) > MAX_DIAGRAMS:
        print(f"  board: {len(diagrams)} diagrams requested, drawing the first "
              f"{MAX_DIAGRAMS}")
    slots = DIAGRAM_SLOTS[min(len(diagrams), MAX_DIAGRAMS)] if diagrams else []
    if streaming and diagrams:
        # a batch cannot know how many diagrams the whole answer will have, so it takes
        # the roomiest arrangement and fills it in arrival order
        slots = DIAGRAM_SLOTS[MAX_DIAGRAMS]

    # --- pass 2: place, preserving the model's ordering
    out, n_diagram = [], 0
    for o in order:
        if o["op"] == "draw":
            if n_diagram >= len(slots):
                continue
            x, dy, r = slots[n_diagram]
            o["at"] = [x, dy]
            o["r"] = r
            n_diagram += 1
            out.append(o)
        elif o["op"] in ("erase", "pause", "underline"):
            out.append(o)
        elif o["op"] == "write" and o.get("role") not in ("trap", "tip"):
            step = line_h * (1.2 if o.get("role") in big else 1.0)
            for i, line in enumerate(o.pop("_lines")):
                out.append(dict(o, text=line, at=[z["text_x"], round(y, 3)],
                                maxw=text_w, **({"cont": True} if i else {})))
                y += step

    if deferred:
        y += line_h * z["gap"]
        for o in deferred:
            for i, line in enumerate(o.pop("_lines")):
                out.append(dict(o, text=line, at=[z["text_x"], round(y, 3)],
                                maxw=text_w, **({"cont": True} if i else {})))
                y += line_h

    # --- pass 3: an underline follows the write it belongs to, at its FINAL position.
    # Deriving the span in the sanitiser left it stranded wherever the model had put the
    # heading, which is the one place the heading is guaranteed not to be.
    prev, keep = None, []
    for o in out:
        if o["op"] == "write":
            prev = o
        elif o["op"] == "underline":
            if prev is None:
                continue                       # nothing written yet; drop it
            x, uy = prev["at"]
            width = min(text_w, 0.52 * prev["size"] * (len(str(prev.get("text", ""))) + 2)
                        / max(1.2, aspect))
            uy = round(min(z["bottom"], uy + 0.022), 3)
            o["role"] = prev.get("role")       # an underline belongs to its heading
            o["from"] = [x, uy]
            o["to"] = [round(min(z["text_right"], x + width), 3), uy]
        keep.append(o)
    return keep


def sanitise(ops: list) -> list:
    """Repair or drop malformed board ops before they reach the renderer.

    The model gets the protocol right most of the time, but "most" is not good enough
    for something projected in a classroom: one op missing its coordinates would throw
    mid-answer and blank the board. Everything is clamped into the safe drawing area,
    and an op that cannot be repaired is dropped rather than rendered wrongly.
    """
    XMIN, XMAX, YMIN, YMAX = 0.04, 0.92, 0.10, 0.88
    ALLOWED = {"write", "draw", "underline", "erase", "pause"}
    SHAPES = {"line", "arrow", "circle", "rect", "axes",
              "sphere3d", "dumbbell3d", "axes3d"}
    ROLES = {"term", "explain", "example", "tip", "question", "trap", "result"}

    def clamp_pt(pt):
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            return None
        try:
            return [min(XMAX, max(XMIN, float(pt[0]))),
                    min(YMAX, max(YMIN, float(pt[1])))]
        except (TypeError, ValueError):
            return None

    out, last_write = [], None
    for raw in ops or []:
        if not isinstance(raw, dict):
            continue
        o = dict(raw)
        if o.get("op") not in ALLOWED:
            continue
        # role is the only sanctioned way to colour a line; a raw hex is dropped so the
        # palette cannot drift lesson to lesson
        if o.get("role") not in ROLES:
            o.pop("role", None)
        o.pop("color", None)
        for k in ("at", "from", "to"):
            if k in o:
                pt = clamp_pt(o[k])
                if pt is None:
                    o.pop(k)
                else:
                    o[k] = pt

        if o["op"] == "write":
            if not str(o.get("text", "")).strip():
                continue
            o.setdefault("at", [0.06, 0.2])
            last_write = o
        elif o["op"] == "underline":
            # the model routinely omits the span; underline whatever it just wrote
            if "from" not in o or "to" not in o:
                if not last_write:
                    continue
                x, y = last_write["at"]
                width = min(0.5, 0.016 * len(str(last_write.get("text", ""))))
                o["from"] = [x, min(YMAX, y + 0.028)]
                o["to"] = [min(XMAX, x + width), min(YMAX, y + 0.028)]
        elif o["op"] == "draw":
            if o.get("shape") not in SHAPES:
                continue
            if o["shape"] in ("line", "arrow", "rect") and ("from" not in o or "to" not in o):
                continue
            if o["shape"] in ("circle", "axes", "sphere3d", "dumbbell3d", "axes3d") \
                    and "at" not in o:
                continue
        elif o["op"] == "erase":
            reg = o.get("region")
            if not (isinstance(reg, list) and len(reg) == 4):
                continue
        out.append(o)
    return out


LIVE_TOOLS = [types.Tool(function_declarations=[
    BOARD_TOOL,
    {
        "name": "refer_to_beat",
        "description": ("Point the student back to the moment in the recording that "
                        "best demonstrates this answer, so they can rewatch it."),
        "parameters": {
            "type": "object",
            "properties": {
                "beat_index": {"type": "integer"},
                "why": {"type": "string", "description": "Short reason, shown as a chip."},
            },
            "required": ["beat_index"],
        },
    },
])]


LANG_NAME = {
    "hi-IN": "Hindi mixed with English (Hinglish)",
    "gu-IN": "Gujarati mixed with English (Gujlish)",
    "en-IN": "Indian English",
    "mr-IN": "Marathi, with technical terms in English",
    "bn-IN": "Bengali, with technical terms in English",
    "ta-IN": "Tamil, with technical terms in English",
    "te-IN": "Telugu, with technical terms in English",
}

BOARD_RULES = """ALWAYS return board ops: the key term, the number, a small diagram, the one-line answer.
4-8 ops, sparse — a board is not a slide.

DO NOT set x, y or coordinates. Layout is assigned for you from the role, so that lines
never overlap: text stacks down the left, diagrams sit on the right, and a `trap` or
`tip` is pinned to the bottom band. Just choose the right role and the right order.

COLOUR IS MEANING. Never set a colour. Set `role`, and it renders in the one colour and
glyph that role always uses:
  term (a definition) · explain (working) · example · tip · question · trap (a common
  mistake) · result (the key answer)

DRAW, DON'T ONLY WRITE. If the answer involves a shape, an axis, an apparatus, a
direction, or two things being compared, put a diagram on the board — a student who
asked "I don't understand" is telling you words were not enough. A board with four
lines of text and no picture is a paragraph, not a blackboard.

THE BOARD IS FLAT. It cannot show a real 3-D object, so never claim to show one unless
you have drawn it. For depth use these shapes, which draw a proper projection:
  "sphere3d"   — a sphere / s orbital, with meridian and equator
  "dumbbell3d" — a p orbital: two lobes plus the nodal plane seen edge-on
  "axes3d"     — x, y, z axes in isometric projection
Anything described in words must exist on the board.

Set `jump_beat` to the beat whose recording best shows this, or -1."""


SCRIPT_WINDOW = 2          # beats either side of where the student is standing


def where_in_lesson(ctx: dict):
    """Which beat the student is standing in, by whatever the client knew.

    It sends `beat` when a beat has started and null before the first one, but it always
    sends `at` — the playback position. Trusting `beat` alone meant a question asked in
    the opening seconds fell back to shipping the entire script, which is exactly the
    cost the window exists to avoid.
    """
    here = ctx.get("beat") or {}
    if here.get("index") is not None:
        return here["index"]
    at = ctx.get("at")
    starts = [(b.get("start"), b.get("index")) for b in (ctx.get("beats") or [])
              if b.get("start") is not None and b.get("index") is not None]
    if at is None or not starts:
        return None
    started = [i for st, i in sorted(starts) if st <= float(at)]
    return started[-1] if started else None


def lesson_script(beats: list, at_index) -> str:
    """The lesson as the tutor needs to see it, not the whole thing every time.

    Sending all nine narrations to both models on every question was ~2,900 input tokens
    per call, twice per question, and on the Live API input is billed at roughly ten
    times ordinary text. Most of it earned nothing: a question asked at beat 3 is almost
    never answered by the verbatim wording of beat 8.

    So the beats around the student arrive in full, and the rest as titles — enough for
    the tutor to say "that comes up later, at the nodal-plane bit" and to pick a
    jump_beat, without paying to re-read the script it will not quote.
    """
    if at_index is None:
        return "\n".join(f"[Beat {b['index']} — {b['title']}] {b.get('narration','')}"
                          for b in beats)
    out = []
    for b in beats:
        near = abs(int(b["index"]) - int(at_index)) <= SCRIPT_WINDOW
        out.append(f"[Beat {b['index']} — {b['title']}] {b.get('narration','')}" if near
                   else f"[Beat {b['index']} — {b['title']}] (not shown; ask about it "
                        f"and refer the student here)")
    return "\n".join(out)


def live_instruction(ctx: dict, for_board: bool = True) -> str:
    """Ground the tutor on the lesson, not on whatever it happens to know.

    The verified fact sheet is always sent in full — that is the accuracy boundary. The
    narration is windowed around where the student is standing; see lesson_script.

    `for_board` is the important half. The voice model has NO tools, so telling it about
    `draw` made it narrate the commands aloud — "call Draw trap zero point seven". The
    board rules now exist only in the prompt that can actually act on them.
    """
    beats = ctx.get("beats_full") or []
    script = lesson_script(beats, where_in_lesson(ctx))
    facts = "\n".join(f"- [{k}] {v['claim']}: {v['detail']}"
                       for k, v in (ctx.get("facts") or {}).items())
    # Language is decided in code from the student's selection, never negotiated by the
    # model: it used to apologise and ask "kaunsi bhasha chahiye" instead of answering.
    lang = LANG_NAME.get(ctx.get("answer_lang") or "",
                         LANG_NAME["gu-IN"] if ctx.get("language") == "gu"
                         else LANG_NAME["hi-IN"])
    here = ctx.get("beat") or {}
    return f"""You are Saarthi, a warm Indian JEE tutor at a blackboard. A student paused
the recording to ask you something.

LANGUAGE — NOT NEGOTIABLE: answer in {lang}. Keep technical terms in English. Never ask
which language to use, never apologise for the language, never offer language options,
never mix in a third language. Just answer.

Answer the question directly in at most 3 short sentences. No preamble, no repetition,
no "great question".

ACCURACY: prefer the verified facts below and quote their numbers exactly. Never invent a
number. If the question is outside this chapter, say so in one line and name where it
belongs — do not improvise detail.

STUDENT IS AT: beat {here.get('index')} — {here.get('title')}

FULL LESSON SCRIPT
{script}

VERIFIED FACTS
{facts}

OUT OF SCOPE: {'; '.join(ctx.get('out_of_scope') or []) or '—'}

{BOARD_RULES if for_board else ''}"""


# Two passes, because removing the NAME of a call leaves its arguments behind:
# `call Draw("trap", 0.70, 0.40, "10% outside?")` would otherwise be spoken as
# "trap, zero point seven, zero point four, ten percent outside".
COMMAND_TOKENS = re.compile(
    r"`[^`]*`"                                        # anything fenced in backticks
    r"|\bcall\s+[`'\"]?\w+[`'\"]?\s*(?:\([^)]*\))?(?:\s+with\s+\S+)?"
    r"|\b(?:draw|refer_to_beat|push_ops)\s*\([^)]*\)"
    r"|\b(?:refer_to_beat|push_ops)\b"
    r"|\{[^{}]*\"op\"[^{}]*\}"                         # a raw op object
    r"|\bop\s*[:=]\s*\w+"
    r"|\(\s*\d?\.\d+\s*,\s*\d?\.\d+\s*\)",          # bare coordinate tuples
    re.I)

# leftover argument debris at the head of a line: "trap", 0.70, 0.40, "" )
ORPHAN_ARGS = re.compile(r'^[\s,;:)\]]*(?:"[^"]*"|\'[^\']*\'|-?\d+(?:\.\d+)?)'
                         r'(?:\s*,\s*(?:"[^"]*"|\'[^\']*\'|-?\d+(?:\.\d+)?))*\s*\)?')


def strip_internals(text: str) -> tuple[str, int]:
    """Last line of defence before anything is spoken, captioned or logged.

    The board DSL is an internal protocol. If it reaches the narration channel the
    student hears "call Draw trap zero point seven", which destroys the illusion and the
    credibility with it. The real fix is upstream — the voice model is no longer told
    about tools it does not have — but this runs on every answer regardless, because one
    leaked line in a classroom is worse than a hundred caught in a log.
    """
    text = text or ""
    # Only sweep orphaned arguments when a command was actually removed from the HEAD of
    # the string. Running it unconditionally ate the "100" out of "100% boundary surface
    # banana possible hi nahi hai" — a sanitiser that damages correct teaching is worse
    # than the leak it was written to stop.
    head = COMMAND_TOKENS.match(text.lstrip())
    cleaned, n = COMMAND_TOKENS.subn(" ", text)
    if head:
        trimmed = ORPHAN_ARGS.sub("", cleaned.lstrip())
        if trimmed != cleaned.lstrip():
            n += 1
        cleaned = trimmed
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ;,·-")
    return cleaned, n


def speak_instruction(ctx: dict) -> str:
    """Voice only. Says nothing about drawing, because it has no drawing tool."""
    base = live_instruction(ctx, for_board=False)
    return base + """

You are ONLY speaking. You have no tools and no board controls. Never say, read out or
spell any command, function name, JSON, coordinate or code — the student hears exactly
what you say. Speak plain sentences and nothing else.

Answer in AT MOST 3 short sentences. Answer the question directly. Do not apologise, do
not offer options, do not ask which language to use, do not repeat yourself."""


MAX_AUDIO = 8 * 1024 * 1024         # a spoken question, not a podcast
TRANSCRIBE_PROMPT = (
    "Transcribe this student's spoken question exactly as said. This is an Indian "
    "student mixing {lang} with English technical terms — keep the technical terms in "
    "English and write the rest in Latin script, the way the student would type it. "
    "Do not translate, do not answer, do not add punctuation the speaker did not imply. "
    "Return only the transcript.")


def decode_audio(data_url) -> bytes | None:
    """A `data:audio/wav;base64,...` clip, or nothing.

    Same treatment as the board crop: this arrives from a browser, so it is size-capped
    and must actually be a RIFF/WAVE before any of it reaches the model.
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:audio/wav;base64,"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not (raw[:4] == b"RIFF" and raw[8:12] == b"WAVE") or len(raw) > MAX_AUDIO:
        return None
    return raw


async def api_transcribe(request):
    """Speech to text through the model, for when the device recogniser is not enough.

    Android's own recogniser decodes one language at a time, so a Hinglish question can
    come back with the English technical terms mangled into Devanagari — and those terms
    are the entire subject of the lesson. This route exists for that case: the model
    handles the code-switching natively, at the cost of a round trip and no partial
    results while the student is still speaking.
    """
    try:
        data = await request.json()
    except Exception:                                   # noqa: BLE001
        raise web.HTTPBadRequest(text="expected json")
    clip = decode_audio(data.get("audio"))
    if clip is None:
        raise web.HTTPBadRequest(text="expected a wav data url under 8 MB")
    if not spend_ask():
        raise web.HTTPTooManyRequests(text="daily budget used up")
    lang = LANG_NAME.get(data.get("language") or "", LANG_NAME["hi-IN"])
    try:
        r = await shared_client().aio.models.generate_content(
            model=JUDGE,
            contents=[TRANSCRIBE_PROMPT.format(lang=lang),
                      types.Part.from_bytes(data=clip, mime_type="audio/wav")],
            config=types.GenerateContentConfig(temperature=0.0))
        text = (r.text or "").strip().strip('"')
    except Exception as e:                              # noqa: BLE001
        return web.json_response({"error": str(e)[:200]}, status=502)
    audit(data.get("topic", "unknown"),
          {"event": "transcribe", "lang": data.get("language"), "chars": len(text)})
    return web.json_response({"text": text})


MAX_SHOT = 4 * 1024 * 1024          # a board crop, not a photo library


def decode_shot(data_url) -> bytes | None:
    """A `data:image/png;base64,...` crop of the board, or nothing.

    Rejected rather than trusted: this arrives from the browser, so it is size-capped
    and must actually be a PNG data URL before any of it reaches the model.
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(b"\x89PNG") or len(raw) > MAX_SHOT:
        return None
    return raw


SELECTION_NOTE = """
The student has HIGHLIGHTED part of the board and is asking about that part. The image
attached is exactly what they highlighted — it may be your own writing, a diagram, a
number, or their own working in their handwriting. Answer about what is in the image
first, and refer to it as "ye" / "this bit", the way you would if you were pointing at
it. If the image is their working and it is wrong, say where it goes wrong, kindly."""


# ---- the switch -----------------------------------------------------------------
# Two independent choices, deliberately not bundled: the brain that decides what to say
# and the mouth that says it. Harsh cannot feel an architecture, but he can feel a voice
# and a wait — and if those were bundled, a thumbs-down would not say which half he
# disliked.
BRAINS = {"gemini": JUDGE, "gemma": "gemma-4-31b-it"}
BOARD_THINKING = int(os.environ.get("SAARTHI_BOARD_THINKING", "128"))

_CLIENT = None
_BOARD_CLIENT = None


def board_client():
    """A second long-lived client, for the board only.

    The board and the voice talk to the same host at the same time, and sharing one
    client made them share a connection pool: the board's first token arrived at seven
    seconds under load against two seconds on its own. Two clients, two pools, no
    queueing behind each other. Still created once each — building one per call breaks
    the SDK's async retry layer outright.
    """
    global _BOARD_CLIENT
    if _BOARD_CLIENT is None:
        _BOARD_CLIENT = client()
    return _BOARD_CLIENT


def shared_client():
    """One client for the process, not one per call.

    Constructing a fresh genai.Client for every request and then using its `.aio` side
    blows up inside the SDK's own tenacity retry layer with a bare AssertionError — the
    request never even leaves the machine, and from the outside it looks like a hang.
    The paths that already worked all captured a client once; this makes that the rule
    rather than a coincidence.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = client()
    return _CLIENT
VOICES = ("live", "tts", "on-device")
TTS_VOICE = "Achird"          # the same voice the recorded lessons are narrated in
# Measured on one short sentence: 2.7-3.2s here against 4.0-4.7s for the 2.5 flash TTS.
# Same voice either way; this is a latency choice, not a change to how the tutor sounds.
TTS_REALTIME = "gemini-3.1-flash-tts-preview"

# Gemma is not a Gemini model and cannot be the one speaking: `voice=live` IS Gemini
# generating audio directly. The combination is refused rather than silently downgraded.
def combo_error(brain: str, voice: str) -> str | None:
    if brain not in BRAINS:
        return f"unknown brain {brain!r}"
    if voice not in VOICES:
        return f"unknown voice {voice!r}"
    if voice == "live" and brain != "gemini":
        return ("voice=live is Gemini speaking natively, so it cannot be paired with "
                "another brain. Use voice=tts with brain=gemma.")
    return None


SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
# A sentence is finished when the buffer itself ends on terminal punctuation —
# without this the last sentence of an answer was never spoken until the whole
# completion arrived, which is most of what streaming was supposed to buy.
SETTLED_END = re.compile(r"[.!?।]\s*$")


def sentences(text: str) -> list:
    """Split for streaming, not for grammar.

    The TTS speaks sentence by sentence so the first one can be heard while the rest is
    still being synthesised — without that, a TTS voice starts several seconds after a
    native-audio one and loses a comparison it should win. Fragments shorter than a few
    words are glued onto the previous sentence; synthesising "Yes." on its own costs a
    whole request and sounds clipped.
    """
    out = []
    for part in SENTENCE_END.split((text or "").strip()):
        part = part.strip()
        if not part:
            continue
        if out and len(part) < 15:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def _tts_once(text: str, model: str) -> bytes:
    r = shared_client().models.generate_content(
        model=model,
        contents=f"Say warmly, like a patient tutor at a blackboard:\n\n{text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=TTS_VOICE)))))
    for cand in (r.candidates or []):
        for part in (cand.content.parts or []):
            d = getattr(part, "inline_data", None)
            if d and d.data:
                return d.data
    return b""


def tts_pcm(text: str, model: str) -> bytes:
    """One sentence of speech as raw 24 kHz s16 mono PCM.

    Deliberately not the WAV that hb/tts.py produces for lesson rendering: the client
    already plays raw PCM on the Web Audio clock for the Live path, so both voices
    arrive through exactly one playback path.

    These preview TTS models return 503 UNAVAILABLE under very little load and carry
    per-model daily caps that do not scale with the billing tier. So a failure walks the
    same chain the lesson renderer uses: retry the transient ones, and on a hard refusal
    move to the next model rather than dropping the sentence. Every model in the chain
    speaks in the same voice, so the fallback is inaudible.
    """
    tried = []
    for candidate in [model] + [m for m in TTS_CHAIN if m != model]:
        tried.append(candidate)
        try:
            pcm = retry(lambda: _tts_once(text, candidate), tries=3, base=2.0,
                        what=f"tts/{candidate}")
            if pcm:
                if candidate != model:
                    print(f"  tts: fell through to {candidate} after {tried[:-1]}")
                return pcm
        except Exception as e:                          # noqa: BLE001
            print(f"  tts: {candidate} failed ({str(e)[:90]})")
    raise RuntimeError(f"every TTS model refused: {tried}")


def ops_so_far(buf: str, taken: int) -> tuple[list, int]:
    """Pull complete op objects out of a half-arrived JSON response.

    The board used to wait for the whole completion to parse, which took nine to
    thirteen seconds — so the tutor talked to an empty board and then wrote to an empty
    room after the voice had stopped. Streaming the same call and emitting each op the
    moment its closing brace arrives puts the first line on the board while the first
    sentence is still being spoken.

    Returns the ops after the first `taken`, and the new total. Scanning is brace
    counting with string and escape awareness — a `{` inside "90% likely" must not open
    an object, and a `\"` inside a string must not close one.
    """
    start = buf.find('"ops"')
    if start < 0:
        return [], taken
    start = buf.find("[", start)
    if start < 0:
        return [], taken
    out, depth, obj_start, in_str, esc = [], 0, None, False, False
    for i in range(start + 1, len(buf)):
        ch = buf[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    out.append(json.loads(buf[obj_start:i + 1]))
                except json.JSONDecodeError:
                    pass                       # not an op yet; wait for more bytes
                obj_start = None
        elif ch == "]" and depth == 0:
            break
    return out[taken:], len(out)


async def answer_once(ctx: dict, question: str, shot: bytes | None, brain: str):
    """One structured call that returns what to say AND what to write.

    The Live path needs two calls because the audio model cannot reliably emit
    structured output. Every other path can do it in one — which halves the input bill
    and, more usefully, means the voice and the board can no longer contradict each
    other, because they came from the same completion.
    """
    prompt = live_instruction(ctx) + (SELECTION_NOTE if shot else "")
    contents = [f"{prompt}\n\nSTUDENT ASKS: {question}"]
    if shot:
        contents.append(types.Part.from_bytes(data=shot, mime_type="image/png"))
    r = await shared_client().aio.models.generate_content(
        model=BRAINS[brain], contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            # Gemma honours response_schema (measured: 10/10) but produces bare lists
            # without it (measured: 0/10), so the schema is mandatory, not a hint.
            response_schema=ANSWER_SCHEMA, temperature=0.4))
    um = getattr(r, "usage_metadata", None)
    usage = {"board_in": getattr(um, "prompt_token_count", 0) or 0,
             "board_out": getattr(um, "candidates_token_count", 0) or 0} if um else {}
    ans = parse_json(r.text)
    ans["say"], _ = strip_internals(ans.get("say", ""))
    return ans, usage


async def run_spoken(ws, ctx: dict, question: str, turn, shot, brain: str, voice: str):
    """Speak and write at the same time, the way the Live path does.

    The first version of this ran the structured call to completion and only then began
    synthesising: the board appeared at 8-12s and the first word at 16-23s, against 1.8s
    for native audio. That would have lost the comparison on plumbing rather than on
    merit — the thing I said would happen if the TTS path were built naively, and then
    built naively anyway.

    So the spoken answer and the board come from two concurrent calls. The spoken one is
    streamed as plain text and each finished sentence is handed to the TTS immediately,
    so speech begins while the model is still writing. Sentences are synthesised in
    order, because they have to be heard in order.
    """
    stats = {"leaked": 0, "ops": 0, "jump": None, "brain": brain, "voice": voice,
             "live_in": 0, "live_total": 0, "board_in": 0, "board_out": 0,
             "say_in": 0, "say_out": 0, "tts_calls": 0}
    said_full = {"text": ""}
    # A queue, not a direct call: awaiting the synthesiser inside the streaming loop
    # stops the loop consuming chunks, so the model's own output stalls behind the
    # voice it is feeding. The speaker drains this in order, in its own task.
    to_speak: asyncio.Queue = asyncio.Queue()

    async def speaker():
        model = os.environ.get("TTS_MODEL", TTS_REALTIME)
        while True:
            sentence = await to_speak.get()
            if sentence is None:
                return
            if voice == "on-device":
                continue
            try:
                pcm = await asyncio.to_thread(tts_pcm, sentence, model)
            except Exception as e:                        # noqa: BLE001
                await ws.send_json({"type": "error", "turn": turn,
                                    "text": f"voice failed: {str(e)[:140]}"})
                continue
            if pcm:
                stats["tts_calls"] += 1
                await ws.send_json({"type": "audio", "turn": turn,
                                    "b64": base64.b64encode(pcm).decode()})

    async def speak():
        # The sentinel MUST be posted even if this fails, or the speaker waits on a
        # queue that will never be fed and the whole turn hangs behind it — which is
        # exactly what happened: an exception in here turned into a dead socket rather
        # than an error the student could see.
        try:
            await speak_inner()
        finally:
            await to_speak.put(None)

    async def speak_inner():
        prompt = (speak_instruction(ctx) + (SELECTION_NOTE if shot else "")
                  + "\n\nReply with the spoken answer only — no JSON, no board "
                    "commands, no headings.")
        contents = [f"{prompt}\n\nSTUDENT ASKS: {question}"]
        if shot:
            contents.append(types.Part.from_bytes(data=shot, mime_type="image/png"))
        buf, spoken = "", 0
        stream = await shared_client().aio.models.generate_content_stream(
            model=BRAINS[brain], contents=contents,
            config=types.GenerateContentConfig(temperature=0.3))
        async for chunk in stream:
            um = getattr(chunk, "usage_metadata", None)
            if um:
                stats["say_in"] = getattr(um, "prompt_token_count", 0) or 0
                stats["say_out"] = getattr(um, "candidates_token_count", 0) or 0
            buf += chunk.text or ""
            done = sentences(buf)
            # A sentence is settled once another has started after it — or once the
            # buffer itself ends on terminal punctuation, which is the case that
            # mattered: a two-sentence answer otherwise waited for the whole completion
            # before speaking its first word, and the streaming bought nothing.
            settled = len(done) if SETTLED_END.search(buf) else len(done) - 1
            while settled > spoken:
                await to_speak.put(done[spoken]); spoken += 1
            said_full["text"] = strip_internals(buf)[0]
            await ws.send_json({"type": "text", "role": "tutor", "turn": turn,
                                "text": said_full["text"], "leaked": 0})
        for tail in sentences(buf)[spoken:]:
            await to_speak.put(tail)

    async def board():
        """The chalk, streamed. See stream_board."""
        await stream_board(ws, ctx, question, turn, shot, brain, stats)

    async def _board_unused():
        ans, usage = await answer_once(ctx, question, shot, brain)
        stats.update(usage)
        raw = sanitise(ans.get("ops") or [])
        if not raw and (ans.get("say") or "").strip():
            raw = sanitise([{"op": "write", "role": "explain", "text": ans["say"].strip()}])
        ops = layout(raw, ctx.get("board_aspect") or 16 / 9)
        stats["ops"] = len(ops)
        if ops:
            await ws.send_json({"type": "ops", "ops": ops, "turn": turn})
        jb = ans.get("jump_beat", -1)
        beat = next((b for b in (ctx.get("beats") or [])
                     if b.get("index") == jb and b.get("start") is not None), None)
        if beat:
            stats["jump"] = jb
            await ws.send_json({"type": "jump", "at": beat["start"], "turn": turn,
                                "beat": jb, "title": beat["title"], "why": beat["title"]})

    results = await asyncio.gather(speaker(), speak(), board(), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            await ws.send_json({"type": "error", "text": str(r)[:200], "turn": turn})
    if voice == "on-device":
        await ws.send_json({"type": "status", "turn": turn,
                            "text": "on-device voice needs the Android build"})
    await ws.send_json({"type": "audio_done", "turn": turn})
    return said_full["text"], 0, stats["ops"], stats["jump"], stats


async def stream_board(ws, ctx: dict, question: str, turn, shot, brain: str, stats: dict,
                       cursor: dict | None = None):
    """Write the board while the tutor is still talking.

    The answer is one structured call, but it is consumed as a stream: each op is sent
    the moment its closing brace arrives, instead of after the whole completion parses.
    That was the gap the student saw — the voice began at two seconds and the board
    stayed blank until nine or thirteen, then carried on writing after the voice had
    stopped. Ops now start landing with the first sentence.
    """
    prompt = live_instruction(ctx) + (SELECTION_NOTE if shot else "")
    contents = [f"{prompt}\n\nSTUDENT ASKS: {question}"]
    if shot:
        contents.append(types.Part.from_bytes(data=shot, mime_type="image/png"))

    cursor = cursor if cursor is not None else {"row": 0}
    buf, taken, first_at = "", 0, None
    t0 = asyncio.get_event_loop().time()
    stream = await board_client().aio.models.generate_content_stream(
        model=BRAINS[brain], contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ANSWER_SCHEMA, temperature=0.4,
            # The board was late because the model thought in silence first: measured
            # 6-11.5s before the first token, then the whole answer in 1.5s. Streaming
            # the parse could not help — there was nothing to parse until the thinking
            # finished. A small thinking budget brings the first token to ~1.9s, which
            # is when the voice starts, and the answer is no worse: same op count, still
            # a diagram. (A budget of 0 is rejected outright by this model.)
            thinking_config=types.ThinkingConfig(thinking_budget=BOARD_THINKING)))
    async for chunk in stream:
        um = getattr(chunk, "usage_metadata", None)
        if um:
            stats["board_in"] = getattr(um, "prompt_token_count", 0) or 0
            stats["board_out"] = getattr(um, "candidates_token_count", 0) or 0
        buf += chunk.text or ""
        fresh, taken = ops_so_far(buf, taken)
        if not fresh:
            continue
        ops = layout(sanitise(fresh), ctx.get("board_aspect") or 16 / 9,
                     start_row=cursor["row"])
        if ops:
            if first_at is None:
                first_at = asyncio.get_event_loop().time() - t0
                stats["board_first_s"] = round(first_at, 1)
            cursor["row"] += len([o for o in ops if o["op"] == "write"])
            stats["ops"] = stats.get("ops", 0) + len(ops)
            await ws.send_json({"type": "ops", "ops": ops, "turn": turn})

    # Whatever the stream produced, the rest of the answer still has to be handled: a
    # refusal with no ops at all should still leave something readable on the board.
    try:
        ans = parse_json(buf)
    except Exception:                                    # noqa: BLE001
        ans = {}
    if not stats.get("ops"):
        said, _ = strip_internals(ans.get("say", ""))
        if said.strip():
            ops = layout(sanitise([{"op": "write", "role": "explain", "text": said.strip()}]),
                         ctx.get("board_aspect") or 16 / 9)
            stats["ops"] = len(ops)
            if ops:
                await ws.send_json({"type": "ops", "ops": ops, "turn": turn})
    jb = ans.get("jump_beat", -1)
    beat = next((b for b in (ctx.get("beats") or [])
                 if b.get("index") == jb and b.get("start") is not None), None)
    if beat:
        stats["jump"] = jb
        await ws.send_json({"type": "jump", "at": beat["start"], "turn": turn,
                            "beat": jb, "title": beat["title"], "why": beat["title"]})
    return ans


async def run_live(ws, ctx: dict, question: str, turn=None, shot: bytes | None = None):
    """Speak the answer while the board is being written.

    Two models, deliberately. The native-audio Live model is excellent at conversational
    speech and poor at function calling — asked to speak AND call `draw`, it reliably
    did the first and skipped the second, leaving a talking tutor at a blank board. So
    the board and the video reference come from a structured text call instead, which
    was already proven reliable, and the two run CONCURRENTLY: chalk starts appearing
    while the voice is still on its first sentence, which is what a real teacher does.
    """
    c = shared_client()

    async def speak(stats):
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=speak_instruction(ctx) + (SELECTION_NOTE if shot else ""),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # NOT a length control. This budget counts AUDIO tokens, and speech runs
            # at roughly 25 of them a second, so the old value of 320 was a hard stop
            # about twelve seconds in — it truncated almost every answer mid-sentence.
            # Brevity is asked for in the instruction, where it belongs; this is only a
            # runaway guard.
            max_output_tokens=2048,
            temperature=0.3,
        )
        said = ""
        parts = [types.Part(text=question)]
        if shot:
            parts.append(types.Part(inline_data=types.Blob(mime_type="image/png",
                                                           data=shot)))
        async with c.aio.live.connect(model=LIVE_MODEL, config=cfg) as sess:
            # turn_complete must be explicit. With a single text part the default
            # closed the turn anyway; with a text part AND an image the session sat
            # waiting for more input after the speech had already finished, which added
            # twenty-odd seconds of silence to every question asked about a selection.
            await sess.send_client_content(
                turns=types.Content(role="user", parts=parts), turn_complete=True)
            async for msg in sess.receive():
                # Token usage, recorded per answer. Audio output is billed by the token
                # and speech spends them fast, so "what does one question cost" is not
                # answerable from wall-clock time — it has to come off the meter.
                um = getattr(msg, "usage_metadata", None)
                if um:
                    stats["live_total"] = getattr(um, "total_token_count", 0) or 0
                    stats["live_in"] = getattr(um, "prompt_token_count", 0) or 0
                    for d in (getattr(um, "response_tokens_details", None) or []):
                        mod = str(getattr(d, "modality", "")).split(".")[-1].lower()
                        stats[f"live_out_{mod}"] = getattr(d, "token_count", 0) or 0
                sc = msg.server_content
                if sc and sc.model_turn:
                    for part in sc.model_turn.parts or []:
                        d = getattr(part, "inline_data", None)
                        if d and d.data:     # 24 kHz s16 mono, played via Web Audio
                            await ws.send_json({"type": "audio", "turn": turn,
                                                "b64": base64.b64encode(d.data).decode()})
                if sc and sc.output_transcription and sc.output_transcription.text:
                    said += sc.output_transcription.text
                    # The board's opening line comes from the voice, not from the other
                    # model. The structured call's first token has been measured
                    # anywhere between two and thirteen seconds depending on load, and
                    # a board that is blank while the tutor talks is the complaint this
                    # exists to fix. The tutor's own first sentence goes up as it is
                    # said, and the structured ops fill in underneath it.
                    if not opened["done"]:
                        first = sentences(strip_internals(said)[0])
                        if len(first) > 1 or SETTLED_END.search(said):
                            opened["done"] = True
                            line = layout(sanitise([{"op": "write", "role": "explain",
                                                     "text": first[0]}]),
                                          ctx.get("board_aspect") or 16 / 9,
                                          start_row=cursor["row"])
                            if line:
                                cursor["row"] += len([o for o in line if o["op"] == "write"])
                                stats["ops"] = stats.get("ops", 0) + len(line)
                                await ws.send_json({"type": "ops", "ops": line,
                                                    "turn": turn})
                    clean, leaked = strip_internals(said)
                    if leaked:
                        stats["leaked"] += leaked
                        print(f"  !! {leaked} internal token(s) leaked into narration — "
                              f"stripped before display: {said[:120]!r}")
                    await ws.send_json({"type": "text", "role": "tutor", "turn": turn,
                                        "text": clean, "leaked": leaked})
                if sc and sc.turn_complete:
                    break
        return said

    cursor = {"row": 0}

    async def blackboard(stats):
        await stream_board(ws, ctx, question, turn, shot, "gemini", stats, cursor)

    async def _blackboard_unused(stats):
        prompt = live_instruction(ctx) + (SELECTION_NOTE if shot else "")
        contents = [f"{prompt}\n\nSTUDENT ASKS: {question}"]
        if shot:
            contents.append(types.Part.from_bytes(data=shot, mime_type="image/png"))
        r = await c.aio.models.generate_content(
            model=JUDGE,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ANSWER_SCHEMA,
                temperature=0.4,
            ),
        )
        um = getattr(r, "usage_metadata", None)
        if um:
            stats["board_in"] = getattr(um, "prompt_token_count", 0) or 0
            stats["board_out"] = getattr(um, "candidates_token_count", 0) or 0
        ans = parse_json(r.text)
        ans["say"], _ = strip_internals(ans.get("say", ""))
        raw = sanitise(ans.get("ops") or [])
        if not raw:
            # A tutor who is talking at a blank board looks broken, and the case this
            # happens in is the one where the student most needs something to read: an
            # out-of-scope question, where the model answers in speech and draws
            # nothing. Put the spoken answer on the board rather than leave it empty.
            said = (ans.get("say") or "").strip()
            if said:
                raw = sanitise([{"op": "write", "role": "explain", "text": said}])
        ops = layout(raw, ctx.get("board_aspect") or 16 / 9)
        stats["ops"] = len(ops)
        if ops:
            await ws.send_json({"type": "ops", "ops": ops})
        jb = ans.get("jump_beat", -1)
        beat = next((b for b in (ctx.get("beats") or [])
                     if b.get("index") == jb and b.get("start") is not None), None)
        if beat:
            stats["jump"] = jb
            await ws.send_json({"type": "jump", "at": beat["start"], "turn": turn,
                                "beat": jb, "title": beat["title"],
                                "why": beat["title"]})

    stats = {"leaked": 0, "ops": 0, "jump": None,
             "live_in": 0, "live_total": 0, "board_in": 0, "board_out": 0}
    opened = {"done": False}     # has the voice put its first line on the board yet
    # Board first, deliberately. Both requests go to the same host; whichever is issued
    # second waits behind the other's connection setup, and the Live session then floods
    # the loop with audio. Issued first, the board's opening token arrives at about two
    # seconds — the moment the voice starts — instead of seven.
    results = await asyncio.gather(blackboard(stats), speak(stats), return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            await ws.send_json({"type": "error", "text": str(r)[:200], "turn": turn})
    await ws.send_json({"type": "audio_done", "turn": turn})
    said = next((r for r in results if isinstance(r, str)), "")
    return said, stats["leaked"], stats["ops"], stats["jump"], stats


# SAARTHI_AUDIT_DIR lets a read-only or ephemeral deployment point this somewhere
# writable. The log is how "what did it tell my students?" gets answered later, so it
# is worth keeping, but never at the cost of the lesson: a failed write is logged and
# swallowed rather than taking an answer down with it.
AUDIT = Path(os.environ.get("SAARTHI_AUDIT_DIR") or (ROOT / "audit"))


def audit(topic: str, row: dict) -> None:
    """Append-only record of every live exchange.

    A classroom product has to be able to answer "what did it tell my students?" months
    later. This is also where the accuracy gate becomes reviewable rather than merely
    asserted: leaked-token counts and out-of-scope refusals are recorded per turn, so a
    regression shows up in the log before it shows up in a complaint.
    """
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
    try:
        AUDIT.mkdir(parents=True, exist_ok=True)
        with (AUDIT / f"{topic}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:                        # read-only filesystem, full disk
        print(f"  !! audit write failed ({e.__class__.__name__}): {e}")


async def api_audit(request):
    p = AUDIT / f"{request.match_info['topic']}.jsonl"
    if not p.exists():
        return web.json_response([])
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return web.json_response(rows[-200:])


async def api_lessons(_):
    out = []
    for p in sorted(LESSONS.glob("*.json")):
        m = json.loads(p.read_text())
        out.append({"topic": m["topic"], "language": m.get("language"),
                    "duration": m.get("duration", 0), "title": m.get("title", "")})
    return web.json_response(out)


async def api_lesson(request):
    p = LESSONS / f"{request.match_info['topic']}.json"
    if not p.exists():
        raise web.HTTPNotFound()
    return web.json_response(json.loads(p.read_text()))


async def media(request):
    """Serve the finished lesson straight out of the build tree — the product and the
    factory share one copy of every artefact."""
    m = LESSONS / f"{request.match_info['topic']}.json"
    if not m.exists():
        raise web.HTTPNotFound()
    man = json.loads(m.read_text())
    name = request.match_info["name"]
    for key in ("video_path", "reel_path"):
        p = man.get(key)
        if p and Path(p).name == name and Path(p).exists():
            return web.FileResponse(Path(p))
    # Packaged deployment: the manifests hold absolute paths from the machine the
    # lesson was rendered on, which do not exist in a container. pack.py copies the
    # media in beside the app, and this is where it is found.
    local = MEDIA / request.match_info["topic"] / name
    if local.exists() and local.is_file() and MEDIA in local.resolve().parents:
        return web.FileResponse(local)
    raise web.HTTPNotFound()


PASSCODE = os.environ.get("SAARTHI_PASSCODE", "").strip()
COOKIE = "saarthi_pass"
DAILY_ASK_CAP = int(os.environ.get("SAARTHI_DAILY_ASKS", "300"))
_asks: dict[str, int] = {}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def spend_ask() -> bool:
    """One shared daily budget for live answers, across everyone holding the link.

    Every question costs a Live turn and a multimodal call on a billed key, and the
    per-model daily caps do not scale with the plan. A demo link that anyone can forward
    is a demo link that can exhaust the day's quota before the people it was meant for
    open it, so the budget is spent here and refused loudly when it runs out.
    """
    day = _today()
    if _asks.get("day") != day:
        _asks.clear()
        _asks["day"] = day
        _asks["n"] = 0
    if _asks["n"] >= DAILY_ASK_CAP:
        return False
    _asks["n"] += 1
    return True


GATE_HTML = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Saarthi classroom</title>
<style>
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f2f5f8;
   color:#16202b;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
 form{background:#fff;border:1px solid #d3dbe3;border-radius:16px;padding:32px;
   box-shadow:0 10px 30px rgba(16,32,43,.13);width:min(360px,90vw)}
 h1{margin:0 0 4px;font-size:17px}p{margin:0 0 20px;color:#5b6b7a;font-size:13px}
 input{width:100%%;box-sizing:border-box;padding:11px 14px;border:1px solid #d3dbe3;
   border-radius:999px;font:15px inherit;margin-bottom:12px}
 button{width:100%%;padding:11px;border:0;border-radius:999px;background:#12b76a;
   color:#fff;font:600 14px inherit;cursor:pointer}
 .err{color:#c62828;font-size:13px;margin:0 0 12px}
</style>
<form method=POST action="/gate">
  <h1>@saarthi <span style="color:#8b9aa8;font-weight:400">classroom</span></h1>
  <p>This is a private preview. Enter the passcode you were given.</p>
  %s<input name=code type=password placeholder="Passcode" autofocus autocomplete="off">
  <button>Enter</button>
</form>"""


def make_app() -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.router.add_get("/api/lessons", api_lessons)
    app.router.add_get("/api/lesson/{topic}", api_lesson)
    app.router.add_get("/api/audit/{topic}", api_audit)
    app.router.add_post("/api/transcribe", api_transcribe)
    app.router.add_get("/media/{topic}/{name}", media)
    app.router.add_get("/ws", ws_handler)
    @web.middleware
    async def no_store(request, handler):
        """Never let a browser hold on to the app shell.

        aiohttp's static handler sends ETag and Last-Modified but no Cache-Control, so
        Chrome applies heuristic caching and will serve a stale stylesheet without
        revalidating. That produced a landing page built from new markup and old CSS —
        the screen-reader text rendered visibly, the context bar showed during playback,
        and the video never appeared. The lesson media is immutable per path and is fine
        to cache; the shell is not.
        """
        resp = await handler(request)
        if not request.path.startswith("/media/"):
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    async def gate_page(request):
        bad = "<p class=err>That passcode did not work.</p>" if request.query.get("bad") else ""
        return web.Response(text=GATE_HTML % bad, content_type="text/html")

    async def gate_post(request):
        form = await request.post()
        if not PASSCODE or str(form.get("code", "")) != PASSCODE:
            raise web.HTTPFound("/gate?bad=1")
        resp = web.HTTPFound("/")
        # Signed cookies would need a secret to rotate and a session store to revoke;
        # for a preview link the passcode itself is the credential, so holding it in an
        # HttpOnly cookie is exactly as strong as the link and no weaker.
        resp.set_cookie(COOKIE, PASSCODE, max_age=7 * 24 * 3600, httponly=True,
                        samesite="Lax", secure=request.scheme == "https")
        raise resp

    @web.middleware
    async def gate(request, handler):
        """No passcode configured means local development, and no gate.

        Set SAARTHI_PASSCODE and every route needs it — including the media and the
        socket, so the videos cannot be hotlinked and the tutor cannot be driven by
        anyone who did not get past this page.
        """
        if not PASSCODE or request.path.startswith("/gate"):
            return await handler(request)
        if request.cookies.get(COOKIE) == PASSCODE:
            return await handler(request)
        if request.path.startswith(("/api/", "/media/", "/ws")):
            raise web.HTTPUnauthorized(text="passcode required")
        raise web.HTTPFound("/gate")

    app.middlewares.append(gate)
    app.middlewares.append(no_store)
    app.router.add_get("/gate", gate_page)
    app.router.add_post("/gate", gate_post)
    app.router.add_get("/", lambda r: web.FileResponse(WEB / "index.html"))
    app.router.add_static("/", WEB, show_index=False)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8756))
    print(f"  classroom on http://localhost:{port}")
    print(f"  passcode: {'set' if PASSCODE else 'NOT SET — the app is open to anyone'}")
    print(f"  daily question budget: {DAILY_ASK_CAP}")
    web.run_app(make_app(), port=port, print=None)
