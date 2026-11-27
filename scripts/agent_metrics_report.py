"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Ensamblado: dataset del dashboard, validación contra schema, escritura de
artefactos y CLI (main).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from agent_metrics_aggregate import _sessions_from_tasks, daily_from_tasks, series
from agent_metrics_attr import build_task_beads_doc, _task_beads_schema_errors
from agent_metrics_beads import (
    BEAD_OUT, TASK_BEADS_OUT, bead_privacy_errors, build_bead_index,
    _bead_schema_errors,
)
from agent_metrics_common import (
    OUT, REPORT, ROOT, SCHEMA, task_day, tz_from_offset, load_config,
)
from agent_metrics_repos import find_git_repos, repo_metrics
from agent_metrics_tasks import build_tasks
from agent_metrics_turns import load_claude_turns, load_pi_turns


# ----------------------------------------------------------------------
# Ensamblado
# ----------------------------------------------------------------------

def _report_metadata(cfg, coverage, report_meta, days):
    """Bloque metadata del dataset del dashboard."""
    return {
        "date_range": {"start": report_meta["date_range"]["start"],
                       "end": report_meta["date_range"]["end"]},
        "timezone": report_meta.get("timezone"),
        "first_task": days[0] if days else None,
        "last_task": days[-1] if days else None,
        "task_definition": (
            "una tarea = un prompt de usuario y los turnos que dispara "
            "hasta el prompt siguiente; el tiempo del agente es la suma "
            "de los turnos (durationMs reportado, o gap desde el evento "
            "previo con tope gap_cap). coffe-dnx.3: una tarea no cruza "
            "sesiones — si el prompt siguiente abre otra sesión, es otra "
            "tarea; sid indexa sessions[] = {source, project, "
            "session_id}, y cost/tokens suman los turnos assistant de "
            "la tarea."),
        "gap_cap_minutes": cfg["gap_cap_minutes"],
        "retry_window_minutes": cfg["retry_window_minutes"],
        "config_source": cfg.get("config_source"),
        "provenance": {"tasks": "derived", "aborts": "reported",
                       "tool_errors": "reported", "repos": "reported",
                       "cost": "reported", "sessions": "derived"},
        "coverage": coverage,
        "reasons": {
            "abort_signal": "solo pi registra stopReason aborted/error; "
                            "claude-cli no escribe esa señal",
            "fork_dedupe": "sesiones pi con el mismo id = fork: se toma "
                           "el archivo más largo",
        },
    }


def build_report(tasks, cfg, **opts):
    """Tareas de todas las fuentes → dataset del dashboard.

    Opciones por keyword: `coverage`, `repos`, `report_meta`, `tz`."""
    coverage = opts["coverage"]
    repos = opts["repos"]
    report_meta = opts["report_meta"]
    tz = opts.get("tz")
    days = sorted({task_day(t, tz) for t in tasks})
    sources = sorted({t["source"] for t in tasks}) or ["pi"]
    models = sorted({t["model"] for t in tasks if t.get("model")}) or ["?"]
    sessions = _sessions_from_tasks(tasks)
    return {
        "metadata": _report_metadata(cfg, coverage, report_meta, days),
        "days": days,
        "sources": sources,
        "models": models,
        "daily": daily_from_tasks(tasks, tz),
        "tasks": series(tasks, days, sources, models, tz=tz),
        "sessions": sessions,
        "repos": repos,
        "months": sorted({t["_start"].strftime("%Y-%m") for t in tasks}),
    }


