"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar, html, json, re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config, schema_validate, site_theme  # noqa: E402,F401



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

def _recon_tracker_totals(report, fees_by_month):
    """Sumas del fallback sin ledger: cash del tracker vs implícito."""
    reported = implied = 0.0
    for ym, mo in report["monthly"].items():
        cash = mo.get("cost_real", 0.0) or 0.0
        fee = fees_by_month.get(ym, 0.0) or 0.0
        reported += cash
        implied += fee + max(cash - fee, 0.0)
    return reported, implied

def _reconcile_tracker(report, cfg, alerts):
    """Fallback sin ledger (shape antiguo): cash del tracker vs fees + p2p."""
    fees_by_month = report.get("subscription_fees_by_month") or {}
    reported, implied = _recon_tracker_totals(report, fees_by_month)
    if implied <= 0:
        return
    tol = _th(cfg, "reconcile_tolerance_pct", 10.0) / 100.0
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
                         "tolerance_pct": tol * 100}})

def _recon_ledger_totals(report, state):
    """Sumas y meses divergentes con ledger *reported*."""
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
    return reported, implied, divergentes

def _reconcile_ledger(report, cfg, alerts, state):
    """Comparación con ledger *reported*: real del ledger vs implícito."""
    tol = _th(cfg, "reconcile_tolerance_pct", 10.0) / 100.0
    reported, implied, divergentes = _recon_ledger_totals(report, state)
    if implied <= 0 and reported <= 0:
        return
    diff_pct = abs(reported - implied) / implied if implied > 0 \
        else (1.0 if reported > 0 else 0.0)
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
                         "divergent_months": divergentes[:5]}})

def _check_reconciliation(report, cfg, alerts):
    """FPA-082: cash real del ledger (*reported*) vs cargas implícitas del
    calendario (*assumed*) + p2p reportado del ledger. coffe-a31.3: con
    ledger disponible, por mes del reporte se compara real = Σ cargos del
    ledger contra implícito = fees del calendario + pay_per_token_charges
    (solo si es *reported*: los créditos/reembolsos del ledger no son
    cargas del calendario, no se fabrican como implícitos). Sin ledger en
    el reporte → fallback al chequeo previo: cash del tracker vs fees +
    p2p tracker (reportes de shape antiguo)."""
    state = _charges_state(report)
    if state["provenance"] != "reported":
        _reconcile_tracker(report, cfg, alerts)
        return
    _reconcile_ledger(report, cfg, alerts, state)

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
                             "budget_cash": r["budget_cash"]}})
        # coffe-ox3: el presupuesto efectivo es informativo (soft, design.md
        # OQ-1) — su overage queda visible en la tabla de varianza, sin
        # alerta (disparaba todos los meses con montos provisionales).

def _check_unit_cost(months, cfg, alerts):
    """FPA-084: coste por 1k interacciones (efectivo) +X% MoM → alerta.
    Guard coffe-wxc: la base previa debe superar el mínimo
    (`unit_cost_min_base_per_1k`, default $5/1k) — el MoM sobre base casi
    nula es falso positivo (+3871% desde $1.45/1k). Meses sin datos se
    excluyen de la comparación (FPA-017); el ratio efectivo/interacciones
    es invariante al escalado FME, así que los meses parciales son
    comparables sin ajuste."""
    th = _th(cfg, "unit_cost_rise_pct", 15.0) / 100.0
    min_base = _th(cfg, "unit_cost_min_base_per_1k", 5.0)
    prev = None
    for m in months:
        if not m["has_data"]:
            continue
        cur = (m["cost_effective"] / m["interactions"] * 1000
               if m["interactions"] else None)
        rise = _unit_cost_rise(cur, prev, th, min_base)
        if rise is not None:
            alerts.append({
                "severity": "medium", "rule": "unit-cost",
                "message": (f"{m['label']}: coste por 1k interacciones "
                            f"{fmt_usd(cur)} vs {fmt_usd(prev)} "
                            f"(+{rise * 100:.1f}%, umbral +{th * 100:.0f}%)"),
                "evidence": {"month": m["ym"],
                             "prev_per_1k": round(prev, 4),
                             "cur_per_1k": round(cur, 4),
                             "rise_pct": round(rise, 4),
                             "threshold_pct": th * 100,
                             "min_base_per_1k": min_base}})
        prev = cur

def _unit_cost_rise(cur, prev, th, min_base):
    """Rise fracción si supera el umbral con base válida; None si no aplica.
    Guard coffe-wxc: la base previa debe superar el mínimo configurado."""
    if (cur is None or prev is None or prev <= 0
            or prev <= min_base):
        return None
    rise = cur / prev - 1
    return rise if rise > th else None

def _premium_share(month, premium_models):
    """Share de coste efectivo de modelos premium en el mes (0–1)."""
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
                            f"{cur * 100:.1f}% vs {shares[i - 3]['label']} "
                            f"({old * 100:.1f}%) = +{rise_pts:.1f} pts "
                            f"(umbral +{th:.0f} pts)"),
                "evidence": {"month": shares[i]["ym"],
                             "share": round(cur, 4),
                             "base_month": shares[i - 3]["ym"],
                             "base_share": round(old, 4),
                             "rise_pts": round(rise_pts, 2),
                             "threshold_pts": th}})

def _project_totals(report):
    """(pares (proyecto, coste efectivo) con coste > 0, total general)."""
    totals = []
    grand = 0.0
    for proj, months_map in (report.get("project_monthly") or {}).items():
        cost = sum(v.get("cost_effective", 0.0) or 0.0
                   for v in months_map.values())
        if cost > 0:
            totals.append((proj, cost))
            grand += cost
    return totals, grand

def _check_concentration(report, cfg, alerts):
    """FPA-086: top-3 proyectos concentran >X% del efectivo → alerta."""
    th = _th(cfg, "concentration_top3_pct", 50.0) / 100.0
    totals, grand = _project_totals(report)
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
            "evidence": {"top3_share": round(share, 4), "threshold_pct": th * 100,
                         "projects": [p for p, _ in top3]}})

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
            "evidence": {"end": end, "age_days": age, "threshold_days": th}})

def build_alerts(report, cfg, today=None):
    """FPA-080: motor de alertas — cada alerta con severity, rule name y
    valores de evidencia. Reglas: verify-plan (081), reconciliación (082),
    budget (083), unit-cost (084), mix premium (085), concentración (086),
    staleness (087). Todos los umbrales salen del config (FPA-088); cada
    alerta lleva exactamente una acción (FPA-167): target del config
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
from viz_fpa_common import _link  # noqa: E402

_link(globals())
