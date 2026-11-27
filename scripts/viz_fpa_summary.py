"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




def _head(t):
    """FPA-008: celda headline; value None → display n/a + razón."""
    key, label, value, provenance, interp, reason, fmt = t
    return {
        "key": key, "label": label, "value": value,
        "display": fmt(value) if value is not None else "n/a",
        "provenance": provenance, "interp": interp,
        "reason": reason,
    }


def _month_word(n):
    return f"{n} {'mes' if n == 1 else 'meses'} de datos"


def _head_eff(n, eff):
    """Celda cost_effective."""
    if eff > 0:
        return ("cost_effective", "Coste efectivo", eff, "reported",
                f"estimación API-equivalente en {_month_word(n)} "
                f"(≈ {fmt_usd(eff / n)}/mes)", None, fmt_usd)
    return ("cost_effective", "Coste efectivo", None, "reported", "",
            "sin meses con datos en el reporte", fmt_usd)


def _head_cash_interp(cash, n, cash_reported, src):
    """(interp, reason) de la celda cost_cash."""
    if cash_reported and cash > 0:
        return (f"cargos reales del ledger ({src}) en {_month_word(n)} "
                f"(≈ {fmt_usd(cash / n)}/mes)"), None
    if cash > 0:
        interp = ("cuotas de suscripción + cargas del tracker, sin ledger "
                  f"en el reporte (≈ {fmt_usd(cash / n)}/mes)")
        return interp, None
    if n == 0:
        return "", "sin meses con datos en el reporte"
    if cash_reported:
        return "", "sin facturas en el periodo (ledger)"
    return "", "sin cargas cash registradas"


def _head_cash(report, cash, n, cash_reported):
    """Celda cost_cash (valor None si 0; provenance según ledger)."""
    interp, reason = _head_cash_interp(
        cash, n, cash_reported, _charges_state(report)["source"])
    prov = "reported" if cash_reported else "assumed"
    return ("cost_cash", "Coste cash", cash if cash else None, prov,
            interp, reason, fmt_usd)


def _head_leverage(eff, cash):
    """Celda leverage (requiere efectivo y cash > 0)."""
    if eff > 0 and cash > 0:
        lev = eff / cash
        return ("leverage", "Leverage", lev, "reported",
                f"el coste efectivo equivale a {lev:.1f}× el cash cost",
                None, lambda v: f"{v:.1f}×")
    return ("leverage", "Leverage", None, "reported", "",
            "requiere coste efectivo y cash mayores que 0", fmt_usd)


def _head_per_1k(interactions, eff):
    """Celda cost_per_1k."""
    if interactions > 0 and eff > 0:
        per_1k = eff / (interactions / 1000)
        return ("cost_per_1k", "Coste por 1k interacciones", per_1k,
                "reported",
                f"por cada {fmt_int(1000)} interacciones "
                f"({fmt_int(interactions)} en el periodo)", None, fmt_usd)
    reason = ("sin interacciones en el periodo" if interactions == 0
              else "sin coste efectivo")
    return ("cost_per_1k", "Coste por 1k interacciones", None, "reported",
            "", reason, fmt_usd)


def _head_outcome(report, eff):
    """Celda outcome: coste efectivo por commit, donde haya datos (FPA-007)."""
    commits = sum(
        (o.get("commits", 0) or 0)
        for m in report["monthly"].values() if m["interactions"] > 0
        for o in (m.get("outcomes_by_project") or {}).values()
    )
    if commits > 0 and eff > 0:
        return ("outcome", "Coste efectivo por commit", eff / commits,
                "reported", f"{fmt_int(commits)} commits en el periodo",
                None, fmt_usd)
    reason = ("el reporte no registra commits/releases" if commits == 0
              else "sin coste efectivo")
    return ("outcome", "Coste efectivo por commit", None, "reported", "",
            reason, fmt_usd)


