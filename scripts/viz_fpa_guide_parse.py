#!/usr/bin/env python3
"""
viz_fpa_guide_parse.py — parser y guardas de grounding de la guía
(coffe-gen.3; extraído de viz_fpa_guide.py para mantener el generador
pequeño: este módulo agrupa las etapas puras de ingesta).

Purpose: parsear docs/fpa-analyses-guide.md (español, 22 entradas /
21 análisis numerados × ELI5/Cotidiano/Practicante/Experto) con stdlib.

Responsibilities:
- load_guide(): parser del markdown → secciones/analysis/levels/IDs FPA-xxx
- check_documentation(): guardas de grounding — cada análisis del mapeo
  existe en la guía y cada umbral citado en la guía coincide con el valor
  real del config/código (lo invoca --check-docs de viz-fpa.py)

Rationale: el contenido es verdad del repo (markdown versionado); el
generador nunca copia el texto a strings propios.
"""

import re
from pathlib import Path

GUIDE_MD = Path("docs/fpa-analyses-guide.md")

NIVEL_RE = re.compile(r"^- \*\*(?P<n>[1-4]) · (?P<label>[^*]+):\*\*\s*"
                      r"(?P<rest>.*)$", flags=re.M)
HEADING_RE = re.compile(
    r"^### (?P<num>\d+(?:\s*\([ab]\))?)\. (?P<title>.+?) "
    r"\((?P<ids>[^)]+)\)\s*$", flags=re.M)

# Números canónicos de análisis para el mapeo (10a/10b del texto original).
_ANALYSIS_NUM_RE = re.compile(r"(\d+)(?:\s*\(\s*([ab])\s*\))?|^(\d+)([ab])$")

_IDS_TOKEN_RE = re.compile(r"(?:FPA-)?(\d+)(?:[–—-](\d+))?$")


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


# Mapeo declarado análisis→superficie (coffe-gen.4 lo consume para la
# marginalia). Claves canónicas del texto; superficies: las vistas del
# dashboard (coffe-8kx suma 'energy', coffe-dnx.6 suma 'models') + 'data'
# (Datos y método) + 'gantt'.
ANALYSIS_SURFACES = [
    ("1", ["summary"]),      # efectivo vs real vs apalancamiento (headline)
    ("2", ["summary"]),      # claim coste/1k vs target
    ("3", ["summary"]),      # headline costo por sesión
    ("4", ["breakdown", "outlook"]),  # Pareto/portfolio + alerta top-3
    ("5", ["cost", "models"]),  # mezcla mensual de modelos + premium share
                                # (coffe-dnx.6: también en la vista Modelos)
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
