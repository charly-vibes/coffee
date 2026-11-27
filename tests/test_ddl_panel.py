#!/usr/bin/env python3
"""Tests del panel repo × mes (cell_from_emitters / build_panel) en ddl-effect-tracker.py.

4.1 (coffe-w02). Solo stdlib. Sin disk I/O: los reducers reciben
estructuras, no paths (mismo estilo que test_tracker.py con aggregate()).
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

REGISTRY = ddl.MANAGED_TOOLS


class PanelTest(unittest.TestCase):
    """4.1 (coffe-w02): ensamblado del panel repo × mes (monthly[])."""

    # --- cell_from_emitters -------------------------------------------------

    def test_scalar_provenance_aplica_a_todos_los_campos(self):
        ci = {"ci_fail_rate": 0.25, "runs_total": 4, "runs_failed": 1,
              "provenance": "reported"}
        row = ddl.cell_from_emitters("wai", "2026-08", {"ci": ci})
        self.assertEqual(row["ci_fail_rate"], 0.25)
        self.assertEqual(row["src"]["ci_fail_rate"], "reported")
        self.assertEqual(row["src"]["runs_total"], "reported")
        self.assertEqual(row["src"]["runs_failed"], "reported")

    def test_dict_provenance_por_campo(self):
        errs = ddl.error_rates(120, 3, {"timeout": 1})
        row = ddl.cell_from_emitters("wai", "2026-08", {"errors": errs})
        self.assertEqual(row["src"]["errors_api_per_100"], "reported")
        self.assertEqual(row["src"]["errors_terapia_per_100"], "reported")
        self.assertEqual(row["src"]["interactions"], "reported")

    def test_tag_missing_normaliza_a_null(self):
        # dose 0.0 con provenance missing nunca llega al panel como 0 fake
        dose = {"dose": 0.0, "dose_detail": {"present": [], "healthy": [],
                                             "missing": list(REGISTRY)},
                "provenance": {"dose": "missing"}}
        row = ddl.cell_from_emitters("ghost", "2026-08", {"dose": dose})
        self.assertIsNone(row["dose"])
        self.assertEqual(row["src"]["dose"], "missing")

    def test_reason_visible_en_la_fila(self):
        ci = {"ci_fail_rate": None, "runs_total": 0, "runs_failed": 0,
              "provenance": "missing", "reason": {"ci_fail_rate": "sin red"}}
        row = ddl.cell_from_emitters("wai", "2026-08", {"ci": ci})
        self.assertEqual(row["reasons"]["ci_fail_rate"], "sin red")

    def test_campo_fuera_del_allowlist_falla_loud(self):
        em = {"karma": 5, "provenance": "reported"}
        with self.assertRaises(ValueError):
            ddl.cell_from_emitters("wai", "2026-08", {"x": em})

    def test_campo_duplicado_entre_emisores_falla_loud(self):
        a = {"dose": 0.5, "provenance": {"dose": "derived"}}
        b = {"dose": 0.6, "provenance": {"dose": "reported"}}
        with self.assertRaises(ValueError):
            ddl.cell_from_emitters("wai", "2026-08", {"a": a, "b": b})

    # --- build_panel --------------------------------------------------------

    def test_mes_sin_senal_afuera_del_array(self):
        cells = [ddl.cell_from_emitters("ghost", "2026-08",
                                        {"dose": {"dose": 0.0, "provenance":
                                                  {"dose": "missing"}}})]
        self.assertEqual(ddl.build_panel(cells), [])

    def test_senal_minima_mantiene_el_mes(self):
        em = {"ah_check_findings": 2, "provenance": "reported"}
        panel = ddl.build_panel([ddl.cell_from_emitters("wai", "2026-08",
                                                        {"ah": em})])
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0]["ah_check_findings"], 2)
        self.assertEqual(panel[0]["src"]["ah_check_findings"], "reported")

    def test_filas_invalidas_fallan_loud(self):
        casos = [
            ("null_con_tag_reported",
             {"repo": "wai", "month": "2026-08", "ci_fail_rate": None,
              "src": {"ci_fail_rate": "reported"}}),
            ("valor_con_tag_missing",
             {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
              "src": {"revert_ratio": "missing"}}),
            ("cifra_sin_tag",
             {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
              "src": {}}),
            ("tag_invalido",
             {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
              "src": {"revert_ratio": "guessed"}}),
        ]
        for nombre, row in casos:
            with self.subTest(caso=nombre):
                with self.assertRaises(ValueError):
                    ddl.build_panel([row])

    def test_celda_duplicada_repo_mes_falla_loud(self):
        em = {"ah_check_findings": 1, "provenance": "reported"}
        c = ddl.cell_from_emitters("wai", "2026-08", {"ah": em})
        with self.assertRaises(ValueError):
            ddl.build_panel([c, c])

    def test_orden_determinista_repo_mes(self):
        c1 = ddl.cell_from_emitters("turu", "2026-03",
                                    {"ah": {"ah_check_findings": 1,
                                            "provenance": "reported"}})
        c2 = ddl.cell_from_emitters("wai", "2026-08",
                                    {"ah": {"ah_check_findings": 2,
                                            "provenance": "reported"}})
        c3 = ddl.cell_from_emitters("wai", "2026-02",
                                    {"ah": {"ah_check_findings": 3,
                                            "provenance": "reported"}})
        panel = ddl.build_panel([c2, c1, c3])
        self.assertEqual([(r["repo"], r["month"]) for r in panel],
                         [("turu", "2026-03"), ("wai", "2026-02"),
                          ("wai", "2026-08")])
        self.assertEqual(ddl.build_panel([c3, c2, c1]), panel)

    def test_fila_completa_de_celdas_reales(self):
        errs = ddl.error_rates(120, 3, {"timeout": 1})
        ci = {"ci_fail_rate": None, "runs_total": 0, "runs_failed": 0,
              "provenance": "missing", "reason": {"ci_fail_rate": "sin red"}}
        dose = {"dose": 4 / 13, "provenance": {"dose": "reported"}}
        row = ddl.cell_from_emitters(
            "wai", "2026-08",
            {"errors": errs, "ci": ci, "dose": dose,
             "rec": {"recovery_p50_interactions": 2, "recovery_n": 5,
                     "provenance": "derived"}})
        panel = ddl.build_panel([row])
        self.assertEqual(panel[0]["repo"], "wai")
        self.assertEqual(panel[0]["month"], "2026-08")
        self.assertEqual(panel[0]["dose"], 4 / 13)
        self.assertEqual(panel[0]["src"]["recovery_p50_interactions"],
                         "derived")
        self.assertNotIn("provenance", panel[0])  # solo src
        self.assertNotIn("dose_detail", panel[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
