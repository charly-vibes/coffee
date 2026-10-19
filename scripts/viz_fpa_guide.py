#!/usr/bin/env python3
"""
viz_fpa_guide.py — ingesta de la guía de análisis a 4 niveles
(coffe-gen.3/gen.4, change update-fpa-site-integration).

Purpose: parsear docs/fpa-analyses-guide.md (español, 22 entradas /
21 análisis numerados ×
ELI5/Cotidiano/Practicante/Experto) con stdlib y exponerlo al sitio.

Responsibilities:
- load_guide(): parser del markdown → secciones/analysis/levels/IDs FPA-xxx
- ANALYSIS_SURFACES: mapeo declarado análisis→superficie (5 vistas del
  dashboard + data + gantt), anclado a los números del texto de la guía
- check_documentation(): guardas de grounding — cada análisis del mapeo
  existe en la guía y cada umbral citado en la guía coincide con el valor
  real del config/código (coffe-gen.3; lo invoca --check-docs de viz-fpa.py)
- render_guide_html(): data/fpa-guide.html autocontenida en el tema
  compartido (scripts/site_theme.py), anchors por análisis
- marginalia_fragment(num): devuelve el HTML de la nota colapsable para la
  vista que la gen.4 integra

Rationale: el contenido es verdad del repo (markdown versionado, pase de
grounding en coffe-gen.1); el generador nunca copia el texto a strings
propios. Las cifras del snapshot original se reemplazaron por cifras
derivadas en generación — la guía NO participa de la verificación
README↔JSON (FPA-143) y sus cifras se etiquetan como foto del reporte.
"""

import argparse
import html as html_mod
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402

GUIDE_MD = Path("docs/fpa-analyses-guide.md")
OUT = Path("data/fpa-guide.html")
CONFIG = Path("config/fpa.json")

NIVEL_RE = re.compile(r"^- \*\*(?P<n>[1-4]) · (?P<label>[^*]+):\*\*\s*"
                      r"(?P<rest>.*)$", flags=re.M)
HEADING_RE = re.compile(
    r"^### (?P<num>\d+(?:\s*\([ab]\))?)\. (?P<title>.+?) "
    r"\((?P<ids>[^)]+)\)\s*$", flags=re.M)

# Números canónicos de análisis para el mapeo (10a/10b del texto original).
_ANALYSIS_NUM_RE = re.compile(r"(\d+)(?:\s*\(\s*([ab])\s*\))?|^(\d+)([ab])$")


def _canon_num(raw):
    """'10 (a)' / '10a' / '10' → clave canónica: '10a' | '10'."""
    raw = re.sub(r"\s+", "", raw)
    m = re.fullmatch(r"(\d+)(?:\(([ab])\))?([ab])?", raw)
    if not m:
        return None
    num, paren, suffix = m.group(1), m.group(2), m.group(3)
    return num + (paren or suffix or "")


def load_guide(text):
    """Parsear el markdown de la guía → dict de secciones/analysis.

    Estructura por análisis: num (canónico), title, ids (lista FPA-xxx),
    levels ({'1 · ELI5': texto, ...}). Falla loud si falta un nivel.
    """
    if not text or "### " not in text:
        raise SystemExit(f"ERROR: guía vacía o sin análisis: {GUIDE_MD}")
    if not list(HEADING_RE.finditer(text)):
        raise SystemExit(f"ERROR: sin análisis parseables en {GUIDE_MD}")
    return _assign_sections(text)


def _assign_sections(text):
    """Recorrer el markdown con spans sobre el texto completo y agrupar
    análisis por sección '## X.'."""
    out = []
    headings = list(re.finditer(r"^## ([A-F])\. (.+)$|^### .+$", text,
                                flags=re.M))
    for i, hm in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block = text[hm.end():end]
        if hm.group(1):  # '## X. título'
            out.append({"key": hm.group(1), "title": hm.group(2).strip(),
                        "analyses": []})
            continue
        ahm = HEADING_RE.match(hm.group(0))
        if not ahm or not out:
            continue
        num = _canon_num(ahm.group("num"))
        ids = _parse_ids(ahm.group("ids"))
        levels = _parse_analysis_block(block, ahm)
        out[-1]["analyses"].append(
            {"num": num, "title": ahm.group("title").strip(),
             "ids": ids, "ids_text": ahm.group("ids").strip(),
             "levels": levels})
    if not out:
        raise SystemExit(f"ERROR: sin secciones A–F en {GUIDE_MD}")
    return {"sections": out}


