#!/usr/bin/env python3
"""test_agent_metrics_contract.py — config, scan de repos, schema del dataset y CLI.

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
SCHEMA = REPO / "specs" / "agent-metrics.schema.json"

spec = importlib.util.spec_from_file_location("agent_tracker", SCRIPT)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

class ReposScanTest(unittest.TestCase):
    def test_raiz_inexistente_se_reporta(self):
        paths, missing = am.find_git_repos(["/no/existe", str(REPO)])
        self.assertEqual(missing, ["/no/existe"])
        self.assertIsInstance(paths, list)


class ReposTest(unittest.TestCase):
    def test_repo_metrics_agrega_dias_y_commits_por_mes(self):
        def run(args, cwd):
            self.assertEqual(args[0], "log")
            return "2026-01-05\n2026-01-05\n2026-02-10\n"

        out = am.repo_metrics([Path("/tmp/x/uno")], run=run, prefixes=["/tmp/x"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "uno")
        self.assertEqual(out[0]["first_commit"], "2026-01-05")
        self.assertEqual(out[0]["days_by_month"], {"2026-01": 1, "2026-02": 1})

    def test_repo_sin_commits_se_omite(self):
        out = am.repo_metrics([Path("/tmp/x/vacio")], run=lambda a, c: "",
                              prefixes=["/tmp/x"])
        self.assertEqual(out, [])

    def test_git_roto_se_omite(self):
        self.assertEqual(am.repo_metrics([Path("/tmp/x/y")],
                                        run=lambda a, c: None,
                                        prefixes=["/tmp/x"]), [])


class ConfigTest(unittest.TestCase):
    def test_config_tiene_defaults_y_bloque_del_sitio(self):
        cfg = am.load_config(REPO / "config" / "fpa.json")
        for key in ("gap_cap_minutes", "retry_window_minutes", "repo_roots",
                    "min_commit_days", "min_returning_months"):
            self.assertIn(key, cfg)
        self.assertEqual(cfg["config_source"], "config/fpa.json")

    def test_config_ausente_usa_defaults(self):
        cfg = am.load_config(REPO / "config" / "no-existe.json")
        self.assertEqual(cfg["gap_cap_minutes"], am.DEFAULTS["gap_cap_minutes"])
        self.assertIsNone(cfg["config_source"])


class SchemaTest(unittest.TestCase):
    def test_schema_declara_las_claves_del_dataset(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for key in ("metadata", "days", "sources", "models", "daily", "tasks",
                    "repos", "months"):
            self.assertIn(key, schema["required"])
        self.assertNotIn("monthly_tasks", schema["required"])
        self.assertIn("sp", schema["properties"]["tasks"]["required"])

    def test_dataset_real_valida_contra_schema(self):
        import sys
        sys.path.insert(0, str(REPO / "scripts"))
        from schema_validate import validate_against_schema
        data = REPO / "data" / "agent_metrics.json"
        if not data.exists():
            self.skipTest("dataset no generado")
        errors = validate_against_schema(
            json.loads(data.read_text(encoding="utf-8")),
            json.loads(SCHEMA.read_text(encoding="utf-8")))
        self.assertEqual(errors, [])


class ValidateCliTest(unittest.TestCase):
    """Escenario del spec: dataset inválido → --validate sale ≠ 0 (EDGE-003)."""

    def test_dataset_invalido_sale_con_error(self):
        import subprocess
        import tempfile
        data = json.loads((REPO / "data" / "agent_metrics.json").read_text(
            encoding="utf-8"))
        del data["daily"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            path = fh.name
        try:
            proc = subprocess.run(
                ["python3", str(SCRIPT), "--validate-only", path],
                capture_output=True, text=True, timeout=120)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("schema:", proc.stderr)


class SchemaBumpTest(unittest.TestCase):
    """Schema bump coffe-dnx.3: sid/cost/tokens en tasks, sessions[] arriba y
    el alcance del gate en coverage.cost_parity. Opcionales: datasets
    anteriores al cambio no los traen (regen pendiente)."""

    def test_schema_declara_las_claves_nuevas(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        tasks_props = schema["properties"]["tasks"]["properties"]
        for key in ("sid", "cost", "tokens"):
            self.assertIn(key, tasks_props)
        sessions = schema["properties"]["sessions"]
        self.assertEqual(sessions["type"], "array")
        self.assertEqual(sessions["items"]["required"],
                         ["source", "project", "session_id"])
        cov = schema["properties"]["metadata"]["properties"]["coverage"]["items"]
        self.assertIn("cost_parity", cov["properties"])


if __name__ == "__main__":
    unittest.main()
