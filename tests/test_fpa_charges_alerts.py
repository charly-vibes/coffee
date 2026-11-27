#!/usr/bin/env python3
"""
test_fpa_charges_alerts.py — alertas del ledger: verify-plan y
reconciliación (FPA-081/082).

Parte de la antigua test_fpa_charges.py (coffe-kav.5).
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TODAY = "2026-07-20"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_charges", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _charged_fixture():
    """Fixture f2 + emisiones del ledger (shape del tracker coffe-a31.2).

    Ledger del fixture: claude-cli may $100 (Max) + jun $20 (créditos
    p2p); codex may $20 (Plus); `otro-tool` solo tiene factura fuera del
    periodo (dic-2025) → n/a con razón en el periodo (FPA-008).
    """
    fx = f2.f2_fixture()
    fx["metadata"]["charges_provenance"] = "reported"
    fx["metadata"]["charges_source"] = "data/charges.json"
    fx["metadata"]["charges_total_real"] = 170.0
    fx["charges_real_by_month"] = {
        "claude-cli": {"2026-05": 100.0, "2026-06": 20.0},
        "codex": {"2026-05": 20.0},
        "otro-tool": {"2025-12": 30.0},  # fuera del periodo del reporte
    }
    # calendario corregido: may = claude Max $100 + codex Plus $20
    fx["subscription_fees_by_month"] = {"2026-05": 120.0}
    # p2p del ledger para jun (créditos claude $20) → *reported* (FPA-013)
    fx["monthly"]["2026-06"]["pay_per_token_charges"] = 20.0
    fx["monthly"]["2026-06"]["pay_per_token_provenance"] = "reported"
    return fx


class TestVerifyPlanSinPlan(unittest.TestCase):
    """FPA-081 sobre el calendario corregido: sin plan que cubra el mes
    con uso de Claude → alerta (el caso jun-sep del calendario real)."""

    def _alerts(self, fx):
        return viz.build_alerts(fx, CONFIG, today=TODAY)

    def test_sin_plan_activo_fira(self):
        """claude jun: efectivo $60 y el calendario no cubre el mes."""
        alerts = self._alerts(_charged_fixture())
        ev = [a for a in alerts if a["rule"] == "verify-plan"
              and a["evidence"]["month"] == "2026-06"]
        self.assertEqual(1, len(ev))
        self.assertEqual("high", ev[0]["severity"])
        self.assertEqual(0.0, ev[0]["evidence"]["plan_fee"])
        self.assertIsNone(ev[0]["evidence"]["multiple"])
        self.assertAlmostEqual(60.0, ev[0]["evidence"]["claude_eff"])

    def test_sin_plan_y_sin_uso_no_fira(self):
        """Sin uso de Claude en el mes no hay nada que verificar."""
        fx = _charged_fixture()
        fx["monthly"]["2026-06"]["tools"]["claude-cli"]["cost_effective"] = 0.0
        fx["monthly"]["2026-06"]["models"] = {}
        alerts = self._alerts(fx)
        ev = [a for a in alerts if a["rule"] == "verify-plan"
              and a["evidence"]["month"] == "2026-06"]
        self.assertEqual(0, len(ev))

    def test_calendario_sin_claude_no_fira(self):
        """Sin entradas de claude-cli en el config no hay calendario que
        verificar (el alerta no fabrica señal)."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["subscriptions"].pop("claude-cli")
        alerts = viz.build_alerts(_charged_fixture(), cfg, today=TODAY)
        self.assertNotIn("verify-plan", {a["rule"] for a in alerts})

    def test_mes_cubierto_usa_el_multiple(self):
        """may: cubierto por el calendario corregido ($20 de Pro tras el
        19-05) → el multiple > umbral sigue disparando como antes."""
        fx = _charged_fixture()
        fx["monthly"]["2026-05"]["tools"]["claude-cli"]["cost_effective"] = 3000.0
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        ev = [a for a in alerts if a["rule"] == "verify-plan"
              and a["evidence"]["month"] == "2026-05"]
        self.assertEqual(1, len(ev))
        self.assertEqual(20.0, ev[0]["evidence"]["plan_fee"])
        self.assertEqual(150.0, ev[0]["evidence"]["multiple"])


class TestReconciliationAlertaLedger(unittest.TestCase):
    """FPA-082: cash real (ledger) vs cargas implícitas del calendario."""

    def test_caso_resuelto_no_fira(self):
        """Calendario corregido: real = implícito + p2p reportado → sin
        alerta (el caso motivador jun $0 vs implícito $0 queda resuelto)."""
        fx = _charged_fixture()
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        self.assertNotIn("reconciliation", {a["rule"] for a in alerts})

    def test_calendario_desalineado_fira(self):
        """Calendario que cobra $50 en may vs facturas reales $120 → alerta
        con ambos montos (real *reported* vs implícito *assumed*)."""
        fx = _charged_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 50.0}
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        ev = [a for a in alerts if a["rule"] == "reconciliation"]
        self.assertEqual(1, len(ev))
        self.assertEqual("high", ev[0]["severity"])
        self.assertAlmostEqual(140.0, ev[0]["evidence"]["reported"])
        self.assertAlmostEqual(70.0, ev[0]["evidence"]["implied"])  # 50 + 20 p2p
        self.assertEqual("data/charges.json",
                         ev[0]["evidence"]["source"])

    def test_tolerancia_configurable(self):
        fx = _charged_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 130.0}
        # 10/150 ≈ 6.7%: dentro de la tolerancia default (10%), fuera de 5%
        cfg10 = json.loads(json.dumps(CONFIG))
        alerts = viz.build_alerts(fx, cfg10, today=TODAY)
        self.assertNotIn("reconciliation", {a["rule"] for a in alerts})
        cfg5 = json.loads(json.dumps(CONFIG))
        cfg5["alert_thresholds"]["reconcile_tolerance_pct"] = 5.0
        alerts = viz.build_alerts(fx, cfg5, today=TODAY)
        self.assertIn("reconciliation", {a["rule"] for a in alerts})

    def test_fallback_sin_ledger_conserva_comportamiento(self):
        """Reporte sin ledger: el chequeo previo (cash tracker vs implícito)
        sigue disponible — no se rompen reportes viejos."""
        fx = f2.f2_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 110.0, "2026-06": 110.0}
        fx["monthly"]["2026-05"]["cost_real"] = 55.58
        fx["monthly"]["2026-06"]["cost_real"] = 10.0
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        ev = [a for a in alerts if a["rule"] == "reconciliation"]
        self.assertEqual(1, len(ev))
        self.assertAlmostEqual(65.58, ev[0]["evidence"]["reported"])


if __name__ == "__main__":
    unittest.main()
