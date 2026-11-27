#!/usr/bin/env python3
"""Agregación: metadata y secciones del reporte (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _agg_sessions_monthly(sessions):
    # --- Sesiones por mes (KPIs: coste por sesión, autonomous share) ---
    sessions_monthly = defaultdict(lambda: {"total": 0, "with_agent": 0})
    for s in sessions:
        ym = (s.get("first_ts") or "")[:7]
        if len(ym) == 7:
            sessions_monthly[ym]["total"] += 1
            if s.get("has_agent"):
                sessions_monthly[ym]["with_agent"] += 1
    return sessions_monthly


def _agg_metadata_totals(st, monthly_dicts, sub_fees, tracker_total):
    return {
        "total_interactions": sum(b.interactions for b in st.hourly.values()),
        "total_input_tokens": sum(b.input_tokens for b in st.hourly.values()),
        "total_output_tokens": sum(b.output_tokens for b in st.hourly.values()),
        "total_cache_read_tokens": sum(b.cache_read_tokens for b in st.hourly.values()),
        "total_cache_write_tokens": sum(b.cache_write_tokens for b in st.hourly.values()),
        "cost_total_effective": round(sum(b.cost_effective for b in st.hourly.values()), 2),
        # coffe-a31.2: unificada con la suma mensual (antes: sumatoria
        # horaria sin fees implícitos → divergencia 381.62 vs 611.62).
        # Los fees implícitos del calendario quedan identificados aparte
        # (subscription_fees_by_month) y NO son cash (FPA-082).
        "cost_total_real": round(sum(mo["cost_real"] for mo in monthly_dicts.values()), 2),
        "cost_real_total_tracker": tracker_total,
        "subscription_fees": round(sum(sub_fees.values()), 2),
        "total_hours": len(st.hourly),
        "total_days": len(st.daily),
        "total_months": len(st.monthly),
        "total_projects": len(st.by_project),
    }


def _agg_metadata_charges(charges):
    charges_total, charges_ok, charges_reason = charges
    return {
        # coffe-a31.2: cash cost real del ledger (provenance reported).
        # Medida separada del efectivo: jamás sumarlas (FPA-002).
        "charges_total_real": round(charges_total, 2),
        "charges_source": CHARGES_SOURCE,
        "charges_provenance": "reported" if charges_ok else "unavailable",
        "charges_reason": charges_reason,  # FPA-008: n/a con razón
    }


def _agg_metadata_energy():
    # coffe-7mj.1: energía estimada — SIEMPRE assumed (ningún proveedor
    # reporta kWh; coeficientes de laboratorio versionados en
    # config/fpa.json). Unavailable con razón solo en fallback sin config
    # (FPA-008).
    out = {
        "energy_provenance": ("assumed" if _FPA_CONFIG is not None
                              else "unavailable"),
        "energy_config_source": CONFIG_SOURCE if _FPA_CONFIG is not None else None,
        "energy_cache_read_factor": (
            _ENERGY_COEFFS["cache_read_energy_factor"]
            if _FPA_CONFIG is not None else None),
        # coffe-5ng: extremos de la banda de sensibilidad de caché que
        # emite cada mes en energy_kwh_band (0% y 100% de acierto).
        **({"energy_band_cache_factors": [0.0, 1.0]}
           if _FPA_CONFIG is not None else {}),
        "energy_method": (
            "tokens (input+output+cache_write a peso completo; "
            "cache_read × cache_read_energy_factor) × J/token del tier "
            "(config energy_coefficients versionado) / 3.6e6 — "
            "coeficientes de laboratorio (Luccioni et al. / AI Energy "
            "Score), orden de magnitud, nunca medición"),
        **({"energy_reason": "config/fpa.json no encontrado (fallback)"}
           if _FPA_CONFIG is None else {}),
    }
    return out


def _agg_metadata(st, interactions, bundle):
    if interactions:
        date_range = {
            "start": min(r["timestamp"] for r in interactions)[:10],
            "end": max(r["timestamp"] for r in interactions)[:10],
        }
    else:
        date_range = {"start": None, "end": None}
    out = {
        "date_range": date_range,
        "filter": "in-scope" if CHARLY_FILTER else "all",
        "pay_per_token_note": (
            "pay_per_token_charges es *reported* cuando el ledger tiene "
            "cargas pay-per-token reales del mes (créditos/reembolsos, "
            "FPA-013); *assumed* (estimado tokens × pricing) si no"),
        "outcomes": bundle["outcomes_reason"] or "unavailable: no emisionado en esta corrida",  # FPA-014
        "token_accounting": (
            "cache_read/cache_write se reportan aparte de input/output. "
            "cache_read no se factura a input rate (10x mas barato); "
            "total tokens = input + output + cache_read + cache_write"
        ),
        "timezone": str(LOCAL_TZ),  # FPA-142: TZ usada en buckets hourly/daily
        # coffe-mbz: fuente de verdad de suscripciones/pricing
        "config_source": CONFIG_SOURCE or "builtin-fallback",
        # coffe-dnx.1: cobertura del mapeo modelo → familia (visible y
        # contado; el fallback "other" ya no es un agujero silencioso)
        "model_mapping_coverage": _model_mapping_coverage(interactions),
        # coffe-dnx.2: cobertura de latencia por fuente (medida, CORR-005)
        "latency_coverage": {
            "measured": dict(LATENCY_COVERAGE_MEASURED),
            "method": ("% de turnos con durationMs por provider — muestreo "
                       "CORR-005 (~400 archivos/provider, nov 2026); "
                       "medido, nunca extrapolado ni maquillado"),
        },
        # coffe-dnx.8: regla de decisión del scorecard de cohorte
        "scorecard": bundle["scorecard_meta"],
    }
    out.update(_agg_metadata_totals(st, bundle["monthly_dicts"],
                                    bundle["sub_fees"], bundle["tracker_total"]))
    out.update(_agg_metadata_charges(bundle["charges"]))
    out.update(_agg_metadata_energy())
    return out


def _clean_json(o):
    if isinstance(o, defaultdict):
        return {k: _clean_json(v) for k, v in o.items()}
    if isinstance(o, Counter):
        return dict(o.most_common())
    return o


def _agg_project_sections(st):
    return {
        "projects": {p: (b.to_dict() | {
            "first_seen": (b.first_seen or "")[:10],
            "last_seen": (b.last_seen or "")[:10],
            "skills": dict(getattr(b, "skills", {}).most_common()),
        }) for p, b in sorted(st.by_project.items(),
                              key=lambda x: -x[1].cost_effective)},
        "project_daily_spend": {
            p: {day: {
                "interactions": v["interactions"],
                "cost_effective": round(v["cost_effective"], 8),
                "cost_real": round(v["cost_real"], 8),
                "tokens": dict(v["tokens"]),
            } for day, v in sorted(days.items())}
            for p, days in sorted(st.project_daily_spend.items())},
        "project_monthly": {p: dict(ms) for p, ms in st.project_monthly.items()},
        "project_models": {p: {m: {"interactions": v["interactions"],
                                   "cost_effective": round(v["cost_effective"], 8)}
                              for m, v in sorted(models.items())}
                           for p, models in st.project_models.items()},
    }


def _agg_context_default():
    # ctx-tracker (coffe-kup): sección requerida por el schema — inyectada
    # por main() desde extract_context_metrics(); si no viene, missing
    # honesto (nunca array fantasma)
    return {
        "available": False,
        "reason": "unavailable: extract_context_metrics() no corrido en "
                  "esta agregación",
        "sessions": [], "monthly_model": [],
        # meta completo igual: la sección es requerida por el schema y
        # los reports sintéticos se validan contra él
        "meta": {
            "chars_per_token": DEFAULT_CONTEXT_THRESHOLDS["chars_per_token"],
            "chars_per_token_tag": "assumed",
            "chars_per_token_bias": (
                "subestima el contexto real: español/código ~3 "
                "chars/token y overhead de serialización"),
            "validation_ratio_median": None,
            "thresholds": dict(DEFAULT_CONTEXT_THRESHOLDS),
            "stubs": 0,
            "excluded_pathological": 0,
        },
    }

usage_state._link(globals())
