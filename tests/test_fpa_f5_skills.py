#!/usr/bin/env python3
"""
test_fpa_f5_skills.py — FPA-113…115: skills y commands con zero/once, y
validación del timeline.gap_days del config.

Parte de la antigua test_fpa_f5.py (coffe-kav.5); el fixture F5 vive en
tests/test_fpa_f5.py.
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


viz = _load("viz_fpa_f5", "scripts/viz-fpa.py")
f5 = _load("test_fpa_f5", "tests/test_fpa_f5.py")
fpa_config = _load("fpa_config_f5", "scripts/fpa_config.py")
f5_fixture = f5.f5_fixture
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class F5Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f5_fixture()
        cls.usage = viz.build_usage_patterns(cls.fixture, CONFIG)

    def _by_name(self, items, name):
        return next(x for x in items if x["name"] == name)


class TestSkillsCommands(F5Base):

    def test_top_skills(self):
        """FPA-113: ordenadas por usos desc."""
        top = self.usage["skills"]["top"]
        self.assertEqual(["commit", "tdd", "ro5", "wrap-up"],
                         [s["name"] for s in top])
        self.assertEqual([75, 4, 1, 1], [s["uses"] for s in top])

    def test_skills_once(self):
        """FPA-114: skills usadas exactamente una vez."""
        self.assertEqual(["ro5", "wrap-up"], self.usage["skills"]["once"])

    def test_skills_zero_na_con_razon(self):
        """FPA-008/114: el tracker no emite instaladas → n/a con razón."""
        self.assertIsNone(self.usage["skills"]["zero"])
        self.assertTrue(self.usage["skills"]["zero_reason"])

    def test_skills_instaladas_emitidas(self):
        """FPA-114: si el tracker emite instaladas, zero-uso sale de la
        diferencia."""
        fx = f5_fixture()
        fx["skills_installed"] = ["commit", "tdd", "ro5", "wrap-up",
                                  "sin-uso", "otro-sin-uso"]
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertEqual(["otro-sin-uso", "sin-uso"], usage["skills"]["zero"])

    def test_trend_na_con_razon(self):
        """FPA-114/115: el tracker no emite uso por mes → n/a con razón."""
        self.assertIsNone(self.usage["skills"]["trend"])
        self.assertTrue(self.usage["skills"]["trend_reason"])
        self.assertIsNone(self.usage["commands"]["trend"])
        self.assertTrue(self.usage["commands"]["trend_reason"])

    def test_top_commands(self):
        self.assertEqual(["/clear", "/model"],
                         [c["name"] for c in self.usage["commands"]["top"]])


class TestGapDaysConfig(unittest.TestCase):

    def test_validacion_gap_days(self):
        cfg = json.loads(json.dumps(CONFIG))
        cfg["timeline"] = {"gap_days": 0}
        errors = fpa_config.validate_config(cfg)
        self.assertTrue(any("timeline.gap_days" in e for e in errors))
        del cfg["timeline"]
        self.assertEqual([], fpa_config.validate_config(cfg))


if __name__ == "__main__":
    unittest.main()
