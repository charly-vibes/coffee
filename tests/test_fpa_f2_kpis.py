#!/usr/bin/env python3
"""
test_fpa_f2_kpis.py — FPA-030…045 y FPA-140…142/120: KPI strip y
sección Data del F2.

Parte de la antigua test_fpa_f2.py (coffe-kav.5); el fixture y el config
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


class _KpisBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


class TestKpis(_KpisBase):
    """FPA-030…045: valores, n/a con denominador cero, daily rates, exclusión."""

    def test_12_kpis_base(self):
        keys = {k["key"] for k in self.model["views"]["all"]["kpis"]}
        for expected in ("cost_effective", "cost_cash", "leverage", "cost_per_1k_eff",
                         "cost_per_1k_cash", "cost_per_session", "top3_concentration",
                         "premium_share", "autonomous_share", "multitasking_share",
                         "cache_hit_rate", "out_in_ratio"):
            self.assertIn(expected, keys)

    def test_valores_contra_fixture(self):
        """Full period: efectivo 50+65+30 = 145; cash 20+25+0 = 45."""
        kpis = {k["key"]: k for k in self.model["views"]["all"]["kpis"]}
        self.assertAlmostEqual(145.0, kpis["cost_effective"]["value"])
        self.assertAlmostEqual(45.0, kpis["cost_cash"]["value"])
        self.assertAlmostEqual(145.0 / 45.0, kpis["leverage"]["value"], places=4)
        # 3 proyectos con coste → top-3 = 100%
        self.assertAlmostEqual(1.0, kpis["top3_concentration"]["value"])
        # premium = opus: 10 (may) + 60 (jun) + 0 (jul) = 70 / 145
        self.assertAlmostEqual(70.0 / 145.0, kpis["premium_share"]["value"], places=4)
        # autonomous: (1+2)/8 sesiones
        self.assertAlmostEqual(3 / 8, kpis["autonomous_share"]["value"], places=4)

    def test_top3_concentracion_del_periodo_no_suma_mensuales(self):
        """coffe-hn7: el KPI top-3 de una ventana es el top-3 del agregado
        del periodo (matchea Pareto y alerta), no la suma de top-3
        mensuales (que sobreestima cuando el ranking cambia por mes)."""
        fx = f2.f2_fixture()
        # 4to proyecto: su coste vive en jun, donde los demás ya son top
        fx["project_monthly"]["charly-zzz"] = {
            "2026-05": {"interactions": 0, "cost_effective": 0.0},
            "2026-06": {"interactions": 60, "cost_effective": 60.0},
            "2026-07": {"interactions": 0, "cost_effective": 0.0}}
        kpis = {k["key"]: k for k in
                viz.build_model(fx, CONFIG)["views"]["all"]["kpis"]}
        # agregado del periodo: top-3 = coffee 85 + zzz 60 + atril 55 = 200
        # (total 205) → 97.6%; la suma de top-3 mensuales daría 100%.
        self.assertAlmostEqual(200.0 / 205.0,
                               kpis["top3_concentration"]["value"], places=4)

    def test_delta_vs_prior_ventana_previa(self):
        """jun vs may por tasa diaria: 65/30 vs 50/31 → +34.3%.
        'all' no tiene periodo previo en el reporte → delta n/a."""
        kpis = {k["key"]: k for k in self.model["views"]["month:2026-06"]["kpis"]}
        self.assertAlmostEqual((65.0 / 30) / (50.0 / 31) - 1,
                               kpis["cost_effective"]["delta"], places=3)
        self.assertIsNone(self.model["views"]["all"]["kpis"][0]["delta"])

    def test_daily_rate_en_mes_parcial(self):
        """FPA-041: julio parcial (10/31) compara por tasa diaria."""
        eff = next(k for k in self.model["views"]["month:2026-07"]["kpis"]
                   if k["key"] == "cost_effective")
        self.assertEqual("daily-rate", eff["delta_kind"])
        # julio: 30/10 = 3.0/día; junio: 65/30 ≈ 2.1667/día → +38.46%
        self.assertAlmostEqual((30.0 / 10) / (65.0 / 30) - 1, eff["delta"], places=4)

    def test_denominador_cero_na_con_razon(self):
        """FPA-042: julio sin sesiones → coste por sesión n/a, no cero."""
        k = next(k for k in self.model["views"]["month:2026-07"]["kpis"]
                 if k["key"] == "cost_per_session")
        self.assertIsNone(k["value"])
        self.assertEqual("n/a", k["display"])
        self.assertTrue(k["reason"])

    def test_outcome_kpis_solo_con_datos(self):
        """FPA-040: sin commits/releases en el fixture → sin cards outcome."""
        keys = {k["key"] for k in self.model["views"]["all"]["kpis"]}
        self.assertNotIn("cost_per_commit", keys)

    def test_token_kpis_con_excluded_share(self):
        """FPA-045: julio con codex sin tokens → exclusión con share visible."""
        cache_hit = next(k for k in self.model["views"]["month:2026-07"]["kpis"]
                         if k["key"] == "cache_hit_rate")
        self.assertAlmostEqual(0.7, cache_hit["excluded_share"])  # 70/100 codex
        self.assertIsNotNone(cache_hit["value"])

    def test_cache_hit_rate_formula(self):
        """FPA-043: cache_read / (in + cache_read + cache_write). may."""
        ch = next(k for k in self.model["views"]["month:2026-05"]["kpis"]
                  if k["key"] == "cache_hit_rate")
        # in 60000, cr 40000, cw 12000 → 40000/112000
        self.assertAlmostEqual(40000 / 112000, ch["value"], places=6)

    def test_out_in_ratio(self):
        """FPA-044: out/in del periodo = 85000/170000 = 0.5."""
        kpis = {k["key"]: k for k in self.model["views"]["all"]["kpis"]}
        self.assertAlmostEqual(0.5, kpis["out_in_ratio"]["value"], places=6)

    def test_premium_share_es_assumed(self):
        """FPA-003/037: deriva de la lista premium del config → assumed."""
        kpis = {k["key"]: k for k in self.model["views"]["all"]["kpis"]}
        self.assertEqual("assumed", kpis["premium_share"]["provenance"])

    def test_multitasking_weighted_by_interactions(self):
        """FPA-039: share de interacciones en horas con ≥2 proyectos."""
        mt = next(k for k in self.model["views"]["month:2026-05"]["kpis"]
                  if k["key"] == "multitasking_share")
        self.assertAlmostEqual(60 / 100, mt["value"], places=6)

    def test_mes_sin_datos_excluido(self):
        """FPA-017: ningún KPI tiene series de meses sin datos."""
        for k in self.model["views"]["all"]["kpis"]:
            for p in k["spark"]:
                self.assertNotEqual("2026-08", p["ym"])


class TestDataNotes(_KpisBase):
    """FPA-140/141/142/120: definición, kinds, share filtrado, timezone."""

    def test_definicion_de_interaccion(self):
        notes = self.model["data_notes"]
        self.assertIn("interacción", notes["interaction_definition"])

    def test_shares_de_eventos(self):
        # tool calls: 20+10+20 = 50 de 300 eventos; FPA-141: filtrados 30/330
        cases = [("tool_call_share", 50 / 300), ("filtered_share", 30 / 330)]
        notes = self.model["data_notes"]
        for key, expected in cases:
            with self.subTest(key=key):
                self.assertAlmostEqual(expected, notes[key], places=4)

    def test_timezone_presente(self):
        """FPA-142: timezone del fixture → no unverified."""
        notes = self.model["data_notes"]
        self.assertFalse(notes["unverified_time"])
        self.assertIn("Buenos_Aires", notes["timezone"])

    def test_timezone_ausente_flag_unverified(self):
        fixture = f2.f2_fixture()
        del fixture["metadata"]["timezone"]
        notes = viz.build_data_notes(fixture)
        self.assertTrue(notes["unverified_time"])
        self.assertTrue(notes["timezone_missing_reason"])

    def test_concurrency_labeled(self):
        """FPA-120: (a)(b) paralelo, (c) contexto humano."""
        conc = self.model["data_notes"]["concurrency"]
        self.assertEqual("parallel-agent", conc["distinct_projects_per_hour"]["measure"])
        self.assertEqual("parallel-agent", conc["peak_simultaneous_sessions"]["measure"])
        self.assertEqual("human-context-switching",
                         conc["project_switches_per_active_hour"]["measure"])


if __name__ == "__main__":
    unittest.main()
