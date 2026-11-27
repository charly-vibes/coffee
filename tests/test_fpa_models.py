#!/usr/bin/env python3
"""
test_fpa_models.py — coffe-dnx.6: vista 'Modelos'.

Parte de la antigua test_fpa.py (coffe-kav.5): la vista Modelos consume
model_scorecard[] con métrica ponderada, cohortes sin datos con razón,
latencia con cobertura, CSV con provenance y deep-link.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa", "scripts/viz-fpa.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _rendered():
    """HTML real del reporte del repo."""
    return viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")


class TestModelsView(unittest.TestCase):
    """coffe-dnx.6: vista 'Modelos' consumiendo model_scorecard[] (.8)."""

    @staticmethod
    def _rows():
        return viz.models_html(REPORT, CONFIG)

    def test_metrica_ponderada_no_coste_por_tokens_totales(self):
        """Rojo (coffe-dnx.6): la vista usa cost_weighted_per_1m; si el
        render usara cost_per_1m_total_tokens (cost/tokens_totales) el
        test falla."""
        html = self._rows()
        self.assertIn("$ ponderado por 1M tokens", html)
        self.assertNotIn("tokens_totales", html)
        self.assertNotIn("cost_per_1m_total_tokens", html)

    def test_cohorte_sin_datos_fila_sin_datos_con_razon(self):
        """coffe-dnx.6: claude-opus-4.5 no tiene interacciones en el
        scorecard (data: null con razón) ⇒ fila 'sin datos' con la razón,
        nunca 0."""
        html = self._rows()
        self.assertIn("sin datos", html)
        self.assertIn("sin interacciones de este modelo", html)
        # la fila del modelo sin datos no debe rendir 0 disfrazado de dato
        row = [r for r in html.split("<tr")
               if "claude-opus-4.5" in r][0]
        self.assertNotIn(">$0.00<", row)

    def test_latencia_con_cobertura_cuando_baja(self):
        """coffe-dnx.6: latency.coverage_pct < 50 ⇒ cobertura junto a la
        cifra; >= 50 no la necesita."""
        html = self._rows()
        self.assertIn("cobertura 11.8%", html)   # glm-5.3-flash, todo
        self.assertIn("cobertura 1.05%", html)   # deepseek-v4-flash, todo
        self.assertNotIn("cobertura 100%", html)  # >=50 no necesita cobertura

    def test_csv_mantiene_provenance_por_fila(self):
        """coffe-dnx.6: el export CSV toma el DOM de la tabla; cada fila
        con cifras lleva su provenance (reported/estimated/assumed)."""
        html = self._rows()
        rows = [r for r in html.split("<tr")
                if "prov-tag" in r and "</td>" in r]
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertIn('class="prov-tag"', r)

    def test_banner_veredicto_con_razon(self):
        """coffe-dnx.6: banner de la regla de decisión con veredicto y
        razón (mantener|cambiar|datos_insuficientes)."""
        html = self._rows()
        self.assertIn("datos_insuficientes", html)
        self.assertIn("referencia claude-opus-4.5 sin cost_per_accepted_task",
                      html)

    def test_nulos_con_razon_y_nunca_cero(self):
        """coffe-dnx.6: nulls[] del scorecard se renderizan con razón;
        celdas ausentes son n/a con razón, no 0."""
        report = json.loads(json.dumps(REPORT))
        entry = report["model_scorecard"][0]
        entry["data"]["cost_per_task"] = None
        entry["data"]["nulls"] = [{"field": "cost_per_task",
                                   "reason": "sin tareas aceptadas"}]
        html = viz.models_html(report, CONFIG)
        self.assertIn("n/a", html)
        self.assertIn("sin tareas aceptadas", html)

    def test_vista_registrada_con_deeplink(self):
        """coffe-dnx.6: tab 'Modelos', sección #models y deep-link ?view=
        models (VIEWS del JS), sin página nueva en data/."""
        html = _rendered()
        self.assertIn('id="models"', html)
        self.assertIn('data-view="models"', html)
        self.assertIn('"models", "gasto"', html)  # VIEWS del JS
        self.assertIn("Modelos", html)


if __name__ == "__main__":
    unittest.main()
