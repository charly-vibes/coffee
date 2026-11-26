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
