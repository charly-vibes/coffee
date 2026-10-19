#!/usr/bin/env python3
"""
viz-fpa.py — Dashboard FP&A de uso de IA (epic coffe-lat).

Fase 1 (coffe-lat.2): esqueleto stdlib-only con resumen ejecutivo.
Fase 6 (coffe-lat.7): arquitectura de 5 vistas por pregunta + Data & method
colapsado (FPA-150…157), presets de periodo YTD/Q/rango custom (FPA-090),
selector único fijo (FPA-153), top-5 + Mostrar todo (FPA-155), merges
(FPA-156), títulos-hallazgo con fallback (FPA-160/161), CTAs del config con
fail si target vacío (FPA-165…168, 172), Export CSV / Download SVG (FPA-169),
Share view con restauración y params inválidos ignorados (FPA-170/171),
accesibilidad móvil (FPA-175…179, 094), numeración de figuras (FPA-144) y
--check-docs contra el README (FPA-143).

Lee data/usage_report_v3.json + config/fpa.json y produce
data/fpa-dashboard.html: un único HTML autocontenido (FPA-001) con SVG inline,
tema compartido del sitio vía scripts/site_theme.py (coffe-gen.2; el modo
claro/oscuro propio se eliminó, FPA-093 modificada en el change
update-fpa-site-integration), formatos USD con separadores y
cifras tabulares (FPA-095), header con fecha de generación y versión del
tracker (FPA-005), marca de mes parcial con días transcurridos/total
(FPA-004), provenance tag reported/assumed en toda cifra (FPA-003),
separación estricta efectivo/cash jamás sumados (FPA-002), resumen ejecutivo
de 3–5 headlines con interpretación de una línea y fallback "n/a" con razón
(FPA-007/008; coffe-esc F3: la banda KPI ES el resumen ejecutivo, la
interpretación pasa al contexto del KPI card y las headline cards
desaparecen), claims narrativos solo con métrica + threshold visibles
(FPA-009) y validación de schema con exit non-zero listando los campos
fallidos (FPA-006).

Las matemáticas se computan en Python (design.md): el modelo va embebido
como JSON (`id="fpa-model"`) para que las fases siguientes (F2+) re-escale
en JS sin recalcular.

Uso: python3 scripts/viz-fpa.py [--report PATH] [--config PATH] [--out PATH]
     python3 scripts/viz-fpa.py --check-docs [--readme PATH]
"""

import argparse
import calendar
import html
import json
import re
import sys
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: E402
import schema_validate  # noqa: E402
import site_theme  # noqa: E402
import viz_fpa_guide as guide  # noqa: E402

REPORT = Path("data/usage_report_v3.json")
CONFIG = Path("config/fpa.json")
OUT = Path("data/fpa-dashboard.html")
SCHEMA = Path("specs/usage-report-v3.schema.json")

MONTH_SHORT = {"01": "ene", "02": "feb", "03": "mar", "04": "abr",
               "05": "may", "06": "jun", "07": "jul", "08": "ago", "09": "sep",
               "10": "oct", "11": "nov", "12": "dic"}


# ======================================================================
# Ledger de cargos reales (coffe-a31.3, CRG-F2; epic coffe-a31 FPA-013/082)
# cash cost = cargos reales del ledger data/charges.json (*reported*).
# Efectivo (tracker) y cash (ledger) jamás se suman (FPA-002).
# ======================================================================

def _charges_state(report):
    """Estado del ledger en el reporte: (by_month, provenance, source, reason).

    by_month: {provider: {YYYY-MM: amount}} tal como lo emite el tracker
    (solo kinds IA; api_cycle aporta $0 y storage queda fuera). Si el
    reporte no trae el ledger (shape previo a coffe-a31.2 o ledger
    ilegible), provenance = "unavailable" y razón no vacía (FPA-008):
    el cash cae al fallback del tracker con provenance *assumed*.
    """
    meta = report.get("metadata", {}) or {}
    by_month = report.get("charges_real_by_month") or {}
    source = meta.get("charges_source") or "data/charges.json"
    if meta.get("charges_provenance") == "reported" and isinstance(by_month, dict):
        return {"by_month": by_month, "provenance": "reported",
                "source": source, "reason": None}
    return {"by_month": {}, "provenance": "unavailable",
            "source": source,
            "reason": (meta.get("charges_reason")
                       or "el reporte no incluye el ledger de cargos reales "
                       "(shape previo a coffe-a31.2); cash = suma del tracker "
                       "(assumed)")}


def _charges_cash_by_ym(report):
    """{ym: cash real del ledger} restringido a los meses del reporte.
    Refund negativo descuenta (dinero devuelto, no gastado)."""
    state = _charges_state(report)
    out = {}
    for ym in report.get("monthly", {}):
        out[ym] = round(sum(pv.get(ym, 0.0) or 0.0
                            for pv in state["by_month"].values()), 2)
    return out


# ======================================================================
# Formatos (FPA-095): USD con separadores, enteros con separadores
# ======================================================================

def fmt_usd(x):
    """Formato USD: $1,234.50 — separador de miles, 2 decimales."""
    if x is None:
        return "n/a"
    return "${:,.2f}".format(x)


def fmt_int(x):
    """Formato de miles: 1,397,354."""
    if x is None:
        return "n/a"
    return "{:,}".format(x)


def month_label(ym):
    y, m = ym.split("-")
    return f"{MONTH_SHORT.get(m, m)} {y[2:]}"


# ======================================================================
# Matemáticas puras (sin IO)
# ======================================================================

def month_total_days(ym):
    """Días del mes 'YYYY-MM' (28/29/30/31)."""
    y, m = int(ym[:4]), int(ym[5:7])
    return calendar.monthrange(y, m)[1]


def fme(value, elapsed, total):
    """Full-month equivalent: valor parcial × días_del_mes / días_transcurridos.

    None si elapsed es 0 (no hay base para anualizar).
    """
    if elapsed == 0:
        return None
    return value * total / elapsed


def build_months(report):
    """Meta por mes: interacciones, costes separados (FPA-002), parcial (FPA-004).

    has_data=False marca meses sin datos (FPA-017): se excluyen de trends y
    unit-cost; el generador los muestra con marker n/a.

    coffe-a31.3 (CRG-F2, FPA-031 cambia de semántica): cost_cash = cargos
    reales del ledger (*reported*, con refund que descuenta); sin ledger
    en el reporte, fallback a la suma del tracker con provenance *assumed*
    y razón (FPA-008). Efectivo y cash jamás se suman (FPA-002).
    """
    end = report["metadata"]["date_range"]["end"]
    state = _charges_state(report)
    cash_by_ym = _charges_cash_by_ym(report)
    months = []
    for ym in sorted(report["monthly"].keys()):
        mo = report["monthly"][ym]
        total_days = month_total_days(ym)
        last = date.fromisoformat(end) if end else None
        partial = last is not None and end[:7] == ym and last.day < total_days
        if state["provenance"] == "reported":
            cash = cash_by_ym[ym]
            cash_prov, cash_reason = "reported", None
            charges_prov = {p: round(pv[ym], 2) for p, pv in
                            state["by_month"].items() if ym in pv}
        else:
            cash = mo["cost_real"]
            cash_prov, cash_reason = "assumed", state["reason"]
            charges_prov = None
        months.append({
            "ym": ym,
            "label": month_label(ym),
            "interactions": mo["interactions"],
            "cost_effective": mo["cost_effective"],
            "cost_cash": cash,
            "cash_provenance": cash_prov,
            "cash_reason": cash_reason,
            "charges_by_provider": charges_prov,
            "subscription_fees": mo.get("subscription_fees", 0.0),
            "total_days": total_days,
            "elapsed": last.day if partial else total_days,
            "partial": partial,
            "has_data": mo["interactions"] > 0,
        })
    return months


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

    heads = []

    def head(key, label, value, provenance, interp, reason=None, fmt=fmt_usd):
        heads.append({
            "key": key, "label": label, "value": value,
            "display": fmt(value) if value is not None else "n/a",
            "provenance": provenance, "interp": interp,
            "reason": reason,
        })

    if eff > 0:
        head("cost_effective", "Coste efectivo", eff, "reported",
             f"estimación API-equivalente en {n} {'mes' if n == 1 else 'meses'} de datos "
             f"(≈ {fmt_usd(eff / n)}/mes)")
    else:
        head("cost_effective", "Coste efectivo", None, "reported", "",
             reason="sin meses con datos en el reporte")

    head("cost_cash", "Coste cash", cash if cash else None,
         "reported" if cash_reported else "assumed",
         (f"cargos reales del ledger ({_charges_state(report)['source']}) "
          f"en {n} {'mes' if n == 1 else 'meses'} de datos "
          f"(≈ {fmt_usd(cash / n)}/mes)") if cash_reported and cash > 0
         else (f"cuotas de suscripción + cargas del tracker, sin ledger "
               f"en el reporte (≈ {fmt_usd(cash / n)}/mes)")
         if cash > 0 else "",
         reason="sin meses con datos en el reporte" if n == 0
         else "sin facturas en el periodo (ledger)"
         if cash_reported and cash == 0
         else "sin cargas cash registradas" if cash == 0 else None)

    if eff > 0 and cash > 0:
        lev = eff / cash
        head("leverage", "Leverage", lev, "reported",
             f"el coste efectivo equivale a {lev:.1f}× el cash cost",
             fmt=lambda v: f"{v:.1f}×")
    else:
        head("leverage", "Leverage", None, "reported", "",
             reason="requiere coste efectivo y cash mayores que 0")

    if interactions > 0 and eff > 0:
        per_1k = eff / (interactions / 1000)
        head("cost_per_1k", "Coste por 1k interacciones", per_1k, "reported",
             f"por cada {fmt_int(1000)} interacciones "
             f"({fmt_int(interactions)} en el periodo)",
             fmt=fmt_usd)
    else:
        head("cost_per_1k", "Coste por 1k interacciones", None, "reported", "",
             reason="sin interacciones en el periodo" if interactions == 0
             else "sin coste efectivo")

    # Outcome metric: coste efectivo por commit, donde haya datos (FPA-007)
    commits = sum(
        (o.get("commits", 0) or 0)
        for m in report["monthly"].values() if m["interactions"] > 0
        for o in (m.get("outcomes_by_project") or {}).values()
    )
    if commits > 0 and eff > 0:
        head("outcome", "Coste efectivo por commit", eff / commits, "reported",
             f"{fmt_int(commits)} commits en el periodo")
    else:
        head("outcome", "Coste efectivo por commit", None, "reported", "",
             reason="el reporte no registra commits/releases"
             if commits == 0 else "sin coste efectivo")

    return heads


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
    }


