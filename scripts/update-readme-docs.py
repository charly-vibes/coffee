#!/usr/bin/env python3
"""Refresca el bloque CHECK-DOCS del README con las cifras del reporte actual.

Reutiliza `expected_doc_figures()` de `scripts/viz-fpa.py` vía importlib —
misma función que valida `--check-docs` (FPA-143), así el bloque queda
siempre en sync con `data/usage_report_v3.json` sin edición manual.

Lo usa el job nocturno (`scripts/nightly-update.sh`); también a mano tras
regenerar el reporte. Sin argumentos: escribe README.md in-place.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
REPORT = ROOT / "data" / "usage_report_v3.json"


def load_viz_fpa():
    """Importa scripts/viz-fpa.py (nombre con guion, no importable directo)."""
    spec = importlib.util.spec_from_file_location(
        "viz_fpa", ROOT / "scripts" / "viz-fpa.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    report = json.loads(REPORT.read_text())
    viz_fpa = load_viz_fpa()
    expected = viz_fpa.expected_doc_figures(report)

    block = "\n" + "".join(f"- {k}: {v}\n" for k, v in expected.items())
    text = README.read_text()
    if viz_fpa.DOC_BEGIN not in text or viz_fpa.DOC_END not in text:
        print("README sin bloque CHECK-DOCS; no se toca", file=sys.stderr)
        return 1
    new_text = re.sub(
        re.escape(viz_fpa.DOC_BEGIN) + r".*?" + re.escape(viz_fpa.DOC_END),
        lambda m: viz_fpa.DOC_BEGIN + block + viz_fpa.DOC_END,
        text, flags=re.S)
    if new_text != text:
        README.write_text(new_text)
        print("README: bloque CHECK-DOCS actualizado")
    else:
        print("README: bloque CHECK-DOCS ya estaba en sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())