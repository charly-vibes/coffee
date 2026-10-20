# usage-report Specification

## Purpose
TBD - created by archiving change add-fpa-dashboard. Update Purpose after archive.

## Requirements

### Requirement: emisiones para dashboard FP&A

El tracker SHALL (debe) emitir, además de lo existente: breakdown mensual por tool
y modelo (interacciones y coste efectivo), project totals por mes vía
`project_daily`, cargas pay-per-token separadas de las cargas de suscripción,
commits y releases por proyecto/mes donde haya token de GitHub configurado, tokens
input/output/cache-read/cache-write por mes y modelo, interacciones por kind
(user prompts, assistant turns, tool calls), interacciones y coste de los proyectos
excluidos por el charly-filter, timezone usada para el bucketing horario/diario, y
las métricas de concurrencia (proyectos distintos por hora, picos de sesiones
simultáneas, project switches por hora activa) para el dashboard FP&A.

Desde coffe-a31.2 (CRG-F1) el tracker SHALL (debe) consumir el ledger de cargos
reales `data/charges.json` y emitirlo de forma aditiva: `charges_real_by_month`
(proveedor → mes → USD, solo kinds IA, provenance *reported*),
`charges_reconciliation_by_month` (por mes y tool: cash real del ledger vs fee
implícito del calendario vs efectivo estimado — insumo de la reconciliación
FPA-082) y metadata `charges_source`/`charges_total_real`/`charges_provenance`.
El campo `pay_per_token_charges` mensual SHALL (debe) ser *reported* cuando el
ledger tiene cargas pay-per-token reales del mes (créditos/reembolsos — FPA-013
deja de ser assumed) y *assumed* (estimado tokens × pricing) si no; los fees
implícitos del calendario NUNCA se mezclan en ese campo. Los cargos del ledger
son cash cost: medida separada del coste efectivo, jamás sumadas (FPA-002).

#### Scenario: tokens por modelo presentes

- **WHEN** el tracker genera `usage_report_v3.json`
- **THEN** el reporte incluye tokens input/output/cache-read/cache-write por mes
  y modelo, validables contra el schema extendido

#### Scenario: interacciones por kind presentes

- **WHEN** el tracker genera el reporte
- **THEN** cada mes incluye interacciones desglosadas por kind, permitiendo
  calcular el share de tool-calls (FPA-140)

#### Scenario: share del filtro visible

- **WHEN** el charly-filter excluye proyectos
- **THEN** el reporte incluye interacciones y coste de los excluidos para calcular
  el share filtrado (FPA-141)

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

### Requirement: ledger de cargos reales

El repo SHALL (debe) mantener `data/charges.json` como única fuente de cargos
reales (facturas de proveedores, provenance *reported*): un objeto por
proveedor con entradas `{date, amount, kind, note}` en USD, donde kind es
`subscription | credits | refund | api_cycle` (factura de ciclo $0). Los
cargos de storage (p.ej. Google One) SHALL (debe) quedar fuera del ledger —
son gasto de storage, no de IA. Un `refund` SHALL (debe) ser el único kind
con amount negativo.

#### Scenario: kinds del vocabulario IA

- **WHEN** se valida el ledger
- **THEN** toda entrada tiene kind `subscription|credits|refund|api_cycle`,
  fecha ISO, amount numérico y note (FPA-008: nunca vacío sin razón)

#### Scenario: totales IA verificables

- **WHEN** se suman los amounts del ledger por proveedor
- **THEN** claude-cli $451.78, codex $40.00, amp $275.00, gemini-cli
  $119.94, openrouter $470.00 — IA $1,356.72 en total (transcripción
  2026-09-20, guardada en tests/test_charges.py)

### Requirement: estimación de energía por modelo (assumed)

El tracker SHALL (debe) emitir por mes `energy_kwh_by_model` (modelo → kWh
estimado como número de 3 decimales, o — solo para modelos sin telemetría de
tokens, ver requirement siguiente — `{"kwh": null, "reason": ...}`) y
`energy_kwh` (total mensual), calculados como
`(tokens_frescos + cache_read × cache_read_energy_factor) × J/token del tier /
3.6e6`. La metadata SHALL (debe) incluir `energy_provenance` (siempre
"assumed" — ningún proveedor reporta energía), `energy_config_source`,
`energy_cache_read_factor` y `energy_method` con la fórmula documentada. Los
campos son aditivos al reporte existente y jamás se mezclan con los costes USD
(FPA-002). Cada modelo SHALL (debe) registrar su `energy_tier` efectivo dentro
de `energy_kwh_by_model` para que la clasificación sea auditable.

