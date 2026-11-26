# Tasks: add-agent-behavior-dashboard

## 1. Dataset
- [x] 1.1 Bloque `agent_behavior` en `config/fpa.json` (gap cap, retry window, repo roots, umbrales).
- [x] 1.2 `scripts/agent-metrics-tracker.py`: turnos pi/claude-cli, tareas, flags, series, repos, cobertura.
- [x] 1.3 `specs/agent-metrics.schema.json` + `--validate` (stdlib).
- [x] 1.4 `tests/test_agent_metrics.py` (tareas, flags, series, repos, schema).

## 2. Dashboard
- [x] 2.1 `scripts/viz-agent.py`: ventanas pre-calculadas (FPA-090) con percentiles exactos.
- [x] 2.2 Render: KPIs, gráficos mensuales, tabla por modelo, cobertura y límites.
- [x] 2.3 `tests/test_viz_agent.py` (modelo + ejecución del JS en node).

## 3. Integración
- [x] 3.1 Job nocturno: tracker + viz (orden tras usage-tracker).
- [x] 3.2 Índice del sitio enlaza el dashboard (guard `test_site_nav`).
- [x] 3.3 README: scripts, datos y sección de definiciones/cobertura.
- [x] 3.4 Regenerar `data/agent_metrics.json`, `data/agent-dashboard.html` e `index.html`.

## 4. Rule-of-5 review (fixes)
- [x] 4.1 CORR-001: ventana de reintento efectiva (desde el último turno de la tarea anterior) + tests de fuera de ventana y de tarea sin actividad.
- [x] 4.2 CORR-002: día local según `metadata.timezone` (no UTC) + test.
- [x] 4.3 CORR-003: preámbulos de reanudación de claude excluidos y contados.
- [x] 4.4 CORR-004/CLAR-003: `—` en lugar de `null`, razones en los `n/a`, test de ventana vacía en node.
- [x] 4.5 CLAR-001: encabezado con `first_task → last_task` y período del reporte por separado.
- [x] 4.6 CLAR-002/CLAR-004: nota de definición en "repos mantenidos" y formato numérico es-AR.
- [x] 4.7 EDGE-001/002: validar antes de escribir; validación no fatal en el nightly.
- [x] 4.8 EDGE-003: `--validate-only` + test negativo del spec; test de deep-link.
- [x] 4.9 EDGE-004/005/006: prefijos de raíz por repo, raíces ausentes y turnos huérfanos declarados.
- [x] 4.10 EXCL-001: `nly` permitido solo en markdown (el typo se sigue detectando en código).
- [x] 4.11 EXCL-002: se elimina `monthly_tasks` (fuente duplicada), `day_ix`, `commits_by_month`, `prompts_per_calendar_day`.
- [x] 4.12 EXCL-004/005: `aria-label` en los gráficos y guard `AGENT-DOCS` (`--update-docs`/`--check-docs`) en el nightly.
