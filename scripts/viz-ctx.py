#!/usr/bin/env python3
"""
viz-ctx.py — dashboard de contexto (epic add-context-dashboard, ctx-viz)

Purpose: renderizar la página nueva data/context-dashboard.html desde la
sección context_metrics de data/usage_report_v3.json (Pi-only).

Responsibilities:
- build_model(report): pre-cálculo en Python (FPA-145: el JS solo
  re-escala — aquí, ni JS: SVG estático) — histograma del pico de
  contexto por sesión sobre el SET LIMPIO (turns>0, sin patológicas),
  banda de percentiles p50/p75/p90/p95/p99, conteo de excluidas/stubs
  junto al título, umbral patológico de config, tags *assumed* del factor
  chars/chars_per_token con su bias y ratio de validación
  (viz_ctx_model.py)
- render(model): HTML autocontenido stdlib-only, SVG inline, tema
  compartido scripts/site_theme.py — sin CDN ni librerías externas
  (anti-goal FPA-145), header con el alcance Pi-only declarado
  (viz_ctx_views.py + viz_ctx_page.py)
- main(): lee el reporte y escribe el HTML; reporte sin sección o
  unavailable → placeholder honesto con la razón (nunca vacío sin valor,
  patrón FPA-108); --check-docs verifica el bloque CTX-DOCS del README
  contra el JSON (coffe-ruk 3.2, viz_ctx_docs.py)

Rationale: página nueva (decisión D1 del design.md) — las métricas de
contexto son por-sesión y Pi-only, granularidad distinta a fpa-dashboard;
el repo ya tiene el patrón viz secundaria que lee el mismo reporte
(viz-gantt.py). El cálculo vive en el tracker (reducer puro); esta viz
solo pre-calcula presentación y renderiza. Split coffe-v5f: vistas,
SVG, página y CTX-DOCS viven en viz_ctx_*.py (mismo patrón que
viz-fpa.py); este archivo mantiene main(), CLI y las re-exportaciones
que usan tests y update-readme-docs.py.
"""

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402,F401
from viz_ctx_docs import CTX_DOC_BEGIN, CTX_DOC_END  # noqa: E402,F401
from viz_ctx_docs import check_docs_ctx, expected_ctx_figures  # noqa: E402
from viz_ctx_model import build_model  # noqa: E402
from viz_ctx_page import full as _page_full, placeholder as _page_placeholder
from viz_ctx_views import sections as _sections  # noqa: E402

REPORT = Path("data/usage_report_v3.json")
OUT = Path("data/context-dashboard.html")


def render(model):
    """HTML autocontenido (sin dependencias externas) con SVG inline."""
    scope = html.escape(model.get("scope_note", ""))
    if not model.get("available"):
        return _page_placeholder(model)
    return _page_full(_sections(model), scope)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Dashboard de contexto de sesiones Pi (viz-ctx.py)")
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument(
        "--check-docs", action="store_true",
        help="verificar cifras CTX-DOCS del README contra el JSON (coffe-ruk)")
    ap.add_argument("--readme", default="README.md")
    args = ap.parse_args(argv)

    try:
        report = json.loads(Path(args.report).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: no se pudo leer el reporte: {e}", file=sys.stderr)
        return 1

    if args.check_docs:
        errors = check_docs_ctx(report, args.readme)
        if errors:
            print("ERROR: README desviado del JSON (CTX-DOCS):",
                  file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("OK: README consistente con context_metrics (CTX-DOCS)")
        return 0

    model = build_model(report)
    Path(args.out).write_text(render(model))
    if not model.get("available"):
        print(f"viz-ctx: {args.out} — placeholder: {model.get('reason')}")
        return
    d = model["dist"]
    print(f"viz-ctx: {args.out} — {d['n_clean']} sesiones "
          f"limpias, {d['excluded_pathological']} excluidas, "
          f"{d['stubs']} stubs")


if __name__ == "__main__":
    main()
