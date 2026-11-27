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




def build_forecast(report, cfg):
    """FPA-070…077: forecast desde run-rate base = media FME de los últimos
    3 meses con datos (FPA-070); <3 meses → n/a con razón (FPA-008), nunca
    un forecast inventado. Escenario default (g=0, r=0) pre-calculado;
    apply_scenario() re-calcula en JS sin reload (FPA-077). Planes futuros
    = calendario de suscripciones del config (FPA-071). Resto del mes
    parcial como fila separada (FPA-075)."""
    end = report["metadata"]["date_range"]["end"]
    end_ym = end[:7] if end else None
    end_date = date.fromisoformat(end) if end else None
    if not end_date or not end_ym:
        return _fc_n_a("el reporte no registra fecha de fin")
    months = [m for m in build_months(report) if m["has_data"]]
    if len(months) < 3:
        return _fc_n_a(f"se requieren 3 meses con datos para el run-rate "
                       f"base; el reporte cubre {len(months)}")

    base_months, eff_base, q_base, rate_base = _run_rate_base(months)
    fees_last, p2p_last, f_last, p2p_base = _cash_base(report, months)

    # FPA-075: resto del mes parcial como fila forecast separada
    remainder = _remainder_row(months[-1], f_last, p2p_last)

    primary, plans, default_plan = _plan_entries(cfg, end_date)

    # meses futuros: del mes siguiente al cierre hasta diciembre (FPA-070)
    fut = [f"{end_ym[:4]}-{mm:02d}" for mm in range(int(end_ym[5:7]) + 1, 13)]

    fc = {
        "provenance": "assumed",  # FPA-003: run-rate y planes derivan de config
        "base": {
            "eff_base": round(eff_base, 6), "q_base": round(q_base, 6),
            "rate_base": round(rate_base, 6) if rate_base is not None else None,
            "fee_base": round(fees_last, 2), "p2p_base": round(p2p_base, 6),
            "months_used": [m["ym"] for m in base_months],
        },
        "plans": plans,
        "default_plan": default_plan,
        "future_months": fut,
        "remainder": remainder,
    }
    fc["rows"] = apply_scenario(fc, 0.0, 0.0, default_plan)["rows"]
    fc["outlook"] = build_outlook(report, cfg, fc)
    return fc


def _fc_n_a(reason):
    return {"provenance": "assumed", "rows": [], "outlook": False, "remainder": None, "n_a_reason": reason}


def _run_rate_base(months):
    base_months = months[-3:]
    eff_base = sum(m["cost_effective"] * m["total_days"] / m["elapsed"] for m in base_months) / 3
    q_base = sum(m["interactions"] * m["total_days"] / m["elapsed"] for m in base_months) / 3
    rate_base = eff_base / q_base if q_base else None
    return base_months, eff_base, q_base, rate_base


def _cash_base(report, months):
    """cash base: p2p del cash real del último mes (ledger *reported* cuando
    hay ledger; suma tracker assumed si no) menos la cuota del calendario"""
    mo_last = report["monthly"][months[-1]["ym"]]
    fees_last = mo_last.get("subscription_fees", 0.0) or 0.0
    p2p_last = max(months[-1]["cost_cash"] - fees_last, 0.0)
    f_last = months[-1]["total_days"] / months[-1]["elapsed"] if months[-1]["elapsed"] else 1.0
    return fees_last, p2p_last, f_last, p2p_last * f_last


def _remainder_row(last, f_last, p2p_last):
    if not last["partial"]: return None
    return {"ym": last["ym"], "label": month_label(last["ym"]),
            "eff": round(last["cost_effective"] * (f_last - 1.0), 2),
            "cash": round(p2p_last * (f_last - 1.0), 2),
            "elapsed": last["elapsed"], "total_days": last["total_days"],
            "marker": {"symbol": "△", "text": "forecast", "favorable": None}}


def _primary_tool(cfg, end_date):
    """FPA-071: la suscripción primaria es la de mayor cuota activa al cierre."""
    primary, best_fee = None, -1.0
    for tool in cfg["subscriptions"]:
        e = _plan_for(cfg, tool, end_date)
        if e and e["monthly_fee"] > best_fee:
            primary, best_fee = tool, e["monthly_fee"]
    return primary


