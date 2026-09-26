#!/usr/bin/env python3
"""Saarthi Manim kit — brand palette, a timed base Scene, and reusable apparatus.

Every lesson scene subclasses SaarthiScene, which:
  * paints the brand background,
  * keeps its own clock (so cue points survive any renderer),
  * writes <clip>.cues.json next to the rendered clip — step 9 uses those
    cue points to auto-cut the reel,
  * can pad itself to an exact target duration.

No LaTeX anywhere: BasicTeX needs an interactive sudo install, so every symbol
here is Unicode text or a hand-built VMobject. Renders on a bare toolchain.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from manim import *
from manim.utils.bezier import interpolate

# ----- brand ---------------------------------------------------------------
BG = "#0e1116"
INK = "#e8eaed"
MUTED = "#9aa3af"
ACCENT = "#d9702f"
GOOD = "#3ec98a"
BAD = "#e2564d"
GLOW = "#7fd4ff"          # cathode-ray / fluorescence blue-green
FLUOR = "#8ef0a8"
PLUS = "#e2564d"
MINUS = "#5a9bf6"

FONT = os.environ.get("SAARTHI_FONT", "Helvetica Neue")


def T(txt, size=40, color=INK, weight=NORMAL, **kw):
    return Text(txt, font=FONT, font_size=size, color=color, weight=weight, **kw)


class SaarthiScene(Scene):
    """Base scene: brand background, own clock, cue points, exact padding."""

    target_seconds: float = 0.0     # set by the renderer via env
    slug: str = "beat"

    def setup(self):
        self.camera.background_color = BG
        self._t = 0.0
        self._anim_t = 0.0         # time spent in animations (never stretched)
        self._wait_t = 0.0         # unscaled hold time
        self._in_wait = False      # Scene.wait() is implemented via play(Wait(...))
        self._cues: dict[str, float] = {}
        self.target_seconds = float(os.environ.get("SAARTHI_TARGET", self.target_seconds or 0))
        self.slug = os.environ.get("SAARTHI_SLUG", self.slug)
        # A beat's animation is shorter than its narration, so the slack has to go
        # somewhere. Dumping it in one hold at the end looks broken; the renderer
        # measures the scene first and passes back a factor that stretches every
        # hold instead, so the picture keeps moving all the way through.
        self.wait_scale = float(os.environ.get("SAARTHI_WAITSCALE", 1.0))

    # --- clock -------------------------------------------------------------
    def play(self, *a, **kw):
        rt = kw.get("run_time")
        if rt is None:
            # fall back to the longest run_time the animations themselves declare
            rts = [getattr(x, "run_time", None) for x in a]
            rts = [r for r in rts if isinstance(r, (int, float))]
            rt = max(rts) if rts else 1.0
        super().play(*a, **kw)
        if not self._in_wait:
            self._t += rt
            self._anim_t += rt

    def wait(self, duration=1.0, scale=True, **kw):
        held = duration * (self.wait_scale if scale else 1.0)
        outer, self._in_wait = self._in_wait, True
        try:
            super().wait(held, **kw)
        finally:
            self._in_wait = outer
        self._t += held
        if scale:
            self._wait_t += duration

    @property
    def t(self) -> float:
        return self._t

    def mark(self, name: str):
        """Record a cue point at the current time (step 9 cuts the reel on these)."""
        self._cues[name] = round(self._t, 3)

    def pad_to(self, total: float | None = None):
        total = total or self.target_seconds
        if total and self._t < total - 0.05:
            self.wait(total - self._t, scale=False)

    def tear_down(self):
        out = os.environ.get("SAARTHI_CUEDIR")
        if out:
            p = Path(out) / f"{self.slug}.cues.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "duration": round(self._t, 3),
                "anim_seconds": round(self._anim_t, 3),
                "hold_seconds_unscaled": round(self._wait_t, 3),
                "wait_scale": self.wait_scale,
                "cues": self._cues,
            }, indent=2))

    # --- chrome ------------------------------------------------------------
    def caption(self, txt, size=34, color=MUTED):
        c = T(txt, size=size, color=color)
        c.to_edge(DOWN, buff=0.55)
        return c

    def kicker(self, txt, size=30):
        k = T(txt, size=size, color=ACCENT, weight=BOLD)
        k.to_edge(UP, buff=0.5)
        return k


# ----- reusable apparatus --------------------------------------------------
def tube_light(width=7.0, height=0.62):
    """A household fluorescent tube: body + two end caps."""
    body = RoundedRectangle(width=width, height=height, corner_radius=height / 2,
                            stroke_color=MUTED, stroke_width=3, fill_color="#1b2029",
                            fill_opacity=1)
    caps = VGroup(*[
        Rectangle(width=0.28, height=height * 1.25, fill_color="#39404b",
                  fill_opacity=1, stroke_width=0).move_to(body.get_left() * s if s > 0 else body.get_left())
        for s in (0,)
    ])
    lcap = Rectangle(width=0.3, height=height * 1.3, fill_color="#39404b",
                     fill_opacity=1, stroke_width=0).move_to(body.get_left())
    rcap = lcap.copy().move_to(body.get_right())
    inner = RoundedRectangle(width=width - 0.5, height=height - 0.22,
                             corner_radius=(height - 0.22) / 2,
                             fill_color="#f3f0d8", fill_opacity=0, stroke_width=0)
    g = VGroup(body, inner, lcap, rcap)
    g.body, g.inner, g.caps = body, inner, VGroup(lcap, rcap)
    return g


def discharge_tube(width=8.4, height=2.3, with_labels=True):
    """Sealed glass tube + cathode (left, –) + anode (right, +)."""
    glass = RoundedRectangle(width=width, height=height, corner_radius=0.45,
                             stroke_color="#5b6673", stroke_width=5,
                             fill_color="#121821", fill_opacity=1)
    cath = Rectangle(width=0.22, height=height * 0.5, fill_color=MINUS,
                     fill_opacity=1, stroke_width=0)
    cath.move_to(glass.get_left() + RIGHT * 0.55)
    anod = Rectangle(width=0.22, height=height * 0.5, fill_color=PLUS,
                     fill_opacity=1, stroke_width=0)
    anod.move_to(glass.get_right() + LEFT * 0.55)
    lead_l = Line(glass.get_left() + LEFT * 0.9, cath.get_left(), color=MUTED, stroke_width=4)
    lead_r = Line(anod.get_right(), glass.get_right() + RIGHT * 0.9, color=MUTED, stroke_width=4)
    g = VGroup(glass, lead_l, lead_r, cath, anod)
    g.glass, g.cathode, g.anode = glass, cath, anod
    g.leads = VGroup(lead_l, lead_r)
    if with_labels:
        cl = VGroup(T("Cathode", size=26, color=MINUS), T("(–)", size=24, color=MINUS)).arrange(RIGHT, buff=0.15)
        cl.next_to(cath, DOWN, buff=0.35).shift(LEFT * 0.1)
        al = VGroup(T("Anode", size=26, color=PLUS), T("(+)", size=24, color=PLUS)).arrange(RIGHT, buff=0.15)
        al.next_to(anod, DOWN, buff=0.35).shift(RIGHT * 0.1)
        g.labels = VGroup(cl, al)
    else:
        g.labels = VGroup()
    return g


def hv_battery(label="~10,000 V"):
    long_ = Line(UP * 0.34, DOWN * 0.34, color=INK, stroke_width=6)
    short = Line(UP * 0.18, DOWN * 0.18, color=INK, stroke_width=6).shift(RIGHT * 0.26)
    cell = VGroup(long_, short)
    txt = T(label, size=26, color=ACCENT).next_to(cell, DOWN, buff=0.25)
    g = VGroup(cell, txt)
    g.txt = txt
    return g


def pressure_gauge(radius=0.85):
    face = Circle(radius=radius, stroke_color=MUTED, stroke_width=4,
                  fill_color="#161c26", fill_opacity=1)
    ticks = VGroup(*[
        Line(face.get_center() + (radius - 0.16) * np.array([np.cos(a), np.sin(a), 0]),
             face.get_center() + radius * np.array([np.cos(a), np.sin(a), 0]),
             color=MUTED, stroke_width=2)
        for a in np.linspace(PI * 0.9, PI * 0.1, 7)
    ])
    needle = Line(face.get_center(), face.get_center() + (radius - 0.2) * np.array([np.cos(PI * 0.85), np.sin(PI * 0.85), 0]),
                  color=ACCENT, stroke_width=5)
    hub = Dot(face.get_center(), radius=0.07, color=ACCENT)
    g = VGroup(face, ticks, needle, hub)
    g.face, g.needle = face, needle
    return g


def needle_to(gauge, frac):
    """frac 0 = low pressure (left of dial is HIGH here) → 1 = high."""
    r = gauge.face.width / 2 - 0.2
    a = interpolate(PI * 0.12, PI * 0.88, frac)
    c = gauge.face.get_center()
    return Line(c, c + r * np.array([np.cos(a), np.sin(a), 0]), color=ACCENT, stroke_width=5)


def cross_mark(size=0.55, color=BAD, width=9):
    a = Line(UL, DR, color=color, stroke_width=width).scale(size)
    b = Line(UR, DL, color=color, stroke_width=width).scale(size)
    return VGroup(a, b)


def tick_mark(size=0.5, color=GOOD, width=9):
    p = VMobject(stroke_color=color, stroke_width=width)
    p.set_points_as_corners([LEFT * 0.45 + DOWN * 0.05, DOWN * 0.42 + LEFT * 0.1, RIGHT * 0.5 + UP * 0.45])
    return p.scale(size / 0.5)


def paddle_wheel(radius=0.62, blades=8):
    hub = Circle(radius=0.09, color=INK, fill_opacity=1, stroke_width=0)
    rim = Circle(radius=radius, stroke_color=INK, stroke_width=3)
    vanes = VGroup(*[
        Rectangle(width=0.18, height=radius * 0.85, fill_color="#c8cdd6",
                  fill_opacity=1, stroke_width=0)
        .move_to(radius * 0.55 * np.array([np.cos(a), np.sin(a), 0]))
        .rotate(a, about_point=radius * 0.55 * np.array([np.cos(a), np.sin(a), 0]))
        for a in np.linspace(0, TAU, blades, endpoint=False)
    ])
    axle = Line(DOWN * (radius + 0.35), DOWN * radius, color=MUTED, stroke_width=4)
    g = VGroup(rim, vanes, hub, axle)
    g.wheel = VGroup(rim, vanes, hub)
    return g


def plate_pair(gap=1.9, w=2.6, h=0.18):
    top = Rectangle(width=w, height=h, fill_color=PLUS, fill_opacity=1, stroke_width=0).shift(UP * gap / 2)
    bot = Rectangle(width=w, height=h, fill_color=MINUS, fill_opacity=1, stroke_width=0).shift(DOWN * gap / 2)
    tl = T("+", size=44, color=PLUS).next_to(top, UP, buff=0.12)
    bl = T("–", size=44, color=MINUS).next_to(bot, DOWN, buff=0.12)
    g = VGroup(top, bot, tl, bl)
    g.top, g.bot = top, bot
    return g


def magnet(w=1.5, h=0.7):
    n = Rectangle(width=w / 2, height=h, fill_color=BAD, fill_opacity=1, stroke_width=0).shift(LEFT * w / 4)
    s = Rectangle(width=w / 2, height=h, fill_color=MINUS, fill_opacity=1, stroke_width=0).shift(RIGHT * w / 4)
    nt = T("N", size=26, color=INK).move_to(n)
    st = T("S", size=26, color=INK).move_to(s)
    return VGroup(n, s, nt, st)


def chip(txt, size=28, color=INK, fill="#1a212c", stroke=None, pad=0.32):
    label = T(txt, size=size, color=color)
    box = RoundedRectangle(width=label.width + pad * 2, height=label.height + pad * 1.3,
                           corner_radius=0.18, fill_color=fill, fill_opacity=1,
                           stroke_color=stroke or "#2b3442", stroke_width=2)
    g = VGroup(box, label)
    label.move_to(box)
    g.box, g.label = box, label
    return g


def card(title, body=None, w=4.4, h=2.4, accent=ACCENT):
    box = RoundedRectangle(width=w, height=h, corner_radius=0.22,
                           fill_color="#151c26", fill_opacity=1,
                           stroke_color=accent, stroke_width=3)
    parts = [T(title, size=32, color=INK, weight=BOLD)]
    if body:
        parts.append(T(body, size=25, color=MUTED))
    grp = VGroup(*parts).arrange(DOWN, buff=0.28).move_to(box)
    g = VGroup(box, grp)
    g.box = box
    return g


def beam_line(start, end, color=GLOW, width=7, opacity=0.95):
    return Line(start, end, color=color, stroke_width=width, stroke_opacity=opacity)


def electron(radius=0.13):
    d = Dot(radius=radius, color=MINUS)
    m = T("–", size=20, color=INK).move_to(d)
    return VGroup(d, m)


def stylised_portrait(name="J.J. Thomson", year="1897"):
    """A neutral silhouette card — no photograph, no likeness claim."""
    frame = RoundedRectangle(width=2.5, height=3.1, corner_radius=0.18,
                             stroke_color=MUTED, stroke_width=3,
                             fill_color="#171e28", fill_opacity=1)
    head = Circle(radius=0.42, fill_color="#3a434f", fill_opacity=1, stroke_width=0).shift(UP * 0.55)
    body = ArcBetweenPoints(LEFT * 0.85 + DOWN * 0.75, RIGHT * 0.85 + DOWN * 0.75,
                            angle=-PI * 0.75, stroke_width=0)
    torso = Polygon(LEFT * 0.82 + DOWN * 0.85, LEFT * 0.55 + DOWN * 0.02,
                    RIGHT * 0.55 + DOWN * 0.02, RIGHT * 0.82 + DOWN * 0.85,
                    fill_color="#3a434f", fill_opacity=1, stroke_width=0)
    sil = VGroup(head, torso).move_to(frame.get_center() + UP * 0.25)
    nm = T(name, size=26, color=INK).next_to(frame, DOWN, buff=0.22)
    yr = T(year, size=30, color=ACCENT, weight=BOLD).next_to(nm, DOWN, buff=0.14)
    return VGroup(frame, sil, nm, yr)


# ===== VISUAL_GRAMMAR.md — camera / emphasis ================================
# Feedback round 1: the animation was factually fine but visually monotonous — one
# small thing moving inside a static wide frame, and the eye never told where to look.
# These are the moves that fix it. Every one logs itself with a timestamp so
# eval_visual_grammar can check a beat actually directs attention instead of holding
# one framing for a minute.
#
# Mixed into a scene that (a) keeps its own clock (SaarthiScene / B3Scene) and
# (b) has a movable camera frame, i.e. derives from MovingCameraScene.

class EmphasisMixin:
    DIM = 0.25
    MIN_FRAME_WIDTH = 5.5

    # --- bookkeeping -------------------------------------------------------
    def _emph_init(self):
        if not hasattr(self, "_emphasis"):
            self._emphasis = []
            self._home_width = float(config.frame_width)

    def _log_emphasis(self, kind: str, on: str = ""):
        self._emph_init()
        self._emphasis.append({"t": round(getattr(self, "t", 0.0), 3),
                               "kind": kind, "on": on})

    @property
    def emphasis_log(self) -> list:
        self._emph_init()
        return self._emphasis

    # --- the moves ---------------------------------------------------------
    def push_in(self, mobj, margin: float = 1.5, run_time: float = 1.1, on: str = ""):
        """Zoom the frame onto the thing the narration is pointing at."""
        self._emph_init()
        w = max(self.MIN_FRAME_WIDTH, mobj.width + margin * 2, mobj.height * 16 / 9)
        self.play(self.camera.frame.animate.set(width=w).move_to(mobj),
                  run_time=run_time)
        self._log_emphasis("push_in", on or getattr(mobj, "name", ""))

    def pull_back(self, run_time: float = 1.0, on: str = ""):
        """Return to the full frame — the other half of every push-in."""
        self._emph_init()
        self.play(self.camera.frame.animate.set(width=self._home_width).move_to(ORIGIN),
                  run_time=run_time)
        self._log_emphasis("pull_back", on)

    def reframe(self, mobj, width: float | None = None, run_time: float = 1.0,
                on: str = ""):
        """Pan to a different part of the board without a full zoom — used between
        sub-points so consecutive seconds never look identical."""
        self._emph_init()
        w = width or self.camera.frame.width
        self.play(self.camera.frame.animate.set(width=w).move_to(mobj),
                  run_time=run_time)
        self._log_emphasis("reframe", on)

    def spotlight(self, focus, others, dim: float | None = None,
                  run_time: float = 0.8, on: str = ""):
        """Dim everything that is not the active element. The eye follows contrast."""
        self._emph_init()
        d = self.DIM if dim is None else dim
        anims = [m.animate.set_opacity(d) for m in others if m is not focus]
        if anims:
            self.play(*anims, run_time=run_time)
        self._log_emphasis("spotlight", on)

    def unspotlight(self, others, run_time: float = 0.6):
        anims = [m.animate.set_opacity(1.0) for m in others]
        if anims:
            self.play(*anims, run_time=run_time)

    def hold(self, seconds: float, drift: float = 0.05, on: str = "",
             scale: bool = True):
        """A stretchable hold that never becomes a frozen frame.

        The renderer stretches waits (not animations) to make a beat meet its
        narration, so a long beat can sit on one motionless frame for many seconds --
        exactly the monotony the teacher flagged. A slow camera creep is added for the
        duration of the wait, so the shot keeps breathing while staying stretchable.
        """
        self._emph_init()
        frame = self.camera.frame
        # Alternate the creep direction. A one-way push on every hold compounds: a
        # two-minute beat with a dozen holds would silently zoom ~15% and leave the
        # framing somewhere the scene never intended.
        self._creep_sign = -getattr(self, "_creep_sign", -1)
        target = frame.width * (1.0 - drift * self._creep_sign)

        def creep(m, dt):
            step = (frame.width - target) * min(1.0, dt / max(seconds, 0.001))
            w = m.width - step
            m.set(width=min(max(w, min(frame.width, target)), max(frame.width, target)))

        frame.add_updater(creep)
        try:
            self.wait(seconds, scale=scale)
        finally:
            frame.remove_updater(creep)
        self._log_emphasis("hold_drift", on)

    def emphasis_summary(self) -> dict:
        self._emph_init()
        kinds: dict[str, int] = {}
        for e in self._emphasis:
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        return {"count": len(self._emphasis), "by_kind": kinds,
                "events": self._emphasis}


class SaarthiDirectedScene(EmphasisMixin, SaarthiScene, MovingCameraScene):
    """SaarthiScene with a movable camera and the emphasis vocabulary.

    New factory scenes should subclass this rather than SaarthiScene, so that
    VISUAL_GRAMMAR's camera requirements are available by default. SaarthiScene is left
    untouched so existing lesson scenes keep rendering unchanged.
    """

    def tear_down(self):
        super().tear_down()
        out = os.environ.get("SAARTHI_CUEDIR")
        if out:
            p = Path(out) / f"{self.slug}.cues.json"
            if p.exists():
                data = json.loads(p.read_text())
                data["emphasis"] = self.emphasis_summary()
                p.write_text(json.dumps(data, indent=2))