#### Scenario: energía mensual por modelo presente

- **WHEN** el tracker genera `usage_report_v3.json` con el bloque
  `energy_coefficients` en config
- **THEN** cada mes incluye `energy_kwh_by_model` y `energy_kwh`, y la metadata
  incluye `energy_provenance` = "assumed", `energy_cache_read_factor` y
  `energy_method`, validables contra el schema extendido

#### Scenario: cache_read ponderado

- **WHEN** un modelo acumula cache_read (p.ej. claude-sonnet-4-6: 87% de sus
  tokens son cache) y `cache_read_energy_factor` = 0.10
- **THEN** solo el 10% de los tokens cache_read entra al cómputo con el coeficiente
  del tier, y el factor usado queda registrado en metadata

#### Scenario: energía nunca es coste

- **WHEN** se compara `energy_kwh` con `cost_effective` del mes
- **THEN** son campos independientes en el reporte (unidades distintas, jamás
  sumados ni convertidos entre sí)

### Requirement: coeficientes de energía versionados y fail-loud en config

`config/fpa.json` SHALL (debe) tener un bloque `energy_coefficients` con:
tiers de J/token entregado (mínimo `flash`, `mid`, `frontier`), mapping
modelo→tier (incluyendo todas las variantes de nombre presentes en el dataset:
`google/…`, `anthropic/…`, `:free`, `<synthetic>`), `default_tier`,
`cache_read_energy_factor` y versionado por `effective` (mismo patrón que
`model_pricing.versions`). Si el bloque falta o está malformado, el tracker
SHALL (debe) abortar con error no-cero listando el campo faltante (criterio
coffe-mbz). Un modelo sin mapping SHALL (debe) usar `default_tier` y su tier
efectivo SHALL (debe) quedar registrado como `energy_tier` en el reporte —
sin defaults silenciosos invisibles. La versión de coeficientes para un bucket
mensual SHALL (debe) seleccionarse por la fecha de inicio del mes (última
`effective` ≤ primer día del mes) — el agregado mensual de tokens no permite
partir un mes entre versiones (a diferencia de `estimate_cost(when)`, que es
por interacción). Un mes que arranque antes que toda versión declarada SHALL
(debe) abortar con error no-cero: el autor de config cubre el rango de datos.

#### Scenario: config válida

- **WHEN** el tracker corre con `energy_coefficients` completo
- **THEN** cada bucket mensual usa la versión con `effective` más reciente ≤
  primer día del mes y el reporte registra `energy_config_source`

#### Scenario: mes sin cobertura de versión

- **WHEN** el primer día de un bucket mensual es anterior a toda `effective`
  declarada
- **THEN** el tracker aborta con error no-cero nombrando el mes, sin escribir
  el reporte

#### Scenario: config malformada aborta

- **WHEN** falta `cache_read_energy_factor` o un tier declarado
- **THEN** el tracker aborta con error no-cero nombrando el campo, sin escribir
  el reporte

#### Scenario: modelo nuevo sin mapping

- **WHEN** aparece un modelo no mapeado (p.ej. un lanzamiento futuro)
- **THEN** se le aplica `default_tier` y su `energy_tier` en el reporte lo
  delata para auditar y mapear explícitamente

### Requirement: modelos sin telemetría de tokens emiten null con razón

Para modelos con interacciones pero tokens en cero, el tracker SHALL (debe) emitir `null` con razón en vez de 0: en `tokens_by_model` (p.ej.
`amp`, `<synthetic>`), `energy_kwh_by_model` SHALL (debe) emitir `null` con
`reason` "sin telemetría de tokens" — nunca 0, que sugeriría medición (FPA-008).

#### Scenario: modelo sin tokens

- **WHEN** el reporte incluye un modelo con interacciones > 0 y tokens = 0
- **THEN** su entrada en `energy_kwh_by_model` es `{"kwh": null, "reason":
  "sin telemetría de tokens"}` y el total mensual `energy_kwh` suma solo los
  modelos con datos

### Requirement: Report contract

El reporte `usage_report_v3.json` SHALL (debe) conformar al JSON Schema
`specs/usage-report-v3.schema.json`. Los viz scripts SHALL (debe)N poder validar
el reporte antes de renderizar (`--validate`).

#### Scenario: reporte válido pasa la validación

- **WHEN** se ejecuta `python3 scripts/viz-dashboard.py --validate`
- **THEN** el reporte satisface el schema y el script imprime `schema OK`

#### Scenario: clave requerida ausente falla la validación

- **WHEN** un reporte sin `project_daily.matrix` se valida
- **THEN** `jsonschema.validate` lanza `ValidationError`

