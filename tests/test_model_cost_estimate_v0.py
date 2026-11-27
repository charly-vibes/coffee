#!/usr/bin/env python3
"""test_model_cost_estimate_v0.py — contrato del estimador v0 de coste por unidad.

Cubre las funciones puras del spike coffe-dnx.10: carriles de coste (real vs
notional), asignación uno-a-uno de unidades a sesiones, ventanas acotadas con
cohorte ambigua, cuantiles/intervalos por cluster, frescura del export de beads,
comparabilidad y el armado de filas del reporte.

Los los tests que fallan por diseño son los de CORR-001/002/003/004 y EDGE-001/002
del review Rule-of-5 (2026-11-26): cada uno corresponde a un CRITICAL/HIGH.
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "model-cost-estimate-v0.py"

spec = importlib.util.spec_from_file_location("model_cost_estimate_v0", SCRIPT)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

SUBS = {"claude-cli": [], "codex": [], "gemini-cli": []}


def _sess(model, cost, sid, end=10):
    """Sesión sintética con el shape que consume build_rows()."""
    return {"model": model, "source": "openrouter", "cost_effective": cost,
            "cost_real": cost, "project": "p", "session_id": sid,
            "start": 0, "end": end, "ambiguous": False}


class LaneTest(unittest.TestCase):
    """CORR-001: notional (suscripción) y real (pay-per-token) no se mezclan."""

    def test_lane_por_fuente(self):
        # sin entrada en config no hay base de coste declarada: se marca aparte
        casos = [
            ("claude-cli", "notional"),
            ("codex", "notional"),
            ("openrouter", "real"),
            ("zai", "real"),
            ("proveedor-nuevo", "unknown"),
        ]
        for fuente, lane in casos:
            with self.subTest(fuente=fuente):
                self.assertEqual(mc.lane_for(fuente, SUBS), lane)


class AssignUnitsTest(unittest.TestCase):
    """CORR-002: una unidad se acredita a UNA sesión, no a todas las que la contienen."""

    def test_acreditacion_segun_ventanas(self):
        casos = [
            ("cerrada_dentro_de_una_sola_ventana", [("bead-1", 150)],
             [{"id": "a", "start": 100, "end": 200}], {"bead-1": "a"}),
            ("solapadas_elige_la_de_inicio_mas_cercano", [("bead-1", 520)],
             [{"id": "larga", "start": 0, "end": 600},
              {"id": "corta", "start": 500, "end": 700}], {"bead-1": "corta"}),
            ("fuera_de_ventana_no_se_acredita", [("bead-1", 900)],
             [{"id": "a", "start": 100, "end": 200}], {"bead-1": None}),
        ]
        for nombre, unidades, win, esperado in casos:
            with self.subTest(caso=nombre):
                self.assertEqual(mc.assign_units(unidades, win), esperado)

    def test_no_se_puede_acreditar_a_dieciseis_sesiones(self):
        win = [{"id": f"s{i}", "start": i * 10, "end": 1000} for i in range(16)]
        got = mc.assign_units([("bead-1", 500)], win)
        targets = [v for v in got.values() if v]
        self.assertEqual(len(targets), 1, "un cierre, un crédito")
        self.assertIn(targets[0], {w["id"] for w in win})

    def test_fuera_de_ventana_no_se_acredita(self):
        win = [{"id": "a", "start": 100, "end": 200}]
        self.assertEqual(mc.assign_units([("bead-1", 900)], win), {"bead-1": None})

    def test_cada_cierre_se_acredita_como_mucho_una_vez(self):
        win = [{"id": "a", "start": 0, "end": 100}]
        got = mc.assign_units([("bead-1", 10), ("bead-1", 20)], win)
        credit = [v for v in got.values() if v]
        self.assertEqual(len(credit), 1)


class BoundedWindowTest(unittest.TestCase):
    """CORR-004: una sesión de 9h no barre los commits de las demás."""

    def test_ventana_corta_no_se_toca(self):
        self.assertEqual(mc.bounded_window(0, 600, 3600), (0, 600, False))

    def test_ventana_larga_se_acota_y_se_marca_ambigua(self):
        a, b, amb = mc.bounded_window(0, 36000, 3600)
        self.assertEqual((a, b), (0, 3600))
        self.assertTrue(amb)

    def test_ventana_exactamente_en_el_limite_no_es_ambigua(self):
        self.assertEqual(mc.bounded_window(0, 3600, 3600), (0, 3600, False))


class QuantileTest(unittest.TestCase):
    def test_mediana_con_interpolacion_lineal(self):
        self.assertAlmostEqual(mc.pctl([1, 2, 3, 4], 0.5), 2.5)

    def test_vacio_devuelve_none_no_nan(self):
        self.assertIsNone(mc.pctl([], 0.5))

    def test_p90_de_un_solo_dato_es_ese_dato(self):
        self.assertAlmostEqual(mc.pctl([7.0], 0.9), 7.0)


class WilsonTest(unittest.TestCase):
    def test_cota_inferior_por_debajo_de_la_proporcion(self):
        lo = mc.wilson_lower(31, 50)
        self.assertLess(lo, 0.62)
        self.assertGreater(lo, 0.48)

    def test_muestra_pequena_da_cota_muy_baja(self):
        self.assertLess(mc.wilson_lower(3, 25), 0.25)

    def test_sin_muestra_es_none(self):
        self.assertIsNone(mc.wilson_lower(0, 0))


class ClusterBootstrapTest(unittest.TestCase):
    """EXCL-002: la sesión está anidada en (repo, día); el IC debe remuestrear clusters."""

    def test_intervalo_contiene_la_mediana(self):
        pairs = [(f"r{i%3}-d{i%5}", float(i)) for i in range(1, 60)]
        lo, hi = mc.cluster_bootstrap_ci([p[1] for p in pairs],
                                        [p[0] for p in pairs], B=400, seed=7)
        self.assertLessEqual(lo, mc.pctl([p[1] for p in pairs], 0.5))
        self.assertGreaterEqual(hi, mc.pctl([p[1] for p in pairs], 0.5))

    def test_un_solo_cluster_no_produce_intervalo(self):
        lo, hi = mc.cluster_bootstrap_ci([1.0, 2.0, 3.0], ["r"] * 3, B=100, seed=7)
        self.assertIsNone(lo)
        self.assertIsNone(hi)


class ComparabilityTest(unittest.TestCase):
    """EXCL-003: comparabilidad derivada de la muestra, no de un umbral inventado."""

    def test_muestra_suficiente_y_ic_angosto_es_comparable(self):
        ok, reason = mc.is_comparable(60, 0.10)
        self.assertTrue(ok)
        self.assertEqual(reason, "comparable")

    def test_ic_ancho_no_es_comparable(self):
        ok, reason = mc.is_comparable(60, 0.90)
        self.assertFalse(ok)
        self.assertIn("ci", reason)

    def test_muestra_chica_no_es_comparable(self):
        ok, reason = mc.is_comparable(4, 0.05)
        self.assertFalse(ok)
        self.assertIn("n", reason)


class BeadsFreshnessTest(unittest.TestCase):
    """EDGE-001: un export viejo no es 'no hubo cierres'."""

    def test_beads_freshness_tabla_de_casos(self):
        # un export viejo no es "no hubo cierres": cada hueco se declara
        casos = [
            (2000, 1000, "fresh"),                # export posterior al último commit
            (1000, 1000 + 60 * 86400, "stale"),   # export muy anterior
            (None, 1000, "no-data"),              # sin archivo
            (1000, None, "unknown"),              # sin historial git
        ]
        for mtime, last_commit, esperado in casos:
            with self.subTest(esperado=esperado):
                self.assertEqual(mc.beads_freshness(mtime, last_commit, 30),
                                 esperado)


class BuildRowsTest(unittest.TestCase):
    """CORR-003 y EDGE-002: nada de 'mixed' como modelo, y el hueco se declara."""

    def test_mixed_no_aparece_como_modelo(self):
        sessions = [_sess("mixed", 10.0, "s1")]
        rows = mc.build_rows(sessions, units={}, projects={"p": "/tmp/p"},
                             subscriptions=SUBS)
        self.assertNotIn("mixed", [r["model"] for r in rows])

    def test_sesiones_sin_repo_van_a_unattributed_y_no_desaparecen(self):
        s = _sess("m1", 5.0, "s1")
        s["project"] = "sin-repo"
        rows = mc.build_rows([s], units={}, projects={}, subscriptions=SUBS)
        unatt = [r for r in rows if r["model"] == "unattributed"]
        self.assertEqual(len(unatt), 1)
        self.assertEqual(unatt[0]["n_sessions"], 1)
        self.assertAlmostEqual(unatt[0]["cost"], 5.0)

    def test_una_unidad_cuenta_para_una_sola_sesion_y_para_su_modelo(self):
        sessions = [_sess("m1", 5.0, "s1"), _sess("m2", 5.0, "s2")]
        units = {"bead-1": "s1", "bead-2": "s1", "bead-3": "s2"}
        rows = mc.build_rows(sessions, units=units, projects={"p": "/tmp/p"},
                             subscriptions=SUBS)
        by_model = {r["model"]: r for r in rows}
        self.assertEqual(by_model["m1"]["units"], 2)
        self.assertEqual(by_model["m2"]["units"], 1)

    def test_los_dos_carriles_no_comparten_fila(self):
        s_notional = _sess("glm", 20.0, "s2")
        s_notional["source"] = "codex"
        s_notional["cost_real"] = 0.0
        sessions = [_sess("glm", 1.0, "s1"), s_notional]
        rows = mc.build_rows(sessions, units={}, projects={"p": "/tmp/p"},
                             subscriptions=SUBS)
        self.assertEqual(sorted(r["lane"] for r in rows if r["model"] == "glm"),
                         ["notional", "real"])
        real = [r for r in rows if r["model"] == "glm" and r["lane"] == "real"][0]
        notional = [r for r in rows if r["model"] == "glm" and r["lane"] == "notional"][0]
        self.assertAlmostEqual(real["cost"], 1.0)
        self.assertAlmostEqual(notional["cost"], 0.0)  # real, no el imputado

    def test_lamina_ambigua_se_declara_aparte(self):
        s = _sess("m1", 2.0, "s1", end=36000)
        s["ambiguous"] = True
        rows = mc.build_rows([s], units={}, projects={"p": "/tmp/p"},
                             subscriptions=SUBS)
        row = [r for r in rows if r["model"] == "m1"][0]
        self.assertEqual(row["n_ambiguous"], 1)
        self.assertAlmostEqual(row["cost_ambiguous"], 2.0)

    def test_unidades_de_la_cohorte_ambigua_se_cuentan_aparte(self):
        s_ambigua = _sess("m1", 2.0, "s1", end=36000)
        s_ambigua["ambiguous"] = True
        sessions = [s_ambigua, _sess("m1", 1.0, "s2", end=60)]
        rows = mc.build_rows(sessions, units={"b1": "s2", "b2": "s1"},
                             projects={"p": "/tmp/p"}, subscriptions=SUBS)
        row = rows[0]
        self.assertEqual(row["units"], 2)
        self.assertEqual(row["units_ambiguous"], 1)
        self.assertAlmostEqual(row["units_ambiguous_share"], 0.5)

    def test_banda_de_sensibilidad_excluye_la_cohorte_ambigua(self):
        # CORR-004: la métrica depende de max_window_s, así que se publica la
        # banda [solo sesiones limpias, todas] en lugar de un único número.
        s_ambigua = _sess("m1", 9.0, "s1", end=36000)
        s_ambigua["ambiguous"] = True
        sessions = [s_ambigua, _sess("m1", 1.0, "s2", end=60)]
        rows = mc.build_rows(sessions, units={"b1": "s2", "b2": "s1"},
                             projects={"p": "/tmp/p"}, subscriptions=SUBS)
        row = rows[0]
        self.assertAlmostEqual(row["p50_unit_cost"], 5.0)          # (1 + 9) / 2
        self.assertAlmostEqual(row["p50_unit_cost_strict"], 1.0)   # solo s2


if __name__ == "__main__":
    unittest.main()