# ======================================================================
# F3 (coffe-lat.4): presupuesto, bridge PVM y forecast
# (FPA-050…077) — maths puras en Python; JS solo re-escala varianza y
# forecast con las fórmulas reproducidas (design.md). El presupuesto de
# efectivo es informativo (soft, design.md OQ-1); el gestionado es el de
# cash. Efectivo y cash jamás se suman (FPA-002).
# ======================================================================

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
    none_cells = {
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
    rows = []
    tot = {"actual_cash": 0.0, "budget_cash": 0.0,
           "actual_eff": 0.0, "budget_eff": 0.0}
    any_budget = False
    for meta in build_months(report):
        ym = meta["ym"]
        bvals = _budget_for(cfg, ym)
        pro_rate = meta["elapsed"] / meta["total_days"]  # FPA-052
        row = {
            "ym": ym, "label": meta["label"], "in_budget": bvals is not None,
            "partial": meta["partial"],
            "elapsed": meta["elapsed"], "total_days": meta["total_days"],
            "pro_rate": round(pro_rate, 6),
            "reason_cash": None, "reason_eff": None,
        }
        row.update(none_cells)
        if bvals is None:
            reason = f"fuera del periodo presupuestado (desde {start})"
            row["reason_cash"] = row["reason_eff"] = reason
        elif not meta["has_data"]:  # FPA-017/008
            reason = "mes sin datos en el reporte"
            row["reason_cash"] = row["reason_eff"] = reason
        else:
            budget_cash = round(bvals[0] * pro_rate, 2)
            budget_eff = round(bvals[1] * pro_rate, 2)
            actual_cash = round(meta["cost_cash"], 2)
            actual_eff = round(meta["cost_effective"], 2)
            var_cash = round(_variance(actual_cash, budget_cash), 2)
            var_eff = round(_variance(actual_eff, budget_eff), 2)
            pct_cash = round(var_cash / budget_cash, 4) if budget_cash else None
            pct_eff = round(var_eff / budget_eff, 4) if budget_eff else None
            row.update({
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
            })
            tot["actual_cash"] += actual_cash
            tot["budget_cash"] += budget_cash
            tot["actual_eff"] += actual_eff
            tot["budget_eff"] += budget_eff
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


def build_bridge(prev, cur, prev_meta, cur_meta):
    """FPA-060…067: bridge del coste efectivo prior→seleccionado.

    Con mes parcial usa valores FME y lo etiqueta (FPA-066). Modelos sin
    mes previo usan su rate actual como prior — contribuyen solo a Mix
    (FPA-064). Identidad Volume+Mix+Rate = Δcoste dentro de $0.01 (FPA-065):
    el residuo de redondeo se absorbe en Mix y se verifica con falla ruidosa.
    Sin desglose de coste por modelo → componentes n/a con razón (FPA-008).
    Eje truncado etiquetado cuando |Δ| es pequeño vs los totales (FPA-067).
    """
    def scale(meta):
        return meta["total_days"] / meta["elapsed"] if meta["partial"] else 1.0

    sp, sc = scale(prev_meta), scale(cur_meta)
    fme = abs(sp - 1.0) > 1e-9 or abs(sc - 1.0) > 1e-9

    def eff_by_model(mo):
        out = {}
        for model, v in (mo or {}).items():
            if isinstance(v, dict):
                out[model] = {"q": v.get("interactions", 0),
                              "cost": v.get("cost_effective", 0.0) or 0.0}
            else:  # counters del reporte viejo: sin coste por modelo
                out[model] = {"q": v, "cost": 0.0}
        return out

    pm, cm = eff_by_model(prev.get("models")), eff_by_model(cur.get("models"))
    q0, q1 = prev["interactions"], cur["interactions"]
    cost0, cost1 = prev["cost_effective"], cur["cost_effective"]
    if fme:
        q0, q1 = q0 * sp, q1 * sc
        cost0, cost1 = cost0 * sp, cost1 * sc

    label = month_label(cur_meta["ym"])
    fme_note = (f" (FME: {month_label(prev_meta['ym'])} ×{sp:.2f} · "
                f"{label} ×{sc:.2f})") if fme else ""
    common = {
        "ym": cur_meta["ym"], "label": label + fme_note, "fme": fme,
        "cost0": round(cost0, 2), "cost1": round(cost1, 2),
        "delta": round(cost1 - cost0, 2),
        "truncated": abs(cost1 - cost0) < 0.1 * max(abs(cost0), abs(cost1), 1e-9),
    }

    # FPA-008: sin coste por modelo no hay rates → PVM no computable
    if (cost0 and not sum(d["cost"] for d in pm.values())) or \
       (cost1 and not sum(d["cost"] for d in cm.values())):
        common.update({
            "volume": None, "mix": None, "rate": None,
            "identity_residual": None, "axis_label": "eje completo",
            "n_a_reason": ("el reporte no desglosa coste por modelo; el bridge "
                           "requiere rates por modelo (FPA-061…063)"),
        })
        return common

    by_model = []
    for m in sorted(set(pm) | set(cm)):
        d0, d1 = pm.get(m, {}), cm.get(m, {})
        # cantidades escaladas al factor FME de su mes (coherentes con Q0/Q1)
        q0_i, c0_i = d0.get("q", 0) * sp, d0.get("cost", 0.0) * sp
        q1_i, c1_i = d1.get("q", 0) * sc, d1.get("cost", 0.0) * sc
        # FPA-064: sin mes previo → rate prior = rate actual (solo Mix)
        p0 = (c0_i / q0_i) if q0_i else ((c1_i / q1_i) if q1_i else 0.0)
        p1 = (c1_i / q1_i) if q1_i else 0.0
        by_model.append((m, q0_i, p0, q1_i, p1))

    volume, mix, rate = build_pvm(q0, q1, by_model)
    delta = cost1 - cost0
    residual = volume + mix + rate - delta
    mix -= residual  # FPA-065: absorbe el residuo de redondeo en Mix
    residual = volume + mix + rate - delta
    if abs(residual) > 0.01:
        raise ValueError(
            f"identidad del bridge rota (FPA-065): V+M+R = "
            f"{volume + mix + rate:.4f} vs Δ {delta:.4f} (residuo {residual:.4f})")
    common.update({
        "volume": round(volume, 2), "mix": round(mix, 2),
        "rate": round(rate, 2),
        "identity_residual": round(residual, 6),
        "axis_label": ("eje truncado (Δ pequeño vs totales)"
                       if common["truncated"] else "eje completo"),
    })
    return common


def waterfall_svg(b, w=420, h=150):
    """FPA-060: waterfall Volume/Mix/Rate como SVG inline determinista.
    Barras flotantes desde cost₀ hasta cost₁; eje truncado etiquetado
    cuando aplica (FPA-067)."""
    steps = [("Volumen", b["volume"]), ("Mix", b["mix"]), ("Rate", b["rate"])]
    base = b["cost0"]
    floats = []
    lvl = base
    for _, v in steps:
        floats.append((lvl, lvl + v))
        lvl += v
    end = lvl
    lo = min([base, end] + [min(a, z) for a, z in floats])
    hi = max([base, end] + [max(a, z) for a, z in floats])
    span = (hi - lo) or 1.0
    pad = 0.08 * span
    lo, hi = lo - pad, hi + pad

    def Y(v):
        return h - 24 - (v - lo) / (hi - lo) * (h - 40)

    n = len(steps) + 2
    bw = min(60.0, (w - 30) / n - 12)
    gap = (w - 20 - n * bw) / (n - 1)

    def rect(x, y_a, y_b, cls, name, val):
        top, bot = min(y_a, y_b), max(y_a, y_b)
        label = f"{name}: {_fmt_signed(val)}"
        return (f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{max(bot - top, 1.0):.1f}" class="{cls}" '
                f'tabindex="0" role="img" aria-label="{esc_html(label)}" '
                f'data-label="{esc_html(name)}" '
                f'data-value="{esc_html(_fmt_signed(val))}"/>')

    bars, texts = [], []
    x = 12.0
    bars.append(rect(x, Y(lo), Y(base), "wfb", "cost₀", b["cost0"]))
    texts.append(f'<text x="{x + bw / 2:.1f}" y="{Y(base) - 3:.1f}" class="wft" '
                 f'text-anchor="middle">{fmt_usd(b["cost0"])}</text>')
    x += bw + gap
    for (name, v), (fa, fz) in zip(steps, floats):
        bars.append(rect(x, Y(fa), Y(fz), "wfup" if v >= 0 else "wfdn",
                         name, v))
        ytxt = Y(max(fa, fz)) - 3
        texts.append(f'<text x="{x + bw / 2:.1f}" y="{ytxt:.1f}" class="wft" '
                     f'text-anchor="middle">{_fmt_signed(v)}</text>')
        x += bw + gap
    bars.append(rect(x, Y(lo), Y(end), "wfb", "cost₁", b["cost1"]))
    texts.append(f'<text x="{x + bw / 2:.1f}" y="{Y(end) - 3:.1f}" class="wft" '
                 f'text-anchor="middle">{fmt_usd(b["cost1"])}</text>')
    labels = []
    lx = 12.0
    for name in ["cost₀"] + [n for n, _ in steps] + ["cost₁"]:
        labels.append(f'<text x="{lx + bw / 2:.1f}" y="{h - 6:.1f}" class="wfl" '
                      f'text-anchor="middle">{name}</text>')
        lx += bw + gap
    axis = (f'<text x="12" y="14" class="wfl">{esc_html(b["axis_label"])}</text>'
            if b.get("truncated") else "")
    return (f'<svg class="wf chart" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="bridge {esc_html(b["label"] or "")}: '
            f'Volumen {fmt_usd(b["volume"])}, Mix {fmt_usd(b["mix"])}, '
            f'Rate {fmt_usd(b["rate"])}">'
            f'{axis}{"".join(bars)}{"".join(texts)}{"".join(labels)}</svg>')


def build_mix_stack(report):
    """FPA-068: mix de modelos por mes como shares que suman 100%.
    Share de coste efectivo cuando hay desglose; con counters (sin coste
    por modelo) → proxy por interacciones, señalado como proxy (FPA-008)."""
    stack = {}
    for meta in build_months(report):
        if not meta["has_data"]:
            continue
        models = report["monthly"][meta["ym"]].get("models") or {}
        segs, proxy = [], False
        for model, v in models.items():
            if isinstance(v, dict):
                segs.append({"model": model,
                             "cost": v.get("cost_effective", 0.0) or 0.0,
                             "interactions": v.get("interactions", 0)})
            else:
                segs.append({"model": model, "cost": 0.0, "interactions": v})
                proxy = True
        total_cost = sum(s["cost"] for s in segs)
        total_inter = sum(s["interactions"] for s in segs)
        if total_cost > 0:
            metric = "coste efectivo"
            for s in segs:
                s["share"] = round(s["cost"] / total_cost, 6)
        elif total_inter > 0:
            metric = "interacciones (proxy)"
            proxy = True
            for s in segs:
                s["share"] = round(s["interactions"] / total_inter, 6)
        else:
            continue
        segs = sorted((s for s in segs if s["share"] > 0),
                      key=lambda s: -s["share"])
        stack[meta["ym"]] = {
            "label": meta["label"], "segments": segs, "proxy": proxy,
            "metric": metric,
            "proxy_reason": ("modelos sin coste desglosado en el reporte; "
                             "shares por interacciones (proxy)" if proxy else None),
        }
    return stack


def build_bridge_section(report):
    """FPA-060/068: pares prior→mes con datos (waterfall por par) + stack
    100% del mix por mes."""
    months = [m for m in build_months(report) if m["has_data"]]
    raw = report["monthly"]
    pairs = {}
    prev = None
    for meta in months:
        if prev is not None:
            b = build_bridge(raw[prev["ym"]], raw[meta["ym"]], prev, meta)
            if not b.get("n_a_reason"):
                b["svg"] = waterfall_svg(b)
            pairs[meta["ym"]] = b
        prev = meta
    return {"provenance": "reported", "pairs": pairs,
            "mix_stack": build_mix_stack(report)}


def _plan_for(cfg, tool, when):
    """Entrada de suscripción activa para `tool` en `when` (date)."""
    for entry in cfg["subscriptions"].get(tool, []):
        start = date.fromisoformat(entry["start"])
        end = date.fromisoformat(entry["end"]) if entry.get("end") else None
        if start <= when and (end is None or when < end):
            return entry
    return None


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
    n_a = {
        "provenance": "assumed", "rows": [], "outlook": False,
        "remainder": None,
    }
    if not end_date or not end_ym:
        n_a["n_a_reason"] = "el reporte no registra fecha de fin"
        return n_a
    months = [m for m in build_months(report) if m["has_data"]]
    if len(months) < 3:
        n_a["n_a_reason"] = (f"se requieren 3 meses con datos para el run-rate "
                             f"base; el reporte cubre {len(months)}")
        return n_a

    base_months = months[-3:]
    eff_base = sum(m["cost_effective"] * m["total_days"] / m["elapsed"]
                   for m in base_months) / 3
    q_base = sum(m["interactions"] * m["total_days"] / m["elapsed"]
                 for m in base_months) / 3
    rate_base = eff_base / q_base if q_base else None

    # cash base: p2p del cash real del último mes (ledger *reported* cuando
    # hay ledger; suma tracker assumed si no) menos la cuota del calendario
    mo_last = report["monthly"][months[-1]["ym"]]
    fees_last = mo_last.get("subscription_fees", 0.0) or 0.0
    p2p_last = max(months[-1]["cost_cash"] - fees_last, 0.0)
    f_last = (months[-1]["total_days"] / months[-1]["elapsed"]
              if months[-1]["elapsed"] else 1.0)
    p2p_base = p2p_last * f_last

    # FPA-075: resto del mes parcial como fila forecast separada
    last = months[-1]
    remainder = None
    if last["partial"]:
        remainder = {
            "ym": last["ym"], "label": month_label(last["ym"]),
            "eff": round(last["cost_effective"] * (f_last - 1.0), 2),
            "cash": round(p2p_last * (f_last - 1.0), 2),
            "elapsed": last["elapsed"], "total_days": last["total_days"],
            "marker": {"symbol": "△", "text": "forecast", "favorable": None},
        }

    # FPA-071: planes futuros del calendario de suscripciones del config;
    # la suscripción primaria es la de mayor cuota activa al cierre
    primary, best_fee = None, -1.0
    for tool in cfg["subscriptions"]:
        e = _plan_for(cfg, tool, end_date)
        if e and e["monthly_fee"] > best_fee:
            primary, best_fee = tool, e["monthly_fee"]
    fallback_plan = None
    if primary is None:
        # FPA-082 (cargos reales): con el calendario corregido a las
        # facturas puede no haber ninguna suscripción activa al cierre
        # (Free/cancelado). El plan default pasa a "sin suscripción"
        # (fee $0 → cash = p2p escalado, FPA-073) y los periodos del
        # calendario quedan como escenarios hipotéticos; la herramienta
        # primaria es la de la última suscripción vigente (fin más
        # tardío; a igual fin, la de mayor cuota).
        best_cand, best_tool = None, None
        for tool, entries_ in cfg["subscriptions"].items():
            for e in entries_:
                if not e.get("end"):
                    continue
                cand = (e["end"], e["monthly_fee"], tool)
                if best_cand is None or cand > best_cand:
                    best_cand, best_tool = cand, tool
        if best_tool is None:
            primary = next(iter(cfg["subscriptions"]), None)
            entries = list(cfg["subscriptions"].get(primary, []))
        else:
            primary = best_tool
            entries = list(cfg["subscriptions"][primary])
            fallback_plan = {"key": f"{primary}:none", "tool": primary,
                             "label": "Sin suscripción (pay-per-token)",
                             "fee": 0.0}
    else:
        entries = cfg["subscriptions"].get(primary, [])
    plans = [{"key": f"{primary}:{i}", "tool": primary,
              "label": e["label"], "fee": e["monthly_fee"]}
             for i, e in enumerate(entries)]
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

    # meses futuros: del mes siguiente al cierre hasta diciembre (FPA-070)
    fut = [f"{end_ym[:4]}-{mm:02d}"
           for mm in range(int(end_ym[5:7]) + 1, 13)]

    fc = {
        "provenance": "assumed",  # FPA-003: run-rate y planes derivan de config
        "base": {
            "eff_base": round(eff_base, 6),
            "q_base": round(q_base, 6),
            "rate_base": round(rate_base, 6) if rate_base is not None else None,
            "fee_base": round(fees_last, 2),
            "p2p_base": round(p2p_base, 6),
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


def apply_scenario(fc, growth, rate_chg, plan_key):
    """FPA-072/073: escenario sobre el run-rate base.
    Efectivo: base×(1+g)ⁿ×(1+r) (FPA-072; equivale a Q×(1+g)ⁿ×rate×(1+r)).
    Cash: cuota del plan + p2p base escalado por volumen (FPA-073).
    Misma fórmula reproducida en JS para el recompute sin reload (FPA-077)."""
    if not fc or fc.get("n_a_reason"):
        return {"rows": []}
    base = fc["base"]
    fee = next((p["fee"] for p in fc["plans"] if p["key"] == plan_key),
               base["fee_base"])
    rows = []
    for i, ym in enumerate(fc["future_months"]):
        f = (1 + growth) ** (i + 1)
        rows.append({
            "ym": ym, "label": month_label(ym),
            "eff": round(base["eff_base"] * f * (1 + rate_chg), 2),
            "cash": round(fee + base["p2p_base"] * f, 2),
            "marker": {"symbol": "△", "text": "forecast", "favorable": None},
        })
    return {"rows": rows}


def build_outlook(report, cfg, fc):
    """FPA-074: YTD real + outlook vs presupuesto, con varianza y markers.
    Presupuesto pro-rateado en meses parciales del reporte (FPA-052)."""
    months = [m for m in build_months(report) if m["has_data"]]
    actual_cash = sum(m["cost_cash"] for m in months)
    actual_eff = sum(m["cost_effective"] for m in months)
    rem = fc["remainder"]
    projected_cash = (actual_cash + rem["cash"]
                      + sum(r["cash"] for r in fc["rows"]))
    projected_eff = (actual_eff + rem["eff"]
                     + sum(r["eff"] for r in fc["rows"]))
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
    var_cash = projected_cash - budget_cash
    var_eff = projected_eff - budget_eff
    return {
        "actual_cash": round(actual_cash, 2),
        "actual_eff": round(actual_eff, 2),
        "budget_cash": round(budget_cash, 2),
        "budget_eff": round(budget_eff, 2),
        "projected_cash": round(projected_cash, 2),
        "projected_eff": round(projected_eff, 2),
        "variance_cash": round(var_cash, 2),
        "variance_eff": round(var_eff, 2),
        "variance_pct_cash": (round(var_cash / budget_cash, 4)
                              if budget_cash else None),
        "variance_pct_eff": (round(var_eff / budget_eff, 4)
                             if budget_eff else None),
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
            if e.get("monthly_fee", 0) > 0
            and e["start"][:7] <= ym
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
                            f"(suscripción cancelada/expirada) — revisar "
                            f"fechas del plan u otras cuentas pagas"),
                "evidence": {"month": m["ym"], "claude_eff": round(eff, 2),
                             "plan_fee": 0.0, "multiple": None,
                             "threshold": th},
            })
            continue
        multiple = eff / fee
        if multiple > th:
            alerts.append({
                "severity": "high", "rule": "verify-plan",
                "message": (f"{month_label(m['ym'])}: efectivo Claude "
                            f"{fmt_usd(eff)} = {multiple:.1f}× el precio del "
                            f"plan (${fee:,.0f}/mes) — revisar fechas del plan "
                            f"u otras cuentas pagas"),
                "evidence": {"month": m["ym"], "claude_eff": round(eff, 2),
                             "plan_fee": fee,
                             "multiple": round(multiple, 2),
                             "threshold": th},
            })


def _check_reconciliation(report, cfg, alerts):
    """FPA-082: cash real del ledger (*reported*) vs cargas implícitas del
    calendario (*assumed*) + p2p reportado del ledger.

    coffe-a31.3: con ledger disponible, por mes del reporte se compara
    real = Σ cargos del ledger contra implícito = fees del calendario +
    pay_per_token_charges (solo si es *reported*: los créditos/reembolsos
    del ledger no son cargas del calendario, no se fabrican como
    implícitos). El caso motivador queda resuelto con el calendario
    corregido (jun 2026: real $0 vs implícito $0).

    Sin ledger en el reporte → fallback al chequeo previo: cash del
    tracker vs fees + p2p tracker (reportes de shape antiguo)."""
    state = _charges_state(report)
    tol = _th(cfg, "reconcile_tolerance_pct", 10.0) / 100.0
    if state["provenance"] != "reported":
        fees_by_month = report.get("subscription_fees_by_month") or {}
        reported = implied = 0.0
        for ym, mo in report["monthly"].items():
            cash = mo.get("cost_real", 0.0) or 0.0
            fee = fees_by_month.get(ym, 0.0) or 0.0
            reported += cash
            implied += fee + max(cash - fee, 0.0)
        if implied <= 0:
            return
        diff_pct = abs(reported - implied) / implied
        if diff_pct > tol:
            alerts.append({
                "severity": "high", "rule": "reconciliation",
                "message": (f"Reconciliación: cash reportado {fmt_usd(reported)} "
                            f"vs cargas implícitas del calendario "
                            f"{fmt_usd(implied)} ({diff_pct * 100:.1f}% off, "
                            f"tolerancia {tol * 100:.0f}%) — revisar suscripciones "
                            f"o cargas no registradas"),
                "evidence": {"reported": round(reported, 2),
                             "implied": round(implied, 2),
                             "diff_pct": round(diff_pct, 4),
                             "tolerance_pct": tol * 100},
            })
        return
    fees_by_month = report.get("subscription_fees_by_month") or {}
    reported = implied = 0.0
    divergentes = []
    for ym, mo in report["monthly"].items():
        real = round(sum(pv.get(ym, 0.0) or 0.0
                         for pv in state["by_month"].values()), 2)
        p2p = ((mo.get("pay_per_token_charges") or 0.0)
               if mo.get("pay_per_token_provenance") == "reported" else 0.0)
        fee = fees_by_month.get(ym, 0.0) or 0.0
        imp = round(fee + p2p, 2)
        reported += real
        implied += imp
        if abs(real - imp) > 0.005:
            divergentes.append({"month": ym, "real": real, "implicit": imp})
    if implied <= 0 and reported <= 0:
        return
    if implied > 0:
        diff_pct = abs(reported - implied) / implied
    else:
        diff_pct = 1.0 if reported > 0 else 0.0
    if diff_pct > tol:
        alerts.append({
            "severity": "high", "rule": "reconciliation",
            "message": (f"Reconciliación: cash real del ledger "
                        f"{fmt_usd(reported)} vs cargas implícitas del "
                        f"calendario {fmt_usd(implied)} "
                        f"({diff_pct * 100:.1f}% off, tolerancia "
                        f"{tol * 100:.0f}%) — revisar suscripciones o "
                        f"cargas no registradas"),
            "evidence": {"reported": round(reported, 2),
                         "implied": round(implied, 2),
                         "diff_pct": round(diff_pct, 4),
                         "tolerance_pct": tol * 100,
                         "source": state["source"],
                         "divergent_months": divergentes[:5]},
        })


def _check_budget(report, cfg, alerts):
    """FPA-083: mes sobre presupuesto (cash o efectivo) → alerta con el
    monto de overage. Reutiliza la tabla de varianza F3 (pro-rating)."""
    for r in build_budget(report, cfg)["rows"]:
        if r["variance_cash"] is not None and r["variance_cash"] > 0.005:
            alerts.append({
                "severity": "high", "rule": "budget",
                "message": (f"{r['label']}: cash {r['actual_display_cash']} "
                            f"sobre el presupuesto "
                            f"({r['budget_display_cash']}) por "
                            f"{_fmt_signed(r['variance_cash'])}"),
                "evidence": {"month": r["ym"], "measure": "cash",
                             "overage_cash": r["variance_cash"],
                             "budget_cash": r["budget_cash"]},
            })
        # coffe-ox3: el presupuesto efectivo es informativo (soft, design.md
        # OQ-1) — su overage queda visible en la tabla de varianza, sin
        # alerta (disparaba todos los meses con montos provisionales).


def _check_unit_cost(months, cfg, alerts):
    """FPA-084: coste por 1k interacciones (efectivo) +X% MoM → alerta.
    Guard coffe-wxc: la base previa debe superar el mínimo
    (`unit_cost_min_base_per_1k`, default $5/1k) — el MoM sobre base casi
    nula es falso positivo (+3871% desde $1.45/1k). Meses sin datos se
    excluyen de la comparación (FPA-017). El ratio efectivo/interacciones
    es invariante al escalado FME (numerador y denominador se anualizan
    por el mismo factor), así que los meses parciales son comparables sin
    ajuste."""
    th = _th(cfg, "unit_cost_rise_pct", 15.0) / 100.0
    min_base = _th(cfg, "unit_cost_min_base_per_1k", 5.0)
    prev = None
    for m in months:
        if not m["has_data"]:
            continue
        cur = (m["cost_effective"] / m["interactions"] * 1000
               if m["interactions"] else None)
        if (cur is not None and prev is not None and prev > 0
                and prev > min_base):
            rise = cur / prev - 1
            if rise > th:
                alerts.append({
                    "severity": "medium", "rule": "unit-cost",
                    "message": (f"{m['label']}: coste por 1k interacciones "
                                f"{fmt_usd(cur)} vs {fmt_usd(prev)} "
                                f"(+{rise * 100:.1f}%, umbral "
                                f"+{th * 100:.0f}%)"),
                    "evidence": {"month": m["ym"],
                                 "prev_per_1k": round(prev, 4),
                                 "cur_per_1k": round(cur, 4),
                                 "rise_pct": round(rise, 4),
                                 "threshold_pct": th * 100,
                                 "min_base_per_1k": min_base},
                })
        prev = cur


def _premium_share(month, premium_models):
    """Share de coste efectivo de modelos premium en el mes (0–1).
    Premium = el nombre de modelo matchea alguna entrada de
    config.premium_models (FPA-037; default Opus)."""
    models = month.get("models", {})
    total = sum((ms.get("cost_effective", 0.0) or 0.0)
                for ms in models.values() if isinstance(ms, dict))
    if not total:
        return None
    premium = sum((ms.get("cost_effective", 0.0) or 0.0)
                  for name, ms in models.items()
                  if isinstance(ms, dict)
                  and any(p.lower() in name.lower()
                          for p in premium_models))
    return premium / total


def _check_premium_mix(report, cfg, months, alerts):
    """FPA-085: share premium +X pts vs 3 meses atrás → alerta."""
    th = _th(cfg, "premium_share_rise_pts", 5.0)
    premium_models = cfg.get("premium_models", [])
    shares = [m for m in months if m["has_data"]]
    for i in range(3, len(shares)):
        cur, old = _premium_share(report["monthly"][shares[i]["ym"]],
                                  premium_models), \
            _premium_share(report["monthly"][shares[i - 3]["ym"]],
                           premium_models)
        if cur is None or old is None:
            continue
        rise_pts = (cur - old) * 100
        if rise_pts > th:
            alerts.append({
                "severity": "medium", "rule": "mix",
                "message": (f"{shares[i]['label']}: share premium "
                            f"{cur * 100:.1f}% vs "
                            f"{shares[i - 3]['label']} "
                            f"({old * 100:.1f}%) = +{rise_pts:.1f} pts "
                            f"(umbral +{th:.0f} pts)"),
                "evidence": {"month": shares[i]["ym"],
                             "share": round(cur, 4),
                             "base_month": shares[i - 3]["ym"],
                             "base_share": round(old, 4),
                             "rise_pts": round(rise_pts, 2),
                             "threshold_pts": th},
            })


def _check_concentration(report, cfg, alerts):
    """FPA-086: top-3 proyectos concentran >X% del efectivo → alerta."""
    th = _th(cfg, "concentration_top3_pct", 50.0) / 100.0
    totals = []
    grand = 0.0
    for proj, months_map in (report.get("project_monthly") or {}).items():
        cost = sum(v.get("cost_effective", 0.0) or 0.0
                   for v in months_map.values())
        if cost > 0:
            totals.append((proj, cost))
            grand += cost
    if grand <= 0:
        return
    totals.sort(key=lambda t: -t[1])
    top3 = totals[:3]
    share = sum(c for _, c in top3) / grand
    if share > th:
        alerts.append({
            "severity": "medium", "rule": "concentration",
            "message": (f"Top-3 proyectos concentran {share * 100:.1f}% del "
                        f"efectivo (umbral {th * 100:.0f}%): "
                        + ", ".join(f"{p} ({fmt_usd(c)})" for p, c in top3)),
            "evidence": {"top3_share": round(share, 4),
                         "threshold_pct": th * 100,
                         "projects": [p for p, _ in top3]},
        })


def _check_staleness(report, cfg, today, alerts):
    """FPA-087: reporte con más de X días de antigüedad → alerta.
    `today` inyectable para tests/golden deterministas (FPA-104)."""
    end = report["metadata"]["date_range"].get("end")
    if not end or not today:
        return
    today_d = today if isinstance(today, date) else date.fromisoformat(today)
    age = (today_d - date.fromisoformat(end)).days
    th = _th(cfg, "staleness_days", 14.0)
    if age > th:
        alerts.append({
            "severity": "low", "rule": "staleness",
            "message": (f"El reporte tiene {age} días de antigüedad "
                        f"(cierre {end}; umbral {th:.0f} días) — regenerar "
                        f"con scripts/usage-tracker.py"),
            "evidence": {"end": end, "age_days": age, "threshold_days": th},
        })


def build_alerts(report, cfg, today=None):
    """FPA-080: motor de alertas — cada alerta con severity, rule name y
    valores de evidencia. Reglas: verify-plan (081), reconciliación (082),
    budget (083), unit-cost (084), mix premium (085), concentración (086),
    staleness (087). Todos los umbrales salen del config (FPA-088).
    Cada alerta lleva exactamente una acción (FPA-167): target del config
    (ctas.alert_actions), con fallback a los defaults del código.
    `today` inyectable para tests/golden; default fecha de hoy."""
    if today is None:
        today = date.today()
    alerts = []
    months = [m for m in build_months(report)]
    _check_verify_plan(report, cfg, months, alerts)
    _check_reconciliation(report, cfg, alerts)
    _check_budget(report, cfg, alerts)
    _check_unit_cost(months, cfg, alerts)
    _check_premium_mix(report, cfg, months, alerts)
    _check_concentration(report, cfg, alerts)
    _check_staleness(report, cfg, today, alerts)
    actions = dict(DEFAULT_ALERT_ACTIONS)
    actions.update((cfg.get("ctas") or {}).get("alert_actions") or {})
    for a in alerts:
        act = actions.get(a["rule"])
        if act:
            a["action"] = act
    return alerts


# ----------------------------------------------------------------------
# Economía de suscripción (FPA-130…133)
# ----------------------------------------------------------------------

# FPA-167: acción default por regla de alerta (el config puede
# sobrescribirla vía ctas.alert_actions; targets nunca vacíos — FPA-168).
DEFAULT_ALERT_ACTIONS = {
    # coffe-a37: config/fpa.json no se deploya al sitio → link al fuente en
    # el repo (el config embede flags de privacidad que no van a producción).
    "verify-plan": {"label": "Revisar el plan",
                    "target": ("https://github.com/charly-vibes/coffee/"
                               "blob/main/config/fpa.json")},
    "reconciliation": {"label": "Ver el reporte",
                       "target": "data/usage_report_v3.json"},
    "budget": {"label": "Ajustar presupuesto", "target": "#budget"},
    "unit-cost": {"label": "Ver el reporte",
                  "target": "data/usage_report_v3.json"},
    "mix": {"label": "Comparar planes",
            "target": ("https://github.com/charly-vibes/coffee/"
                       "blob/main/config/fpa.json")},
    "concentration": {"label": "Ver proyectos", "target": "#pareto"},
    "staleness": {"label": "Regenerar datos",
                  "target": "https://github.com/charly-vibes/coffee"},
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
        seg_end = min(period_end, next_month)
        seg_start = max(period_start, month_start)
        days = (seg_end - seg_start).days
        total += fee * days / dim
        d = next_month
    return total


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
        try:
            day = date.fromisoformat(key[:10])
        except ValueError:
            continue
        if start <= day < end:
            st = h.get("tools", {}).get(tool) or {}
            if st:
                present = True
            ce = st.get("cost_eff", 0.0) or 0.0
            total += ce
            seen_cost = seen_cost or ce != 0.0
    if seen_cost:
        return total
    return 0.0 if present else None


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
    end_s = report["metadata"]["date_range"].get("end")
    end_d = date.fromisoformat(end_s) if end_s else None
    plans = []
    for tool, entries in cfg.get("subscriptions", {}).items():
        for e in entries:
            start = date.fromisoformat(e["start"])
            e_end = (date.fromisoformat(e["end"]) if e.get("end")
                     else end_d)
            if e_end is None or start >= e_end:
                continue
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
            plans.append({
                "tool": tool, "label": e.get("label", tool),
                "start": e["start"], "end": e.get("end"),
                "monthly_fee": fee, "price": price,
                "eff_cost": round(eff, 2) if eff is not None else None,
                "utilization": util, "utilization_reason": util_reason,
                "break_even": fee, "headroom": headroom,
                "provenance": "assumed",  # FPA-003
            })
    plans.sort(key=lambda p: (p["tool"], p["start"]))

    # FPA-132: 4 casos sobre el cash del periodo. El caso actual usa el
    # cash real (ledger *reported* cuando hay ledger; suma tracker si no) —
    # la misma medida que los KPIs (coffe-a31.3).
    cash_by_ym = {m["ym"]: m["cost_cash"] for m in build_months(report)}
    claude_fees = [e["monthly_fee"] for e in
                   cfg.get("subscriptions", {}).get("claude-cli", [])
                   if e.get("monthly_fee", 0) > 0]
    pro_fee = min(claude_fees) if claude_fees else 0.0
    max_fee = max(claude_fees) if claude_fees else 0.0
    actual = all_p2p = all_pro = all_max = 0.0
    for ym, mo in sorted(report["monthly"].items()):
        if not mo["interactions"]:  # FPA-017
            continue
        actual += cash_by_ym.get(ym, 0.0)
        all_p2p += mo.get("cost_effective", 0.0) or 0.0
        tools = mo.get("tools", {})
        claude_active = bool(tools.get("claude-cli"))
        non_claude_cash = sum(
            (t.get("cost_real", 0.0) or 0.0) for t in tools.values()
            if isinstance(t, dict)) \
            - ((tools.get("claude-cli", {}).get("cost_real", 0.0)
                or 0.0) if isinstance(tools.get("claude-cli"), dict) else 0.0)
        all_pro += (pro_fee if claude_active else 0.0) + non_claude_cash
        all_max += (max_fee if claude_active else 0.0) + non_claude_cash
    note = ("el cash no-Claude de reportes con tools en shape contador "
            "(sin coste por tool) no se incluye en todo-Pro/todo-Max") \
        if any(not isinstance(t, dict)
               for mo in report["monthly"].values()
               for t in mo.get("tools", {}).values()) else None
    return {
        "provenance": "assumed",
        "plans": plans,
        "four_cases": {"actual": round(actual, 2),
                       "todo_pay_per_token": round(all_p2p, 2),
                       "todo_pro": round(all_pro, 2),
                       "todo_max": round(all_max, 2)},
        "four_cases_provenance": "assumed",
        "four_cases_note": note,
        "usage_limits_disclaimer": (
            "La equivalencia por coste efectivo ignora los usage limits del "
            "plan: un plan puede no alcanzar el consumo mostrado (FPA-133)."),
    }


def build_reconciliation(report):
    """coffe-a31.3 (CRG-F2): sección de reconciliación FPA-082.

    - providers: cash real del ledger por proveedor dentro del periodo del
      reporte (*reported*). Proveedor sin facturas en el periodo → n/a con
      razón (FPA-008), nunca $0 inventado; mes sin factura de un proveedor
      con facturas → $0 (la ausencia de factura es un dato, no un hueco).
    - rows: por tool y mes, real pagado (*reported*) vs fee implícito del
      calendario (*assumed*) vs efectivo estimado (*assumed*), tal como lo
      emite el tracker (charges_reconciliation_by_month).

    Sin ledger en el reporte → provenance unavailable + razón (FPA-008).
    """
    state = _charges_state(report)
    meses = sorted(report.get("monthly", {}).keys())
    if state["provenance"] != "reported":
        return {"provenance": "unavailable", "source": state["source"],
                "reason": state["reason"], "providers": [], "rows": [],
                "months": meses}
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
            "provenance": "reported",
        })
    rec = report.get("charges_reconciliation_by_month") or {}
    rows = []
    for ym in sorted(rec):
        for tool in sorted(rec[ym]):
            c = rec[ym][tool]
            rows.append({
                "ym": ym, "tool": tool,
                "charges_real": round(float(c.get("charges_real", 0.0) or 0.0), 2),
                "charges_provenance": "reported",
                "subscription_fee_implicit": round(float(
                    c.get("subscription_fee_implicit", 0.0) or 0.0), 2),
                "cost_effective": round(float(
                    c.get("cost_effective", 0.0) or 0.0), 2),
                "fee_month_total": round(float(
                    c.get("fee_month_total", 0.0) or 0.0), 2),
            })
    return {"provenance": "reported", "source": state["source"],
            "reason": None, "providers": providers, "rows": rows,
            "months": meses}


# ======================================================================
# F5 (coffe-lat.6): patrones de uso, concurrencia y lifecycle
# (FPA-110…123, 028, 036, 098) — maths puras en Python; JS no recalcila
# nada de esta fase (design.md). Doble coste jamás se suma (FPA-002);
# los umbrales derivados del config van con provenance *assumed* (FPA-003)
# y todo faltante del tracker sale como "n/a" con razón (FPA-008).
# ======================================================================

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
        "timezone": tz,
        "verified": tz is not None,
        "unverified_reason": (None if tz else
                              "el tracker no registró timezone; las vistas "
                              "hora/día no están verificadas (FPA-142)"),
        "grid": grid,
        "total": sum(sum(row) for row in grid),
    }


def _hour_in_working_hours(dow, hour, wh):
    """¿La hora está dentro del horario laboral? days en isoweekday
    (1=lun…7=dom), start/end 'HH:MM' con end excluyente."""
    if dow + 1 not in wh.get("days", []):
        return False
    start = int(str(wh.get("start", "09:00"))[:2])
    end = int(str(wh.get("end", "18:00"))[:2])
    return start <= hour < end