### Requirement: agregación pura e inyectable

`aggregate()` NO SHALL (debe) leer archivos del disco; skills y comandos se
inyectan como parámetros (colección en `collect_skills_and_commands()`).

#### Scenario: aggregate con rows sintéticos es determinista

- **WHEN** `aggregate(rows_sintéticas, sessions_sintéticas)` corre dos veces
- **THEN** el output es idéntico byte a byte (golden test)

### Requirement: loss visible, no silenciosa

Las líneas de logs descartadas por errores de parseo SHALL (debe)N contarse en
`metadata.skipped_lines` (y detalle por archivo en `metadata.skipped`).

#### Scenario: archivo corrupto no desaparece sin rastro

- **WHEN** un JSONL tiene una línea corrupta
- **THEN** el extractor la salta y `metadata.skipped_lines` la refleja

### Requirement: banda de sensibilidad de caché por mes

El reporte SHALL (debe) emitir `monthly[m].energy_kwh_band: {low, high}` —
kWh estimados recomputados con `cache_read_energy_factor` 0.0 y 1.0
respectivamente, mismos buckets, coeficientes versionados y `energy_version`
que la emisión nominal de 7mj.1. La banda por modelo replica la semántica
nominal: modelos sin telemetría quedan excluidos de ambos extremos (igual que
del total nominal), así un mes cuyo total es 0.0 (solo modelos sin telemetría)
emite banda 0.0/0.0. La metadata SHALL (debe) registrar
`energy_band_cache_factors: [0.0, 1.0]`.

#### Scenario: extremos de banda coherentes con el nominal

- **WHEN** un mes tiene tokens con cache_read > 0 y telemetría completa
- **THEN** `energy_kwh_band.low ≤ energy_kwh ≤ energy_kwh_band.high` y los
  extremos coinciden con recomputar el método 7mj con factor 0.0 y 1.0

#### Scenario: mes solo con modelos sin telemetría

- **WHEN** un mes solo tiene modelos sin telemetría de tokens (p.ej. amp)
- **THEN** `energy_kwh_band` es `{low: 0.0, high: 0.0}` (igual que su
  `energy_kwh` 0.0) y las entradas por modelo conservan `null` con razón

### Requirement: granularidad proyecto × mes con tokens y cost_real

El tracker SHALL (debe) emitir en `project_monthly[proj][m]`, junto a las
cifras existentes de `interactions` y `cost_effective` (FPA-012), los campos:
`cost_real` (USD *assumed* vía sub_cost, misma semántica que los buckets
horarios/diarios/mensuales) y `tokens` con `in`/`out`/`cache_read`/
`cache_write` (misma semántica que `tokens_by_model` en `month_models`).
La emisión SHALL (debe) ser aditiva: ningún campo existente de
`project_monthly` cambia de semántica.

#### Scenario: proyecto con tokens y cost_real por mes

- **WHEN** el tracker genera el reporte
- **THEN** cada entrada `project_monthly[proj][m]` incluye `cost_real` y
  `tokens` (`in`/`out`/`cache_read`/`cache_write`) consistentes con la suma
  de sus interacciones

#### Scenario: emisión aditiva no rompe el golden existente

- **WHEN** se regenera el golden con `REGEN_GOLDEN=1`
- **THEN** el diff solo muestra los campos nuevos; las cifras existentes son
  byte a byte idénticas

### Requirement: granularidad proyecto × día fuera de alcance

El tracker NO SHALL (debe) emitir coste efectivo ni tokens por proyecto y día
en esta emisión; la granularidad proyecto × mes es la de la vista del dashboard.
La actividad por proyecto/día sigue cubierta por `project_daily` (Gantt).

#### Scenario: sin coste diario por proyecto

- **WHEN** se inspecciona el reporte en busca de coste por proyecto y día
- **THEN** no existe tal campo; solo `project_monthly` y los buckets globales

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

### Requirement: métricas de sesión con señal de compaction para Pi

La sección `sessions` del reporte SHALL (debe) computar `total_compactions` desde
las entradas `type=="compaction"` de los JSONL de Pi. La nota vigente de "sin señal
en pi (compactions)" (`scripts/usage-tracker.py`) SHALL (debe) actualizarse: la
señal existe en los archivos desde ~may-2026 y el emisor la consume.

#### Scenario: total de compactions refleja los JSONL

- **WHEN** un archivo de sesión de Pi contiene entradas `type=="compaction"`
- **THEN** `sessions.total_compactions` las cuenta y el docstring ya no declara
  "sin señal" para compactions
