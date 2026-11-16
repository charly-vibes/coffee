## ADDED Requirements

### Requirement: dimensiones fuente, proveedor y plan

El reporte SHALL (debe) separar `source` (harness), `provider` (router/API) y
`plan` (suscripción) en `monthly`, `hourly`, `projects` y `session_spend`.
Cada cifra SHALL (debe) conservar la dimensión a la que pertenece;
`session_spend.source` SHALL (debe) ser el harness y `provider`/`plan` campos
separados.

#### Scenario: OpenRouter no es una herramienta

- **WHEN** una llamada de Pi usa el provider `openrouter`
- **THEN** `source="pi"`, `provider="openrouter"` y `plan=null`; no aparece
  como source

#### Scenario: plan de suscripción

- **WHEN** una llamada usa `qwen-token-plan-individual`
- **THEN** se emite como `provider`/`plan`, no como source; el conteo del
  índice no lo llama "proveedor" sin explicar la dimensión

#### Scenario: sesiones por harness

- **WHEN** se lee `sessions.by_source`
- **THEN** las claves son `pi`, `claude-cli`, `amp`, `codex`, `omp` y no
  providers/planes

### Requirement: migración de claves de dimensión

Las claves `tools` de `monthly`, `hourly` y `projects` SHALL (debe)N
reemplazarse por `sources`, `providers` y `plans`; `sessions.by_tool` y
`filtered_out.by_tool` SHALL (debe)N renombrarse a `by_source`. No se mantiene
`tools` como alias.

#### Scenario: sin clave tools

- **WHEN** se inspecciona un mes del reporte nuevo
- **THEN** no existe `tools`; existen `sources`, `providers` y `plans` con
  los datos que correspondan

#### Scenario: excluidos por fuente

- **WHEN** se lee `filtered_out`
- **THEN** la clave es `by_source` y sus valores son harness, no providers

### Requirement: conteo de excluidos por unidad

`filtered_out` SHALL (debe) emitir `interactions`, `sessions`, `files` y
`cost_effective` por separado. `share` SHALL (debe) calcularse con
interacciones; si `interactions` es `null`, `share` es `null` con razón. Un
extractor que solo puede contar archivos SHALL (debe) llenar `files` y dejar
`interactions=null` con razón, o contar las filas si las tiene; nunca
reutilizar archivos como interacciones.

#### Scenario: proyecto fuera de scope

- **WHEN** un proyecto no in-scope tiene 19.550 mensajes assistant y 327
  archivos
- **THEN** `filtered_out.interactions` refleja los mensajes, `files` los
  archivos y `share` usa el mismo denominador que el total

#### Scenario: coste excluido desconocido

- **WHEN** no hay pricing para un proyecto excluido
- **THEN** `cost_effective` es `null` con razón, no 0 fake

### Requirement: período canónico del reporte

`metadata.date_range` SHALL (debe) ser el rango local de los buckets
`daily`/`hourly`; `metadata.date_range_utc` SHALL (debe) ser el rango UTC de
los timestamps. Los consumidores SHALL (debe) declarar cuál usan. README,
índice y dashboard SHALL (debe) usar la misma convención para "datos hasta".

#### Scenario: evento cerca de medianoche

- **WHEN** una interacción cae a las 23:30 local (02:30 UTC del día siguiente)
- **THEN** `date_range` termina el día local y `date_range_utc` el día UTC;
  ambos son correctos y no se contradicen

## MODIFIED Requirements

### Requirement: emisiones para dashboard FP&A

El tracker SHALL (debe) emitir, además de lo existente: breakdown mensual por
`source`, `provider`, `plan` y modelo, project totals por mes vía
`project_daily`, cargas pay-per-token separadas de suscripciones, commits y
releases por proyecto/mes (donde haya token de GitHub), tokens
input/output/cache-read/cache-write por mes y modelo, interacciones por kind,
interacciones/sesiones/archivos y coste de los excluidos por el charly-filter,
timezone de bucketing, período local/UTC y métricas de concurrencia.

#### Scenario: concurrencia desglosada

- **WHEN** el tracker emite las métricas de concurrencia del mes
- **THEN** incluyen proyectos distintos por hora, picos de sesiones simultáneas
  y project switches por hora activa

#### Scenario: kinds declarados

- **WHEN** el tracker desglosa interacciones por kind
- **THEN** los kinds son user prompts, assistant turns y tool calls (FPA-140)

#### Scenario: tokens por modelo presentes

- **WHEN** el tracker genera `usage_report_v3.json`
- **THEN** el reporte incluye tokens input/output/cache-read/cache-write por mes
  y modelo, validables contra el schema extendido

#### Scenario: breakdown mensual desglosado

- **WHEN** se revisa el breakdown mensual por source/provider/plan y modelo
- **THEN** cada celda trae interacciones y coste efectivo

#### Scenario: interacciones por kind presentes

- **WHEN** el tracker genera el reporte
- **THEN** cada mes incluye interacciones desglosadas por kind, permitiendo
  calcular el share de tool-calls (FPA-140)

#### Scenario: share del filtro visible

- **WHEN** el charly-filter excluye proyectos
- **THEN** el reporte incluye interacciones, sesiones, archivos y coste de los
  excluidos para calcular el share filtrado con unidades separadas (FPA-141)

#### Scenario: cargas pay-per-token reales del ledger

- **WHEN** el tracker genera el reporte y el ledger tiene cargas p2p del mes
  (p.ej. enero 2026: claude $20 en créditos + amp $80)
- **THEN** `pay_per_token_charges` del mes es la suma real del ledger con
  `pay_per_token_provenance` = "reported" (FPA-013 deja de ser assumed)

#### Scenario: mes sin cargas p2p en el ledger

- **WHEN** un mes no registra cargas p2p en el ledger (p.ej. abril 2026)
- **THEN** `pay_per_token_charges` es el estimado del tracker con provenance
  "assumed" — cifra presente, nunca vacío sin razón (FPA-008)

#### Scenario: insumo de reconciliación FPA-082

- **WHEN** el tracker genera `charges_reconciliation_by_month`
- **THEN** cada mes incluye cada tool con `charges_real` (ledger, *reported*),
  `subscription_fee_implicit` (calendario, *assumed*, no es cash) y
  `cost_effective` (tracker, *assumed*); los meses con facturas pero sin
  interacciones también aparecen (p.ej. recargas openrouter de jul-2026)

#### Scenario: total real unificado

- **WHEN** se compara `metadata.cost_total_real` con la suma mensual de
  `cost_real`
- **THEN** coinciden (una sola cuenta); la parte tracker queda separada en
  `cost_real_total_tracker` y los fees implícitos en
  `subscription_fees_by_month`, sin doble conteo (coffe-a31.2: resuelve la
  divergencia 381.62 vs 611.62 del dataset previo)