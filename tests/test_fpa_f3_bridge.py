#!/usr/bin/env python3
"""
test_fpa_f3_bridge.py — FPA-060…068, 101: bridge precio-volumen-mix con
identidad, FME en meses parciales, eje truncado, waterfall y mix stack.

Parte de la antigua test_fpa_f3.py (coffe-kav.5); el fixture y el config
viven en tests/test_fpa_f2.py.
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
viz = f2.viz
CONFIG = f2.CONFIG


def _mini_month(interactions, cost, models=None):
    """Mes mínimo para tests de bridge directo."""
    return {
        "interactions": interactions, "cost_effective": cost, "cost_real": 0.0,
        "models": models or {}, "tools": {}, "subscription_fees": 0.0,
        "ym": "2026-06",
    }


def _mini_meta(partial, elapsed, total_days, has_data=True):
    return {"partial": partial, "elapsed": elapsed, "total_days": total_days,
            "has_data": has_data}


class _BridgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


class TestBridge(_BridgeBase):
    """FPA-060…068: fórmulas Volume/Mix/Rate, identidad, FME, eje truncado."""

    def test_formulas_volume_mix_rate(self):
        """FPA-061…063: may→jun del fixture, valores a mano.
        may: sonnet q40 p=.75, opus q20 p=.5 → Q0=100, cost0=50, rate0=.5
        jun: opus q80 p=.75, gpt q20 p=.25 → Q1=100, cost1=65
        Volume = 0×.5 = 0; Mix = (80×.5+20×.25) − 100×.5 = −5; Rate = 80×.25 = 20."""
        b = self.model["bridge"]["pairs"]["2026-06"]
        self.assertAlmostEqual(0.0, b["volume"], places=6)
        self.assertAlmostEqual(-5.0, b["mix"], places=6)
        self.assertAlmostEqual(20.0, b["rate"], places=6)
        self.assertAlmostEqual(15.0, b["delta"], places=6)

    def test_modelo_nuevo_contribuye_solo_a_mix(self):
        """FPA-064: gpt sin interacciones previas → rate prior = rate actual."""
        b = self.model["bridge"]["pairs"]["2026-06"]
        # la contribución de gpt al Rate es 20×(.25−.25) = 0; solo Mix (−5)
        self.assertAlmostEqual(20.0, b["rate"], places=6)

    def test_identidad_todos_los_pares(self):
        """FPA-065/101: Volume+Mix+Rate = Δcoste dentro de $0.01."""
        pairs = self.model["bridge"]["pairs"]
        self.assertEqual({"2026-06", "2026-07"}, set(pairs))
        for ym, b in pairs.items():
            residual = abs(b["volume"] + b["mix"] + b["rate"] - b["delta"])
            self.assertLessEqual(residual, 0.01, ym)
            self.assertLessEqual(b["identity_residual"], 0.01, ym)

    def test_fme_en_meses_parciales(self):
        """FPA-066: jun→jul (jul parcial 10/31) usa valores FME y lo etiqueta.
        jul escalado ×3.1: Q1=310, cost1=93 → Volume=(310−100)×.65=136.5;
        jul escalado ×3.1: Q1=310, cost1=93; sonnet q93 (nuevo: p0=p1=1.0,
        FPA-064), gpt q217 p1=0; opus del mes previo no está en jul.
        rate₀ = 0.65 → Volume=(310−100)×0.65=136.5;
        Mix = (217×0.25 + 93×1.0) − 310×0.65 = −54.25;
        Rate = 217×(0−0.25) = −54.25; Δ = 28."""
        b = self.model["bridge"]["pairs"]["2026-07"]
        self.assertTrue(b["fme"])
        self.assertIn("FME", b["label"])
        self.assertAlmostEqual(136.5, b["volume"], places=6)
        self.assertAlmostEqual(-54.25, b["mix"], places=6)
        self.assertAlmostEqual(-54.25, b["rate"], places=6)
        self.assertAlmostEqual(28.0, b["delta"], places=6)

    def test_sin_fme_meses_completos(self):
        b = self.model["bridge"]["pairs"]["2026-06"]
        self.assertFalse(b["fme"])

    def test_eje_truncado_cuando_delta_pequeno(self):
        """FPA-067: |Δ| < 10% de los totales → eje truncado etiquetado."""
        meta = {"ym": "2026-06", "partial": False, "elapsed": 31,
                "total_days": 31, "has_data": True}
        prev = _mini_month(100, 1000.0,
                           {"m": {"interactions": 100, "cost_effective": 1000.0}})
        cur = _mini_month(100, 1005.0,
                          {"m": {"interactions": 100, "cost_effective": 1005.0}})
        b = viz.build_bridge(prev, cur, meta, meta)
        self.assertTrue(b["truncated"])
        self.assertIn("truncado", b["axis_label"].lower())
        # Δ grande relativo → sin truncar
        cur2 = _mini_month(100, 1500.0,
                          {"m": {"interactions": 100, "cost_effective": 1500.0}})
        b2 = viz.build_bridge(prev, cur2, meta, meta)
        self.assertFalse(b2["truncated"])

    def test_waterfall_svg(self):
        """FPA-060: waterfall renderizado por par (SVG inline)."""
        svg = self.model["bridge"]["pairs"]["2026-06"]["svg"]
        self.assertIn("<svg", svg)
        self.assertIn("Volumen", svg)
        self.assertIn("Mix", svg)
        self.assertIn("Rate", svg)
        # eje truncado solo donde aplica
        self.assertNotIn("truncado", self.model["bridge"]["pairs"]["2026-06"]["svg"])

    def test_mix_stack_100_por_ciento(self):
        """FPA-068: mix 100% stacked por mes en share de coste."""
        stack = self.model["bridge"]["mix_stack"]
        jun = stack["2026-06"]
        self.assertAlmostEqual(1.0, sum(s["share"] for s in jun["segments"]), places=6)
        self.assertFalse(jun["proxy"])
        labels = {s["model"] for s in jun["segments"]}
        self.assertEqual({"claude-opus-4.7", "gpt-5.4"}, labels)

    def test_mix_stack_proxy_sin_coste_por_modelo(self):
        """Reporte real (modelos sin coste) → proxy por interacciones, visible."""
        fixture = f2.f2_fixture()
        fixture["monthly"]["2026-06"]["models"] = {
            "claude-opus-4.7": 80, "gpt-5.4": 20}  # counters, sin coste
        stack = viz.build_mix_stack(fixture)
        jun = stack["2026-06"]
        self.assertTrue(jun["proxy"])
        self.assertAlmostEqual(0.8, jun["segments"][0]["share"], places=6)
        self.assertTrue(jun["proxy_reason"])


if __name__ == "__main__":
    unittest.main()
