"""The preview gate.

Anything published on a link runs on a billed key, so "it seemed to work when I clicked
it" is not evidence. Each case here is a way the gate could be open without looking
open: the socket unguarded while the page is guarded, the media hotlinkable, a wrong
passcode accepted, the budget not actually spent.

Driven through asyncio.run rather than an async-fixture plugin, so it needs nothing
beyond pytest and aiohttp — which the server already depends on.

    python3 -m pytest classroom/tests/test_gate.py -q
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

CODE = "let-me-in-42"


def serve(passcode=CODE, budget="3"):
    """Reload the module so the environment is read fresh, and hand back an app."""
    import os
    if passcode is None:
        os.environ.pop("SAARTHI_PASSCODE", None)
    else:
        os.environ["SAARTHI_PASSCODE"] = passcode
    os.environ["SAARTHI_DAILY_ASKS"] = budget
    import app
    importlib.reload(app)
    return app


def run(app, body):
    """Run one coroutine against a live test server and tear it down."""
    async def go():
        c = TestClient(TestServer(app.make_app()))
        await c.start_server()
        try:
            return await body(c)
        finally:
            await c.close()
    return asyncio.run(go())


def test_the_page_is_gated():
    app = serve()
    st, loc = run(app, lambda c: _status_loc(c, "/"))
    assert st == 302 and loc == "/gate"


async def _status_loc(c, path):
    r = await c.get(path, allow_redirects=False)
    return r.status, r.headers.get("Location")


def test_the_gate_page_itself_is_reachable():
    app = serve()

    async def body(c):
        r = await c.get("/gate")
        return r.status, await r.text()
    st, text = run(app, body)
    assert st == 200 and "Passcode" in text


@pytest.mark.parametrize("path", ["/api/lessons", "/api/lesson/ML5.10",
                                  "/media/ML5.10/ML5.10.mp4", "/ws"])
def test_the_data_the_page_needs_is_gated_too(path):
    """A gate on the HTML alone leaves the videos hotlinkable and the tutor drivable."""
    app = serve()
    st, _ = run(app, lambda c: _status_loc(c, path))
    assert st == 401, f"{path} answered {st}"


@pytest.mark.parametrize("code", ["", "wrong", "let-me-in-4", "LET-ME-IN-42"])
def test_a_wrong_passcode_is_refused(code):
    app = serve()

    async def body(c):
        r = await c.post("/gate", data={"code": code}, allow_redirects=False)
        return r.status, r.headers.get("Location"), r.cookies.get("saarthi_pass")
    st, loc, cookie = run(app, body)
    assert st == 302 and "bad=1" in loc
    assert cookie is None


def test_the_right_passcode_lets_you_in_and_stays_in():
    app = serve()

    async def body(c):
        r = await c.post("/gate", data={"code": CODE}, allow_redirects=False)
        cookie = r.cookies.get("saarthi_pass")
        after = await c.get("/api/lessons")
        return r.status, r.headers.get("Location"), cookie, after.status
    st, loc, cookie, after = run(app, body)
    assert st == 302 and loc == "/"
    assert cookie is not None and cookie.value == CODE
    assert cookie["httponly"], "the passcode cookie is readable from JavaScript"
    assert after == 200


def test_no_passcode_configured_means_no_gate():
    """Local development must not need a passcode, or nobody will run the tests."""
    app = serve(passcode=None)

    async def body(c):
        return (await c.get("/api/lessons")).status
    assert run(app, body) == 200


def test_the_daily_budget_is_actually_spent():
    app = serve()
    app._asks.clear()
    assert [app.spend_ask() for _ in range(3)] == [True, True, True]
    assert app.spend_ask() is False, "the fourth question was not refused"


def test_the_budget_resets_on_a_new_day():
    app = serve()
    app._asks.clear()
    assert app.spend_ask() is True
    app._asks["day"] = "1999-01-01"          # pretend the day rolled over
    assert app.spend_ask() is True
    assert app._asks["n"] == 1
