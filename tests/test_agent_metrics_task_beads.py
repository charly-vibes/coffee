#!/usr/bin/env python3
"""test_agent_metrics_task_beads.py — atribución tarea↔bead y coste por bead.

Archivo derivado de test_agent_metrics.py (split por tema, coffe-kav.4).
Cada archivo del split es autocontenido: importa el guion como módulo y
define sus propios fixtures.
"""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent-metrics-tracker.py"

spec = importlib.util.spec_from_file_location("agent_tracker", SCRIPT)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def at(seconds, role="assistant", **kw):
    turn = {"ts": T0 + timedelta(seconds=seconds), "role": role}
    turn.update(kw)
    return turn

class TaskBeadsTest(unittest.TestCase):
    """coffe-dnx.5: atribución tarea↔bead y fórmula de coste por bead."""

    SES = {"source": "pi", "project": "coffee", "session_id": "s1"}

    def bead(self, bid, started=-3600, closed=3600):
        return {"id": bid, "repo": "charly/coffee", "path": Path("/x/coffee"),
                "started_at": T0 + timedelta(seconds=started),
                "closed_at": (T0 + timedelta(seconds=closed)) if closed else None}

    def tasks_with_prompt(self, *prompts, session_id="s1"):
        turns, ts = [], 0
        ses = dict(self.SES, session_id=session_id)
        for p in prompts:
            turns.append(at(ts, "user_prompt", text=p, ses=ses))
            turns.append(at(ts + 10, ses=ses))
            ts += 30
        tasks = am.build_tasks(turns)
        am._sessions_from_tasks(tasks)
        return tasks

    def test_prompt_con_id_es_explicit_1(self):
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("arregla coffe-abc ya"),
            [self.bead("coffe-abc")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["method"], "explicit")
        self.assertEqual(rows[0]["confidence"], 1.0)
        self.assertEqual(rows[0]["bead"], "coffe-abc")

    def test_temporal_dentro_de_ventana_nosuma_coste(self):
        tasks = self.tasks_with_prompt("sin ids acá")
        rows, sessions = am.attribute_task_beads(
            tasks, [self.bead("coffe-abc")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["method"], "temporal")
        self.assertLessEqual(rows[0]["confidence"], 0.5)
        self.assertEqual(rows[0]["window"]["started_at"],
                         (T0 - timedelta(seconds=3600)).isoformat())
        per_bead, _ = am.compute_bead_costs(
            rows, sessions, [{"session_id": "s1", "cost_effective": 7.0}])
        # temporal NO contribuye al coste (solo n_temporal)
        self.assertEqual(per_bead["coffe-abc"]["cost_per_bead"], 0.0)
        self.assertEqual(per_bead["coffe-abc"]["n_sessions"], 0)
        self.assertEqual(per_bead["coffe-abc"]["n_temporal"], 1)

    def test_temporal_emitido_como_explicit_falla(self):
        # guarda: una fila temporal lleva method temporal y confianza <= 0.5;
        # emitirla como explicit (confidence 1.0) rompe ambos asserts
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("sin ids acá"), [self.bead("coffe-abc")])
        self.assertTrue(rows)
        for r in rows:
            self.assertNotEqual(r["method"], "explicit")
            self.assertLessEqual(r["confidence"], 0.5)

    def test_dos_sesiones_explicit_suman_coste_exacto(self):
        tasks = (self.tasks_with_prompt("toca coffe-abc", session_id="s1")
                 + self.tasks_with_prompt("toca coffe-abc", session_id="s2"))
        rows, sessions = am.attribute_task_beads(tasks, [self.bead("coffe-abc")])
        per_bead, _ = am.compute_bead_costs(rows, sessions, [
            {"session_id": "s1", "cost_effective": 2.0},
            {"session_id": "s2", "cost_effective": 3.5}])
        self.assertEqual(per_bead["coffe-abc"]["n_sessions"], 2)
        self.assertAlmostEqual(per_bead["coffe-abc"]["cost_per_bead"], 5.5)

    def test_sesion_en_dos_beads_se_reparte_split(self):
        tasks = self.tasks_with_prompt("coffe-abc y coffe-def")
        rows, sessions = am.attribute_task_beads(
            tasks, [self.bead("coffe-abc"), self.bead("coffe-def")])
        per_bead, _ = am.compute_bead_costs(
            rows, sessions, [{"session_id": "s1", "cost_effective": 10.0}])
        splits = [r for r in rows if r.get("split")]
        self.assertEqual(len(splits), 2)
        self.assertAlmostEqual(sum(per_bead[b]["cost_per_bead"]
                                   for b in ("coffe-abc", "coffe-def")),
                               10.0, places=6)
        self.assertAlmostEqual(per_bead["coffe-abc"]["cost_per_bead"], 5.0)
        self.assertAlmostEqual(per_bead["coffe-def"]["cost_per_bead"], 5.0)

    def test_sin_beads_la_tarea_no_aparece(self):
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("nada que ver"), [])
        self.assertEqual(rows, [])

    def test_doc_completo_valida_contra_schema(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "charly" / "coffee"
            (repo / ".beads").mkdir(parents=True)
            issues = [{"id": "coffe-abc",
                       "started_at": (T0 - timedelta(hours=1)).isoformat(),
                       "closed_at": (T0 + timedelta(hours=1)).isoformat()}]
            (repo / ".beads" / "issues.jsonl").write_text(
                "\n".join(json.dumps(i) for i in issues), encoding="utf-8")
            tasks = self.tasks_with_prompt("implemento coffe-abc")
            spend = [{"session_id": "s1", "cost_effective": 4.0}]
            doc = am.build_task_beads_doc(
                tasks, [repo], prefixes=[tmp], session_spend=spend,
                run=lambda args, cwd: "")
        self.assertEqual(len(doc["task_beads"]), 1)
        self.assertEqual(doc["task_beads"][0]["method"], "explicit")
        self.assertEqual(doc["beads"][0]["cost_per_bead"], 4.0)
        self.assertEqual(doc["n_beads_sin_coste"], 0)
        self.assertAlmostEqual(doc["cost_per_bead_p50"], 4.0)
        self.assertEqual(am._task_beads_schema_errors(doc), [])

    def test_cli_valida_task_beads_contra_schema(self):
        import subprocess
        data = REPO / "data" / "task_beads.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        self.assertEqual(subprocess.run(
            ["python3", str(SCRIPT), "--validate-task-beads", str(data)],
            capture_output=True, text=True, timeout=120).returncode, 0)


if __name__ == "__main__":
    unittest.main()