def build_headlines(report):
    """FPA-007: 3–5 cifras headline con interpretación de una línea.

    coffe-esc F3: las headline cards desaparecieron; esto ya no renderiza
    cards, solo provee la interpretación de una línea que pasa al contexto
    del KPI card (el KPI strip ES el resumen ejecutivo).

    FPA-002: efectivo y cash son medidas separadas y nunca se suman.
    FPA-008: valor no computable → value=None + reason; display "n/a".
    """
    months = [m for m in build_months(report) if m["has_data"]]  # FPA-017
    interactions = sum(m["interactions"] for m in months)
    eff = sum(m["cost_effective"] for m in months)
    cash = sum(m["cost_cash"] for m in months)
    n = len(months)
    cash_reported = all(m["cash_provenance"] == "reported" for m in months) \
        if months else False
    return [_head(t) for t in (
        _head_eff(n, eff),
        _head_cash(report, cash, n, cash_reported),
        _head_leverage(eff, cash),
        _head_per_1k(interactions, eff),
        _head_outcome(report, eff),
    )]


def build_claims(report, cfg):
    """FPA-009: claims narrativos solo con métrica nombrada + threshold.

    Un claim se emite solo si la métrica es computable; el threshold viene de
    config y es *assumed*. Sin métrica → el claim no se renderiza.
    """
    months = [m for m in build_months(report) if m["has_data"]]
    interactions = sum(m["interactions"] for m in months)
    eff = sum(m["cost_effective"] for m in months)
    claims = []
    target = cfg["budgets"]["target_per_1k"]  # assumed (config)
    if interactions > 0 and eff > 0:
        per_1k = eff / (interactions / 1000)
        inside = per_1k <= target
        claims.append({
            "text": ("Coste por 1k dentro del objetivo" if inside
                     else "Coste por 1k por encima del objetivo"),
            "metric_name": "coste por 1k interacciones",
            "metric_value": per_1k,
            "threshold": target,
            "threshold_provenance": "assumed",
            "verdict": "✔" if inside else "✘",
        })
    return claims


def build_model(report, cfg):
    """Modelo completo del dashboard (va embebido como JSON en el HTML).
    F2 añade vistas pre-calculadas por periodo, árboles con roll-up
    verificado y la sección Data (FPA-140/141/142/120).
    F3 añade budget (pro-rating/varianza/YTD), bridge PVM y forecast
    (FPA-050…077); las fórmulas viven en Python, JS solo re-escala.
    F4 (alertas FPA-080…088 y economía FPA-130…133) se calcula aparte
    en render_html: dependen de la fecha de hoy (staleness) y no
    deben contaminar los goldens del modelo base (FPA-104)."""
    months = build_months(report)
    return {
        "period": report["metadata"]["date_range"],
        "months": months,
        "headlines": build_headlines(report),
        "claims": build_claims(report, cfg),
        "views": build_views(report, cfg),
        "data_notes": build_data_notes(report),
        "budget": build_budget(report, cfg),
        "bridge": build_bridge_section(report),
        "forecast": build_forecast(report, cfg),
        "usage": build_usage_patterns(report, cfg),
        # coffe-a31.3 (CRG-F2): cash por proveedor y reconciliación FPA-082
        "reconciliation": build_reconciliation(report),
        # coffe-5ng (F8): energía estimada con banda de caché
        "energy": build_energy(report),
        # add-session-spend-report: vista Gasto diario con drill-down
        "daily_spend": build_daily_spend(report),
    }


# ======================================================================
# F3 (coffe-lat.4): presupuesto, bridge PVM y forecast
# (FPA-050…077) — maths puras en Python; JS solo re-escala varianza y
# forecast con las fórmulas reproducidas (design.md). El presupuesto de
# efectivo es informativo (soft, design.md OQ-1); el gestionado es el de
# cash. Efectivo y cash jamás se suman (FPA-002).
# ======================================================================
from viz_fpa_common import _link  # noqa: E402

_link(globals())
