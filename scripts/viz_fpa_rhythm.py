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




def _hourly_shares(report, wh):
    """FPA-111: acumula total, weekend y after-hours desde hourly."""
    total = after = weekend = 0
    for ts, h in report.get("hourly", {}).items():
        dow = date.fromisoformat(ts[:10]).isoweekday() - 1
        n = h.get("interactions", 0)
        total += n
        weekend += n if dow >= 5 else 0
        if not _hour_in_working_hours(dow, int(ts[11:13]), wh):
            after += n
    return total, after, weekend


def _apply_wow(weeks):
    """WoW solo entre semanas consecutivas completas (muta `weeks`)."""
    for i in range(1, len(weeks)):
        prev, w = weeks[i - 1], weeks[i]
        if w["partial"] or prev["partial"]:
            continue
        if prev["interactions"]:
            w["wow_interactions"] = (w["interactions"]
                                     - prev["interactions"]) / \
                prev["interactions"]
        if prev["cost_effective"]:
            w["wow_cost"] = (w["cost_effective"]
                             - prev["cost_effective"]) / \
                prev["cost_effective"]


def build_rhythm(report, cfg):
    """FPA-111: shares after-hours (fuera del horario laboral del config)
    y weekend, desde hourly. FPA-112: serie semanal ISO con WoW y varianza
    poblacional sobre semanas completas (los bordes de cobertura van
    marcados partial y se excluyen)."""
    wh = cfg.get("working_hours", {})
    total, after, weekend = _hourly_shares(report, wh)
    weeks, _ = _build_weeks(report)
    _apply_wow(weeks)
    full = [w for w in weeks if not w["partial"]]
    var_i = _pop_variance([w["interactions"] for w in full])
    var_c = _pop_variance([w["cost_effective"] for w in full])
    reason = (f"se requieren 3+ semanas completas de datos; hay "
              f"{len(full)} (FPA-008)") if None in (var_i, var_c) else None
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
from viz_fpa_common import _link  # noqa: E402

_link(globals())
