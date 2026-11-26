#!/usr/bin/env python3
"""
agent-metrics-tracker.py — métricas de comportamiento del agente.

Purpose: medir el eje que el talk de Armin Ronacher ("Build the Workshop
Before the Software Factory") presenta y que el reporte de uso no cubre:
- prompts de usuario y turnos abiertos por el modelo,
- duración de tarea (cuánto corre el agente por pedido) con percentiles,
- tiempo activo del agente (no wall-clock humano),
- abortos/errores y reintentos tras error (proxy de "pushback"),
- repos creados y mantenidos (git local, sin GITHUB_TOKEN).

Responsibilities:
- parsear los logs con timestamps por turno (`~/.pi/agent/sessions/*/*.jsonl`,
  `~/.claude/projects/**/*.jsonl`) → turnos con rol, stopReason, isError;
- agrupar turnos en tareas (un prompt de usuario abre tarea, el siguiente la
  cierra) y sumar el tiempo que el agente estuvo ocupado — `durationMs` del
  turno cuando existe, si no el gap desde el evento previo, con tope
  `gap_cap_minutes` para no contar inactividad humana como trabajo del agente;
- emitir `data/agent_metrics.json` (serie diaria, series compactas de tareas
  para percentiles por ventana, y panel de repos por mes);
- escanear repos git locales para "creados" (primer commit) y "mantenidos"
  (días con commit + retorno en meses distintos) — la métrica del talk sin
  depender de la API de GitHub (`metadata.outcomes` del reporte sigue siendo
  `unavailable` por falta de token).

Rationale: el dashboard necesita percentiles exactos por ventana arbitraria
(el P90 de una tarea no se promedia desde P90 mensuales), por eso se emite la
serie de tareas completa y el viz recalcula. Se declara cobertura por fuente:
claude-cli no registra stopReason, así que sus abortos son `missing` con
razón, nunca 0. Cero dependencias (stdlib-only), igual que el resto del repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "usage_report_v3.json"
CONFIG = ROOT / "config" / "fpa.json"
OUT = ROOT / "data" / "agent_metrics.json"
SCHEMA = ROOT / "specs" / "agent-metrics.schema.json"

PI_DIR = Path.home() / ".pi" / "agent" / "sessions"
CLAUDE_DIR = Path.home() / ".claude" / "projects"

DEFAULTS = {
    "gap_cap_minutes": 30,        # gap mayor = inactividad, no trabajo del agente
    "retry_window_minutes": 5,    # prompt de usuario que sigue a un error
    "max_task_minutes": 240,      # tope de cordura para una tarea
    "repo_roots": ["~/para/areas/dev/gh"],
    "repo_max_depth": 3,
    "min_commit_days": 5,         # "mantenido": commit en >5 días
    "min_returning_months": 2,    # ... y vuelve en al menos 2 meses
    "bulk_day_interactions": 5000,  # día "de máquina" (volumen de API, no de persona)
}

FLAG_ABORTED, FLAG_ERROR, FLAG_TOOL_ERROR, FLAG_RETRY = 1, 2, 4, 8


# ----------------------------------------------------------------------
# Config / utilidades
# ----------------------------------------------------------------------

def load_config(path=None):
    """Bloque `agent_behavior` del config único del sitio, con defaults."""
    cfg = dict(DEFAULTS)
    p = Path(path) if path else CONFIG
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8")).get("agent_behavior") or {}
        cfg.update({k: v for k, v in raw.items() if v is not None})
        cfg["config_source"] = str(p.relative_to(ROOT)) if p.is_absolute() else str(p)
    else:
        cfg["config_source"] = None
    return cfg


def _dt(value):
    """ISO-8601 → datetime UTC. None si no parsea."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pctl(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[int(p * (len(sorted_vals) - 1))]


def _iter_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ----------------------------------------------------------------------
# Turnos por fuente
# ----------------------------------------------------------------------

