## 1. Config y schema

- [ ] 1.1 Agregar bloque `ops_behavior` a `config/fpa.json`: `quota_signatures` por proveedor, `retry_window_minutes` (reusa 5), `gap_cap_minutes` (reusa 30), umbrales de alerta
- [ ] 1.2 Extender `specs/agent-metrics.schema.json` con el bloque `ops` (latency, concurrency, stall, quota_events, fallback, completion, intervention, queue_wait, provenance, coverage)

## 2. Extracción (scripts/agent_metrics_ops.py, reusa agent_metrics_common/turns)

- [ ] 2.1 Latencia: p50/p90/p95 por proveedor/modelo sobre TODA la población de turnos con `durationMs` (elimina el sampling CORR-005 para ops); emitir `n` y `coverage_pct` por fuente (D4)
- [ ] 2.2 Concurrencia: interval-union de spans de turnos y de tareas por minuto de reloj; `requests_p95` y `agents_p95` diarios; desglose por `purpose` para los `model_usage` auxiliares de OMP (D2)
- [ ] 2.3 Stall: `span_wall_task - suma(durationMs)` solo sobre turnos con duración; clasificación error-adjacent (<= retry_window tras turno con error/abort) vs idle; tope gap_cap (D2)
- [ ] 2.4 Fallback: parsear `model_change.resolvedModelIsFallback` de JSONL de OMP como evento `reported`; proxy mid-task-switch para pi/claude-cli con discriminador de prompt intermedio; excluir sesiones fork; etiquetar `method` (D1)
- [ ] 2.5 Eventos de cuota: parsear solo turnos `stopReason:"error"` (campo `errorMessage` / `providerPayload`) contra `ops_behavior.quota_signatures`; proveedor sin firma -> `null` con razón (D3)
- [ ] 2.6 Completion autónoma: join con la atribución del estimador restringido a cohorte no-ambigua; publicar `ambiguous_share` (D5)
- [ ] 2.7 Intervención humana: aborts + prompts de usuario en retry_window tras error, por cada 100 beads aceptados
- [ ] 2.8 Utilización de suscripción: `cost_imputed(notional) / monthly_fee` por período de `config/fpa.json`; `n/a` con razón si no hay período para el modelo; headroom diario `1 - p95(utilización diaria)` (D6)
- [ ] 2.9 Queue wait: emitir `null` con razón `"no_ttft_in_local_logs"` (D7)
- [ ] 2.10 Provenance y coverage por cifra en `ops.provenance` / `ops.coverage`, mismos valores del léxico existente (reported/derived/measured/assumed)

## 3. Orquestación y visualización

- [ ] 3.1 Integrar `agent_metrics_ops.py` en `scripts/nightly-update.sh` después del tracker de agente
- [ ] 3.2 Vista "Ops/Continuidad" en `scripts/viz-agent.py`: tarjetas p95, concurrencia, stall, fallback, cuota, completion; reusar selector de período y tabla de cobertura; SVG inline sin dependencias
- [ ] 3.3 Actualizar bloque AGENT-DOCS del README vía `--update-docs` y verificar con `--check-docs`

## 4. Tests

- [ ] 4.1 Tests de funciones puras con filas sintéticas (stall con turnos sin durationMs, fallback flag true/false, firmas de cuota, cohorte ambigua) en `tests/test_tracker.py` o archivo nuevo
- [ ] 4.2 Snapshot dorado del bloque `ops` y revisión de diff (`REGEN_GOLDEN=1`)
- [ ] 4.3 Smoke test Chromium de la vista nueva (0 pageerrors, charts renderizan) en `tests/test_viz.py`
- [ ] 4.4 Paridad de coste: `ops` no altera `sum(tasks.cost) == session_spend` por modelo

## 5. Validación

- [ ] 5.1 `openspec validate add-ops-continuity-metrics --strict`
- [ ] 5.2 Regenerar y verificar contra datos reales: cobertura de fallback en OMP > 0, `ambiguous_share` publicado, `queue_wait` null con razón
