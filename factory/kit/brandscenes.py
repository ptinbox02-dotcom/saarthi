"""Brand intro / outro cards — rendered by Manim, shared by every lesson.

Text comes from env so the same file serves the whole backlog:
  SAARTHI_TITLE, SAARTHI_HANDLE, SAARTHI_TAGLINE, SAARTHI_CTA, SAARTHI_TARGET
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from manimkit import *          # noqa: F401,F403


class Intro(SaarthiScene):
    slug = "intro"
    target_seconds = 2.0

    def construct(self):
        handle = T(os.environ.get("SAARTHI_HANDLE", "@saarthi"),
                   size=76, color=ACCENT, weight=BOLD)
        title = T(os.environ.get("SAARTHI_TITLE", ""), size=36, color=INK)
        if title.width > 12.0:
            title.scale(12.0 / title.width)
        rule = Line(LEFT * 2.2, RIGHT * 2.2, color="#2b3442", stroke_width=4)
        g = VGroup(handle, rule, title).arrange(DOWN, buff=0.42)
        self.play(FadeIn(handle, shift=UP * 0.25), run_time=0.6)
        self.play(Create(rule), FadeIn(title, shift=UP * 0.15), run_time=0.7)
        self.pad_to()


class Outro(SaarthiScene):
    slug = "outro"
    target_seconds = 3.0

    def construct(self):
        handle = T(os.environ.get("SAARTHI_HANDLE", "@saarthi"),
                   size=76, color=ACCENT, weight=BOLD)
        tag = T(os.environ.get("SAARTHI_TAGLINE", "JEE ka asli mentor"), size=40, color=INK)
        cta = T(os.environ.get("SAARTHI_CTA", ""), size=28, color=MUTED)
        if cta.width > 12.0:
            cta.scale(12.0 / cta.width)
        g = VGroup(handle, tag, cta).arrange(DOWN, buff=0.5)
        self.play(FadeIn(handle, shift=UP * 0.25), run_time=0.7)
        self.play(FadeIn(tag), run_time=0.6)
        self.play(FadeIn(cta), run_time=0.7)
        self.pad_to()
