#!/usr/bin/env python3
"""
test_fpa_f2.py — tests de viz-fpa.py F2 (ticket coffe-lat.3)

Alcance: árboles expandibles Time/Tool con roll-up (FPA-020…025, 101).
Golden con fixture de meses parciales (FPA-102): REGEN_GOLDEN=1.

(coffe-kav.5: KPIs/DataNotes, Portfolio+vistas y render viven ahora en
test_fpa_f2_kpis.py, test_fpa_f2_portfolio.py y test_fpa_f2_render.py;
el fixture se compone de helpers _f2_* que devuelven estructuras
parciales.)
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f2-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f2", "scripts/viz-fpa.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _month(interactions=100, cost_eff=50.0, cost_cash=20.0, tools=None):
    tools = tools if tools is not None else {}
    return {
        "interactions": interactions, "input_tokens": 1000 * interactions,
        "output_tokens": 500 * interactions, "cache_read_tokens": 400 * interactions,
        "cache_write_tokens": 100 * interactions,
        "cost_effective": cost_eff, "cost_real": cost_cash,
        "tools": tools,
        "models": flat_models(tools),
        "subscription_fees": cost_cash,
    }


def flat_models(tools):
    """Union de los modelos de todas las tools de un mes (shape plano)."""
    out = {}
    for st in tools.values():
        for model, ms in st["models"].items():
            acc = out.setdefault(model, {"interactions": 0, "cost_effective": 0.0})
            acc["interactions"] += ms["interactions"]
            acc["cost_effective"] += ms["cost_effective"]
    return out


def _f2_tools():
    """Tools por mes del fixture F2: may, jun, jul (julio con codex sin
    coste — FPA-045)."""
    return {
        "2026-05": {
            "claude-cli": {"interactions": 60, "cost_effective": 40.0, "cost_real": 0.0,
                           "models": {"claude-sonnet-4-6": {"interactions": 40, "cost_effective": 30.0},
                                      "claude-opus-4.7": {"interactions": 20, "cost_effective": 10.0}}},
            "codex": {"interactions": 40, "cost_effective": 10.0, "cost_real": 10.0,
                      "models": {"gpt-5.4": {"interactions": 40, "cost_effective": 10.0}}},
        },
        "2026-06": {
            "claude-cli": {"interactions": 80, "cost_effective": 60.0, "cost_real": 0.0,
                           "models": {"claude-opus-4.7": {"interactions": 80, "cost_effective": 60.0}}},
            "codex": {"interactions": 20, "cost_effective": 5.0, "cost_real": 5.0,
                      "models": {"gpt-5.4": {"interactions": 20, "cost_effective": 5.0}}},
        },
        "2026-07": {
            "claude-cli": {"interactions": 30, "cost_effective": 30.0, "cost_real": 0.0,
                           "models": {"claude-sonnet-4-6": {"interactions": 30, "cost_effective": 30.0}}},
            "codex": {"interactions": 70, "cost_effective": 0.0, "cost_real": 0.0,
                      "models": {"gpt-5.4": {"interactions": 70, "cost_effective": 0.0}}},
        },
    }


def _f2_monthly(tools_by_month):
    """Monthly del fixture: tres meses con tokens_by_model (cache-hit rate
    FPA-043, ratio out/in FPA-044) y interaction_kinds por mes."""
    months = {}
    costes = {"2026-05": (100, 50.0, 20.0),
              "2026-06": (100, 65.0, 25.0),
              "2026-07": (100, 30.0, 0.0)}
    for ym, tools in tools_by_month.items():
        n, eff, cash = costes[ym]
        months[ym] = _month(n, eff, cash, tools=tools)
    # tokens_by_model por mes (cache-hit rate FPA-043, ratio out/in FPA-044)
    months["2026-05"]["tokens_by_model"] = {
        "claude-sonnet-4-6": {"in": 40000, "out": 20000, "cache_read": 32000, "cache_write": 8000},
        "claude-opus-4.7": {"in": 20000, "out": 10000, "cache_read": 8000, "cache_write": 4000},
    }
    months["2026-06"]["tokens_by_model"] = {
        "claude-opus-4.7": {"in": 80000, "out": 40000, "cache_read": 32000, "cache_write": 8000},
    }
    months["2026-07"]["tokens_by_model"] = {
        "claude-sonnet-4-6": {"in": 30000, "out": 15000, "cache_read": 24000, "cache_write": 6000},
    }
    months["2026-05"]["interaction_kinds"] = {"user_prompt": 20, "assistant_turn": 60, "tool_call": 20}
    months["2026-06"]["interaction_kinds"] = {"user_prompt": 30, "assistant_turn": 60, "tool_call": 10}
    months["2026-07"]["interaction_kinds"] = {"user_prompt": 25, "assistant_turn": 55, "tool_call": 20}
    return dict(sorted(months.items()))


def _f2_structure():
    """Estructuras estáticas del fixture F2: metadata, hourly, proyectos
    (monthly + models, consistentes — roll-up del drill, FPA-021),
    concurrencia, filtrado y sesiones."""
    return {
        "metadata": {
            "date_range": {"start": "2026-05-01", "end": "2026-07-10"},
            "filter": "in-scope", "total_interactions": 300,
            "total_days": 71, "total_hours": 4, "total_projects": 3,
            "cost_total_effective": 145.0, "cost_total_real": 45.0,
            "subscription_fees": 45.0,
            "timezone": "America/Argentina/Buenos_Aires",
            "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cache_read_tokens": 0, "total_cache_write_tokens": 0,
        },
        "hourly": {
            "2026-05-10 14:00": {"interactions": 60, "projects_active": 2,
                                 "tools": {"claude-cli": {"req": 60, "in": 60000, "out": 30000,
                                                          "cache_read": 40000, "cache_write": 8000,
                                                          "cost_eff": 40.0, "cost_real": 0.0}}},
            "2026-05-11 09:00": {"interactions": 40, "projects_active": 1,
                                 "tools": {"codex": {"req": 40, "in": 0, "out": 0,
                                                     "cache_read": 0, "cache_write": 0,
                                                     "cost_eff": 10.0, "cost_real": 10.0}}},
            "2026-07-02 10:00": {"interactions": 70, "projects_active": 1,
                                 "tools": {"codex": {"req": 70, "in": 0, "out": 0,
                                                     "cache_read": 0, "cache_write": 0,
                                                     "cost_eff": 0.0, "cost_real": 0.0}}},
        },
        "projects": {}, "skills": {}, "commands": {},
        "sessions": {"total_sessions": 8},
        "multitasking": {}, "project_daily": {},
        "project_monthly": {
            "charly-coffee": {"2026-05": {"interactions": 55, "cost_effective": 30.0},
                              "2026-06": {"interactions": 60, "cost_effective": 40.0},
                              "2026-07": {"interactions": 30, "cost_effective": 15.0}},
            "charly-atril": {"2026-05": {"interactions": 35, "cost_effective": 15.0},
                             "2026-06": {"interactions": 40, "cost_effective": 25.0},
                             "2026-07": {"interactions": 70, "cost_effective": 15.0}},
            "proyecto-externo": {"2026-05": {"interactions": 10, "cost_effective": 5.0}},
        },
        "project_models": {
            "charly-coffee": {"claude-sonnet-4-6": {"interactions": 70, "cost_effective": 60.0},
                              "claude-opus-4.7": {"interactions": 75, "cost_effective": 25.0}},
            "charly-atril": {"gpt-5.4": {"interactions": 145, "cost_effective": 55.0}},
            "proyecto-externo": {"gpt-5.4": {"interactions": 10, "cost_effective": 5.0}},
        },
        "concurrency": {
            "distinct_projects_per_hour": {"measure": "parallel-agent", "peak": 2, "avg": 1.5},
            "peak_simultaneous_sessions": {"measure": "parallel-agent", "peak": 2, "reason": None},
            "project_switches_per_active_hour": {"measure": "human-context-switching", "value": 1.0},
        },
        "filtered_out": {"interactions": 30, "cost_effective": 5.0,
                         "by_tool": {"codex": 30}, "share": 0.0909},
        "sessions_monthly": {"2026-05": {"total": 4, "with_agent": 1},
                             "2026-06": {"total": 4, "with_agent": 2}},
        "subscription_config": {}, "subscription_fees_by_month": {},
    }


def f2_fixture():
    """FPA-102: fixture con mes parcial (julio 10/31) y meses completos.

    Dos tools, 3 modelos (opus = premium), 3 proyectos (2 charly + 1
    unclassified). Julio con codex sin tokens (FPA-045). Compuesto a
    partir de helpers _f2_* (coffe-kav.5)."""
    fx = _f2_structure()
    fx["daily"] = {}
    fx["monthly"] = _f2_monthly(_f2_tools())
    return fx


def _raw_tree(fixture, cfg, name):
    if name == "time":
        return viz.build_time_tree(fixture, cfg)
    if name == "tool":
        return viz.build_tool_tree(fixture)
    return viz.build_portfolio_tree(fixture, cfg)


class F2Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


class TestToolTree(F2Base):
    """FPA-020/022/023: árbol Tool→Model con columnas completas."""

    def test_estructura_tool_model(self):
        tree = self.model["views"]["all"]["trees"]["tool"]
        labels = {c["label"] for c in tree["children"]}
        self.assertEqual({"claude-cli", "codex"}, labels)
        claude = next(c for c in tree["children"] if c["label"] == "claude-cli")
        self.assertEqual({"claude-opus-4.7", "claude-sonnet-4-6"},
                         {c["label"] for c in claude["children"]})

    def test_hijos_con_las_mismas_columnas(self):
        """FPA-022: hijos con las mismas columnas que el padre."""
        rows = self.model["views"]["all"]["trees"]["tool"]
        for child in rows["children"]:
            for field in ("cost_display", "pct_display", "interactions_display",
                          "per_1k_display", "delta_display"):
                self.assertIn(field, child)

    def test_rollup_tool_model(self):
        """FPA-025: tool = suma de sus modelos, root = suma de tools."""
        self.assertEqual([], viz.check_rollup(viz.build_tool_tree(self.fixture)))


class TestTimeTree(F2Base):
    """FPA-020/024: Time Year→Quarter→Month con presupuesto y varianza."""

    def test_estructura_anio_trimestre_mes(self):
        tree = self.model["views"]["all"]["trees"]["time"]
        self.assertEqual(1, len(tree["children"]))
        quarters = {c["label"] for c in tree["children"][0]["children"]}
        self.assertEqual({"Q2 26", "Q3 26"}, quarters)

    def test_budget_del_config_en_todos_los_meses(self):
        """FPA-024/050: presupuesto cash desde budgets.start_month (2026-04)."""
        tree = self.model["views"]["all"]["trees"]["time"]
        month_nodes = {c["key"]: c for y in tree["children"]
                       for q in y["children"] for c in q["children"]}
        self.assertIn("$100.00", month_nodes["time:2026-05"]["budget_display"])
        self.assertIn("$100.00", month_nodes["time:2026-06"]["budget_display"])

    def test_budget_pro_rata_mes_parcial(self):
        """coffe-9p4/FPA-052: julio parcial (10/31) recibe presupuesto
        pro-rateado $32.26, no $100 — misma fuente que build_budget."""
        tree = self.model["views"]["all"]["trees"]["time"]
        jul = [c for y in tree["children"] for q in y["children"]
               for c in q["children"] if c["key"] == "time:2026-07"][0]
        self.assertIn("$32.26", jul["budget_display"])
        # YTD del árbol = 100 + 100 + 32.26 (no $300)
        self.assertIn("$232.26", tree["budget_display"])

    def test_varianza_ventana_mixta_tramo_in_budget(self):
        """coffe-9p4: ventana que mezcla meses fuera del periodo presupuestado
        — la varianza compara solo el tramo in-budget (may cash $20 queda fuera)."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["budgets"]["start_month"] = "2026-06"
        model = viz.build_model(self.fixture, cfg)
        root = model["views"]["all"]["trees"]["time"]
        # budget = 100 (jun) + 32.26 (jul pro-rata); coste in-budget = 25 + 0
        self.assertIn("$132.26", root["budget_display"])
        self.assertEqual("-$107.26", root["variance_display"])
        self.assertEqual("-81.1%", root["variance_pct_display"])

    def test_varianza_cash_vs_presupuesto(self):
        """jun: cash 25.0 vs presupuesto 100 → varianza -75 (-75%)."""
        view = self.model["views"]["month:2026-06"]
        jun = [c for y in view["trees"]["time"]["children"]
               for q in y["children"] for c in q["children"]
               if c["key"] == "time:2026-06"][0]
        self.assertEqual("-$75.00", jun["variance_display"])
        self.assertEqual("-75.0%", jun["variance_pct_display"])

    def test_rollup_time(self):
        self.assertEqual([], viz.check_rollup(viz.build_time_tree(self.fixture, CONFIG)))


class TestGolden(unittest.TestCase):
    """FPA-101/102: golden de vistas+KPIs sobre el fixture con meses parciales."""

    def test_golden_f2(self):
        model = viz.build_model(f2_fixture(), CONFIG)
        model.pop("usage", None)  # F5 (coffe-lat.6): golden propio en test_fpa_f5
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(model, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f2.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, model)


if __name__ == "__main__":
    unittest.main()
