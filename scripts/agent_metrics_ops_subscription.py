"""Purpose: ops.subscription — utilización de suscripción (epic coffe-a5v,
hijo coffe-9fp): coste notional por período de suscripción vs la cuota mensual
reportada en config/fpa.json, sin inventar cifras.

Responsibilities: leer el bloque `subscriptions` de config/fpa.json (lista de
{source, label, monthly_fee, start, end} con start/end 'YYYY-MM-DD' o null;
también acepta el formato dict {source: [períodos]} que usa hoy el repo),
imputar coste notional por período sumando session_spend.cost_effective de
data/usage_report_v3.json filtrando por source y por day ∈ [start, end]
(cuando el período tiene límites), calcular utilization = cost_imputed /
monthly_fee y headroom_daily = max(0, fee − cost_imputed) / días del período,
y listar modelos de session_spend cuya familia (model_mapping) no cae en
ninguna suscripción configurada. Lane notional; provenance {subscription:
'derived'}; coverage = % de días del período con gasto reportado.

Rationale: la decisión "¿renuevo la suscripción o pago por token?" necesita
comparar el coste notional imputado contra la cuota real; el coste ya está
reportado por sesión en usage_report_v3.json, así que no se vuelve a estimar.
Fee 0 o ausente → utilization null con razón (nunca 0-as-missing), igual que
el resto de los slices del epic.

Corrida: `python3 scripts/agent_metrics_ops_subscription.py` después del
tracker; mergea ops.subscription y valida contra el schema.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_common import ROOT  # noqa: E402
from agent_metrics_ops import compile_mapping, provider_for, run_slice  # noqa: E402

USAGE_REPORT = ROOT / "data" / "usage_report_v3.json"
NO_CONFIG_REASON = "no_subscription_config"
NO_MODEL_REASON = "no_subscription_for_model"
NO_FEE_REASON = "no_monthly_fee"
NO_BOUNDS_REASON = "no_period_bounds"


# ----------------------------------------------------------------------
# Config: bloque subscriptions
# ----------------------------------------------------------------------

def _normalize_entry(source, entry):
    return {
        "source": entry.get("source") or source,
        "label": entry.get("label") or "",
        "monthly_fee": entry.get("monthly_fee"),
        "start": entry.get("start"),
        "end": entry.get("end"),
    }


def subscription_periods(config):
    """Bloque `subscriptions` de config → lista normalizada de períodos.

    Acepta dict {source: [entry, ...]} (formato actual del repo) o lista de
    {source, label, monthly_fee, start, end}. Absente/inválido → []."""
    raw = config.get("subscriptions")
    if not raw:
        return []
    if isinstance(raw, dict):
        return [_normalize_entry(source, e)
                for source, entries in raw.items() for e in (entries or [])]
    if isinstance(raw, list):
        return [_normalize_entry(e.get("source"), e) for e in raw]
    return []


def _in_period(day, start, end):
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _days_in_period(period):
    """Días del período (inclusivos) o None si faltan límites."""
    if not (period["start"] and period["end"]):
        return None
    d0 = date.fromisoformat(period["start"])
    d1 = date.fromisoformat(period["end"])
    if d1 < d0:
        return None
    return (d1 - d0).days + 1


# ----------------------------------------------------------------------
# Cómputo del slice
# ----------------------------------------------------------------------

def _spend_days(rows, source):
    return {r["day"] for r in rows if r.get("source") == source and r.get("day")}


def compute_subscription(spend, config, mapping):
    """Slice ops.subscription completo desde session_spend + config."""
    periods_cfg = subscription_periods(config)
    models = sorted({r.get("model") or "(sin modelo)" for r in spend})
    out = {"periods": [], "models_without_period": [], "provenance": {},
           "coverage": []}

    if not periods_cfg:
        # Sin config: ningún período, y todos los modelos quedan sin período.
        out["models_without_period"] = [{"model": m, "reason": NO_CONFIG_REASON}
                                        for m in models]
        out["provenance"] = {"subscription": "derived"}
        out["coverage"] = [{"source": "subscription", "coverage_pct": 0.0,
                            "reason": NO_CONFIG_REASON}]
        return out

    covered_sources = {p["source"] for p in periods_cfg}
    out["models_without_period"] = [
        {"model": m, "reason": NO_MODEL_REASON}
        for m in models
        if provider_for(m, mapping) not in covered_sources
    ]
    out["provenance"] = {"subscription": "derived"}

    for period in periods_cfg:
        rows = [r for r in spend
                if r.get("source") == period["source"]
                and _in_period(r.get("day") or "", period["start"], period["end"])]
        cost = round(sum(r["cost_effective"] for r in rows
                         if r.get("cost_effective") is not None), 2)
        fee = period["monthly_fee"]
        period_out = {
            "source": period["source"],
            "label": period["label"],
            "start": period["start"],
            "end": period["end"],
            "monthly_fee": fee if isinstance(fee, (int, float)) and fee >= 0 else 0.0,
            "cost_imputed": cost,
            "utilization": None,
            "headroom_daily": None,
        }
        reasons = []
        if isinstance(fee, (int, float)) and fee > 0:
            period_out["utilization"] = round(cost / fee, 4)
        else:
            reasons.append(NO_FEE_REASON)
        days = _days_in_period(period)
        if days:
            period_out["headroom_daily"] = round(max(0.0, fee - cost) / days, 2) \
                if isinstance(fee, (int, float)) and fee >= 0 else None
        else:
            reasons.append(NO_BOUNDS_REASON)
        if reasons:
            period_out["reason"] = "; ".join(reasons)
        out["periods"].append(period_out)

        spend_days = _spend_days(spend, period["source"])
        if days:
            covered = sum(1 for d in spend_days if _in_period(d, period["start"], period["end"]))
            pct = round(100.0 * covered / days, 1)
            out["coverage"].append({"source": f"{period['source']} {period['label']}",
                                    "coverage_pct": pct, "reason": None})
        else:
            out["coverage"].append({"source": f"{period['source']} {period['label']}",
                                    "coverage_pct": 0.0, "reason": NO_BOUNDS_REASON})
    return out


def compute(inputs):
    """Punto de entrada run_slice: carga fpa.json completo (run_slice entrega
    solo el bloque agent_behavior) y session_spend del usage report."""
    config = json.loads((inputs["root"] / "config" / "fpa.json").read_text(encoding="utf-8"))
    mapping = compile_mapping((config.get("model_mapping") or {}).get("entries"))
    report = json.loads((inputs["root"] / "data" / "usage_report_v3.json")
                        .read_text(encoding="utf-8"))
    return compute_subscription(report.get("session_spend") or [],
                                config, mapping)


def main():
    doc, sub = run_slice("subscription", compute)
    periods = sub["periods"]
    util_txt = ", ".join(
        f"{p['label'] or p['source']}: "
        + (f"{p['utilization'] * 100:.0f}%" if p.get("utilization") is not None
           else f"n/a ({p.get('reason', NO_FEE_REASON)})")
        for p in periods)
    print(f"ops.subscription: {len(periods)} períodos; utilización {util_txt or 'n/a (sin config)'}; "
          f"{len(sub['models_without_period'])} modelos sin período")
    return 0


# ----------------------------------------------------------------------
# Render (viz-agent.py): una <section class="block"> server-side, sin JS
# ----------------------------------------------------------------------

def _fmt_money(v):
    if v is None:
        return "—"
    return f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fmt_dates(p):
    if p.get("start") and p.get("end"):
        return f"{p['start']} → {p['end']}"
    if p.get("start") or p.get("end"):
        return f"desde {p.get('start') or p.get('end')}"
    return "sin límites"


def render_section(ops):
    sub = (ops or {}).get("subscription")
    if not sub:
        return ('<section class="block"><h2>Utilización de suscripción</h2>'
                '<p class="reason">n/a: bloque ops.subscription ausente '
                '(correr scripts/agent_metrics_ops_subscription.py).</p></section>')
    rows = []
    for p in sub.get("periods") or []:
        util = p.get("utilization")
        util_txt = (f"{util * 100:.1f}%".replace(".", ",") if util is not None
                    else f"n/a: {p.get('reason', NO_FEE_REASON)}")
        head = p.get("headroom_daily")
        rows.append(
            f"<tr><td>{p['source']}</td><td>{p['label']} ({_fmt_dates(p)})</td>"
            f"<td>${_fmt_money(p.get('monthly_fee'))}</td>"
            f"<td>${_fmt_money(p.get('cost_imputed'))}</td>"
            f"<td>{util_txt}</td>"
            f"<td>{'—' if head is None else '$' + _fmt_money(head) + '/día'}</td></tr>")
    if rows:
        table = ('<table><thead><tr><th>Fuente</th><th>Período</th><th>Cuota</th>'
                 '<th>Coste imputado</th><th>Utilización</th><th>Headroom/día</th>'
                 '</tr></thead><tbody>\n' + "\n".join(rows) + "\n</tbody></table>")
    else:
        table = '<p class="reason">Sin períodos de suscripción configurados en config/fpa.json.</p>'
    without = sub.get("models_without_period") or []
    note = ""
    if without:
        models_txt = ", ".join(m["model"] for m in without)
        note = (f'<p class="reason">Modelos sin período de suscripción: {models_txt} '
                f'({without[0]["reason"]}).</p>')
    return ('<section class="block"><h2>Utilización de suscripción</h2>' + table + note
            + '<p class="reason">Coste notional imputado de session_spend '
            '(usage_report_v3, lane notional) contra la cuota mensual reportada '
            'en config; utilization = coste imputado / cuota. Headroom/día = '
            'sobrante no gastado por día del período.</p></section>')


if __name__ == "__main__":
    sys.exit(main())
