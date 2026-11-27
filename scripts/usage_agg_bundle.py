#!/usr/bin/env python3
"""Agregación: bundle final y aggregate() (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _agg_skills(opts, by_project):
    # --- Skills (inyectados; default: colección en vivo) ---
    skills_total = opts.get("skills_total")
    skills_by_project = opts.get("skills_by_project")
    commands = opts.get("commands")
    if skills_total is None or skills_by_project is None or commands is None:
        skills_total, skills_by_project, commands = collect_skills_and_commands()
    skills_total = Counter(skills_total)
    commands = Counter(commands)
    for proj_clean, sk in skills_by_project.items():
        if proj_clean in by_project:
            by_project[proj_clean].skills = Counter(sk)
    return skills_total, skills_by_project, commands


def _agg_report_sections(st, bundle):
    return {
        "hourly": {h: b.to_dict() | {"projects_active": getattr(b, "projects_active", 0)}
                   for h, b in st.hourly.items()},
        "daily": {d: b.to_dict() for d, b in st.daily.items()},
        "monthly": dict(sorted(bundle["monthly_dicts"].items())),
        "skills": dict(bundle["skills_total"].most_common(50)),
        "commands": dict(bundle["commands"].most_common(30)),
        "sessions": bundle["session_stats"],
        "context_metrics": bundle["context_metrics"] or _agg_context_default(),
        "multitasking": bundle["multitasking"],
        "project_daily": bundle["project_daily"],
        "session_spend": bundle["spend_rows"],
        # coffe-dnx.8: scorecard de cohorte (misma sesión-gasto como insumo)
        "model_scorecard": bundle["model_scorecard"],
        "concurrency": bundle["concurrency"],
        "filtered_out": bundle["filtered_out"],
        "sessions_monthly": dict(sorted(bundle["sessions_monthly"].items())),
        "subscription_config": SUBSCRIPTIONS,
        "subscription_fees_by_month": bundle["sub_fees"],
        "metadata": bundle["metadata"],
    }


def _agg_report_charges(bundle):
    charges_by_month, charges_ok = bundle["charges_by_month"], bundle["charges_ok"]
    return {
        # coffe-a31.2: cash real del ledger por proveedor y mes (provenance
        # reported), y el insumo de reconciliación FPA-082 (real vs fee
        # implícito del calendario vs efectivo estimado)
        "charges_real_by_month": charges_by_month,
        "charges_reconciliation_by_month": reconciliation_charges(
            charges_by_month, bundle["monthly_dicts"]) if charges_ok else {},
        # coffe-mbz: config de pricing cargado (no constantes muertas)
        "model_pricing_config": {
            "default_rates": DEFAULT_RATES,
            "cache_write_factor": _CACHE_WRITE_FACTOR,
            "versions": _PRICING_VERSIONS,
        },
    }


def _agg_multitasking(st, interactions):
    """(multitasking, switches_by_day): cambios de contexto + proyectos
    activos por hora."""
    switches_by_day = _context_switches(interactions)
    multitasking = _multitasking_block(st.hour_projects, st.day_projects,
                                       {d: b.to_dict() for d, b in st.daily.items()},
                                       switches_by_day)
    # Conteo de proyectos activos por hora
    for h, ps in st.hour_projects.items():
        if h in st.hourly:
            st.hourly[h].projects_active = len(ps)
    return multitasking, switches_by_day


def _agg_fees_and_charges(st, monthly_dicts, opts):
    """(sub_fees, tracker_total, charges) tras fees, split tracker,
    emisiones F2 y energía."""
    sub_fees = calc_subscription_fees(monthly_dicts)
    _agg_apply_fees(monthly_dicts, sub_fees, st.monthly, st.daily)
    _agg_tracker_split(monthly_dicts)
    tracker_total = round(sum(b.cost_real for b in st.hourly.values()), 2)

    charges_by_month, charges_total, charges_p2p, charges_ok, charges_reason = (
        _agg_charges())

    # --- Emisiones F2 (coffe-lat.3) en monthly_dicts ---
    _agg_month_dicts_emit(monthly_dicts, st, opts, (charges_ok, charges_p2p))

    # --- coffe-7mj.1: energía estimada (kWh) por mes y modelo (aditivo) ---
    _energy_for_month(monthly_dicts)
    return sub_fees, tracker_total, (charges_by_month, charges_total,
                                     charges_ok, charges_reason)


def _agg_bundle(st, interactions, sessions, opts):
    """Arma el bundle intermedio del reporte (todo lo que comparten
    secciones, metadata y cargos)."""
    multitasking, switches_by_day = _agg_multitasking(st, interactions)
    monthly_dicts = {m: b.to_dict(detail=True) for m, b in st.monthly.items()}
    sub_fees, tracker_total, charges = _agg_fees_and_charges(st, monthly_dicts,
                                                             opts)
    charges_by_month, charges_total, charges_ok, charges_reason = charges

    # --- Skills (inyectados; default: colección en vivo) ---
    skills_total, skills_by_project, commands = _agg_skills(opts, st.by_project)

    # --- Sessions ---
    session_stats = _session_stats(sessions, commands)

    # --- project_daily ---
    project_daily = _project_daily(st.by_project, st.day_projects, st.proj_day)

    # coffe-dnx.8: session_spend se calcula UNA vez (bloque del reporte +
    # insumo del scorecard de cohorte: latencia/switches/subscription split)
    spend_rows = _session_spend(interactions)
    model_scorecard, scorecard_meta = _model_scorecard(interactions, spend_rows)

    return {
        "monthly_dicts": monthly_dicts,
        "sub_fees": sub_fees,
        "skills_total": skills_total,
        "skills_by_project": skills_by_project,
        "commands": commands,
        "context_metrics": opts.get("context_metrics"),
        "multitasking": multitasking,
        "project_daily": project_daily,
        "spend_rows": spend_rows,
        "model_scorecard": model_scorecard,
        "concurrency": _agg_concurrency(st.hour_projects, switches_by_day, sessions),
        "filtered_out": _agg_filtered_out(opts.get("excluded"), st.hourly),
        "sessions_monthly": _agg_sessions_monthly(sessions),
        "session_stats": session_stats,
        "charges_by_month": charges_by_month,
        "charges_ok": charges_ok,
        "metadata": _agg_metadata(st, interactions, {
            "monthly_dicts": monthly_dicts,
            "sub_fees": sub_fees,
            "tracker_total": tracker_total,
            "charges": (charges_total, charges_ok, charges_reason),
            "scorecard_meta": scorecard_meta,
            "outcomes_reason": opts.get("outcomes_reason"),
        }),
    }


def aggregate(interactions, sessions, *args, **opts):
    """Agrega rows → reporte completo. Pura: los datos de skills/comandos
    se inyectan (ver collect_skills_and_commands()).
    Opts (posicionales o por keyword): skills_total, skills_by_project,
    commands, excluded, interaction_kinds, outcomes, outcomes_reason,
    context_metrics."""
    for name, value in zip(("skills_total", "skills_by_project", "commands",
                            "excluded", "interaction_kinds", "outcomes",
                            "outcomes_reason", "context_metrics"), args):
        opts.setdefault(name, value)
    st = _AggState()
    for r in interactions:
        _agg_row(st, r)
    bundle = _agg_bundle(st, interactions, sessions, opts)
    report = _agg_report_sections(st, bundle)
    report.update(_agg_report_charges(bundle))
    report.update(_agg_project_sections(st))
    return _clean_json(report)

usage_state._link(globals())
