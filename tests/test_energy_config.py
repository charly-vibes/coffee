#!/usr/bin/env python3
"""
test_energy_config.py — coffe-7mj.1: configuración, fallback y schema de la
energía estimada (kWh) en el reporte (openspec change add-energy-estimates).

Cubre:
- config: bloque energy_coefficients requerido y validado fail-loud
  (fpa_config.validate_config), con tiers/default_tier/cache_read_energy_factor
  y versions por fecha effective.
- fpa_config.energy_for(): selección de versión por fecha (mismo contrato que
  rates_for, pero sin fallback silencioso — None si no hay cobertura).
- mes sin cobertura de versión → ValueError nombrando el mes (abort loud).
- fallback sin config → metadata energy_provenance "unavailable" con razón
  (FPA-008), sin campos mensuales.
- schema: el reporte con energía valida contra usage-report-v3 (aditivo) y
  el corrupto es rechazado.

La emisión mensual (energy_kwh_by_model, energy_kwh, banda) vive en
test_energy.py.
"""

import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
SCHEMA_PATH = REPO / "specs" / "usage-report-v3.schema.json"
CONFIG_PATH = REPO / "config" / "fpa.json"


def load_tracker(name="usage_tracker_energy_cfg"):
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


def aggregate_of(rows):
    """aggregate() con skills/sesiones inyectadas vacías (sin colección viva)."""
    return ut.aggregate(rows, sessions=[], skills_total={},
                        skills_by_project={}, commands={})


def mes(rep, m):
    return rep["monthly"][m]


CFG_VALIDA = fc.load_fpa_config(CONFIG_PATH)


def cfg_base():
    """Config mínimo con pricing/subs vacíos para probar solo energía."""
    return {
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


class TestConfigEnergia(unittest.TestCase):
    """Bloque energy_coefficients requerido y validado fail-loud."""

    def test_config_repo_valida(self):
        """El config del repo (con el bloque nuevo) valida limpio."""
        self.assertEqual(fc.validate_config(CFG_VALIDA), [])

    def test_config_sin_bloque_energia_aborta(self):
        cfg = fc.load_fpa_config(CONFIG_PATH)
        del cfg["energy_coefficients"]
        errors = fc.validate_config(cfg)
        self.assertTrue(any("energy_coefficients" in e for e in errors),
                        f"errors sin energy_coefficients: {errors}")

    def test_config_invalido_falla_nombrando_el_campo(self):
        campos = ("cache_read_energy_factor", "default_tier", "model_tiers",
                  "versions")
        for campo in campos:
            with self.subTest(campo=campo):
                cfg = cfg_base()
                if campo == "cache_read_energy_factor":
                    cfg["energy_coefficients"]["cache_read_energy_factor"] = -1
                elif campo == "default_tier":
                    cfg["energy_coefficients"]["default_tier"] = "ultra"
                elif campo == "model_tiers":
                    cfg["energy_coefficients"]["versions"][0]["model_tiers"] = {
                        "foo-model": "ultra"}
                else:
                    del cfg["energy_coefficients"]["versions"]
                errors = fc.validate_config(cfg)
                self.assertTrue(any(campo in e for e in errors),
                                f"errors sin {campo}: {errors}")


class TestSeleccionVersion(unittest.TestCase):
    """energy_for(): versión por primer día del bucket mensual."""

    def setUp(self):
        self.cfg = cfg_base()
        self.cfg["energy_coefficients"]["versions"].append(
            {"effective": "2026-07-01",
             "tiers": {"flash": 0.01, "mid": 0.05, "frontier": 0.1},
             "model_tiers": {}})

    def test_version_vigente_antes_de_segunda(self):
        v = fc.energy_for(self.cfg, date(2026, 6, 1))
        self.assertEqual(v["tiers"]["mid"], 0.1)

    def test_version_vigente_desde_effective(self):
        v = fc.energy_for(self.cfg, date(2026, 7, 1))
        self.assertEqual(v["tiers"]["mid"], 0.05)

    def test_sin_cobertura_devuelve_none(self):
        self.assertIsNone(fc.energy_for(self.cfg, date(2025, 11, 1)))


class TestAbortLoud(unittest.TestCase):
    """Mes sin cobertura de versión → ValueError nombrando el mes."""

    def test_mes_sin_cobertura(self):
        cfg = cfg_base()
        cfg["energy_coefficients"]["versions"][0]["effective"] = "2030-01-01"
        original = ut._FPA_CONFIG
        ut._FPA_CONFIG = cfg
        try:
            with self.assertRaises(ValueError) as ctx:
                aggregate_of([row()])
            self.assertIn("2026-05", str(ctx.exception))
        finally:
            ut._FPA_CONFIG = original


class TestFallbackSinConfig(unittest.TestCase):
    """Sin config (fallback): provenance unavailable + razón, sin campos mensuales."""

    def test_fallback_energy_unavailable(self):
        original = ut._FPA_CONFIG
        ut._FPA_CONFIG = None
        try:
            rep = aggregate_of([row()])
            self.assertEqual(rep["metadata"]["energy_provenance"],
                             "unavailable")
            self.assertTrue(rep["metadata"].get("energy_reason"))
            self.assertNotIn("energy_kwh_by_model", mes(rep, "2026-05"))
        finally:
            ut._FPA_CONFIG = original


class TestSchema(unittest.TestCase):
    """El schema extendido valida un reporte con energía (aditivo)."""

    def _jsonschema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema no instalado")
        return jsonschema

    def _validate(self, doc, js=None):
        js = js or self._jsonschema()
        js.validate(doc, json.loads(SCHEMA_PATH.read_text()))

    def test_reporte_con_energia_valida(self):
        rep = aggregate_of([row(), row({"tool": "amp", "model_raw": "amp",
                                        "input_tokens": 0, "output_tokens": 0,
                                        "cache_read_tokens": 0,
                                        "cache_write_tokens": 0})])
        self._validate(rep)
        # el reporte que acaba de validar lleva la estructura de energía
        # completa (campos mensuales + provenance assumed)
        m = mes(rep, "2026-05")
        self.assertIn("energy_kwh", m)
        self.assertIn("energy_kwh_by_model", m)
        self.assertIn("claude-sonnet-4-6", m["energy_kwh_by_model"])
        self.assertGreaterEqual(m["energy_kwh"], 0.0)
        self.assertEqual(rep["metadata"]["energy_provenance"], "assumed")

    def test_reporte_con_energia_invalida_rechazado(self):
        js = self._jsonschema()
        rep = aggregate_of([row()])
        mes(rep, "2026-05")["energy_kwh"] = -1.0  # minimum: 0 en el schema
        with self.assertRaises(js.ValidationError):
            self._validate(rep, js)

    def test_schema_tiene_campos_energy(self):
        s = json.loads(SCHEMA_PATH.read_text())
        mb = s["definitions"]["monthlyBucket"]["properties"]
        self.assertIn("energy_kwh", mb)
        self.assertIn("energy_kwh_by_model", mb)
        md = s["properties"]["metadata"]["properties"]
        self.assertIn("energy_provenance", md)
        self.assertIn("energy_kwh_band", mb)
        self.assertIn("energy_band_cache_factors", md)


if __name__ == "__main__":
    unittest.main()
