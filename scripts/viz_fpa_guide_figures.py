#!/usr/bin/env python3
"""
viz_fpa_guide_figures.py — cifras derivadas del reporte vigente
(coffe-udt/0zp; extraído de viz_fpa_guide.py).

Purpose: derivar del reporte las cifras que la guía cita como tokens
{{fig:...}} (el HTML de marginalia que las consume queda en el generador).
- derive_figures(): cifras del reporte → dict determinista
- resolve_figures(): sustituye los tokens {{fig:...}} del markdown
"""

import re

FIG_TOKEN_RE = re.compile(r"\{\{fig:([a-z0-9_]+)\}\}")
_MARKDOWN_RE = re.compile(r"\*\*?|`")

_MES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
        "agosto", "septiembre", "octubre", "noviembre", "diciembre")
_MONTHS_ES = {f"{i + 1:02d}": m for i, m in enumerate(_MES)}
_MONTHS_SHORT = {k: m[:3] for k, m in _MONTHS_ES.items()}

def _money(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _pct(v, nd=1):
    return f"{v:.{nd}f}%"


def _short_proj(label):
    return label.split("-", 1)[1] if "-" in label else label


def _model_series(prev, cur):
    # Serie (q_prev, rate_prev, q_cur, rate_cur) para el bridge.
    # FPA-064: sin mes previo → rate prior = rate actual (solo Mix)
    def models(mo):
        out = {}
        for m, v in (mo.get("models") or {}).items():
            if isinstance(v, dict):
                out[m] = (v.get("interactions", 0), v.get("cost_effective", 0.0) or 0.0)
        return out

    pm, cm = models(prev), models(cur)
    series = []
    for m in sorted(set(pm) | set(cm)):
        qi0, c0 = pm.get(m, (0, 0.0))
        qi1, c1 = cm.get(m, (0, 0.0))
        p0 = (c0 / qi0) if qi0 else ((c1 / qi1) if qi1 else 0.0)
        p1 = (c1 / qi1) if qi1 else 0.0
        series.append((qi0, p0, qi1, p1))
    return series


def _bridge_example(report):
    """Par completo más reciente con datos → (mes_label, V, M, R).
    Reproduce build_pvm/build_bridge de viz-fpa.py (formulas puras, sin
    importar el generador para no ciclar): el par elegido (−3, −2) está
    completo y sin FME."""
    months = [(ym, mo) for ym, mo in sorted(report["monthly"].items())
              if mo["interactions"] > 0]
    if len(months) < 3:
        return "n/a", "n/a", "n/a", "n/a"  # reporte mínimo: sin ejemplo
    prev_ym, prev = months[-3]
    cur_ym, cur = months[-2]
    by_model = _model_series(prev, cur)
    q0, q1 = prev["interactions"], cur["interactions"]
    cost0, cost1 = prev["cost_effective"], cur["cost_effective"]
    rate0 = (sum(q * p for q, p, _, _ in by_model) / q0) if q0 else 0.0
    volume = (q1 - q0) * rate0
    mix = sum(q1 * p0 for _, p0, q1, _ in by_model) - q1 * rate0
    rate = sum(q1 * (p1 - p0) for _, p0, q1, p1 in by_model)
    delta = cost1 - cost0
    residual = volume + mix + rate - delta
    mix -= residual  # FPA-065
    if abs(volume + mix + rate - delta) > 0.01:
        raise SystemExit("ERROR: identidad del bridge de la guía rota (FPA-065)")
    label = (f"{_MONTHS_SHORT[cur_ym[5:7]]}-{cur_ym[2:4]}")
    return label, _money(volume), _money(mix), _money(rate)


def _top3_figs(report, eff):
    # Top-3 de proyectos por costo efectivo (FPA-028/036)
    projs = sorted(
        ((k, v) for k, v in report.get("projects", {}).items()
         if isinstance(v, dict)),
        key=lambda kv: -(kv[1].get("cost_effective") or 0.0))
    top3 = projs[:3]
    top3_sum = sum((v.get("cost_effective") or 0.0) for _, v in top3)
    top3_str = ", ".join(f"{_short_proj(k)} {_money(v.get('cost_effective') or 0.0)}"
                         for k, v in top3)
    share = _pct(100.0 * top3_sum / eff) if eff else "n/a"
    return projs, top3_str, share


def _token_figs(md):
    # Tokens y cache (FPA-043/044)
    inp = md["total_input_tokens"] or 0
    out = md["total_output_tokens"] or 0
    cache_read = md["total_cache_read_tokens"] or 0
    cache_write = md["total_cache_write_tokens"] or 0
    cache_den = inp + cache_read + cache_write
    cache_hit = 100.0 * cache_read / cache_den if cache_den else 0.0
    return {
        "cache_hit": _pct(cache_hit),
        "tokens_out_m": f"{out / 1e6:,.1f}M",
        "tokens_in_m": f"{inp / 1e6:,.0f}M",
        "ratio_out_in": f"{out / inp:.2f}" if inp else "n/a",
    }


def _multitask_hours(report):
    """Horas activas y switches/hora del bloque multitasking."""
    mt = report.get("multitasking") or {}
    mh = mt.get("hourly") or {}
    switches = mt.get("context_switches") or {}
    active_hours = mh.get("total_active_hours") or 0
    total_sw = switches.get("total") or 0
    swph = total_sw / active_hours if active_hours else 0.0
    return mh, active_hours, swph


def _multitask_figs(report):
    """Multitasking / concurrencia (FPA-039/120)."""
    mh, active_hours, swph = _multitask_hours(report)
    pico = mh.get("max_projects_in_one_hour") or {}
    return {
        "multitask_pct": _pct(mh.get("pct_hours_multitasking") or 0.0),
        "horas_activas": f"{active_hours:,}",
        "avg_proj_hora": f"{mh.get('avg_projects_per_active_hour') or 0:.2f}",
        "pico_proyectos": str(pico.get("count") or 0),
        "switches_hora": f"{swph:.2f}/h",
    }


def _skill_fig(skills, i):
    if len(skills) <= i:
        return "n/a"
    name, val = skills[i]
    uses = val["uses"] if isinstance(val, dict) else val
    return f"{name} {uses}"


def _pico_fig(report, inter):
    # Mes pico por share de interacciones (análisis 19)
    monthly = {ym: mo for ym, mo in (report.get("monthly") or {}).items()
               if mo.get("interactions")}
    if monthly and inter:
        pico_ym, pico_mo = max(monthly.items(),
                               key=lambda kv: kv[1]["interactions"])
        return (_MONTHS_ES.get(pico_ym[5:7], pico_ym),
                _pct(100.0 * pico_mo["interactions"] / inter))
    return ("n/a", "n/a")


def _tz_fig(md):
    # Filtro y timezone (FPA-141/142)
    tz_name = md.get("timezone") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        off = _dt(2026, 1, 15, tzinfo=ZoneInfo(tz_name)).utcoffset()
    except Exception:
        off = None
    return f"{off.total_seconds() / 3600:+03.0f}" if off is not None else tz_name


def _energy_fig(report, md):
    # Energía (coffe-8kx) recomputada como la vista F8; FPA-008:
    # fallback sin config → n/a, jamás un 0.0 que parezca medición.
    if md.get("energy_provenance") != "assumed":
        return ("n/a", "n/a", "n/a")
    en_tot = en_lo = en_hi = 0.0
    for mo in (report.get("monthly") or {}).values():
        if "energy_kwh" not in mo:
            continue  # fallback sin config: sin campos mensuales
        en_tot += mo.get("energy_kwh") or 0.0
        band = mo.get("energy_kwh_band") or {}
        en_lo += band.get("low", 0.0)
        en_hi += band.get("high", 0.0)
    return (f"{en_tot:.1f}", f"{en_lo:.1f}", f"{en_hi:.1f}")


def _period_figs(md, projs):
    """Periodo, filtro y conteos del headline."""
    inter = md.get("total_interactions") or 0
    dr = md.get("date_range") or {}
    return {
        "periodo": f"{dr.get('start')} → {dr.get('end')}",
        "filtro": md.get("filter") or "in-scope",
        "interacciones_total": f"{inter:,}",
        "proyectos_total": str(md.get("total_projects") or len(projs)),
    }


def _headline_money_figs(md):
    """Costo total, apalancamiento y costo por 1k (FPA-001/035)."""
    eff = md.get("cost_total_effective") or 0.0
    real = md.get("cost_total_real") or 0.0
    inter = md.get("total_interactions") or 0
    return {
        "efectivo_total": _money(eff),
        "cash_real_total": _money(real),
        "apalancamiento": f"{eff / real:.1f}×" if real else "n/a",
        "per_1k_efectivo": _money(eff / inter * 1000) if inter else "n/a",
        "per_1k_real": _money(real / inter * 1000) if inter else "n/a",
    }


def _session_figs(ses, eff):
    """Sesiones del periodo y costo por sesión."""
    ses_total = ses.get("total_sessions") or 0
    with_agent = ses.get("with_agent") or 0
    return {
        "por_sesion": _money(eff / ses_total) if ses_total else "n/a",
        "sesiones_total": f"{ses_total:,}",
        "sesiones_agent": str(with_agent),
        "agent_share": _pct(100.0 * with_agent / ses_total
                            if ses_total else 0.0),
    }


def _budget_figs(b):
    """Presupuesto citado en la guía."""
    return {
        "target_per_1k": _money(b.get("target_per_1k") or 0.0),
        "budget_cash": _money(b.get("cash_monthly") or 0.0),
        "budget_start": b.get("start_month") or "n/a",
    }


def _cost_figs(md, ses, b, projs):
    """Cifras de costo/headline del periodo."""
    eff = md.get("cost_total_effective") or 0.0
    figs = _period_figs(md, projs)
    figs.update(_headline_money_figs(md))
    figs.update(_session_figs(ses, eff))
    figs.update(_budget_figs(b))
    return figs


def derive_figures(report, cfg):
    """Cifras citadas en la guía, derivadas del reporte vigente.
    Determinista; se usa en generación (resolve_figures) y en --check."""
    md = report["metadata"]
    ses = report.get("sessions") or {}
    fo = report.get("filtered_out") or {}
    projs, top3_str, top3_share = _top3_figs(
        report, md.get("cost_total_effective") or 0.0)
    pico_txt = _pico_fig(report, md.get("total_interactions") or 0)
    b = cfg.get("budgets") or {}
    bridge = _bridge_example(report)
    en_fig = _energy_fig(report, md)
    skills = _sorted_skills(report)
    figs = _cost_figs(md, ses, b, projs)
    figs.update(_token_figs(md))
    figs.update(_multitask_figs(report))
    figs.update({
        "top3_nombres_cifras": top3_str,
        "top3_share": top3_share,
        "skills_top1": _skill_fig(skills, 0),
        "skills_top2": _skill_fig(skills, 1),
        "mes_pico": pico_txt[0],
        "mes_pico_share": pico_txt[1],
        "filtro_count": str(fo.get("interactions") or 0),
        "filtro_share": _pct(100.0 * (fo.get("share") or 0.0), 2),
        "timezone": _tz_fig(md),
        "bridge_mes": bridge[0],
        "bridge_v": bridge[1],
        "bridge_m": bridge[2],
        "bridge_r": bridge[3],
        "energia_total": en_fig[0],
        "energia_low": en_fig[1],
        "energia_high": en_fig[2],
        "cache_factor": str(md.get("energy_cache_read_factor") or "n/a"),
    })
    return figs


def _sorted_skills(report):
    # Skills top-2 (FPA-113)
    return sorted(
        ((k, v) for k, v in (report.get("skills") or {}).items()),
        key=lambda kv: -((kv[1].get("uses") if isinstance(kv[1], dict) else kv[1]) or 0))


def resolve_figures(text, report, cfg):
    """Resolver los tokens {{fig:...}} del markdown con derive_figures.
    Token desconocido → falla loud (SystemExit): el build nunca publica
    una cifra que no venga del reporte."""
    figs = derive_figures(report, cfg)

    def sub(m):
        k = m.group(1)
        if k not in figs:
            raise SystemExit(f"ERROR: figura desconocida {{{{fig:{k}}}}} "
                             f"en la guía — agregála a derive_figures")
        return figs[k]

    return FIG_TOKEN_RE.sub(sub, text)
