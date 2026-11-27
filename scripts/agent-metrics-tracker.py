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
- re-exportar la API completa de los módulos agent_metrics_* (los tests
  cargan este archivo standalone con spec_from_file_location y acceden a
  los atributos acá);
- emitir `data/agent_metrics.json` (serie diaria, series compactas de tareas
  para percentiles por ventana, y panel de repos por mes) más bead_index y
  task_beads, y validar contra schema antes de escribir.

Rationale: el dashboard necesita percentiles exactos por ventana arbitraria
(el P90 de una tarea no se promedia desde P90 mensuales), por eso se emite la
serie de tareas completa y el viz recalcula. Se declara cobertura por fuente:
claude-cli no registra stopReason, así que sus abortos son `missing` con
razón, nunca 0. Cero dependencias (stdlib-only), igual que el resto del repo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_common import (  # noqa: E402
    CLAUDE_DIR, CONFIG, DEFAULTS, FLAG_ABORTED, FLAG_ERROR, FLAG_RETRY,
    FLAG_TOOL_ERROR, OUT, PI_DIR, REPORT, ROOT, SCHEMA, PREAMBLE_MARKERS,
    _bead_rel_name, _cost_from, _dt, _git, _is_preamble, _is_tool_result,
    _iter_jsonl, _project_from_cwd, _pctl, _prompt_text, _session_from_records,
    _usage_tokens, load_config, task_day, tz_from_offset,
)
from agent_metrics_turns import (  # noqa: E402
    _pi_session_id, _pi_tool_turn, _pi_turn_from_record, _claude_tool_error,
    _claude_user_turn, _claude_assistant_turn, load_claude_turns,
    load_pi_turns, turns_from_claude, turns_from_pi,
)
from agent_metrics_tasks import (  # noqa: E402
    TaskWindows,
    _apply_assistant_turn, _apply_turn, _close_task, _crosses_session,
    _is_retry, _new_task, _open_prompt_task, _retry_context, _turn_gap,
    build_tasks, task_flags,
)
from agent_metrics_aggregate import (  # noqa: E402
    _series_axes, _series_row, _sessions_from_tasks, daily_from_tasks, series,
)
from agent_metrics_repos import (  # noqa: E402
    _commit_days, _days_by_month, _repo_metrics_row, _walk_git_root,
    find_git_repos, repo_metrics,
)
from agent_metrics_beads import (  # noqa: E402
    BEAD_ORGS, BEAD_OUT, BEAD_SCHEMA, BEAD_TEXT_KEYS, TASK_BEADS_OUT,
    TASK_BEADS_SCHEMA, _bead_in_scope, _bead_month_rows, _count_bead,
    _fold_lead, _month_row, _bead_schema_errors, bead_privacy_errors,
    build_bead_index,
)
from agent_metrics_attr import (  # noqa: E402
    BEAD_ID_RE, SESSION_UUID_RE, TASK_BEAD_CONFIDENCE, _dedupe_beads, _explicit_beads,
    _git_bead_ids, _git_beads, _git_mentioned_beads, _in_bead_window,
    _per_bead, _session_repo, _session_cost, _spend_index, _split_bead_costs,
    _mark_splits, _temporal_beads, _task_beads_schema_errors, attribute_task_beads,
    build_task_beads_doc, compute_bead_costs, load_bead_records,
)
from agent_metrics_report import (  # noqa: E402
    _argv_value, _build_source_tasks, _finalize_metadata, _print_summary,
    _report_errors, _schema_errors, _session_spend_rows, _validate_only_mode,
    _write_bead_index, _write_task_beads, _report_metadata, build_report,
    main, summarize_coverage,
)

if __name__ == "__main__":
    sys.exit(main())