def turns_from_pi(records):
    """Registros de una sesión Pi → turnos {ts, role, model, stop, is_error,
    dur_s}. Roles: user_prompt / assistant / tool. Los preámbulos inyectados
    del harness viajan como `custom`, no como user: no abren tarea."""
    model = None
    turns = []
    for r in records:
        typ = r.get("type")
        if typ == "model_change":
            model = r.get("modelId") or model
            continue
        if typ != "message":
            continue
        msg = r.get("message") or {}
        role = msg.get("role")
        ts = _dt(r.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            continue
        if role == "user":
            turns.append({"ts": ts, "role": "user_prompt", "model": model})
        elif role == "assistant":
            dur_ms = msg.get("durationMs")
            turns.append({
                "ts": ts, "role": "assistant",
                "model": msg.get("model") or model,
                "stop": msg.get("stopReason"),
                "dur_s": (float(dur_ms) / 1000.0) if isinstance(dur_ms, (int, float)) else None,
            })
        elif role == "toolResult":
            turns.append({"ts": ts, "role": "tool",
                          "is_error": bool(msg.get("isError"))})
    return turns


def turns_from_claude(records):
    """Registros de una sesión Claude Code → turnos. Solo el hilo principal
    (`isSidechain` false): los subagentes duplicarían los conteos. Claude no
    escribe stopReason `aborted`, así que `stop` queda None (cobertura)."""
    turns = []
    for r in records:
        if r.get("isSidechain"):
            continue
        typ = r.get("type")
        msg = r.get("message") or {}
        ts = _dt(r.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            continue
        if typ == "user":
            content = msg.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if is_tool_result:
                err = bool(r.get("toolUseResult", {}).get("is_error")) if isinstance(
                    r.get("toolUseResult"), dict) else False
                turns.append({"ts": ts, "role": "tool", "is_error": err})
            elif isinstance(content, str) or isinstance(content, list):
                turns.append({"ts": ts, "role": "user_prompt"})
        elif typ == "assistant":
            turns.append({"ts": ts, "role": "assistant",
                          "model": msg.get("model"),
                          "stop": msg.get("stop_reason")})
    return turns


def load_pi_turns(sessions_dir=None):
    """Walk de sesiones Pi. Dedupe de forks por id de sesión: dos archivos con
    el mismo id (fork copia el historial) aportan solo el más largo."""
    base = Path(sessions_dir or PI_DIR)
    if not base.exists():
        return [], {"sessions": 0, "skipped_reason": "sin directorio de sesiones pi"}
    by_id = {}
    for path in sorted(base.glob("*/*.jsonl")):
        records = list(_iter_jsonl(path))
        if not records:
            continue
        sid = None
        for r in records[:3]:
            if r.get("type") == "session":
                sid = r.get("id")
                break
        key = sid or str(path)
        prev = by_id.get(key)
        if prev is None or len(records) > prev[1]:
            by_id[key] = (path, len(records), records)
    turns = []
    for path, _n, records in by_id.values():
        turns.extend(turns_from_pi(records))
    return turns, {"sessions": len(by_id), "files": len(list(base.glob("*/*.jsonl")))}


def load_claude_turns(claude_dir=None):
    base = Path(claude_dir or CLAUDE_DIR)
    if not base.exists():
        return [], {"sessions": 0, "skipped_reason": "sin directorio de sesiones claude"}
    turns = []
    sessions = 0
    for path in sorted(base.glob("**/*.jsonl")):
        if "subagents" in path.parts:
            continue
        records = list(_iter_jsonl(path))
        if not records:
            continue
        sessions += 1
        turns.extend(turns_from_claude(records))
    return turns, {"sessions": sessions}


# ----------------------------------------------------------------------
# Tareas (funciones puras: testeables sin disco)
# ----------------------------------------------------------------------

def build_tasks(turns, gap_cap_s=1800, retry_window_s=300, max_task_s=14400):
    """Turnos → tareas. Un `user_prompt` abre tarea; el siguiente la cierra.

    Tiempo ocupado del agente: por turno, `dur_s` si el log lo trae, si no el
    gap desde el evento previo, ambos con tope `gap_cap_s` (un gap mayor es
    inactividad humana o máquina dormida, no trabajo).

    Flags: aborted (stopReason aborted), error (stopReason error), tool_error
    (algún toolResult con isError), retry (el prompt que abrió la tarea llegó
    ≤ retry_window_s después de un error de la tarea anterior).
    """
    ordered = sorted(turns, key=lambda t: t["ts"])
    tasks = []
    cur = None
    prev_ts = None
    prev_end_ts = None
    prev_ended_error = False

    def close(task, end_ts):
        if task is None:
            return
        end = task.pop("_last", end_ts) or end_ts
        task["span_s"] = max(0.0, (end - task["_start"]).total_seconds())
        if task["span_s"] > max_task_s:
            task["span_s"] = max_task_s
            task["clipped"] = True
        # el durationMs reportado puede exceder el span (mensaje con timestamp
        # de inicio de respuesta): el tiempo ocupado nunca supera al de reloj
        task["busy_s"] = min(task["busy_s"], task["span_s"])
        if task["busy_s"] > 0:
            tasks.append(task)

    for t in ordered:
        gap = (t["ts"] - prev_ts).total_seconds() if prev_ts else None
        if gap is not None and gap < 0:
            gap = 0
        if t["role"] == "user_prompt":
            if cur is not None:
                prev_ended_error = bool(cur["aborted"] or cur["errors"])
                prev_end_ts = t["ts"]
            close(cur, t["ts"])
            cur = {"_start": t["ts"], "busy_s": 0.0, "turns": 0,
                   "aborted": 0, "errors": 0, "tool_errors": 0, "retry": 0,
                   "model": None, "clipped": False}
            if (prev_ended_error and prev_end_ts is not None
                    and (t["ts"] - prev_end_ts).total_seconds() <= retry_window_s):
                cur["retry"] = 1
            prev_ts = t["ts"]
            continue
        if cur is None:
            prev_ts = t["ts"]
            continue
        busy = None
        if t["role"] == "assistant":
            cur["turns"] += 1
            cur["model"] = cur["model"] or t.get("model")
            if t.get("stop") == "aborted":
                cur["aborted"] = 1
            elif t.get("stop") == "error":
                cur["errors"] = 1
            if t.get("dur_s") is not None:
                busy = min(float(t["dur_s"]), gap_cap_s)
        elif t["role"] == "tool":
            if t.get("is_error"):
                cur["tool_errors"] += 1
        if busy is None and gap is not None:
            busy = min(gap, gap_cap_s)
        if busy:
            cur["busy_s"] += busy
        cur["_last"] = t["ts"]
        prev_ts = t["ts"]
    if ordered:
        close(cur, ordered[-1]["ts"])
    return tasks


def task_flags(task):
    bits = 0
    if task.get("aborted"):
        bits |= FLAG_ABORTED
    if task.get("errors"):
        bits |= FLAG_ERROR
    if task.get("tool_errors"):
        bits |= FLAG_TOOL_ERROR
    if task.get("retry"):
        bits |= FLAG_RETRY
    return bits


# ----------------------------------------------------------------------
# Agregaciones puras
# ----------------------------------------------------------------------

def daily_from_tasks(tasks):
    """Tareas → métricas por día (día local del inicio de la tarea)."""
    daily = {}
    for t in tasks:
        day = t["_start"].date().isoformat()
        d = daily.setdefault(day, {"prompts": 0, "tasks": 0, "turns": 0,
                                   "agent_s": 0.0, "aborted": 0, "errors": 0,
                                   "tool_errors": 0, "retry": 0})
        d["prompts"] += 1
        d["tasks"] += 1
        d["turns"] += t["turns"]
        d["agent_s"] += t["busy_s"]
        d["aborted"] += t["aborted"]
        d["errors"] += t["errors"]
        d["tool_errors"] += t["tool_errors"]
        d["retry"] += t["retry"]
    for d in daily.values():
        d["agent_s"] = round(d["agent_s"], 1)
    return daily


def series(tasks, days, sources, models):
    """Series compactas para percentiles exactos por ventana en el viz.

    `day` = índice en `days` (día local), `s` = segundos ocupados (int),
    `sp` = segundos de reloj de la tarea (incluye esperas),
    `src`/`model` = índice, `f` = bitmask de flags.
    """
    day_ix = {d: i for i, d in enumerate(days)}
    src_ix = {s: i for i, s in enumerate(sources)}
    model_ix = {m: i for i, m in enumerate(models)}
    out = {"day": [], "s": [], "sp": [], "src": [], "model": [], "f": []}
    for t in tasks:
        day = t["_start"].date().isoformat()
        if day not in day_ix:
            continue
        out["day"].append(day_ix[day])
        out["s"].append(int(round(t["busy_s"])))
        out["sp"].append(int(round(t.get("span_s") or 0)))
        out["src"].append(src_ix.get(t.get("source"), 0))
        out["model"].append(model_ix.get(t.get("model") or "?", 0))
        out["f"].append(task_flags(t))
    return out


def monthly_tasks(tasks):
    """Percentiles de duración por mes (para la tabla del dashboard)."""
    by = {}
    for t in tasks:
        by.setdefault(t["_start"].strftime("%Y-%m"), []).append(t["busy_s"])
    out = {}
    for ym, vals in by.items():
        vals.sort()
        out[ym] = {"tasks": len(vals),
                   "p50_s": round(_pctl(vals, .5), 1),
                   "p90_s": round(_pctl(vals, .9), 1),
                   "p99_s": round(_pctl(vals, .99), 1),
                   "sum_s": round(sum(vals), 1)}
    return out


# ----------------------------------------------------------------------
# Repos (git local)
# ----------------------------------------------------------------------

def _git(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def find_git_repos(roots, max_depth=3):
    """Repos git bajo cada raíz (profundidad acotada), sin duplicados."""
    found = {}
    for raw in roots:
        root = Path(os.path.expanduser(raw))
        if not root.exists():
            continue
        root_depth = len(root.parts)
        for dirpath, dirnames, _files in os.walk(root):
            p = Path(dirpath)
            if len(p.parts) - root_depth >= max_depth:
                dirnames[:] = []
            if ".git" in dirnames:
                dirnames[:] = [d for d in dirnames if d != ".git"]
                found[str(p)] = p
    return [found[k] for k in sorted(found)]


def repo_metrics(paths, run=_git, prefix="~/para/areas/dev/gh"):
    """Repos → {name, first_commit, days_by_month, commits_by_month}.

    Días con commit y commits por mes calendario: la ventana del dashboard
    suma meses (los días no se solapan entre meses, así el conteo de días del
    rango es exacto).
    """
    out = []
    home_prefix = os.path.expanduser(prefix)
    for path in paths:
        log = run(["log", "--date=short", "--format=%ad"], path)
        if not log:
            continue
        dates = [line.strip() for line in log.splitlines() if line.strip()]
        days = sorted(set(dates))
        if not days:
            continue
        days_by_month = {}
        for d in days:
            days_by_month[d[:7]] = days_by_month.get(d[:7], 0) + 1
        commits_by_month = {}
        for d in dates:
            commits_by_month[d[:7]] = commits_by_month.get(d[:7], 0) + 1
        name = str(path)
        if name.startswith(home_prefix + os.sep):
            name = name[len(home_prefix) + 1:]
        out.append({"name": name, "first_commit": days[0],
                    "days_by_month": days_by_month,
                    "commits_by_month": commits_by_month})
    return out


# ----------------------------------------------------------------------
# Ensamblado
# ----------------------------------------------------------------------

def build_report(tasks, cfg, coverage, repos, report_meta):
    """Tareas de todas las fuentes → dataset del dashboard."""
    days = sorted({t["_start"].date().isoformat() for t in tasks})
    sources = sorted({t["source"] for t in tasks}) or ["pi"]
    models = sorted({t["model"] for t in tasks if t.get("model")}) or ["?"]
    return {
        "metadata": {
            "date_range": {"start": report_meta["start"], "end": report_meta["end"]},
            "first_task": days[0] if days else None,
            "last_task": days[-1] if days else None,
            "task_definition": (
                "una tarea = un prompt de usuario y los turnos que dispara "
                "hasta el prompt siguiente; el tiempo del agente es la suma "
                "de los turnos (durationMs reportado, o gap desde el evento "
                "previo con tope gap_cap)."),
            "gap_cap_minutes": cfg["gap_cap_minutes"],
            "retry_window_minutes": cfg["retry_window_minutes"],
            "config_source": cfg.get("config_source"),
            "provenance": {"tasks": "derived", "aborts": "reported",
                           "tool_errors": "reported", "repos": "reported"},
            "coverage": coverage,
            "reasons": {
                "abort_signal": "solo pi registra stopReason aborted/error; "
                                "claude-cli no escribe esa señal",
                "fork_dedupe": "sesiones pi con el mismo id = fork: se toma "
                               "el archivo más largo",
            },
        },
        "days": days,
        "sources": sources,
        "models": models,
        "daily": daily_from_tasks(tasks),
        "tasks": series(tasks, days, sources, models),
        "monthly_tasks": monthly_tasks(tasks),
        "repos": repos,
        "months": sorted({t["_start"].strftime("%Y-%m") for t in tasks}),
    }


def summarize_coverage(pi_stats, claude_stats):
    return [
        {"source": "pi", "turn_level": True, "abort_signal": True,
         "sessions": pi_stats.get("sessions", 0),
         "note": pi_stats.get("skipped_reason") or
                 "solo sesiones on-disk (pi rota las viejas; arranca 2026-04)"},
        {"source": "claude-cli", "turn_level": True, "abort_signal": False,
         "sessions": claude_stats.get("sessions", 0),
         "note": claude_stats.get("skipped_reason")},
        {"source": "amp", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el log de amp no expone turnos con timestamp ni stopReason"},
        {"source": "omp", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el log de omp no expone turnos con timestamp por mensaje"},
        {"source": "codex", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el rollout no se camina acá (una tarea por rollout, sin "
                 "frontera de prompt de usuario)"},
    ]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = OUT
    if "--output" in argv:
        out_path = Path(argv[argv.index("--output") + 1])
    cfg = load_config()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    meta = report["metadata"]["date_range"]

    pi_turns, pi_stats = load_pi_turns()
    cl_turns, cl_stats = load_claude_turns()
    coverage = summarize_coverage(pi_stats, cl_stats)

    tasks = []
    for turns, source in ((pi_turns, "pi"), (cl_turns, "claude-cli")):
        built = build_tasks(turns, gap_cap_s=cfg["gap_cap_minutes"] * 60,
                            retry_window_s=cfg["retry_window_minutes"] * 60,
                            max_task_s=cfg["max_task_minutes"] * 60)
        for t in built:
            t["source"] = source
        tasks.extend(built)

    repos = repo_metrics(find_git_repos(cfg["repo_roots"], cfg["repo_max_depth"]))
    merged = build_report(tasks, cfg, coverage, repos, meta)
    merged["metadata"]["repo_roots"] = cfg["repo_roots"]
    merged["metadata"]["min_commit_days"] = cfg["min_commit_days"]
    merged["metadata"]["min_returning_months"] = cfg["min_returning_months"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")

    if "--validate" in argv:
        sys.path.insert(0, str(ROOT / "scripts"))
        from schema_validate import validate_against_schema  # noqa: PLC0415
        errors = validate_against_schema(merged,
                                         json.loads(SCHEMA.read_text(encoding="utf-8")))
        if errors:
            for e in errors[:20]:
                print(f"schema: {e}", file=sys.stderr)
            return 1

    m = merged["metadata"]
    print(f"tareas: {len(merged['tasks']['s'])} · días: {len(merged['days'])} "
          f"· fuentes: {', '.join(merged['sources'])} · modelos: {len(merged['models'])}")
    print(f"rango: {m['first_task']} → {m['last_task']} · repos: {len(merged['repos'])}")
    print(f"escrito: {out_path.relative_to(ROOT) if out_path.is_absolute() else out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
