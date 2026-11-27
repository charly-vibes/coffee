#!/usr/bin/env python3
"""Tests de análisis causal (ITS, DiD, DiD bootstrap, dosis-respuesta) en ddl-effect-tracker.py.

4.2-4.4 y 5.4. Solo stdlib. Sin disk I/O: los reducers reciben
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


class ItsTest(unittest.TestCase):
    """4.2 (coffe-ktu): ITS pre/post, media móvil trimestral + IC bootstrap."""

    MONTHS = [f"2026-{m:02d}" for m in range(1, 9)]  # 2026-01..2026-08

    def _rows(self, values):
        return [{"month": m, "ah_check_findings": v}
                for m, v in zip(self.MONTHS, values)]

    def test_delta_pre_post_paso_escalon(self):
        out = ddl.its_pre_post(self._rows([1, 1, 1, 1, 3, 3, 3, 3]),
                               "ah_check_findings", "2026-05")
        self.assertEqual(out["pre_mean"], 1.0)
        self.assertEqual(out["post_mean"], 3.0)
        self.assertEqual(out["delta"], 2.0)
        self.assertEqual(out["n_pre"], 4)
        self.assertEqual(out["n_post"], 4)

    def test_ci_bracketea_delta(self):
        out = ddl.its_pre_post(self._rows([1, 2, 1, 3, 5, 4, 6, 5]),
                               "ah_check_findings", "2026-05", n_boot=1000)
        self.assertLess(out["ci_low"], out["delta"])
        self.assertGreater(out["ci_high"], out["delta"])

    def test_reproducible_con_seed_fija(self):
        rows = self._rows([1, 2, 1, 3, 5, 4, 6, 5])
        a = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        b = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        self.assertEqual(a, b)

    def test_ventanas_insuficientes_omitidas_con_razon(self):
        # ventanas insuficientes → delta None con razón, nunca 0 disfrazado:
        # adopción que precede al tracking (2 meses pre < mínimo 3),
        # adopción tras el último mes (post vacío) y adopción en el último
        # mes (1 mes post no alcanza para la media móvil trimestral)
        casos = [
            ("ventana_pre_insuficiente", "2026-03",
             [1, 1, 3, 3, 3, 3, 3, 3], "n_pre", 2),
            ("sin_meses_post", "2026-09",
             [1, 1, 1, 1, 1, 1, 1, 1], "n_post", 0),
            ("ventana_ma_trimestral_insuficiente", "2026-08",
             [1, 1, 1, 1, 1, 1, 1, 1], "n_post", 1),
        ]
        for razon, mes_adopcion, valores, clave_n, n in casos:
            with self.subTest(razon=razon):
                out = ddl.its_pre_post(self._rows(valores),
                                       "ah_check_findings", mes_adopcion)
                self.assertIsNone(out["delta"])
                self.assertEqual(out["reason"], razon)
                self.assertEqual(out[clave_n], n)

    def test_ventana_pre_de_3_meses_si_estima(self):
        # borde: exactamente 3 meses pre es suficiente (D5)
        out = ddl.its_pre_post(self._rows([1, 1, 1, 3, 3, 3, 3, 3]),
                               "ah_check_findings", "2026-04")
        self.assertEqual(out["n_pre"], 3)
        self.assertIsNone(out.get("reason"))
        self.assertEqual(out["delta"], 2.0)

    def test_meses_con_metrica_none_excluidos(self):
        rows = self._rows([1, None, 1, 1, 3, 3, 3, 3])
        out = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        self.assertEqual(out["n_pre"], 3)  # el None de feb no cuenta
        self.assertEqual(out["delta"], 2.0)


class DidTest(unittest.TestCase):
    """4.3 (coffe-dek): DiD 2×2 contra control + regla dura n<3."""

    MONTHS = [f"2026-{m:02d}" for m in range(1, 9)]

    def _rows(self, pre, post):
        vals = list(pre) + list(post)
        return [{"month": m, "ah_check_findings": v}
                for m, v in zip(self.MONTHS, vals)]

    def _repo_deltas(self):
        return ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5]),
             "bd": self._rows([7, 7, 7, 7], [7, 7, 7, 7]),
             "wild": self._rows([9, 9, 9, 9], [9, 9, 9, 9])},
            "ah_check_findings", "2026-05")

    def test_repo_deltas_por_repo(self):
        out = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3])},
            "ah_check_findings", "2026-05")
        self.assertEqual(out["wai"]["delta"], 2.0)
        self.assertEqual(out["wai"]["pre_mean"], 1.0)
        self.assertEqual(out["wai"]["post_mean"], 3.0)

    def test_repo_sin_post_delta_none(self):
        rows = [{"month": m, "ah_check_findings": 1}
                for m in self.MONTHS[:4]]
        out = ddl.repo_deltas({"ghost": rows}, "ah_check_findings",
                              "2026-05")
        self.assertIsNone(out["ghost"]["delta"])

    def test_did_basico_control_plano(self):
        deltas = self._repo_deltas()
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 2.0)  # tratamiento +2, control +0
        self.assertEqual(out["n_treatment"], 3)
        self.assertEqual(out["n_control"], 3)

    def test_did_resta_tendencia_global(self):
        # control sube igual que el tratamiento: el efecto se cancela
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "bd": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "wild": self._rows([0, 0, 0, 0], [2, 2, 2, 2])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 0.0)

    def test_n_insuficiente_tratamiento_marca_el_json(self):
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5]),
             "bd": self._rows([7, 7, 7, 7], [7, 7, 7, 7]),
             "wild": self._rows([9, 9, 9, 9], [9, 9, 9, 9])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertIsNone(out["did"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n_treatment"], 2)  # visible, no oculto
        self.assertEqual(out["n_control"], 3)

    def test_n_insuficiente_control(self):
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont"})
        self.assertIsNone(out["did"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n_control"], 1)

    def test_repo_sin_delta_no_cuenta_en_n(self):
        deltas = self._repo_deltas()
        deltas["ghost"] = {"pre_mean": 1.0, "post_mean": None, "delta": None}
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 2.0)
        self.assertEqual(out["n_treatment"], 3)

    def test_borde_n_3_si_estima(self):
        deltas = self._repo_deltas()
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"},
                          min_n=3)
        self.assertIsNone(out.get("reason"))
        self.assertEqual(out["did"], 2.0)


class DoseResponseTest(unittest.TestCase):
    """4.4 (coffe-s8j): Spearman dosis × outcome, n por punto."""

    def _points(self, pairs):
        return [{"repo": f"r{i}", "dose": d, "ah_check_findings": v}
                for i, (d, v) in enumerate(pairs)]

    def test_monotona_creciente_rho_1(self):
        pts = self._points([(0.0, 1), (0.3, 2), (0.6, 3), (1.0, 4)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertAlmostEqual(out["spearman_rho"], 1.0)
        self.assertEqual(out["n"], 4)

    def test_monotona_decreciente_rho_menos_1(self):
        pts = self._points([(0.0, 4), (0.3, 3), (0.6, 2), (1.0, 1)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertAlmostEqual(out["spearman_rho"], -1.0)

    def test_empates_promedian_rangos(self):
        # dosis [1,1,0.5,0.3] vs outcome [1,2,3,4]: anticorrelación casi
        # perfecta con empate promediado en el top → rho ≈ −0.9487
        pts = self._points([(1.0, 1), (1.0, 2), (0.5, 3), (0.3, 4)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertAlmostEqual(out["spearman_rho"], -0.9487, places=3)

    def test_n_insuficiente_no_estima(self):
        pts = self._points([(0.0, 1), (1.0, 4)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertIsNone(out["spearman_rho"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n"], 2)  # visible, no oculto

    def test_borde_n_3_si_estima(self):
        pts = self._points([(0.0, 1), (0.5, 3), (1.0, 4)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertIsNone(out.get("reason"))
        self.assertEqual(out["n"], 3)

    def test_pares_con_none_excluidos_del_n(self):
        pts = self._points([(0.0, 1), (0.3, None), (0.6, 3), (None, 4),
                            (1.0, 5)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertEqual(out["n"], 3)
        self.assertAlmostEqual(out["spearman_rho"], 1.0)

    def test_dosis_sin_varianza_degenerada(self):
        # toda la flota viva con dosis ~1 (design Riesgos) → rho None
        # declarado, no correlación fake
        pts = self._points([(1.0, 1), (1.0, 2), (1.0, 3), (1.0, 4)])
        out = ddl.dose_response(pts, "ah_check_findings")
        self.assertIsNone(out["spearman_rho"])
        self.assertEqual(out["reason"], "sin_varianza")
        self.assertEqual(out["n"], 4)


class DidBootstrapTest(unittest.TestCase):
    """5.4 (coffe-470): DiD con IC bootstrap (re-muestreo de deltas por grupo)."""

    def _deltas(self):
        return {"wai": {"pre_mean": 1.0, "post_mean": 3.0, "delta": 2.0},
                "turu": {"pre_mean": 2.0, "post_mean": 4.0, "delta": 2.5},
                "ah": {"pre_mean": 0.0, "post_mean": 1.0, "delta": 1.0},
                "dont": {"pre_mean": 5.0, "post_mean": 5.0, "delta": 0.0},
                "bd": {"pre_mean": 7.0, "post_mean": 7.5, "delta": 0.5},
                "wild": {"pre_mean": 9.0, "post_mean": 8.5, "delta": -0.5}}

    def test_punto_igual_que_did_2x2(self):
        out = ddl.did_bootstrap(self._deltas(), {"dont", "bd", "wild"})
        self.assertEqual(out["did"], ddl.did_2x2(
            self._deltas(), {"dont", "bd", "wild"})["did"])

    def test_ci_bracketea_el_punto(self):
        out = ddl.did_bootstrap(self._deltas(), {"dont", "bd", "wild"})
        self.assertLess(out["ci_low"], out["did"])
        self.assertGreater(out["ci_high"], out["did"])

    def test_reproducible_con_seed_fija(self):
        a = ddl.did_bootstrap(self._deltas(), {"dont", "bd", "wild"})
        b = ddl.did_bootstrap(self._deltas(), {"dont", "bd", "wild"})
        self.assertEqual(a, b)

    def test_n_insuficiente_regla_dura(self):
        deltas = {k: v for k, v in self._deltas().items() if k != "ah"}
        out = ddl.did_bootstrap(deltas, {"dont", "bd", "wild"})
        self.assertIsNone(out["did"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n_treatment"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
