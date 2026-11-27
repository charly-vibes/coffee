#!/usr/bin/env python3
"""
test_fpa_f6_a11y.py — F6: accesibilidad del chart y motion (FPA-175/178,
094): readout persistente, reduced-motion, retro confinado y focus
visible.

Parte de la antigua test_fpa_f6.py (coffe-kav.5); el fixture F6 vive en
tests/test_fpa_f6.py.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f6", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class F6Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="T")


class TestAccesibilidadJs(F6Base):

    def test_reduced_motion(self):
        """FPA-178: sin animación bajo prefers-reduced-motion."""
        self.assertIn("prefers-reduced-motion", self.html)

    def test_retro_confinado(self):
        """FPA-178: retro solo en header/footer (ya F1), sin animación."""
        header = self.html[:self.html.index("<main")]
        footer = self.html[self.html.index("<footer"):]
        body = self.html[self.html.index("<main"):self.html.index("<footer")]
        for zone in (header, footer):
            self.assertIn('class="retro', zone)
        self.assertNotIn('class="retro"', body)

    def test_readout_persistente(self):
        """FPA-175: chart inspeccionable por tap/focus/hover con readout
        persistente (no captions hover-only)."""
        self.assertIn('id="wf-readout"', self.html)
        self.assertIn('tabindex="0"', self.html)
        self.assertIn("aria-label", self.html)
        js = self.html.split("<script>")[-1]
        self.assertIn("wf-readout", js)
        self.assertIn("focus", js)

    def test_focus_visible(self):
        """FPA-094: focus visible por CSS."""
        self.assertIn(":focus-visible", self.html)


if __name__ == "__main__":
    unittest.main()
