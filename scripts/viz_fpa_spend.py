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




def _day_projects(pds, day):
    projects = []
    for p in sorted(pds):
        v = pds[p].get(day)
        if v:
            projects.append({
                "project": p,
                "interactions": v["interactions"],
                "cost_effective": v["cost_effective"],
                "cost_real": v["cost_real"],
                "tokens": dict(v["tokens"]),
            })
    return projects


def _day_tokens(projects):
    return {t: sum(p["tokens"][t] for p in projects)
            for t in ("in", "out", "cache_read", "cache_write")}


def _zero_cost_reason(total_eff, day_tokens):
    if total_eff == 0 and (day_tokens["in"] or day_tokens["out"]
                           or day_tokens["cache_read"]
                           or day_tokens["cache_write"]):
        return ("coste efectivo 0.0 con tokens > 0: modelos sin pricing "
                "en config o sesiones sin telemetría de coste")
    return None


def _session_entries(sess):
    return [{
        "session_id": s["session_id"],
        "project": s["project"],
        "start": s["start"][11:16],
        "source": s["source"],
        "model": s["model"],
        "n_interactions": s["n_interactions"],
        "cost_effective": s["cost_effective"],
        "cost_reason": s.get("cost_reason"),
        "crosses_midnight": s.get("crosses_midnight", False),
    } for s in sess]


def _delta7(total_eff, days, i, day_totals):
    """Delta vs media de los 7 días previos disponibles; None si no hay."""
    prev = [day_totals[d]["total_effective"] for d in days[max(0, i - 7):i]]
    return round(total_eff - sum(prev) / len(prev), 8) if prev else None

def _day_entry(projects, total_eff, sess, delta7):
    """Dicc del día: totales, tokens, delta, proyectos y sesiones."""
    total_real = round(sum(p["cost_real"] for p in projects), 8)
    day_tokens = _day_tokens(projects)
    razones = [s["cost_reason"] for s in sess if s.get("cost_reason")]
    return {
        "total_effective": total_eff,
        "total_real": total_real,
        "tokens": day_tokens,
        "delta7": delta7,
        "projects": projects,
        "sessions": _session_entries(sess),
        "razon": _zero_cost_reason(total_eff, day_tokens),
        "razones_sesiones": razones,
    }

def _day_totals(pds, days, sessions_by_day):
    """Una entrada por día, en orden; delta7 mira hasta 7 días atrás."""
    day_totals = {}
    for i, day in enumerate(days):
        sess = sorted(sessions_by_day.get(day, []), key=lambda s: s["start"])
        projects = _day_projects(pds, day)
        total_eff = round(sum(p["cost_effective"] for p in projects), 8)
        day_totals[day] = _day_entry(projects, total_eff, sess,
                                     _delta7(total_eff, days, i, day_totals))
    return day_totals

def build_daily_spend(report):
    """Datos de la vista "Gasto diario": por día, total con delta vs media
    de los 7 días previos disponibles, desglose por proyecto y sesiones del
    día. Todo pre-calculado desde project_daily_spend + session_spend.
    Los dos costes son serie *assumed* del tracker (el cash del ledger se
    concilia a nivel mensual, CRG-F1 — nunca mezclado en esta vista)."""
    pds = report.get("project_daily_spend") or {}
    ss = report.get("session_spend") or []
    if not pds and not ss:
        return {"available": False,
                "reason": ("el reporte no trae project_daily_spend ni "
                           "session_spend (dataset anterior al cambio o "
                           "regen pendiente del nightly)")}
    days = sorted({d for days in pds.values() for d in days}
                  | {r["day"] for r in ss})
    sessions_by_day = {}
    for s in ss:
        sessions_by_day.setdefault(s["day"], []).append(s)
    return {"available": True, "provenance": "assumed", "days_keys": days,
            "days": _day_totals(pds, days, sessions_by_day)}