def _parse_analysis_block(block, ahm):
    """Bloque de un análisis (ya recortado por heading) → niveles."""
    levels, current = {}, None
    for line in block.splitlines():
        hm = NIVEL_RE.match(line.strip())
        if hm:
            current = f"{hm.group('n')} · {hm.group('label').strip()}"
            rest = hm.group("rest").strip()
            levels[current] = [rest] if rest else []
            continue
        if current and line.strip():
            levels[current].append(line.strip())
    if len(levels) != 4:
        raise SystemExit(f"ERROR: análisis {ahm.group('num')} tiene "
                         f"{len(levels)} niveles, se esperaban 4")
    return {k: " ".join(v) for k, v in levels.items()}


_IDS_TOKEN_RE = re.compile(r"(?:FPA-)?(\d+)(?:[–—-](\d+))?$")


def _parse_ids(raw):
    """Lista de IDs FPA desde el texto del heading, expandiendo rangos.

    'FPA-002, 030–032' → ['FPA-002', 'FPA-030', 'FPA-031', 'FPA-032'].
    Los números sin prefijo heredan el FPA- del token anterior.
    """
    out, seen = [], set()
    for token in (t.strip() for t in raw.split(",")):
        m = _IDS_TOKEN_RE.match(token)
        if not m:
            continue
        lo, hi = m.group(1), m.group(2)
        if hi is not None:
            span = [f"FPA-{i:03d}" for i in range(int(lo), int(hi) + 1)]
        else:
            span = [f"FPA-{int(lo):03d}"]
        for i in span:
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out


def analyses_flat(guide_dict):
    return [a for s in guide_dict["sections"] for a in s["analyses"]]


_MARKDOWN_RE = re.compile(r"\*\*?|`")


# ----------------------------------------------------------------------
# Cifras derivadas del reporte vigente (coffe-udt/0zp: los tokens
# {{fig:...}} del markdown se resuelven en generación — las cifras de la
# guía ya no quedan hardcodeadas ni se vuelven stale con cada regen).
# ----------------------------------------------------------------------

FIG_TOKEN_RE = re.compile(r"\{\{fig:([a-z0-9_]+)\}\}")

_MONTHS_ES = {"01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
              "05": "mayo", "06": "junio", "07": "julio", "08": "agosto",
              "09": "septiembre", "10": "octubre", "11": "noviembre",
              "12": "diciembre"}
_MONTHS_SHORT = {"01": "ene", "02": "feb", "03": "mar", "04": "abr",
                 "05": "may", "06": "jun", "07": "jul", "08": "ago",
                 "09": "sep", "10": "oct", "11": "nov", "12": "dic"}


