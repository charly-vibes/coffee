# Design: Métricas de continuidad operativa (`ops`)

## Context

Revisión Rule of 5 (2026-11-27) de la investigación de uso de modelos contra la
implementación actual. Gaps confirmados: p95 latencia (solo p50/p90 muestreados,
CORR-005 con 11.8% de cobertura), concurrencia, stall, utilización, fallback,
eventos de cuota y completion autónoma. La propuesta original defería fallback a
un "hook de captura OMP" pendiente de inspección; la inspección ya se hizo y
resuelve el tier.

## Goals / Non-Goals

- Goals: métricas derivables de logs locales existentes, con provenance y
  coverage por cifra, respetando la separación de carriles real/notional.
- Non-Goals: instrumentación nueva en el harness, OTel/collector, llamadas a APIs
  de proveedores, métricas de proyectos fuera del scope charly, cambiar la
  definición de tarea.

## Decisions

### D1: Fallback = evento reportado de OMP (resuelve CORR-002)

`model_change` en los JSONL de OMP trae `resolvedModelIsFallback` (verificado:
`{"type":"model_change","model":"openrouter/z-ai/glm-5.3-flash","resolvedModelIsFallback":false}`).
`ops.fallback` cuenta switches con flag true como eventos `reported`. Para
pi/claude-cli (sin la señal) aplica el proxy mid-task-switch con discriminador:
un prompt de usuario entre el error y el switch indica switch intencional y se
excluye. Sesiones fork excluidas (convención de paridad de coste "sesiones no-fork").

### D2: Stall y concurrencia solo sobre turnos con duración (resuelve CORR-001)

`latency_coverage.measured`: claude-cli 0.0; amp/omp/codex `turn_level: false`.
`stall = span_wall_task - suma(durationMs)` calculado solo sobre turnos con
`durationMs`; turnos sin duración usan el fallback de gap con tope
`gap_cap_minutes` (30). Toda cifra de `ops` lleva `coverage_pct` por fuente.

### D3: Eventos de cuota desde turnos de error, no del archivo crudo (resuelve CORR-003)

Grep crudo de "429|quota|rate limit" matchea 2,556 archivos dominados por
contenido de código. Parseo con scope a turnos con `stopReason:"error"`: campo
`errorMessage` (pi: cuerpo JSON del proveedor con `code`/`type`, ej.
`model_not_found`) y `providerPayload` (OMP). Firma por proveedor configurable en
`config/fpa.json` (`ops_behavior.quota_signatures`); proveedor sin firma produce
`null` con razón.

### D4: Latencia p95 vive en `ops`, no en `usage-report`

El scorecard de `usage_report_v3.json` conserva su p50/p90 muestreado
(consumidores existentes); `ops.latency` calcula p50/p90/p95 sobre TODA la
población de turnos con `durationMs` desde `agent_metrics_tracker.py`. Sin
segunda convención: mismo campo `durationMs`, misma nomenclatura de provenance.
Seguimiento fuera de scope: retirar el sampling CORR-005 cuando ops lo cubra.

### D5: Completion autónoma sobre cohorte no-ambigua (resuelve EDGE-001)

La atribución del estimador acredita 2,311/4,354 eventos (53%); unidades
ambiguas 25-71% por modelo. `ops.completion` se calcula sobre la cohorte
no-ambigua y publica `ambiguous_share` al lado. Sin eso la tasa es
ininterpretable (mismo modo de fallo que hizo parecer a DeepSeek el doble de caro).

### D6: Utilización de suscripción, `n/a` honesto (resuelve CLAR-001)

`utilization = cost_imputed(notional) / monthly_fee` por período de
`config/fpa.json` (hoy: claude-cli y codex históricos). Para el modelo principal
(GLM) no hay período de suscripción, entonces es `n/a` con razón hasta que exista.
Headroom diario: `1 - p95(utilización diaria)`, nunca punto-en-el-tiempo.

### D7: Queue wait queda no-medible

No hay TTFT en ningún log local. `ops.queue_wait = null` con razón
(`"no_ttft_in_local_logs"`). Único camino de cierre: registro con timestamps de
request-start en el harness, fuera de scope.

## Métricas y prioridad

Cada métrica mapea a la lista de 12 prioridades de la investigación (conversación
cotorreo 6b08df19): 1-2 coste (existen, session_spend), 3-4 utilización/cuota
(D6, D3), 5 latencia p95 (D4), 6-7 concurrencia (D2), 8 fallback (D1), 9 stall
(D2), 10 beads/día y coste/bead (existen, estimador), 11 coste/bead (existe),
12 intervención humana (nuevo: aborts + prompts en retry_window tras error, por
cada 100 beads aceptados).

## Riesgos / Trade-offs

- Proxy de fallback para pi/claude-cli sobre-cuenta switches intencionales,
  mitigado por discriminador de prompt intermedio y etiqueta `method`.
- pi rota sesiones viejas (arranque 2026-04): cobertura declarada por fuente,
  igual que hoy.
- Firmas de cuota por proveedor son frágiles ante cambios de formato:
  config-driven, `null` con razón si no matchean.

## Open Questions

- ¿Los `model_usage` auxiliares del harness de OMP (`purpose`: "auto-thinking",
  "task-completion-probe") cuentan en concurrencia de requests? Default: sí,
  separados por `purpose` en el desglose.
