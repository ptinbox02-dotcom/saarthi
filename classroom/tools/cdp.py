"""A small DevTools-protocol client: commands and events, over one socket.

The test harness only ever needed command/response. Recording needs events too —
screencast frames arrive unsolicited and must each be acknowledged or Chrome stops
sending them.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time

import aiohttp

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Browser:
    def __init__(self, profile: str, port: int = 9455, size=(1440, 900), scale=1):
        self.profile, self.port, self.size, self.scale = profile, port, size, scale
        self.proc = self.ws = self.session = None
        self._id = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, list] = {}
        self._pump: asyncio.Task | None = None

    async def __aenter__(self):
        subprocess.run(["rm", "-rf", self.profile], check=False)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--mute-audio",
             f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             f"--window-size={self.size[0]},{self.size[1]}",
             "--autoplay-policy=no-user-gesture-required", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.session = aiohttp.ClientSession()
        page = None
        for _ in range(60):
            try:
                tabs = await (await self.session.get(
                    f"http://localhost:{self.port}/json")).json()
                # /json lists more than pages; a browser target has no DOM and rejects
                # every command that matters here.
                page = next((t for t in tabs if t.get("type") == "page"
                             and t.get("webSocketDebuggerUrl")), None)
                if page:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        if not page:
            raise RuntimeError("no page target; is Chrome running?")
        self.ws = await self.session.ws_connect(page["webSocketDebuggerUrl"],
                                                max_msg_size=0)
        self._pump = asyncio.create_task(self._read())
        for d in ("Page", "Runtime", "DOM", "Network"):
            await self.cmd(f"{d}.enable")
        try:
            await self.cmd("Emulation.setDeviceMetricsOverride",
                           {"width": self.size[0], "height": self.size[1],
                            "deviceScaleFactor": self.scale, "mobile": False})
        except RuntimeError:
            pass          # --window-size already sized it; the override is a nicety
        return self

    async def __aexit__(self, *exc):
        if self._pump:
            self._pump.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass
        await self.session.close()
        if self.proc:
            self.proc.terminate()

    async def _read(self):
        async for msg in self.ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if "id" in m:
                fut = self._waiting.pop(m["id"], None)
                if fut and not fut.done():
                    fut.set_result(m)
            else:
                for fn in self._handlers.get(m.get("method", ""), []):
                    await fn(m.get("params") or {})

    def on(self, method: str, fn):
        self._handlers.setdefault(method, []).append(fn)

    async def cmd(self, method: str, params=None):
        self._id += 1
        fut = asyncio.get_event_loop().create_future()
        self._waiting[self._id] = fut
        await self.ws.send_json({"id": self._id, "method": method,
                                 "params": params or {}})
        m = await asyncio.wait_for(fut, timeout=60)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m.get("result", {})

    async def notify(self, method: str, params=None):
        """Send a command and do not wait for its reply.

        Event handlers run inside the read pump, so a handler that awaits a response
        deadlocks: the only task that could deliver it is the one now blocked. Frame
        acknowledgements have nothing useful to return, so they go out unwaited.
        """
        self._id += 1
        await self.ws.send_json({"id": self._id, "method": method,
                                 "params": params or {}})

    async def js(self, expr: str):
        r = await self.cmd("Runtime.evaluate",
                           {"expression": expr, "returnByValue": True,
                            "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    # ---- input, with a cursor the viewer can follow ----------------------------
    async def move(self, x: int, y: int, steps: int = 12):
        """Glide, not teleport: a cursor that jumps is impossible to follow."""
        cur = await self.js("window.__cursor ? [__cursor.x, __cursor.y] : [40, 40]") or [40, 40]
        for i in range(1, steps + 1):
            nx = cur[0] + (x - cur[0]) * i / steps
            ny = cur[1] + (y - cur[1]) * i / steps
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": nx, "y": ny})
            await self.js(f"window.__moveCursor && __moveCursor({nx:.0f},{ny:.0f})")
            await asyncio.sleep(0.016)

    async def click(self, sel: str, pause: float = 0.4):
        box = await self.js(f"""(() => {{
            const e = document.querySelector({sel!r});
            if (!e) return null;
            const r = e.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2];
        }})()""")
        if not box:
            raise RuntimeError(f"no element for {sel}")
        await self.move(int(box[0]), int(box[1]))
        for t in ("mousePressed", "mouseReleased"):
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": t, "x": box[0], "y": box[1], "button": "left",
                            "clickCount": 1})
        await asyncio.sleep(pause)

    async def drag(self, x0, y0, x1, y1, steps=18):
        await self.move(x0, y0)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x0, "y": y0,
                        "button": "left", "clickCount": 1})
        for i in range(1, steps + 1):
            nx, ny = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
            await self.cmd("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": nx, "y": ny, "button": "left"})
            await self.js(f"window.__moveCursor && __moveCursor({nx:.0f},{ny:.0f})")
            await asyncio.sleep(0.02)
        await self.cmd("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x1, "y": y1, "button": "left"})

    async def type(self, sel: str, text: str, cps: float = 22):
        await self.click(sel, pause=0.1)
        for ch in text:
            await self.cmd("Input.insertText", {"text": ch})
            await asyncio.sleep(1 / cps)
