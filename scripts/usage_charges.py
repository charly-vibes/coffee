#!/usr/bin/env python3
"""Ledger de cargos reales + reconciliación (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _ledger_date_ok(d):
    """Fecha ISO del ledger (YYYY-MM-DD) — chequeo estructural rápido."""
    return (isinstance(d, str) and len(d) == 10
            and d[4] == "-" and d[7] == "-"
            and d[:4].isdigit() and d[5:7].isdigit() and d[8:].isdigit())


def _charge_entry_error(provider, i, e):
    """Valida una entrada del ledger; devuelve el mensaje de error o None."""
    kind = e.get("kind")
    if kind not in CHARGES_KINDS_IA:
        return f"ledger: {provider}[{i}] kind fuera del vocabulario IA: {kind!r}"
    amount = e.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return f"ledger: {provider}[{i}] amount no numérico"
    if not _ledger_date_ok(e.get("date")):
        return f"ledger: {provider}[{i}] date inválida: {e.get('date')!r}"
    return None


def _apply_charge(by_month, p2p, provider, e):
    """Agrega un cargo al mes correspondiente; devuelve el amount (cash)."""
    mes = e["date"][:7]
    amount = e["amount"]
    by_month.setdefault(provider, {}).setdefault(mes, 0.0)
    # redondeo a centavos: transcripción manual, dinero real
    by_month[provider][mes] = round(by_month[provider][mes] + amount, 2)
    if e.get("kind") in CHARGES_KINDS_P2P:
        p2p.setdefault(provider, {}).setdefault(mes, 0.0)
        p2p[provider][mes] = round(p2p[provider][mes] + amount, 2)
    return amount


def cargar_charges(path=None):
    """coffe-a31.2: cargar data/charges.json y agregar por mes (YYYY-MM).

    Devuelve (charges_real_by_month, total, p2p_by_month): {provider:
    {YYYY-MM: amount}} con TODOS los kinds del vocabulario IA (api_cycle
    aporta $0 y así queda en la serie), la suma total IA y la serie p2p
    (credits/refund — FPA-013). Falla loud ante ledger roto (kind fuera
    del vocabulario, fecha inválida, amount no numérico): el ledger es
    transcripción manual — un error debe cortar, no colarse al dashboard.
    """
    path = Path(path) if path else CHARGES_PATH
    ledger = json.loads(path.read_text())
    by_month = {}
    p2p = {}  # solo kinds pay-per-token (credits/refund) — FPA-013
    total = 0.0
    for provider, entries in (ledger.get("providers") or {}).items():
        if not isinstance(entries, list):
            raise ValueError(f"ledger: providers.{provider} no es una lista")
        for i, e in enumerate(entries):
            err = _charge_entry_error(provider, i, e)
            if err is not None:
                raise ValueError(err)
            total = round(total + _apply_charge(by_month, p2p, provider, e), 2)
    return by_month, total, p2p


def _recon_tools(charges_by_month, monthly_dicts):
    tools = set(SUBSCRIPTIONS) | set(charges_by_month)
    # tools con interacciones del tracker también entran (su efectivo importa)
    for mo in monthly_dicts.values():
        tools.update((mo.get("tools") or {}))
    return tools


def _recon_months(charges_by_month, monthly_dicts):
    # meses = los del tracker ∪ los del ledger: un mes con facturas pero sin
    # interacciones (p.ej. recargas openrouter de jul-2026) también reconcilia
    return sorted(set(monthly_dicts) | {
        m for meses_prov in charges_by_month.values() for m in meses_prov})


def _fee_month_total(mes):
    """Fee total del mes por calendario, incluso sin interacciones."""
    return round(sum(_implicit_fee_tool_mes(t, mes) for t in SUBSCRIPTIONS), 8)


def _recon_row(tool, mes, mo, ctx):
    charges_by_month, fee_cal = ctx
    return {
        "charges_real": round(float(charges_by_month.get(tool, {}).get(mes, 0.0)), 8),
        "charges_provenance": "reported",
        "subscription_fee_implicit": _implicit_fee_tool_mes(tool, mes),
        "cost_effective": round(float(
            (mo.get("tools") or {}).get(tool, {}).get("cost_effective", 0.0)), 8),
        "fee_month_total": fee_cal,
    }


def reconciliation_charges(charges_by_month, monthly_dicts):
    """coffe-a31.2: insumo de reconciliación FPA-082, por tool y mes.

    Tres medidas separadas (jamás sumadas entre sí, FPA-002):
    - charges_real: cash pagado según el ledger (*reported*);
    - subscription_fee_implicit: cuota que el calendario de suscripciones
      atribuye al tool-mes (*assumed*) — no es cash, solo reconciliación y
      plan economy;
    - cost_effective: estimado API-equivalente del tracker (*assumed*).

    Proveedores sin cargos en un mes SIEMPRE aparecen (charges_real 0.0,
    nunca vacío — FPA-008): la ausencia de factura es un dato, no un hueco.
    """
    tools = _recon_tools(charges_by_month, monthly_dicts)
    rec = defaultdict(dict)
    for mes in _recon_months(charges_by_month, monthly_dicts):
        mo = monthly_dicts.get(mes, {})
        ctx = (charges_by_month, _fee_month_total(mes))
        for tool in sorted(tools | set(mo.get("tools") or {})):
            rec[mes][tool] = _recon_row(tool, mes, mo, ctx)
    return dict(rec)


def _implicit_fee_tool_mes(tool, mes):
    """Cuota implícita del calendario para (tool, mes): suma de los fees de
    los periodos cuyo rango [start, end) solapa el mes. Complemento por
    tool de calc_subscription_fees (que solo emite el total del mes)."""
    total = 0.0
    for period in SUBSCRIPTIONS.get(tool, []):
        fee = period.get("monthly_fee", 0)
        if fee > 0:
            p_start = period["start"]
            p_end = period["end"] or "9999-12"
            if mes >= p_start[:7] and mes < p_end[:7]:
                total += fee
    return total

usage_state._link(globals())