def build_rhythm(report, cfg):
    """FPA-111: shares after-hours (fuera del horario laboral del config)
    y weekend, desde hourly. FPA-112: serie semanal ISO con WoW y varianza
    poblacional sobre semanas completas (los bordes de cobertura van
    marcados partial y se excluyen)."""
    wh = cfg.get("working_hours", {})
    total = after = weekend = 0
    for ts, h in report.get("hourly", {}).items():
        dow = date.fromisoformat(ts[:10]).isoweekday() - 1
        n = h.get("interactions", 0)
        total += n
        weekend += n if dow >= 5 else 0
        if not _hour_in_working_hours(dow, int(ts[11:13]), wh):
            after += n
    weeks, _ = _build_weeks(report)
    full = [w for w in weeks if not w["partial"]]
    for i, w in enumerate(weeks):
        if i == 0:
            continue  # primera semana: no hay prior en la serie
        prev = weeks[i - 1]
        if not w["partial"] and not prev["partial"]:
            if prev["interactions"]:
                w["wow_interactions"] = (w["interactions"]
                                         - prev["interactions"]) / \
                    prev["interactions"]
            if prev["cost_effective"]:
                w["wow_cost"] = (w["cost_effective"]
                                 - prev["cost_effective"]) / \
                    prev["cost_effective"]
    var_i = _pop_variance([w["interactions"] for w in full])
    var_c = _pop_variance([w["cost_effective"] for w in full])
    reason = None
    if var_i is None or var_c is None:
        reason = (f"se requieren 3+ semanas completas de datos; hay "
                  f"{len(full)} (FPA-008)")
    return {
        "after_hours_share": after / total if total else None,
        "weekend_share": weekend / total if total else None,
        "working_hours_text": _wh_text(wh),
        "weeks": weeks,
        "variance": {
            "interactions": var_i,
            "cost_effective": var_c,
            "weeks_count": len(full),
            "reason": reason,
        },
    }


def _iso_week_start(d):
    """Lunes (date) de la semana ISO que contiene a d."""
    return date.fromordinal(d.toordinal() - (d.isoweekday() - 1))


def _build_weeks(report):
    """Serie semanal ISO desde daily: solo semanas con datos (convención
    FPA-017). partial = la semana queda truncada por el borde de cobertura
    de datos (primer/último día con datos)."""
    daily = report.get("daily", {})
    if not daily:
        return [], None
    days = sorted(daily)
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    acc = {}
    for d, dv in daily.items():
        dd = date.fromisoformat(d)
        wk = acc.setdefault(_iso_week_start(dd),
                            {"interactions": 0, "cost_effective": 0.0})
        wk["interactions"] += dv.get("interactions", 0)
        wk["cost_effective"] += dv.get("cost_effective", 0.0)
    weeks = []
    for start in sorted(acc):
        end = date.fromordinal(start.toordinal() + 6)
        weeks.append({
            "start": start.isoformat(),
            "interactions": acc[start]["interactions"],
            "cost_effective": acc[start]["cost_effective"],
            "partial": start < first or end > last,
            "wow_interactions": None,
            "wow_cost": None,
        })
    return weeks, (first, last)


def _pop_variance(values):
    """Varianza poblacional; None si hay <3 valores (FPA-008)."""
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def build_usage_skills(report):
    """FPA-113/114: top skills por usos, usadas exactamente una vez y
    zero-uso (solo si el tracker emite la lista de instaladas)."""
    skills = report.get("skills", {}) or {}
    top = [{"name": k, "uses": v}
           for k, v in sorted(skills.items(), key=lambda x: (-x[1], x[0]))]
    once = sorted(k for k, v in skills.items() if v == 1)
    installed = report.get("skills_installed")
    if installed is not None:
        zero = sorted(set(installed) - set(skills))
        zero_reason = None
    else:
        zero, zero_reason = _na("el tracker no emite la lista de skills "
                                "instaladas (FPA-114)")
    trend, trend_reason = _na("el tracker no emite uso de skills por mes "
                              "(FPA-114)")
    return {"top": top, "once": once, "zero": zero,
            "zero_reason": zero_reason, "trend": trend,
            "trend_reason": trend_reason}


def build_usage_commands(report):
    """FPA-115: slash commands más ejecutados; trend mensual n/a con
    razón si el tracker no lo emite."""
    commands = report.get("commands", {}) or {}
    top = [{"name": k, "uses": v}
           for k, v in sorted(commands.items(), key=lambda x: (-x[1], x[0]))]
    trend, trend_reason = _na("el tracker no emite uso de comandos por mes "
                              "(FPA-115)")
    return {"top": top, "trend": trend, "trend_reason": trend_reason}


_SESSION_BUCKET_LABELS = (("1-10", "1–10"), ("11-50", "11–50"),
                          ("51-100", "51–100"))


def build_usage_sessions(report, cfg):
    """FPA-116…118: buckets de longitud de sesión (1–10, 11–50, 51–100,
    100+), sesiones más largas, coste/mediana/p90 (n/a sin coste por
    sesión) y resets por 100 sesiones con provenance por tool (coffe-i31:
    /clear es una métrica claude-only; en pi el reset es /new = archivo
    nuevo, el denominador del ratio es claude-only y se declara)."""
    sessions = report.get("sessions", {}) or {}
    raw = sessions.get("length_distribution", {}) or {}
    counts = {label: raw.get(key, 0) for key, label in _SESSION_BUCKET_LABELS}
    counts["100+"] = sum(v for k, v in raw.items()
                         if k not in {key for key, _ in _SESSION_BUCKET_LABELS})
    buckets = [{"label": label, "count": counts[label]}
               for label in ("1–10", "11–50", "51–100", "100+")]
    cost_reason = "el tracker no emite coste por sesión (FPA-116/117)"
    longest = [{"turns": s.get("turns"), "date": s.get("date"),
                "project": s.get("project"), "cost": None,
                "cost_reason": cost_reason}
               for s in sessions.get("top_longest_by_turns", [])]
    total_sessions = sessions.get("total_sessions") or 0
    # coffe-i31: clear_per_100 con denominador claude-only (el /clear solo
    # existe en Claude; dividir por el total global sesga el ratio)
    by_tool = sessions.get("by_tool", {}) or {}
    claude = by_tool.get("claude-cli", {}) or {}
    claude_total = claude.get("total") or 0
    clears = ((claude.get("resets") or {}).get("count"))
    if claude_total and clears is not None:
        clear_per_100 = 100.0 * clears / claude_total
        clear_scope_reason = None
    else:
        clear_per_100 = None
        clear_scope_reason = ("sin sesiones claude en la ventana o sin /clear "
                              "emitido (FPA-118); el ratio es claude-only")
    resets_by_tool = {t: (v.get("resets") or {})
                      for t, v in by_tool.items()}
    monthly_trend, monthly_reason = _na(
        "el tracker no emite /clear ni sesiones por mes (FPA-118)")
    long_turns = _cfg_int(cfg, "sessions", "long_turns", 100)
    no_clear, no_clear_reason = _na(
        "el tracker no marca /clear por sesión (FPA-118)")
    return {
        "buckets": buckets,
        "longest": longest,
        "cost_by_bucket": None, "cost_by_bucket_reason": cost_reason,
        "median_p90": None, "median_p90_reason": cost_reason,
        "clear_per_100": clear_per_100,
        "clear_scope_reason": clear_scope_reason,
        "resets_by_tool": resets_by_tool,
        "total_sessions": total_sessions,
        "clear_monthly": monthly_trend,
        "clear_monthly_reason": monthly_reason,
        "long_no_clear": no_clear,
        "long_no_clear_reason": no_clear_reason,
        "long_turns": long_turns,
    }


def build_timeline(report, cfg):
    """FPA-119: primera/última actividad por tool y por model, con gaps
    > gap_days (config, default 7) marcados. Vista de día → hereda el
    flag unverified de timezone (FPA-142)."""
    gap_days = _cfg_int(cfg, "timeline", "gap_days", 7)
    tools, models = {}, {}
    for ts in sorted(report.get("hourly", {})):
        d = ts[:10]
        entry = report["hourly"][ts]
        for tool in entry.get("tools", {}):
            tools.setdefault(tool, set()).add(d)
        for model in entry.get("models", {}):
            models.setdefault(model, set()).add(d)

    def _series(days):
        days = sorted(days)
        gaps = []
        for prev, cur in zip(days, days[1:]):
            gap = (date.fromisoformat(cur) - date.fromisoformat(prev)).days
            if gap > gap_days:
                gaps.append({"from": prev, "to": cur, "days": gap})
        return {
            "first": days[0], "last": days[-1],
            "gaps": gaps,
            "max_gap_days": max((g["days"] for g in gaps), default=0),
            "flagged": bool(gaps),
        }

    tz = report["metadata"].get("timezone")
    return {
        "verified": tz is not None,
        "unverified_reason": (None if tz else
                              "sin timezone del tracker, fechas por día no "
                              "verificadas (FPA-142)"),
        "gap_days": gap_days,
        "tools": [_series(v) | {"name": k}
                  for k, v in sorted(tools.items())],
        "models": [_series(v) | {"name": k}
                   for k, v in sorted(models.items())],
    }


def build_usage_concurrency(report):
    """FPA-120: (a) proyectos distintos/hora y (b) pico de sesiones o
    agentes simultáneos etiquetados parallel-agent; (c) switches de
    proyecto/hora activa etiquetado human-context-switching."""
    emitted = report.get("concurrency")
    if emitted:  # el tracker emite las medidas etiquetadas
        return {
            "projects_per_hour": emitted.get("distinct_projects_per_hour",
                                             {"measure": "parallel-agent"}),
            "peak_simultaneous_sessions": emitted.get(
                "peak_simultaneous_sessions",
                {"measure": "parallel-agent"}),
            "switches_per_hour": emitted.get(
                "project_switches_per_active_hour",
                {"measure": "human-context-switching"}),
        }
    active = [h.get("projects_active", 0)
              for h in report.get("hourly", {}).values()
              if h.get("interactions", 0)]
    if active:
        pph = {"measure": "parallel-agent",
               "peak": max(active),
               "avg": sum(active) / len(active)}
    else:
        pph = {"measure": "parallel-agent", "peak": None,
               "avg": None, "reason": "sin horas activas en hourly"}
    peak, peak_reason = _na("el tracker no emite sesiones/agentes "
                            "simultáneos (FPA-120b)")
    mt = report.get("multitasking", {}) or {}
    switches_total = (mt.get("context_switches", {}) or {}).get("total")
    active_hours = (mt.get("hourly", {}) or {}).get("total_active_hours")
    if switches_total is not None and active_hours:
        switches = {"measure": "human-context-switching",
                    "value": switches_total / active_hours}
    else:
        switches = {"measure": "human-context-switching", "value": None,
                    "reason": "el tracker no emite switches de proyecto "
                              "(FPA-120c)"}
    return {
        "projects_per_hour": pph,
        "peak_simultaneous_sessions": {
            "measure": "parallel-agent", "peak": peak,
            "reason": peak_reason},
        "switches_per_hour": switches,
    }


def build_agent_share(report):
    """FPA-121: share de sesiones con Agent, total y trend mensual si el
    tracker emite sesiones por mes. coffe-i31: share por tool — la
    semántica de Agent difiere por harness (agent_semantics)."""
    sessions = report.get("sessions", {}) or {}
    total = sessions.get("total_sessions") or 0
    with_agent = sessions.get("with_agent") or 0
    share = with_agent / total if total else None
    by_tool_shares = {}
    for t, v in (sessions.get("by_tool", {}) or {}).items():
        t_total = v.get("total") or 0
        by_tool_shares[t] = {
            "share": (v.get("with_agent", 0) / t_total) if t_total else None,
            "reason": None if t_total else "sin sesiones de esta tool",
        }
    monthly_raw = report.get("sessions_monthly")
    if monthly_raw:
        monthly = [{"ym": ym,
                    "share": (v.get("with_agent", 0) / v["total"]
                              if v.get("total") else None)}
                   for ym, v in sorted(monthly_raw.items())]
        monthly_reason = None
    else:
        monthly, monthly_reason = _na(
            "el tracker no emite sesiones por mes (FPA-121)")
    return {"share": share, "monthly": monthly,
            "monthly_reason": monthly_reason,
            "by_tool_shares": by_tool_shares,
            "agent_semantics": sessions.get("agent_semantics")}


def build_lifecycle(report, cfg):
    """FPA-122/123: clasificación de proyectos new/active/dormant contra
    la fecha fin del periodo (determinista), coste efectivo de dormantes
    (total y por proyecto) y activos por mes (si el tracker emite
    project_monthly)."""
    lifecycle = cfg.get("lifecycle", {})
    new_days = int(lifecycle.get("new_days", 30))
    dormant_days = int(lifecycle.get("dormant_days", 30))
    end = date.fromisoformat(report["metadata"]["date_range"]["end"])
    projects = []
    dormant_cost = 0.0
    for name, p in sorted((report.get("projects", {}) or {}).items()):
        first = p.get("first_seen")
        last = p.get("last_seen")
        if not first or not last:
            continue
        d_first = (end - date.fromisoformat(first)).days
        d_last = (end - date.fromisoformat(last)).days
        if d_first <= new_days:
            status = "new"
        elif d_last > dormant_days:
            status = "dormant"
        else:
            status = "active"
        if status == "dormant":
            dormant_cost += p.get("cost_effective", 0.0)
        projects.append({"name": name, "status": status,
                         "first_seen": first, "last_seen": last})
    n_dormant = sum(1 for p in projects if p["status"] == "dormant")
    project_monthly = report.get("project_monthly")
    if project_monthly:
        months_acc = {}
        for pms in project_monthly.values():  # {proyecto: {ym: metrics}}
            for ym, v in pms.items():
                if v.get("interactions", 0) > 0:
                    months_acc[ym] = months_acc.get(ym, 0) + 1
        by_month = [{"ym": ym, "count": months_acc[ym]}
                    for ym in sorted(months_acc)]
        month_reason = None
    else:
        by_month, month_reason = _na(
            "el tracker no emite actividad de proyectos por mes "
            "(FPA-123)")
    return {
        "reference": end.isoformat(), "new_days": new_days,
        "dormant_days": dormant_days,
        "projects": projects,
        "dormant": {"count": n_dormant, "cost_total": dormant_cost,
                    "cost_per_project": (dormant_cost / n_dormant
                                         if n_dormant else None)},
        "active_by_month": by_month,
        "active_by_month_reason": month_reason,
    }


_PARETO_TOP = 10


def build_pareto(report):
    """FPA-028/036: proyectos por coste efectivo con share y share
    acumulado; la cola (fuera del top N) se agrupa en una fila. Top-3
    como concentración del coste efectivo (FPA-036)."""
    costs = {name: p.get("cost_effective", 0.0)
             for name, p in (report.get("projects", {}) or {}).items()
             if p.get("cost_effective", 0.0) > 0}
    total = sum(costs.values())
    ordered = sorted(costs.items(), key=lambda x: (-x[1], x[0]))
    rows, cum = [], 0.0
    for name, cost in ordered[:_PARETO_TOP]:
        cum += cost / total if total else 0.0
        rows.append({"name": name, "cost": cost,
                     "share": cost / total if total else None,
                     "cumulative_share": cum})
    tail_items = ordered[_PARETO_TOP:]
    if tail_items:
        tail_cost = sum(c for _, c in tail_items)
        tail = {"count": len(tail_items), "cost": tail_cost,
                "share": tail_cost / total if total else None,
                "cumulative_share": 1.0}
    else:
        tail = None
    top3 = sum(c for _, c in ordered[:3])
    return {"rows": rows, "tail": tail, "total": total,
            "top3_share": top3 / total if total else None}


def build_usage_patterns(report, cfg):
    """Modelo F5 completo (va embebido en el JSON del dashboard)."""
    return {
        "heatmap": build_heatmap(report),
        "rhythm": build_rhythm(report, cfg),
        "skills": build_usage_skills(report),
        "commands": build_usage_commands(report),
        "sessions": build_usage_sessions(report, cfg),
        "timeline": build_timeline(report, cfg),
        "concurrency": build_usage_concurrency(report),
        "agent_share": build_agent_share(report),
        "lifecycle": build_lifecycle(report, cfg),
        "pareto": build_pareto(report),
        "repo_overlay": {"available": False,
                         "reason": "el tracker no emite fechas de creación "
                                   "de repos (FPA-098, condicional)"},
    }


# ======================================================================
# Validación de schema stdlib-only (FPA-006) — única fuente compartida
# en scripts/schema_validate.py (extraído en coffe-nea para ddl-effect)
# ======================================================================

validate_against_schema = schema_validate.validate_against_schema


def validate_report(report):
    """FPA-006: valida el reporte contra el schema; lista de campos fallidos."""
    schema = json.loads(SCHEMA.read_text())
    return validate_against_schema(report, schema)


# ======================================================================
# F2 (coffe-lat.3): árboles expandibles + KPI strip
# (FPA-020…045, 140–142) — maths puras; el HTML lleva los valores ya
# pre-calculados por periodo y JS solo re-escala (design.md)
# ======================================================================

def _node(key, label):
    """Nodo de árbol: valores por mes (para recompute de periodo) + hijos."""
    return {"key": key, "label": label, "by_month": {}, "children": []}


def _node_add(node, ym, cost=0.0, interactions=0, budget=None):
    b = node["by_month"].setdefault(ym, {"cost": 0.0, "interactions": 0})
    b["cost"] += cost
    b["interactions"] += interactions
    if budget is not None:
        b["budget"] = b.get("budget", 0.0) + budget


def _cost_of(node, months=None):
    if node.get("totals_only"):  # coffe-0d5: el drill solo tiene el total
        return node["by_month"].get("__total__", {}).get("cost", 0.0)
    if months is None:
        months = node["by_month"].keys()
    return sum(node["by_month"][m]["cost"] for m in months if m in node["by_month"])


def _inter_of(node, months=None):
    if node.get("totals_only"):  # coffe-0d5: el drill solo tiene el total
        return node["by_month"].get("__total__", {}).get("interactions", 0)
    if months is None:
        months = node["by_month"].keys()
    return sum(node["by_month"][m]["interactions"]
               for m in months if m in node["by_month"])


def _bucket(v):
    """Compat: modelos/tools del reporte viejo son Counters (int); los nuevos
    llevan dicts. Devuelve (interactions, cost) según el shape."""
    if isinstance(v, dict):
        return v.get("interactions", 0), v.get("cost_effective", 0.0) or 0.0
    return v, 0.0


def build_tool_tree(report):
    """FPA-020: árbol Tool → Model con valores por mes; el selector de periodo
    (FPA-026) recompute sumando meses pre-calculados, sin lógica de negocio."""
    root = _node("tool:__all__", "Todas las tools")
    tools = {}
    for ym, mo in sorted(report["monthly"].items()):
        if mo["interactions"] == 0:  # FPA-017: mes sin datos no aporta
            continue
        for tool, st in mo["tools"].items():
            n, cost = _bucket(st)
            tn = tools.setdefault(tool, _node(f"tool:{tool}", tool))
            _node_add(tn, ym, cost, n)
            _node_add(root, ym, cost, n)
            for model, ms in (st.get("models", {}) if isinstance(st, dict) else {}).items():
                model_node = next((c for c in tn["children"]
                                   if c["key"] == f"model:{tool}:{model}"), None)
                if model_node is None:
                    model_node = _node(f"model:{tool}:{model}", model)
                    tn["children"].append(model_node)
                n_model, c_model = _bucket(ms)
                _node_add(model_node, ym, c_model, n_model)
    for tn in tools.values():
        tn["children"].sort(key=lambda n: -_cost_of(n))
    root["children"] = sorted(tools.values(), key=lambda n: -_cost_of(n))
    return root


def build_time_tree(report, cfg):
    """FPA-020/024: Year → Quarter → Month con coste CASH (el comparable con
    el presupuesto cash; FPA-002: jamás se suma con el efectivo) y
    presupuesto mensual del config (assumed) en cada mes."""
    root = _node("time:__all__", "Todo el periodo")
    start = cfg.get("budgets", {}).get("start_month")
    cash_budget = cfg.get("budgets", {}).get("cash_monthly")
    years = {}
    for meta in build_months(report):
        ym = meta["ym"]
        y, q = ym[:4], f"Q{(int(ym[5:7]) - 1) // 3 + 1}"
        budget = None
        if start and ym >= start and cash_budget:
            # FPA-052 (coffe-9p4): pro-rata de mes parcial, misma fuente que
            # build_budget — el árbol no contradice la tabla de varianza.
            budget = round(cash_budget * meta["elapsed"] / meta["total_days"], 2)
        month_node = _node(f"time:{ym}", month_label(ym))
        _node_add(month_node, ym, meta["cost_cash"], meta["interactions"], budget)
        ynode = years.setdefault(y, _node(f"time:{y}", y))
        qnode = next((c for c in ynode["children"]
                      if c["key"] == f"time:{y}-{q}"), None)
        if qnode is None:
            qnode = _node(f"time:{y}-{q}", f"{q} {y[2:]}")
            ynode["children"].append(qnode)
        qnode["children"].append(month_node)
        _node_add(qnode, ym, meta["cost_cash"], meta["interactions"], budget)
        _node_add(ynode, ym, meta["cost_cash"], meta["interactions"], budget)
    root["children"] = [years[y] for y in sorted(years)]
    # roll-up por construcción: el root es la suma de sus años
    for ynode in root["children"]:
        for ym, b in ynode["by_month"].items():
            _node_add(root, ym, b["cost"], b["interactions"], b.get("budget"))
    return root


def repo_link(proj, cfg):
    """coffe-vp8: link al repo GitHub del proyecto, sin red.

    Los flags de visibilidad viven en config/fpa.json (repos): owners mapea
    prefijo de label → org GH; private/no_remote se auditaron con gh api
    (404 = sin remoto público → no se linkea; private=true → 🔒 en el UI).
    Devuelve {'url', 'private'} o None si el proyecto no es linkeable.
    """
    repos = cfg.get("repos") or {}
    owners = repos.get("owners") or {}
    p = proj or ""
    for org in sorted(owners, key=len, reverse=True):
        if p == org:  # label bare → página de la org
            return {"url": f"https://github.com/{owners[org]}",
                    "private": False}
        if p.startswith(org + "-") or p.startswith(org + "/"):
            repo = p[len(org) + 1:]
            if repo.endswith("-jl"):  # clean_proj_name convierte .jl → -jl
                repo = repo[:-3] + ".jl"
            if f"{org}/{repo}" in (repos.get("no_remote") or []):
                return None
            url = ((repos.get("url_overrides") or {}).get(p)
                   or f"https://github.com/{owners[org]}/{repo}")
            return {"url": url,
                    "private": f"{org}/{repo}" in (repos.get("private") or [])}
    return None


def build_portfolio_tree(report, cfg):
    """FPA-018/020/021: Category → Project con drill a Model (totales).
    Con project_monthly (FPA-012) el árbol tiene valores por mes; el drill
    Project→Model solo tiene datos del periodo completo — limitación visible."""
    root = _node("pf:__all__", "Portfolio")
    cats = {}
    for proj, months in report.get("project_monthly", {}).items():
        cat = fpa_config.classify_project(cfg, proj)
        cn = cats.setdefault(cat, _node(f"pf:{cat}", cat))
        proj_node = next((c for c in cn["children"]
                          if c["key"] == f"pf:{proj}"), None)
        if proj_node is None:
            proj_node = _node(f"pf:{proj}", proj)
            link = repo_link(proj, cfg)  # coffe-vp8: link al repo GH
            if link:
                proj_node["url"] = link["url"]
                proj_node["private"] = link["private"]
            cn["children"].append(proj_node)
        for ym, v in months.items():
            _node_add(proj_node, ym, v["cost_effective"], v["interactions"])
            _node_add(cn, ym, v["cost_effective"], v["interactions"])
    for cat in cats.values():
        for p_node in cat["children"]:
            for model, ms in report.get("project_models", {}).get(p_node["label"], {}).items():
                m_node = _node(f"pfm:{p_node['label']}:{model}", model)
                m_node["totals_only"] = True
                m_node["by_month"]["__total__"] = {
                    "cost": ms["cost_effective"], "interactions": ms["interactions"]}
                p_node["children"].append(m_node)
        cat["children"].sort(key=lambda n: -_cost_of(n))
    root["children"] = sorted(cats.values(), key=lambda n: -_cost_of(n))
    for cn in root["children"]:
        for ym, b in cn["by_month"].items():
            _node_add(root, ym, b["cost"], b["interactions"])
    return root


def check_rollup(node):
    """FPA-025: todo padre = suma de sus hijos dentro de $0.01 y 1 interacción.
    Devuelve violaciones (lista vacía = OK). Verificación recursiva."""
    bad = []
    if node.get("children"):
        c_cost = sum(_cost_of(c) for c in node["children"])
        c_inter = sum(_inter_of(c) for c in node["children"])
        p_cost, p_inter = _cost_of(node), _inter_of(node)
        if abs(p_cost - c_cost) > 0.01:
            bad.append(f"{node['key']}: coste padre {p_cost:.4f} ≠ hijos {c_cost:.4f}")
        if abs(p_inter - c_inter) > 1:
            bad.append(f"{node['key']}: interacciones padre {p_inter} ≠ hijos {c_inter}")
        for c in node["children"]:
            bad.extend(check_rollup(c))
    return bad


