"""Board composition invariants.

The board is projected in a classroom, so "it usually looks right" is not a standard it
can be held to. Every case below is either a rule of the composition or a defect that
actually shipped, kept as a regression.

    python3 -m pytest classroom/tests/test_layout.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import app as srv  # noqa: E402

ASPECT = 16 / 9
Z = srv.ZONES


# --- helpers ---------------------------------------------------------------------
def w(text, role=None):
    return {"op": "write", "text": text, **({"role": role} if role else {}), "at": [0.5, 0.5]}


def d(shape="sphere3d"):
    return {"op": "draw", "shape": shape, "at": [0.5, 0.5], "r": 0.09}


# The real board is wide and short once the chrome has its rows; tests run at the two
# extremes so a change cannot be tuned to one shape.
ASPECTS = (16 / 9, 2.93)


def place(ops, aspect=2.93):
    return srv.layout(srv.sanitise(ops), aspect)


def boxes(out, aspect=2.93):
    """Normalised bounding boxes, with x narrowed by the aspect ratio as drawn."""
    global ASPECT
    ASPECT = aspect
    b = []
    for o in out:
        if o["op"] == "write":
            size = o["size"]
            width = min(o.get("maxw", 0.88),
                        0.52 * size * (len(o["text"]) + 2) / ASPECT)
            x, y = o["at"]
            b.append(("write", x, y - size * 0.82, x + width, y + size * 0.28, o["text"]))
        elif o["op"] == "draw":
            # measured against board.js proj3d, not guessed: a dumbbell's nodal ellipse
            # is the widest thing on it, and an axes triad is asymmetric about its origin
            x, y = o["at"]
            r = o.get("r", 0.09)
            ext = {"sphere3d": (-1.0, 1.0, -1.0, 1.0),
                   "dumbbell3d": (-1.25, 1.25, -1.35, 1.35),
                   "axes3d": (-0.62, 1.30, -1.30, 0.50)}[o["shape"]]
            b.append(("draw", x + ext[0] * r / ASPECT, y + ext[2] * r,
                      x + ext[1] * r / ASPECT, y + ext[3] * r, o["shape"]))
        elif o["op"] == "underline":
            (x0, y0), (x1, y1) = o["from"], o["to"]
            b.append(("underline", x0, y0 - 0.004, x1, y1 + 0.004, "_"))
    return b


def overlaps(out, aspect=2.93):
    bad = []
    bs = boxes(out, aspect)
    for i, a in enumerate(bs):
        for c in bs[i + 1:]:
            if a[0] == "underline" or c[0] == "underline":
                continue                       # an underline is meant to hug its text
            if a[1] < c[3] and c[1] < a[3] and a[2] < c[4] and c[2] < a[4]:
                bad.append((a[5], c[5]))
    return bad


# --- the composition rules -------------------------------------------------------
ANSWERS = [
    [w("Orbit vs Orbital", "term"), {"op": "underline"},
     w("Orbit = a fixed circular path, the Bohr picture", "explain"),
     w("Orbital = a 3D region where the electron is 90% likely to be", "explain"),
     d("sphere3d"), d("dumbbell3d"),
     w("A nodal plane is not empty space between the lobes", "trap"),
     w("n=2, l=1 gives three p orbitals", "result")],
    [w("s and p", "term"), w("s is spherical, p is a dumbbell", "explain")],
    [w("Answer", "result")],
    [w("Azimuthal quantum number l fixes the shape of the orbital", "explain"),
     w("l = 0 is s, l = 1 is p, l = 2 is d", "example"),
     w("Count the nodal planes to name it", "tip")],
    [d("axes3d"), d("sphere3d"), d("dumbbell3d"),
     w("Three projections, one board", "explain")],
    [w("x" * 220, "explain")],                                   # a wall of text
    [w("Supercalifragilisticexpialidocious" * 4, "term")],       # one unbreakable token
    [w("यह ऑर्बिटल का आकार है, ध्यान से देखो", "explain"),
     w("s ऑर्बिटल गोल होता है", "example")],
]


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize("i,ops", list(enumerate(ANSWERS)))
def test_nothing_overlaps(i, ops, aspect):
    assert overlaps(place(ops, aspect)) == []


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize("i,ops", list(enumerate(ANSWERS)))
def test_everything_stays_on_the_board(i, ops, aspect):
    for kind, x0, y0, x1, y1, what in boxes(place(ops, aspect), aspect):
        assert 0.0 <= x0 and x1 <= 1.0, f"{kind} {what!r} runs off the side: {x0}..{x1}"
        assert 0.0 <= y0 and y1 <= 1.0, f"{kind} {what!r} runs off the top/bottom: {y0}..{y1}"


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize("i,ops", list(enumerate(ANSWERS)))
def test_text_never_enters_the_diagram_gutter(i, ops, aspect):
    for kind, x0, y0, x1, y1, what in boxes(place(ops, aspect), aspect):
        if kind == "write":
            assert x1 <= Z["text_right"] + 1e-6, f"{what!r} crosses into the diagrams"


@pytest.mark.parametrize("aspect", ASPECTS)
@pytest.mark.parametrize("i,ops", list(enumerate(ANSWERS)))
def test_the_video_corner_is_left_clear(i, ops, aspect):
    """The recording sits top-right while the board is up; chalk must not go under it."""
    for kind, x0, y0, x1, y1, what in boxes(place(ops, aspect), aspect):
        assert not (x1 > 0.80 and y0 < 0.24), f"{kind} {what!r} is under the video"


def test_composition_is_centred_not_stretched():
    """Regression: two lines used to be flung to opposite edges of an empty board."""
    out = place([w("s and p", "term"), w("s is spherical, p is a dumbbell", "explain")])
    ys = [o["at"][1] for o in out if o["op"] == "write"]
    assert max(ys) - min(ys) < 0.16, f"a two-line answer is spread over {ys}"
    mid = (min(ys) + max(ys)) / 2
    assert 0.4 < mid < 0.6, f"the block is not centred, midpoint {mid}"


def test_a_trap_gets_its_own_band_below_the_working():
    out = place([w("Orbital is a region", "explain"), w("Not a path", "trap")])
    body = [o for o in out if o.get("role") == "explain"][0]
    trap = [o for o in out if o.get("role") == "trap"][0]
    assert trap["at"][1] - body["at"][1] > Z["line_h"] * 2, "no separation before the trap"


def test_underline_follows_its_heading_to_the_final_position():
    """Regression: the span was derived pre-layout, so it floated mid-board alone."""
    out = place([w("Orbit vs Orbital", "term"), {"op": "underline"},
                 w("body text here", "explain")])
    head = out[0]
    ul = [o for o in out if o["op"] == "underline"][0]
    assert ul["from"][0] == pytest.approx(head["at"][0])
    assert 0 < ul["from"][1] - head["at"][1] < 0.04, "underline is not under its heading"


def test_long_text_wraps_instead_of_running_off():
    out = place([w("The azimuthal quantum number l determines the shape of the orbital "
                   "and the number of nodal planes it carries", "explain")])
    lines = [o for o in out if o["op"] == "write"]
    assert len(lines) >= 2, "a long line was not wrapped"
    assert sum(1 for o in lines if o.get("cont")) == len(lines) - 1


def test_wrapped_lines_do_not_repeat_the_role_glyph():
    out = place([w("A nodal plane is a surface where the probability of finding the "
                   "electron is exactly zero, not a gap between the lobes", "trap")])
    conts = [o for o in out if o.get("cont")]
    assert conts, "expected continuation lines"
    assert all(o["role"] == "trap" for o in conts), "continuations lost their role"


def test_model_supplied_coordinates_are_ignored():
    """Every op arrives at [0.5, 0.5]; if any survives, the model is steering layout."""
    out = place([w("one", "explain"), w("two", "explain"), w("three", "explain")])
    assert not any(o["at"] == [0.5, 0.5] for o in out)


def test_diagrams_never_stack_off_the_bottom():
    """A fourth diagram used to be placed below the board edge; now it is dropped."""
    out = place([d(), d(), d(), d(), d()])
    assert len(out) == srv.MAX_DIAGRAMS
    for o in out:
        assert 0.26 <= o["at"][1] - o["r"] * 1.5 and o["at"][1] + o["r"] * 1.5 <= 0.89


def test_a_stray_underline_with_nothing_to_underline_is_dropped():
    assert place([{"op": "underline"}]) == []


def test_empty_answer_is_empty_not_a_crash():
    assert place([]) == []


@pytest.mark.parametrize("aspect", ASPECTS)
def test_no_orphan_words(aspect):
    """One short word alone on a last line reads as a mistake in handwriting."""
    out = place([w("3D region where the electron is 90% likely to be found", "explain")],
                aspect)
    lines = [o["text"] for o in out]
    assert not (len(lines) > 1 and len(lines[-1].split()) == 1 and len(lines[-1]) <= 8), \
        f"orphan: {lines}"


def test_a_wider_board_fits_more_per_line():
    """Regression: the wrap budget was hard-coded to 16:9 and broke lines far too early
    on the real board, which is close to 3:1 once the chrome has taken its rows."""
    text = "The azimuthal quantum number l fixes the shape of the orbital"
    narrow = len(place([w(text, "explain")], 16 / 9))
    wide = len(place([w(text, "explain")], 2.93))
    assert wide < narrow, f"{wide} lines wide vs {narrow} narrow"
