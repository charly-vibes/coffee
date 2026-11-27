"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar, html, json, re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config, schema_validate, site_theme  # noqa: E402,F401



# Economía de suscripción (FPA-130…133)

# FPA-167: acción default por regla de alerta (el config puede
# sobrescribirla vía ctas.alert_actions; targets nunca vacíos — FPA-168).
DEFAULT_ALERT_ACTIONS = {
    # coffe-a37: config/fpa.json no se deploya al sitio → link al fuente en
    # el repo (el config embede flags de privacidad que no van a producción).
    "verify-plan": {"label": "Revisar el plan",
                    "target": "https://github.com/charly-vibes/coffee/blob/main/config/fpa.json"},
    "reconciliation": {"label": "Ver el reporte", "target": "data/usage_report_v3.json"},
    "budget": {"label": "Ajustar presupuesto", "target": "#budget"},
    "unit-cost": {"label": "Ver el reporte", "target": "data/usage_report_v3.json"},
    "mix": {"label": "Comparar planes",
            "target": "https://github.com/charly-vibes/coffee/blob/main/config/fpa.json"},
    "concentration": {"label": "Ver proyectos", "target": "#pareto"},
    "staleness": {"label": "Regenerar datos", "target": "https://github.com/charly-vibes/coffee"},
}

def _plan_price_prorated(entry, period_start, period_end):
    """Precio del plan para el periodo: cuota mensual pro-rateada por
    calendario (Σ días del mes dentro del periodo / días del mes).
    Determinista, sin constantes mágicas (30.44 etc.)."""
    fee = entry["monthly_fee"]
    if fee <= 0:
        return 0.0
    total = 0.0
    d = period_start
    while d < period_end:
        ym = f"{d.year:04d}-{d.month:02d}"
        dim = month_total_days(ym)
        month_start = date(d.year, d.month, 1)
        next_month = (date(d.year + (d.month == 12), (d.month % 12) + 1, 1))
        days = (min(period_end, next_month) - max(period_start, month_start)).days
        total += fee * days / dim
        d = next_month
    return total

def _eff_record_state(key, h, tool, span):
    """(coste, presente) si el día `key` está en el periodo; None si no."""
    try:
        day = date.fromisoformat(key[:10])
    except ValueError:
        return None
    if not span[0] <= day < span[1]:
        return None
    st = h.get("tools", {}).get(tool) or {}
    return st.get("cost_eff", 0.0) or 0.0, bool(st)

def _eff_cost_in_period(report, tool, start, end):
    """Coste efectivo de `tool` dentro del periodo [start, end) desde hourly
    per-tool. None si la tool no tiene registros horarios en el periodo
    (n/a con razón, FPA-008) — 0.0 real es distinto de "no hay datos" y
    distinto de None: una tool con registros de coste 0 (gemini-cli sin
    cost en los logs, coffe-8t8) es utilización 0, no sin datos."""
    total = 0.0
    seen_cost = False   # al menos un registro con coste ≠ 0
    present = False     # al menos un registro de la tool en el periodo
    for key, h in (report.get("hourly") or {}).items():
        rec = _eff_record_state(key, h, tool, (start, end))
        if rec is None:
            continue
        total += rec[0]
        present = present or rec[1]
        seen_cost = seen_cost or rec[0] != 0.0
    if seen_cost:
        return total
    return 0.0 if present else None

def _plan_panel(report, end_d, tool, e):
    """Panel FPA-130 de una suscripción; None si el periodo es inválido."""
    start = date.fromisoformat(e["start"])
    e_end = date.fromisoformat(e["end"]) if e.get("end") else end_d
    if e_end is None or start >= e_end:
        return None
    price = round(_plan_price_prorated(e, start, e_end), 2)
    eff = _eff_cost_in_period(report, tool, start, e_end)
    fee = e["monthly_fee"]
    if eff is None:
        util, util_reason = None, ("sin datos horarios de coste para "
                                   "el periodo")
        headroom = None
    elif price <= 0:
        util, util_reason = None, ("precio del plan es 0 "
                                   "(pay-per-token)")
        headroom = round(fee - eff, 2)
    else:
        util, util_reason = round(eff / price, 4), None
        headroom = round(fee - eff, 2)
    return {
        "tool": tool, "label": e.get("label", tool),
        "start": e["start"], "end": e.get("end"),
        "monthly_fee": fee, "price": price,
        "eff_cost": round(eff, 2) if eff is not None else None,
        "utilization": util, "utilization_reason": util_reason,
        "break_even": fee, "headroom": headroom,
        "provenance": "assumed"}  # FPA-003

