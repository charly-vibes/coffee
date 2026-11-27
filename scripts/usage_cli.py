#!/usr/bin/env python3
"""CLI: argumentos, guards, impresión y main() (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _main_args():
    ap = argparse.ArgumentParser(description="Extractor de uso de IA (filtro de proyectos configurable)")
    ap.add_argument("--output", default=None,
                    help="Ruta del JSON de salida (default: data/usage_report_v3.json)")
    ap.add_argument("--force", action="store_true",
                    help="Escribir aunque los datos extraídos sean casi vacíos")
    ap.add_argument("--filter", choices=("charly", "all"), default="charly",
                    help=("Filtro de proyectos: charly = scope in-scope "
                          "(repos charly/sk/ak, default); all = sin filtro"))
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="Fecha inicial (inclusive) — descarta eventos anteriores (UTC)")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                    help="Fecha final (inclusive) — descarta eventos posteriores (UTC)")
    ap.add_argument("--config", default=None,
                    help="Ruta del config (default: config/fpa.json; si no hay, fallback a constantes)")
    return ap, ap.parse_args()


def _main_config(ap, args):
    usage_state.set_state("CHARLY_FILTER", args.filter == "charly")
    try:
        since, until = parse_window(args.since, args.until)
        usage_state.set_state("SINCE", since)
        usage_state.set_state("UNTIL", until)
    except ValueError as e:
        ap.error(str(e))
    try:
        load_config(args.config)
    except FileNotFoundError as e:
        print(f"ERROR: config no encontrado: {e.filename}\n"
              "  (default: config/fpa.json; sin config hay fallback a constantes)",
              file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _main_out_path(args):
    out_path = Path(args.output) if args.output else OUTPUT_DIR / "usage_report_v3.json"
    if (SINCE or UNTIL) and not args.output and not args.force:
        # coffe-snj: una corrida con ventana es un SUBCONJUNTO — sin --output
        # pisaría el dataset principal de data/ (el guard de arriba ya no
        # protege con ventana). Forzar explícitamente si es intencional.
        print(
            "\nABORTADO: --since/--until produce un subconjunto del histórico;"
            "\n escribirlo en data/usage_report_v3.json pisaría el dataset principal."
            "\nUsa --output RUTA para volcarlo a otro archivo, o --force si es intencional.",
            flush=True,
        )
        sys.exit(1)
    return out_path


def _main_extract(excluded):
    interactions = []
    skipped = {}
    kinds = {}       # FPA-140: eventos por kind/mes (incluye user prompts)

    claude_rows, claude_excluded = extract_claude(skipped, kinds, excluded)
    pi_rows, pi_excluded = extract_pi(skipped, kinds, excluded)
    omp_rows, omp_excluded = extract_omp(skipped, kinds, excluded)
    all_sources = [
        ("Claude", claude_rows),
        ("Pi", pi_rows),
        ("Amp", extract_amp()),
        ("Codex", extract_codex(skipped)),  # coffe-2sy: CLI standalone
        ("omp", omp_rows),
    ]
    excluded.extend(claude_excluded)
    excluded.extend(pi_excluded)
    excluded.extend(omp_excluded)

    for name, rows in all_sources:
        print(f"  {name}: {len(rows)} rows", flush=True)
        interactions.extend(rows)
    return interactions, skipped, kinds


def _main_unique(interactions):
    # Dedup across sources
    seen = set()
    unique = []
    for r in sorted(interactions, key=lambda x: x["timestamp"]):
        key = (r["timestamp"], r["tool"], r["model_raw"], r.get("source", ""), r.get("project", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _main_guard(args, unique, out_path):
    # coffe-snj: con ventana explícita, una extracción angosta es legítima —
    # el guard solo protege contra corridas sin datos (máquina sin logs).
    min_required = 1 if (SINCE or UNTIL) else MIN_INTERACTIONS
    if len(unique) < min_required and not args.force:
        print(
            f"\nABORTADO: solo {len(unique)} interacciones encontradas"
            f" (mínimo esperado: {min_required}).",
            "\nLos extractores leen ~/.claude, ~/.pi/agent y ~/.amp — ¿estás en la máquina con los logs?"
            "\nUsa --force para escribir de todas formas.",
            flush=True,
        )
        sys.exit(1)

    # coffe-0c8: guard fail-loud contra poda del cache frágil de Claude
    # (coffe-jg3). Compara claude-cli por mes contra el reporte vigente ANTES
    # de escribir; aborta si un mes cae más del umbral (TRACKER_MONTHLY_DROP_PCT).
    check_monthly_regressions(unique, out_path, force=args.force)


def _main_context_metrics():
    print("Context metrics...", flush=True)  # ctx-tracker (coffe-kup)
    t_ctx = time.time()
    context_metrics = extract_context_metrics()
    if context_metrics["available"]:
        print(f"  {len(context_metrics['sessions'])} sesiones pi con turnos "
              f"({context_metrics['meta'].get('excluded_pathological', 0)} "
              f"patológicas excluidas, "
              f"{context_metrics['meta'].get('stubs', 0)} stubs) "
              f"[{time.time() - t_ctx:.1f}s]", flush=True)
    else:
        print(f"  {context_metrics['reason']}", flush=True)
    return context_metrics


def _main_report(unique, sessions, ctx):
    print("Aggregating...", flush=True)
    outcomes, outcomes_reason = collect_outcomes()
    sk_total, sk_by_proj, sk_cmds = collect_skills_and_commands()
    report = aggregate(unique, sessions, skills_total=sk_total,
                       skills_by_project=sk_by_proj, commands=sk_cmds,
                       excluded=ctx["excluded"], interaction_kinds=ctx["kinds"],
                       outcomes=outcomes, outcomes_reason=outcomes_reason,
                       context_metrics=ctx["context_metrics"])
    if ctx["skipped"]:
        report["metadata"]["skipped"] = dict(ctx["skipped"])
        report["metadata"]["skipped_lines"] = sum(ctx["skipped"].values())
    if SINCE or UNTIL:
        # coffe-snj: registrar la ventana pedida (reproducibilidad)
        report["metadata"]["window"] = {
            "since": SINCE.isoformat() if SINCE else None,
            "until": UNTIL.isoformat() if UNTIL else None,
        }
    return report


def _main_write(report, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)


def _print_summary(report):
    m = report["metadata"]
    print(f"\n=== REPORT v4.3 ===")
    print(f"Period: {m['date_range']['start']} → {m['date_range']['end']} ({m['filter']})")
    print(f"Interactions: {m['total_interactions']:,}")
    print(f"Cost effective: ${m['cost_total_effective']:,.2f}")
    print(f"Cost real: ${m['cost_total_real']:,.2f}")
    print(f"  Subscription fees: ${m['subscription_fees']:,.2f}")
    print(f"  Pay-per-token: ${m['cost_total_real'] - m['subscription_fees']:,.2f}")
    print(f"Cash real (ledger, reported): ${m.get('charges_total_real', 0):,.2f}"
          f" ({m.get('charges_source', 'n/a')})")
    print(f"Hours: {m['total_hours']}, Days: {m['total_days']}, Projects: {m['total_projects']}")


def _print_monthly(report):
    print(f"\n--- Monthly ---")
    for m_name, mo in report["monthly"].items():
        tools_s = ", ".join(f"{t}:{c}" for t, c in list(mo["tools"].items())[:3])
        models_s = ", ".join(f"{mv}:{c}" for mv, c in list(mo["models"].items())[:3])
        sub = mo.get("subscription_fees", 0)
        print(f"  {m_name}: {mo['interactions']:>6} reqs  "
              f"${mo['cost_effective']:>7.2f} eff  "
              f"${mo['cost_real']:>6.2f} real"
              f"{f' (sub ${sub:.0f})' if sub else ''}  "
              f"{mo['input_tokens']//1000:>5}K in  {mo['output_tokens']//1000:>5}K out")
        print(f"       Tools: {tools_s}")
        print(f"       Models: {models_s}")


def _print_sessions(report):
    print(f"\n--- Sessions ---")
    ss = report["sessions"]
    print(f"  Total: {ss['total_sessions']}")
    print(f"  Length distribution: {dict(ss['length_distribution'])}")
    if ss['total_sessions'] > 0:
        print(f"  With Agent (autonomous): {ss['with_agent']}/{ss['total_sessions']} "
              f"({100 * ss['with_agent'] / ss['total_sessions']:.0f}%)")
    print(f"  Avg turns: {ss['avg_turns']:.0f}, Avg tools: {ss['avg_tools']:.1f}")
    print(f"  API errors: {ss['total_api_errors']}, Compactions: {ss['total_compactions']}")
    print(f"  Longest sessions:")
    for s in ss['top_longest_by_turns'][:5]:
        print(f"    {s['turns']:>4} turns | {s['msgs']:>3} msgs | {s['date']} | {s['project'][:45]}")


def _print_multitasking(report):
    print(f"\n--- Multitasking ---")
    mt = report["multitasking"]
    mh, md, mc = mt["hourly"], mt["daily"], mt["context_switches"]
    print(f"  Horas con ≥2 proyectos: {mh['hours_with_multiple_projects']}/{mh['total_active_hours']} "
          f"({mh['pct_hours_multitasking']}%)")
    print(f"  Avg proyectos/hora activa: {mh['avg_projects_per_active_hour']}")
    print(f"  Distribución por hora: {mh['distribution']}")
    mx = mh["max_projects_in_one_hour"]
    print(f"  Máx simultáneo: {mx['count']} proyectos en {mx['hours']}")
    print(f"  Días con ≥2 proyectos: {md['days_with_multiple_projects']}/{md['total_active_days']} "
          f"({md['pct_days_multitasking']}%)")
    print(f"  Context switches: {mc['total']} total, {mc['avg_per_active_day']}/día activo")
    print(f"  Top días multitasking:")
    for t in md["top_10_days"][:5]:
        print(f"    {t['date']}: {t['projects']} proyectos, {t['interactions']} reqs")


def _print_skills_commands(report):
    print(f"\n--- Skills ---")
    for skill, count in list(report['skills'].items())[:10]:
        print(f"  {skill}: {count}")

    print(f"\n--- Commands ---")
    for cmd, count in list(report['commands'].items())[:10]:
        print(f"  {cmd}: {count}")


def main():
    ap, args = _main_args()
    _main_config(ap, args)
    out_path = _main_out_path(args)

    ventana = ""
    if SINCE or UNTIL:
        ventana = f", ventana: {SINCE or 'inicio'}..{UNTIL or 'hoy'}"
    print(f"=== IA Usage Tracker v4.3 (filtro: {args.filter}{ventana}) ===", flush=True)

    excluded = []    # FPA-141: filas fuera del filtro charly
    interactions, skipped, kinds = _main_extract(excluded)
    unique = _main_unique(interactions)
    print(f"  Total: {len(interactions)} → {len(unique)} unique", flush=True)

    _main_guard(args, unique, out_path)

    print("Session stats...", flush=True)
    sessions = extract_sessions()  # coffe-i31: claude + pi + amp con provenance
    print(f"  {len(sessions)} charly sessions", flush=True)

    context_metrics = _main_context_metrics()
    report = _main_report(unique, sessions, {
        "excluded": excluded,
        "skipped": skipped,
        "kinds": kinds,
        "context_metrics": context_metrics,
    })

    _main_write(report, out_path)

    # Pretty print
    _print_summary(report)
    _print_monthly(report)
    _print_sessions(report)
    _print_multitasking(report)
    _print_skills_commands(report)

    print(f"\nDone. {out_path}")

usage_state._link(globals())
