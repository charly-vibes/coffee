#!/usr/bin/env python3
"""tests/test_model_eval.py — casos test-first del ticket coffe-dnx.7."""

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
import importlib.util
_spec = importlib.util.spec_from_file_location("model_eval", REPO / "scripts" / "model-eval.py")
model_eval = importlib.util.module_from_spec(_spec)
sys.modules["model_eval"] = model_eval
_spec.loader.exec_module(model_eval)

TASK = "m-e-01"
MODEL = "glm-5.3-flash"
OTHER_MODEL = "deepseek-v4-flash"


def run_cmd(argv, data_path):
    """Corre main() con data/model_eval.json aislado y bd stubbeado."""
    with mock.patch.object(model_eval, "DATA_PATH", data_path), \
         mock.patch.object(model_eval, "_bd", side_effect=_fake_bd(data_path)):
        rc = model_eval.main(argv)
    return rc, json.loads(Path(data_path).read_text())


def report_at(data_path):
    with mock.patch.object(model_eval, "DATA_PATH", data_path):
        return model_eval.build_report()


_bd_calls = []


def _fake_bd(data_path):
    calls = []
    log = getattr(_fake_bd, "log", None)
    if log is None:
        _fake_bd.log = log = []
    existing = set()

    def fake(args, dry_run=False):
        calls.append(args)
        log.append(args)
        if args[:1] == ["show"]:
            return (0, "found") if args[1] in existing else (1, "not found")
        if args[:1] == ["create"]:
            existing.add(args[1])
            return 0, "created"
        return 0, ""
    fake.calls = calls
    return fake