def _plan_panels(report, cfg):
    """Paneles de todas las suscripciones, ordenados por (tool, start)."""
    end_s = report["metadata"]["date_range"].get("end")
    end_d = date.fromisoformat(end_s) if end_s else None
    plans = []
    for tool, entries in cfg.get("subscriptions", {}).items():
        plans.extend(p for e in entries
                     if (p := _plan_panel(report, end_d, tool, e)) is not None)
    plans.sort(key=lambda p: (p["tool"], p["start"]))
    return plans

def _non_claude_cash(tools):
    """Cash real de las tools que no son claude-cli (FPA-002)."""
    total = sum((t.get("cost_real", 0.0) or 0.0) for t in tools.values()
                if isinstance(t, dict))
    claude = tools.get("claude-cli")
    if isinstance(claude, dict):
        total -= claude.get("cost_real", 0.0) or 0.0
    return total

def _four_case_sums(report, cash_by_ym, pro_fee, max_fee):
    """Sumas del cash del periodo bajo los 4 escenarios FPA-132 (el caso
    actual usa el cash real, la misma medida que los KPIs — coffe-a31.3)."""
    actual = all_p2p = all_pro = all_max = 0.0
    for ym, mo in sorted(report["monthly"].items()):
        if not mo["interactions"]:  # FPA-017
            continue
        actual += cash_by_ym.get(ym, 0.0)
        all_p2p += mo.get("cost_effective", 0.0) or 0.0
        tools = mo.get("tools", {})
        claude_active = bool(tools.get("claude-cli"))
        non_claude_cash = _non_claude_cash(tools)
        all_pro += (pro_fee if claude_active else 0.0) + non_claude_cash
        all_max += (max_fee if claude_active else 0.0) + non_claude_cash
    return actual, all_p2p, all_pro, all_max

def _four_cases(report, cfg):
    """(four_cases, note) FPA-132 sobre el cash del periodo."""
    cash_by_ym = {m["ym"]: m["cost_cash"] for m in build_months(report)}
    claude_fees = [e["monthly_fee"]
                   for e in cfg.get("subscriptions", {}).get("claude-cli", [])
                   if e.get("monthly_fee", 0) > 0]
    pro_fee = min(claude_fees) if claude_fees else 0.0
    max_fee = max(claude_fees) if claude_fees else 0.0
    actual, all_p2p, all_pro, all_max = \
        _four_case_sums(report, cash_by_ym, pro_fee, max_fee)
    note = ("el cash no-Claude de reportes con tools en shape contador "
            "(sin coste por tool) no se incluye en todo-Pro/todo-Max") \
        if any(not isinstance(t, dict)
               for mo in report["monthly"].values()
               for t in mo.get("tools", {}).values()) else None
    return {"actual": round(actual, 2),
            "todo_pay_per_token": round(all_p2p, 2),
            "todo_pro": round(all_pro, 2),
            "todo_max": round(all_max, 2)}, note

def build_plan_economy(report, cfg):
    """FPA-130…133: economía de suscripción pre-calculada.

    - plans: un panel por periodo del calendario (FPA-015) con utilización
      (FPA-130: efectivo del periodo ÷ precio pro-rateado del plan),
      break-even (FPA-131: el precio mensual del plan) y headroom
      (fee − efectivo consumido).
    - four_cases (FPA-132): cash del periodo bajo 4 escenarios — actual,
      todo pay-per-token (pricing del config), todo Pro, todo Max — usando
      cash no-Claude por tool. Efectivo y cash jamás se suman (FPA-002).
    - usage_limits_disclaimer (FPA-133): la equivalencia por coste
      efectivo ignora los usage limits del plan.

    Todo derivado del config → provenance assumed (FPA-003)."""
    four_cases, note = _four_cases(report, cfg)
    return {
        "provenance": "assumed",
        "plans": _plan_panels(report, cfg),
        "four_cases": four_cases,
        "four_cases_provenance": "assumed",
        "four_cases_note": note,
        "usage_limits_disclaimer": ("La equivalencia por coste efectivo "
            "ignora los usage limits del plan: un plan puede no alcanzar el "
            "consumo mostrado (FPA-133)."),
    }

