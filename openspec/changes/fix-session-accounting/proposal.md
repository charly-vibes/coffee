# Proposal: fix-session-accounting

## Why

El tracker pierde una parte material de la actividad de omp y no define una
identidad de sesión estable para deduplicar:

- `extract_omp()` camina solo `sessions/<cwd>/*.jsonl`; las sesiones de
  subagente viven en `sessions/<cwd>/<parent-session>/<subagent>.jsonl`.
  En la verificación del 2026-11-16 quedan fuera **103 sesiones y 5.253
  llamadas facturadas** (~54% de las llamadas omp in-scope).
- `sessions.agent_semantics` declara "omp: sin señal de subagentes" pese a
  que esos archivos existen.
- El dedupe global usa `(timestamp, tool, model, source, project)` sin
  identidad de sesión. Descarta 102 eventos de archivo Amp distintos y 3
  llamadas Pi distintas; a la vez, los forks de Pi copian historial y hoy
  `context_metrics` procesa el prefijo copiado dos veces.
- `session_spend` no cumple "una fila por sesión" cuando un hilo Amp toca
  varios repos (4/2/2 filas con el mismo `session_id`).

## What Changes

- **usage-report**: cobertura de logs omp anidados; dedupe por identidad de
  sesión; semántica de subagentes declarada; forks de Pi marcados y
  contabilizados una vez en contexto; `session_spend` por sesión nativa con
  desglose `projects` para hilos multi-proyecto.
- **BREAKING (dataset)**: `session_spend[]` agrega `projects`, `provider` y
  `plan`; los conteos de omp cambian (más interacciones, subagentes
  separados). Los consumidores deben leer `source` como harness y `provider`
  como proveedor, no asumir que `source` es el proveedor.
- Actualizar schema, goldens, README y la regeneración real.

## Impact

- Affected specs: `usage-report`.
- Affected code: `scripts/usage-tracker.py`
  (`_omp_scan`, `extract_omp`, `extract_omp_sessions`, dedupe de `main()`,
  `_session_spend`, `aggregate`), `specs/usage-report-v3.schema.json`,
  `tests/test_tracker_omp.py`, `tests/test_tracker_sessions.py`,
  `tests/test_tracker_context.py`, `data/usage_report_v3.json`, `README.md`.
- Runtime/artefacto: +~5.3k interacciones omp; +~103 sesiones de subagente
  contadas en `sessions.subagents` (no en `total_sessions`); el JSON crece
  de forma acotada.
- No cambia la semántica de coste de las fuentes existentes; solo la
  completitud y la identidad de sesión.

## Tickets

- coffe-c15.1 (omp anidado), coffe-c15.9 (dedupe/session_spend), EDGE-001 (forks de Pi).