class StdlibOnlyTest(unittest.TestCase):
    def test_no_non_stdlib_imports(self):
        """El runner es stdlib-only: falla si aparece un import fuera de stdlib."""
        stdlib = getattr(sys, "stdlib_module_names", None)
        tree = ast.parse((REPO / "scripts" / "model-eval.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        imported.discard("model_eval")
        if stdlib:
            self.assertEqual(imported - set(stdlib), set(),
                             f"imports no-stdlib en model-eval.py: {imported - set(stdlib)}")
        else:
            expected = {"argparse", "datetime", "json", "re", "subprocess", "sys", "pathlib", "__future__"}
            self.assertTrue(imported <= expected, f"imports inesperados: {imported - expected}")


class RunCase(unittest.TestCase):
    """Base común: bd stub limpio + data/model_eval.json aislado por test."""

    def setUp(self):
        _fake_bd.log = []
        self.tmp = tempfile.TemporaryDirectory()
        self.data_path = str(Path(self.tmp.name) / "model_eval.json")

    def tearDown(self):
        self.tmp.cleanup()


class RunTest(RunCase):
    def test_run_without_verdict_excluded_from_acceptance(self):
        """Rojo: run sin veredicto ⇒ no entra en las tasas de aceptación."""
        rc, _ = run_cmd(["run", "--task", TASK, "--model", MODEL], self.data_path)
        self.assertEqual(rc, 0)
        rc, _ = run_cmd(["run", "--task", TASK, "--model", OTHER_MODEL,
                         "--verdict", "rejected"], self.data_path)
        self.assertEqual(rc, 0)
        report = report_at(self.data_path)
        entry = report["models"][MODEL]["by_class"]["docs"]
        self.assertEqual(entry["runs_without_verdict"]["value"], 1)
        self.assertEqual(entry["judged"]["value"], 0)
        self.assertIsNone(entry["acceptance_rate"]["value"])  # nunca se asume aceptada
        other = report["models"][OTHER_MODEL]["by_class"]["docs"]
        self.assertEqual(other["acceptance_rate"]["value"], 0.0)

    def test_run_records_all_fields(self):
        """La corrida registra base_commit, harness+versión, coste, latencia,
        tests, intervenciones humanas y veredicto."""
        run_cmd(["run", "--task", TASK, "--model", MODEL,
                 "--cost-usd", "0.5", "--latency-s", "120",
                 "--tests-cmd", "pytest -q", "--tests-exit", "0",
                 "--human-interventions", "1", "--verdict", "accepted"], self.data_path)
        data = json.loads(Path(self.data_path).read_text())
        run = data["runs"][0]
        self.assertEqual(run["base_commit"], "13bc5fff7dc723e65ed669b13b95fa0e1717e10b")
        self.assertEqual(run["harness"], {"name": model_eval.HARNESS_NAME,
                                          "version": model_eval.HARNESS_VERSION})
        self.assertEqual(run["cost_usd"], {"value": 0.5, "provenance": "measured"})
        self.assertEqual(run["latency_s"]["value"], 120)
        self.assertEqual(run["tests_executed"]["exit_code"], 0)
        self.assertEqual(run["human_interventions"]["value"], 1)
        self.assertEqual(run["verdict"], "accepted")


class IncompleteCohortTest(RunCase):
    def test_incomplete_cohort_flagged_not_averaged(self):
        """Corrida con cohorte incompleta ⇒ incomplete: true y sin promedio."""
        rc, _ = run_cmd(["run", "--task", TASK, "--model", MODEL,
                         "--verdict", "accepted"], self.data_path)
        self.assertEqual(rc, 0)
        report = report_at(self.data_path)
        self.assertTrue(report["incomplete"]["value"])
        self.assertNotIn("cohort_average", report)  # no se promedia


class ProvenanceTest(RunCase):
    def setUp(self):
        super().setUp()
        run_cmd(["run", "--task", TASK, "--model", MODEL, "--verdict", "accepted"],
                self.data_path)
        run_cmd(["run", "--task", TASK, "--model", OTHER_MODEL,
                 "--cost-usd", "0.1", "--cost-provenance", "estimated",
                 "--verdict", "rejected"], self.data_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_figure_carries_provenance(self):
        """Cada cifra del reporte lleva measured|estimated|judged; sin él, falla."""
        report = report_at(self.data_path)
        errors = model_eval.validate_report(report)
        self.assertEqual(errors, [])
        figures = []

        def walk(node):
            if isinstance(node, dict):
                if "provenance" in node:
                    figures.append(node)
                else:
                    for v in node.values():
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(report)
        self.assertGreater(len(figures), 5)
        for fig in figures:
            self.assertIn(fig["provenance"], ("measured", "estimated", "judged"))

    def test_cost_provenance_propagates(self):
        """Coste estimado contamina el promedio a provenance estimated."""
        report = report_at(self.data_path)
        entry = report["models"][OTHER_MODEL]["by_class"]["docs"]
        self.assertEqual(entry["mean_cost_usd"]["provenance"], "estimated")

    def test_validate_fails_without_provenance(self):
        report = report_at(self.data_path)
        for entry in report["models"][MODEL]["by_class"].values():
            entry.pop("provenance", None)
            entry["accepted"]["provenance"] = "guessed"
        errors = model_eval.validate_report(report)
        self.assertTrue(errors)


class BlindTest(RunCase):
    def test_judged_by_present_when_blind_broken(self):
        """Si el ciego se rompe (--judged-by), el reporte lo declara."""
        rc, _ = run_cmd(["run", "--task", TASK, "--model", MODEL,
                         "--verdict", "accepted", "--judged-by", "charly"],
                        self.data_path)
        self.assertEqual(rc, 0)
        report = report_at(self.data_path)
        entry = report["models"][MODEL]["by_class"]["docs"]
        self.assertIn("judged_by", entry)
        self.assertIn("charly", entry["judged_by"])

    def test_no_judged_by_when_blind_kept(self):
        run_cmd(["run", "--task", TASK, "--model", MODEL, "--verdict", "accepted"],
                self.data_path)
        report = report_at(self.data_path)
        self.assertNotIn("judged_by", report["models"][MODEL]["by_class"]["docs"])


class BeadTest(RunCase):
    def test_bead_created_once_idempotent(self):
        """Bead eval-* creado una sola vez por corrida (idempotente)."""
        argv = ["run", "--task", TASK, "--model", MODEL, "--verdict", "accepted"]
        run_cmd(argv, self.data_path)
        run_cmd(argv, self.data_path)  # re-ejecución
        creates = [a for a in _fake_bd.log if a[:1] == ["create"]]
        self.assertEqual(len(creates), 1)
        self.assertTrue(creates[0][1].startswith("eval-"))
        data = json.loads(Path(self.data_path).read_text())
        self.assertEqual(len(data["runs"]), 1)  # la corrida no se duplica


if __name__ == "__main__":
    unittest.main()
