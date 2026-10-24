#!/usr/bin/env python3
"""test_tracker_regression_guard.py — guard fail-loud contra poda del cache
frágil de Claude (coffe-0c8, antecedente coffe-jg3).

dashboard-cache.json es fuente única de claude-cli para sesiones cuyo jsonl
ya no existe; Claude Code lo reescribe truncando turns viejos (~49k
interacciones perdidas el 2026-10-21 sin ruido). El guard compara las
interacciones claude-cli por mes contra el reporte vigente y aborta la regen
si un mes cae más del umbral (default 20%), en vez de pisar el dataset.

Hermetic: la función de detección es pura; el wiring se testea con archivos
temporales, sin tocar data/ real ni ~/.claude.
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_tracker():
    """Importa usage-tracker.py (guión: no importable como módulo normal)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "usage_tracker", REPO / "scripts" / "usage-tracker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def _old_report(claude_by_month=None, codex_by_month=None):
    """Reporte vigente mínimo: solo lo que lee el guard (monthly.tools)."""
    monthly = {}
    for mes, n in (claude_by_month or {}).items():
        monthly.setdefault(mes, {"tools": {}})["tools"]["claude-cli"] = {
            "interactions": n}
    for mes, n in (codex_by_month or {}).items():
        monthly.setdefault(mes, {"tools": {}})["tools"]["codex"] = {
            "interactions": n}
    return {"monthly": monthly}


def _rows(claude_by_month, codex_by_month=None):
    """Filas deduped como las que main() pasa al guard."""
    rows = []
    for mes, n in (claude_by_month or {}).items():
        for i in range(n):
            rows.append({"tool": "claude-cli", "timestamp": f"{mes}-15T10:0{i:02d}:00",
                         "model_raw": "claude-sonnet-4-6", "project": "p"})
    for mes, n in (codex_by_month or {}).items():
        for i in range(n):
            rows.append({"tool": "codex", "timestamp": f"{mes}-15T10:0{i:02d}:00",
                         "model_raw": "gpt-5.4", "project": "p"})
    return rows


class DetectMonthlyRegressionsTest(unittest.TestCase):
    """detect_monthly_regressions(): pura, solo claude-cli, mes actual fuera."""

    def test_sin_caida_no_reporta(self):
        old = _old_report({"2026-04": 100, "2026-05": 200})
        new = _rows({"2026-04": 105, "2026-05": 220})
        self.assertEqual(ut.detect_monthly_regressions(new, old, 20.0), [])

    def test_caida_sobre_umbral_detecta(self):
        old = _old_report({"2026-04": 100, "2026-05": 200})
        new = _rows({"2026-04": 100, "2026-05": 80})  # -60%
        regs = ut.detect_monthly_regressions(new, old, 20.0)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["mes"], "2026-05")
        self.assertEqual(regs[0]["old"], 200)
        self.assertEqual(regs[0]["new"], 80)
        self.assertAlmostEqual(regs[0]["drop_pct"], 60.0)

    def test_caida_bajo_umbral_no_reporta(self):
        old = _old_report({"2026-05": 100})
        new = _rows({"2026-05": 90})  # -10% < 20%
        self.assertEqual(ut.detect_monthly_regressions(new, old, 20.0), [])

    def test_mes_actual_ignorado(self):
        # el mes en curso crece mes a mes; una "caída" a mitad de mes es ruido
        hoy = ut.datetime.now().strftime("%Y-%m")
        old = _old_report({hoy: 500})
        new = _rows({hoy: 10})
        self.assertEqual(ut.detect_monthly_regressions(new, old, 20.0), [])

    def test_solo_claude_cli_otras_tools_ignoradas(self):
        old = _old_report(codex_by_month={"2026-05": 100})
        new = _rows(claude_by_month={}, codex_by_month={"2026-05": 5})
        self.assertEqual(ut.detect_monthly_regressions(new, old, 20.0), [])

    def test_mes_nuevo_no_reporta(self):
        # mes ausente del reporte viejo: no hay contra qué comparar
        old = _old_report({"2026-04": 100})
        new = _rows({"2026-04": 100, "2026-05": 50})
        self.assertEqual(ut.detect_monthly_regressions(new, old, 20.0), [])

    def test_sin_reporte_previo_no_reporta(self):
        new = _rows({"2026-05": 10})
        self.assertEqual(ut.detect_monthly_regressions(new, None, 20.0), [])
        self.assertEqual(ut.detect_monthly_regressions(new, {}, 20.0), [])

    def test_mes_viejo_desaparecido_detecta(self):
        # poda total del mes: new=0 también es caída
        old = _old_report({"2026-05": 100})
        new = _rows({"2026-04": 50})
        regs = ut.detect_monthly_regressions(new, old, 20.0)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["mes"], "2026-05")
        self.assertEqual(regs[0]["new"], 0)


class CheckMonthlyRegressionsWiringTest(unittest.TestCase):
    """check_monthly_regressions(): lee el reporte vigente de out_path,
    respeta --force y aborta con SystemExit cuando hay caídas."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.out_path = Path(self.tmp.name) / "usage_report_v3.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_reporte_previo_pasa(self):
        rows = _rows({"2026-05": 10})
        ut.check_monthly_regressions(rows, self.out_path, force=False)  # no raise

    def test_regresion_aborta(self):
        self.out_path.write_text(json.dumps(
            _old_report({"2026-05": 100, "2026-04": 100})))
        rows = _rows({"2026-04": 100, "2026-05": 10})
        with self.assertRaises(SystemExit) as cm:
            ut.check_monthly_regressions(rows, self.out_path, force=False)
        self.assertEqual(cm.exception.code, 1)

    def test_force_pisando_acepta(self):
        self.out_path.write_text(json.dumps(_old_report({"2026-05": 100})))
        rows = _rows({"2026-05": 10})
        ut.check_monthly_regressions(rows, self.out_path, force=True)  # no raise

    def test_umbral_por_env(self):
        import os
        self.out_path.write_text(json.dumps(_old_report({"2026-05": 100})))
        rows = _rows({"2026-05": 85})  # -15%: pasa con default 20%, aborta con 10
        ut.check_monthly_regressions(rows, self.out_path, force=False)
        os.environ["TRACKER_MONTHLY_DROP_PCT"] = "10"
        try:
            with self.assertRaises(SystemExit):
                ut.check_monthly_regressions(rows, self.out_path, force=False)
        finally:
            del os.environ["TRACKER_MONTHLY_DROP_PCT"]


if __name__ == "__main__":
    unittest.main()
