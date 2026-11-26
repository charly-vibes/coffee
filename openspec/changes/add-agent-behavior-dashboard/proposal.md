# Proposal: add-agent-behavior-dashboard

## Why

El reporte mide volumen, costo y contexto, pero no el eje que Armin Ronacher
presenta en *Build the Workshop Before the Software Factory*: cuánto corre el
agente por tarea, cuántos prompts se envían por día, cuánto tiempo está
activo, cuánto se aborta/reintenta, y cuántos repos se crean y se mantienen.
Hoy esas cifras no existen en el repo (o existen sólo como agregado mensual no
presentado), así que no se pueden comparar contra el talk ni seguir su
evolución por período.

## What Changes

- Nuevo dataset `data/agent_metrics.json` (schema
  `specs/agent-metrics.schema.json`) producido por
  `scripts/agent-metrics-tracker.py`: tareas reconstruidas desde turnos con
  timestamp (pi + claude-cli), serie diaria, series compactas por tarea para
  percentiles exactos, y panel de repos desde `git log` local.
- Nuevo dashboard `data/agent-dashboard.html` (`scripts/viz-agent.py`) con el
  mismo selector de período pre-calculado que el dashboard FPA (FPA-090),
  provenance por cifra, cobertura por fuente y `n/a` con razón.
- Config: bloque `agent_behavior` (topes de gap/tarea, ventana de reintento,
  raíces de repos, umbrales de mantenimiento) en `config/fpa.json`.
- Wiring: job nocturno (tracker + viz), índice del sitio y README.
- Tests: `tests/test_agent_metrics.py` (funciones puras + schema) y
  `tests/test_viz_agent.py` (modelo por ventana + ejecución del JS en node).

## Impact

- Affected specs: nueva capability `agent-behavior-dashboard`.
- Affected code: `scripts/agent-metrics-tracker.py`,
  `scripts/viz-agent.py`, `scripts/nightly-update.sh`,
  `scripts/viz-index.py`, `config/fpa.json`, `README.md`, `index.html`,
  `specs/agent-metrics.schema.json`.
- No rompe el reporte existente: el dataset es nuevo y el dashboard nuevo; el
  reporte `usage_report_v3.json` sólo se lee.
- Limitación declarada: claude-cli no registra `stopReason`, así que la tasa
  de abortos es un piso (sólo pi); amp/omp/codex no aportan tareas por no
  exponer turnos con timestamp/frontera de prompt.
