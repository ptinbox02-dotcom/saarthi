"""Generate the board fixtures the visual suite renders.

They go through the real sanitiser and the real layout, so a screenshot is evidence
about the shipping composition and not about a hand-written JSON file that has drifted
away from it. Regenerate whenever the layout changes:

    python3 classroom/tests/make_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import app as srv  # noqa: E402

# The board is close to 3:1 once the chrome has taken its rows, not 16:9.
ASPECT = 2.93
WEB = ROOT / "web"


def w(text, role):
    return {"op": "write", "text": text, "role": role, "at": [0.5, 0.5]}


def d(shape):
    return {"op": "draw", "shape": shape, "at": [0.5, 0.5]}


CASES = {
    # a dense answer: heading, working, two diagrams, a trap and a result
    "_fx_rich": [
        w("Orbital", "term"), {"op": "underline"},
        w("3D region where the electron is 90% likely to be found", "explain"),
        w("s: spherical — same in every direction", "example"),
        w("p: dumbbell — two lobes, one nodal plane", "example"),
        d("sphere3d"), d("dumbbell3d"),
        w("A nodal plane is not empty space between the lobes", "trap"),
        w("n=2, l=1 gives three p orbitals", "result"),
    ],
    # the sparse case that used to leave two lines marooned at opposite edges
    "_fx_sparse": [
        w("s vs p", "term"),
        w("s is spherical, p is a dumbbell", "explain"),
    ],
    # a wall of text, to exercise wrapping and the tip band
    "_fx_wall": [
        w("Azimuthal quantum number", "term"),
        w("The azimuthal quantum number l fixes the shape of the orbital and also "
          "the number of nodal planes it carries, which is exactly l for any given "
          "orbital", "explain"),
        w("l = 0 is s, l = 1 is p, l = 2 is d, l = 3 is f", "example"),
        d("axes3d"),
        w("Count the nodal planes to name the orbital", "tip"),
    ],
}

if __name__ == "__main__":
    for name, ops in CASES.items():
        out = srv.layout(srv.sanitise(ops), ASPECT)
        (WEB / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False))
        print(f"  {name}.json  {len(out)} ops")
