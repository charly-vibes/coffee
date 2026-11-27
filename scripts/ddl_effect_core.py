"""Constantes compartidas de ddl-effect-tracker (facade: scripts/ddl-effect-tracker.py)."""
from __future__ import annotations

from pathlib import Path


# 13 herramientas del registry gestionado (MANAGED-TOOLS en dulce-de-leche/README.md;
# drift-tested allá por tests/tool_registry_drift.rs — no duplicamos el check aquí).
MANAGED_TOOLS = (
    "wai", "testaruda",          # Core
    "turu", "dont", "ah",        # Recommended
    "pretender", "vampiro", "incitaciones", "bd", "openspec", "specodelic",
    "funkalization", "wild",     # Extension
)
assert len(MANAGED_TOOLS) == 13, "registry gestionado incompleto"

# Repos canónicos de la flota que evalúa este análisis.
FLEET_REPOS = (
    "wai", "testaruda", "turu", "dont", "ah", "pretender", "vampiro",
    "incitaciones", "bd", "openspec", "specodelic", "funkalization", "wild",
    "dulce-de-leche", "coffee",
)
DDL_OUTLIER_REPO = "charly-dulce-de-leche"

DDL_SCHEMA = (Path(__file__).resolve().parent.parent / "specs"
              / "ddl-effect.schema.json")
