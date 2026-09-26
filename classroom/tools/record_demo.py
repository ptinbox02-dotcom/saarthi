#!/usr/bin/env python3
"""Record a demo of the classroom, driving the real app against the real tutor.

    python3 classroom/tools/record_demo.py [--port 8756] [--out build/Saarthi-demo.mp4]

Screencast frames come from Chrome over the DevTools protocol; the tutor's voice is
captured in the page as it arrives, because headless Chrome renders no audio. The two
are aligned on a single clock started at the first frame, then muxed with a narration
track. Nothing here is staged — every answer is a live one.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "classroom" / "tools"))
sys.path.insert(0, str(ROOT / "classroom" / "server"))
sys.path.insert(0, str(ROOT / "hb"))

from cdp import Browser                                     # noqa: E402

WORK = Path("/tmp/saarthi-demo")
FPS = 12
NARRATOR = "Achird"

# The cursor Chrome does not draw. Without it a demo looks like things happening by
# themselves, and a viewer cannot tell what was clicked.
CURSOR_JS = """
window.__cursor = {x: 40, y: 40};
(() => {
  const d = document.createElement('div');
  d.id = '__cur';
  d.style.cssText = 'position:fixed;z-index:2147483647;width:22px;height:22px;'
    + 'pointer-events:none;left:0;top:0;transition:transform .05s linear;'
    + 'background:no-repeat center/contain url("data:image/svg+xml;utf8,'
    + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
      + '<path d="M4 2l7 18 2.5-7.5L21 10z" fill="%23111" stroke="white"'
      + ' stroke-width="1.5" stroke-linejoin="round"/></svg>') + '")';
  document.documentElement.appendChild(d);
  window.__moveCursor = (x, y) => {
    window.__cursor = {x, y};
    d.style.transform = `translate(${x}px, ${y}px)`;
  };
  __moveCursor(40, 40);
})();
"""

# Capture the tutor's audio where it actually arrives. Headless Chrome plays nothing,
# so the only faithful copy of what a student would hear is the PCM on the wire.
TAP_JS = """
window.__audio = [];
window.__t0 = performance.now();
(() => {
  const OrigWS = window.WebSocket;
  window.WebSocket = function (...a) {
    const ws = new OrigWS(...a);
    ws.addEventListener('message', (ev) => {
      try {
        const m = JSON.parse(ev.data);
        if (m.type === 'audio') {
          window.__audio.push([performance.now() - window.__t0, m.b64]);
        }
      } catch {}
    });
    return ws;
  };
  window.WebSocket.prototype = OrigWS.prototype;
})();
"""


def say(text: str, path: Path) -> float:
    """Narration line -> wav. Returns its length in seconds."""
    import app as srv
    pcm = srv.tts_pcm(text, srv.TTS_REALTIME)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(pcm)
    return len(pcm) / 2 / 24000


def pcm_track(chunks, total_s: float, path: Path):
    """Lay captured audio chunks onto one timeline, silence in the gaps."""
    rate = 24000
    buf = bytearray(int(total_s * rate) * 2)
    for at_ms, b64 in chunks:
        raw = base64.b64decode(b64)
        off = int(at_ms / 1000 * rate) * 2
        end = min(len(buf), off + len(raw))
        if off < len(buf):
            buf[off:end] = raw[:end - off]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(buf))


# The script. Each step is a line of narration and the thing that happens while it is
# spoken — so the demo explains itself rather than being a silent screen recording.
async def scenario(b, port, note):
    Q1 = "s orbital aur p orbital mein kya farq hai?"
    Q2 = "iska ek example do"

    await note("This is a recorded chemistry lesson — the shapes of s and p orbitals, "
               "taught in Hinglish.")
    await b.click("#play"); await asyncio.sleep(3.5)

    await note("The student doesn't follow something. Instead of rewinding, she raises "
               "her hand.")
    await b.click("#ask")
    await asyncio.sleep(3)

    await note("The recording stops at the end of the sentence — never mid-word — and "
               "the blackboard comes up. The video shrinks into a corner so she can "
               "still see where she was.")
    await asyncio.sleep(2)

    await note("She asks in her own words, in the language she thinks in.")
    await b.type("#qtext", Q1)
    await b.click("#qsend", pause=0.2)

    await note("The tutor answers out loud, and writes on the board while it is "
               "talking — the way a teacher does.")
    # The legend only lists roles that are actually written, so a chip appearing is
    # the DOM's way of saying the board has started.
    for _ in range(50):
        if await b.js("document.querySelectorAll('#legend .lgd').length > 0"):
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(6)

    await note("It draws, too. Here it is showing the spherical s orbital against the "
               "dumbbell shape of p.")
    await asyncio.sleep(5)

    await note("If something is still unclear, she can steer the answer without typing "
               "a new question.")
    await b.click('[data-say^="Iska ek example"]', pause=0.3)
    await asyncio.sleep(9)

    await note("She can also circle any part of the board and ask about only that line.")
    await b.click('[data-tool="select"]', pause=0.3)
    bw = await b.js("(() => { const r = document.getElementById('board')"
                    ".getBoundingClientRect();"
                    "return [r.left, r.top, r.width, r.height]; })()")
    x0 = int(bw[0] + bw[2] * 0.06); y0 = int(bw[1] + bw[3] * 0.20)
    x1 = int(bw[0] + bw[2] * 0.55); y1 = int(bw[1] + bw[3] * 0.34)
    await b.drag(x0, y0, x1, y1)
    await asyncio.sleep(1.2)

    await note("The part she highlighted is sent along with the question, so the answer "
               "is about that, and not about the chapter in general.")
    await b.click("#selexplain", pause=0.3)
    await asyncio.sleep(10)

    await note("The board is hers as well. She can write on it, draw on it, or drop in "
               "a photo of her own working.")
    await b.click('[data-tool="pen"]', pause=0.3)
    px = int(bw[0] + bw[2] * 0.12); py = int(bw[1] + bw[3] * 0.70)
    await b.drag(px, py, px + 150, py - 40, steps=14)
    await b.drag(px + 160, py, px + 300, py - 55, steps=14)
    await asyncio.sleep(1)

    await note("Everything she asks is kept, chapter by chapter, and it survives "
               "closing the app.")
    await b.click("#navnotes", pause=1.2)
    await asyncio.sleep(2)

    await note("Tapping a note puts that whole board back, so she can revisit an "
               "explanation from last week.")
    await b.click(".turn.openable", pause=1.0)
    await asyncio.sleep(3.5)

    await note("And when an answer is covered in the recording, it points her at the "
               "exact moment — one tap and she is watching it.")
    await b.click("#closenotes", pause=0.5)
    await asyncio.sleep(1.5)

    await note("Then back to the lesson, from where she left it.")
    await b.click("#home", pause=0.5)
    await asyncio.sleep(4)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8756")
    ap.add_argument("--out", default=str(ROOT / "build" / "Saarthi-walkthrough.mp4"))
    ap.add_argument("--topic", default="ML5.10")
    args = ap.parse_args()

    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "frames").mkdir(parents=True)
    (WORK / "vo").mkdir(parents=True)

    frames: list[tuple[float, Path]] = []
    lines: list[tuple[float, Path, float]] = []      # (start_s, wav, seconds)
    t0 = None

    async with Browser(str(WORK / "chrome"), size=(1440, 900), scale=1) as b:
        await b.cmd("Page.addScriptToEvaluateOnNewDocument",
                    {"source": TAP_JS + CURSOR_JS})
        await b.cmd("Page.navigate",
                    {"url": f"http://localhost:{args.port}/?lesson={args.topic}"})
        await asyncio.sleep(5)
        # app.js is an ES module, so its bindings are not on window. Everything here
        # goes through the DOM, the same surface a person has.
        await b.js(f"""(() => {{
            const s = document.getElementById('lesson');
            s.value = {args.topic!r};
            s.dispatchEvent(new Event('change'));
        }})()""")
        await asyncio.sleep(4)
        await b.js("document.getElementById('miclang').value='hi-IN'")

        n = 0

        async def on_frame(p):
            nonlocal n, t0
            if t0 is None:
                return
            n += 1
            f = WORK / "frames" / f"f{n:05d}.jpg"
            f.write_bytes(base64.b64decode(p["data"]))
            frames.append((time.time() - t0, f))
            try:
                await b.notify("Page.screencastFrameAck",
                               {"sessionId": p["sessionId"]})
            except Exception:
                pass

        b.on("Page.screencastFrame", on_frame)

        async def note(text: str):
            """Speak a line of narration and hold the recording while it plays."""
            i = len(lines) + 1
            wav = WORK / "vo" / f"{i:02d}.wav"
            secs = await asyncio.to_thread(say, text, wav)
            lines.append((time.time() - t0, wav, secs))
            print(f"  {len(lines):2d}. [{time.time()-t0:5.1f}s] {text[:64]}")
            await asyncio.sleep(secs + 0.35)

        await b.cmd("Page.startScreencast",
                    {"format": "jpeg", "quality": 78, "everyNthFrame": 1})
        t0 = time.time()
        await b.js("window.__t0 = performance.now()")
        try:
            await scenario(b, args.port, note)
        finally:
            total = time.time() - t0
            await b.cmd("Page.stopScreencast")
            # Pulled in slices: one Runtime.evaluate carrying every chunk silently
            # truncates, and the first cut of this recorded 268 seconds of video over
            # four seconds of speech without complaining about it.
            count = await b.js("(window.__audio || []).length") or 0
            tutor = []
            for i in range(0, count, 15):
                part = await b.js(
                    f"JSON.stringify((window.__audio || []).slice({i}, {i + 15}))")
                tutor.extend(json.loads(part or "[]"))
            print(f"  pulled {len(tutor)}/{count} audio chunks")

    print(f"\n  {len(frames)} frames, {total:.1f}s, {len(lines)} narration lines")
    secs = sum(len(base64.b64decode(b)) for _, b in tutor) / 2 / 24000
    print(f"  {len(tutor)} audio chunks = {secs:.0f}s of the tutor speaking")

    # ---- assemble -------------------------------------------------------------
    pcm_track(tutor, total, WORK / "tutor.wav")

    # narration on the same clock, silence between lines
    rate = 24000
    vo = bytearray(int(total * rate) * 2)
    for at, wav, secs in lines:
        with wave.open(str(wav), "rb") as w:
            raw = w.readframes(w.getnframes())
        off = int(at * rate) * 2
        end = min(len(vo), off + len(raw))
        if off < len(vo):
            vo[off:end] = raw[:end - off]
    with wave.open(str(WORK / "vo.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(vo))

    listing = WORK / "frames.txt"
    with listing.open("w") as fh:
        for i, (at, f) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else total
            fh.write(f"file '{f}'\nduration {max(0.02, nxt - at):.3f}\n")
        fh.write(f"file '{frames[-1][1]}'\n")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # The narration carries the explanation, so the tutor's own voice sits under it.
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-i", str(WORK / "tutor.wav"), "-i", str(WORK / "vo.wav"),
        "-filter_complex",
        "[1:a]volume=0.42[t];[2:a]volume=1.0[v];[t][v]amix=inputs=2:duration=longest[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)], check=True)
    size = out.stat().st_size / 1e6
    print(f"\n  {out}  {size:.1f} MB  {total:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