def kpi_ingredients(report, cfg):
    """Ingredientes crudos por mes (solo meses con datos, FPA-017).

    Todo lo que un KPI necesita por mes: costes, días para daily rate,
    tokens, sesiones, concentración top-3, premium, multitasking, outcomes
    y presencia de tokens por tool (FPA-045). Las funciones de KPI solo
    combinan estos números."""
    sessions_m = report.get("sessions_monthly", {}) or {}
    premium = [p.lower() for p in cfg.get("premium_models", [])]
    ing = {}
    for meta in build_months(report):
        ym = meta["ym"]
        if not meta["has_data"]:
            continue
        mo = report["monthly"][ym]
        inp = out_t = cr = cw = 0
        for t in mo.get("tokens_by_model", {}).values():
            inp += t.get("in", 0)
            out_t += t.get("out", 0)
            cr += t.get("cache_read", 0)
            cw += t.get("cache_write", 0)
        else_cost = 0.0  # coste de modelos sin datos nuevos (reporte viejo)
        # FPA-045: tools sin datos de tokens ese mes → exclusión con share
        inter_by_tool, notokens_inter = {}, 0
        for h, hb in report.get("hourly", {}).items():
            if h[:7] != ym:
                continue
            for tool, ts_ in hb.get("tools", {}).items():
                inter_by_tool[tool] = inter_by_tool.get(tool, 0) + ts_.get("req", 0)
                if (ts_.get("in", 0) + ts_.get("out", 0)
                        + ts_.get("cache_read", 0) + ts_.get("cache_write", 0)) == 0:
                    notokens_inter += ts_.get("req", 0)
        mt_inter = sum(hb["interactions"]
                       for h, hb in report.get("hourly", {}).items()
                       if h[:7] == ym and hb.get("projects_active", 0) >= 2)
        proj_costs_map = {p: v[ym]["cost_effective"]
                          for p, v in report.get("project_monthly", {}).items()
                          if ym in v}
        proj_costs = sorted(proj_costs_map.values(), reverse=True)
        outc = mo.get("outcomes_by_project") or {}
        premium_cost = sum(
            _bucket(st)[1] for model, st in mo.get("models", {}).items()
            if any(p in model.lower() for p in premium))
        sm = sessions_m.get(ym, {})
        ing[ym] = {
            "cost_effective": mo["cost_effective"],
            # coffe-a31.3: cash = ledger (*reported*) o fallback tracker (assumed)
            "cost_cash": meta["cost_cash"],
            "cash_provenance": meta["cash_provenance"],
            "interactions": mo["interactions"],
            "days": meta["elapsed"],  # FPA-041: días efectivos (parciales: elapsed)
            "partial": meta["partial"],
            "sessions": sm.get("total", 0),
            "sessions_agent": sm.get("with_agent", 0),
            "tokens": {"in": inp, "out": out_t, "cache_read": cr, "cache_write": cw},
            "excluded_interactions": notokens_inter,
            "interactions_by_tool": inter_by_tool,
            "mt_interactions": mt_inter,
            "top3_cost": sum(proj_costs[:3]),
            "proj_cost": sum(proj_costs),
            # coffe-hn7: el top-3 de una ventana se computa sobre el agregado
            # del periodo (matchea Pareto/alerta), no sumando top-3 mensuales
            "proj_costs": proj_costs_map,
            "premium_cost": premium_cost,
            "commits": sum(o.get("commits", 0) or 0 for o in outc.values()),
            "releases": sum(o.get("releases", 0) or 0 for o in outc.values()),
        }
    return ing


def _pct_change(cur, prev):
    """Delta porcentual; None si no hay base o valor (FPA-042: nunca infinito)."""
    if prev is None or prev == 0 or cur is None:
        return None
    return (cur - prev) / prev


def _sum_ing(ing, window, key):
    return sum(ing[ym].get(key, 0) for ym in window if ym in ing)


def _sum_tokens(ing, window, key):
    return sum(ing[ym]["tokens"].get(key, 0) for ym in window if ym in ing)


def _spark(ing, compute):
    """Serie mensual de un KPI (solo meses con datos) para el sparkline."""
    out = []
    for ym in sorted(ing):
        v = compute(ing[ym])
        if v is not None:
            out.append({"ym": ym, "value": round(v, 6)})
    return out


def _kpi(key, label, value, fmt, reason=None, provenance="reported",
         prev=None, cur_rate=None, prev_rate=None, spark=None,
         excluded_share=None):
    """Card de KPI normalizada. value None → n/a con razón (FPA-008/042).

    Delta: si viene cur_rate/prev_rate se compara la tasa diaria (FPA-041);
    si no, Δ% del valor del periodo vs el previo. Sin periodo previo en el
    reporte → delta n/a."""
    if cur_rate is not None:
        delta = _pct_change(cur_rate, prev_rate)
        kind = "daily-rate"
    else:
        delta = _pct_change(value, prev)
        kind = "periodo"
    k = {
        "key": key, "label": label, "value": value,
        "display": fmt(value) if value is not None else "n/a",
        "provenance": provenance, "reason": reason,
        "delta": None if delta is None else round(delta, 4),
        "delta_display": (f"{'+' if delta >= 0 else ''}{delta * 100:.1f}%"
                          if delta is not None else "n/a"),
        "delta_kind": kind,
        "spark": spark or [],
    }
    if excluded_share:
        k["excluded_share"] = round(excluded_share, 4)
        k["excluded_display"] = f"{excluded_share * 100:.1f}%"
    return k


def kpis_for_window(ing, window, prior):
    """KPIs FPA-030…045 para una ventana de meses, con delta vs la ventana
    previa (de igual longitud) y sparkline mensual. Denominador 0 → n/a con
    razón (FPA-042). FPA-041: con mes parcial las comparaciones de coste son
    tasas diarias."""
    if not window:
        return []
    eff = _sum_ing(ing, window, "cost_effective")
    cash = _sum_ing(ing, window, "cost_cash")
    inter = _sum_ing(ing, window, "interactions")
    days = _sum_ing(ing, window, "days")
    sessions = _sum_ing(ing, window, "sessions")
    sessions_agent = _sum_ing(ing, window, "sessions_agent")
    mt_inter = _sum_ing(ing, window, "mt_interactions")
    # coffe-hn7: concentración top-3 del agregado de la ventana — el mismo
    # número que el Pareto y la alerta de concentración muestran.
    def _top3_of(ths):
        acc = {}
        for ym in ths:
            for p, c in (ing.get(ym, {}).get("proj_costs") or {}).items():
                acc[p] = acc.get(p, 0.0) + c
        return sum(sorted(acc.values(), reverse=True)[:3])

    top3 = _top3_of(window)
    proj_cost = _sum_ing(ing, window, "proj_cost")
    premium_cost = _sum_ing(ing, window, "premium_cost")
    commits = _sum_ing(ing, window, "commits")
    releases = _sum_ing(ing, window, "releases")
    tok = {k: _sum_tokens(ing, window, k)
           for k in ("in", "out", "cache_read", "cache_write")}
    excluded_inter = _sum_ing(ing, window, "excluded_interactions")

    partial = any(ym in ing and ing[ym]["partial"] for ym in window)
    if prior:
        partial = partial or any(ym in ing and ing[ym]["partial"] for ym in prior)

    # tasas diarias (FPA-041) — siempre calculadas; se usan para costes
    rate_eff = eff / days if days else None
    rate_cash = cash / days if days else None
    p_eff = _sum_ing(ing, prior, "cost_effective") if prior else 0
    p_cash = _sum_ing(ing, prior, "cost_cash") if prior else 0
    p_days = _sum_ing(ing, prior, "days") if prior else 0
    p_inter = _sum_ing(ing, prior, "interactions") if prior else 0
    p_sessions = _sum_ing(ing, prior, "sessions") if prior else 0
    p_tok = {k: _sum_tokens(ing, prior, k) if prior else 0
             for k in ("in", "out", "cache_read", "cache_write")}
    p_denom = p_tok["in"] + p_tok["cache_read"] + p_tok["cache_write"] if prior else 0

    excluded_share = (excluded_inter / inter) if inter else None

    kpis = []
    # coffe-a31.3: la provenance del cash KPI sigue al ledger (reported si
    # todo el window viene del ledger; assumed con fallback)
    en_ventana = [ing[ym] for ym in window if ym in ing]
    prov_cash = ("reported" if en_ventana and all(
        i.get("cash_provenance") == "reported" for i in en_ventana)
        else "assumed")

    # FPA-030/031: costes con delta de tasa diaria (siempre tasa diaria: es
    # la comparación justa incluso entre meses completos de distinta longitud)
    kpis.append(_kpi(
        "cost_effective", "Coste efectivo", eff if eff else None, fmt_usd,
        reason="sin coste efectivo en el periodo" if not eff else None,
        cur_rate=eff / days if days else None,
        prev_rate=(p_eff / p_days) if p_days else None,
        spark=_spark(ing, lambda i: i["cost_effective"] / i["days"]
                     if i["days"] else None)))
    kpis.append(_kpi(
        "cost_cash", "Coste cash", cash if cash else None, fmt_usd,
        reason="sin coste cash en el periodo" if not cash else None,
        provenance=prov_cash,
        cur_rate=cash / days if days else None,
        prev_rate=(p_cash / p_days) if p_days else None,
        spark=_spark(ing, lambda i: i["cost_cash"] / i["days"]
                     if i["days"] else None)))
    p_cash_ = p_cash if prior else 0
    kpis.append(_kpi(
        "leverage", "Leverage", eff / cash if cash else None,
        lambda v: f"{v:.1f}×",
        reason="requiere coste cash > 0" if not cash else None,
        provenance=prov_cash,
        prev=(p_eff / p_cash) if p_cash else None,
        spark=_spark(ing, lambda i: i["cost_effective"] / i["cost_cash"]
                     if i["cost_cash"] else None)))
    kpis.append(_kpi(
        "cost_per_1k_eff", "Coste efectivo por 1k",
        eff / (inter / 1000) if inter else None, fmt_usd,
        reason="sin interacciones en el periodo" if not inter else None,
        prev=(p_eff / (p_inter / 1000)) if p_inter else None,
        spark=_spark(ing, lambda i: i["cost_effective"] / (i["interactions"] / 1000)
                     if i["interactions"] else None)))
    kpis.append(_kpi(
        "cost_per_1k_cash", "Coste cash por 1k",
        cash / (inter / 1000) if inter else None, fmt_usd,
        reason="sin interacciones en el periodo" if not inter else None,
        provenance=prov_cash,
        prev=(p_cash / (p_inter / 1000)) if p_inter else None,
        spark=_spark(ing, lambda i: i["cost_cash"] / (i["interactions"] / 1000)
                     if i["interactions"] else None)))
    kpis.append(_kpi(
        "cost_per_session", "Coste por sesión",
        eff / sessions if sessions else None, fmt_usd,
        reason="sin sesiones en el periodo" if not sessions else None,
        prev=(p_eff / _sum_ing(ing, prior, "sessions")) if p_sessions else None,
        spark=_spark(ing, lambda i: i["cost_effective"] / i["sessions"]
                     if i["sessions"] else None)))
    kpis.append(_kpi(
        "top3_concentration", "Concentración top-3",
        top3 / proj_cost if proj_cost else None, lambda v: f"{v * 100:.1f}%",
        reason="sin coste por proyecto en el periodo" if not proj_cost else None,
        prev=(_top3_of(prior) / _sum_ing(ing, prior, "proj_cost"))
        if prior and _sum_ing(ing, prior, "proj_cost") else None,
        spark=_spark(ing, lambda i: i["top3_cost"] / i["proj_cost"]
                     if i["proj_cost"] else None)))
    # share premium: la lista de modelos premium viene del config → assumed
    kpis.append(_kpi(
        "premium_share", "Share premium",
        premium_cost / eff if eff else None, lambda v: f"{v * 100:.1f}%",
        provenance="assumed",
        reason="sin coste efectivo en el periodo" if not eff else None,
        prev=(_sum_ing(ing, prior, "premium_cost") / p_eff) if p_eff else None,
        spark=_spark(ing, lambda i: i["premium_cost"] / i["cost_effective"]
                     if i["cost_effective"] else None)))
    kpis.append(_kpi(
        "autonomous_share", "Autonomous share",
        sessions_agent / sessions if sessions else None, lambda v: f"{v * 100:.1f}%",
        reason="sin sesiones en el periodo" if not sessions else None,
        prev=(_sum_ing(ing, prior, "sessions_agent") / p_sessions) if p_sessions else None,
        spark=_spark(ing, lambda i: i["sessions_agent"] / i["sessions"]
                     if i["sessions"] else None)))
    kpis.append(_kpi(
        "multitasking_share", "Multitasking",
        mt_inter / inter if inter else None, lambda v: f"{v * 100:.1f}%",
        reason="sin interacciones en el periodo" if not inter else None,
        prev=(_sum_ing(ing, prior, "mt_interactions") / p_inter) if p_inter else None,
        spark=_spark(ing, lambda i: i["mt_interactions"] / i["interactions"]
                     if i["interactions"] else None)))
    # FPA-043/045: token KPIs con exclusión de tools sin tokens
    denom = tok["in"] + tok["cache_read"] + tok["cache_write"]
    kpis.append(_kpi(
        "cache_hit_rate", "Cache-hit rate",
        tok["cache_read"] / denom if denom else None, lambda v: f"{v * 100:.1f}%",
        reason="sin tokens registrados en el periodo" if not denom else None,
        prev=(p_tok["cache_read"] / p_denom) if p_denom else None,
        excluded_share=excluded_share,
        spark=_spark(ing, lambda i: (lambda d: i["tokens"]["cache_read"] / d
                                     if d else None)(
            i["tokens"]["in"] + i["tokens"]["cache_read"] + i["tokens"]["cache_write"]))))
    kpis.append(_kpi(
        "out_in_ratio", "Ratio out/in",
        tok["out"] / tok["in"] if tok["in"] else None, lambda v: f"{v:.2f}",
        reason="sin tokens de input en el periodo" if not tok["in"] else None,
        prev=(p_tok["out"] / p_tok["in"]) if p_tok["in"] else None,
        excluded_share=excluded_share,
        spark=_spark(ing, lambda i: i["tokens"]["out"] / i["tokens"]["in"]
                     if i["tokens"]["in"] else None)))
    # FPA-040: outcome KPIs solo donde haya datos
    if commits:
        kpis.append(_kpi(
            "cost_per_commit", "Coste por commit", eff / commits, fmt_usd,
            prev=(p_eff / _sum_ing(ing, prior, "commits"))
            if prior and _sum_ing(ing, prior, "commits") else None,
            spark=_spark(ing, lambda i: i["cost_effective"] / i["commits"]
                         if i["commits"] else None)))
    if releases:
        kpis.append(_kpi(
            "cost_per_release", "Coste por release", eff / releases, fmt_usd,
            prev=(p_eff / _sum_ing(ing, prior, "releases"))
            if prior and _sum_ing(ing, prior, "releases") else None,
            spark=_spark(ing, lambda i: i["cost_effective"] / i["releases"]
                         if i["releases"] else None)))
    return kpis



# ----------------------------------------------------------------------
# Vistas pre-calculadas por periodo (FPA-026): Python computa, JS re-escala
# ----------------------------------------------------------------------

def _windows(months):
    """Ventanas predefinidas (FPA-090): periodo completo, YTD por año, cada
    año, cada trimestre y cada mes con datos, más todo rango contiguo
    custom de ≥2 meses — todas pre-calculadas; el selector JS solo elige
    entre vistas ya calculadas."""
    out = [("all", "Todo el periodo", list(months), None)]
    years = sorted({ym[:4] for ym in months})
    for y in years:
        wy = [ym for ym in months if ym.startswith(y)]
        py = str(int(y) - 1)
        pwy = [ym for ym in months if ym.startswith(py)]
        prior = pwy if len(pwy) == len(wy) else None
        out.append((f"ytd:{y}", f"YTD {y}", wy, prior))
        out.append((f"year:{y}", y, wy, prior))
        for q in range(1, 5):
            qms = [f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)
                   if f"{y}-{m:02d}" in months]
            if not qms:
                continue
            pq = (q - 1) or 4
            py = f"{int(y) - 1}" if q == 1 else y
            pms = [f"{py}-{m:02d}" for m in range(3 * pq - 2, 3 * pq + 1)
                   if f"{py}-{m:02d}" in months]
            out.append((f"q:{y}Q{q}", f"Q{q} {y[2:]}", qms,
                        pms if len(pms) == len(qms) else None))
    for ym in months:
        idx = months.index(ym)
        p = [months[idx - 1]] if idx else None
        out.append((f"month:{ym}", month_label(ym), [ym], p))
    # FPA-090: rangos custom — todo rango contiguo de ≥2 meses que no sea
    # ya un preset; prior = rango inmediato anterior de la misma longitud.
    known = {k for k, *_ in out}
    for i in range(len(months)):
        for j in range(i + 2, len(months) + 1):
            window = months[i:j]
            key = f"range:{window[0]}..{window[-1]}"
            if key in known:
                continue
            plen = j - i
            prior = months[i - plen:i] if i - plen >= 0 else None
            label = f"{month_label(window[0])} → {month_label(window[-1])}"
            out.append((key, label, window, prior))
    return out


def _tree_rows(node, window, prior, total_cost, is_time=False, full=False):
    """Filas de árbol pre-formateadas para una ventana (columnas FPA-023/024).
    Cada celda sale lista para render; JS no formatea nada."""
    cost = _cost_of(node, window)
    inter = _inter_of(node, window)
    prior_cost = None if node.get("totals_only") else (
        _cost_of(node, prior) if prior else None)
    row = {
        "key": node["key"], "label": node["label"],
        "cost": round(cost, 2),
        "cost_display": fmt_usd(cost) if cost else "n/a",
        "pct_display": (f"{100 * cost / total_cost:.1f}%"
                        if total_cost and cost else "n/a"),
        "interactions": inter,
        "interactions_display": fmt_int(inter),
        "per_1k_display": (fmt_usd(cost / (inter / 1000)) if inter and cost
                           else "n/a"),  # FPA-042
        "delta_display": (_delta_display(_pct_change(cost, prior_cost))
                          if prior_cost is not None else "n/a"),
        "totals_only": bool(node.get("totals_only")),
        "children": [],
    }
    if is_time:
        # coffe-9p4: los presupuestos solo existen en meses in-budget; si la
        # ventana mezcla meses fuera del periodo presupuestado (root, años),
        # la varianza compara solo el tramo in-budget en vez de mezclar
        # coste de 10 meses contra presupuesto de 6.
        bmonths = [m for m in window if m in node["by_month"]
                   and node["by_month"][m].get("budget")]
        budget = sum(node["by_month"][m]["budget"] for m in bmonths)
        if budget:
            variance = (sum(node["by_month"][m]["cost"] for m in bmonths)
                        if len(bmonths) < len(window) else cost) - budget
        row["budget_display"] = fmt_usd(budget) + " *" if budget else "n/a"
        row["variance_display"] = (f"{'+' if variance >= 0 else '-'}"
                                   f"${abs(variance):,.2f}" if budget else "n/a")
        row["variance_pct_display"] = (f"{100 * variance / budget:+.1f}%"
                                       if budget else "n/a")
    if node.get("url"):  # coffe-vp8: passthrough del link al repo
        row["url"] = node["url"]
        row["private"] = bool(node.get("private"))
    row["children"] = [
        _tree_rows(c, window, prior, total_cost, is_time, full)
        for c in node["children"]
        if full or not c.get("totals_only")  # drill Model: solo periodo completo
    ]
    return row


def _delta_display(delta):
    if delta is None:
        return "n/a"
    return f"{'+' if delta >= 0 else ''}{delta * 100:.1f}%"


def build_views(report, cfg):
    """FPA-026: una vista pre-calculada por ventana (todo, año, trimestre, mes).
    Cada vista trae KPIs con delta vs ventana previa, las tres columnas de
    árboles y su limitación si aplica (FPA-027)."""
    months_all = [m["ym"] for m in build_months(report)]
    ing = kpi_ingredients(report, cfg)
    months = [m for m in months_all if m in ing]
    trees = {
        "time": build_time_tree(report, cfg),
        "tool": build_tool_tree(report),
        "portfolio": build_portfolio_tree(report, cfg),
    }
    # FPA-101: roll-up verificado por árbol (falla ruidosa si se rompe)
    for name, tree in trees.items():
        bad = check_rollup(tree)
        if bad:
            raise ValueError(f"roll-up inconsistente en árbol {name}: {bad}")
    views = {}
    for key, label, window, prior in _windows(months):
        full = key == "all"
        view = {
            "label": label,
            "months": window,
            "has_prior": prior is not None,
            "kpis": kpis_for_window(ing, window, prior),
        }
        view["trees"] = {
            "time": _tree_rows(trees["time"], window, prior,
                               _cost_of(trees["time"], window), is_time=True),
            "tool": _tree_rows(trees["tool"], window, prior,
                               _cost_of(trees["tool"], window)),
            "portfolio": _tree_rows(trees["portfolio"], window, prior,
                                    _cost_of(trees["portfolio"], window),
                                    full=full),
        }
        if not full:  # FPA-021/027: el drill Project→Model solo tiene totales
            view["limitation"] = ("el drill Project→Model solo está disponible "
                                  "para el periodo completo (no hay "
                                  "project-by-model por mes)")
        views[key] = view
    return views


# ======================================================================
# F8 (coffe-5ng): vista de energía estimada con banda de caché
# ======================================================================

def build_energy(report):
    """F8 (coffe-5ng): datos de la vista Energía — kWh mensuales del
    reporte (7mj, nominal con factor del config) + banda de sensibilidad
    de caché (coffe-5ng: low/high con factor 0.0/1.0). Los meses cuyo
    energy_kwh es 0.0 por modelos sin telemetría se listan con las razones
    por modelo (loss visible, FPA-008): nunca se muestran como medición.
    """
    md = report["metadata"]
    factors = (md.get("energy_band_cache_factors")
               if md.get("energy_provenance") == "assumed" else None)
    end = md.get("date_range", {}).get("end")
    months = []
    tot_kwh = tot_low = tot_high = 0.0
    for ym in sorted(report["monthly"].keys()):
        mo = report["monthly"][ym]
        if "energy_kwh" not in mo:
            continue  # fallback sin config (7mj): sin campos mensuales
        kwh = mo.get("energy_kwh") or 0.0
        band = mo.get("energy_kwh_band") or {"low": 0.0, "high": 0.0}
        no_tel = [f"{model}: {e['reason']}"
                  for model, e in (mo.get("energy_kwh_by_model") or {}).items()
                  if isinstance(e, dict) and e.get("kwh") is None]
        # FPA-004: meses parciales marcados (días transcurridos / total)
        total_days = month_total_days(ym)
        partial = bool(end) and end[:7] == ym and end[8:10] and \
            int(end[8:10]) < total_days
        elapsed = int(end[8:10]) if partial else total_days
        tot_kwh += kwh
        tot_low += band["low"]
        tot_high += band["high"]
        months.append({"ym": ym, "label": month_label(ym), "kwh": kwh,
                       "low": band["low"], "high": band["high"],
                       "no_telemetry": no_tel, "partial": partial,
                       "elapsed": elapsed, "total_days": total_days})
    return {
        "months": months,
        "total": {"kwh": round(tot_kwh, 3), "low": round(tot_low, 3),
                  "high": round(tot_high, 3)},
        "provenance": md.get("energy_provenance", "unavailable"),
        "factors": factors,
        "nominal_factor": md.get("energy_cache_read_factor"),
    }


def energy_bars_svg(energy, w=560, h=190):
    """F8 (coffe-5ng): barras mensuales con banda low–high y marcador
    del nominal dentro. SVG inline determinista; cada bar lleva el
    contrato del readout (tabindex/aria-label/data-label, FPA-175)."""
    ms = [m for m in energy["months"] if m["low"] > 0 or m["high"] > 0]
    if not ms:
        return ""
    hi = max(m["high"] for m in ms) or 1.0
    n = len(ms)
    bw = min(60.0, (w - 30) / n - 14)
    gap = (w - 20 - n * bw) / max(n - 1, 1)

    def Y(v):
        return h - 26 - v / hi * (h - 48)

    parts = []
    x = 12.0
    for m in ms:
        pm = " (parcial: {}/{} días)".format(m["elapsed"], m["total_days"]) \
            if m.get("partial") else ""
        label = (f"{m['label']}: {m['kwh']:.3f} kWh "
                 f"(banda {m['low']:.3f}–{m['high']:.3f}){pm}")
        parts.append(
            f'<rect class="enb" x="{x:.1f}" y="{Y(m["high"]):.1f}" '
            f'width="{bw:.1f}" height="{max(Y(m["low"]) - Y(m["high"]), 1.0):.1f}" '
            f'tabindex="0" role="img" aria-label="{esc_html(label)}" '
            f'data-label="{esc_html(m["label"])}" '
            f'data-value="{esc_html(f"{m["kwh"]:.3f}")}"/>')
        parts.append(
            f'<line class="enbn" x1="{x:.1f}" x2="{x + bw:.1f}" '
            f'y1="{Y(m["kwh"]):.1f}" y2="{Y(m["kwh"]):.1f}"/>')
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{Y(m["high"]) - 4:.1f}" '
            f'class="enbt" text-anchor="middle">{m["kwh"]:.1f}</text>')
        parts.append(
            f'<text x="{x + bw / 2:.1f}" y="{h - 8:.1f}" class="enbl" '
            f'text-anchor="middle">{m["label"]}</text>')
        x += bw + gap
    return (f'<svg class="enb-chart chart" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" '
            f'aria-label="Energía estimada por mes con banda de caché">'
            f'{"".join(parts)}</svg>')


