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




def esc_html(s):
    """Escape mínimo HTML para texto/atributos con datos del reporte."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt_signed(x):
    """FPA-053: signo explícito — +$12.00 / -$12.00."""
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def _fmt_pct_signed(frac):
    """Porcentaje con signo: +12.3% / -12.3%; None → n/a."""
    if frac is None:
        return "n/a"
    return f"{'+' if frac >= 0 else ''}{frac * 100:.1f}%"


def _variance(actual, budget):
    """FPA-053: varianza = actual − presupuesto; positivo = over budget."""
    return actual - budget


def _marker(variance, tol=0.005):
    """FPA-054: favorable/desfavorable con símbolo Y texto (no solo color)."""
    if variance < -tol:
        return {"symbol": "▼", "text": "bajo", "favorable": True}
    if variance > tol:
        return {"symbol": "▲", "text": "sobre", "favorable": False}
    return {"symbol": "●", "text": "en", "favorable": None}


def _budget_for(cfg, ym):
    """FPA-050/051: presupuestos (cash, efectivo) aplicables a `ym` según
    budgets.start_month; None si el mes es anterior al inicio."""
    b = cfg["budgets"]
    start = b.get("start_month")
    if not start or ym < start:
        return None
    return b["cash_monthly"], b["effective_monthly"]


_BUDGET_NONE_CELLS = {
    "budget_cash": None, "budget_eff": None,
    "actual_cash": None, "actual_eff": None,
    "variance_cash": None, "variance_eff": None,
    "variance_pct_cash": None, "variance_pct_eff": None,
    "budget_display_cash": None, "budget_display_eff": None,
    "actual_display_cash": None, "actual_display_eff": None,
    "variance_display_cash": None, "variance_display_eff": None,
    "variance_pct_display_cash": None, "variance_pct_display_eff": None,
    "marker_cash": None, "marker_eff": None,
}


def _budget_base_row(meta, bvals, start):
    """Fila base del mes con pro-rate (FPA-052) y razón si no aplica."""
    pro_rate = meta["elapsed"] / meta["total_days"]
    row = {
        "ym": meta["ym"], "label": meta["label"],
        "in_budget": bvals is not None, "partial": meta["partial"],
        "elapsed": meta["elapsed"], "total_days": meta["total_days"],
        "pro_rate": round(pro_rate, 6),
        "reason_cash": None, "reason_eff": None,
    }
    row.update(_BUDGET_NONE_CELLS)
    if bvals is None:
        reason = f"fuera del periodo presupuestado (desde {start})"
        row["reason_cash"] = row["reason_eff"] = reason
    elif not meta["has_data"]:  # FPA-017/008
        reason = "mes sin datos en el reporte"
        row["reason_cash"] = row["reason_eff"] = reason
    return row


def _budget_cells(bvals, meta):
    """Celdas calculadas del mes: presupuestos pro-rateados (FPA-052),
    varianzas con signo (FPA-053) y markers (FPA-054); más los 4 valores
    que alimentan los totales YTD."""
    pro_rate = meta["elapsed"] / meta["total_days"]
    budget_cash = round(bvals[0] * pro_rate, 2)
    budget_eff = round(bvals[1] * pro_rate, 2)
    actual_cash = round(meta["cost_cash"], 2)
    actual_eff = round(meta["cost_effective"], 2)
    var_cash = round(_variance(actual_cash, budget_cash), 2)
    var_eff = round(_variance(actual_eff, budget_eff), 2)
    pct_cash = round(var_cash / budget_cash, 4) if budget_cash else None
    pct_eff = round(var_eff / budget_eff, 4) if budget_eff else None
    cells = {
        "budget_cash": budget_cash, "budget_eff": budget_eff,
        "actual_cash": actual_cash, "actual_eff": actual_eff,
        "variance_cash": var_cash, "variance_eff": var_eff,
        "variance_pct_cash": pct_cash, "variance_pct_eff": pct_eff,
        "budget_display_cash": fmt_usd(budget_cash),
        "budget_display_eff": fmt_usd(budget_eff),
        "actual_display_cash": fmt_usd(actual_cash),
        "actual_display_eff": fmt_usd(actual_eff),
        "variance_display_cash": _fmt_signed(var_cash),
        "variance_display_eff": _fmt_signed(var_eff),
        "variance_pct_display_cash": _fmt_pct_signed(pct_cash),
        "variance_pct_display_eff": _fmt_pct_signed(pct_eff),
        "marker_cash": _marker(var_cash),
        "marker_eff": _marker(var_eff),
    }
    return cells, (actual_cash, budget_cash, actual_eff, budget_eff)


def build_budget(report, cfg):
    """FPA-050…056: tabla de varianza mensual con YTD.

    Pro-rating de meses parciales (FPA-052: presupuesto ×
    días transcurridos/días del mes), varianza con signo (FPA-053),
    markers favorable/desfavorable no-solo-color (FPA-054). El
    presupuesto de efectivo es informativo (soft, design.md OQ-1); el
    gestionado es el de cash. Efectivo y cash jamás se suman (FPA-002).
    Valor no computable → None + razón (FPA-008/017).
    """
    b = cfg["budgets"]
    inputs = {"cash_monthly": b["cash_monthly"],
              "effective_monthly": b["effective_monthly"],
              "target_per_1k": b["target_per_1k"],
              "start_month": b.get("start_month")}
    start = inputs["start_month"]
    rows = []
    tot = {"actual_cash": 0.0, "budget_cash": 0.0,
           "actual_eff": 0.0, "budget_eff": 0.0}
    any_budget = False
    for meta in build_months(report):
        bvals = _budget_for(cfg, meta["ym"])
        row = _budget_base_row(meta, bvals, start)
        if bvals is not None and meta["has_data"]:  # FPA-017/008
            cells, totals = _budget_cells(bvals, meta)
            row.update(cells)
            tot["actual_cash"] += totals[0]
            tot["budget_cash"] += totals[1]
            tot["actual_eff"] += totals[2]
            tot["budget_eff"] += totals[3]
            any_budget = True
        rows.append(row)

    def _ytd(actual, budget):
        """FPA-055: totales YTD por medida (nunca efectivo+cash juntos)."""
        if not any_budget:
            return False
        var = round(actual - budget, 2)
        y = {
            "actual": round(actual, 2), "budget": round(budget, 2),
            "variance": var, "marker": _marker(var),
            "actual_display": fmt_usd(actual),
            "budget_display": fmt_usd(budget),
            "variance_display": _fmt_signed(var),
        }
        if budget:
            y["variance_pct"] = round(var / budget, 4)
            y["variance_pct_display"] = _fmt_pct_signed(var / budget)
        else:
            y["variance_pct"] = None
            y["variance_pct_display"] = "n/a"
        return y

    return {
        "provenance": "assumed",  # FPA-003: deriva de config
        "inputs": inputs,
        "soft_eff": True,  # design.md OQ-1: presupuesto efectivo informativo
        "rows": rows,
        "ytd_cash": _ytd(tot["actual_cash"], tot["budget_cash"]),
        "ytd_eff": _ytd(tot["actual_eff"], tot["budget_eff"]),
    }


def build_pvm(q0, q1, by_model):
    """FPA-061…063: descomposición precio-volumen-mix del coste efectivo.

    by_model: lista (model, q0_i, p0_i, q1_i, p1_i) por modelo.
    Volume = (Q1−Q0)×rate₀ con rate₀ = coste medio previo por interacción;
    Mix = Σ q1ᵢ·p0ᵢ − Q1·rate₀; Rate = Σ q1ᵢ·(p1ᵢ−p0ᵢ).
    """
    rate0 = (sum(q * p for _, q, p, _, _ in by_model) / q0) if q0 else 0.0
    volume = (q1 - q0) * rate0
    mix = sum(q1 * p0 for _, _, p0, q1, _ in by_model) - q1 * rate0
    rate = sum(q1 * (p1 - p0) for _, _, p0, q1, p1 in by_model)
    return volume, mix, rate
from viz_fpa_common import _link  # noqa: E402

_link(globals())
