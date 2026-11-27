# Change: Métricas de continuidad operativa (`ops`) para uso de modelos

## Why

La decisión de compra (suscripción GLM + fallback API) necesita métricas de
continuidad que el pipeline no produce: p95 de latencia sobre población completa,
concurrencia de agentes vs. requests, stall, utilización de suscripción, fallback,
eventos de cuota y completion autónoma. Hoy solo hay p50/p90 muestreados
(CORR-005, 11.8% de cobertura) y ninguna señal de bloqueo.

## What Changes

- Nuevo bloque `ops` en `data/agent_metrics.json` (y `specs/agent-metrics.schema.json`)
  con 9 métricas + 1 declarada no-medible (`queue_wait`, `null` con razón).
- Nueva capacidad spec `ops-metrics`.
- Vista "Ops/Continuidad" en el dashboard de agente (`scripts/viz-agent.py`),
  reusando el selector de período y la tabla de cobertura existentes.
- Regeneración por `scripts/nightly-update.sh` (sin job nuevo). Bloque nuevo:
  no hay valores previos que migren.

Fixes de review incorporados (Rule of 5, 2026-11-27):
- Fallback es evento **reportado**: `model_change.resolvedModelIsFallback` en los
  JSONL de OMP (verificado en `-para-areas-dev-gh-at-catherine/…/BpzPlanner.jsonl`);
  no hace falta hook nuevo. El proxy mid-task-switch queda solo para fuentes sin
  la señal, discriminando switch intencional (prompt del usuario entre error y switch).
- Stall/concurrencia solo sobre turnos con `durationMs` (claude-cli: 0% cobertura;
  amp/omp/codex no turn-level) con `coverage_pct` por fuente.
- Eventos de cuota parseados solo de turnos `stopReason:"error"` (campo
  `errorMessage` / `providerPayload`); nunca grep crudo del archivo (2,556 archivos
  matchean "quota" por contenido de código).
- Completion autónoma sobre cohorte no-ambigua del estimador, con `ambiguous_share`
  publicado al lado (cobertura actual: 2,311/4,354 eventos = 53%).
- Utilización de suscripción es `n/a` mientras no exista período de suscripción GLM
  en `config/fpa.json` (hoy solo claude-cli/codex históricos).

## Impact

- Affected specs: nueva `ops-metrics`; `usage-report` sin cambios de contrato
  (el scorecard de latencia muestreado queda como está; decisión D4 en design.md).
- Affected code: `scripts/agent_metrics_tracker.py` (o módulo nuevo
  `agent_metrics_ops.py` reutilizando `agent_metrics_common/turns`), `scripts/viz-agent.py`,
  `scripts/nightly-update.sh`, `specs/agent-metrics.schema.json`, `config/fpa.json`
  (bloque `ops_behavior` con umbrales y firmas de cuota).
- Co-modificación: `scripts/viz-agent.py` es propiedad del change
  `add-agent-behavior-dashboard` (completo pero sin archivar al 2026-11-27).
  Archivarlo antes de implementar esta vista, o coordinar el merge en esa vista.
