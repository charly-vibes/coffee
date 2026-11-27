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
import viz_fpa_guide as guide  # noqa: F401,E402


def _link(g):
    """Inyectar los nombres de todos los módulos viz_fpa_* en `g` (setdefault).

    Se invoca al final de cada módulo: para entonces las definiciones del
    módulo llamante están completas, y las referencias hacia atrás (módulos
    definidos después) se resuelven sin ciclos de importación (import_module
    nunca re-ejecuta un módulo en curso). Se inyecta en cada ejecución: un
    mismo archivo puede ejecutarse varias veces bajo nombres distintos
    (p. ej. viz-fpa.py cargado por tests como "viz_fpa_bridge").
    """
    import importlib
    import os
    here = os.path.dirname(os.path.abspath(g["__file__"]))
    siblings = [f[:-3] for f in sorted(os.listdir(here))
                if f.startswith("viz_fpa_") and f.endswith(".py")]
    mods = []
    for s in siblings:
        mods.append(importlib.import_module(s))
    for m in mods:
        for k, v in vars(m).items():
            if not k.startswith("__"):
                g.setdefault(k, v)

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


def _month_cash(mo, ym, state, cash_by_ym):
    """(cash, provenance, reason, charges_by_provider) del mes según ledger."""
    if state["provenance"] == "reported":
        return (cash_by_ym[ym], "reported", None,
                {p: round(pv[ym], 2) for p, pv in
                 state["by_month"].items() if ym in pv})
    return mo["cost_real"], "assumed", state["reason"], None


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
    last = date.fromisoformat(end) if end else None
    for ym in sorted(report["monthly"].keys()):
        mo = report["monthly"][ym]
        total_days = month_total_days(ym)
        partial = last is not None and end[:7] == ym and last.day < total_days
        cash, cash_prov, cash_reason, charges_prov = _month_cash(
            mo, ym, state, cash_by_ym)
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
