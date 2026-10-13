## ADDED Requirements

### Requirement: sección context_metrics por sesión de Pi

El reporte SHALL (debe) incluir una sección `context_metrics` con una fila por
sesión de Pi in-scope, conteniendo como mínimo: `month`, `project`, `model`,
`provider`, `turns`, `peak_ctx_est`, `final_ctx_est`, `compactions`, atribución de
chars por fuente (`asst_text`, `asst_thinking`, `asst_args`, y resultados por
`toolName`), `big_results` por tool y `cost_in/out/cr/cw`. La emisión es aditiva:
ningún campo existente cambia de semántica.

#### Scenario: sesión con atribución completa

- **WHEN** el tracker procesa una sesión de Pi con turnos de assistant, tool
  results y compaction
- **THEN** la fila de la sesión tiene el pico de contexto estimado, la atribución
  por fuente que suma al transcript observado y `compactions >= 1`

#### Scenario: atribución coherente con el transcript

- **WHEN** se suma la atribución por fuente de una sesión
- **THEN** el total es la suma de chars de user + assistant content + toolResults
  observados en el JSONL, sin duplicar ni omitir roles

### Requirement: estimación de contexto con método declarado

El estimador de contexto SHALL (debe) usar `chars / chars_per_token` acumulado por
turno, con `chars_per_token` declarado en `context_metrics.meta` y marcado *assumed*
(default 4). Las sesiones con proveedor `claude-cli` SHALL (deben) emitir
`context_source: "claude-cli"` para poder excluirlas de comparaciones de usage.

#### Scenario: factor documentado

- **WHEN** se lee `context_metrics.meta`
- **THEN** el factor de estimación está presente con su tag *assumed* y el ratio
  de validación documentado contra sesiones con usage confiable

#### Scenario: sesiones claude-cli marcadas

- **WHEN** una sesión usa el proveedor claude-cli (usage inválido, coste de
  suscripción)
- **THEN** la fila lleva `context_source: "claude-cli"` y sus cifras de coste no
  se mezclan con las de proveedores de pago

### Requirement: patologías contadas, nunca silenciosas

El reporte SHALL (debe) contar en `context_metrics.meta.excluded_pathological` las
sesiones con un bloque assistant >500k chars (markup de tool-call roto) y excluirlas
de los percentiles. El conteo SHALL (debe) ser visible en el dashboard.

#### Scenario: sesión glitch

- **WHEN** una sesión contiene un bloque assistant de más de 500k chars
- **THEN** queda excluida de los percentiles y `excluded_pathological` la cuenta,
  con la sesión listable en el dashboard

### Requirement: agregados mensuales por modelo

`context_metrics.monthly_model[]` SHALL (debe) incluir, por mes × modelo: conteo de
sesiones, p50 y p90 del pico de contexto, % de sesiones >100k, tok/turno por fuente
y spend por componente (in/out/cache).

#### Scenario: tabla mes x modelo

- **WHEN** el reporte cubre sesiones de dos modelos en un mismo mes
- **THEN** el mes tiene dos filas en `monthly_model`, cada una con sus percentiles
  y growth per turno por fuente