def _fallback_tool(cfg):
    # FPA-082 (cargos reales): con el calendario corregido a las
    # facturas puede no haber ninguna suscripción activa al cierre
    # (Free/cancelado). La herramienta primaria es la de la última
    # suscripción vigente (fin más tardío; a igual fin, la de mayor cuota).
    best_cand, best_tool = None, None
    for tool, entries_ in cfg["subscriptions"].items():
        for e in entries_:
            if not e.get("end"): continue
            cand = (e["end"], e["monthly_fee"], tool)
            if best_cand is None or cand > best_cand:
                best_cand, best_tool = cand, tool
    return best_tool


def _plan_entries(cfg, end_date):
    """(primary, plans, default_plan) del calendario de suscripciones."""
    primary = _primary_tool(cfg, end_date)
    fallback_plan = None
    if primary is None:
        best_tool = _fallback_tool(cfg)
        if best_tool is None:
            primary = next(iter(cfg["subscriptions"]), None)
            entries = list(cfg["subscriptions"].get(primary, []))
        else:
            primary, entries = best_tool, list(cfg["subscriptions"][best_tool])
            fallback_plan = {"key": f"{primary}:none", "tool": primary,
                             "label": "Sin suscripción (pay-per-token)", "fee": 0.0}
    else:
        entries = cfg["subscriptions"].get(primary, [])
    plans = [{"key": f"{primary}:{i}", "tool": primary, "label": e["label"],
              "fee": e["monthly_fee"]} for i, e in enumerate(entries)]
    default_plan = plans[0]["key"] if plans else None
    for i, e in enumerate(entries):
        s = date.fromisoformat(e["start"])
        e_end = date.fromisoformat(e["end"]) if e.get("end") else None
        if s <= end_date and (e_end is None or end_date < e_end):
            default_plan = f"{primary}:{i}"
            break
    if fallback_plan:
        plans.append(fallback_plan)
        default_plan = fallback_plan["key"]
    elif primary is None:
        # sin activa al cierre y sin fines en el calendario: no inventar
        # default — apply_scenario cae a base["fee_base"] (del reporte)
        default_plan = None
    return primary, plans, default_plan


def apply_scenario(fc, growth, rate_chg, plan_key):
    """FPA-072/073: escenario sobre el run-rate base.
    Efectivo: base×(1+g)ⁿ×(1+r) (FPA-072; equivale a Q×(1+g)ⁿ×rate×(1+r)).
    Cash: cuota del plan + p2p base escalado por volumen (FPA-073).
    Misma fórmula reproducida en JS para el recompute sin reload (FPA-077)."""
    if not fc or fc.get("n_a_reason"): return {"rows": []}
    base = fc["base"]
    fee = next((p["fee"] for p in fc["plans"] if p["key"] == plan_key), base["fee_base"])
    rows = []
    for i, ym in enumerate(fc["future_months"]):
        f = (1 + growth) ** (i + 1)
        rows.append({"ym": ym, "label": month_label(ym),
                     "eff": round(base["eff_base"] * f * (1 + rate_chg), 2),
                     "cash": round(fee + base["p2p_base"] * f, 2),
                     "marker": {"symbol": "△", "text": "forecast", "favorable": None}})
    return {"rows": rows}


def _outlook_budget(cfg, report, fc):
    """Presupuesto pro-rateado en meses parciales del reporte (FPA-052)
    + meses futuros completos del forecast."""
    budget_cash = budget_eff = 0.0
    for m in build_months(report):
        bv = _budget_for(cfg, m["ym"])
        if bv:
            budget_cash += bv[0] * m["elapsed"] / m["total_days"]
            budget_eff += bv[1] * m["elapsed"] / m["total_days"]
    for ym in fc["future_months"]:
        bv = _budget_for(cfg, ym)
        if bv:
            budget_cash += bv[0]
            budget_eff += bv[1]
    return budget_cash, budget_eff


def build_outlook(report, cfg, fc):
    """FPA-074: YTD real + outlook vs presupuesto, con varianza y markers.
    Presupuesto pro-rateado en meses parciales del reporte (FPA-052)."""
    months = [m for m in build_months(report) if m["has_data"]]
    actual_cash = sum(m["cost_cash"] for m in months)
    actual_eff = sum(m["cost_effective"] for m in months)
    rem = fc["remainder"]
    projected_cash = actual_cash + rem["cash"] + sum(r["cash"] for r in fc["rows"])
    projected_eff = actual_eff + rem["eff"] + sum(r["eff"] for r in fc["rows"])
    budget_cash, budget_eff = _outlook_budget(cfg, report, fc)
    var_cash = projected_cash - budget_cash
    var_eff = projected_eff - budget_eff
    return {
        "actual_cash": round(actual_cash, 2), "actual_eff": round(actual_eff, 2),
        "budget_cash": round(budget_cash, 2), "budget_eff": round(budget_eff, 2),
        "projected_cash": round(projected_cash, 2),
        "projected_eff": round(projected_eff, 2),
        "variance_cash": round(var_cash, 2), "variance_eff": round(var_eff, 2),
        "variance_pct_cash": round(var_cash / budget_cash, 4) if budget_cash else None,
        "variance_pct_eff": round(var_eff / budget_eff, 4) if budget_eff else None,
        "marker_cash": _marker(var_cash), "marker_eff": _marker(var_eff),
        "ym_to": fc["future_months"][-1] if fc["future_months"] else None,
        "provenance": "assumed",
    }