def summarize_coverage(pi_stats, claude_stats):
    # coffe-dnx.3: el gate de paridad de coste está acotado — session_spend
    # incluye fuentes (amp, omp, codex, ...) que agent_metrics no procesa;
    # el alcance declarado queda en cada fila con `cost_parity`.
    return [
        {"source": "pi", "turn_level": True, "abort_signal": True,
         "sessions": pi_stats.get("sessions", 0),
         "cost_parity": "sum(tasks.cost) por modelo == suma de las filas "
                        "session_spend de pi (sesiones no-fork)",
         "note": pi_stats.get("skipped_reason") or
                 "solo sesiones on-disk (pi rota las viejas; arranca 2026-04)"},
        {"source": "claude-cli", "turn_level": True, "abort_signal": False,
         "sessions": claude_stats.get("sessions", 0),
         "cost_parity": "sum(tasks.cost) por modelo == suma de las filas "
                        "session_spend de claude-cli (sesiones no-fork)",
         "note": claude_stats.get("skipped_reason") or (
             f"preámbulos de reanudación excluidos: "
             f"{claude_stats.get('preambles_skipped', 0)}")},
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


def _schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(doc, json.loads(SCHEMA.read_text(encoding="utf-8")))


def _report_errors(errors):
    for e in errors[:20]:
        print(f"schema: {e}", file=sys.stderr)
    return 1


def _argv_value(argv, flag):
    """Valor de una opción `flag valor` en argv (IndexError si no lo trae)."""
    return argv[argv.index(flag) + 1]


def _validate_only_mode(argv):
    """Modos --validate-only / --validate-bead / --validate-task-beads:
    validan un dataset ya escrito sin re-extraer logs. None = seguir."""
    if "--validate-only" in argv:
        # valida un dataset ya escrito (los tests usan esto para el caso
        # negativo del spec, sin re-extraer logs)
        target = Path(_argv_value(argv, "--validate-only"))
        return _report_errors(_schema_errors(json.loads(target.read_text(encoding="utf-8"))))
    if "--validate-bead" in argv:
        # coffe-dnx.4: valida data/bead_index.json contra su schema + guard
        # de privacidad (sin re-escanear repos)
        doc = json.loads(Path(_argv_value(argv, "--validate-bead")).read_text(encoding="utf-8"))
        errors = _bead_schema_errors(doc) + bead_privacy_errors(doc)
        return _report_errors(errors) if errors else 0
    if "--validate-task-beads" in argv:
        # coffe-dnx.5: valida data/task_beads.json contra su schema
        target = Path(_argv_value(argv, "--validate-task-beads"))
        errors = _task_beads_schema_errors(json.loads(target.read_text(encoding="utf-8")))
        return _report_errors(errors) if errors else 0
    return None


def _build_source_tasks(cfg):
    """Turnos de todas las fuentes → tareas etiquetadas, coverage y stats."""
    pi_turns, pi_stats = load_pi_turns()
    cl_turns, cl_stats = load_claude_turns()
    coverage = summarize_coverage(pi_stats, cl_stats)
    task_stats = {}
    tasks = []
    for turns, source in ((pi_turns, "pi"), (cl_turns, "claude-cli")):
        built = build_tasks(turns, gap_cap_s=cfg["gap_cap_minutes"] * 60,
                            retry_window_s=cfg["retry_window_minutes"] * 60,
                            max_task_s=cfg["max_task_minutes"] * 60,
                            stats=task_stats)
        for t in built:
            t["source"] = source
        tasks.extend(built)
    return tasks, coverage, task_stats, cl_stats


def _write_bead_index(bead):
    """coffe-dnx.4: bead_index desde .beads/issues.jsonl de los repos.
    Schema drift no aborta (mismo criterio del nightly); un error de
    privacidad sí impide escribir el artefacto."""
    for e in _bead_schema_errors(bead)[:10]:
        print(f"bead_index schema: {e}", file=sys.stderr)
    bead_priv = bead_privacy_errors(bead)
    if bead_priv:
        for e in bead_priv[:10]:
            print(f"bead_index privacy: {e}", file=sys.stderr)
        return
    BEAD_OUT.parent.mkdir(parents=True, exist_ok=True)
    BEAD_OUT.write_text(json.dumps(bead, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def _session_spend_rows():
    """session_spend del reporte; [] si no se puede leer."""
    try:
        return json.loads(REPORT.read_text(encoding="utf-8")).get("session_spend")
    except (OSError, json.JSONDecodeError):
        return []


def _write_task_beads(tasks, repo_paths, cfg):
    """coffe-dnx.5: atribución tarea↔bead + coste por bead. Schema drift no
    aborta (mismo criterio que bead_index)."""
    tb = build_task_beads_doc(tasks, repo_paths, prefixes=cfg["repo_roots"],
                              session_spend=_session_spend_rows())
    errors = _task_beads_schema_errors(tb)
    for e in errors[:10]:
        print(f"task_beads schema: {e}", file=sys.stderr)
    if errors:
        return
    TASK_BEADS_OUT.parent.mkdir(parents=True, exist_ok=True)
    TASK_BEADS_OUT.write_text(json.dumps(tb, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")


def _finalize_metadata(merged, cfg, stats, tz):
    """Metadata derivada del run: config de repos, stats y razones.
    `stats` = {"task": task_stats, "claude": cl_stats,
               "missing_roots": raíces inexistentes}."""
    m = merged["metadata"]
    missing_roots = stats["missing_roots"]
    m["repo_roots"] = cfg["repo_roots"]
    m["repo_roots_missing"] = missing_roots
    m["min_commit_days"] = cfg["min_commit_days"]
    m["min_returning_months"] = cfg["min_returning_months"]
    m["bulk_day_interactions"] = cfg["bulk_day_interactions"]
    m["orphan_turns"] = stats["task"].get("orphan_turns", 0)
    m["preamble_prompts_skipped"] = stats["claude"].get("preambles_skipped", 0)
    if missing_roots:
        m["reasons"]["repo_roots_missing"] = (
            "raíces de repos configuradas que no existen: "
            + ", ".join(missing_roots))
    if tz is None:
        m["reasons"]["timezone"] = (
            "el reporte no declara timezone: días atribuidos en UTC")


def _print_summary(merged, out_path, missing_roots):
    m = merged["metadata"]
    print(f"tareas: {len(merged['tasks']['s'])} · días: {len(merged['days'])} "
          f"· fuentes: {', '.join(merged['sources'])} · modelos: {len(merged['models'])}")
    print(f"rango: {m['first_task']} → {m['last_task']} · repos: {len(merged['repos'])}")
    if missing_roots:
        print(f"aviso: raíces inexistentes: {', '.join(missing_roots)}", file=sys.stderr)
    print(f"escrito: {out_path.relative_to(ROOT) if out_path.is_absolute() else out_path}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = Path(_argv_value(argv, "--output")) if "--output" in argv else OUT
    validated = _validate_only_mode(argv)
    if validated is not None:
        return validated

    cfg = load_config()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    meta = report["metadata"]
    tz = tz_from_offset(meta.get("timezone"))

    tasks, coverage, task_stats, cl_stats = _build_source_tasks(cfg)
    repo_paths, missing_roots = find_git_repos(cfg["repo_roots"],
                                               cfg["repo_max_depth"])
    repos = repo_metrics(repo_paths, prefixes=cfg["repo_roots"])
    _write_bead_index(build_bead_index(repo_paths, prefixes=cfg["repo_roots"]))
    merged = build_report(tasks, cfg, coverage=coverage, repos=repos,
                          report_meta=meta, tz=tz)
    _write_task_beads(tasks, repo_paths, cfg)
    _finalize_metadata(merged, cfg, {"task": task_stats, "claude": cl_stats,
                                     "missing_roots": missing_roots}, tz)

    # validar ANTES de escribir: un dataset inválido no debe quedar en disco
    if "--validate" in argv:
        errors = _schema_errors(merged)
        if errors:
            return _report_errors(errors)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    _print_summary(merged, out_path, missing_roots)
    return 0
