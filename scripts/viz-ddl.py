#!/usr/bin/env python3
"""
viz-ddl.py — dashboard del análisis ddl-effect (add-ddl-effectiveness-analysis).

Lee `data/ddl_effect.json` (schema: `specs/ddl-effect.schema.json`) y genera
`data/ddl-dashboard.html`: único HTML autocontenido, SVG inline, sin recursos
externos, tema compartido (`scripts/site_theme.py`), 6 vistas con deep-link
por hash, export CSV/SVG y `--validate` (coffe-31l esqueleto; las vistas se
completan en coffe-het/470/46h, la banda de disclosure en coffe-8yf, el smoke
Chromium en coffe-x1s).

Stdlib-only, igual que el resto del repo. Descompuesto en módulos viz_ddl_*
(constantes/helpers, vistas, armado del HTML); este archivo conserva la CLI y
re-exporta todos los nombres que tests y trackers consumen.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import viz_ddl_common  # noqa: E402
import viz_ddl_html  # noqa: E402
import viz_ddl_views  # noqa: E402
from viz_ddl_common import (  # noqa: E402,F401
    DDL_SCHEMA, SKELETON_REPORT, VIEWS, VIEW_TITLES,
    _cell, _estimator_table, _fmt, _monthly_rows,
    did_rows_html, esc, fig, its_rows_html, placeholder, prov_tag,
)
from viz_ddl_html import (  # noqa: E402,F401
    CSS_EXTRA, DISCLOSURE, JS,
    _tab_html, add_export_buttons, build_html, load_report, tabs_html,
    validate_report,
)
from viz_ddl_views import (  # noqa: E402,F401
    _ci_cell, _dose_block, _recovery_cell, _scatter_svg,
    sections_html, view_adopcion, view_calidad, view_dosis_respuesta,
    view_errores, view_html, view_lazo_feedback, view_pre_post,
)

__all__ = [  # re-export: nombres públicos que tests/consumidores esperan acá
    "DDL_SCHEMA", "SKELETON_REPORT", "VIEWS", "VIEW_TITLES",
    "DISCLOSURE", "CSS_EXTRA", "JS",
    "add_export_buttons", "build_html", "did_rows_html", "esc", "fig",
    "its_rows_html", "load_report", "main", "placeholder", "prov_tag",
    "sections_html", "tabs_html", "validate_report", "view_adopcion",
    "view_calidad", "view_dosis_respuesta", "view_errores", "view_html",
    "view_lazo_feedback", "view_pre_post",
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default="data/ddl_effect.json")
    ap.add_argument("--output", default="data/ddl-dashboard.html")
    ap.add_argument("--validate", metavar="ARCHIVO", default=None,
                    help="valida el JSON contra specs/ddl-effect.schema.json "
                         "y sale (0/1) sin generar HTML")
    args = ap.parse_args(argv)

    if args.validate:
        errors = validate_report(load_report(args.validate))
        if errors:
            print("viz-ddl: schema inválido:", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
            return 1
        print(f"viz-ddl: {args.validate} válido contra {DDL_SCHEMA.name}")
        return 0

    report = load_report(args.report)
    errors = validate_report(report)
    if errors:  # fail loud: el dashboard no renderiza datos inválidos
        ap.error(f"{args.report} inválido contra {DDL_SCHEMA.name}: "
                 + "; ".join(errors))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(report))
    print(f"viz-ddl: {out} generado ({len(VIEWS)} vistas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