# ======================================================================
# F4 (coffe-lat.5): alertas y economía de suscripción
# (FPA-080…088, 130–133) — maths puras en Python; el HTML las muestra
# pre-calculadas. Umbrales del config (FPA-088), toda cifra derivada es
# assumed (FPA-003), efectivo y cash jamás se suman (FPA-002).
# ======================================================================

def _th(cfg, key, default):
    """FPA-088: umbral configurable con default de la spec."""
    return float(cfg.get("alert_thresholds", {}).get(key, default))


def _tool_eff_month(report, ym, tool):
    """Coste efectivo de `tool` en el mes: shape nuevo (dict con
    cost_effective) o fallback a hourly per-tool cost_eff."""
    st = report.get("monthly", {}).get(ym, {}).get("tools", {}).get(tool)
    if isinstance(st, dict) and st.get("cost_effective") is not None:
        return st["cost_effective"] or 0.0
    total = 0.0
    for key, h in (report.get("hourly") or {}).items():
        if key[:7] == ym:
            total += (h.get("tools", {}).get(tool) or {}).get("cost_eff", 0.0) or 0.0
    return total


def _plan_fee_for_month(cfg, ym, tool="claude-cli"):
    """Cuota mensual del plan de `tool` activo durante el mes (la mayor si
    hubo varios); 0.0 si ninguno cubre el mes."""
    fees = [e["monthly_fee"] for e in cfg.get("subscriptions", {}).get(tool, [])
            if e.get("monthly_fee", 0) > 0 and e["start"][:7] <= ym
            and (e.get("end") is None or ym < e["end"][:7])]
    return max(fees) if fees else 0.0


def _check_verify_plan(report, cfg, months, alerts):
    """FPA-081: efectivo de Claude ÷ precio del plan > 25× → alerta.
    "Revisar fechas del plan u otras cuentas pagas".

    coffe-a31.3 (calendario corregido a las facturas): un mes con uso de
    Claude sin ningún plan que lo cubra (suscripción cancelada/expirada,
    p.ej. jun-sep 2026) también dispara la alerta — el efectivo no está
    respaldado por ninguna cuota. Solo con uso y sin calendario de claude
    en el config no hay señal que fabricar."""
    th = _th(cfg, "plan_usage_multiple", 25.0)
    calendario = cfg.get("subscriptions", {}).get("claude-cli", [])
    tiene_calendario = any(e.get("monthly_fee", 0) > 0 for e in calendario)
    for m in months:
        eff = _tool_eff_month(report, m["ym"], "claude-cli")
        if not eff:
            continue
        fee = _plan_fee_for_month(cfg, m["ym"])
        if not fee:
            if not tiene_calendario:
                continue
            alerts.append({
                "severity": "high", "rule": "verify-plan",
                "message": (f"{month_label(m['ym'])}: efectivo Claude "
                            f"{fmt_usd(eff)} sin plan activo en el calendario "
                            f"(suscripción cancelada/expirada) — revisar fechas del plan u otras cuentas pagas"),
                "evidence": {"month": m["ym"], "claude_eff": round(eff, 2),
                             "plan_fee": 0.0, "multiple": None, "threshold": th},
            })
            continue
        multiple = eff / fee
        if multiple > th:
            alerts.append({
                "severity": "high", "rule": "verify-plan",
                "message": (f"{month_label(m['ym'])}: efectivo Claude "
                            f"{fmt_usd(eff)} = {multiple:.1f}× el precio del "
                            f"plan (${fee:,.0f}/mes) — revisar fechas del plan u otras cuentas pagas"),
                "evidence": {"month": m["ym"], "claude_eff": round(eff, 2),
                             "plan_fee": fee, "multiple": round(multiple, 2),
                             "threshold": th},
            })
from viz_fpa_common import _link  # noqa: E402

_link(globals())