def _money(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _pct(v, nd=1):
    return f"{v:.{nd}f}%"


def _short_proj(label):
    """charly-miblioteca → miblioteca · sk-REPLy-jl → REPLy-jl."""
    return label.split("-", 1)[1] if "-" in label else label


def _bridge_example(report):
    """Par completo más reciente con datos → (mes_label, V, M, R).

    Reproduce build_pvm/build_bridge de viz-fpa.py (formulas puras, sin
    importar el generador para no ciclar): mes parcial solo al final del
    periodo, así que el par elegido (−3, −2) está completo y sin FME."""
    months = [(ym, mo) for ym, mo in sorted(report["monthly"].items())
              if mo["interactions"] > 0]
    if len(months) < 3:
        return "n/a", "n/a", "n/a", "n/a"  # reporte mínimo: sin ejemplo
    prev_ym, prev = months[-3]
    cur_ym, cur = months[-2]

    def models(mo):
        out = {}
        for m, v in (mo.get("models") or {}).items():
            if isinstance(v, dict):
                out[m] = (v.get("interactions", 0), v.get("cost_effective", 0.0) or 0.0)
        return out

    pm, cm = models(prev), models(cur)
    q0, q1 = prev["interactions"], cur["interactions"]
    cost0, cost1 = prev["cost_effective"], cur["cost_effective"]
    by_model = []
    for m in sorted(set(pm) | set(cm)):
        qi0, c0 = pm.get(m, (0, 0.0))
        qi1, c1 = cm.get(m, (0, 0.0))
        # FPA-064: sin mes previo → rate prior = rate actual (solo Mix)
        p0 = (c0 / qi0) if qi0 else ((c1 / qi1) if qi1 else 0.0)
        p1 = (c1 / qi1) if qi1 else 0.0
        by_model.append((qi0, p0, qi1, p1))
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


def derive_figures(report, cfg):
    """Cifras citadas en la guía, derivadas del reporte vigente.

    Determinista: el mismo reporte siempre produce las mismas cifras.
    Se usa en generación (resolve_figures) y en --check (falla loud si un
    token del markdown no existe acá)."""
    md = report["metadata"]
    eff = md.get("cost_total_effective") or 0.0
    real = md.get("cost_total_real") or 0.0
    inter = md.get("total_interactions") or 0
    ses = report.get("sessions") or {}
    ses_total = ses.get("total_sessions") or 0
    dr = md.get("date_range") or {}

    # top-3 de proyectos por costo efectivo (FPA-028/036)
    projs = sorted(
        ((k, v) for k, v in report.get("projects", {}).items()
         if isinstance(v, dict)),
        key=lambda kv: -(kv[1].get("cost_effective") or 0.0))
    top3 = projs[:3]
    top3_sum = sum((v.get("cost_effective") or 0.0) for _, v in top3)
    top3_str = ", ".join(f"{_short_proj(k)} {_money(v.get('cost_effective') or 0.0)}"
                         for k, v in top3)

    # tokens y cache (FPA-043/044)
    inp = md["total_input_tokens"] or 0
    out = md["total_output_tokens"] or 0
    cache_read = md["total_cache_read_tokens"] or 0
    cache_write = md["total_cache_write_tokens"] or 0
    cache_den = inp + cache_read + cache_write
    cache_hit = 100.0 * cache_read / cache_den if cache_den else 0.0

    # multitasking / concurrencia (FPA-039/120)
    mh = (report.get("multitasking") or {}).get("hourly") or {}
    switches = (report.get("multitasking") or {}).get("context_switches") or {}
    active_hours = mh.get("total_active_hours") or 0
    swph = (switches.get("total") or 0) / active_hours if active_hours else 0.0

    # skills top-2 (FPA-113)
    skills = sorted(
        ((k, v) for k, v in (report.get("skills") or {}).items()),
        key=lambda kv: -((kv[1].get("uses") if isinstance(kv[1], dict) else kv[1]) or 0))

    # mes pico por share de interacciones (headline ejemplar, análisis 19)
    monthly = {ym: mo for ym, mo in (report.get("monthly") or {}).items()
               if mo.get("interactions")}
    if monthly and inter:
        pico_ym, pico_mo = max(monthly.items(),
                               key=lambda kv: kv[1]["interactions"])
        pico_txt = (_MONTHS_ES.get(pico_ym[5:7], pico_ym),
                    _pct(100.0 * pico_mo["interactions"] / inter))
    else:
        pico_txt = ("n/a", "n/a")

    # filtro y timezone (FPA-141/142)
    fo = report.get("filtered_out") or {}
    tz_name = md.get("timezone") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        off = _dt(2026, 1, 15, tzinfo=ZoneInfo(tz_name)).utcoffset()
    except Exception:
        off = None
    tz_txt = f"{off.total_seconds() / 3600:+03.0f}" if off is not None else tz_name

    # energía estimada (coffe-8kx): totales del periodo recomputados como
    # la vista F8 (build_energy) — suma de monthly con energía emitida.
    # FPA-008: sin provenance assumed (fallback sin config) → n/a, jamás
    # un 0.0 que parezca medición.
    if md.get("energy_provenance") == "assumed":
        en_tot = en_lo = en_hi = 0.0
        for mo in (report.get("monthly") or {}).values():
            if "energy_kwh" not in mo:
                continue  # fallback sin config (7mj): sin campos mensuales
            en_tot += mo.get("energy_kwh") or 0.0
            band = mo.get("energy_kwh_band") or {}
            en_lo += band.get("low", 0.0)
            en_hi += band.get("high", 0.0)
        en_fig = (f"{en_tot:.1f}", f"{en_lo:.1f}", f"{en_hi:.1f}")
    else:
        en_fig = ("n/a", "n/a", "n/a")

    b = cfg.get("budgets") or {}
    bridge_label, bridge_v, bridge_m, bridge_r = _bridge_example(report)
    fo_share = 100.0 * (fo.get("share") or 0.0)
    return {
        "periodo": f"{dr.get('start')} → {dr.get('end')}",
        "filtro": md.get("filter") or "in-scope",
        "interacciones_total": f"{inter:,}",
        "proyectos_total": str(md.get("total_projects") or len(projs)),
        "efectivo_total": _money(eff),
        "cash_real_total": _money(real),
        "apalancamiento": (f"{eff / real:.1f}×" if real else "n/a"),
        "per_1k_efectivo": _money(eff / inter * 1000) if inter else "n/a",
        "per_1k_real": _money(real / inter * 1000) if inter else "n/a",
        "target_per_1k": _money((b.get("target_per_1k") or 0.0)),
        "por_sesion": _money(eff / ses_total) if ses_total else "n/a",
        "sesiones_total": f"{ses_total:,}",
        "sesiones_agent": str(ses.get("with_agent") or 0),
        "agent_share": _pct(100.0 * (ses.get("with_agent") or 0) / ses_total
                            if ses_total else 0.0),
        "top3_nombres_cifras": top3_str,
        "top3_share": _pct(100.0 * top3_sum / eff) if eff else "n/a",
        "cache_hit": _pct(cache_hit),
        "tokens_out_m": f"{out / 1e6:,.1f}M",
        "tokens_in_m": f"{inp / 1e6:,.0f}M",
        "ratio_out_in": f"{out / inp:.2f}" if inp else "n/a",
        "multitask_pct": _pct(mh.get("pct_hours_multitasking") or 0.0),
        "horas_activas": f"{active_hours:,}",
        "avg_proj_hora": f"{mh.get('avg_projects_per_active_hour') or 0:.2f}",
        "pico_proyectos": str((mh.get("max_projects_in_one_hour") or {}).get("count") or 0),
        "switches_hora": f"{swph:.2f}/h",
        "skills_top1": (f"{skills[0][0]} {skills[0][1]['uses'] if isinstance(skills[0][1], dict) else skills[0][1]}"
                        if skills else "n/a"),
        "skills_top2": (f"{skills[1][0]} {skills[1][1]['uses'] if isinstance(skills[1][1], dict) else skills[1][1]}"
                        if len(skills) > 1 else "n/a"),
        "mes_pico": pico_txt[0],
        "mes_pico_share": pico_txt[1],
        "filtro_count": str(fo.get("interactions") or 0),
        "filtro_share": _pct(fo_share, 2),
        "timezone": tz_txt,
        "budget_cash": _money(b.get("cash_monthly") or 0.0),
        "budget_start": b.get("start_month") or "n/a",
        "bridge_mes": bridge_label,
        "bridge_v": bridge_v,
        "bridge_m": bridge_m,
        "bridge_r": bridge_r,
        # coffe-8kx: energía estimada (assumed) para el análisis 20
        "energia_total": en_fig[0],
        "energia_low": en_fig[1],
        "energia_high": en_fig[2],
        "cache_factor": str(md.get("energy_cache_read_factor") or "n/a"),
    }


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


def _plain(text):
    """Markdown básico → texto plano (para tooltip y notas)."""
    return html_mod.escape(_MARKDOWN_RE.sub("", text))


NIVEL_TEASER = "1 · ELI5"
NIVEL_COTIDIANO = "2 · Cotidiano"
NIVEL_PRACTICANTE = "3 · Practicante"


def _mchip(a, figures=None):
    """Un chip de marginalia: <details> nativo con tooltip ELI5 (title),
    nivel 2 visible al expandir, nivel 3 colapsado dentro y enlace a la
    guía completa (FPA coffe-gen.4). Con `figures`, resuelve {{fig:*}}
    del reporte vigente (coffe-8kx)."""
    if figures is not None:
        a = dict(a)
        a["levels"] = {
            lvl: FIG_TOKEN_RE.sub(
                lambda m: figures.get(m.group(1), m.group(0)), text)
            for lvl, text in a["levels"].items()}
    teaser = _plain(a["levels"][NIVEL_TEASER])
    daily = _plain(a["levels"][NIVEL_COTIDIANO])
    prac = _plain(a["levels"][NIVEL_PRACTICANTE])
    anchor = _anchor(a["num"])
    return (
        f'<details class="mchip" title="{teaser}">'
        f'<summary>¿Qué es esto? · {html_mod.escape(a["num"])}</summary>'
        f'<div class="mnote">'
        f'<div class="mlevel">{daily}</div>'
        f'<details class="mchip-deep">'
        f'<summary>Cómo se computa</summary>'
        f'<div class="mlevel">{prac}</div>'
        f'</details>'
        f'<div class="mlevel"><a href="fpa-guide.html#{anchor}">'
        f'Guía completa → análisis {html_mod.escape(a["num"])}</a></div>'
        f'</div>'
        f'</details>')


def marginalia_html(guide_dict, cfg, surface, figures=None):
    """Strip de marginalia para una superficie (6 vistas + data + gantt):
    chips por análisis mapeado, con tooltip del teaser ELI5.

    coffe-8kx: con `figures` (derivadas del reporte vigente), los tokens
    {{fig:*}} del texto se resuelven también en los chips — si no, el chip
    de energía citaría 0.0 kWh en el fallback sin config (FPA-008: nunca
    un cero que parezca medición)."""
    by_num = {a["num"]: a for a in analyses_flat(guide_dict)}
    chips = []
    for num, surfaces in ANALYSIS_SURFACES:
        if surface in surfaces and num in by_num:
            chips.append(_mchip(by_num[num], figures))
    if not chips:
        return ""
    label = ('¿Qué es esto? Los análisis que esta sección mira '
             '(tooltip = versión simple; al abrir, la explicación):')
    return (f'<div class="marginalia" id="m-{html_mod.escape(surface)}">'
            f'<span class="mlabel">{html_mod.escape(label)}</span>'
            f'{"".join(chips)}</div>')


# Mapeo declarado análisis→superficie (coffe-gen.4 lo consume para la
# marginalia). Claves canónicas del texto; superficies: las 6 vistas del
# dashboard (coffe-8kx suma 'energy') + 'data' (Datos y método) + 'gantt'.
ANALYSIS_SURFACES = [
    ("1", ["summary"]),      # efectivo vs real vs apalancamiento (headline)
    ("2", ["summary"]),      # claim coste/1k vs target
    ("3", ["summary"]),      # headline costo por sesión
    ("4", ["breakdown", "outlook"]),  # Pareto/portfolio + alerta top-3
    ("5", ["cost"]),         # mezcla mensual de modelos + premium share
    ("6", ["cost"]),         # KPIs cache-hit rate y out/in
    ("7", ["habits"]),       # share de sesiones con Agent
    ("8", ["habits", "gantt"]),  # multitasking (3 medidas) + concurrencia
    ("9", ["habits", "gantt"]),  # heatmap/after-hours + ritmo semanal
    ("10a", ["habits"]),     # buckets de sesión + /clear
    ("10b", ["habits"]),     # skills/commands
    ("11", ["breakdown"]),   # lifecycle new/active/dormant
    ("12", ["outlook"]),     # outcomes: costo por commit/release
    ("13", ["outlook"]),     # presupuesto/varianza + alertas
    ("14", ["outlook"]),     # bridge PVM
    ("15", ["outlook"]),     # forecast/escenarios
    ("16", ["outlook"]),     # economía de suscripción
    ("17", ["breakdown"]),   # árboles con roll-up
    ("18", ["data"]),        # calidad de datos
    ("19", ["data"]),        # interfaz y CTAs
    ("20", ["energy"]),      # energía estimada y banda de caché (coffe-8kx)
    ("21", ["ddl"]),         # efectividad del toolset ddl (coffe-6io;
                             # surfacea al dashboard ddl, no al FPA)
]

# Umbrales citados en la guía ↔ valores reales (config o defaults del
# código). La clave es (ruta, etiqueta humana); el valor efectivo se lee
# del config con su default de código (check_documentation).
CITED_THRESHOLDS = [
    (("alert_thresholds", "plan_usage_multiple"), "25x verify-plan"),
    (("alert_thresholds", "reconcile_tolerance_pct"), "10% reconciliación"),
    (("alert_thresholds", "unit_cost_rise_pct"), "15% costo unitario"),
    (("alert_thresholds", "premium_share_rise_pts"), "5 pts premium"),
    (("alert_thresholds", "concentration_top3_pct"), "50% top-3"),
    (("alert_thresholds", "staleness_days"), "14 días staleness"),
    (("lifecycle", "dormant_days"), "30 días dormant"),
    (("lifecycle", "new_days"), "30 días new"),
    (("sessions", "long_turns"), "100 turns larga"),
    (("timeline", "gap_days"), "7 días gap"),
]

# Defaults de código para claves ausentes en el config (igual que hace
# viz-fpa.py con _cfg_int).
CODE_DEFAULTS = {
    ("sessions", "long_turns"): 100,
    ("timeline", "gap_days"): 7,
}


def _effective_threshold(cfg, path):
    section, key = path
    default = CODE_DEFAULTS.get(path)
    val = (cfg.get(section) or {}).get(key, default)
    return val


def check_documentation(cfg, guide_text, mapping_override=None):
    """Guardas de grounding. Devuelve lista de errores ([] = OK).

    1. cada análisis del mapeo existe en la guía
    2. cada umbral CITED_THRESHOLDS: su valor efectivo (config o default
       de código) aparece citado como número en el texto de la guía — si
       el config cambia, la guía debe re-groundearse o el build falla
    """
    errors = []
    nums = {a["num"]
            for s in _assign_sections(guide_text)["sections"]
            for a in s["analyses"]}
    mapping = mapping_override if mapping_override is not None else (
        ANALYSIS_SURFACES)
    for num, surfaces in mapping:
        if num not in nums:
            errors.append(f"mapping: análisis {num} no existe en la guía "
                          f"(superficies: {','.join(surfaces)})")
    for path, label in CITED_THRESHOLDS:
        val = _effective_threshold(cfg, path)
        if val is None:
            errors.append(f"umbral {label}: sin valor en config ni default")
            continue
        # citar los enteros como tales; tolerar coma decimal
        if not re.search(rf"(?<![\d.,]){re.escape(str(val))}(?![\d.,])",
                         guide_text):
            errors.append(
                f"umbral {label}: el valor real {val} "
                f"(config {path[0]}.{path[1]}) no aparece citado en la guía —"
                f" re-groundear docs/fpa-analyses-guide.md")
    return errors


CSS = site_theme.base_css() + """
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  line-height: 1.5; font-family: var(--font-stack); }
main { max-width: 900px; margin: 0 auto; padding: 0 1rem 2rem; }
header.site, footer.site { border-bottom: 1px solid var(--line);
  max-width: 900px; margin: 0 auto; padding: .8rem 1rem; }
footer.site { border: 0; border-top: 1px solid var(--line); }
header.site h1 { margin: 0; font-size: 1.35rem; }
.meta, .small { color: var(--muted); font-size: .82rem; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: .5rem; top: .5rem; background: var(--card);
  padding: .4rem; border: 1px solid var(--line); }
h2 { font-size: 15px; color: var(--navy); margin: 26px 0 8px;
  text-transform: uppercase; letter-spacing: .06em;
  border-bottom: 3px double var(--navy); padding-bottom: 3px; }
.analysis { background: var(--card); border: 2px outset #fff;
  box-shadow: 3px 3px 0 #404040; padding: .8rem 1rem; margin: 0 0 1rem; }
.analysis h3 { margin: 0 0 .4rem; font-size: .95rem; }
.analysis h3 .fpa { font-size: .7rem; color: var(--muted); font-weight: 400; }
.level { margin: .45rem 0; padding-left: .6rem; border-left: 4px double
  var(--navy); }
.level b { color: var(--accent2); }
.volver { font-size: .78rem; }
nav.toc { background: var(--card); border: 2px outset #fff;
  padding: .7rem 1rem; margin: 1rem 0; }
nav.toc a { color: var(--navy); text-decoration: none; }
"""


def _anchor(num):
    return f"analisis-{html_mod.escape(num)}"


def render_guide_html(guide_dict, cfg, generated=None):
    """HTML autocontenido de la guía completa (determinista)."""
    generated = generated or datetime.now().strftime("%Y-%m-%d %H:%M")
    site_name = cfg.get("site_name", "Uso y costos de IA")
    n_analyses = len(analyses_flat(guide_dict))  # coffe-udt: 10a/10b → 20
    toc = []
    sections_html = []
    for s in guide_dict["sections"]:
        items = []
        for a in s["analyses"]:
            anchor = _anchor(a["num"])
            items.append(f'<a href="#{anchor}">{a["num"]}</a>')
            levels = "".join(
                f'<div class="level"><b>{html_mod.escape(k)}</b> '
                f'{html_mod.escape(v)}</div>'
                for k, v in a["levels"].items())
            ids_text = html_mod.escape(a.get("ids_text", ""))
            sections_html.append(
                f'<div class="analysis" id="{anchor}">'
                f'<h3>{html_mod.escape(a["num"])}. '
                f'{html_mod.escape(a["title"])} '
                f'<span class="fpa">{ids_text}</span></h3>'
                f'{levels}'
                f'<p class="small volver"><a href="#toc">↑ índice</a></p>'
                f'</div>')
        toc.append(f'<div><b>{s["key"]}.</b> {html_mod.escape(s["title"])} '
                   f'{"".join(items)}</div>')
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>Guía de análisis — {html_mod.escape(site_name)}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#toc">Saltar al índice</a>
<header class="site">
  <h1>Guía de análisis — {html_mod.escape(site_name)}</h1>
  <p class="meta">Generado: {generated} · fuente: docs/fpa-analyses-guide.md
  ({n_analyses} análisis × 4 niveles)</p>
</header>
<main>
<nav class="toc" id="toc">
  {"".join(toc)}
</nav>
{"".join(sections_html)}
<footer class="site">
  <p class="small">Guía de análisis · generado por viz_fpa_guide.py
  (stdlib-only) · las cifras citadas provienen del reporte vigente y son una
  foto del momento de la traducción — el dashboard calcula las cifras vivas
  en cada corrida.</p>
</footer>
</main>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(
        description="Guía de análisis a 4 niveles (viz_fpa_guide.py)")
    ap.add_argument("--check", action="store_true",
                    help="solo validar grounding (mapeo + umbrales vs config)")
    ap.add_argument("--report", default=str(Path("data/usage_report_v3.json")))
    args = ap.parse_args()
    text = GUIDE_MD.read_text(encoding="utf-8")
    cfg = json.loads(CONFIG.read_text())
    errors = check_documentation(cfg, text)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    # coffe-udt/0zp: los tokens {{fig:...}} se resuelven contra el reporte
    # vigente acá y en --check (falla loud si un token no existe).
    report = json.loads(Path(args.report).read_text())
    text = resolve_figures(text, report, cfg)
    if args.check:
        print("OK: guía con grounding verificado (mapeo + umbrales + cifras "
              "derivadas del reporte vigente)")
        return
    guide_dict = load_guide(text)
    OUT.write_text(render_guide_html(guide_dict, cfg), encoding="utf-8")
    print(f"OK → {OUT} ({len(analyses_flat(guide_dict))} análisis, cifras "
          f"derivadas del reporte)")


if __name__ == "__main__":
    main()
