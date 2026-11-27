"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Atribución tarea↔bead (coffe-dnx.5): explicit (id en el prompt) > git (rama
o commit menciona el id) > temporal (ventana [started_at, closed_at] del
bead, mismo repo). Confianza: explicit 1.0, git 0.8, temporal 0.5 (techo).
Las filas temporal NO suman coste.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_metrics_beads import TASK_BEADS_SCHEMA, _bead_in_scope
from agent_metrics_common import ROOT, _bead_rel_name, _git, _dt, _iter_jsonl, _pctl
from agent_metrics_aggregate import _sessions_from_tasks

TASK_BEAD_CONFIDENCE = {"explicit": 1.0, "git": 0.8, "temporal": 0.5}
# tokens con guión (coffe-abc, coffe-dnx.5); case-insensitive. Sin NLP: el
# matcheo es literal y solo atribuye si el token existe en el índice de
# beads (by_id), así que el patrón amplio no inventa atribuciones.
BEAD_ID_RE = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.\d+)?)\b",
                        re.IGNORECASE)
def load_bead_records(repo_paths, prefixes=None):
    """Repos → registros de bead {id, repo, path, started_at, closed_at}
    desde .beads/issues.jsonl (misma fuente y mismo filtro de scope que
    build_bead_index). started_at/closed_at parseados a datetime UTC."""
    beads = []
    for path in repo_paths:
        name = _bead_rel_name(path, prefixes)
        jsonl = Path(path) / ".beads" / "issues.jsonl"
        if not jsonl.exists() or not _bead_in_scope(name):
            continue
        for it in _iter_jsonl(jsonl):
            if not isinstance(it, dict) or not it.get("id"):
                continue
            beads.append({"id": str(it["id"]), "repo": name, "path": Path(path),
                          "started_at": _dt(it.get("started_at")),
                          "closed_at": _dt(it.get("closed_at"))})
    return beads


def _git_mentioned_beads(repo_path, run=_git):
    """Ids de beads mencionados en el git del repo: subjects de commits
    (--all, último año) y nombres de rama. Un solo par de llamadas por repo."""
    ids = set()
    for args in (["log", "--all", "--since=1.year", "--format=%s"],
                 ["branch", "--all", "--format=%(refname:short)"]):
        out = run(args, repo_path)
        for line in (out or "").splitlines():
            for m in BEAD_ID_RE.finditer(line):
                ids.add(m.group(1).lower())
    return ids


def _session_repo(session, beads):
    """Repo de la sesión: proyecto (última componente del cwd) matcheado
    contra `<org>/<repo>` de los beads. None si no hay match único."""
    project = (session or {}).get("project")
    if not project:
        return None
    repos = {b["repo"] for b in beads
             if b["repo"] == project or b["repo"].endswith("/" + project)}
    return next(iter(repos)) if len(repos) == 1 else None


def _explicit_beads(t, by_id):
    """explicit: ids del prompt presentes en el índice de beads."""
    found = []
    for m in BEAD_ID_RE.finditer(t.get("prompt") or ""):
        b = by_id.get(m.group(1).lower())
        if b:
            found.append((b, "explicit"))
    return found


def _git_bead_ids(beads, repo, git_cache, run):
    """Ids de beads que el git del repo menciona, con caché por repo."""
    if repo not in git_cache:
        git_cache[repo] = _git_mentioned_beads(
            next(b["path"] for b in beads if b["repo"] == repo), run=run)
    return git_cache[repo]


def _in_bead_window(b, start, end):
    """La ventana acota qué tareas del repo toca el bead: sin ella, cada
    tarea heredaría todos los ids del git (ruido)."""
    if not b["started_at"] or b["started_at"] > end:
        return False
    return not b["closed_at"] or start <= b["closed_at"]


def _git_beads(t, beads, repo, git_ids):
    """git: el git del repo de la sesión menciona el id (rama o commit) y la
    tarea cae dentro de la ventana del bead."""
    found = []
    start = t["_start"]
    end = start + timedelta(seconds=t.get("span_s") or 0.0)
    for b in beads:
        if b["repo"] != repo or b["id"].lower() not in git_ids:
            continue
        if _in_bead_window(b, start, end):
            found.append((b, "git"))
    return found


