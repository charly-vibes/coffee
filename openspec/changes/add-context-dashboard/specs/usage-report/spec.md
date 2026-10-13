## ADDED Requirements

### Requirement: sección context_metrics por sesión de Pi

El reporte SHALL (debe) incluir una sección `context_metrics` con una fila por
sesión de Pi in-scope, conteniendo como mínimo: `month`, `project`, `model`,
`models_turns`, `provider`, `turns`, `peak_ctx_est`, `final_ctx_est`,
`compactions`, atribución de chars por fuente (`asst_text`, `asst_thinking`,
`asst_args`, y resultados por `toolName`), `big_results` por tool y
`cost_in/out/cr/cw`. La emisión es aditiva: ningún campo existente cambia de
semántica. Sesiones sin turnos (stubs) quedan fuera del array y se cuentan en
`context_metrics.meta.stubs`.

#### Scenario: sesión con atribución completa

- **WHEN** el tracker procesa una sesión de Pi con turnos de assistant, tool
  results y compaction
- **THEN** la fila de la sesión tiene el pico de contexto estimado, la atribución
  por fuente que suma al transcript observado y `compactions >= 1`

#### Scenario: atribución coherente con el transcript

- **WHEN** se suma la atribución por fuente de una sesión
- **THEN** el total es la suma de chars de user + assistant content + toolResults
  observados en el JSONL, sin duplicar ni omitir roles

#### Scenario: stubs sin turnos

- **WHEN** una sesión no tiene mensajes de assistant
- **THEN** no aparece en `context_metrics.sessions[]` y `meta.stubs` la cuenta

### Requirement: pico de contexto por segmento de compactación

El estimador de pico SHALL (debe) resetear el acumulador de chars en cada entrada
`type=="compaction"` y computar `peak_ctx_est` como máximo del acumulador
**dentro del segmento**, no del archivo completo — los archivos de sesión de Pi
retienen todos los mensajes previos a una compactación.

#### Scenario: pico post-compactación no hereda chars previos

- **WHEN** una sesión acumula 200k chars, compacta y luego acumula 50k más en su
  segmento final
- **THEN** `peak_ctx_est` refleja el mayor de los segmentos (200k), no la suma
  continua (250k), y `compactions` cuenta 1

### Requirement: atribución de turnos por modelo

Cada fila SHALL (debe) emitir `models_turns` (mapa modelo → turnos observados) y
`model` como modelo dominante por conteo de turnos, usado por los agregados
`monthly_model` — las sesiones de Pi pueden cambiar de modelo mid-sesión
(`type=="model_change"`).

#### Scenario: sesión con cambio de modelo

- **WHEN** una sesión registra 20 turnos con el modelo A y 10 con el modelo B
- **THEN** la fila tiene `models_turns: {A: 20, B: 10}` y `model: A`

### Requirement: estimación de contexto con método declarado

El estimador de contexto SHALL (debe) usar `chars / chars_per_token` acumulado por
turno, con `chars_per_token` declarado en `context_metrics.meta` y marcado *assumed*
(default 4, dirección de bias documentada: subestima). Las sesiones con proveedor
`claude-cli` SHALL (deben) emitir `context_source: "claude-cli"` para poder
excluirlas de comparaciones de usage.

#### Scenario: factor documentado

- **WHEN** se lee `context_metrics.meta`
- **THEN** el factor de estimación está presente con su tag *assumed*, la dirección
  de bias y el ratio de validación (`validation_ratio_median`) mediano contra
  sesiones con usage confiable

#### Scenario: sesiones claude-cli marcadas

- **WHEN** una sesión usa el proveedor claude-cli (usage inválido, coste de
  suscripción)
- **THEN** la fila lleva `context_source: "claude-cli"` y sus cifras de coste no
  se mezclan con las de proveedores de pago

### Requirement: coste por sesión con telemetría faltante como null con razón

El emisor SHALL (debe) tratar el coste ausente como dato faltante: cuando
`usage.cost` no está disponible para una sesión (turnos abortados o proveedor sin
billing por token), `cost_in/out/cr/cw` emiten `null` acompañado de una razón en
la fila — mismo patrón que "modelos sin telemetría de tokens emiten null con
razón" — en lugar de 0 fake.

#### Scenario: coste ausente

- **WHEN** una sesión no tiene `usage.cost` en ningún turno
- **THEN** los cuatro campos de coste son `null` y la fila explica la razón; el
  dashboard muestra la razón en vez de un cero silencioso

### Requirement: patologías contadas, nunca silenciosas

El reporte SHALL (debe) contar en `context_metrics.meta.excluded_pathological` las
sesiones con un bloque assistant > `context_thresholds.pathological_chars` (config,
default 500k) y excluirlas de los percentiles. El conteo SHALL (debe) ser visible
en el dashboard.

#### Scenario: sesión glitch

- **WHEN** una sesión contiene un bloque assistant por encima del umbral de config
- **THEN** queda excluida de los percentiles y `excluded_pathological` la cuenta,
  con la sesión listable en el dashboard

### Requirement: agregados mensuales por modelo

`context_metrics.monthly_model[]` SHALL (debe) incluir, por mes × modelo dominante:
conteo de sesiones, p50 y p90 del pico de contexto, % de sesiones >
`context_thresholds.big_session_tokens` (config), tok/turno por fuente y spend por
componente (in/out/cache). Los agregados se computan sobre el set limpio
(`turns > 0`, sin patológicas).

#### Scenario: tabla mes x modelo

- **WHEN** el reporte cubre sesiones de dos modelos en un mismo mes
- **THEN** el mes tiene dos filas en `monthly_model`, cada una con sus percentiles
  y growth per turno por fuente

## MODIFIED Requirements

### Requirement: métricas de sesión con señal de compaction para Pi

La sección `sessions` del reporte SHALL (debe) computar `total_compactions` desde
las entradas `type=="compaction"` de los JSONL de Pi. La nota vigente de "sin señal
en pi (compactions)" (`scripts/usage-tracker.py`) SHALL (debe) actualizarse: la
señal existe en los archivos desde ~may-2026 y el emisor la consume.

#### Scenario: total de compactions refleja los JSONL

- **WHEN** un archivo de sesión de Pi contiene entradas `type=="compaction"`
- **THEN** `sessions.total_compactions` las cuenta y el docstring ya no declara
  "sin señal" para compactions
