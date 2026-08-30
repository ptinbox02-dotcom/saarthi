#!/usr/bin/env python3
"""3Blue1Brown-style Manim kit for the ML1.1 rebuild.

What separates this from the factory's manimkit:

  * Canvas is near-black and the palette is 3b1b's own blues/yellow rather than the
    muted brand greys — high contrast, few hues, colour used to mean something.
  * Text is WRITTEN and shapes are DRAWN (Write/Create/Transform), never faded in on
    top of a static diagram. Motion carries the argument.
  * Electrons are an actual particle system, not a line labelled "beam". A field bends
    the particles themselves, so the student sees the evidence rather than a caption
    asserting it.
  * A moving camera, so the frame pushes in on whatever is being reasoned about.

Math is typeset with LaTeX when a toolchain is present and falls back to Unicode text
otherwise, so the scenes render on a bare machine either way (checked once at import).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path

import numpy as np
from manim import *

# The camera/emphasis vocabulary lives in the factory kit so there is exactly one
# implementation of VISUAL_GRAMMAR.md's moves; only the palette differs between the
# two kits. Imported by name rather than * so the factory's brand colours do not
# overwrite the 3b1b ones defined below.
_FACTORY = Path(__file__).resolve().parent.parent.parent / "micro-lectures" / "factory"
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))
from manimkit import EmphasisMixin  # noqa: E402

# ----- palette (3b1b) -------------------------------------------------------
BG = "#000000"
WHITE_ = "#ffffff"
GREY = "#888888"
GREY_D = "#4a4a4a"

BLUE = "#58C4DD"          # the workhorse: electrons, cathode, "the thing itself"
BLUE_DEEP = "#29ABCA"
BLUE_PALE = "#9CDCEB"
YELLOW = "#FFFF00"        # emphasis — the punchline of a beat
GOLD = "#F0AC5F"
RED = "#FC6255"           # positive plate / anode / "wrong"
GREEN = "#83C167"         # confirmed / correct
PURPLE = "#9A72AC"
TEAL = "#5CD0B3"

FONT = os.environ.get("SAARTHI_FONT", "Helvetica Neue")

# LaTeX gives real Computer Modern, which is most of the 3b1b typographic signature.
# Probed once: a missing toolchain must degrade to Unicode, never crash a render.
HAS_TEX = bool(shutil.which("latex") and shutil.which("dvisvgm"))


def T(txt, size=40, color=WHITE_, weight=NORMAL, **kw):
    return Text(txt, font=FONT, font_size=size, color=color, weight=weight, **kw)


def M(tex: str, unicode_fallback: str | None = None, size=48, color=WHITE_):
    """Typeset math. Falls back to Unicode text when LaTeX is unavailable."""
    if HAS_TEX:
        return MathTex(tex, font_size=size, color=color)
    return Text(unicode_fallback or tex, font=FONT, font_size=size * 0.8, color=color)


class TimedSceneMixin:
    """Own clock, cue points and exact padding — shared by the 2D and 3D bases.

    Semantics match the factory's SaarthiScene so the renderer's measure/stretch pass
    works unchanged: animations are never stretched, holds are, and the scene reports
    where its cue points and emphasis moves landed.
    """

    target_seconds: float = 0.0
    slug: str = "beat"

    def setup(self):
        super().setup()
        self.camera.background_color = BG
        self._t = 0.0
        self._anim_t = 0.0
        self._wait_t = 0.0
        self._in_wait = False
        self._cues: dict[str, float] = {}
        self.target_seconds = float(os.environ.get("SAARTHI_TARGET",
                                                   self.target_seconds or 0))
        self.slug = os.environ.get("SAARTHI_SLUG", self.slug)
        self.wait_scale = float(os.environ.get("SAARTHI_WAITSCALE", 1.0))
        self.rng = random.Random(20260809)      # deterministic particle layouts

    def play(self, *a, **kw):
        rt = kw.get("run_time")
        if rt is None:
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
        self._cues[name] = round(self._t, 3)

    def pad_to(self, total: float | None = None):
        """Fill the remaining time to the narration — without freezing.

        The slack here is not small: a beat whose animation is shorter than its
        narration can have ten seconds left over, and a bare wait turns that into a
        still image at exactly the moment the student is still listening. Routed
        through hold() so the shot keeps breathing and the tail is logged.
        """
        total = total or self.target_seconds
        remaining = total - self._t
        if total and remaining > 0.05:
            self.hold(remaining, on="tail", scale=False)

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
                "emphasis": self.emphasis_summary(),
            }, indent=2))

    # --- chrome ------------------------------------------------------------
    def title(self, txt, size=44, color=WHITE_):
        return T(txt, size=size, color=color).to_edge(UP, buff=0.5)

    def punch(self, txt, size=52, color=YELLOW):
        """The one line a beat is trying to land."""
        return T(txt, size=size, color=color, weight=BOLD)


class B3Scene(EmphasisMixin, TimedSceneMixin, MovingCameraScene):
    """Flat 3b1b canvas with a movable camera frame."""

    def focus(self, mobj, zoom=1.0, run_time=1.2, buff=1.4, on=""):
        """Kept for the scenes written before the shared mixin existed."""
        self.push_in(mobj, margin=buff, run_time=run_time, on=on)
        if zoom != 1.0:
            self.play(self.camera.frame.animate.scale(zoom), run_time=0.6)

    def reset_camera(self, run_time=1.0):
        self.pull_back(run_time=run_time)


class B3DScene(EmphasisMixin, TimedSceneMixin, ThreeDScene):
    """3D apparatus, per VISUAL_GRAMMAR.md section 2.

    A ThreeDScene has no `camera.frame`, so the emphasis moves are re-expressed as
    camera orientation changes (zoom / orbit) while still logging through the shared
    mixin — the eval counts moves, it does not care how the frame got there.

    Rule from the spec, kept here: **3D carries no critical text.** Every label and
    value is added with `add_fixed_in_frame_mobjects`, so it renders flat and crisp and
    never tumbles with the apparatus.
    """

    HOME_PHI = 68 * DEGREES
    HOME_THETA = -75 * DEGREES

    def home_camera(self, zoom=1.0):
        self.set_camera_orientation(phi=self.HOME_PHI, theta=self.HOME_THETA, zoom=zoom)

    def push_in(self, mobj=None, zoom=1.55, run_time=1.2, on="", **kw):
        self.move_camera(zoom=zoom, run_time=run_time)
        self._log_emphasis("push_in", on)

    def pull_back(self, zoom=1.0, run_time=1.0, on=""):
        self.move_camera(zoom=zoom, run_time=run_time)
        self._log_emphasis("pull_back", on)

    def orbit(self, d_theta=25 * DEGREES, d_phi=0.0, run_time=2.0, on=""):
        """Rotate around the apparatus — the move that makes depth legible."""
        self.move_camera(theta=self.camera.get_theta() + d_theta,
                         phi=self.camera.get_phi() + d_phi, run_time=run_time)
        self._log_emphasis("orbit", on)

    def hold(self, seconds: float, on: str = "", rate: float = 0.014,
             scale: bool = True):
        """A stretchable hold that keeps the apparatus readable.

        The 2D mixin creeps `camera.frame`, which a ThreeDCamera does not have. Here the
        equivalent is a slow ambient orbit: it is an updater, so it runs *during* the
        wait and stays stretchable by the renderer, and unlike a decorative drift it
        earns its place by making the depth of the apparatus legible. Direction
        alternates so a long beat oscillates instead of winding away from the framing.
        """
        self._orbit_sign = -getattr(self, "_orbit_sign", -1)
        self.begin_ambient_camera_rotation(rate=rate * self._orbit_sign)
        try:
            self.wait(seconds, scale=scale)
        finally:
            self.stop_ambient_camera_rotation()
        self._log_emphasis("hold_orbit", on)

    def reframe(self, mobj=None, width=None, run_time=1.0, on=""):
        """No frame to pan in 3D — an orbit is the equivalent reframing."""
        self.orbit(d_theta=18 * DEGREES, run_time=run_time, on=on)

    def flat(self, *mobs):
        """Pin flat mobjects to the frame so 3D never carries the text."""
        self.add_fixed_in_frame_mobjects(*mobs)
        return mobs[0] if len(mobs) == 1 else mobs


# ----- the particle system --------------------------------------------------
class ElectronStream(VGroup):
    """Electrons as actual moving particles between two x positions.

    The point of the whole rebuild: a cathode ray is shown as discrete charges in
    flight, so a deflecting field visibly acts on them. `deflect` maps progress along
    the tube (0..1) to a vertical displacement, which is how every proof in beats 3
    and 5 is staged — the beam bends because the particles do.
    """

    def __init__(self, x0: float, x1: float, y: float = 0.0, n: int = 26,
                 speed: float = 1.9, radius: float = 0.055, color: str = BLUE,
                 spread: float = 0.30, rng: random.Random | None = None,
                 glow: bool = True):
        super().__init__()
        self.x0, self.x1, self.y0 = x0, x1, y
        self.speed = speed
        self.spread = spread
        self.deflect = None            # callable(progress, lane) -> dy
        self.blocked_at = None         # progress in 0..1 where particles are absorbed
        # (progress, half_height): particles whose lane falls inside the obstacle are
        # absorbed at it, the rest sail past. That is what casts a real shadow with a
        # sharp edge, rather than drawing a dark rectangle and calling it one.
        self.block_zone = None
        # Half-height of the envelope the particles must stay inside. A strong field
        # would otherwise fling them straight through the glass wall, which reads as a
        # rendering bug rather than as deflection.
        self.y_clamp = None
        self._rng = rng or random.Random(7)

        self.dots = VGroup()
        for _ in range(n):
            d = Dot(radius=radius, color=color)
            if glow:
                d.set_glow_factor = getattr(d, "set_glow_factor", None)
            d.prog = self._rng.random()
            d.lane = self._rng.uniform(-spread, spread)
            self.dots.add(d)
        self.add(self.dots)
        self._place()

    def _pos(self, dot):
        p = dot.prog
        x = self.x0 + (self.x1 - self.x0) * p
        dy = self.deflect(p, dot.lane) if self.deflect else 0.0
        y = self.y0 + dot.lane + dy
        if self.y_clamp is not None:
            lo, hi = self.y0 - self.y_clamp, self.y0 + self.y_clamp
            y = min(hi, max(lo, y))
        return np.array([x, y, 0.0])

    def _place(self):
        for d in self.dots:
            d.move_to(self._pos(d))

    def start(self):
        span = max(0.001, self.x1 - self.x0)

        def upd(mob, dt):
            for d in mob.dots:
                d.prog += self.speed * dt / span
                if d.prog >= 1.0:
                    d.prog -= 1.0
                    d.lane = self._rng.uniform(-self.spread, self.spread)
                cut = self.blocked_at
                hidden = cut is not None and d.prog > cut
                if not hidden and self.block_zone is not None:
                    bp, bh = self.block_zone
                    hidden = d.prog > bp and abs(d.lane) < bh
                d.set_opacity(0.0 if hidden else 1.0)
                d.move_to(self._pos(d))

        self.add_updater(upd)
        return self

    def stop(self):
        self.clear_updaters()
        return self


def deflect_toward(strength: float, start: float = 0.30, end: float = 0.72):
    """Parabolic deflection that begins inside the field region and persists after it.

    Real charges accelerate while inside the plates and then travel straight, so the
    curve is quadratic in the field and linear beyond it — the shape a student is
    expected to recognise in a JEE diagram.
    """
    def f(p, lane=0.0):
        if p <= start:
            return 0.0
        if p <= end:
            u = (p - start) / (end - start)
            return strength * u * u
        u_end = 1.0
        slope = 2 * strength
        return strength * u_end + slope * (p - end) / (end - start)
    return f


# ----- apparatus (3b1b styling: thin strokes, dark fills, colour = meaning) --
def discharge_tube(width=9.0, height=2.8, stroke=GREY):
    glass = RoundedRectangle(width=width, height=height, corner_radius=0.55,
                             stroke_color=stroke, stroke_width=3.5,
                             fill_color="#05070a", fill_opacity=1)
    cath = Rectangle(width=0.16, height=height * 0.42, fill_color=BLUE,
                     fill_opacity=1, stroke_width=0)
    cath.move_to(glass.get_left() + RIGHT * 0.62)
    anod = Rectangle(width=0.16, height=height * 0.42, fill_color=RED,
                     fill_opacity=1, stroke_width=0)
    anod.move_to(glass.get_right() + LEFT * 0.62)
    lead_l = Line(glass.get_left() + LEFT * 0.85, cath.get_left(),
                  color=GREY_D, stroke_width=3)
    lead_r = Line(anod.get_right(), glass.get_right() + RIGHT * 0.85,
                  color=GREY_D, stroke_width=3)
    g = VGroup(glass, lead_l, lead_r, cath, anod)
    g.glass, g.cathode, g.anode = glass, cath, anod
    g.leads = VGroup(lead_l, lead_r)
    return g


def field_arrows(region, n=4, direction=UP, color=RED, length=0.9):
    """Evenly spaced field arrows across a region — the cause, drawn separately
    from the effect so the two can be introduced one at a time."""
    xs = np.linspace(region.get_left()[0] + 0.9, region.get_right()[0] - 0.9, n)
    y = region.get_center()[1]
    arrows = VGroup()
    for x in xs:
        start = np.array([x, y - direction[1] * length / 2, 0])
        end = np.array([x, y + direction[1] * length / 2, 0])
        arrows.add(Arrow(start, end, buff=0, color=color, stroke_width=4,
                         max_tip_length_to_length_ratio=0.28))
    return arrows


def _perp_field(region, n, rows, color, radius, into: bool):
    """B perpendicular to the screen: (x) into the page, (.) out of it.

    A magnetic field that deflects the beam within the screen plane MUST point out of
    that plane, because the force is q v x B. Drawing B as in-plane arrows beside E --
    as the first cut of these scenes did -- describes a field whose force acts
    perpendicular to the screen, which can never balance an in-plane electric force.
    Symbols are laid out in rows above and below the axis so the beam stays visible.
    """
    xs = np.linspace(region.get_left()[0] + 1.0, region.get_right()[0] - 1.0, n)
    y0 = region.get_center()[1]
    g = VGroup()
    for dy in rows:
        for x in xs:
            c = Circle(radius=radius, stroke_color=color, stroke_width=3)
            c.move_to([x, y0 + dy, 0])
            if into:
                k = radius * 0.62
                mark = VGroup(
                    Line([x - k, y0 + dy - k, 0], [x + k, y0 + dy + k, 0],
                         color=color, stroke_width=3),
                    Line([x - k, y0 + dy + k, 0], [x + k, y0 + dy - k, 0],
                         color=color, stroke_width=3),
                )
            else:
                mark = Dot([x, y0 + dy, 0], radius=radius * 0.30, color=color)
            g.add(VGroup(c, mark))
    return g


def field_into_page(region, n=5, rows=(0.95, -0.95), color=BLUE, radius=0.17):
    """B pointing away from the viewer, drawn (x)."""
    return _perp_field(region, n, rows, color, radius, into=True)


def field_out_of_page(region, n=5, rows=(0.95, -0.95), color=BLUE, radius=0.17):
    """B pointing toward the viewer, drawn with a dot."""
    return _perp_field(region, n, rows, color, radius, into=False)


def fluorescent_wall(tube, color=GREEN):
    """The far glass wall that glows when the beam lands on it."""
    g = tube.glass
    wall = Line(g.get_corner(UR) + LEFT * 0.08 + DOWN * 0.12,
                g.get_corner(DR) + LEFT * 0.08 + UP * 0.12,
                color=color, stroke_width=10)
    wall.set_opacity(0)
    return wall


def labelled(mobj, text, direction=DOWN, buff=0.3, size=26, color=GREY):
    lbl = T(text, size=size, color=color).next_to(mobj, direction, buff=buff)
    return lbl


def check(size=0.42, color=GREEN, width=8):
    p = VMobject(stroke_color=color, stroke_width=width)
    p.set_points_as_corners([LEFT * 0.45 + DOWN * 0.05,
                             DOWN * 0.42 + LEFT * 0.1,
                             RIGHT * 0.5 + UP * 0.45])
    return p.scale(size / 0.5)


def cross(size=0.45, color=RED, width=8):
    a = Line(UL, DR, color=color, stroke_width=width).scale(size)
    b = Line(UR, DL, color=color, stroke_width=width).scale(size)
    return VGroup(a, b)


def underline(mobj, color=YELLOW, buff=0.12, width=5):
    return Line(mobj.get_corner(DL) + DOWN * buff,
                mobj.get_corner(DR) + DOWN * buff,
                color=color, stroke_width=width)


# ----- 3D apparatus (VISUAL_GRAMMAR.md section 2) ---------------------------
def tube3d(length=7.0, radius=1.15, stroke=GREY):
    """A discharge tube with real depth: glass cylinder + two electrode discs.

    Built along the X axis so the beam runs left-to-right on screen at the default
    camera orientation, and so E (Z) and B (Y) are genuinely perpendicular to it and to
    each other — the geometry the flat version could only imply with (x) symbols.
    """
    # Low mesh resolution on purpose: a dense wireframe reads as noise and competes
    # with the beam, which is the thing the student is meant to watch.
    glass = Cylinder(radius=radius, height=length, direction=RIGHT,
                     resolution=(4, 16), fill_opacity=0.06, fill_color=BLUE_PALE,
                     stroke_color=stroke, stroke_width=1.0, checkerboard_colors=False)
    glass.set_stroke(opacity=0.45)
    cath = Circle(radius=radius * 0.42, fill_color=BLUE, fill_opacity=1, stroke_width=0)
    cath.rotate(PI / 2, axis=UP).move_to(LEFT * (length / 2 - 0.35))
    anod = Circle(radius=radius * 0.42, fill_color=RED, fill_opacity=1, stroke_width=0)
    anod.rotate(PI / 2, axis=UP).move_to(RIGHT * (length / 2 - 0.35))
    g = VGroup(glass, cath, anod)
    g.glass, g.cathode, g.anode = glass, cath, anod
    g.length, g.radius = length, radius
    return g


class ElectronStream3D(VGroup):
    """The beam as particles in three dimensions.

    Deflection is applied along Z (vertical on screen) so an E field along -Z and a B
    field along +Y push the electrons in opposite directions — the balance that Thomson
    actually performed, shown rather than asserted.
    """

    def __init__(self, x0: float, x1: float, n: int = 18, speed: float = 2.4,
                 radius: float = 0.075, color: str = BLUE, spread: float = 0.16,
                 rng: random.Random | None = None):
        super().__init__()
        self.x0, self.x1 = x0, x1
        self.speed, self.spread = speed, spread
        self.deflect = None            # callable(progress) -> dz
        self.z_clamp = None
        self._rng = rng or random.Random(11)
        self.dots = VGroup()
        for _ in range(n):
            d = Dot3D(radius=radius, color=color, resolution=(6, 6))
            d.prog = self._rng.random()
            d.lane_y = self._rng.uniform(-spread, spread)
            d.lane_z = self._rng.uniform(-spread, spread)
            self.dots.add(d)
        self.add(self.dots)
        self._place()

    def _pos(self, d):
        x = self.x0 + (self.x1 - self.x0) * d.prog
        dz = self.deflect(d.prog) if self.deflect else 0.0
        z = d.lane_z + dz
        if self.z_clamp is not None:
            z = max(-self.z_clamp, min(self.z_clamp, z))
        return np.array([x, d.lane_y, z])

    def _place(self):
        for d in self.dots:
            d.move_to(self._pos(d))

    def start(self):
        span = max(0.001, self.x1 - self.x0)

        def upd(mob, dt):
            for d in mob.dots:
                d.prog += self.speed * dt / span
                if d.prog >= 1.0:
                    d.prog -= 1.0
                    d.lane_y = self._rng.uniform(-self.spread, self.spread)
                    d.lane_z = self._rng.uniform(-self.spread, self.spread)
                d.move_to(self._pos(d))

        self.add_updater(upd)
        return self

    def stop(self):
        self.clear_updaters()
        return self


def deflect3d(strength: float, start: float = 0.32, end: float = 0.70):
    """Quadratic inside the field region, linear after it — as a real charge moves."""
    def f(p):
        if p <= start:
            return 0.0
        if p <= end:
            u = (p - start) / (end - start)
            return strength * u * u
        return strength + 2 * strength * (p - end) / (end - start)
    return f


def field_arrows3d(length, radius, direction=OUT, color=RED, n=3, span=0.55):
    """Field arrows drawn in 3D, spaced along the tube axis."""
    arrows = VGroup()
    for x in np.linspace(-length * span / 2, length * span / 2, n):
        start = np.array([x, 0, 0]) - direction * radius * 1.5
        end = np.array([x, 0, 0]) + direction * radius * 1.5
        arrows.add(Arrow3D(start=start, end=end, color=color,
                           thickness=0.028, base_radius=0.13, height=0.34))
    return arrows


# ----- orbital geometry (ML5.10) --------------------------------------------
def s_orbital(radius=1.4, color=BLUE, opacity=0.22, resolution=(24, 48)):
    """Boundary surface of an s orbital: a sphere.

    Drawn translucent because it is a *probability* boundary, not a solid ball — the
    single most common misreading of these diagrams.
    """
    sph = Sphere(radius=radius, resolution=resolution,
                 fill_opacity=opacity, fill_color=color,
                 stroke_width=0.6, stroke_color=color, checkerboard_colors=False)
    sph.set_stroke(opacity=0.25)
    return sph


def p_lobe(axis=OUT, size=1.5, elong=1.45, color=BLUE, opacity=0.30, sign=+1,
           resolution=(18, 36)):
    """One lobe of a p orbital.

    The angular part of a p orbital goes as cos(theta), so r = |cos(theta)| in spherical
    coordinates traces a lobe that pinches to a point at the nucleus — which is the
    physically important bit: psi = 0 there. Stretched along its own axis so the pair
    reads as an elongated dumbbell rather than two tangent balls.
    """
    def f(u, v):
        # u = polar angle over one hemisphere, v = azimuth
        r = size * abs(np.cos(u))
        x = r * np.sin(u) * np.cos(v)
        y = r * np.sin(u) * np.sin(v)
        z = r * np.cos(u) * elong
        return np.array([x, y, z])

    lo, hi = (0, PI / 2) if sign > 0 else (PI / 2, PI)
    lobe = Surface(f, u_range=[lo, hi], v_range=[0, TAU], resolution=resolution,
                   fill_opacity=opacity, fill_color=color, stroke_width=0.5,
                   stroke_color=color, checkerboard_colors=False)
    lobe.set_stroke(opacity=0.3)
    # built along z; rotate onto the requested axis
    if not np.allclose(axis, OUT):
        ax = np.array(axis, dtype=float)
        ax = ax / np.linalg.norm(ax)
        rot_axis = np.cross(np.array([0, 0, 1.0]), ax)
        if np.linalg.norm(rot_axis) > 1e-6:
            angle = np.arccos(np.clip(np.dot([0, 0, 1.0], ax), -1, 1))
            lobe.rotate(angle, axis=rot_axis, about_point=ORIGIN)
    return lobe


def p_orbital(axis=OUT, size=1.5, colors=(BLUE, RED), opacity=0.30, phase=False):
    """A full p orbital: two lobes meeting at the nucleus.

    `phase` colours the lobes differently to show the opposite sign of psi — a property
    of the wave function, not of charge.
    """
    c1, c2 = colors if phase else (colors[0], colors[0])
    up = p_lobe(axis=axis, size=size, color=c1, opacity=opacity, sign=+1)
    dn = p_lobe(axis=axis, size=size, color=c2, opacity=opacity, sign=-1)
    g = VGroup(up, dn)
    g.lobes = (up, dn)
    return g


def nodal_plane(size=3.2, color=YELLOW, opacity=0.18, normal=OUT):
    """The plane where psi = 0, drawn as a translucent square through the nucleus."""
    pl = Square(side_length=size, fill_color=color, fill_opacity=opacity,
                stroke_color=color, stroke_width=2)
    if np.allclose(normal, OUT):
        pass                      # square already lies in the xy-plane
    else:
        n = np.array(normal, dtype=float); n = n / np.linalg.norm(n)
        rot_axis = np.cross(np.array([0, 0, 1.0]), n)
        if np.linalg.norm(rot_axis) > 1e-6:
            angle = np.arccos(np.clip(np.dot([0, 0, 1.0], n), -1, 1))
            pl.rotate(angle, axis=rot_axis, about_point=ORIGIN)
    return pl


def axis_triad(length=2.6, labels=("x", "y", "z"), color=GREY):
    """The three Cartesian axes — what "mutually perpendicular" actually means."""
    axes = VGroup()
    for vec in (RIGHT, UP, OUT):
        axes.add(Line(-np.array(vec) * length, np.array(vec) * length,
                      color=color, stroke_width=2, stroke_opacity=0.5))
    return axes


def nucleus(radius=0.075, color=WHITE_):
    return Dot3D(radius=radius, color=color)
