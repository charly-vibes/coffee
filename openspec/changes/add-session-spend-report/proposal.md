# Proposal: add-session-spend-report

## Why

Hoy no existe un reporte de gasto por día × proyecto × sesión:

- El reporte (`usage_report_v3.json`) emite gasto por día agregado
  (`daily`) y por proyecto × mes (`project_monthly`), pero la
  especificación excluye
  explícitamente el coste por proyecto × día
  ("granularidad proyecto × día fuera de alcance").
- La sección `sessions` del reporte solo trae contadores agregados
  (`total_sessions`, `length_distribution`, …); no hay filas por sesión con
  coste ni tokens, pese a que el contrato ya define la semántica de coste por
  sesión (con `null` + razón cuando falta telemetría).
- El dashboard FP&A tiene vista Proyectos: timeline a nivel mensual, pero
  ningún análisis responde "¿cuánto gasté hoy, en qué proyecto y en qué
  sesiones?".

## What Changes

1. **usage-report**: levantar la exclusión — emitir gasto y tokens por
   proyecto × día (`project_daily_spend`) y filas por sesión
   (`session_spend`) con `session_id`, proyecto, día, coste efectivo/real y
   tokens. Aditivo: ningún campo existente cambia de semántica.
2. **fpa-dashboard**: nueva vista "Gasto diario" — selector de día (o rango),
   desglose por proyecto y drill-down a sesiones del día/proyecto, con
   export CSV y deep-link.

## Capabilities

### Affected specs

- `specs/usage-report/spec.md`
  - MODIFIED: `granularidad proyecto × día fuera de alcance` → pasa a emitir
    `project_daily_spend`
  - ADDED: filas por sesión `session_spend`
- `specs/fpa-dashboard/spec.md`
  - ADDED: vista "Gasto diario" con drill-down por sesión

### Related

- `specs/context-dashboard/spec.md` — sin cambios; `context_metrics` sigue
  agregada por mes × modelo.

## Impact

- `scripts/usage-tracker.py`: nueva emisión (aditiva) + schema
  (`specs/usage-report-v3.schema.json`) + golden snapshot.
- `scripts/viz-fpa.py` (+ `fpa_config.py`): nueva vista registrada en el
  sistema de vistas existente.
- Tests: `tests/test_tracker_sessions.py` (o nuevo `test_tracker_spend.py`)
  y `tests/test_fpa_*.py` para la vista.
- Sin cambios en el ledger `data/charges.json` ni en la reconciliación cash.
