#!/usr/bin/env python3
"""model-cost-estimate-v0.py — estimador rápido de coste por unidad aceptada (spike).

Propósito:
- Responder "¿cuánto cuesta una unidad de trabajo aceptada por modelo?" sin
  implementar los hijos de coffe-dnx (.1–.9): cruza session_spend (ventana +
  proyecto + modelo + coste) con señales *locales* de resultado (git log y
  .beads/issues.jsonl por repo) y con los proxies ya calculados en
  agent_metrics.json.

Responsabilidades:
- lane_for(): separar carriles de coste (real pay-per-token vs notional de
  suscripción) usando la MISMA regla que scripts/usage-tracker.py: una fuente
  está en config/fpa.json → subscriptions si su dinero no sale del ledger.
- assign_units(): acreditar cada evento (bead cerrado / commit) a UNA sola sesión
  — la de inicio más cercano entre las que contienen el evento (CORR-002).
- bounded_window(): acotar sesiones de reloj largo y marcarlas ambiguas
  (CORR-004) en lugar de barrer los commits de las sesiones concurrentes.
- pctl()/wilson_lower()/cluster_bootstrap_ci(): cuantiles, cota inferior de
  Wilson y bootstrap por cluster (repo día) — la sesión está anidada, así que un
  iid bootstrap angosta los intervalos de más (EXCL-002).
- beads_freshness(): distinguir "export viejo" de "no hubo cierres" (EDGE-001).
- build_rows()/aggregate_report(): filas por (modelo, carril) con n, yield,
  p50/p99 y banda de sensibilidad, más las filas declaradas `unattributed`
  (EDGE-002) y el resumen de lo excluido (`mixed`, carril unknown).

Rationale: el reporte ya trae coste por modelo y por sesión; lo que falta es la
clave sesión→resultado. Mientras .3/.4/.5 no existan, la unión se hace contra
git y beads del propio repo porque es gratis y local (sin GITHUB_TOKEN). El
script es deliberadamente descartable: NO es el runner de .7 ni el scorecard de
.8; su salida es una estimación de orden de magnitud con procedencia declarada.

Uso:
  python3 scripts/model-cost-estimate-v0.py run --since 2026-09-01 \
      --unit bead --markdown /tmp/est.md --json /tmp/est.json

Implementación: constants + estadística pura + render en scripts/model_cost_core.py;
recolección (git/beads) + filas + agregación en scripts/model_cost_pipeline.py.
Este módulo re-exporta la API completa (los tests la consumen de pie, vía
importlib spec_from_file_location sobre este archivo).
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_cost_core import (          # noqa: E402,F401 — re-export para tests/CLI
    CONFIG,
    EXCLUDED_MODELS,
    KNOWN_PAY_PER_TOKEN,
    REPORT,
    REPO,
    THRESHOLDS,
    assign_units,
    beads_freshness,
    bounded_window,
    cluster_bootstrap_ci,
    is_comparable,
    lane_for,
    pctl,
    render_markdown,
    wilson_lower,
)
from model_cost_pipeline import (      # noqa: E402,F401 — re-export para tests/CLI
    aggregate_report,
    beads_closes,
    build_rows,
    collect,
    commit_times,
    last_commit_ts,
    load_sessions,
    local_day,
    resolve_projects,
)


def cmd_run(args):
    """Corre la estimación end-to-end y escribe/renderiza el payload."""
    report = Path(args.report).expanduser()
    sessions, units, projects, subs, meta = collect(
        report, os.path.expanduser(args.root), args.unit,
        (args.since, args.until, args.tz))
    rows = build_rows(sessions, units, projects, subs)
    payload = aggregate_report(sessions, rows, meta)
    md = render_markdown(payload)
    if args.markdown:
        Path(args.markdown).write_text(md)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=1, default=str))
    print(md)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="genera la estimación")
    run.add_argument("--report", default=str(REPORT))
    run.add_argument("--root", default="~/para/areas/dev/gh")
    run.add_argument("--since", default=None)
    run.add_argument("--until", default=None)
    run.add_argument("--unit", choices=("bead", "commit"), default="bead")
    run.add_argument("--tz", type=int, default=-3)
    run.add_argument("--markdown", default=None)
    run.add_argument("--json", dest="json", default=None)
    run.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