def daily_spend_html(ds):
    """HTML de la vista "Gasto diario": selector de día (templates
    pre-calculados, JS solo monta — mismo patrón del selector de mes del
    bridge), tabla por proyecto y drill-down por sesión. Provenance y
    alcance del cash declarados; día sin datos → n/a con razón."""
    if not ds.get("available"):
        return (f'\n  <p class="finding"><span class="na">n/a</span></p>'
                f'\n  <p class="finding-meta">{esc_html(ds["reason"])}</p>')
    days = ds["days"]
    default = ds["days_keys"][-1]
    opts = "".join(
        f'<option value="{d}"{" selected" if d == default else ""}>{d}</option>'
        for d in ds["days_keys"])

    def _sessions_table(d):
        if not d["sessions"]:
            return '<p class="small">Sin sesiones registradas este día.</p>'
        sess_rows = "".join(
            f'<tr><td>{esc_html(s["start"])}</td>'
            f'<td>{esc_html(s["project"])}</td>'
            f'<td>{esc_html(s["source"])}</td>'
            f'<td>{esc_html(s["model"])}'
            f'{" <span class=\"na\">(cruza medianoche)</span>" if s["crosses_midnight"] else ""}'
            f'</td><td class="num">{s["n_interactions"]}</td>'
            f'<td class="num">'
            + (fmt_usd(s["cost_effective"]) if s["cost_effective"] is not None
               else f'<span class="na">n/a — {esc_html(s["cost_reason"])}</span>')
            + '</td></tr>'
            for s in d["sessions"])
        faltantes = sum(1 for s in d["sessions"] if s["cost_effective"] is None)
        nota_falt = (f' <span class="na">({faltantes} sesión(es) sin '
                     'telemetría de coste, excluidas del total)</span>'
                     if faltantes else "")
        return (f'<h3>Sesiones del día</h3>'
                f'<table class="small"><tr><th>Inicio</th><th>Proyecto</th>'
                f'<th>Source</th><th>Modelo</th><th>Inter.</th><th>Coste</th></tr>'
                f'{sess_rows}</table><p class="small">{nota_falt}</p>')

    def _day_block(day):
        d = days[day]
        delta = (f" Δ {fmt_usd(d['delta7'])} vs media 7d"
                 if d["delta7"] is not None else
                 " <span class=\"na\">Δ n/a (sin días previos suficientes)</span>")
        razon = (f'<p class="finding-meta">{esc_html(d["razon"])}</p>'
                 if d.get("razon") else "")
        proj_rows = "".join(
            f'<tr><td>{esc_html(p["project"])}</td>'
            f'<td class="num">{p["interactions"]}</td>'
            f'<td class="num">{fmt_usd(p["cost_effective"])}</td>'
            f'<td class="num">{fmt_usd(p["cost_real"])}</td>'
            f'<td class="num">{fmt_int(sum(p["tokens"].values()))}</td></tr>'
            for p in d["projects"])
        sess_table = _sessions_table(d)
        return (f'<p class="finding"><span class="val">{fmt_usd(d["total_effective"])}'
                f'</span> <span class="small">efectivo {delta}</span></p>'
                f'<p class="finding-meta">coste real sub_cost '
                f'{fmt_usd(d["total_real"])} · tokens '
                f'{fmt_int(sum(d["tokens"].values()))}</p>'
                f'{razon}'
                f'<h3>Por proyecto</h3>'
                f'<table class="small"><tr><th>Proyecto</th><th>Inter.</th>'
                f'<th>Coste efectivo</th><th>Coste real (sub_cost)</th><th>Tokens</th></tr>'
                f'{proj_rows}</table>'
                f'{sess_table}')

    templates = "".join(
        f'<template data-daily-day="{d}">{_day_block(d)}</template>'
        for d in ds["days_keys"])
    return f'''
  <p class="finding-meta">costes serie <em>assumed</em> del tracker — el cash
  del ledger de cargos se concilia a nivel mensual (CRG-F1) y no se mezcla
  en este desglose diario. El selector solo lista días con actividad.</p>
  <label class="small" for="daily-spend-day">Día
    <select id="daily-spend-day">{opts}</select></label>
  <div id="daily-spend-figure"></div>
  {templates}'''


def _kinds_totals(report):
    """Totales por kind de interacción (FPA-140) sobre meses con datos."""
    kinds_total = {"user_prompt": 0, "assistant_turn": 0, "tool_call": 0}
    for mo in report["monthly"].values():
        if mo["interactions"] == 0:
            continue
        for k, n in (mo.get("interaction_kinds") or {}).items():
            kinds_total[k] = kinds_total.get(k, 0) + n
    return kinds_total


def build_data_notes(report):
    """Sección Data: definición de interacción + kinds (FPA-140), share
    filtrado (FPA-141), timezone (FPA-142) y concurrencia (FPA-120)."""
    md = report["metadata"]
    kinds_total = _kinds_totals(report)
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
from viz_fpa_common import _link  # noqa: E402

_link(globals())
