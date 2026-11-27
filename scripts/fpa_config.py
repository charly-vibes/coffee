#!/usr/bin/env python3
"""
fpa_config.py — carga y validación de config/fpa.json (F0 del epic coffe-lat)

Responsabilidades:
- Cargar el config único del dashboard FP&A (FPA-015/016/018/050/051/088/037/
  111/122/168/162/178) y validarlo antes de generar el dashboard.
- classify_project(): taxonomía FPA-018 — override gana, luego la primera regla
  cuyo patrón matchea el nombre del proyecto, si no el fallback ("Unclassified").
- rates_for(): pricing FPA-016 versionado por fecha efectiva — la última versión
  con effective <= fecha; si no hay ninguna, default_rates.
- energy_for(): coeficientes de energía (kWh) versionados por fecha — igual
  contrato que rates_for pero SIN fallback silencioso: None si el `when` es
  anterior a toda versión (el tracker aborta loud, coffe-7mj.1).
- validate_config() exige el bloque energy_coefficients (coffe-7mj.1): tiers de
  J/token entregado, default_tier, cache_read_energy_factor y versions
  consistentes (validadores por sección en fpa_config_rules.py).

Rationale: los valores de coste son *assumed* (FPA-003); centralizarlos en un
JSON versionado evita precios hardcodeados (hallazgo FPA-015/016 de la spec).
Stdlib-only, igual que el resto del repo.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

# Carga standalone (los tests cargan este archivo con spec_from_file_location
# sin paquete): hacer importable el módulo hermano de validaciones.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fpa_config_rules import REQUIRED_SECTIONS, validate_config  # noqa: E402

DEFAULT_CONFIG_PATH = Path("config") / "fpa.json"


def load_fpa_config(path=DEFAULT_CONFIG_PATH):
    """Cargar config/fpa.json. Lanza FileNotFoundError si no existe."""
    return json.loads(Path(path).read_text())


def classify_project(cfg, project):
    """FPA-018: categoría del proyecto según taxonomía del config."""
    taxonomy = cfg["taxonomy"]
    overrides = taxonomy.get("overrides", {})
    if project in overrides:
        return overrides[project]
    for rule in taxonomy.get("rules", []):
        if re.search(rule["match"], project):
            return rule["category"]
    return taxonomy.get("fallback", "Unclassified")


def rates_for(cfg, when):
    """FPA-016: rates vigentes para `when` (date) según versiones por effective.

    La última versión con effective <= when gana; si no hay ninguna
    (when anterior a todas), se usa default_rates.
    """
    pricing = cfg["model_pricing"]
    applicable = [
        v for v in pricing.get("versions", [])
        if date.fromisoformat(v["effective"]) <= when
    ]
    if not applicable:
        return pricing["default_rates"]
    return max(applicable, key=lambda v: v["effective"])["rates"]


def energy_for(cfg, when):
    """coffe-7mj.1: versión de energy_coefficients vigente para `when` (date).

    Igual contrato que rates_for PERO sin fallback silencioso: devuelve None
    si `when` es anterior a toda versión — el tracker aborta loud nombrando
    el mes (el agregado mensual de tokens no puede partirse entre versiones,
    así que la selección es por primer día del bucket).
    """
    energy = cfg["energy_coefficients"]
    applicable = [
        v for v in energy.get("versions", [])
        if date.fromisoformat(v["effective"]) <= when
    ]
    if not applicable:
        return None
    return max(applicable, key=lambda v: v["effective"])


if __name__ == "__main__":
    config = load_fpa_config()
    problems = validate_config(config)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}")
        raise SystemExit(1)
    print("config/fpa.json OK")