def _temporal_beads(t, beads, repo, ses):
    """temporal: sin id, mismo repo, y el inicio de la tarea cae dentro de
    [started_at, closed_at] del bead (closed_at null = abierto)."""
    found = []
    if not repo or not ses:
        return found
    start = t["_start"]
    for b in beads:
        if b["repo"] != repo or not b["started_at"] or b["started_at"] > start:
            continue
        if b["closed_at"] and start > b["closed_at"]:
            continue
        found.append((b, "temporal"))
    return found


def _dedupe_beads(found, i, t):
    """Una fila por (id, método); la temporal declara su ventana."""
    seen = {}
    for b, method in found:
        seen.setdefault((b["id"], method), (b, method))
    rows = []
    for (_bid, method), (b, _) in seen.items():
        row = {"task": i, "session": t.get("_sid"), "bead": b["id"],
               "method": method, "confidence": TASK_BEAD_CONFIDENCE[method]}
        if method == "temporal":
            row["window"] = {"started_at": b["started_at"].isoformat(),
                             "closed_at": (b["closed_at"].isoformat()
                                           if b["closed_at"] else None)}
        rows.append(row)
    return rows


def attribute_task_beads(tasks, beads, run=_git):
    """Tareas × beads → filas de atribución [{task, session, bead, method,
    confidence, ...}] en orden de resolución explicit > git > temporal.

    - explicit: el prompt de la tarea contiene el id del bead (conf 1.0).
    - git: el git del repo de la sesión menciona el id (rama o commit) y la
      tarea cae dentro de la ventana del bead (conf 0.8).
    - temporal: sin id, mismo repo, y el inicio de la tarea cae dentro de
      [started_at, closed_at] del bead (closed_at null = abierto); conf 0.5
      y ventana declarada en la fila.
    """
    by_id = {b["id"].lower(): b for b in beads}
    git_cache = {}
    rows = []
    sessions = _sessions_from_tasks(tasks)
    for i, t in enumerate(tasks):
        ses = sessions[t["_sid"]] if t.get("_sid") is not None else None
        repo = _session_repo(ses, beads)
        found = _explicit_beads(t, by_id)
        if not found and repo:
            found = _git_beads(t, beads, repo,
                               _git_bead_ids(beads, repo, git_cache, run))
        if not found:
            found = _temporal_beads(t, beads, repo, ses)
        rows.extend(_dedupe_beads(found, i, t))
    return rows, sessions


SESSION_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _spend_index(session_spend):
    """{session_id: fila} y {uuid embutido: fila} del reporte."""
    spend, spend_uuid = {}, {}
    for r in session_spend or []:
        if not (isinstance(r, dict) and r.get("session_id")):
            continue
        spend.setdefault(r["session_id"], r)
        # pi: el tracker usa el uuid desnudo; el reporte lo trae embutido
        # en el nombre de archivo de la sesión (`..._uuid`)
        for m in SESSION_UUID_RE.finditer(str(r["session_id"])):
            spend_uuid.setdefault(m.group(0), r)
    return spend, spend_uuid


def _session_cost(sid, sessions, spend_maps):
    """c(s) = session_spend[s].cost_effective (0.0 si no hay fila o es null)."""
    s = sessions[sid] if isinstance(sid, int) and 0 <= sid < len(sessions) else {}
    sid = s.get("session_id")
    row = next((m[sid] for m in spend_maps if sid in m), None)
    c = (row or {}).get("cost_effective")
    return float(c) if isinstance(c, (int, float)) else 0.0


def _split_bead_costs(rows, sessions, spend_maps):
    """s_beads = sid → beads con coste (explicit/git); s_temporal = sid →
    beads temporales (solo cuentan); bead_cost = coste repartido por bead."""
    s_beads = defaultdict(set)
    s_temporal = defaultdict(set)
    for row in rows:
        if row["method"] in ("explicit", "git"):
            s_beads[row["session"]].add(row["bead"])
        else:
            s_temporal[row["session"]].add(row["bead"])
    cost = {sid: _session_cost(sid, sessions, spend_maps)
            for sid in set(s_beads) | set(s_temporal)}
    bead_cost = defaultdict(float)
    for sid, blist in s_beads.items():
        share = cost[sid] / len(blist) if blist else 0.0
        for b in blist:
            bead_cost[b] += share
    return s_beads, s_temporal, bead_cost