def energy_html(energy):
    """F8 (coffe-5ng): sección de la vista Energía — KPI total con banda,
    chart mensual, caption de extremos y meses sin telemetría con razón.
    FPA-008: sin config de energía (fallback) muestra n/a con razón,
    nunca un 0 que parezca medición."""
    t = energy["total"]
    if energy["provenance"] != "assumed":
        reason = ("sin config de energía (fallback): sin coeficientes "
                  "versionados no hay estimación")
        na = fig('<span class="na">n/a</span>', energy["provenance"])
        return (f'\n  <p class="finding">{na}</p>'
                f'\n  <p class="finding-meta">{reason}</p>')
    kpi = (f"{t['kwh']:.1f} kWh (banda {t['low']:.1f}–{t['high']:.1f})")
    parciales = [m for m in energy["months"] if m.get("partial")]
    nota_par = ""
    if parciales:
        pm = ", ".join(f"{m['label']} ({m['elapsed']}/{m['total_days']} días)"
                       for m in parciales)
        nota_par = (f" Meses parciales sin anualizar: {pm} — el total "
                    "acumula lo transcurrido, no proyecta fin de mes.")
    cap = ("Estimación con coeficientes de laboratorio (assumed, nunca "
           "medición). La banda muestra los extremos de sensibilidad al "
           "acierto de caché: low = 0% y high = 100% de los tokens de "
           "cache_read servidos desde caché; la marca en cada barra es el "
           f"escenario base (factor nominal {energy['nominal_factor']})."
           + nota_par)
    no_tel = [(m["label"], m["no_telemetry"])
              for m in energy["months"] if m["no_telemetry"]]
    no_tel_html = ("<details class=\"tree\" id=\"energy-no-telemetry\">"
                   "<summary><h3>Meses sin telemetría de tokens</h3></summary>"
                   + "".join(
                       f"<p class=\"small\"><strong>{lbl}</strong>: "
                       + "; ".join(reasons) + "</p>"
                       for lbl, reasons in no_tel)
                   + "</details>") if no_tel else ""
    return f"""
  <p class="finding">{fig(f'<span class="val">{kpi}</span>', energy["provenance"])}</p>
  <p class="finding-meta">total del periodo, banda de sensibilidad de caché incluida</p>
  {energy_bars_svg(energy)}
  <p class="small">{cap}</p>
  {no_tel_html}"""


def build_data_notes(report):
    """Sección Data: definición de interacción + kinds (FPA-140), share
    filtrado (FPA-141), timezone (FPA-142) y concurrencia (FPA-120)."""
    md = report["metadata"]
    kinds_total = {"user_prompt": 0, "assistant_turn": 0, "tool_call": 0}
    for mo in report["monthly"].values():
        if mo["interactions"] == 0:
            continue
        for k, n in (mo.get("interaction_kinds") or {}).items():
            kinds_total[k] = kinds_total.get(k, 0) + n
    total_kinds = sum(kinds_total.values())
    filtered = report.get("filtered_out") or {}
    total_all = md["total_interactions"] + filtered.get("interactions", 0)
    tz = md.get("timezone")
    conc = report.get("concurrency") or {}
    return {
        "interaction_definition": (
            "interacción = una llamada de API registrada en los logs "
            "(assistant turn facturable). Los eventos se cuentan además por "
            "kind — user prompts, assistant turns y tool calls — para que la "
            "inflación por actividad de agentes sea visible (FPA-140)."),
        "kinds": kinds_total,
        "tool_call_share": (round(kinds_total["tool_call"] / total_kinds, 4)
                            if total_kinds else None),
        "filtered_share": (round(filtered.get("interactions", 0) / total_all, 4)
                           if total_all else None),
        "filtered_interactions": filtered.get("interactions", 0),
        "timezone": tz if tz else None,
        "timezone_missing_reason": (None if tz else
                                    "el reporte no registra timezone (FPA-142); "
                                    "vistas hora/día sin verificar"),
        "unverified_time": tz is None,
        "concurrency": conc,
    }


# ======================================================================
# F3: render de presupuesto, bridge y forecast (valores pre-calculados;
# JS solo re-escala varianza y forecast — design.md)
# ======================================================================

def marker_html(mk, cls=""):
    """FPA-054/076: marker favorable/desfavorable — símbolo + texto."""
    if not mk:
        return ""
    fav = {True: "mk-fav", False: "mk-unfav", None: "mk-neutral"}[mk.get("favorable")]
    return (f'<span class="marker {fav} {cls}" title="{esc_html(mk["text"])}">'
            f'{mk["symbol"]} {esc_html(mk["text"])}</span>')


def budget_html(budget):
    """FPA-050…055: tabla de varianza mensual + YTD + inputs editables.
    El presupuesto de efectivo se muestra como informativo (soft, OQ-1)."""
    rows_html = []
    for r in budget["rows"]:
        if r["in_budget"] and r["actual_cash"] is not None:
            cells_cash = (f'<td>{r["actual_display_cash"]}</td>'
                          f'<td>{r["budget_display_cash"]}</td>'
                          f'<td>{_fmt_signed(r["variance_cash"])} '
                          f'{marker_html(r["marker_cash"])}</td>'
                          f'<td>{r["variance_pct_display_cash"]}</td>')
            cells_eff = (f'<td>{r["actual_display_eff"]}</td>'
                         f'<td>{r["budget_display_eff"]}</td>'
                         f'<td>{_fmt_signed(r["variance_eff"])} '
                         f'{marker_html(r["marker_eff"])}</td>'
                         f'<td>{r["variance_pct_display_eff"]}</td>')
        else:  # FPA-008: n/a con razón, nunca vacío
            reason = esc_html(r["reason_cash"] or "")
            na = f'<span class="na" title="{reason}">n/a</span>'
            cells_cash = (f'<td colspan="4">{na} — {reason}</td>' if reason
                          else '<td colspan="4"><span class="na">n/a</span></td>')
            cells_eff = cells_cash
        pr = (f' <span class="small" title="pro-rata FPA-052">'
              f'({r["elapsed"]}/{r["total_days"]})</span>' if r["partial"] else "")
        rows_html.append(
            f'<tr data-ym="{r["ym"]}"><td>{r["label"]}{pr}</td>'
            f'<td>{"sí" if r["in_budget"] else "no"}</td>'
            f'{cells_cash}{cells_eff}</tr>')
    ytd_cash, ytd_eff = budget["ytd_cash"], budget["ytd_eff"]
    if ytd_cash:
        ytd_row = (f'<tr class="ytd"><td><strong>YTD</strong></td><td></td>'
                   f'<td>{ytd_cash["actual_display"]}</td>'
                   f'<td>{ytd_cash["budget_display"]}</td>'
                   f'<td>{ytd_cash["variance_display"]} '
                   f'{marker_html(ytd_cash["marker"])}</td>'
                   f'<td>{ytd_cash["variance_pct_display"]}</td>'
                   f'<td>{ytd_eff["actual_display"]}</td>'
                   f'<td>{ytd_eff["budget_display"]}</td>'
                   f'<td>{ytd_eff["variance_display"]} '
                   f'{marker_html(ytd_eff["marker"])}</td>'
                   f'<td>{ytd_eff["variance_pct_display"]}</td></tr>')
    else:
        ytd_row = (f'<tr class="ytd"><td><strong>YTD</strong></td>'
                   f'<td colspan="9"><span class="na">n/a</span> — sin meses '
                   f'dentro del periodo presupuestado</td></tr>')
    inputs = budget["inputs"]
    return f'''<details class="tree" data-tree="budget" id="budget" open>
<summary><h2>Presupuesto y varianza</h2></summary>
<p class="small">El presupuesto de <strong>efectivo</strong> es informativo
(soft); el gestionado es el de <strong>cash</strong>. Editá los valores —
las varianzas se recalculan sin recargar (FPA-056). Cifras del config:
{prov_tag("assumed")}. El YTD suma solo meses con datos dentro del periodo
presupuestado (los meses sin datos no fabrican actual=0, FPA-017).</p>
<label>Cash $/mes <input class="b-input" id="budget-cash" type="number"
 step="0.01" min="0" value="{inputs["cash_monthly"]}"></label>
<label>Efectivo $/mes <input class="b-input" id="budget-eff" type="number"
 step="0.01" min="0" value="{inputs["effective_monthly"]}"></label>
<label>Objetivo $/1k <input class="b-input" id="budget-target" type="number"
 step="0.01" min="0" value="{inputs["target_per_1k"]}"></label>
<table class="btable">
<thead><tr><th>Mes</th><th>En presupuesto</th>
<th>Cash real</th><th>Budget cash</th><th>Var cash</th><th>Var %</th>
<th>Efectivo real</th><th>Budget efectivo</th><th>Var efectivo</th><th>Var %</th>
</tr></thead>
<tbody>{"".join(rows_html)}{ytd_row}</tbody>
</table>
</details>'''


def bridge_html(bridge):
    """FPA-060/067/068 + D6 (coffe-8nw): un único waterfall visible con
    selector de mes (default: último mes con datos de mix). Python
    pre-calcula los waterfalls en <template data-bridge-month> y el JS
    monta el elegido sin recarga. Meses sin datos de mix (par n/a o con
    coste $0 en ambos meses — marco vacío) quedan fuera del selector;
    sin ningún mes con mix → n/a con razón, sin selector vacío (FPA-008).
    """
    def _has_mix(b):
        # señal de mix: el par no es n/a y alguno de los dos meses tiene
        # coste; con $0 en ambos el waterfall es un marco vacío
        return (not b.get("n_a_reason")
                and (b["cost0"] > 0 or b["cost1"] > 0))

    def _figure(b):
        return (f'<figure><h3>{esc_html(b["label"])}</h3>{b["svg"]}'
                f'<figcaption class="small">Δ {fmt_usd(b["delta"])} = Volumen '
                f'{fmt_usd(b["volume"])} + Mix {fmt_usd(b["mix"])} + Rate '
                f'{fmt_usd(b["rate"])} (identidad ≤ $0.01, residuo '
                f'{abs(b["identity_residual"]):.4f})</figcaption></figure>')

    mix = {ym: b for ym, b in bridge["pairs"].items() if _has_mix(b)}
    fuera = [b["label"] for ym, b in bridge["pairs"].items()
             if ym not in mix]
    if not mix:  # FPA-008: n/a con razón, sin selector vacío
        razon = "ningún mes con datos de mix (coste efectivo por modelo)"
        if fuera:
            razon += " — pares sin señal: " + ", ".join(fuera)
        head = f'<p class="f3-nv">n/a — {esc_html(razon)}</p>'
    else:
        # default: último mes con datos de mix (orden de inserción cronológico)
        default = list(mix)[-1]
        opts = "".join(
            f'<option value="{ym}"'
            f'{" selected" if ym == default else ""}>'
            f'{esc_html(b["label"])}</option>' for ym, b in mix.items())
        head = (f'<label class="small" for="bridge-month">Mes '
                f'<select id="bridge-month">{opts}</select></label>'
                f'<div id="bridge-figure"></div>'
                + "".join(f'<template data-bridge-month="{ym}">'
                          f'{_figure(b)}</template>' for ym, b in mix.items()))
        if fuera:
            head += (f'<p class="small">Fuera del selector (sin datos de '
                     f'mix): {esc_html(", ".join(fuera))}</p>')
    stack_rows = []
    for s in bridge["mix_stack"].values():
        cells = []
        for i, seg in enumerate(s["segments"]):
            pct = f"{100 * seg['share']:.1f}%"
            title = f"{esc_html(seg['model'])}: {pct} ({esc_html(s['metric'])})"
            cells.append(f'<span class="stackbar" title="{title}" '
                         f'style="width:{100 * seg["share"]:.1f}%; '
                         f'background:var(--acc);opacity:{0.35 + 0.13 * i:.2f}">'
                         f'</span>')
        proxy = (f' <span class="na">(proxy: {esc_html(s["proxy_reason"])})</span>'
                 if s["proxy"] else "")
        stack_rows.append(
            f'<tr><td>{esc_html(s["label"])}</td>'
            f'<td><span class="stackrow">{"".join(cells)}</span>{proxy}</td></tr>')
    return f'''<details class="tree" data-tree="bridge" id="bridge">
<summary><h2>Bridge precio-volumen-mix (efectivo)</h2></summary>
{head}
<h3>Mix de modelos por mes (100% stacked)</h3>
<table class="small" id="mix-stack">
<tbody>{"".join(stack_rows)}</tbody>
</table>
</details>'''


def forecast_html(fc):
    """FPA-070…077: forecast con escenarios editables (update sin reload),
    filas forecast marcadas con △ (no solo color) y outlook vs presupuesto."""
    if fc.get("n_a_reason"):  # FPA-008: nunca forecast inventado
        return (f'<details class="tree" data-tree="forecast" '
                f'id="forecast" open>\n'
                f'<summary><h2>Forecast y outlook</h2></summary>\n'
                f'<p class="f3-nv">n/a — {esc_html(fc["n_a_reason"])}</p>\n'
                f'</details>')
    base = fc["base"]
    plan_opts = "".join(
        f'<option value="{esc_html(p["key"])}"'
        f'{" selected" if p["key"] == fc["default_plan"] else ""}>'
        f'{esc_html(p["label"])} (${p["fee"]:,.2f}/mes)</option>'
        for p in fc["plans"])
    rows = []
    rem = fc["remainder"]
    if rem:  # FPA-075: resto del mes parcial, fila separada y marcada
        rows.append(
            f'<tr data-fc-ym="{rem["ym"]}"><td>{esc_html(rem["label"])} '
            f'<span class="small">(resto: {rem["elapsed"]}/{rem["total_days"]})</span></td>'
            f'<td>{fmt_usd(rem["eff"])} {marker_html(rem["marker"])}</td>'
            f'<td>{fmt_usd(rem["cash"])} {marker_html(rem["marker"])}</td></tr>')
    for r in fc["rows"]:
        rows.append(
            f'<tr data-fc-ym="{r["ym"]}"><td>{esc_html(r["label"])}</td>'
            f'<td>{fmt_usd(r["eff"])} {marker_html(r["marker"])}</td>'
            f'<td>{fmt_usd(r["cash"])} {marker_html(r["marker"])}</td></tr>')
    out = fc["outlook"]
    # FPA-074: YTD real + outlook vs presupuesto, con varianza
    outlook = (f'<h3>Outlook: YTD + forecast vs presupuesto'
               f' (hasta {esc_html(out["ym_to"] or "")})</h3>'
               f'<table class="fc-tbl"><tbody>'
               f'<tr><td>Real YTD cash</td><td>{fmt_usd(out["actual_cash"])}</td></tr>'
               f'<tr><td>Proyección cash (YTD + outlook)</td>'
               f'<td>{fmt_usd(out["projected_cash"])} '
               f'{marker_html(out["marker_cash"])}</td></tr>'
               f'<tr><td>Budget cash</td><td>{fmt_usd(out["budget_cash"])}</td></tr>'
               f'<tr><td>Varianza cash</td><td>{_fmt_signed(out["variance_cash"])} '
               f'({_fmt_pct_signed(out["variance_pct_cash"])}) '
               f'{marker_html(out["marker_cash"])}</td></tr>'
               f'<tr><td>Real YTD efectivo</td><td>{fmt_usd(out["actual_eff"])}</td></tr>'
               f'<tr><td>Proyección efectivo (YTD + outlook)</td>'
               f'<td>{fmt_usd(out["projected_eff"])} '
               f'{marker_html(out["marker_eff"])}</td></tr>'
               f'<tr><td>Budget efectivo (informativo)</td>'
               f'<td>{fmt_usd(out["budget_eff"])}</td></tr>'
               f'<tr><td>Varianza efectivo</td><td>{_fmt_signed(out["variance_eff"])} '
               f'({_fmt_pct_signed(out["variance_pct_eff"])}) '
               f'{marker_html(out["marker_eff"])}</td></tr>'
               f'</tbody></table>')
    return f'''<details class="tree" data-tree="forecast" id="forecast" open>
<summary><h2>Forecast y outlook</h2></summary>
<p class="small">Run-rate base = media FME de los últimos 3 meses con datos
({", ".join(base["months_used"])}). Escenario default pre-calculado en
Python; los cambios se recalculan sin recargar (FPA-077). Cifras forecast:
{prov_tag("assumed")}.</p>
<label>Crecimiento mensual % <input class="fc-input" id="fc-growth" type="number"
 step="0.5" value="0"></label>
<label>Cambio de rate % <input class="fc-input" id="fc-rate" type="number"
 step="0.5" value="0"></label>
<label>Plan futuro <select id="fc-plan">{plan_opts}</select></label>
<table class="fc-tbl">
<thead><tr><th>Mes</th><th>Efectivo</th><th>Cash</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
{outlook}
</details>'''


# ======================================================================
# coffe-6lz F4: jerarquía tipográfica — FPA-ids fuera del cuerpo visible
# ======================================================================

# Referencia a spec: FPA-120b, FPA-116/117, FPA-061…063, FPA-028/036
FPA_REF_RE = re.compile(r"FPA-\d{3}[a-z]?(?:\s*[/…]\s*\d{3}[a-z]?)*")

VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img",
                 "input", "link", "meta", "param", "source", "track",
                 "wbr"}


class _FpaRefMover(HTMLParser):
    """Mueve refs "FPA-xxx" del texto visible al atributo title del
    elemento contenedor (machine-readable, invisible). Los chips de
    marginalia quedan intactos (anti-goal del ticket: sí llevan ids)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.stack = []  # índices de tokens de start tag abiertos

    def _marginalia(self, attrs):
        return "marginalia" in dict(attrs).get("class", "").split()

    def handle_starttag(self, tag, attrs):
        idx = len(self.out)
        self.out.append({"tag": tag, "attrs": list(attrs),
                         "marg": self._marginalia(attrs)})
        if tag not in VOID_ELEMENTS:
            self.stack.append(idx)

    def handle_startendtag(self, tag, attrs):
        # conservar el autocierre: sin "/>" el parser HTML anida elementos
        # SVG (rects hijos de rect no se pintan → waterfall vacío, F7).
        self.out.append(self._serialize(tag, list(attrs), [], self_closing=True))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.out[self.stack[i]]["tag"] == tag:
                del self.stack[i:]
                break
        self.out.append(f"</{tag}>")

    def _in_marginalia(self):
        return any(self.out[i]["marg"] for i in self.stack)

    @staticmethod
    def _serialize(tag, attrs, extra_ids, self_closing=False):
        if extra_ids:
            attrs = list(attrs)
            merged = None
            for i, (k, v) in enumerate(attrs):
                if k == "title":
                    merged = (v + " · " if v else "") + " ".join(extra_ids)
                    attrs[i] = (k, merged)
                    break
            if merged is None:
                attrs.append(("title", " ".join(extra_ids)))
        parts = [f"<{tag}"]
        for k, v in attrs:
            if v is None:
                parts.append(f" {k}")
            else:
                parts.append(f' {k}="{html.escape(v, quote=True)}"')
        parts.append("/>" if self_closing else ">")
        return "".join(parts)

    def handle_data(self, data):
        if not FPA_REF_RE.search(data) or self._in_marginalia():
            self.out.append(data)
            return
        ids = [m.group(0).replace(" ", "") for m in FPA_REF_RE.finditer(data)]
        t = re.sub(r"config\s+(?=FPA-\d{3})", "", data)
        t = FPA_REF_RE.sub("", t)
        t = re.sub(r"\(\s*\)", "", t)          # paréntesis vacío
        t = re.sub(r"\(\s*[,;:]\s*", "(", t)   # puntuación huérfana inicial
        t = re.sub(r"\s*[,;:]\s*\)", ")", t)   # puntuación huérfana final
        t = re.sub(r"\s+\)", ")", t)
        t = re.sub(r"\(\s+", "(", t)
        t = re.sub(r"[ \t]{2,}", " ", t)        # colapso de espacios
        t = re.sub(r" +([.,;:!?])", r"\1", t)   # espacio antes de puntuación
        self.out.append(t)
        if self.stack:
            self.out[self.stack[-1]].setdefault("refs", []).extend(ids)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{data}>")

    def result(self):
        chunks = []
        for tok in self.out:
            if isinstance(tok, str):
                chunks.append(tok)
            else:
                chunks.append(self._serialize(tok["tag"], tok["attrs"],
                                              tok.get("refs", [])))
        return "".join(chunks)


def move_fpa_ids_to_titles(html_text):
    """coffe-6lz F4: saca "FPA-xxx" del texto visible y lo pasa al title
    del elemento contenedor. Determinista; marginalia sin tocar."""
    mover = _FpaRefMover()
    mover.feed(html_text)
    mover.close()
    return mover.result()


def fig(value_html, provenance, extra_cls=""):

    """Envoltorio de cifra con provenance tag (FPA-003)."""
    cls = f' class="fig{"" if not extra_cls else " " + extra_cls}"'
    return f'<span{cls} data-provenance="{provenance}">{value_html}</span>'


def claim_html(c):
    """FPA-009: claim con métrica y threshold visibles (con provenance)."""
    return (f'<p class="claim">{c["verdict"]} <strong>{c["text"]}</strong> '
            f'— métrica: <em>{c["metric_name"]}</em> '
            + fig(fmt_usd(c["metric_value"]), "reported") + prov_tag("reported")
            + ' · umbral: '
            + fig(fmt_usd(c["threshold"]), c["threshold_provenance"])
            + prov_tag(c["threshold_provenance"]) + '</p>')


CSS = (site_theme.base_css() + site_theme.STRUCTURE_CSS
       + site_theme.DISCLOSURE_CSS + """
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); line-height:1.5; }
header.site, footer.site { border-bottom: 1px solid var(--line);
  max-width: 960px; margin: 0 auto; padding: .8rem 1rem; }
footer.site { border:0; border-top: 1px solid var(--line); }
header.site h1 { margin: 0; font-size: 1.35rem; }
header.site .meta, .small { color: var(--muted); font-size: .82rem; }
.skip { position:absolute; left:-9999px; }
.skip:focus { left:.5rem; top:.5rem; background:var(--card); padding:.4rem;
  border:1px solid var(--line); }
.banner-partial { background: var(--card); border: 2px outset #fff;
  padding: .5rem .8rem; font-size: .85rem; }
#summary { margin: 1rem 0; }
#summary h2 { margin: 0 0 .2rem; }
.headline h3 { margin: 0 0 .3rem; font-size: .8rem; font-weight: 600;
  color: var(--muted); }
.fig { font-variant-numeric: tabular-nums; font-size: 1.25rem;
  font-weight: 700; display: inline-block; }
.headline .interp, .headline .reason { margin: .3rem 0 0; font-size: .78rem;
  color: var(--muted); }
.na { color: var(--muted); font-weight: 400; }
.claims { margin: .6rem 0 1rem; }
.claim { background: var(--card); border: 1px solid var(--line);
  border-left: 4px solid var(--acc); padding: .5rem .7rem; font-size: .85rem; }
.prov-tag { font-size: .62rem; font-weight: 500; vertical-align: super;
  padding: 0 .25em; border-radius: 3px; background: var(--line);
  color: var(--fg); letter-spacing: .03em; }
.retro { font-size: .7rem; color: var(--muted); letter-spacing: .08em; }
.kpi .spark { display: block; margin-top: .3rem; color: var(--acc); }
#period-select { font: inherit; margin-left: .4rem; }
.ttree { width: 100%; border-collapse: collapse; font-size: .85rem;
  font-variant-numeric: tabular-nums; }
.ttree th { text-align: right; padding: .25rem .5rem; border-bottom: 1px solid var(--line);
  font-weight: 600; color: var(--muted); }
