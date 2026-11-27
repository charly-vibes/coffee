#!/usr/bin/env python3
"""Tests de ensamblado end-to-end de scripts/ddl-effect-tracker.py (coffe-q9z).

assemble_report() con insumos sintéticos (sin I/O). La extracción que
alimenta los insumos vive en test_ddl_extract.py; los reducers puros en
test_ddl_effect*.py.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)


class AssembleReportTest(unittest.TestCase):
    """Ensamblado end-to-end con insumos sintéticos (sin I/O)."""

    def _insumos(self):
        git_coffee = [
            {"repo": "coffee", "date": "2026-03-05T10:00:00Z",
             "message": "ddl init", "files": [".ddl/config.json"]},
            {"repo": "coffee", "date": "2026-03-10T10:00:00Z",
             "message": "fix: bug", "files": ["src/x.py"]},
            {"repo": "coffee", "date": "2026-03-11T10:00:00Z",
             "message": "test: caso", "files": ["tests/test_x.py"]},
        ]
        sessions = [
            # formato de walk_pi_sessions: events con ts, uses de gestionados
            {"repo": "coffee", "uses": {"wai": "2026-03-05T09:00:00Z"},
             "events": [("assistant", None, None, "2026-03-20T10:00:00Z"),
                        ("result", "bash", False, "2026-03-20T10:00:01Z"),
                        ("result", "bash", True, "2026-03-20T10:00:02Z")]},
            {"repo": "dont", "uses": {}, "events": []},
        ]
        return {
            "usage_report": {
                "project_monthly": {
                    "charly-coffee": {"2026-03": {"interactions": 100}},
                    "charly-dont": {"2026-03": {"interactions": 50}},
                }},
            "sessions": sessions,
            "git": {"coffee": {"commits": git_coffee, "releases": [],
                               "has_ddl_now": True}},
            "gh": {"coffee": {"runs": [
                {"id": 1, "status": "completed", "conclusion": "success",
                 "created_at": "2026-03-01T00:00:00Z", "head_branch": "main"},
                {"id": 2, "status": "completed", "conclusion": "failure",
                 "created_at": "2026-03-02T00:00:00Z", "head_branch": "main"}],
                "provenance": "cached"}},
            "topics": {"coffee": {"topic_ddl": True, "provenance": "cached"},
                       "dont": {"topic_ddl": False, "provenance": "cached"}},
            "ddl_status": {"present": list(ddl.MANAGED_TOOLS),
                           "healthy": list(ddl.MANAGED_TOOLS)},
            "terapia": None,
            "ah": {"coffee": {"findings": 2, "provenance": "cached",
                              "fetched_until": "2026-03-25T00:00:00Z"}},
            "now": "2026-03-25T12:00:00Z",
        }

    # --- aserciones por sección del reporte (panel, repos, analysis) ---

    def _assert_coffee_cell(self, coffee):
        self.assertEqual(coffee["interactions"], 100)
        self.assertEqual(coffee["src"]["interactions"], "reported")
        self.assertIsNone(coffee["errors_api_per_100"])
        self.assertEqual(coffee["src"]["errors_api_per_100"], "missing")
        # recovery: 1 error con éxito después → p50 0 (retry inmediato)
        self.assertEqual(coffee["recovery_p50_interactions"], 0)
        self.assertEqual(coffee["src"]["recovery_p50_interactions"], "derived")
        self.assertEqual(coffee["ci_fail_rate"], 0.5)  # 1 failure / 2 runs
        # gh prov cached en el fixture → el tag hereda cached (D7)
        self.assertEqual(coffee["src"]["ci_fail_rate"], "cached")
        # latencia: sesión 2026-03-20, commit posterior 2026-03-10 NO cuenta
        # (previo) → sin pares → None con missing
        self.assertIsNone(coffee["session_commit_latency_p50_h"])
        self.assertEqual(coffee["src"]["session_commit_latency_p50_h"],
                         "missing")
        # git proxies: fix_ratio sin releases → missing; test_commit_ratio 1/3
        self.assertIsNone(coffee["fix_ratio"])
        self.assertEqual(coffee["src"]["fix_ratio"], "missing")
        self.assertEqual(coffee["test_commit_ratio"], 1 / 3)
        self.assertEqual(coffee["ah_check_findings"], 2)
        self.assertEqual(coffee["src"]["ah_check_findings"], "cached")
        # dosis derivada del mes: wai adoptado 2026-03-05 → 1/13
        self.assertAlmostEqual(coffee["dose"], 1 / 13)
        self.assertEqual(coffee["src"]["dose"], "derived")

    def _assert_dont_cell(self, dont):
        # dont: solo usage y sessions (sin git/gh/ah)
        self.assertEqual(dont["interactions"], 50)
        self.assertEqual(dont["src"]["interactions"], "reported")
        self.assertNotIn("ci_fail_rate", dont)  # sin señal: campo afuera
        self.assertAlmostEqual(dont["dose"], 0.0)  # sin usos → 0.0 derivada

    def _assert_repos(self, rep):
        by_repo = {r["repo"]: r for r in rep["repos"]}
        coffee_r = by_repo["coffee"]
        self.assertFalse(coffee_r["is_control"])
        self.assertEqual(coffee_r["adoption"]["first_ddl_commit"],
                         "2026-03-05T10:00:00Z")
        self.assertAlmostEqual(coffee_r["dose"], 1.0)  # ddl status reporta
        dont_r = by_repo["dont"]
        self.assertTrue(dont_r["is_control"])
        # sin .ddl, sin usos de gestionados → 0.0 con provenance missing
        self.assertAlmostEqual(dont_r["dose"], 0.0)
        self.assertEqual(dont_r["dose_provenance"], "missing")

    def _assert_analysis(self, rep):
        # analysis: estructuras presentes y honestas (datos chicos → razones)
        self.assertIn("its", rep["analysis"])
        self.assertIn("did", rep["analysis"])
        self.assertIn("dose_response", rep["analysis"])
        its_coffee = [e for e in rep["analysis"]["its"]
                      if e["repo"] == "coffee"]
        self.assertTrue(its_coffee)  # ventana pre insuficiente con razón
        self.assertIsNone(its_coffee[0]["delta"])

    def test_panel_repos_y_analysis(self):
        rep = ddl.assemble_report(self._insumos())
        errs = ddl.validate_report(rep)
        self.assertEqual(errs, [], "; ".join(errs))

        # panel
        rows = {(r["repo"], r["month"]): r for r in rep["monthly"]}
        self._assert_coffee_cell(rows[("coffee", "2026-03")])
        self._assert_dont_cell(rows[("dont", "2026-03")])
        # meses sin señal afuera
        self.assertNotIn(("coffee", "2026-02"), rows)

        self._assert_repos(rep)
        self._assert_analysis(rep)

    def test_ci_cached_hereda_tag_de_cache_gh(self):
        """D7: runs del cache gh → src.ci_fail_rate = cached, no reported."""
        insumos = self._insumos()
        insumos["gh"]["coffee"]["provenance"] = "cached"
        rep = ddl.assemble_report(insumos)
        row = next(r for r in rep["monthly"]
                   if r["repo"] == "coffee" and r["month"] == "2026-03")
        self.assertEqual(row["src"]["ci_fail_rate"], "cached")
        self.assertEqual(row["src"]["runs_total"], "cached")

    def test_mes_parcial_y_metadata(self):
        insumos = self._insumos()
        rep = ddl.assemble_report(insumos)
        self.assertIn("generated_at", rep["metadata"])
        self.assertEqual(rep["metadata"]["seed_bootstrap"], 42)
        self.assertEqual(len(rep["metadata"]["managed_tools"]), 13)
        # provenance de flota: repos con y sin git
        prov = rep["metadata"]["provenance"]
        self.assertIn("fleet", prov)
        self.assertIn("coffee", prov["fleet"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