def _mark_splits(rows, s_beads):
    """Sesión en k > 1 beads con coste: split: true en las filas afectadas."""
    for row in rows:
        k = len(s_beads.get(row["session"]) or ())
        if row["method"] in ("explicit", "git") and k > 1:
            row["split"] = True


def _per_bead(rows, s_beads, s_temporal, bead_cost):
    per_bead = {}
    for b in {r["bead"] for r in rows}:
        per_bead[b] = {
            "cost_per_bead": round(bead_cost.get(b, 0.0), 8),
            "n_sessions": sum(1 for s in s_beads if b in s_beads[s]),
            "n_temporal": sum(1 for s in s_temporal if b in s_temporal[s]),
        }
    return per_bead


def compute_bead_costs(rows, sessions, session_spend):
    """Fórmula CLRT-005. S(b) = sesiones atribuidas a b con method ∈
    {explicit, git} (temporal solo cuenta); c(s) = session_spend[s]
    .cost_effective (0.0 si no hay fila o es null). Sesión en k beads:
    c(s)/k con split: true en las filas afectadas. Devuelve
    {bead: {cost_per_bead, n_sessions, n_temporal}} y el mapa sesión→beads
    con coste."""
    spend_maps = _spend_index(session_spend)
    s_beads, s_temporal, bead_cost = _split_bead_costs(rows, sessions, spend_maps)
    _mark_splits(rows, s_beads)
    return _per_bead(rows, s_beads, s_temporal, bead_cost), s_beads


def build_task_beads_doc(tasks, repo_paths, **opts):
    """Dataset data/task_beads.json: filas de atribución + coste por bead.

    Opciones por keyword: `prefixes`, `session_spend`, `run`, `now`."""
    prefixes = opts.get("prefixes")
    session_spend = opts.get("session_spend")
    run = opts.get("run", _git)
    now = opts.get("now")
    beads = load_bead_records(repo_paths, prefixes)
    rows, sessions = attribute_task_beads(tasks, beads, run=run)
    per_bead, s_beads = compute_bead_costs(rows, sessions, session_spend)
    with_cost = sorted(v["cost_per_bead"] for v in per_bead.values()
                       if v["n_sessions"] >= 1)
    return {
        "metadata": {
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "repo_roots": prefixes or ["~/para/areas/dev/gh"],
            "confidence": dict(TASK_BEAD_CONFIDENCE),
            "n_beads": len(beads),
            "resolution": (
                "explicit (id en el prompt de la tarea) > git (rama o commit "
                "del repo menciona el id, tarea dentro de la ventana del "
                "bead) > temporal (inicio de la tarea dentro de "
                "[started_at, closed_at] del bead, closed_at null = abierto, "
                "mismo repo)."),
            "temporal_window": (
                "[started_at, closed_at] del bead; techo de confianza 0.5 y "
                "ventana declarada en cada fila temporal"),
            "formula": (
                "S(b) = sesiones atribuidas a b con method ∈ {explicit, "
                "git}; cost_per_bead(b) = Σ c(s) con c(s) = "
                "session_spend[s].cost_effective; las filas temporal NO "
                "suman coste (solo n_temporal). Sesión en k beads: c(s)/k "
                "con split: true. cost_per_bead_p50 = p50 sobre beads con "
                "|S(b)| >= 1; n_beads_sin_coste = beads con S(b) = ∅."),
            "task_index": (
                "índice en la lista de tareas del tracker (mismo orden que "
                "agent_metrics tasks); session = índice en sessions[]"),
        },
        "task_beads": rows,
        "beads": [{"bead": k, **v} for k, v in sorted(per_bead.items())],
        "cost_per_bead_p50": round(_pctl(with_cost, 0.5), 8) if with_cost else None,
        "n_beads_sin_coste": len(beads) - len(with_cost),
    }


def _task_beads_schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(
        doc, json.loads(TASK_BEADS_SCHEMA.read_text(encoding="utf-8")))