.ttree th:first-child { text-align: left; }
.ttree td { text-align: right; padding: .2rem .5rem; border-bottom: 1px solid var(--line); }
.ttree td:first-child { text-align: left; }
.ttree details > summary { cursor: pointer; list-style: none; }
.ttree details > summary::before { content: '▸ '; color: var(--muted); }
.ttree details[open] > summary::before { content: '▾ '; }
.ttree .repo-link { color: inherit; text-decoration: underline dotted; }
.ttree .repo-link:hover { color: var(--accent, #06c); }
.ttree .lock { font-size: .85em; }
#data { margin-top: 1.5rem; }
#data table { border-collapse: collapse; }
#data th, #data td { padding: .2rem .7rem; border-bottom: 1px solid var(--line);
  text-align: left; }
#view-limitation { color: var(--muted); }
.wf .wfb { fill: var(--line); stroke: var(--muted); stroke-width: .5; }
.wf .wfup { fill: var(--bad); opacity: .75; }
.wf .wfdn { fill: var(--ok); opacity: .75; }
.wf .wft { font-size: 9px; font-variant-numeric: tabular-nums; fill: var(--fg); }
.wf .wfl { font-size: 9px; fill: var(--muted); }
/* F8 (coffe-5ng): vista Energía — banda low–high con marca nominal */
.enb-chart .enb { fill: var(--line); stroke: var(--muted); stroke-width: .5; }
.enb-chart .enbn { stroke: var(--acc); stroke-width: 2; }
.enb-chart .enbt { font-size: 9px; font-variant-numeric: tabular-nums; fill: var(--fg); }
.enb-chart .enbl { font-size: 9px; fill: var(--muted); }
.stackbar { display: inline-block; height: 14px; }
.fc-tbl, .btable { border-collapse: collapse; font-size: .85rem;
  font-variant-numeric: tabular-nums; }
.fc-tbl th, .btable th { text-align: right; padding: .2rem .5rem;
  border-bottom: 1px solid var(--line); color: var(--muted); font-weight: 600; }
.fc-tbl th:first-child, .btable th:first-child { text-align: left; }
.fc-tbl td, .btable td { text-align: right; padding: .15rem .5rem;
  border-bottom: 1px solid var(--line); }
.fc-tbl td:first-child, .btable td:first-child { text-align: left; }
.mk-fav { color: var(--ok); }
.mk-unfav { color: var(--bad); }
.mk-neutral { color: var(--muted); }
.fc-input, .b-input { font: inherit; width: 5.5em; }
.fc-input:invalid, .b-input:invalid { border-color: var(--bad); }
.f3-nv { color: var(--muted); }

/* ===== F6: topbar, tabs, CTAs, export, hallazgos, accesibilidad ===== */
.topbar { position: sticky; top: 0; z-index: 5; background: var(--bg);
  border-bottom: 1px solid var(--line); max-width: 960px; margin: 0 auto;
  padding: .35rem .5rem; display: flex; flex-wrap: wrap; gap: .4rem;
  align-items: center; }
.topbar .tabs { display: flex; flex-wrap: wrap; gap: .25rem; }
.topbar .tab { display: inline-flex; align-items: center; padding: .4rem .7rem;
  min-height:44px; color: var(--fg); text-decoration: none;
  border: 1px solid transparent; font-size: .9rem; }
.topbar .tab[aria-current] { border-color: var(--acc); color: var(--acc);
  font-weight: 600; }
#period-select { font: inherit; min-height:44px; margin-left: auto; }
#share-view { font: inherit; min-height:44px; background: var(--card);
  border: 2px outset #fff; cursor: pointer; }
button, .cta, .ptoggle, .show-all, .export-csv, .dl-svg {
  min-height:44px; font: inherit; cursor: pointer; }
.cta { display: inline-flex; align-items: center; padding: .4rem .8rem;
  border: 2px outset #fff; text-decoration: none;
  color: var(--fg); background: var(--card); }
.cta-primary { border-color: var(--acc); color: var(--acc); font-weight: 600; }
.show-all, .export-csv, .dl-svg { font-size: .8rem; background: var(--card);
  border: 2px outset #fff; padding: .2rem .6rem;
  margin: .3rem 0; }
.ptoggle { background: var(--card); border: 2px outset #fff;
  padding: .2rem .8rem; }
.ptoggle.active { border-color: var(--acc); color: var(--acc); font-weight: 600; }
.finding { margin: .4rem 0 0; font-size: 1.05rem; }
.finding-meta, .sub { color: var(--muted); font-weight: 400;
  font-size: .8rem; margin: .1rem 0 .5rem; }
#wf-readout { font-size: .82rem; min-height: 1.2em;
  font-variant-numeric: tabular-nums; color: var(--muted); }
