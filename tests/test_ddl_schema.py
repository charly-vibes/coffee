#!/usr/bin/env python3
"""Tests de schema/validación del reporte ddl-effect en scripts/ddl-effect-tracker.py.

4.5 (coffe-nea): specs/ddl-effect.schema.json + --validate. Solo stdlib.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)


class SchemaValidateTest(unittest.TestCase):
    """4.5 (coffe-nea): specs/ddl-effect.schema.json + --validate."""

    @classmethod
    def setUpClass(cls):
        # coffe-q9z: main() regenera con insumos reales (walk/git/gh) — el
        # fixture de schema ahora es assemble_report con sintéticos, hermético
        cls.report = ddl.assemble_report({
            "usage_report": {"project_monthly": {
                "charly-wai": {"2026-08": {"interactions": 10}}}},
            "sessions": [{"repo": "wai", "uses": {}, "events": []}],
            "git": {"wai": {"commits": [{"repo": "wai",
                                          "date": "2026-06-01T00:00:00Z",
                                          "message": "ddl init",
                                          "files": [".ddl/config.json"]}],
                            "releases": [], "has_ddl_now": True}},
            "topics": {"wai": {"topic_ddl": True}},
            "ddl_status": {"present": list(ddl.MANAGED_TOOLS),
                           "healthy": list(ddl.MANAGED_TOOLS)},
            "now": "2026-08-10T00:00:00Z",
        })

    def _with_monthly(self, monthly):
        """Copia profunda del reporte con monthly[] reemplazado."""
        bad = json.loads(json.dumps(self.report))
        bad["monthly"] = monthly
        return bad

    def test_schema_existe_y_es_draft07(self):
        schema = json.loads(ddl.DDL_SCHEMA.read_text())
        self.assertEqual(schema["$schema"],
                         "http://json-schema.org/draft-07/schema#")

    def test_esqueleto_valida(self):
        self.assertEqual(ddl.validate_report(self.report), [])

    def test_monthly_corrupto_falla(self):
        casos = [
            ("fila_sin_src",
             [{"repo": "wai", "month": "2026-08", "dose": 0.5}], "src"),
            ("src_tag_invalido",
             [{"repo": "wai", "month": "2026-08", "dose": 0.5,
               "src": {"dose": "guessed"}}], "guessed"),
            ("month_patro",
             [{"repo": "wai", "month": "agosto", "dose": 0.5,
               "src": {"dose": "reported"}}], "month"),
        ]
        for nombre, monthly, fragmento in casos:
            with self.subTest(caso=nombre):
                errors = ddl.validate_report(self._with_monthly(monthly))
                self.assertTrue(any(fragmento in e for e in errors))

    def test_validate_cli_salida_corrupta_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            corrupt = Path(d) / "corrupt.json"
            corrupt.write_text(json.dumps({"metadata": {}}))
            self.assertEqual(ddl.main(["--validate", str(corrupt)]), 1)

    def test_validate_cli_salida_valida_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "ok.json"
            out.write_text(json.dumps(self.report))
            self.assertEqual(ddl.main(["--validate", str(out)]), 0)

    def test_validador_reutilizado_no_copiado(self):
        import schema_validate
        self.assertIs(ddl.validate_against_schema,
                      schema_validate.validate_against_schema)


if __name__ == "__main__":
    unittest.main(verbosity=1)
