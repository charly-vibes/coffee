# ops-metrics Specification

## Purpose

Métricas de continuidad operativa del uso de modelos de IA (bloque `ops` en
`data/agent_metrics.json`, generado por `scripts/agent_metrics_ops.py`): p95 de
latencia por proveedor sobre población completa, concurrencia de agentes y
requests, stall con clasificación, fallback (evento reportado de OMP + proxy
etiquetado), eventos de cuota desde turnos de error, completion autónoma sobre
cohorte no-ambigua, intervención humana, utilización de suscripción con `n/a`
honesto y queue wait declarado no-medible. Toda cifra lleva provenance y
coverage; los carriles real/notional nunca se comparan entre sí.

## ADDED Requirements

### Requirement: Latencia p95 sobre población completa

El bloque `ops` SHALL (debe) calcular p50/p90/p95 de `durationMs` por
proveedor/modelo sobre TODA la población de turnos con `durationMs` (sin
muestreo de archivos), emitiendo `n`, `p50_ms`, `p90_ms`, `p95_ms` y
`coverage_pct` por fuente. Proveedores sin turnos con duración SHALL (deben)
emitir `null` con razón.

#### Scenario: p95 sobre población completa

- **WHEN** se genera el bloque `ops` y existen turnos de pi con `durationMs`
- **THEN** `ops.latency[provider]` reporta `p95_ms` calculado sobre todos los turnos con duración de ese proveedor, con `n` y `coverage_pct`

#### Scenario: fuente sin señal de duración

- **WHEN** un proveedor no tiene `durationMs` en sus turnos (ej. claude-cli, cobertura medida 0.0%)
- **THEN** su entrada de latencia es `null` con razón y `coverage_pct: 0`

### Requirement: Concurrencia de agentes y requests separadas

El bloque `ops` SHALL (debe) calcular concurrencia p95 diaria como unión de
intervalos: `agents_p95` sobre spans de tareas y `requests_p95` sobre spans de
turnos (más `model_usage` auxiliares desglosados por `purpose`), solo sobre
fuentes turn-level.

#### Scenario: agentes vs requests no intercambiables

- **WHEN** dos tareas se solapan en el tiempo y cada una tiene 3 turnos secuenciales
- **THEN** `agents_p95` del minuto de solape es 2 y `requests_p95` es 2, no 6

### Requirement: Stall con cobertura por fuente

El bloque `ops` SHALL (debe) calcular minutos de stall por tarea como
`span_wall_task - suma(durationMs)` solo sobre turnos con `durationMs`, usando
el fallback de gap con tope `gap_cap_minutes` para turnos sin duración, y SHALL
(debe) clasificar los segmentos en `error_adjacent` (dentro de
`retry_window_minutes` tras un turno con error o abort) e `idle`.

#### Scenario: stall no infla con turnos sin duración

- **WHEN** una tarea tiene turnos de claude-cli sin `durationMs`
- **THEN** el stall de esos turnos se calcula por gap con tope `gap_cap_minutes` y `ops.stall.coverage` declara la cobertura real de la fuente

### Requirement: Fallback como evento reportado con proxy etiquetado

El bloque `ops` SHALL (debe) contar fallbacks de OMP desde los registros
`model_change` con `resolvedModelIsFallback: true` (provenance `reported`). Para
fuentes sin la señal SHALL (debe) usar el proxy mid-task-switch (switch de modelo
tras un turno de error dentro de `retry_window_minutes`, excluyendo switches con
prompt de usuario intermedio y sesiones fork) con provenance `derived` y campo
`method` que identifique el mecanismo usado.

#### Scenario: fallback verdadero en OMP

- **WHEN** un JSONL de OMP contiene `model_change` con `resolvedModelIsFallback: true`
- **THEN** el evento cuenta en `ops.fallback.events` con provenance `reported`

#### Scenario: switch intencional no cuenta como fallback

- **WHEN** en pi hay un turno de error seguido de un prompt de usuario y luego un cambio de modelo
- **THEN** el cambio no cuenta como fallback (el prompt intermedio indica switch intencional)