.wf rect:focus-visible, button:focus-visible, .cta:focus-visible,
.tab:focus-visible, select:focus-visible, input:focus-visible {
  outline: 2px solid var(--acc); outline-offset: 1px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
/* coffe-dqz: una vista a la vez a TODO ancho (revierte deliberadamente
   la parte desktop de FPA-152: "en desktop las vistas van en secuencia");
   el ancla href de cada tab queda como fallback degradado sin JS. */
#summary, #cost, #breakdown, #habits, #outlook, #energy, #data { display: none; }
body[data-view="summary"] #summary,
body[data-view="cost"] #cost,
body[data-view="breakdown"] #breakdown,
body[data-view="habits"] #habits,
body[data-view="outlook"] #outlook,
body[data-view="energy"] #energy,
body[data-view="data"] #data { display: block; }
@media print {
  /* coffe-dqz: la impresión incluye todas las vistas; !important vence
     al toggle por id de la regla base */
  .fpa-view { display: block !important; }
}
@media (max-width:599px) {
  /* FPA-092: una columna (.cards-grid ya colapsa sola con auto-fit) */
  header.site h1 { font-size: 1.1rem; }
  /* coffe-2ni/FPA-109: primer viewport 390x844 con título + selector +
     primera headline — ritmo vertical compacto (estructura, no tokens):
     la línea decorativa retro sale, la topbar y los márgenes se aprietan. */
  header.site .retro { display: none; }
  .topbar { padding: .25rem .4rem; gap: .3rem; }
  main .fpa-view > h2 { font-size: 1.2rem; margin: .3rem 0 .1rem; }
  #summary { margin: .5rem 0; }
  .banner-partial { padding: .3rem .6rem; }
  .marginalia { margin: .3rem 0 .4rem; }
  .claims { margin: .5rem 0; }
  /* FPA-092/177: tablas anchas en contenedor con scroll horizontal y
     primera columna fija */
  .ttree, .btable, .fc-tbl, #data table, table.small {
    display: block; overflow-x:auto; white-space: nowrap; }
  .ttree th:first-child, .ttree td:first-child, .btable td:first-child,
  .fc-tbl td:first-child, #data th:first-child, #data td:first-child,
  table.small th:first-child, table.small td:first-child {
    position:sticky; left: 0; background: var(--bg); }
  /* coffe-26b: touch targets WCAG 2.5.8 — los 5 inputs del dashboard
     suben a 44px (Apple HIG; coste nulo); los 73 summaries anidados del
     ttree suben a >= 24px (mínimo AA real) porque llevarlos a 44px
     duplicaría el scroll del Desglose, denso por diseño. */
  .fc-input, .b-input { min-height: 44px; }
  .ttree details > summary { padding: .25rem 0; }
  /* coffe-26b: labels del forecast en bloque — inline fluyen con el
     label anterior y parten el texto con el input intercalado. */
  #forecast > label { display: block; margin: .45rem 0; }
  /* coffe-26b: al cambiar de tab el h2 de la vista entra bajo la
     topbar (sticky ~4rem) en vez de a ~350px de header común. */
  .fpa-view > h2 { scroll-margin-top: 4rem; }
}
""")

CSS_LEGACY = (  # retro confinado a header/footer, sin animación (FPA-178)
    ".retro::before { content: '▚▞ '; color: var(--acc); }"
)


def finding(value, metric_name, threshold_text, template, fallback,
            _fmt=None):
    """FPA-160/161: bloque título-hallazgo. Título computado de una métrica
    nombrada + threshold, ambos visibles (FPA-009); valor no disponible →
    fallback al label descriptivo."""
    title, meta = finding_parts(value, metric_name, threshold_text, template,
                                fallback, _fmt)
    return (f'<h2 class="finding">{esc_html(title)}</h2>'
            f'<p class="small finding-meta">{meta}</p>')


def finding_parts(value, metric_name, threshold_text, template, fallback,
                  _fmt=None):
    """Partes del hallazgo: (título, meta) — para armar summary + meta en
    details. meta trae métrica, valor (o n/a) y threshold, nunca vacío."""
    _fmt = _fmt or (lambda v: f"{100 * v:.1f}%")
    if value is None:  # FPA-161: fallback al label descriptivo
        title = fallback
        val = '<span class="na">n/a</span>'
    else:
        title = template.format(_fmt(value))
        val = _fmt(value)
    meta = (f'métrica: {esc_html(metric_name)} = {val} · '
            f'threshold: {esc_html(threshold_text)}')
    return title, meta


def top5_rows(rows_html):
    """FPA-155: primeras 5 filas visibles, el resto oculto (extra-row) con
    botón 'Mostrar todo (N)'. Devuelve (filas_html, botón_html)."""
    trs = re.findall(r"<tr.*?</tr>", rows_html, flags=re.S)
    if len(trs) <= 5:
        return rows_html, ""
    extra = "".join(t.replace("<tr", '<tr class="extra-row" hidden', 1)
                    for t in trs[5:])
    btn = (f'<button class="show-all" type="button">Mostrar todo '
           f'({len(trs) - 5})</button>')
    return "".join(trs[:5]) + extra, btn


def number_figures(html):
    """FPA-144: numeración automática de tablas (<caption>) y figuras
    (<figcaption>) — sin duplicados (verificado por FPA-106)."""
    n = [0]

    def _cap(m):
        n[0] += 1
        return f"<caption>Tabla {n[0]} — {m.group(1)}</caption>"

    html = re.sub(r"<caption>(.*?)</caption>", _cap, html, flags=re.S)
    f = [0]

    def _fig(m):
        f[0] += 1
        return f'<figcaption class="small">Figura {f[0]} — {m.group(1)}</figcaption>'

    html = re.sub(r'<figcaption class="small">(.*?)</figcaption>', _fig,
                  html, flags=re.S)
    return html


def add_export_buttons(html):
    """FPA-169: inserta botón 'Exportar CSV' delante de cada tabla y
    'Descargar SVG' delante de cada chart SVG (clase chart). El click lo
    maneja el JS por delegación; sin librerías externas."""
    html = re.sub(
        r"<table",
        '<button class="export-csv" type="button">Exportar CSV</button><table',
        html)
    html = re.sub(
        r'<svg class="(wf |enb-chart )chart"',
        '<button class="dl-svg" type="button">Descargar SVG</button>'
        '<svg class="\\1chart"',
        html)
    return html


def ctas_html(cfg):
    """FPA-165/168: CTAs del Summary desde el config — 1 primario + hasta 3
    secundarios. Los targets vacíos ya hicieron fallar validate_config
    (FPA-168); labels verb-first ≤4 palabras (FPA-172)."""
    ctas = cfg.get("ctas") or {}
    out = ['<div class="ctas" id="view-ctas">']
    p = ctas.get("primary") or {}
    if p.get("target"):
        out.append(f'<a class="cta cta-primary" '
                   f'href="{esc_html(p["target"])}">'
                   f'{esc_html(p["label"])}</a>')
    for c in (ctas.get("secondary") or [])[:3]:
        if c.get("target"):
            out.append(f'<a class="cta" href="{esc_html(c["target"])}">'
                       f'{esc_html(c["label"])}</a>')
    out.append("</div>")
    return "".join(out)


_DOW_NAMES = {1: "lun", 2: "mar", 3: "mié", 4: "jue", 5: "vie",
              6: "sáb", 7: "dom"}


def _wh_text(wh):
    """Horario laboral del config como texto (para el threshold visible)."""
    days = sorted(wh.get("days", []))
    names = (f"{_DOW_NAMES.get(days[0], '?')}–"
             f"{_DOW_NAMES.get(days[-1], '?')}" if days else "n/a")
    return f"{names} {wh.get('start', '?')}–{wh.get('end', '?')}"


def prov_tag(kind):
    """Tag visible de procedencia: reported / assumed."""
    label = "reported" if kind == "reported" else "assumed"
    return f'<span class="prov-tag" title="procedencia: {kind}">{label}</span>'


def kpi_card(k):
    """Card de KPI con sparkline SVG, delta y provenance (FPA-030…045)."""
    if k["value"] is None:  # FPA-042: n/a con razón, nunca cero/infinito
        value = fig('<span class="na">n/a</span>', k["provenance"])
        sub = f'<p class="reason">{k["reason"]}</p>'
    else:
        value = fig(f'<span class="val">{k["display"]}</span>', k["provenance"])
        d = k.get("delta_display")
        # coffe-esc F3: la interpretación de una línea de la headline (si el
        # KPI tiene una) pasa al contexto del card, junto al delta.
        ctx = k.get("headline_interp") or ""
        sub = (f'<p class="interp"'
               + (' title="FPA-045"' if k.get("excluded_share") else "")
               + f'>Δ vs prior: {d or "n/a"}'
               + (f' <span class="small">(tasa diaria)</span>'
                  if k.get("delta_kind") == "daily-rate" and d else "")
               + (f' · excluido por tokens: {k["excluded_display"]}'
                  if k.get("excluded_share") else "")
               + (f' — {ctx}' if ctx else "")
               + "</p>")
    return (f'<div class="headline kpi" data-key="{k["key"]}">'
            f'<h3>{k["label"]}</h3>{value}{sub}'
            f'{sparkline_svg(k.get("spark"))}</div>')


def sparkline_svg(series, w=120, h=28):
    """FPA-030: sparkline mensual como SVG inline determinista."""
    vals = [p["value"] for p in (series or [])]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = w / (len(vals) - 1)
    pts = " ".join(
        f"{i * step:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}"
        for i, v in enumerate(vals))
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="sparkline"><polyline fill="none" '
            f'stroke="currentColor" stroke-width="1.5" points="{pts}"/></svg>')


def tree_section(name, title, root, is_time=False, open_=False):
    """Sección de árbol expandible (FPA-020/022) con columnas pre-formateadas."""
    cols = ("Nodo", "Coste", "% total", "Interacciones", "$/1k", "Δ vs prior")
    if is_time:
        cols += ("Presupuesto*", "Varianza", "Var %")
    head = "".join(f"<th>{c}</th>" for c in cols)
    rows = _tree_html(root, is_time)
    open_attr = " open" if open_ else ""
    return (f'<details class="tree" data-tree="{name}"{open_attr}>'
            f'<summary><h2>{title}</h2></summary>'
            f'<table class="ttree"><thead><tr>{head}</tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'{"<p class=\"small\">* presupuesto cash del config (assumed). "
            f'En ventanas que mezclan meses fuera del periodo presupuestado, '
            f'la varianza compara solo el tramo con presupuesto (coffe-9p4).</p>' if is_time else ""}'
            f'</details>')


def _repo_anchor(row):
    """coffe-vp8: label con ancla al repo GH y 🔒 si es privado."""
    label = row["label"]
    if not row.get("url"):
        return esc_html(label)
    anchor = (f'<a class="repo-link" href="{esc_html(row["url"])}" '
              f'target="_blank" rel="noopener noreferrer">{esc_html(label)}</a>')
    if row.get("private"):
        anchor += (' <span class="lock" title="repo privado" '
                   'aria-label="repo privado">🔒</span>')
    return anchor


def _tree_html(row, is_time=False, depth=0):
    cells = (f'<td>{row["cost_display"]}</td><td>{row["pct_display"]}</td>'
             f'<td>{row["interactions_display"]}</td><td>{row["per_1k_display"]}</td>'
             f'<td>{row["delta_display"]}</td>')
    if is_time:
        cells += (f'<td>{row["budget_display"]}</td>'
                  f'<td>{row["variance_display"]}</td>'
                  f'<td>{row["variance_pct_display"]}</td>')
    kids = "".join(_tree_html(c, is_time, depth + 1) for c in row["children"])
    if kids:
        label = (f'<details open><summary class="tlabel">{_repo_anchor(row)}</summary>'
                 f'</details>')
    else:
        label = (f'<span style="display:inline-block;margin-left:{depth * 14}px">'
                 f'{_repo_anchor(row)}</span>')
    return f'<tr class="trow" data-key="{row["key"]}"><td>{label}</td>{cells}</tr>{kids}'


def data_notes_html(notes):
    """Sección Data: definición, kinds, share filtrado, timezone, concurrencia."""
    ks = notes["kinds"]
    total_kinds = sum(ks.values())
    kind_cells = "".join(
        f'<tr><td>{k}</td><td>{fmt_int(v)}</td>'
        f'<td>{(f"{100 * v / total_kinds:.1f}%" if total_kinds else "n/a")}</td></tr>'
        for k, v in sorted(ks.items(), key=lambda x: -x[1]))
    share_txt = (f"{100 * notes['tool_call_share']:.1f}%"
                 if notes["tool_call_share"] is not None else "n/a (sin kinds)")
    filt = (f"{100 * notes['filtered_share']:.1f}%"
            if notes["filtered_share"] is not None else "n/a")
    if notes["unverified_time"]:  # FPA-142
        tz_cell = ('<span class="na">n/a</span> — ' + notes["timezone_missing_reason"])
    else:
        tz = notes["timezone"]
        tz_cell = fig(tz, "reported")
    conc = notes["concurrency"] or {}
    dp = conc.get("distinct_projects_per_hour") or {}
    ps = conc.get("peak_simultaneous_sessions") or {}
    sw = conc.get("project_switches_per_active_hour") or {}
    ps_peak = (fmt_int(ps["peak"]) if ps.get("peak") is not None
               else 'n/a — ' + (ps.get("reason") or "sin datos"))
    return f'''<div class="data-notes">
  <h3>Datos y definiciones</h3>
  <p>{notes["interaction_definition"]}</p>
  <table class="small"><caption>Interacciones por kind</caption>
    <thead><tr><th>kind</th><th>Eventos</th><th>Share</th></tr></thead>
    <tbody>{kind_cells}</tbody></table>
  <p>Share de tool calls: {share_fig(notes["tool_call_share"])} —
     share excluido por el filtro charly: {share_fig(notes["filtered_share"])}
     ({fmt_int(notes["filtered_interactions"])} interacciones).</p>
  <p>Timezone de bucketing hora/día: {tz_cell}</p>
  <p>Concurrencia — proyectos distintos/hora ({dp.get("measure", "n/a")}): pico {fmt_int(dp.get("peak", 0))},
     media {dp.get("avg", 0)}/h · pico de sesiones simultáneas ({ps.get("measure", "parallel-agent")}):
     {ps_peak} · switches de proyecto/hora activa ({sw.get("measure", "human-context-switching")}): {sw.get("value", "n/a")}</p>
</div>'''


def share_fig(v):
    return fig(f"{100 * v:.1f}%", "reported") if v is not None else \
        '<span class="na">n/a</span>'


def alerts_html(alerts):
    """FPA-080: lista de alertas con severity, regla y evidencia.
    Sin alertas → estado visible "sin alertas", nunca sección vacía."""
    if not alerts:
        return ('<details class="tree" data-tree="alerts" id="alerts">'
                '<summary><h2>Alertas</h2>'
                '</summary><p class="small" id="alerts-none">Sin alertas — '
                'ninguna regla se disparó con los umbrales del config '
                '(FPA-088).</p></details>')
    sev_cls = {"high": "sev-high", "medium": "sev-medium", "low": "sev-low"}
    items = []
    for a in alerts:
        ev = ", ".join(f"{k}: {v}" for k, v in sorted(a["evidence"].items()))
        action = ""
        if a.get("action"):
            action = (f'<a class="cta alert-action" '
                      f'href="{esc_html(a["action"]["target"])}">'
                      f'{esc_html(a["action"]["label"])}</a>')
        items.append(
            f'<li class="alert {sev_cls.get(a["severity"], "")}" '
            f'data-rule="{esc_html(a["rule"])}">'
            f'<span class="sev">{esc_html(a["severity"])}</span> '
            f'<strong>{esc_html(a["rule"])}</strong> — '
            f'{esc_html(a["message"])}'
            f'<span class="small">Evidencia: {esc_html(ev)}</span>'
            f'{action}</li>')
    return (f'<details class="tree" data-tree="alerts" id="alerts">'
            f'<summary>'
            f'<h2>Alertas <span class="small">({len(alerts)})</span></h2>'
            f'</summary><ul class="alert-list">{"".join(items)}</ul>'
            f'</details>')


def plan_economy_html(econ):
    """FPA-130…133: paneles por plan (utilización, break-even, headroom) +
    comparación de 4 casos + disclaimer de usage limits."""
    rows = []
    for p in econ["plans"]:
        if p["utilization"] is not None:  # FPA-008: n/a con razón
            util = f'{p["utilization"] * 100:.1f}%'
        else:
            util = (f'<span class="na" '
                    f'title="{esc_html(p["utilization_reason"] or "")}">n/a'
                    f'</span> — {esc_html(p["utilization_reason"] or "")}')
        head = (fmt_usd(p["headroom"]) if p["headroom"] is not None
                else '<span class="na">n/a</span>')
        eff = (fmt_usd(p["eff_cost"]) if p["eff_cost"] is not None
               else '<span class="na">n/a</span>')
        rows.append(
            f'<tr><td>{esc_html(p["tool"])}</td>'
            f'<td>{esc_html(p["label"])}</td>'
            f'<td>{esc_html(p["start"])} → {esc_html(p["end"] or "vigente")}</td>'
            f'<td>{fmt_usd(p["monthly_fee"])}</td>'
            f'<td>{fmt_usd(p["price"])}</td>'
            f'<td>{eff}</td><td>{util}</td>'
            f'<td>{fmt_usd(p["break_even"])}</td><td>{head}</td>'
            f'<td>{prov_tag("assumed")}</td></tr>')
    fc = econ["four_cases"]
    note = (f'<p class="small">Nota: {esc_html(econ["four_cases_note"])}.</p>'
            if econ.get("four_cases_note") else "")
    cases = (("actual (calendario vigente)", fc["actual"]),
             ("todo pay-per-token", fc["todo_pay_per_token"]),
             ("todo Pro", fc["todo_pro"]),
             ("todo Max", fc["todo_max"]))
    case_rows = "".join(
        f'<tr><td>{esc_html(label)}</td><td>{fmt_usd(val)}</td>'
        f'<td>{prov_tag("assumed")}</td></tr>' for label, val in cases)
    return f'''<details class="tree" data-tree="plan-economy" 
id="plan-economy">
<summary><h2>Economía de suscripción</h2></summary>
<p class="small">{esc_html(econ["usage_limits_disclaimer"])}</p>
<table class="btable">
<thead><tr><th>Tool</th><th>Plan</th><th>Periodo</th><th>Cuota/mes</th>
<th>Precio periodo</th><th>Efectivo</th><th>Utilización</th>
<th>Break-even</th><th>Headroom</th><th>Provenance</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
<h3 class="small">Cash del periodo bajo 4 casos (FPA-132)</h3>
<table class="btable" id="four-cases">
<thead><tr><th>Caso</th><th>Cash</th><th>Provenance</th></tr></thead>
<tbody>{case_rows}</tbody>
</table>
{note}
</details>'''


def reconciliation_html(rec):
    """coffe-a31.3 (CRG-F2): HTML de la sección de reconciliación FPA-082.

    Dos tablas: cash real por proveedor (*reported*, con n/a con razón
    para proveedores sin facturas en el periodo, FPA-008) y la matriz por
    tool y mes (real pagado vs fee implícito vs efectivo estimado). Las
    filas todo-cero de la matriz se omiten con nota visible (el detalle
    completo queda en el JSON embebido)."""
    if rec.get("provenance") != "reported":
        reason = esc_html(rec.get("reason") or "n/a")
        return (f'<details class="tree" data-tree="reconciliation" '
                f'id="reconciliation"><summary>'
                f'<h2>Cash real y reconciliación (FPA-082)</h2></summary>'
                f'<p class="f3-nv">n/a — {reason}</p></details>')
    prov_rows = []
    for p in rec["providers"]:
        if p["total_in_period"] is not None:
            detalle = " · ".join(
                f"{esc_html(month_label(ym))}: {fmt_usd(v)}"
                for ym, v in sorted(p["months"].items()) if v)
            celda = (f'{fmt_usd(p["total_in_period"])} '
                     f'{prov_tag("reported")}'
                     f'<span class="small">{" — " + detalle if detalle else ""}</span>')
        else:  # FPA-008: n/a con razón, nunca vacío ni cero inventado
            celda = (f'<span class="na" title="{esc_html(p["n_a_reason"] or "")}">'
                     f'n/a</span> — {esc_html(p["n_a_reason"] or "")}')
        prov_rows.append(f'<tr><td>{esc_html(p["provider"])}</td>'
                         f'<td>{celda}</td></tr>')
    filas = [r for r in rec["rows"]
             if r["charges_real"] or r["subscription_fee_implicit"]
             or r["cost_effective"]]
    omitidas = len(rec["rows"]) - len(filas)
    row_rows = []
    for r in filas:
        pr = f' <span class="small">({esc_html(r["ym"])})</span>'
        row_rows.append(
            f'<tr data-ym="{r["ym"]}"><td>{esc_html(month_label(r["ym"]))}{pr}</td>'
            f'<td>{esc_html(r["tool"])}</td>'
            f'<td>{fmt_usd(r["charges_real"])} {prov_tag("reported")}</td>'
            f'<td>{fmt_usd(r["subscription_fee_implicit"])} '
            f'{prov_tag("assumed")}</td>'
            f'<td>{fmt_usd(r["cost_effective"])} {prov_tag("assumed")}</td>'
            f'<td>{fmt_usd(r["fee_month_total"])}</td></tr>')
    nota = (f'<p class="small">{omitidas} filas todo-cero omitidas '
            f'(el detalle completo queda en el JSON embebido del dashboard).</p>'
            if omitidas else "")
    tabla_prov = ("".join(prov_rows) or
                  '<tr><td colspan="2"><span class="na">n/a</span> — sin '
                  'proveedores en el ledger</td></tr>')
    tabla_rows = ("".join(row_rows) or
                  '<tr><td colspan="6"><span class="na">n/a</span> — la '
                  'reconciliación no está disponible en este reporte '
                  '(regenerar con scripts/usage-tracker.py)</td></tr>')
    return f'''<details class="tree" data-tree="reconciliation" 
id="reconciliation">
<summary><h2>Cash real y reconciliación (FPA-082)</h2></summary>
<p class="small">Cash real = cargos reales del ledger
({esc_html(rec["source"])}) {prov_tag("reported")}; los fees implícitos del
calendario {prov_tag("assumed")} NO son cash: solo reconciliación y economía
de planes. El efectivo estimado {prov_tag("assumed")} es otra medida:
jamás se suma al cash (FPA-002).</p>
<h3 class="small">Cash real por proveedor (periodo del reporte)</h3>
<table class="btable" id="charges-providers">
<thead><tr><th>Proveedor</th><th>Cash periodo (*reported*)</th></tr></thead>
<tbody>{tabla_prov}</tbody>
</table>
<h3 class="small">Reconciliación por tool y mes</h3>
<table class="btable" id="reconciliation-rows">
<thead><tr><th>Mes</th><th>Tool</th><th>Real pagado</th>
<th>Fee implícito</th><th>Efectivo estimado</th><th>Fee total mes</th>
</tr></thead>
<tbody>{tabla_rows}</tbody>
</table>
{nota}
</details>'''


# ======================================================================
# F5: HTML de patrones de uso, concurrencia y lifecycle
# ======================================================================

def _na_cell(reason):
    """Celda n/a con razón (FPA-008), nunca vacía."""
    return f'<span class="na">n/a</span> — {esc_html(reason)}'


def _provenance_note(usage):
    hm = usage["heatmap"]
    if not hm["verified"]:
        return f'<p class="small">Vistas hora/día unverified: {esc_html(hm["unverified_reason"])}</p>'
    return f'<p class="small">Timezone de bucketing: <strong>{esc_html(hm["timezone"])}</strong></p>'


def heatmap_html(usage):
    """FPA-110/142: matriz día×hora como tabla con intensidad; timezone
    etiquetada o marca unverified."""
    hm = usage["heatmap"]
    peak = max((max(row) for row in hm["grid"]), default=0) or 1
    head = "".join(f"<th>{h}</th>" for h in range(24))
    rows = []
    for dow, vals in enumerate(hm["grid"]):
        cells = []
        for n in vals:
            if n:
                alpha = 0.15 + 0.75 * n / peak
                cells.append(f'<td style="background:rgba(138,43,30,{alpha:.2f})" '
                             f'title="{n} interacciones">{n}</td>')
            else:
                cells.append("<td></td>")
        rows.append(f'<tr><th scope="row">{_DOW_SHORT[dow]}</th>{"".join(cells)}</tr>')
    rh = usage["rhythm"]
    title, meta = finding_parts(
        rh["after_hours_share"], "after_hours_share",
        f"fuera de {rh['working_hours_text']} (config, assumed)",
        "El {0} del uso ocurre fuera del horario laboral",
        "After-hours y weekend")
    return f'''<details class="tree" data-tree="heatmap" open id="heatmap">
<summary><h2 class="finding">{esc_html(title)}</h2></summary>
<p class="small finding-meta">Heatmap día×hora · {meta}</p>
{_provenance_note(usage)}
<table class="small heatmap"><caption>Interacciones por día de semana y hora
(día = isoweekday, lunes arriba)</caption>
<thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<p>After-hours (fuera del horario laboral del config, assumed):
{fig(_share(usage["rhythm"]["after_hours_share"]), "reported")} ·
weekend: {fig(_share(usage["rhythm"]["weekend_share"]), "reported")}</p>
</details>'''


def weekly_html(usage):
    """FPA-112: tabla semanal con WoW y varianza de semanas completas."""
    rhythm = usage["rhythm"]
    rows = []
    for w in rhythm["weeks"]:
        tag = " (parcial)" if w["partial"] else ""
        wow_i = _fmt_pct_signed(w["wow_interactions"])
        wow_c = _fmt_pct_signed(w["wow_cost"])
        rows.append(f'<tr><td>{w["start"]}{tag}</td>'
                    f'<td>{fmt_int(w["interactions"])}</td>'
                    f'<td>{fmt_usd(w["cost_effective"])}</td>'
                    f'<td>{wow_i}</td><td>{wow_c}</td></tr>')
    var = rhythm["variance"]
    if var["interactions"] is None:
        var_txt = _na_cell(var["reason"])
    else:
        var_txt = (f'int {var["interactions"]:.1f} · coste '
                   f'{fmt_usd(var["cost_effective"])} '
                   f'({var["weeks_count"]} semanas completas)')
    return f'''<details class="tree" data-tree="weekly" id="weekly">
<summary><h2>Semanal: WoW y varianza</h2></summary>
<table class="small"><caption>Serie semanal ISO (solo semanas con datos)</caption>
<thead><tr><th>Semana (inicio)</th><th>Interacciones</th><th>Coste efectivo</th>
<th>WoW int</th><th>WoW coste</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p>Varianza de valores semanales (reported): {var_txt}</p>
</details>'''


def _skills_inner(usage):
    """Contenido de skills: top, once-uso, zero-uso y trend (FPA-113/114)."""
    s = usage["skills"]
    top_rows, show_all = top5_rows("".join(
        f'<tr><td>{esc_html(x["name"])}</td>'
        f'<td>{fmt_int(x["uses"])}</td></tr>' for x in s["top"]))
    once = ", ".join(s["once"]) if s["once"] else 'n/a — sin skills de un solo uso'
    zero = (", ".join(s["zero"]) if s["zero"] else _na_cell(s["zero_reason"]))
    trend = s["trend"] if s["trend"] else _na_cell(s["trend_reason"])
    return (f'<table class="small"><caption>Skills más usadas (FPA-113)</caption>'
            f'<thead><tr><th>Skill</th><th>Usos</th></tr></thead>'
            f'<tbody>{top_rows}</tbody></table>{show_all}'
            f'<p>Usadas exactamente una vez (FPA-114): {esc_html(once)} · '
            f'sin uso: {zero} · trend mensual: {trend}</p>')


def _commands_inner(usage):
    """Contenido de slash commands (FPA-115)."""
    c = usage["commands"]
    top_rows, show_all = top5_rows("".join(
        f'<tr><td>{esc_html(x["name"])}</td>'
        f'<td>{fmt_int(x["uses"])}</td></tr>' for x in c["top"]))
    trend = c["trend"] if c["trend"] else _na_cell(c["trend_reason"])
    return (f'<table class="small"><caption>Comandos más ejecutados '
            f'(FPA-115)</caption>'
            f'<thead><tr><th>Comando</th><th>Ejecuciones</th></tr></thead>'
            f'<tbody>{top_rows}</tbody></table>{show_all}'
            f'<p>Trend mensual: {trend}</p>')


def skills_commands_html(usage):
    """FPA-156: skills y comandos en una sola vista con toggle."""
    # coffe-dqz: sin open por defecto — la sección primaria de Habits es el
    # heatmap (D5); skills-commands abierto dejaba la vista > 2 viewports a
    # 1280x900 tras el toggle a todo ancho (F1).
    return f'''<details class="tree" data-tree="skills-commands" id="skills-commands">
<summary><h2>Skills y comandos</h2></summary>
<div class="panel-toggle">
<button class="ptoggle active" data-panel="skills-panel" type="button">Skills</button>
<button class="ptoggle" data-panel="commands-panel" type="button">Comandos</button>
</div>
<div id="skills-panel">{_skills_inner(usage)}</div>
<div id="commands-panel" hidden>{_commands_inner(usage)}</div>
</details>'''


def sessions_html(usage):
    """FPA-116…118: buckets, sesiones largas, coste/mediana/p90 y /clear."""
    s = usage["sessions"]
    bucket_rows = "".join(f'<tr><td>{b["label"]}</td>'
                          f'<td>{fmt_int(b["count"])}</td></tr>'
                          for b in s["buckets"])
    cost_bucket = (s["cost_by_bucket"] if s["cost_by_bucket"]
                   else _na_cell(s["cost_by_bucket_reason"]))
    median_p90 = (s["median_p90"] if s["median_p90"]
                  else _na_cell(s["median_p90_reason"]))
    long_raw = "".join(
        f'<tr><td>{fmt_int(x["turns"])}</td><td>{esc_html(x["date"] or "")}</td>'
        f'<td>{esc_html(x["project"] or "")}</td>'
        f'<td>{fmt_usd(x["cost"]) if x["cost"] is not None else _na_cell(x["cost_reason"])}</td></tr>'
        for x in s["longest"])
    long_rows, show_all = top5_rows(long_raw)
    clear = (f"{s['clear_per_100']:.1f}" if s["clear_per_100"] is not None
             else "n/a")
    clear_scope = (" (claude-only)" if s["clear_per_100"] is not None
                   else f' — {_na_cell(s["clear_scope_reason"])}')
    resets_rows = " · ".join(
        f"{esc_html(t)}: {fmt_int(v.get('count', 0))} ({esc_html(v.get('signal', ''))})"
        for t, v in sorted((s.get("resets_by_tool") or {}).items()))
    clear_monthly = (s["clear_monthly"] if s["clear_monthly"]
                     else _na_cell(s["clear_monthly_reason"]))
    no_clear = (s["long_no_clear"] if s["long_no_clear"]
                else _na_cell(s["long_no_clear_reason"]))
    return f'''<details class="tree" data-tree="sessions" id="sessions">
<summary><h2>Sesiones</h2></summary>
<table class="small"><caption>Distribución de longitud de sesión (FPA-116)</caption>
<thead><tr><th>Bucket (turns)</th><th>Sesiones</th></tr></thead>
<tbody>{bucket_rows}</tbody></table>
<table class="small"><caption>Sesiones más largas (FPA-116)</caption>
<thead><tr><th>Turns</th><th>Fecha</th><th>Proyecto</th><th>Coste</th></tr></thead>
<tbody>{long_rows}</tbody></table>
{show_all}
<p>Coste por bucket: {cost_bucket} · mediana/p90 por sesión: {median_p90}</p>
<p>/clear por 100 sesiones (FPA-118){clear_scope}:
{fig(clear, "reported")} · por mes: {clear_monthly} ·
sesiones > {s["long_turns"]} turns sin /clear: {no_clear}</p>
<p>Resets por tool (coffe-i31, señales distintas por harness): {resets_rows}</p>
</details>'''


def timeline_html(usage):
    """FPA-119: primera/última actividad por tool y model con gaps."""
    tl = usage["timeline"]
    note = (_na_cell(tl["unverified_reason"]) if not tl["verified"]
            else 'fechas verificadas con la timezone del tracker')

    def _table(items, caption):
        raw = "".join(
            f'<tr><td>{esc_html(x["name"])}</td><td>{x["first"]}</td>'
            f'<td>{x["last"]}</td>'
            f'<td>{x["max_gap_days"]}</td>'
            f'<td>{"⚠ gap > " + str(usage["timeline"]["gap_days"]) + " días" if x["flagged"] else "—"}</td></tr>'
            for x in items)
        rows, show_all = top5_rows(raw)
        return (f'<table class="small"><caption>{caption}</caption>'
                '<thead><tr><th>Nombre</th><th>Primera</th><th>Última</th>'
                '<th>Gap máx (días)</th><th>Flag</th></tr></thead>'
                f'<tbody>{rows}</tbody></table>{show_all}')

    return f'''<details class="tree" data-tree="timeline" id="timeline">
<summary><h2>Timeline de tools y models</h2></summary>
{_table(tl["tools"], "Por tool (FPA-119)")}
{_table(tl["models"], "Por model (FPA-119)")}
<p class="small">Umbral de gap: {tl["gap_days"]} días (config, assumed) —
{note}</p>
</details>'''


def concurrency_html(usage):
    """FPA-120/121: concurrencia etiquetada + share de sesiones con Agent."""
    c = usage["concurrency"]
    pph = c["projects_per_hour"]
    peak_s = c["peak_simultaneous_sessions"]
    sw = c["switches_per_hour"]
    pph_txt = (f'pico {fmt_int(pph["peak"])}, media {pph["avg"]:.2f}/h'
               if pph.get("peak") is not None else _na_cell(pph.get("reason", "sin datos")))
    peak_txt = (fmt_int(peak_s["peak"]) if peak_s.get("peak") is not None
                else _na_cell(peak_s.get("reason")))
    sw_txt = (f'{sw["value"]:.2f}/h' if sw.get("value") is not None
              else _na_cell(sw.get("reason")))
    a = usage["agent_share"]
    share = _share(a["share"])
    per_tool = " · ".join(
        f"{esc_html(t)}: {_share(v['share']) if v['share'] is not None else _na_cell(v['reason'])}"
        for t, v in sorted((a.get("by_tool_shares") or {}).items()))
    if a["monthly"]:
        m_rows = "".join(f'<tr><td>{m["ym"]}</td><td>{_share(m["share"])}</td></tr>'
                         for m in a["monthly"])
        monthly = (f'<table class="small"><caption>Sesiones con Agent por mes '
                   f'(FPA-121)</caption><thead><tr><th>Mes</th><th>Share</th></tr>'
                   f'</thead><tbody>{m_rows}</tbody></table>')
    else:
        monthly = f'<p>Trend mensual: {_na_cell(a["monthly_reason"])}</p>'
    return f'''<details class="tree" data-tree="concurrency" id="concurrency">
<summary><h2>Concurrencia y autonomía</h2></summary>
<ul class="small">
<li>Proyectos distintos por hora ({esc_html(pph["measure"])}): {pph_txt}</li>
<li>Pico de sesiones/agentes simultáneos ({esc_html(peak_s["measure"])}): {peak_txt}</li>
<li>Switches de proyecto por hora activa ({esc_html(sw["measure"])}): {sw_txt}</li>
</ul>
<p>Share de sesiones con Agent (FPA-121): {fig(share, "reported")}</p>
<p class="small">Por tool: {per_tool} —
{esc_html(a.get("agent_semantics") or "")}</p>
{monthly}
</details>'''


def lifecycle_html(usage):
    """FPA-122/123: new/active/dormant + coste dormante + activos por mes."""
    lc = usage["lifecycle"]
    raw = "".join(
        f'<tr><td>{esc_html(p["name"])}</td><td>{p["status"]}</td>'
        f'<td>{p["first_seen"]}</td><td>{p["last_seen"]}</td></tr>'
        for p in lc["projects"])
    rows, show_all = top5_rows(raw)
    if lc["active_by_month"]:
        m_rows = "".join(f'<tr><td>{m["ym"]}</td><td>{fmt_int(m["count"])}</td></tr>'
                         for m in lc["active_by_month"])
        monthly = (f'<table class="small"><caption>Proyectos activos por mes '
                   f'(FPA-123)</caption><thead><tr><th>Mes</th><th>Activos</th>'
                   f'</tr></thead><tbody>{m_rows}</tbody></table>')
    else:
        monthly = f'<p>Activos por mes: {_na_cell(lc["active_by_month_reason"])}</p>'
    dormant = lc["dormant"]
    per_project = (fmt_usd(dormant["cost_per_project"])
                   if dormant["cost_per_project"] is not None else "n/a")
    ov = usage["repo_overlay"]
    overlay = ("" if ov["available"]
               else f'<p class="small">Overlay de creación de repos (FPA-098): '
                    f'{_na_cell(ov["reason"])}</p>')
    return f'''<details class="tree" data-tree="lifecycle" id="lifecycle">
<summary><h2>Ciclo de vida de proyectos</h2></summary>
<p class="small">Referencia: {lc["reference"]} · new ≤ {lc["new_days"]} días ·
dormant > {lc["dormant_days"]} días sin actividad (config, assumed)</p>
<table class="small"><caption>Clasificación de proyectos (FPA-122)</caption>
<thead><tr><th>Proyecto</th><th>Estado</th><th>Primera</th><th>Última</th></tr></thead>
<tbody>{rows}</tbody></table>
{show_all}
<p>Coste efectivo dormante (FPA-123): total
{fig(fmt_usd(dormant["cost_total"]), "reported")} ·
por proyecto {fig(per_project, "reported")} ({dormant["count"]} proyectos)</p>
{monthly}
{overlay}
</details>'''


def pareto_html(usage):
    """FPA-028/036: Pareto con cola agrupada y concentración top-3."""
    p = usage["pareto"]
    rows = "".join(
        f'<tr><td>{esc_html(r["name"])}</td><td>{fmt_usd(r["cost"])}</td>'
        f'<td>{_share(r["share"])}</td><td>{_share(r["cumulative_share"])}</td></tr>'
        for r in p["rows"])
    rows_html, show_all = top5_rows(rows)
    tail_html = (f'<tr class="tail"><td>Cola agrupada ({p["tail"]["count"]} '
                 f'proyectos)</td>'
                 f'<td>{fmt_usd(p["tail"]["cost"])}</td>'
                 f'<td>{_share(p["tail"]["share"])}</td>'
                 f'<td>{_share(p["tail"]["cumulative_share"])}</td></tr>'
                 if p["tail"] else
                 '<tr class="tail"><td>Cola agrupada (0 proyectos)</td>'
                 '<td>$0.00</td><td>0.0%</td><td>100.0%</td></tr>')
    title, meta = finding_parts(
        p["top3_share"], "top3_share",
        "50% del coste en top-3 (config FPA-088)",
        "Tres proyectos concentran el {0} del coste efectivo",
        "Pareto de proyectos")
    return f'''<details class="tree" data-tree="pareto" id="pareto">
<summary><h2 class="finding">{esc_html(title)}</h2></summary>
<p class="small finding-meta">Pareto de proyectos · {meta}</p>
<table class="small"><caption>Pareto por coste efectivo (FPA-028; fuera del
top {_PARETO_TOP} la cola se agrupa)</caption>
<thead><tr><th>Proyecto</th><th>Coste</th><th>Share</th><th>Share acum.</th></tr></thead>
<tbody>{rows_html}{tail_html}</tbody></table>
{show_all}
<p>Concentración top-3 (FPA-036): {fig(_share(p["top3_share"]), "reported")}
del coste efectivo · total {fig(fmt_usd(p["total"]), "reported")}</p>
</details>'''


def usage_html(usage):
    """Contenido de la vista Habits (FPA-150): patrones de uso, concurrencia
    y lifecycle, sin wrapper de sección (la vista la provee render_html).
    El Pareto vive en Breakdown (FPA-158) y el timeline en Data & method
    (FPA-157)."""
    return (heatmap_html(usage) + weekly_html(usage)
            + skills_commands_html(usage) + sessions_html(usage)
            + concurrency_html(usage) + lifecycle_html(usage))


def render_html(report, cfg, generated=None, today=None):
    """Generar el HTML completo (determinista salvo `generated`, FPA-104)."""
    generated = generated or datetime.now().strftime("%Y-%m-%d %H:%M")
    model = build_model(report, cfg)
    # F4: alertas y economía se calculan fuera del modelo base porque la
    # regla de staleness depende de la fecha de hoy (today inyectable para
    # tests/golden deterministas, FPA-104/105).
    model["alerts"] = build_alerts(report, cfg, today=today)
    model["plan_economy"] = build_plan_economy(report, cfg)
    lang = cfg.get("language", "es")
    site_name = cfg.get("site_name", "Uso y costos de IA")
    retro = cfg.get("retro", {}).get("enabled", False)

    months_meta = model["months"]
    partials = [m for m in months_meta if m["partial"]]
    banner = ""
    if partials:
        bits = [f'{m["label"]} <strong>{m["elapsed"]}/{m["total_days"]}</strong>'
                for m in partials]
        banner = ('<p class="banner-partial">Mes parcial: '
                  + ", ".join(bits)
                  + ' — las comparaciones del mes parcial usan tasas diarias.</p>')

    tracker_version = report["metadata"].get("tracker_version")
    tracker_cell = (f"Tracker {tracker_version}" if tracker_version
                    else "Tracker n/a (versión no registrada en el reporte)")
    period = model["period"]
    period_cell = " → ".join(p or "n/a" for p in
                             (period["start"], period["end"]))

    claims = model["claims"]

    claims_html = "".join(claim_html(c) for c in claims)
    if claims_html:
        claims_html = f"<div class='claims'>{claims_html}</div>"

    # F2: vistas pre-calculadas, KPI strip, árboles y sección Data
    views = model["views"]
    all_view = views["all"]
    # coffe-esc F3: la banda KPI ES el resumen ejecutivo — la interpretación
    # de una línea de cada headline pasa al contexto del KPI card de la vista
    # "all" (por key; los claims conservan sus cifras, fuera de este check).
    HEADLINE_TO_KPI = {"cost_effective": "cost_effective",
                       "cost_cash": "cost_cash",
                       "leverage": "leverage",
                       "cost_per_1k": "cost_per_1k_eff",
                       "outcome": "cost_per_commit"}
    interp_by_key = {HEADLINE_TO_KPI[h["key"]]: h["interp"]
                     for h in model["headlines"] if h["interp"]}
    for k in all_view["kpis"]:
        if k["key"] in interp_by_key:
            k["headline_interp"] = interp_by_key[k["key"]]
    kpis_html = "".join(kpi_card(k) for k in all_view["kpis"])
    trees_html = (
        tree_section("time", "Árbol Time (Año → Trimestre → Mes)",
                     all_view["trees"]["time"], is_time=True)
        + tree_section("tool", "Árbol Tool (Tool → Model)",
                       all_view["trees"]["tool"])
        + tree_section("portfolio", "Árbol Portfolio (Categoría → Proyecto)",
                       all_view["trees"]["portfolio"], open_=True))
    # F4: alertas y economía de suscripción (pre-calculadas)
    f4_html = (alerts_html(model["alerts"])
               + plan_economy_html(model["plan_economy"]))
    # coffe-a31.3 (CRG-F2): cash real del ledger y reconciliación FPA-082
    recon_html = reconciliation_html(model["reconciliation"])
    # F5: patrones de uso, concurrencia y lifecycle (pre-calculados)
    f5_html = usage_html(model["usage"])
    # F6: pareto/timeline/timeline separados del bloque F5 (FPA-157/158)
    pareto_only_html = pareto_html(model["usage"])
    timeline_block = timeline_html(model["usage"])
    notes_inner = data_notes_html(model["data_notes"])
    # F3: presupuesto + bridge quedan en Costo; forecast en Outlook
    budget_bridge_html = (budget_html(model["budget"])
                          + bridge_html(model["bridge"]))
    forecast_only_html = forecast_html(model["forecast"])
    # coffe-5ng (F8): vista Energía
    energy_only_html = energy_html(model["energy"])
    # F6: CTAs del Summary desde el config (FPA-165/168)
    ctas_block = ctas_html(cfg)

    model_json = json.dumps(model, ensure_ascii=False, sort_keys=True)
    tabs = ("summary", "cost", "breakdown", "habits", "outlook",
            "energy", "data")
    tab_labels = {"summary": "Resumen", "cost": "Costo",
                  "breakdown": "Desglose", "habits": "Hábitos",
                  "outlook": "Pronóstico", "energy": "Energía",
                  "data": "Datos y método"}
    tab_html = "".join(
        f'<a class="tab" role="tab" href="#{k}" data-view="{k}"'
        f'{" aria-current=\"true\"" if k == "summary" else ""}>'
        f'{tab_labels[k]}</a>' for k in tabs)
    # Selector de periodo: presets + rangos custom, todos pre-calculados
    preset_opts = "".join(
        f'<option value="{esc_html(k)}">{esc_html(v["label"])}</option>'
        for k, v in views.items() if not k.startswith("range:"))
    range_opts = "".join(
        f'<option value="{esc_html(k)}">{esc_html(v["label"])}</option>'
        for k, v in views.items() if k.startswith("range:"))
    period_options = (
        f'<optgroup label="Presets">{preset_opts}</optgroup>'
        f'<optgroup label="Rango custom">{range_opts}</optgroup>')

    # Marginalia progressive disclosure (coffe-gen.4): strip por superficie
    # con chips de los análisis mapeados (scripts/viz_fpa_guide.py).
    # coffe-udt/0zp: los tokens {{fig:...}} se resuelven contra el reporte
    # vigente — los chips nunca muestran cifras de un snapshot viejo.
    if guide.GUIDE_MD.exists():
        guide_dict = guide.load_guide(guide.resolve_figures(
            guide.GUIDE_MD.read_text(encoding="utf-8"), report, cfg))
        # coffe-8kx: figures para los chips (resuelve {{fig:*}} de energía;
        # sin esto el chip citaría 0.0 kWh en el fallback sin config, FPA-008)
        figures = guide.derive_figures(report, cfg)
        marginalia = {
            s: guide.marginalia_html(guide_dict, cfg, s, figures=figures)
            for s in ("summary", "cost", "breakdown", "habits", "outlook",
                      "energy", "data")}
    else:
        marginalia = {s: "" for s in ("summary", "cost", "breakdown",
                                      "habits", "outlook", "energy", "data")}

    body_inner = f"""<main id="main">
  {banner}
  <section id="summary" class="fpa-view" aria-label="Resumen ejecutivo">
    <h2>¿Cuál es el estado de las cosas?</h2>
    {marginalia["summary"]}
    {claims_html}
    <h2 class="sub">KPIs del periodo</h2>
    <div class="cards-grid" id="kpi-cards">{kpis_html}</div>
    <p class="small" id="view-limitation" hidden></p>
    {ctas_block}
  </section>
  <section id="cost" class="fpa-view" aria-label="Costo">
    <h2>¿Qué estoy gastando?</h2>
    {marginalia["cost"]}
    {budget_bridge_html}
    {f4_html}
    {recon_html}
  </section>
  <section id="breakdown" class="fpa-view" aria-label="Desglose">
    <h2>¿A dónde va el gasto?</h2>
    {marginalia["breakdown"]}
    <div id="tree-box">{trees_html}</div>
    {pareto_only_html}
  </section>
  <section id="habits" class="fpa-view" aria-label="Hábitos">
    <h2>¿Cómo trabajo?</h2>
    {marginalia["habits"]}
    {f5_html}
  </section>
  <section id="outlook" class="fpa-view" aria-label="Pronóstico">
    <h2>¿Qué viene después?</h2>
    {marginalia["outlook"]}
    {forecast_only_html}
  </section>
  <section id="energy" class="fpa-view" aria-label="Energía">
    <h2>¿Cuánta energía consume?</h2>
    {marginalia["energy"]}
    {energy_only_html}
  </section>
  <section id="data" class="fpa-view" aria-label="Datos y método">
    <h2>Datos y método</h2>
    {marginalia["data"]}
    <details class="tree" data-tree="data-method" id="data-method" open>
      <summary><h3>Datos y método</h3></summary>
      {notes_inner}
      {timeline_block}
      <p class="small">Método de render: HTML único generado por
      viz-fpa.py (Python stdlib-only, SVG inline); las matemáticas se
      calculan en Python y el JS solo re-escala (FPA-145).</p>
      <p class="small">¿Qué significa cada análisis? La
      <a href="fpa-guide.html">guía de análisis a 4 niveles</a> explica
      cada uno desde el nivel más simple hasta el más técnico (fuente:
      docs/fpa-analyses-guide.md).</p>
      <p class="small">Descargas: botones Exportar CSV (tablas) y
      Descargar SVG (charts) en cada figura, sin librerías externas.</p>
    </details>
  </section>
