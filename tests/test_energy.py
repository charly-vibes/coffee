#!/usr/bin/env python3
"""
test_energy.py — coffe-7mj.1: energía estimada (kWh) por modelo en el
reporte (openspec change add-energy-estimates).

Cubre:
- emisión aditiva del tracker: monthly[].energy_kwh_by_model
  ({"kwh": <3 dec>, "tier": <str>} | {"kwh": null, "reason": ...}) y
  monthly[].energy_kwh (suma de modelos con datos), metadata.energy_*.
- pesos: cache_read ponderado por cache_read_energy_factor; cache_write a peso
  completo (prefill); energía jamás mezclada con costes USD (FPA-002).
- banda de sensibilidad: monthly[].energy_kwh_band {low, high} (coffe-5ng).

La validación/abort del bloque energy_coefficients, fallback sin config y
schema viven en test_energy_config.py.

Los coeficientes son estimaciones de laboratorio (Luccioni et al. / AI Energy
Score) — la energía es SIEMPRE provenance "assumed", jamás "reported".
"""

import contextlib
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker(name="usage_tracker_energy"):
    spec = importlib.util.spec_from_file_location(name, TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()
fc = ut.fpa_config


ROW_DEFAULTS = {
    "source": "test", "tool": "claude-cli",
    "model_raw": "claude-sonnet-4-6",
    "model_family": "claude", "model_version": "sonnet-4.6",
    "project": "charly-coffee", "ts": "2026-05-10T14:23:00+00:00",
    "input_tokens": 1_000_000, "output_tokens": 500_000,
    "cache_read_tokens": 10_000_000, "cache_write_tokens": 0,
    "cost_effective": 0.01,
}


def row(overrides=None):
    """Row sintético con el shape exacto que producen los extractores."""
    o = dict(ROW_DEFAULTS)
    o.update(overrides or {})
    ts = o.pop("ts")
    return {
        **o,
        "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
    }


AMP_ROW = {"tool": "amp", "model_raw": "amp", "input_tokens": 0,
           "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


def aggregate_of(rows):
    """aggregate() con skills/sesiones inyectadas vacías (sin colección viva)."""
    return ut.aggregate(rows, sessions=[], skills_total={},
                        skills_by_project={}, commands={})


def mes(rep, m):
    return rep["monthly"][m]


@contextlib.contextmanager
def _fpa_cfg(cfg):
    """Switchea ut._FPA_CONFIG (inyección de config sin colección viva)."""
    original = ut._FPA_CONFIG
    ut._FPA_CONFIG = cfg
    try:
        yield
    finally:
        ut._FPA_CONFIG = original


def cfg_dos_versiones():
    """cfg_base() + una segunda versión effective 2026-06-01 (mid 0.05)."""
    cfg = {
        "taxonomy": {"rules": [], "overrides": {}, "fallback": "Unclassified"},
        "subscriptions": {},
        "model_pricing": {"default_rates": {"input": 1e-6, "output": 1e-5,
                                            "cache_read": 1e-7}},
        "energy_coefficients": {
            "cache_read_energy_factor": 0.1,
            "default_tier": "mid",
            "versions": [{"effective": "2025-12-23",
                          "tiers": {"flash": 0.03, "mid": 0.1,
                                    "frontier": 0.2},
                          "model_tiers": {}}],
        },
    }
    cfg["energy_coefficients"]["versions"].append(
        {"effective": "2026-06-01",
         "tiers": {"flash": 0.01, "mid": 0.05, "frontier": 0.1},
         "model_tiers": {}})
    return cfg


class TestEmisionEnergia(unittest.TestCase):
    """monthly[].energy_kwh_by_model / energy_kwh + metadata energy_*."""

    @classmethod
    def setUpClass(cls):
        cls.rep = aggregate_of([
            row(),
            row({"ts": "2026-05-11T09:00:00+00:00"}),
            row({"model_raw": "deepseek/deepseek-v4-flash"}),
        ])

    def test_energy_kwh_by_model_presente(self):
        e = mes(self.rep, "2026-05")["energy_kwh_by_model"]
        self.assertIn("claude-sonnet-4-6", e)
        self.assertIn("deepseek/deepseek-v4-flash", e)

    def test_kwh_con_tier_y_3_decimales(self):
        e = mes(self.rep, "2026-05")["energy_kwh_by_model"]["claude-sonnet-4-6"]
        self.assertGreater(e["kwh"], 0)
        self.assertEqual(e["tier"], "mid")
        self.assertEqual(e["kwh"], round(e["kwh"], 3))

    def test_ponderacion_cache_read(self):
        """(in + out + cache_write + cache_read×0.10) × J/tier / 3.6e6."""
        rep = aggregate_of([row()])
        e = mes(rep, "2026-05")["energy_kwh_by_model"]["claude-sonnet-4-6"]
        esperado = (1_000_000 + 500_000 + 0 + 10_000_000 * 0.1) * 0.1 / 3.6e6
        self.assertAlmostEqual(e["kwh"], round(esperado, 3), places=3)

    def test_dos_rows_mismo_modelo_agregan_antes_de_energia(self):
        e = mes(self.rep, "2026-05")["energy_kwh_by_model"]["claude-sonnet-4-6"]
        esperado = (2_000_000 + 1_000_000 + 20_000_000 * 0.1) * 0.1 / 3.6e6
        self.assertAlmostEqual(e["kwh"], round(esperado, 3), places=3)

    def test_tier_flash_para_deepseek(self):
        e = mes(self.rep, "2026-05")["energy_kwh_by_model"][
            "deepseek/deepseek-v4-flash"]
        self.assertEqual(e["tier"], "flash")
        self.assertGreater(e["kwh"], 0)

    def test_modelo_sin_mapping_usa_default_tier(self):
        rep = aggregate_of([row({"model_raw": "modelo-futuro-sin-mapping"})])
        e = mes(rep, "2026-05")["energy_kwh_by_model"]["modelo-futuro-sin-mapping"]
        self.assertEqual(e["tier"], "mid")

    def test_modelo_sin_telemetria_null_con_razon(self):
        rep = aggregate_of([row(AMP_ROW)])
        e = mes(rep, "2026-05")["energy_kwh_by_model"]["amp"]
        self.assertIsNone(e["kwh"])
        self.assertEqual(e["reason"], "sin telemetría de tokens")

    def test_total_mensual_suma_solo_modelos_con_datos(self):
        m = mes(self.rep, "2026-05")
        e = m["energy_kwh_by_model"]
        suma = round(sum(v["kwh"] for v in e.values()
                         if isinstance(v, dict) and v["kwh"] is not None), 3)
        self.assertAlmostEqual(m["energy_kwh"], suma, places=3)

    def test_metadata_energy(self):
        md = self.rep["metadata"]
        self.assertEqual(md["energy_provenance"], "assumed")
        self.assertEqual(md["energy_cache_read_factor"], 0.1)
        self.assertTrue(md["energy_config_source"])
        self.assertIn("J/token", md["energy_method"])

    def test_energia_ya_no_vive_en_campos_de_coste(self):
        """Aditivo: las claves de coste existentes no cambian de semántica."""
        m = mes(self.rep, "2026-05")
        for clave in ("cost_effective", "cost_real", "cost_real_tracker",
                      "subscription_fees"):
            self.assertIn(clave, m)
        self.assertNotIn("kwh", m["cost_effective"] if isinstance(
            m["cost_effective"], dict) else {})

    def test_meses_multiples_usan_su_version(self):
        """Un mes posterior a una segunda effective usa el coeficiente nuevo."""
        with _fpa_cfg(cfg_dos_versiones()):
            rep = aggregate_of([row(),
                                row({"ts": "2026-06-10T14:23:00+00:00"})])
        j_v1 = 0.1   # tiers de cfg_base
        j_v2 = 0.05  # tiers de la segunda versión
        tokens = 1_000_000 + 500_000 + 10_000_000 * 0.1
        e_may = mes(rep, "2026-05")["energy_kwh_by_model"]["claude-sonnet-4-6"]
        e_jun = mes(rep, "2026-06")["energy_kwh_by_model"]["claude-sonnet-4-6"]
        self.assertAlmostEqual(e_may["kwh"], round(tokens * j_v1 / 3.6e6, 3))
        self.assertAlmostEqual(e_jun["kwh"], round(tokens * j_v2 / 3.6e6, 3))


class TestBandaSensibilidad(unittest.TestCase):
    """coffe-5ng: monthly[].energy_kwh_band {low, high} — cache factor 0.0/1.0.

    Mismos buckets, coeficientes versionados y energy_version que la emisión
    nominal de 7mj.1; modelos sin telemetría excluidos de ambos extremos.
    """

    @classmethod
    def setUpClass(cls):
        cls.rep = aggregate_of([
            row(),
            row({"model_raw": "deepseek/deepseek-v4-flash"}),
            row(AMP_ROW),
        ])

    def test_banda_presente_con_low_y_high(self):
        b = mes(self.rep, "2026-05")["energy_kwh_band"]
        self.assertIn("low", b)
        self.assertIn("high", b)
        self.assertEqual(b["low"], round(b["low"], 3))
        self.assertEqual(b["high"], round(b["high"], 3))

    def test_extremos_exactos_sonnet(self):
        """low = fresh × J / 3.6e6; high = (fresh + cache_read) × J / 3.6e6."""
        rep = aggregate_of([row()])
        b = mes(rep, "2026-05")["energy_kwh_band"]
        esperado_low = round(1_500_000 * 0.1 / 3.6e6, 3)     # mid 0.1 J/tok
        esperado_high = round(11_500_000 * 0.1 / 3.6e6, 3)
        self.assertAlmostEqual(b["low"], esperado_low, places=3)
        self.assertAlmostEqual(b["high"], esperado_high, places=3)

    def test_identidad_low_nominal_high(self):
        m = mes(self.rep, "2026-05")
        b = m["energy_kwh_band"]
        self.assertLessEqual(b["low"], m["energy_kwh"])
        self.assertLessEqual(m["energy_kwh"], b["high"])

    def test_modelo_sin_telemetria_excluido_de_ambos_extremos(self):
        """amp (null) no suma a la banda, igual que no suma al nominal."""
        rep = aggregate_of([row(), row(AMP_ROW)])
        b = mes(rep, "2026-05")["energy_kwh_band"]
        esperado_low = round(1_500_000 * 0.1 / 3.6e6, 3)
        esperado_high = round(11_500_000 * 0.1 / 3.6e6, 3)
        self.assertAlmostEqual(b["low"], esperado_low, places=3)
        self.assertAlmostEqual(b["high"], esperado_high, places=3)
        self.assertAlmostEqual(mes(rep, "2026-05")["energy_kwh"],
                               round(2_500_000 * 0.1 / 3.6e6, 3), places=3)

    def test_mes_solo_sin_telemetria_banda_cero(self):
        """Mes amp-only: banda 0.0/0.0, igual que su energy_kwh 0.0."""
        rep = aggregate_of([row(AMP_ROW)])
        m = mes(rep, "2026-05")
        self.assertEqual(m["energy_kwh"], 0.0)
        self.assertEqual(m["energy_kwh_band"], {"low": 0.0, "high": 0.0})

    def test_banda_usa_la_version_del_bucket(self):
        """Mes posterior a una segunda effective usa el coeficiente nuevo."""
        with _fpa_cfg(cfg_dos_versiones()):
            rep = aggregate_of([row({"ts": "2026-06-10T14:23:00+00:00"})])
        b = mes(rep, "2026-06")["energy_kwh_band"]
        self.assertAlmostEqual(b["low"], round(1_500_000 * 0.05 / 3.6e6, 3))
        self.assertAlmostEqual(b["high"], round(11_500_000 * 0.05 / 3.6e6, 3))

    def test_metadata_band_cache_factors(self):
        md = self.rep["metadata"]
        self.assertEqual(md["energy_band_cache_factors"], [0.0, 1.0])

    def test_fallback_sin_config_sin_banda(self):
        with _fpa_cfg(None):
            rep = aggregate_of([row()])
            self.assertNotIn("energy_kwh_band", mes(rep, "2026-05"))
            self.assertNotIn("energy_band_cache_factors", rep["metadata"])


if __name__ == "__main__":
    unittest.main()
