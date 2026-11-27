#!/usr/bin/env python3
"""viz-fpa.py — Dashboard FP&A de uso de IA (epic coffe-lat).

Implementación descompuesta en módulos viz_fpa_*.py; este archivo re-exporta
toda la API (incluidos los nombres con `_`) y conserva main() y el bloque
--check-docs para que los tests y scripts/update-readme-docs.py sigan
cargándolo de forma standalone por ruta.
"""
import argparse  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import viz_fpa_common
import viz_fpa_summary
import viz_fpa_budget
import viz_fpa_bridge
import viz_fpa_forecast
import viz_fpa_alerts
import viz_fpa_economy
import viz_fpa_rhythm
import viz_fpa_usage
import viz_fpa_trees
import viz_fpa_kpis
import viz_fpa_views
import viz_fpa_spend
import viz_fpa_budget_html
import viz_fpa_dochtml
import viz_fpa_css
import viz_fpa_cards
import viz_fpa_sections
import viz_fpa_usage_html
import viz_fpa_models
import viz_fpa_render


from viz_fpa_common import _link  # noqa: E402

_link(globals())

REPORT = Path("data/usage_report_v3.json")
CONFIG = Path("config/fpa.json")
OUT = Path("data/fpa-dashboard.html")
SCHEMA = Path("specs/usage-report-v3.schema.json")

def _cash_ledger_fig(report):
    """Cifra 'Cash real (ledger)': USD agregado del ledger, n/a si asumido."""
    state = _charges_state(report)
    if state["provenance"] != "reported":
        return "n/a"
    cash_by_ym = _charges_cash_by_ym(report)
    return fmt_usd(round(sum(cash_by_ym.values()), 2))


def expected_doc_figures(report):
    """FPA-143: cifras del reporte con el formato del bloque CHECK-DOCS del
    README (interacciones, proyectos, coste, sesiones, periodo).

    'Costo real' queda acoplado a la suma mensual del tracker (línea
    histórica del README); 'Cash real (ledger)' es la cifra nueva de
    coffe-a31.3 y sale del ledger (*reported*; n/a si el reporte no lo
    trae — se refresca en CRG-F3 / coffe-a31.4)."""
    md = report["metadata"]
    daily = sorted(report.get("daily") or {})
    monthly = report.get("monthly") or {}
    cost_eff = round(sum(mo["cost_effective"] for mo in monthly.values()), 2)
    cost_real = round(sum(mo.get("cost_real", 0) or 0
                          for mo in monthly.values()), 2)
    cash_ledger = _cash_ledger_fig(report)
    sessions = (report.get("sessions") or {}).get("total_sessions") or 0
    return {
        "Interacciones": fmt_int(md["total_interactions"]),
        "Proyectos": fmt_int(len(report.get("projects") or {})),
        "Costo efectivo": fmt_usd(cost_eff),
        "Costo real": fmt_usd(cost_real),
        "Cash real (ledger)": cash_ledger,
        "Sesiones": fmt_int(sessions),
        "Periodo": f"{daily[0]} → {daily[-1]}" if daily else "n/a",
    }


DOC_BEGIN = "<!-- CHECK-DOCS:BEGIN -->"
DOC_END = "<!-- CHECK-DOCS:END -->"


def check_docs(report, readme_path):
    """FPA-143: las cifras del bloque CHECK-DOCS del README deben salir del
    JSON. Devuelve la lista de desvíos ([] = consistente)."""
    try:
        text = Path(readme_path).read_text()
    except OSError as e:
        return [f"no se pudo leer {readme_path}: {e}"]
    if DOC_BEGIN not in text or DOC_END not in text:
        return ["README sin bloque CHECK-DOCS (marcadores "
                f"{DOC_BEGIN} … {DOC_END})"]
    block = text.split(DOC_BEGIN, 1)[1].split(DOC_END, 1)[0]
    expected = expected_doc_figures(report)
    seen = set()
    errors = []
    for line in block.splitlines():
        m = re.match(r"-\s+([^:]+):\s*(.+)$", line.strip())
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        seen.add(key)
        exp = expected.get(key)
        if exp is None:
            errors.append(f"cifra desconocida en README: {key}")
        elif val != exp:
            errors.append(f"{key}: README dice {val}, JSON dice {exp}")
    for key in expected:
        if key not in seen:
            errors.append(f"falta cifra en README: {key}")
    return errors


def check_placeholders(html):
    """FPA-108: problemas de placeholders — resumen vacío o ph sin valor."""
    problems = []
    if html.count('class="headline') < 3:
        problems.append("resumen ejecutivo vacío o incompleto (<3 headlines)")
    for m in re.finditer(r'<(\w+)[^>]*class="[^"]*\bph\b[^"]*"[^>]*>([^<]*)</\1>',
                         html):
        if not m.group(2).strip():
            problems.append(f"placeholder sin valor: <{m.group(1)} class='ph'>")
    return problems


# ======================================================================
# CLI
# ======================================================================

def _print_errors(prefix, errors):
    print(prefix, file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Dashboard de uso y costos de IA (viz-fpa.py)")
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check-docs", action="store_true",
                    help="verificar cifras del README contra el JSON (FPA-143)")
    ap.add_argument("--readme", default="README.md")
    return ap.parse_args(argv)


def _load_cfg(path):
    """Carga y valida el config; None = error ya reportado (FPA-168)."""
    try:
        cfg = fpa_config.load_fpa_config(path)
        cfg_errors = fpa_config.validate_config(cfg)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: config inválida: {e}", file=sys.stderr)
        return None
    if cfg_errors:
        _print_errors("ERROR: config inválida:", cfg_errors)
        return None
    return cfg


def _load_report(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: no se pudo leer el reporte: {e}", file=sys.stderr)
        return None


def _run_check_docs(report, cfg, args):
    """FPA-143/107: consistencia README ↔ JSON + grounding de la guía."""
    errors = check_docs(report, Path(args.readme))
    # coffe-gen.3: grounding de la guía (mapeo + umbrales vs config)
    guide_text = guide.GUIDE_MD.read_text(encoding="utf-8") \
        if guide.GUIDE_MD.exists() else ""
    errors += guide.check_documentation(cfg, guide_text)
    if errors:
        _print_errors("ERROR: check-docs falló:", errors)
        return 1
    print("OK: README consistente con el reporte (FPA-143) · "
          "guía con grounding verificado (mapeo + umbrales)")
    return 0


def _generate(report, cfg, args):
    # FPA-006: schema validation con exit non-zero y campos fallidos listados
    errors = validate_report(report)
    if errors:
        _print_errors(f"ERROR: schema validation falló ({len(errors)} campos):",
                      errors)
        return 1
    html = render_html(report, cfg)
    problems = check_placeholders(html)
    if problems:  # FPA-108
        _print_errors("ERROR: placeholder-check:", problems)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"OK: {out} ({len(html):,} bytes)")
    return 0


def main(argv=None):
    args = _parse_args(argv)
    # FPA-168: targets de CTAs vacíos hacen fallar el generador (se valida
    # antes del reporte: no depende de los datos)
    cfg = _load_cfg(args.config)
    if cfg is None:
        return 1
    report = _load_report(args.report)
    if report is None:
        return 1
    if args.check_docs:
        return _run_check_docs(report, cfg, args)
    return _generate(report, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