</main>"""
    # FPA-144: numeración automática de tablas/figuras, luego botones de
    # export (sobre el contenido del main, nunca sobre el JS embebido)
    body_inner = number_figures(body_inner)
    body_inner = add_export_buttons(body_inner)
    # coffe-6lz F4: jerarquía tipográfica — los ids FPA-xxx salen del
    # cuerpo visible (compiten con las cifras primarias) y viajan al
    # title del elemento contenedor; marginalia queda intacta.
    body_inner = move_fpa_ids_to_titles(body_inner)

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>{site_name}</title>
<style>{CSS}{CSS_LEGACY if retro else ""}</style>
</head>
<body data-view="summary">
<a class="skip" href="#summary">Saltar al contenido</a>
<header class="site">
  <h1>{site_name}</h1>
  <p class="meta">Generado: {generated} · {tracker_cell} · periodo
  {period_cell}</p>
  {'<p class="retro">desk de métricas · edición quarterly</p>' if retro else ""}
</header>
<nav class="topbar" aria-label="Vistas y periodo">
  <div class="tabs" role="tablist">{tab_html}</div>
  <select id="period-select" aria-label="Seleccionar periodo">
    {period_options}
  </select>
  <button id="share-view" type="button">Compartir vista</button>
  <span id="wf-readout" aria-live="polite"></span>
</nav>
{body_inner}
<footer class="site">
  <p class="retro small">{site_name} · generado por viz-fpa.py (stdlib-only,
  SVG inline) · cifras con tag reported/assumed</p>
</footer>
<script type="application/json" id="fpa-model">{model_json}</script>
<script>
(function () {{
  "use strict";
  var MODEL = JSON.parse(document.getElementById("fpa-model").textContent);
  function esc(s) {{
    return String(s).replace(/[&<>"']/g, function (c) {{
      if (c === "&") return "&amp;";
      if (c === "<") return "&lt;";
      if (c === ">") return "&gt;";
      if (c === "'") return "&#39;";
      return "&quot;";
    }});
  }}
  function kpiCard(k) {{
    var value = k.value === null
      ? '<span class="fig" data-provenance="' + k.provenance + '"><span class="na">n/a</span></span><p class="reason">' + esc(k.reason || "") + '</p>'
      : '<span class="fig" data-provenance="' + k.provenance + '"><span class="val">' + esc(k.display) + '</span></span>'
        + '<p class="interp"' + (k.excluded_share ? ' title="FPA-045"' : '') + '>'
        + 'Δ vs prior: ' + esc(k.delta_display || "n/a")
        + (k.delta_kind === "daily-rate" && k.delta_display ? ' <span class="small">(tasa diaria)</span>' : '')
        + (k.excluded_share ? ' · excluido por tokens: ' + esc(k.excluded_display) : '')
        + (k.headline_interp ? ' — ' + esc(k.headline_interp) : '')
        + '</p>';
    var spark = (k.spark && k.spark.length > 1)
      ? '<svg class="spark" width="120" height="28" viewBox="0 0 120 28" role="img" aria-label="sparkline">'
        + sparkPoints(k.spark) + '</svg>' : "";
    return '<div class="headline kpi" data-key="' + esc(k.key) + '"><h3>' + esc(k.label) + '</h3>'
      + value + spark + '</div>';
  }}
  function sparkPoints(series) {{
    var vals = series.map(function (p) {{ return p.value; }});
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1, step = 120 / (vals.length - 1);
    var pts = vals.map(function (v, i) {{
      return (i * step).toFixed(1) + "," + (28 - 2 - (v - lo) / span * 24).toFixed(1);
    }}).join(" ");
    return '<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="' + pts + '"/>';
  }}
  function repoAnchor(row) {{
    // coffe-vp8: espejo JS de _repo_anchor — ancla al repo GH + 🔒 privado
    var label = esc(row.label);
    if (!row.url) return label;
    var html = '<a class="repo-link" href="' + esc(row.url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
    if (row.private) html += ' <span class="lock" title="repo privado" aria-label="repo privado">🔒</span>';
    return html;
  }}
  function treeRows(row, isTime, depth) {{
    var cells = '<td>' + esc(row.cost_display) + '</td><td>' + esc(row.pct_display)
      + '</td><td>' + esc(row.interactions_display) + '</td><td>' + esc(row.per_1k_display)
      + '</td><td>' + esc(row.delta_display) + '</td>';
    if (isTime) cells += '<td>' + esc(row.budget_display) + '</td><td>'
      + esc(row.variance_display) + '</td><td>' + esc(row.variance_pct_display) + '</td>';
    var label = row.children.length
      ? '<details open><summary class="tlabel">' + repoAnchor(row) + '</summary></details>'
      : '<span style="display:inline-block;margin-left:' + (depth * 14) + 'px">' + repoAnchor(row) + '</span>';
    return '<tr class="trow" data-key="' + esc(row.key) + '"><td>' + label + '</td>' + cells + '</tr>'
      + row.children.map(function (c) {{ return treeRows(c, isTime, depth + 1); }}).join("");
  }}
  function renderView(key) {{
    var v = MODEL.views[key];
    if (!v) return;
    document.getElementById("kpi-cards").innerHTML = v.kpis.map(kpiCard).join("");
    var lim = document.getElementById("view-limitation");
    if (v.limitation) {{ lim.textContent = "Limitación: " + v.limitation; lim.hidden = false; }}
    else lim.hidden = true;
    var box = document.getElementById("tree-box");
    box.innerHTML = treeBlock("time", "Árbol Time (Año → Trimestre → Mes)", v.trees.time, true)
      + treeBlock("tool", "Árbol Tool (Tool → Model)", v.trees.tool, false)
      + treeBlock("portfolio", "Árbol Portfolio (Categoría → Proyecto)", v.trees.portfolio, false);
  }}
  function treeBlock(name, title, root, isTime) {{
    var cols = ["Nodo", "Coste", "% total", "Interacciones", "$/1k", "Δ vs prior"];
    if (isTime) cols.push("Presupuesto*", "Varianza", "Var %");
    var head = cols.map(function (c) {{ return "<th>" + c + "</th>"; }}).join("");
    // coffe-x6l F5: solo la primaria (portfolio) abre por defecto;
    // espeja los defaults del render Python.
    var openAttr = (name === "portfolio") ? " open" : "";
    return '<details class="tree" data-tree="' + name + '"' + openAttr
      + '><summary><h2>' + title
      + '</h2></summary><button class="export-csv" type="button">Exportar CSV</button>'
      + '<table class="ttree"><thead><tr>' + head
      + '</tr></thead><tbody>' + treeRows(root, isTime, 0) + '</tbody></table>'
      + (isTime ? '<p class="small">* presupuesto cash del config (assumed). En ventanas que mezclan meses fuera del periodo presupuestado, la varianza compara solo el tramo con presupuesto.</p>' : "")
      + '</details>';
  }}
  var sel = document.getElementById("period-select");
  if (sel) sel.addEventListener("change", function () {{ renderView(sel.value); }});

  // ============ F3: recompute sin reload (FPA-056/077) ============
  // Las fórmulas replican apply_scenario/build_budget de Python; un test
  // de paridad Playwright verifica que igualan los golden pre-calculados.
  function fmtUsd(x) {{
    // FPA-053: signo explícito (+/-$), igual que _fmt_signed de Python
    return (x < 0 ? "-$" : "+$") + Math.abs(x).toLocaleString("en-US",
      {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
  }}
  function fmtPct(f) {{
    return (f >= 0 ? "+" : "") + (f * 100).toFixed(1) + "%";
  }}
  function markerFor(v) {{
    if (v < -0.005) return {{symbol: "\u25bc", text: "bajo", favorable: true}};
    if (v > 0.005) return {{symbol: "\u25b2", text: "sobre", favorable: false}};
    return {{symbol: "\u25cf", text: "en", favorable: null}};
  }}
  function markerHtml(mk) {{
    var cls = mk.favorable === true ? "mk-fav" :
              mk.favorable === false ? "mk-unfav" : "mk-neutral";
    return '<span class="marker ' + cls + '">' + mk.symbol + ' ' + mk.text + '</span>';
  }}
  function budgetRecompute() {{
    // FPA-056: editar presupuesto → re-calcular varianzas sin reload
    var cash = parseFloat(document.getElementById("budget-cash").value) || 0;
    var eff = parseFloat(document.getElementById("budget-eff").value) || 0;
    var rows = document.querySelectorAll("#budget tbody tr[data-ym]");
    var budgetData = MODEL.budget.rows;
    var totA = 0, totB = 0, totE = 0, totBef = 0;
    rows.forEach(function (tr) {{
      var ym = tr.getAttribute("data-ym");
      var r = budgetData.find(function (x) {{ return x.ym === ym; }});
      if (!r || !r.in_budget || r.actual_cash === null) return;
      var pr = r.pro_rate;
      var bCash = Math.round(cash * pr * 100) / 100;
      var bEff = Math.round(eff * pr * 100) / 100;
      var vCash = Math.round((r.actual_cash - bCash) * 100) / 100;
      var vEff = Math.round((r.actual_eff - bEff) * 100) / 100;
      var tds = tr.querySelectorAll("td");
      tds[2].innerHTML = '$' + r.actual_cash.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      tds[3].textContent = '$' + bCash.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      tds[4].innerHTML = fmtUsd(vCash) + ' ' + markerHtml(markerFor(vCash));
      tds[5].textContent = bCash ? fmtPct(vCash / bCash) : "n/a";
      tds[6].innerHTML = '$' + r.actual_eff.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      tds[7].textContent = '$' + bEff.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      tds[8].innerHTML = fmtUsd(vEff) + ' ' + markerHtml(markerFor(vEff));
      tds[9].textContent = bEff ? fmtPct(vEff / bEff) : "n/a";
      totA += r.actual_cash; totB += bCash;
      totE += r.actual_eff; totBef += bEff;
    }});
    var ytd = document.querySelector("#budget tr.ytd");
    if (ytd) {{
      var t = ytd.querySelectorAll("td");
      var vC = Math.round((totA - totB) * 100) / 100;
      var vE = Math.round((totE - totBef) * 100) / 100;
      t[2].textContent = '$' + totA.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      t[3].textContent = '$' + totB.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      t[4].innerHTML = fmtUsd(vC) + ' ' + markerHtml(markerFor(vC));
      t[5].textContent = totB ? fmtPct(vC / totB) : "n/a";
      t[6].textContent = '$' + totE.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      t[7].textContent = '$' + totBef.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
      t[8].innerHTML = fmtUsd(vE) + ' ' + markerHtml(markerFor(vE));
      t[9].textContent = totBef ? fmtPct(vE / totBef) : "n/a";
    }}
  }}
  function applyScenario(growth, rateChg, planKey) {{
    // FPA-072/073: replica de viz.apply_scenario
    var fc = MODEL.forecast;
    if (!fc || fc.n_a_reason) return [];
    var fee = fc.base.fee_base;
    fc.plans.forEach(function (p) {{ if (p.key === planKey) fee = p.fee; }});
    return fc.future_months.map(function (ym, i) {{
      var f = Math.pow(1 + growth, i + 1);
      return {{
        ym: ym,
        eff: Math.round(fc.base.eff_base * f * (1 + rateChg) * 100) / 100,
        cash: Math.round((fee + fc.base.p2p_base * f) * 100) / 100
      }};
    }});
  }}
  function forecastRecompute() {{
    var g = (parseFloat(document.getElementById("fc-growth").value) || 0) / 100;
    var r = (parseFloat(document.getElementById("fc-rate").value) || 0) / 100;
    var plan = document.getElementById("fc-plan").value;
    var rows = applyScenario(g, r, plan);
    var trs = document.querySelectorAll("#forecast tr[data-fc-ym]");
    var fc = MODEL.forecast;
    var nStatic = fc.remainder ? 1 : 0;
    trs.forEach(function (tr, idx) {{
      var i = idx - nStatic;
      if (i < 0 || i >= rows.length) return;  // fila del mes parcial: fija
      var tds = tr.querySelectorAll("td");
      tds[1].innerHTML = '$' + rows[i].eff.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",")
        + ' ' + markerHtml({{symbol: "\u25b3", text: "forecast", favorable: null}});
      tds[2].innerHTML = '$' + rows[i].cash.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",")
        + ' ' + markerHtml({{symbol: "\u25b3", text: "forecast", favorable: null}});
    }});
  }}
  ["budget-cash", "budget-eff", "budget-target"].forEach(function (id) {{
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", budgetRecompute);
  }});
  ["fc-growth", "fc-rate", "fc-plan"].forEach(function (id) {{
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", forecastRecompute);
    if (el && el.tagName === "SELECT") el.addEventListener("change", forecastRecompute);
  }});

  // ============ F6: vistas, tabs, top-5, export, share, readout ============
  var VIEWS = ["summary", "cost", "breakdown", "habits", "outlook",
               "energy", "data"];
  function setView(key) {{
    if (VIEWS.indexOf(key) < 0) return;
    document.body.setAttribute("data-view", key);
    document.querySelectorAll(".tab").forEach(function (t) {{
      if (t.getAttribute("data-view") === key) t.setAttribute("aria-current", "true");
      else t.removeAttribute("aria-current");
    }});
  }}
  document.querySelectorAll(".tab").forEach(function (t) {{
    t.addEventListener("click", function (e) {{
      // coffe-dqz: las vistas se alternan a todo ancho (revierte la parte
      // desktop de FPA-152); en mobile se evita el salto del ancla.
      if (window.matchMedia &&
          window.matchMedia("(max-width:599px)").matches) {{
        e.preventDefault();
        setView(t.getAttribute("data-view"));
        // coffe-26b: el h2 de la vista entra bajo la topbar — los
        // primeros ~350px (header/topbar/banner) no cambian entre
        // vistas y el salto a scrollY=0 no da señal del cambio.
        var h = document.querySelector(
          "#" + t.getAttribute("data-view") + " > h2");
        if (h) h.scrollIntoView({{ block: "start" }});
        return;
      }}
      setView(t.getAttribute("data-view"));
    }});
  }});
  // FPA-155: Mostrar todo — revela las filas extra-row de su bloque
  document.addEventListener("click", function (e) {{
    var b = e.target.closest && e.target.closest("button.show-all");
    if (!b) return;
    var zone = b.closest("details") || document;
    zone.querySelectorAll("tr.extra-row").forEach(function (r) {{ r.hidden = false; }});
    b.remove();
  }});
  // FPA-156: toggle skills/comandos en una sola vista
  document.querySelectorAll(".ptoggle").forEach(function (b) {{
    b.addEventListener("click", function () {{
      var root = b.closest("details");
      root.querySelectorAll(".ptoggle").forEach(function (x) {{
        x.classList.toggle("active", x === b);
      }});
      root.querySelectorAll("div[id$='-panel']").forEach(function (p) {{
        p.hidden = p.id !== b.getAttribute("data-panel");
      }});
    }});
  }});
  // FPA-169: Export CSV (tablas) / Download SVG (charts), sin librerías
  function tableToCSV(table) {{
    var rows = Array.prototype.slice.call(table.querySelectorAll("tr"))
      .filter(function (tr) {{ return !tr.hidden; }});
    return rows.map(function (tr) {{
      return Array.prototype.slice.call(tr.querySelectorAll("th,td")).map(function (c) {{
        var t = c.textContent.replace(/\\s+/g, " ").trim();
        return /[",\\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
      }}).join(",");
    }}).join("\\n");
  }}
  function svgXml(svg) {{
    return '<?xml version="1.0" encoding="UTF-8"?>\\n' + svg.outerHTML;
  }}
  function downloadText(name, text, mime) {{
    var blob = new Blob([text], {{type: mime}});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  }}
  document.addEventListener("click", function (e) {{
    var b = e.target.closest && e.target.closest("button.export-csv");
    if (b) {{
      var t = b.nextElementSibling;
      while (t && t.tagName !== "TABLE") t = t.nextElementSibling;
      if (t) downloadText("fpa-tabla.csv", tableToCSV(t), "text/csv");
      return;
    }}
    b = e.target.closest && e.target.closest("button.dl-svg");
    if (b) {{
      var s = b.nextElementSibling;
      while (s && s.tagName !== "SVG" && s.tagName !== "svg") s = s.nextElementSibling;
      if (s) downloadText("fpa-chart.svg", svgXml(s), "image/svg+xml");
    }}
  }});
  // FPA-175: readout persistente del chart por tap/focus/hover
  // (re-bindable: el bridge monta waterfalls dinámicamente, D6 coffe-8nw)
  var readout = document.getElementById("wf-readout");
  function bindReadout() {{
    document.querySelectorAll(".wf.chart rect[data-label], .enb-chart rect[data-label]").forEach(function (r) {{
      if (r.__wfReadout) return;
      r.__wfReadout = true;
      function show() {{ if (readout) readout.textContent = r.getAttribute("aria-label"); }}
      r.addEventListener("mouseenter", show);
      r.addEventListener("click", show);
      r.addEventListener("focus", show);
    }});
  }}
  bindReadout();
  // D6 (coffe-8nw): bridge con selector de mes — monta el waterfall del
  // mes elegido desde el template data-bridge-month, sin recarga.
  var bridgeSel = document.getElementById("bridge-month");
  function mountBridge() {{
    var fig = document.getElementById("bridge-figure");
    if (!bridgeSel || !fig) return;
    var t = document.querySelector(
      'template[data-bridge-month="' + bridgeSel.value + '"]');
    fig.textContent = "";
    if (t && t.content) fig.appendChild(t.content.cloneNode(true));
    bindReadout();
  }}
  if (bridgeSel) {{
    bridgeSel.addEventListener("change", mountBridge);
    mountBridge();
  }}
  // FPA-170/171: Share view — URL con periodo, vista y expansión de árbol;
  // al cargar se restaura solo con parámetros válidos (los inválidos se
  // ignoran y quedan los defaults).
  function shareUrl() {{
    var p = new URLSearchParams();
    p.set("view", document.body.getAttribute("data-view") || "summary");
    p.set("period", sel ? sel.value : "all");
    var open = [], closed = [];
    document.querySelectorAll("details[data-tree]").forEach(function (d) {{
      (d.open ? open : closed).push(d.getAttribute("data-tree"));
    }});
    if (open.length) p.set("open", open.join(","));
    if (closed.length) p.set("closed", closed.join(","));
    return location.href.split("?")[0] + "?" + p.toString();
  }}
  var shareBtn = document.getElementById("share-view");
  if (shareBtn) shareBtn.addEventListener("click", function () {{
    var url = shareUrl();
    function done() {{ shareBtn.title = "URL copiada: " + url; }}
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(url).then(done, done);
    }} else {{ done(); }}
  }});
  (function applyParams() {{
    try {{ var q = new URLSearchParams(location.search); }} catch (err) {{ return; }}
    var v = q.get("view");
    if (v && VIEWS.indexOf(v) >= 0) setView(v);  // FPA-171: inválidos ignorados
    var pd = q.get("period");
    if (pd && sel && MODEL.views[pd]) {{ sel.value = pd; renderView(pd); }}
    var open = q.get("open");
    if (open) open.split(",").forEach(function (name) {{
      if (!/^[a-z0-9-]+$/.test(name)) return;  // FPA-171: inválidos ignorados
      var d = document.querySelector('details[data-tree="' + name + '"]');
      if (d) d.open = true;
    }});
    var closed = q.get("closed");
    if (closed) closed.split(",").forEach(function (name) {{
      if (!/^[a-z0-9-]+$/.test(name)) return;  // FPA-171: inválidos ignorados
      var d = document.querySelector('details[data-tree="' + name + '"]');
      if (d) d.open = false;
    }});
  }})();
  // Exposición mínima para los tests Playwright (no es parte del UI)
  window.__fpa = {{ setView: setView, tableToCSV: tableToCSV, svgXml: svgXml,
                   shareUrl: shareUrl, VIEWS: VIEWS }};
}})();
</script>
</body>
</html>"""


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
    state = _charges_state(report)
    if state["provenance"] == "reported":
        cash_by_ym = _charges_cash_by_ym(report)
        cash_ledger = fmt_usd(round(sum(cash_by_ym.values()), 2))
    else:
        cash_ledger = "n/a"
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

def main(argv=None):
    ap = argparse.ArgumentParser(description="Dashboard de uso y costos de IA (viz-fpa.py)")
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check-docs", action="store_true",
                    help="verificar cifras del README contra el JSON (FPA-143)")
    ap.add_argument("--readme", default="README.md")
    args = ap.parse_args(argv)

    # FPA-168: targets de CTAs vacíos hacen fallar el generador (se valida
    # antes del reporte: no depende de los datos)
    try:
        cfg = fpa_config.load_fpa_config(args.config)
        cfg_errors = fpa_config.validate_config(cfg)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: config inválida: {e}", file=sys.stderr)
        return 1
    if cfg_errors:
        print("ERROR: config inválida:", file=sys.stderr)
        for e in cfg_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    try:
        report = json.loads(Path(args.report).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: no se pudo leer el reporte: {e}", file=sys.stderr)
        return 1

    if args.check_docs:  # FPA-143/107: consistencia README ↔ JSON
        errors = check_docs(report, Path(args.readme))
        # coffe-gen.3: grounding de la guía (mapeo + umbrales vs config)
        guide_text = guide.GUIDE_MD.read_text(encoding="utf-8") \
            if guide.GUIDE_MD.exists() else ""
        errors += guide.check_documentation(cfg, guide_text)
        if errors:
            print("ERROR: check-docs falló:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print("OK: README consistente con el reporte (FPA-143) · "
              "guía con grounding verificado (mapeo + umbrales)")
        return 0

    # FPA-006: schema validation con exit non-zero y campos fallidos listados
    errors = validate_report(report)
    if errors:
        print(f"ERROR: schema validation falló ({len(errors)} campos):",
              file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    html = render_html(report, cfg)
    problems = check_placeholders(html)
    if problems:  # FPA-108
        print("ERROR: placeholder-check:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"OK: {out} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