def _recon_providers(state, meses):
    """Cash real del ledger por proveedor (*reported*); sin facturas en el
    periodo → n/a con razón (FPA-008), nunca $0 inventado."""
    providers = []
    for prov in sorted(state["by_month"]):
        serie = {ym: round(float(state["by_month"][prov].get(ym, 0.0) or 0.0), 2)
                 for ym in meses}
        tiene = any(ym in state["by_month"][prov] for ym in meses)
        providers.append({
            "provider": prov,
            "months": serie,
            "total_in_period": round(sum(serie.values()), 2) if tiene else None,
            "n_a_reason": None if tiene else "sin facturas en el periodo",
            "provenance": "reported"})
    return providers

def _recon_row(ym, tool, c):
    """Fila de reconciliación por tool y mes (valores *reported*/*assumed*)."""
    return {
        "ym": ym, "tool": tool,
        "charges_real": round(float(c.get("charges_real", 0.0) or 0.0), 2),
        "charges_provenance": "reported",
        "subscription_fee_implicit": round(float(c.get("subscription_fee_implicit", 0.0) or 0.0), 2),
        "cost_effective": round(float(c.get("cost_effective", 0.0) or 0.0), 2),
        "fee_month_total": round(float(c.get("fee_month_total", 0.0) or 0.0), 2)}

def _recon_rows(report):
    """Filas tal como las emite el tracker (charges_reconciliation_by_month)."""
    rec = report.get("charges_reconciliation_by_month") or {}
    return [_recon_row(ym, tool, rec[ym][tool])
            for ym in sorted(rec) for tool in sorted(rec[ym])]

def build_reconciliation(report):
    """coffe-a31.3 (CRG-F2): sección de reconciliación FPA-082.

    - providers: cash real del ledger por proveedor dentro del periodo del
      reporte (*reported*). Proveedor sin facturas en el periodo → n/a con
      razón (FPA-008), nunca $0 inventado; mes sin factura de un proveedor
      con facturas → $0 (la ausencia de factura es un dato, no un hueco).
    - rows: por tool y mes, real pagado (*reported*) vs fee implícito del
      calendario (*assumed*) vs efectivo estimado (*assumed*), tal como lo
      emite el tracker (charges_reconciliation_by_month).

    Sin ledger en el reporte → provenance unavailable + razón (FPA-008)."""
    state = _charges_state(report)
    meses = sorted(report.get("monthly", {}).keys())
    if state["provenance"] != "reported":
        return {"provenance": "unavailable", "source": state["source"],
                "reason": state["reason"], "providers": [], "rows": [],
                "months": meses}
    return {"provenance": "reported", "source": state["source"],
            "reason": None, "providers": _recon_providers(state, meses),
            "rows": _recon_rows(report), "months": meses}

# F5 (coffe-lat.6): patrones de uso, concurrencia y lifecycle (FPA-110…123,
# 028, 036, 098) — maths puras en Python; JS no recalcila nada (design.md).
# Doble coste jamás se suma (FPA-002); umbrales del config → *assumed* (FPA-003)
# y todo faltante del tracker sale como "n/a" con razón (FPA-008).

_DOW_SHORT = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

def _na(reason):
    """FPA-008: valor ausente del tracker → (None, razón), nunca vacío."""
    return None, reason

def _cfg_int(cfg, section, key, default):
    """Umbral numérico opcional del config con default de la spec."""
    return int(cfg.get(section, {}).get(key, default))

def _share(v):
    """Share como % con 1 decimal; None → 'n/a'."""
    return f"{100 * v:.1f}%" if v is not None else "n/a"

def build_heatmap(report):
    """FPA-110/142: matriz 7×24 de interacciones (fila = isoweekday,
    lunes=0) desde hourly. Timezone del tracker etiquetada; si falta, la
    vista va marcada unverified (FPA-142)."""
    grid = [[0] * 24 for _ in range(7)]
    for ts, h in report.get("hourly", {}).items():
        dow = date.fromisoformat(ts[:10]).isoweekday() - 1
        grid[dow][int(ts[11:13])] += h.get("interactions", 0)
    tz = report["metadata"].get("timezone")
    return {
        "timezone": tz, "verified": tz is not None,
        "unverified_reason": (None if tz else
                              "el tracker no registró timezone; las vistas "
                              "hora/día no están verificadas (FPA-142)"),
        "grid": grid, "total": sum(sum(row) for row in grid)}

def _hour_in_working_hours(dow, hour, wh):
    """¿La hora está dentro del horario laboral? days en isoweekday
    (1=lun…7=dom), start/end 'HH:MM' con end excluyente."""
    if dow + 1 not in wh.get("days", []):
        return False
    start = int(str(wh.get("start", "09:00"))[:2])
    end = int(str(wh.get("end", "18:00"))[:2])
    return start <= hour < end
from viz_fpa_common import _link  # noqa: E402

_link(globals())
