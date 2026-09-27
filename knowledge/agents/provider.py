"""Whoever is answering. Deliberately not a vendor.

This corpus is built offline, in batch, with no latency budget — there is no reason for
it to depend on one provider, and one good reason not to: this project lost access to
its model mid-build and everything stopped. The corpus is an asset that should outlive
that happening again.

    SAARTHI_KB_PROVIDER = gemini | anthropic | stub
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Provider:
    name = "base"

    def complete(self, prompt: str, schema: dict, *, images: list[bytes] = ()) -> dict:
        raise NotImplementedError


class Gemini(Provider):
    name = "gemini"

    def __init__(self, model=None):
        sys.path.insert(0, str(ROOT / "core"))
        from gclient import JUDGE, client            # noqa: E402
        self.client, self.model = client(), model or JUDGE

    def complete(self, prompt, schema, *, images=()):
        from google.genai import types
        parts = [prompt] + [types.Part.from_bytes(data=i, mime_type="image/png")
                            for i in images]
        r = self.client.models.generate_content(
            model=self.model, contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=schema,
                temperature=0.2))
        return json.loads(r.text)


class Anthropic(Provider):
    name = "anthropic"

    def __init__(self, model="claude-sonnet-5"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, prompt, schema, *, images=()):
        import base64
        content = [{"type": "text", "text": prompt}]
        for i in images:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(i).decode()}})
        r = self.client.messages.create(
            model=self.model, max_tokens=4096,
            tools=[{"name": "emit", "description": "Return the result.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": "emit"},
            messages=[{"role": "user", "content": content}])
        for block in r.content:
            if block.type == "tool_use":
                return block.input
        raise RuntimeError("no structured output returned")


class Stub(Provider):
    """Returns empty results of the right shape.

    So the pipeline, the store, the batching and the run log can all be exercised —
    and tested — without a key, a network, or a bill.
    """
    name = "stub"

    def complete(self, prompt, schema, *, images=()):
        def empty(s):
            t = s.get("type")
            if t == "array":
                return []
            if t == "object":
                return {k: empty(v) for k, v in (s.get("properties") or {}).items()}
            return {"string": "", "integer": 0, "number": 0, "boolean": False}.get(t, None)
        return empty(schema)


def get(name: str | None = None) -> Provider:
    name = (name or os.environ.get("SAARTHI_KB_PROVIDER") or "gemini").lower()
    try:
        return {"gemini": Gemini, "anthropic": Anthropic, "stub": Stub}[name]()
    except KeyError:
        raise SystemExit(f"unknown provider {name!r}; use gemini, anthropic or stub")
    except Exception as e:
        print(f"  !! {name} unavailable ({str(e)[:90]}) — falling back to stub")
        return Stub()
