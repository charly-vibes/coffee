#!/usr/bin/env python3
"""test_agent_metrics_beads.py — índice de beads: métricas, privacidad y schema.

Archivo derivado de test_agent_metrics.py (split por tema, coffe-kav.4).
Cada archivo del split es autocontenido: importa el guion como módulo y
define sus propios fixtures.
"""

import importlib.util
import json
import tempfile

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent-metrics-tracker.py"

spec = importlib.util.spec_from_file_location("agent_tracker", SCRIPT)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

class BeadIndexFixtureTest(unittest.TestCase):
    """coffe-dnx.4: fixture de 2 repos — uno con lead_time medible y un bead
    sin started_at, otro fuera del filtro charly → excluded."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.prefixes = [str(base)]

        def repo(name, issues):
            d = base / name
            (d / ".beads").mkdir(parents=True)
            (d / ".beads" / "issues.jsonl").write_text(
                "".join(json.dumps(i) + "\n" for i in issues),
                encoding="utf-8")
            return d / ".beads" / "issues.jsonl"

        repo("charly/uno", [{
            "_type": "issue", "id": "bead-1a2",
            "title": "SECRETO-title", "description": "SECRETO-desc",
            "created_at": "2026-05-01T10:00:00Z",
            "started_at": "2026-05-01T10:00:00Z",
            "closed_at": "2026-05-01T11:30:00Z", "status": "closed",
        }])
        repo("sk/dos", [{
            "_type": "issue", "id": "bead-3b4",
            "notes": "SECRETO-notes",
            "created_at": "2026-05-02T10:00:00Z",
            "closed_at": "2026-05-02T12:00:00Z", "status": "closed",
        }])
        repo("donp/otros", [{
            "_type": "issue", "id": "bead-9z9",
            "design": "SECRETO-design",
            "created_at": "2026-05-03T10:00:00Z", "status": "open",
        }])
        cls.doc = am.build_bead_index([base / "charly/uno", base / "sk/dos",
                                       base / "donp/otros"],
                                      prefixes=cls.prefixes)
        cls.by = {r["name"]: r for r in cls.doc["repos"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lead_time_y_completeness(self):
        row = self.by["charly/uno"]["months"]["2026-05"]
        self.assertEqual(row["created"], 1)
        self.assertEqual(row["started"], 1)
        self.assertEqual(row["closed"], 1)
        self.assertEqual(row["lead_time_p50_min"], 90)
        self.assertEqual(row["lead_time_n"], 1)
        self.assertEqual(row["completeness"], 1.0)

    def test_bead_sin_started_at_no_entra_en_lead_time(self):
        row = self.by["sk/dos"]["months"]["2026-05"]
        self.assertEqual(row["closed"], 1)
        self.assertEqual(row["lead_time_n"], 0)
        self.assertIsNone(row["lead_time_p50_min"])
        self.assertEqual(row["completeness"], 0.0)  # lead_time_n / closed

    def test_reopened_es_null_con_razon(self):
        for repo in self.doc["repos"]:
            for row in repo["months"].values():
                self.assertIsNone(row["reopened"])
        self.assertEqual(self.doc["metadata"]["reasons"]["reopened"],
                         "no event log in no-db mode")

    def test_repo_fuera_de_is_charly_va_a_excluded(self):
        self.assertNotIn("donp/otros", self.by)
        ex = {e["name"]: e["issues"] for e in self.doc["excluded"]}
        self.assertEqual(ex.get("donp/otros"), 1)


class BeadPrivacyGuardTest(unittest.TestCase):
    """CLRT-003: title/description/notes/design de issues no se publican
    (los ids sí: son opacos). El guard falla si el artefacto los trae."""

    def test_guard_detecta_claves_de_texto(self):
        base = {"repos": [], "excluded": [], "metadata": {}}
        with_title = {**base, "repos": [
            {"name": "charly/x", "months": {"2026-05": {"title": "x"}}}]}
        self.assertTrue(am.bead_privacy_errors(with_title))
        self.assertTrue(am.bead_privacy_errors(
            {**base, "excluded": [{"name": "d/y", "issues": 1,
                                   "description": "x"}]}))
        self.assertEqual(am.bead_privacy_errors(base), [])

    def test_artefacto_real_sin_texto_de_issues(self):
        data = REPO / "data" / "bead_index.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        doc = json.loads(data.read_text(encoding="utf-8"))
        self.assertEqual(am.bead_privacy_errors(doc), [])


class BeadIndexSchemaTest(unittest.TestCase):
    """Schema del artefacto + CLI --validate-bead."""

    def test_schema_declara_las_claves_del_mes(self):
        schema = json.loads((REPO / "specs" / "bead-index.schema.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(schema["required"], ["metadata", "repos", "excluded"])
        month = schema["definitions"]["month"]
        for key in ("created", "started", "closed", "lead_time_p50_min",
                    "lead_time_n", "completeness", "reopened"):
            self.assertIn(key, month["required"])
        self.assertEqual(month["properties"]["reopened"]["type"], "null")

    def test_cli_valida_artefacto_contra_schema(self):
        import subprocess
        import tempfile
        data = REPO / "data" / "bead_index.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        self.assertEqual(subprocess.run(
            ["python3", str(SCRIPT), "--validate-bead", str(data)],
            capture_output=True, text=True, timeout=120).returncode, 0)
        bad = json.loads(data.read_text(encoding="utf-8"))
        del bad["excluded"]
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            json.dump(bad, fh)
            path = fh.name
        try:
            proc = subprocess.run(
                ["python3", str(SCRIPT), "--validate-bead", path],
                capture_output=True, text=True, timeout=120)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("schema:", proc.stderr)


if __name__ == "__main__":
    unittest.main()
