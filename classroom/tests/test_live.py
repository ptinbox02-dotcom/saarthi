"""End-to-end: a real question through the real WebSocket, against the real models.

Costs one Live turn and one structured call, so it is opt-in:

    SAARTHI_LIVE=1 python3 -m pytest classroom/tests/test_live.py -q -s

It exists because the two cheap suites between them cover the layout maths and the
rendered page, and neither would notice if the wire contract between them broke — which
is exactly what happened when the board and the voice were split into two models.
"""
from __future__ import annotations

import asyncio
import json
import time
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

pytestmark = pytest.mark.skipif(os.environ.get("SAARTHI_LIVE") != "1",
                                reason="set SAARTHI_LIVE=1 to spend a real turn")

PORT = os.environ.get("SAARTHI_PORT", "8756")

# Ask each lesson about its own subject. The first version asked ML1.1 (cathode rays)
# about orbitals, the tutor correctly declined, and the failure it produced was about
# the question being out of scope rather than about anything under test.
QUESTIONS = {
    "ML5.10": "s orbital aur p orbital mein kya farq hai?",
    "ML1.1": "cathode ray tube mein electron ka charge by mass ratio kaise nikalte hain?",
}


def question_for(topic: str) -> str:
    for prefix, q in QUESTIONS.items():
        if topic.startswith(prefix):
            return q
    return next(iter(QUESTIONS.values()))


async def _turn():
    import aiohttp
    async with aiohttp.ClientSession() as s:
        man = await (await s.get(f"http://localhost:{PORT}/api/lessons")).json()
        want = os.environ.get("SAARTHI_TOPIC")
        topic = want or next((m["topic"] for m in man if m["topic"] == "ML5.10"),
                             man[0]["topic"])
        question = os.environ.get("SAARTHI_Q") or question_for(topic)
        lesson = await (await s.get(f"http://localhost:{PORT}/api/lesson/{topic}")).json()
        got = {"ops": [], "text": "", "audio": 0, "jump": None, "errors": [],
               "turns": set(), "elapsed": 0.0}
        async with s.ws_connect(f"http://localhost:{PORT}/ws") as ws:
            await ws.send_json({
                "type": "context", "topic": topic, "language": lesson["language"],
                "at": 60.0, "beat": None,
                "beats": [{"index": b["index"], "title": b["title"], "start": b["start"]}
                          for b in lesson["beats"]],
                "beats_full": [{"index": b["index"], "title": b["title"],
                                "narration": b["narration"]} for b in lesson["beats"]],
                "facts": lesson["facts"], "in_scope": lesson["in_scope"],
                "out_of_scope": lesson["out_of_scope"],
                "answer_lang": "hi-IN", "board_aspect": 2.93,
            })
            t0 = time.monotonic()
            await ws.send_json({"type": "live_ask", "text": question, "turn": 7,
                                "answer_lang": "hi-IN", "board_aspect": 2.93,
                                "image": os.environ.get("SAARTHI_IMAGE")})
            async for m in ws:
                d = json.loads(m.data)
                t = d.get("type")
                if "turn" in d:
                    got["turns"].add(d["turn"])
                if t == "ops":
                    got["ops"] = d["ops"]
                elif t == "text":
                    got["text"] = d["text"]
                elif t == "audio":
                    got["audio"] += 1
                elif t == "jump":
                    got["jump"] = d
                elif t == "error":
                    got["errors"].append(d["text"])
                elif t == "audio_done":
                    got["elapsed"] = time.monotonic() - t0
                    break
        return lesson, got


@pytest.fixture(scope="module")
def turn():
    return asyncio.run(asyncio.wait_for(_turn(), timeout=120))


def test_no_errors(turn):
    _, got = turn
    assert not got["errors"], got["errors"]


def test_the_tutor_speaks(turn):
    _, got = turn
    assert got["audio"] > 0, "no audio chunks — the voice half of the answer is missing"
    assert got["text"].strip(), "no transcript"


def test_the_tutor_writes(turn):
    """Regression: an out-of-scope question produced speech and a blank board."""
    _, got = turn
    assert got["ops"], "no board ops — the board half of the answer is missing"


def test_nothing_internal_leaks_into_speech(turn):
    """The Live model used to read the drawing DSL out loud."""
    _, got = turn
    said = got["text"].lower()
    for token in ("draw", "underline", "sphere3d", "dumbbell3d", "jump_beat", "role",
                  "json", "{", "}"):
        assert token not in said, f"leaked {token!r}: {got['text'][:200]}"


def test_the_board_is_laid_out_by_the_server(turn):
    """Every op must carry a position this side assigned, not one the model invented."""
    import app as srv
    _, got = turn
    for o in got["ops"]:
        assert "color" not in o, "a raw colour survived the sanitiser"
        if o["op"] == "write":
            assert o["at"][0] == pytest.approx(srv.ZONES["text_x"]), o
            assert "size" in o and "maxw" in o, o
        if o["op"] == "draw":
            assert o["at"][0] > srv.ZONES["text_right"], "a diagram is in the text column"


def test_the_answer_does_not_overlap_itself(turn):
    from test_layout import overlaps
    _, got = turn
    assert overlaps(got["ops"], 2.93) == []


def test_the_jump_points_into_the_lesson(turn):
    lesson, got = turn
    if got["jump"] is None:
        pytest.skip("the tutor offered no video reference for this question")
    assert 0 <= got["jump"]["at"] <= lesson["duration"]


def test_every_reply_is_stamped_with_its_turn(turn):
    """Regression: an unstamped late reply cleared the spinner of the turn after it."""
    _, got = turn
    assert got["turns"] == {7}, got["turns"]


def test_the_answer_is_not_cut_off_mid_sentence(turn):
    """Regression: max_output_tokens counted AUDIO tokens, so 320 was a hard stop about
    twelve seconds in and truncated almost every answer part-way through a word."""
    _, got = turn
    said = got["text"].strip()
    assert len(said) > 40, f"suspiciously short: {said!r}"
    assert said[-1] in ".!?।", f"does not end on a sentence boundary: {said[-60:]!r}"


def test_a_highlighted_region_reaches_the_model():
    """The student highlights part of the board and asks about that, not the lesson."""
    import base64, struct, zlib

    def png(w=240, h=80):
        def chunk(t, d):
            c = t + d
            return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
        rows = b"".join(b"\x00" + b"\xf5" * (w * 3) for _ in range(h))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    import app as srv
    url = "data:image/png;base64," + base64.b64encode(png()).decode()
    assert srv.decode_shot(url) is not None
    assert srv.decode_shot("data:image/png;base64,not-base64!!") is None
    assert srv.decode_shot("https://example.com/a.png") is None
    assert srv.decode_shot(None) is None


def test_the_turn_completes_promptly(turn):
    """Regression: `send_client_content` without an explicit `turn_complete` left the
    Live session waiting for more input after the speech had finished. With a text part
    alone the default closed the turn anyway; add an image and every question about a
    selection gained twenty-odd seconds of silence before the answer was marked done."""
    _, got = turn
    assert got["elapsed"] < 45, f"the turn took {got['elapsed']:.1f}s"