### Requirement: Eventos de cuota solo desde turnos de error

El bloque `ops` SHALL (debe) detectar eventos de cuota/rate-limit parseando
únicamente turnos con `stopReason:"error"` (campo `errorMessage` de pi y
`providerPayload` de OMP) contra firmas configurables por proveedor en
`config/fpa.json` (`ops_behavior.quota_signatures`). El parseo SHALL (debe)
registrarse como `derived`. Un proveedor sin firma configurada o sin match
SHALL (debe) emitir `null` con razón y no `0`.

#### Scenario: evento de cuota detectado

- **WHEN** un turno de pi tiene `stopReason: "error"` y `errorMessage` con cuerpo del proveedor que matchea la firma configurada
- **THEN** se emite un evento en `ops.quota_events` con `ts`, `provider`, `signature` y `session_id`

#### Scenario: contenido de código no genera falso positivo

- **WHEN** el contenido de un turno (código o texto) menciona "quota" o "429" sin que el turno sea un error del proveedor
- **THEN** no se emite ningún evento de cuota

### Requirement: Completion autónoma sobre cohorte no-ambigua

El bloque `ops` SHALL (debe) calcular la tasa de completion autónoma (beads
cerrados sin turnos de error/abort en las tareas atribuidas sobre beads
iniciados) restringida a la cohorte de atribución no-ambigua del estimador,
publicando `ambiguous_share` al lado de la tasa.

#### Scenario: tasa interpretable

- **WHEN** la atribución del estimador dejó eventos ambiguos sin acreditar
- **THEN** `ops.completion` reporta la tasa sobre la cohorte no-ambigua y `ambiguous_share` con el porcentaje de eventos ambiguos excluidos

### Requirement: Utilización de suscripción con n/a honesto

El bloque `ops` SHALL (debe) calcular la utilización de suscripción como
`cost_imputed(notional) / monthly_fee` por período de `config/fpa.json`, y el
headroom como `1 - p95(utilización diaria)` del período. Para modelos sin
período de suscripción configurado SHALL (debe) emitir `n/a` con razón.
El carril notional SHALL (debe) reportarse separado del carril real y nunca
combinarse ni rankearse entre sí.

#### Scenario: suscripción sin período configurado

- **WHEN** el modelo principal no tiene período de suscripción en `config/fpa.json`
- **THEN** su utilización es `n/a` con razón y no se infiere de otro carril

### Requirement: Queue wait declarado no-medible

El bloque `ops` SHALL (debe) emitir `queue_wait: null` con razón
`"no_ttft_in_local_logs"` mientras ningún log local registre el tiempo hasta el
primer token. Prohibido inferir queue wait de gaps entre turnos.

#### Scenario: sin TTFT en logs

- **WHEN** se genera el bloque `ops`
- **THEN** `ops.queue_wait` es `null` con razón y ninguna métrica lo aproxima con gaps entre turnos

### Requirement: Intervención humana normalizada

El bloque `ops` SHALL (debe) reportar la intervención humana como aborts más
prompts de usuario dentro de `retry_window_minutes` tras un turno de error,
normalizado por cada 100 beads aceptados.

#### Scenario: normalización por beads aceptados

- **WHEN** hubo 5 aborts y 3 prompts de recuperación en un día con 40 beads aceptados
- **THEN** `ops.intervention` reporta 20 por cada 100 beads aceptados con el conteo crudo al lado

### Requirement: Provenance y coverage por cifra

Todo valor del bloque `ops` SHALL (debe) llevar provenance del léxico existente
(`reported`, `derived`, `measured`, `assumed`) y cobertura por fuente. Los
valores `null` SHALL (debe) llevar razón. La generación SHALL (debe) correr en
`nightly-update.sh` sin job nuevo y el bloque `ops` SHALL (debe) pasar la
validación del schema `specs/agent-metrics.schema.json`.

#### Scenario: provenance completo

- **WHEN** se valida `data/agent_metrics.json` contra su schema tras la regeneración nocturna
- **THEN** cada métrica de `ops` tiene provenance y coverage, y los `null` tienen razón
