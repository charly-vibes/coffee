"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Índice de beads (coffe-dnx.4) — fuera de repo_metrics(): el panel de repos
queda git-only; esto escanea .beads/issues.jsonl de cada repo bajo
agent_behavior.repo_roots (bd corre no-db: el JSONL es la fuente de verdad).
Agregado por repo × mes, sin texto de issues: ids y conteos solamente.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_metrics_common import ROOT, _bead_rel_name, _dt, _iter_jsonl, _pctl

BEAD_OUT = ROOT / "data" / "bead_index.json"
BEAD_SCHEMA = ROOT / "specs" / "bead-index.schema.json"
TASK_BEADS_OUT = ROOT / "data" / "task_beads.json"
TASK_BEADS_SCHEMA = ROOT / "specs" / "task-beads.schema.json"
BEAD_TEXT_KEYS = {"title", "description", "notes", "design"}
# FPA-141: los mismos tres orgs del filtro charly del reporte de uso
# (usage_tracker.in_scope). Copia local para no acoplar el import: los nombres
# de repo acá son `<org>/<repo>` relativos a la raíz, así que basta el primer
# segmento.
BEAD_ORGS = ("charly", "sk", "ak")


def _bead_in_scope(rel_name):
    """Filtro FPA-141 sobre `<org>/<repo>`: primer segmento = org."""
    org = rel_name.lower().split("/", 1)[0]
    return org in BEAD_ORGS


def _month_row(months, key):
    return months.setdefault(key, {"created": 0, "started": 0, "closed": 0,
                                   "lead_time_p50_min": None,
                                   "lead_time_n": 0, "completeness": None,
                                   "reopened": None, "_lead": []})


def _count_bead(row, it):
    """Cuenta un issue en su cohorte: created, started, closed y lead time
    (closed − started, en minutos, solo con ambos)."""
    row["created"] += 1
    started = _dt(it.get("started_at"))
    closed = _dt(it.get("closed_at"))
    if started:
        row["started"] += 1
    if closed:
        row["closed"] += 1
        if started:
            row["lead_time_n"] += 1
            row["_lead"].append((closed - started).total_seconds() / 60.0)


def _fold_lead(row):
    """Cierra la fila del mes: p50 de lead time y completeness."""
    lead = sorted(row.pop("_lead"))
    if lead:
        row["lead_time_p50_min"] = round(_pctl(lead, 0.5), 1)
    row["completeness"] = (round(row["lead_time_n"] / row["closed"], 4)
                           if row["closed"] else None)


def _bead_month_rows(issues):
    """Issues de un repo → filas por mes (mes de `created_at`: la cohorte de
    beads creados ese mes; started/closed/lead_time se cuentan dentro de esa
    cohorte, así created ≤ started, closed es consistente y completeness =
    lead_time_n / closed compara sobre la misma población)."""
    months = {}
    for it in issues:
        if not isinstance(it, dict):
            continue
        created = _dt(it.get("created_at"))
        if created is None:
            continue
        _count_bead(_month_row(months, created.strftime("%Y-%m")), it)
    for row in months.values():
        _fold_lead(row)
    return {k: months[k] for k in sorted(months)}


def bead_privacy_errors(doc):
    """Guard de privacidad (CLRT-003): title/description/notes/design de
    issues son una clase de datos nueva no cubierta por el resto del sitio
    (EDGE-002). Los ids quedan permitidos (opacos). Recorre todo el doc."""
    errors = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in BEAD_TEXT_KEYS:
                    errors.append(f"clave de texto de issue en {path}.{k}")
                else:
                    walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(doc, "$")
    return errors


def build_bead_index(repo_paths, prefixes=None, now=None):
    """Repos bajo las raíces → doc de data/bead_index.json.

    Per repo: filas por mes {created, started, closed, lead_time_p50_min,
    lead_time_n, completeness, reopened}. reopened queda `null` con razón en
    metadata: el JSONL es una foto de status sin historial de transiciones,
    y un 0 afirmaría un hecho que no se midió (CLRT-001). Repos fuera del
    filtro charly van a `excluded[]` con conteo de issues, sin texto.
    """
    prefixes = prefixes or ["~/para/areas/dev/gh"]
    repos, excluded = [], []
    for path in repo_paths:
        name = _bead_rel_name(path, prefixes)
        jsonl = Path(path) / ".beads" / "issues.jsonl"
        if not jsonl.exists():
            continue
        issues = list(_iter_jsonl(jsonl))
        if not _bead_in_scope(name):
            excluded.append({"name": name, "issues": len(issues)})
            continue
        repos.append({"name": name, "months": _bead_month_rows(issues)})
    repos.sort(key=lambda r: r["name"])
    excluded.sort(key=lambda e: e["name"])
    return {
        "metadata": {
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "repo_roots": prefixes,
            "definition": (
                "por repo × mes (mes de created_at): created = beads "
                "creados; started/closed = con started_at/closed_at dentro "
                "de la cohorte; lead_time = closed_at − started_at en "
                "minutos para beads con ambos, p50 exacto; completeness = "
                "lead_time_n / closed. Fuente: .beads/issues.jsonl de cada "
                "repo (bd no-db)."),
            "reasons": {
                "reopened": "no event log in no-db mode",
                "excluded": "repos fuera del filtro charly (FPA-141): "
                            "conteo de issues, sin texto",
            },
        },
        "repos": repos,
        "excluded": excluded,
    }


def _bead_schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(
        doc, json.loads(BEAD_SCHEMA.read_text(encoding="utf-8")))
