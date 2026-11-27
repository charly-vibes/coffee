#!/usr/bin/env python3
"""Docs con test: el README documenta el contrato de data/ddl_effect.json (coffe-zx5)."""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class ReadmeDdlEffectDocsTest(unittest.TestCase):
    """coffe-zx5: el README documenta el contrato de data/ddl_effect.json —
    layout, caches (gh/terapia), config (repo_aliases, umbrales, TTLs) y gaps.
    Docs con test: si el README deja de nombrar una pieza del contrato, falla.
    """

    REQUERIDO = (
        # layout del reporte
        "data/ddl_effect.json",
        "specs/ddl-effect.schema.json",
        "provenance",  # reported/derived/cached/missing en toda cifra
        # caches y TTLs
        "data/cache/terapia_errors.json",
        "TTL",
        # config
        "ddl_effect.repo_aliases",
        # umbrales y determinismo
        "seed_bootstrap",
        # gaps declarados
        "n < 3",
    )

    def test_readme_documenta_contrato_ddl_effect(self):
        texto = (REPO / "README.md").read_text(encoding="utf-8")
        faltantes = [a for a in self.REQUERIDO if a not in texto]
        self.assertEqual(faltantes, [],
                         "README no documenta: " + ", ".join(faltantes))


if __name__ == "__main__":
    unittest.main(verbosity=1)
