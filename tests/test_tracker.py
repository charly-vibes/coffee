#!/usr/bin/env python3
"""
test_tracker.py — tests de scripts/usage-tracker.py

Estrategia (std-lib only, como el repo):
- Funciones puras (estimate_cost, parse_ts, model_details, clean_proj_name,
  get_sub_cost, calc_subscription_fees, hour_key): aserciones directas.
- aggregate(): rows sintéticos; golden test contra snapshot JSON en
  tests/golden/. Regenerar con REGEN_GOLDEN=1 (revisar diff antes de aceptar).
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
GOLDEN = Path(__file__).resolve().parent / "golden" / "aggregate-snapshot.json"

REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def load_tracker():
    """Importa usage-tracker.py (guión: no importable como módulo normal)."""
    spec = importlib.util.spec_from_file_location("usage_tracker", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee", tool="claude-cli",
        model_raw="claude-sonnet-4-6", **kw):
    """Row sintético con el shape exacto que producen los extractores."""
    base = {
        "source": "test", "tool": tool, "model_raw": model_raw,
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": project, "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.01,
    }
    base.update(kw)
    return base


def synthetic_rows():
    """3 rows: 2 proyectos, 2 meses, 1 hora compartida (multitasking)."""
    return [
        row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee"),
        row(ts="2026-05-10T14:40:00+00:00", project="charly-atril", tool="codex",
            model_raw="gpt-5.4", model_family="codex", model_version="gpt-5.4"),
        row(ts="2026-06-01T09:00:00+00:00", project="charly-coffee"),
    ]


def synthetic_sessions():
    return [
        {"project": "charly-coffee", "first_ts": "2026-05-10", "duration_msgs": 10,
         "n_turns": 5, "n_tools": 2, "n_skills": 1, "n_errors": 0,
         "n_compactions": 0, "has_agent": False},
        {"project": "charly-atril", "first_ts": "2026-05-10", "duration_msgs": 300,
         "n_turns": 120, "n_tools": 4, "n_skills": 0, "n_errors": 1,
         "n_compactions": 1, "has_agent": True},
    ]


# ============================ funciones puras ============================

class TestPureFunctions(unittest.TestCase):

    def test_estimate_cost_sonnet_known_value(self):
        # 1M input + 100K output sonnet: 1e6*3e-6 + 1e5*1.5e-5 = 3 + 1.5
        self.assertEqual(ut.estimate_cost("claude", "sonnet-4.6", 1_000_000, 100_000), 4.5)

    def test_estimate_cost_cache_write_premium(self):
        # cache write cobra 1.25x input rate
        no_w = ut.estimate_cost("claude", "sonnet-4.6", 1_000_000, 0, 0, 0)
        with_w = ut.estimate_cost("claude", "sonnet-4.6", 1_000_000, 0, 0, 1000)
        self.assertAlmostEqual(with_w - no_w, 1000 * 1.25 * 0.000003, places=9)

    def test_estimate_cost_unknown_model_falls_back(self):
        # DEFAULT_RATES en vez de KeyError
        self.assertGreater(ut.estimate_cost("claude", "modelo-nuevo", 1000, 1000), 0)

    def test_estimate_cost_unknown_family(self):
        self.assertEqual(ut.estimate_cost("no-existe", "x", 0, 0), 0.0)

    def test_parse_ts_variants(self):
        self.assertEqual(ut.parse_ts("2026-05-10T14:23:00Z").year, 2026)
        self.assertEqual(ut.parse_ts(1700000000000).year, 2023)
        self.assertIsNone(ut.parse_ts(None))
        self.assertIsNone(ut.parse_ts("garbage"))

    def test_model_details_mapping(self):
        self.assertEqual(ut.model_details("claude-opus-4-7"), ("claude", "opus-4.7"))
        self.assertEqual(ut.model_details("gpt-5.4-2026"), ("codex", "gpt-5.4"))
        self.assertEqual(ut.model_details("google/gemini-3-pro"), ("gemini", "gemini-3-pro"))
        self.assertEqual(ut.model_details("deepseek-v4"), ("deepseek", "v4-flash"))
        self.assertEqual(ut.model_details("algo-desconocido")[0], "other")

    def test_clean_proj_name_strips_path_prefix(self):
        # logs históricos (path pre-rename) derivan el label viejo y se
        # canonicalizan al nombre corregido (bd coffe-85z)
        self.assertEqual(
            ut.clean_proj_name("-var-home-sasha-para-areas-dev-gh-charly-coffee"),
            "charly-coffee")

    def test_clean_proj_name_strips_renamed_dir(self):
        # dir local post-rename y label histórico convergen al mismo label
        self.assertEqual(
            ut.clean_proj_name("-var-home-sasha-para-areas-dev-gh-charly-coffee"),
            "charly-coffee")

    def test_clean_proj_name_aliases(self):
        self.assertEqual(ut.clean_proj_name("charly-mibilioteca"), "charly-miblioteca")
        self.assertEqual(ut.clean_proj_name("sk-sxAct"), "sk-XAct-jl")
        self.assertEqual(ut.clean_proj_name("charly-coffee"), "charly-coffee")

    def test_clean_proj_name_julia_repos(self):
        self.assertEqual(ut.clean_proj_name("-sk-REPLy.jl"), "sk-REPLy-jl")

    def test_clean_proj_name_dots_vs_dashes(self):
        """coffe-vp8: Claude mungea '.' del path a '-', Pi no — alias a un label."""
        self.assertEqual(ut.clean_proj_name("ak-akielbowicz-github-io"),
                         "ak-akielbowicz.github.io")
        self.assertEqual(ut.clean_proj_name("ak-akielbowicz.github.io"),
                         "ak-akielbowicz.github.io")

    def test_clean_proj_name_amp_org_root_file(self):
        """coffe-vp8: uri de Amp a archivo en raíz del org (gh/ak/justfile)
        derivaba un repo fantasma 'ak-justfile' — colapsa a la org."""
        self.assertEqual(ut.clean_proj_name("ak-justfile"), "ak")

    def test_is_charly(self):
        # coffe-vp8: el scope incluye repos ak (akielbowicz) además de charly/sk
        self.assertTrue(ut.in_scope("charly-coffee"))
        self.assertTrue(ut.in_scope("sk-XAct-jl"))
        self.assertTrue(ut.in_scope("ak-journal"))
        self.assertTrue(ut.in_scope("ak-100DiasEnMeli"))
        self.assertTrue(ut.in_scope("ak"))  # bare
        self.assertTrue(ut.in_scope("sk"))  # bare
        self.assertFalse(ut.in_scope("otro-proyecto"))
        self.assertFalse(ut.in_scope("akelarre"))  # prefijo ak- exacto, no substring

    def test_in_scope_path_forms(self):
        """Claude mungea paths: -var-home-sasha-para-areas-dev-gh-<org>-<repo>.
        Regression coffe-vp8: startswith puro dejaba fuera skills/commands."""
        self.assertTrue(ut.in_scope("-var-home-sasha-para-areas-dev-gh-ak-journal"))
        self.assertTrue(ut.in_scope("-var-home-sasha-para-areas-dev-gh-sk-poco"))
        self.assertTrue(ut.in_scope("-var-home-sasha-para-areas-dev-gh-charly-tv"))
        self.assertTrue(ut.in_scope(
            "file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py"))
        self.assertTrue(ut.in_scope("file:///para/areas/dev/gh/ak/journal/x.py"))
        self.assertFalse(ut.in_scope("-var-home-otro-akelarre"))
        self.assertFalse(ut.in_scope("file:///tmp/akelarre/x.py"))
        # segmento completo, no prefijo: -gh-akaria no es org ak
        self.assertFalse(ut.in_scope("-var-home-sasha-para-areas-dev-gh-akaria-x"))

    def test_get_sub_cost_subscription_period(self):
        # mayo 2026 = Max $100 → real cost 0
        real, eff, label = ut.get_sub_cost("claude-cli", "2026-05-10T14:23:00+00:00", 0.01)
        self.assertEqual((real, label), (0.0, "Max $100/mes"))

    def test_get_sub_cost_pay_per_token_after_period(self):
        real, eff, label = ut.get_sub_cost("codex", "2026-06-10T14:23:00+00:00", 0.01)
        self.assertEqual(real, 0.01)  # post 2026-05-15: pay-per-token

    def test_get_sub_cost_non_subscribed_tool(self):
        real, eff, label = ut.get_sub_cost("amp", "2026-05-10", 0.0)
        self.assertEqual(label, "pay-per-token")

    def test_calc_subscription_fees_present_for_active_months(self):
        monthly = {"2026-04": {"tools": ["claude-cli", "codex"]},
                   "2026-05": {"tools": ["claude-cli"]},
                   "2026-06": {"tools": {"claude-cli": 5}}}  # shape dict como el real
        fees = ut.calc_subscription_fees(monthly)
        # calendario corregido a facturas (FPA-082/reales): abril = Max $100
        # + Plus $20; mayo = Pro $20 (el mes arranca en Pro tras el 19);
        # junio = $0 — sin factura jun (claude cancelado, codex Free
        # después del 02). El mes cuenta la fee del plan activo al inicio.
        self.assertEqual({"2026-04": 120.0, "2026-05": 20.0}, fees)

    def test_hour_key_format(self):
        dt = datetime(2026, 5, 10, 23, 30, tzinfo=timezone.utc)
        self.assertRegex(ut.hour_key(dt), r"^\d{4}-\d{2}-\d{2} \d{2}:00$")


# ============================ config (coffe-mbz) ============================

class TestTrackerConfig(unittest.TestCase):
    """coffe-mbz: SUBSCRIPTIONS/MODEL_PRICING viven en config/fpa.json (F0);
    el tracker las carga de ahí. Fallback a constantes hardcodeadas solo si
    no hay config (backwards compat, con warning)."""

    REAL_CONFIG = REPO / "config" / "fpa.json"

    def _reload_con_config(self, cfg, name="tracker_cfg_test"):
        """Reimporta el tracker con TRACKER_CONFIG apuntando a un config dado."""
        tmpdir = tempfile.mkdtemp()
        path = Path(tmpdir) / "fpa.json"
        path.write_text(json.dumps(cfg))
        old = os.environ.get("TRACKER_CONFIG")
        os.environ["TRACKER_CONFIG"] = str(path)
        try:
            spec = importlib.util.spec_from_file_location(name, TRACKER_PATH)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
        finally:
            if old is None:
                os.environ.pop("TRACKER_CONFIG", None)
            else:
                os.environ["TRACKER_CONFIG"] = old
        return m

    def test_subs_desde_config_real(self):
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        self.assertEqual(ut.SUBSCRIPTIONS, cfg["subscriptions"])

    def test_default_rates_desde_config_real(self):
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        self.assertEqual(ut.DEFAULT_RATES, cfg["model_pricing"]["default_rates"])
        self.assertEqual(ut._CACHE_WRITE_FACTOR,
                         cfg["model_pricing"]["cache_write_factor"])

    def test_config_source_en_modulo(self):
        self.assertIsNotNone(ut.CONFIG_SOURCE)
        self.assertIn("fpa.json", ut.CONFIG_SOURCE)

    def test_lee_suscripciones_de_config_dado(self):
        """El tracker usa el config apuntado, no las constantes: con otro
        config, get_sub_cost responde según ese config."""
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        cfg["subscriptions"] = {
            "otro-cli": [{"start": "2026-01-01", "end": None,
                          "label": "X", "monthly_fee": 5}]}
        m = self._reload_con_config(cfg)
        self.assertEqual(m.SUBSCRIPTIONS, cfg["subscriptions"])
        real, _, label = m.get_sub_cost("otro-cli", "2026-02-01T00:00:00+00:00", 1.0)
        self.assertEqual((real, label), (0.0, "X"))

    def test_pricing_versionado_por_fecha(self):
        """estimate_cost usa la versión de pricing vigente en la fecha de la
        interacción (FPA-016), no una tabla plana."""
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        cfg["model_pricing"]["versions"] = [
            {"effective": "2026-01-01", "rates": {
                "claude": {"sonnet-4.6": {"input": 0.000001,
                                          "output": 0.000002,
                                          "cache_read": 0.0000001}}}},
            {"effective": "2026-06-01", "rates": {
                "claude": {"sonnet-4.6": {"input": 0.000002,
                                          "output": 0.000004,
                                          "cache_read": 0.0000002}}}},
        ]
        m = self._reload_con_config(cfg)
        antes = m.estimate_cost("claude", "sonnet-4.6", 1_000_000, 0,
                                when=date(2026, 5, 1))
        despues = m.estimate_cost("claude", "sonnet-4.6", 1_000_000, 0,
                                  when=date(2026, 7, 1))
        self.assertAlmostEqual(antes, 1.0, places=8)
        self.assertAlmostEqual(despues, 2.0, places=8)

    def test_antes_de_primera_version_usa_default(self):
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        cfg["model_pricing"]["versions"] = [
            {"effective": "2026-06-01", "rates": {
                "claude": {"sonnet-4.6": {"input": 0.000002,
                                          "output": 0.000004,
                                          "cache_read": 0.0000002}}}},
        ]
        m = self._reload_con_config(cfg)
        cost = m.estimate_cost("claude", "sonnet-4.6", 1_000_000, 0,
                               when=date(2026, 1, 1))
        self.assertAlmostEqual(
            cost, 1_000_000 * cfg["model_pricing"]["default_rates"]["input"],
            places=8)

    def test_fallback_sin_config(self):
        """Sin config disponible (CWD y repo sin config/): constantes
        hardcodeadas + warning. Explicit --config/TRACKER_CONFIG inexistente
        falla loud (FileNotFoundError), no fallback silencioso."""
        old_cwd = os.getcwd()
        old_root, old_subs, old_cfg = ut._REPO_ROOT, ut.SUBSCRIPTIONS, ut._FPA_CONFIG
        os.chdir(tempfile.mkdtemp())
        err = io.StringIO()
        try:
            ut._REPO_ROOT = Path(tempfile.mkdtemp())  # sin config/fpa.json
            with contextlib.redirect_stderr(err):
                cfg = ut.load_config()
            self.assertIsNone(cfg)
            self.assertEqual(ut.SUBSCRIPTIONS, ut._FALLBACK_SUBSCRIPTIONS)
            self.assertIsNone(ut._FPA_CONFIG)
            self.assertIn("WARNING", err.getvalue())
            # explicit path inexistente → error, no fallback
            with self.assertRaises(FileNotFoundError):
                ut.load_config("/no/existe/fpa.json")
        finally:
            os.chdir(old_cwd)
            ut._REPO_ROOT = old_root
            ut.load_config()  # restaura globals con el config real
            self.assertEqual(ut._FPA_CONFIG, old_cfg)

    def test_config_invalido_falla_loud(self):
        """Config con errores de validación → error claro, no fallback silencioso."""
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        cfg.pop("subscriptions", None)
        with self.assertRaises(ValueError):
            self._reload_con_config(cfg)

    def test_model_pricing_config_en_reporte(self):
        """El reporte refleja el config cargado, no constantes muertas."""
        cfg = ut.fpa_config.load_fpa_config(self.REAL_CONFIG)
        mpc = self.report["model_pricing_config"]
        self.assertEqual(mpc["default_rates"], cfg["model_pricing"]["default_rates"])
        self.assertEqual(mpc["cache_write_factor"],
                         cfg["model_pricing"]["cache_write_factor"])
        self.assertEqual(mpc["versions"], cfg["model_pricing"]["versions"])

    def test_metadata_config_source(self):
        self.assertIn("fpa.json", self.report["metadata"]["config_source"])

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(synthetic_rows(), synthetic_sessions())


# ============================ aggregate ============================

class TestAggregate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(synthetic_rows(), synthetic_sessions())

    def test_metadata_totals(self):
        m = self.report["metadata"]
        self.assertEqual(m["total_interactions"], 3)
        self.assertEqual(m["total_projects"], 2)
        self.assertEqual(m["total_hours"], 2)
        self.assertEqual(m["total_days"], 2)
        self.assertEqual(m["total_months"], 2)

    def test_monthly(self):
        self.assertIn("2026-05", self.report["monthly"])
        self.assertIn("2026-06", self.report["monthly"])
        self.assertEqual(self.report["monthly"]["2026-05"]["interactions"], 2)
        self.assertEqual(self.report["monthly"]["2026-06"]["interactions"], 1)

    def test_projects_sorted_by_cost_desc(self):
        costs = [v["cost_effective"] for v in self.report["projects"].values()]
        self.assertEqual(costs, sorted(costs, reverse=True))

    def test_sessions_length_distribution(self):
        ld = self.report["sessions"]["length_distribution"]
        self.assertEqual(ld.get("1-10"), 1)
        self.assertEqual(ld.get("101-300"), 1)
        self.assertEqual(self.report["sessions"]["with_agent"], 1)
        self.assertEqual(self.report["sessions"]["total_api_errors"], 1)

    def test_multitasking_detects_shared_hour(self):
        mt = self.report["multitasking"]["hourly"]
        self.assertEqual(mt["total_active_hours"], 2)
        self.assertEqual(mt["hours_with_multiple_projects"], 1)
        self.assertEqual(mt["pct_hours_multitasking"], 50.0)

    def test_context_switches(self):
        # semántica real: cambio de proyecto entre requests consecutivos,
        # aunque crucen la medianoche (coffe→atril=1, atril→coffe=1)
        cs = self.report["multitasking"]["context_switches"]
        self.assertEqual(cs["total"], 2)
        self.assertEqual(cs["max_in_one_day"], 1)

    def test_project_daily_dense_matrix(self):
        pd_ = self.report["project_daily"]
        self.assertEqual(set(pd_["matrix"].keys()), {"charly-coffee", "charly-atril"})
        for p, series in pd_["matrix"].items():
            self.assertEqual(len(series), len(pd_["days"]))  # densa, sin huecos
        # días continuos: 2026-05-10 → 2026-06-01 = 23 días
        self.assertEqual(len(pd_["days"]), 23)

    def test_no_dead_keys(self):
        self.assertNotIn("tools_summary", self.report)


class TestProjectMonthlyTokens(unittest.TestCase):
    """add-project-cost-token-timeline (d1/t1.2): project_monthly acumula
    tokens (in/out/cache_read) y cost_real por proyecto×mes, para la timeline
    de volumen y coste por proyecto del dashboard (d2)."""

    @classmethod
    def setUpClass(cls):
        rows = [
            row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee",
                tool="gemini-cli", model_raw="gemini-3-pro",
                model_family="gemini", model_version="gemini-3-pro",
                input_tokens=1000, output_tokens=500,
                cache_read_tokens=200, cost_effective=0.01),
            row(ts="2026-05-11T09:00:00+00:00", project="charly-coffee",
                tool="gemini-cli", model_raw="gemini-3-pro",
                model_family="gemini", model_version="gemini-3-pro",
                input_tokens=0, output_tokens=0, cache_read_tokens=0,
                cost_effective=0.0),
            row(ts="2026-06-01T09:00:00+00:00", project="charly-coffee",
                tool="gemini-cli", model_raw="gemini-3-pro",
                model_family="gemini", model_version="gemini-3-pro",
                input_tokens=100, output_tokens=50, cache_read_tokens=0,
                cost_effective=0.005),
        ]
        rep = ut.aggregate(rows, synthetic_sessions())
        cls.pm = rep["project_monthly"]["charly-coffee"]

    def test_tokens_acumulan_por_proyecto_mes(self):
        # gemini-cli no está suscripto en el calendario sintético → real = effective
        may = self.pm["2026-05"]
        self.assertEqual(1000, may["tokens"]["in"])
        self.assertEqual(500, may["tokens"]["out"])
        self.assertEqual(200, may["tokens"]["cache_read"])

    def test_project_multiple_months(self):
        self.assertEqual(set(self.pm.keys()), {"2026-05", "2026-06"})
        self.assertEqual(100, self.pm["2026-06"]["tokens"]["in"])

    def test_mes_coste_cero_con_tokens(self):
        # mes con coste 0.0 pero tokens > 0: el volumen timeline no depende del coste
        # (aquí la row 2026-06 tiene coste pero 0 tokens: usa la row de 2026-05 con 0)
        # para la aserción dedicada usamos el mes donde el coste es 0 y tokens no:
        # tomamos la row 2 (2026-05, 0 tokens) vs mes completo: el caso limpio es
        # un mes entero sin coste — sintetizamos dentro del test.
        rows = [row(ts="2026-07-01T09:00:00+00:00", project="charly-coffee",
                    tool="gemini-cli", model_raw="gemini-3-pro",
                    model_family="gemini", model_version="gemini-3-pro",
                    input_tokens=42, output_tokens=0, cache_read_tokens=0,
                    cost_effective=0.0)]
        pm = ut.aggregate(rows, synthetic_sessions())["project_monthly"]
        july = pm["charly-coffee"]["2026-07"]
        self.assertEqual(0.0, july["cost_effective"])
        self.assertEqual(42, july["tokens"]["in"])


# ============================ golden ============================

class TestPiCostEstimate(unittest.TestCase):
    """coffe-8t8: logs Pi sin cost (gemini-cli) → estimación pay-per-token
    con estimate_cost (rates del config versionados por fecha)."""

    def _extract(self, usage):
        entry = {"type": "message", "timestamp": "2026-05-10T14:23:00Z",
                 "provider": "google-gemini-cli",
                 "message": {"role": "assistant", "model": "gemini-3-pro",
                             "usage": usage}}
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "charly-coffee"
            d.mkdir()
            (d / "s1.jsonl").write_text(json.dumps(entry) + "\n")
            rows, _ = ut.extract_pi(sessions_dir=Path(tmp))
        return rows

    def test_gemini_sin_cost_se_estima(self):
        rows = self._extract({"input_tokens": 1000, "output_tokens": 500,
                              "cacheRead": 100, "cacheWrite": 0, "cost": {}})
        self.assertEqual(1, len(rows))
        ts = date(2026, 5, 10)
        expected = ut.estimate_cost("gemini", "gemini-3-pro", 1000, 500,
                                    100, 0, when=ts)
        self.assertEqual(expected, rows[0]["cost_effective"])
        self.assertGreater(rows[0]["cost_effective"], 0)

    def test_cost_presente_pasa_directo(self):
        rows = self._extract({"input_tokens": 1000, "output_tokens": 500,
                              "cost": {"total": 0.42}})
        self.assertEqual(0.42, rows[0]["cost_effective"])

    def test_cero_coste_y_cero_tokens_queda_cero(self):
        rows = self._extract({"input_tokens": 0, "output_tokens": 0,
                              "cost": {}})
        self.assertEqual(0.0, rows[0]["cost_effective"])


class TestGolden(unittest.TestCase):

    def test_aggregate_golden(self):
        report = ut.aggregate(synthetic_rows(), synthetic_sessions())
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
            self.skipTest("golden regenerado")
        self.assertTrue(GOLDEN.exists(),
                        "Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_tracker.py")
        expected = json.loads(GOLDEN.read_text())
        actual = json.loads(json.dumps(report, default=str))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
