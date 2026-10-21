# usage-report Specification

## Purpose
Contrato del dataset de uso de IA (`data/usage_report_v3.json`) que produce
`scripts/usage-tracker.py` a partir de los logs locales (Claude, Pi, Amp,
Codex, Gemini): agregaciones mensuales/diarias por tool, modelo y proyecto,
sesiones multi-tool, cash real desde el ledger `data/charges.json`, energía
estimada y métricas de contexto. Es la única fuente de los dashboards
(`viz-fpa.py`, `viz-gantt.py`, `viz-ctx.py`, `viz_ddl.py`); el schema
contractual vive en `specs/usage-report-v3.schema.json`.

## Requirements

### Requirement: emisiones para dashboard FP&A

El tracker SHALL (debe) emitir, además de lo existente: breakdown mensual por
tool y modelo, project totals por mes vía `project_daily`, cargas pay-per-token
separadas de suscripciones, commits y releases por proyecto/mes (donde haya
token de GitHub), tokens input/output/cache-read/cache-write por mes y modelo,
interacciones por kind, interacciones y coste de los excluidos por el
charly-filter, timezone de bucketing y métricas de concurrencia.

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

- **WHEN** se revisa el breakdown mensual por tool y modelo
- **THEN** cada celda trae interacciones y coste efectivo

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

### Requirement: cash real desde el ledger de cargos (CRG-F1)

El tracker SHALL (debe) consumir el ledger `data/charges.json` y emitir de
forma aditiva `charges_real_by_month`, `charges_reconciliation_by_month`
(insumo de FPA-082) y metadata de source/total/provenance. `pay_per_token_charges`
mensual SHALL (debe) ser *reported* con cargas p2p reales del ledger (FPA-013
deja de ser assumed) y *assumed* si no; los fees implícitos del calendario NUNCA
se mezclan ahí. Los cargos del ledger son cash cost: jamás sumados al coste
efectivo (FPA-002).

#### Scenario: emisión aditiva del ledger

- **WHEN** el tracker genera el reporte con el ledger presente
- **THEN** `charges_real_by_month` es proveedor → mes → USD solo kinds IA con
  provenance *reported*, y la metadata registra `charges_source`,
  `charges_total_real` y `charges_provenance`

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

El tracker SHALL (debe) emitir por mes `energy_kwh_by_model` (modelo → kWh con
3 decimales, o `{"kwh": null, "reason": ...}` solo para modelos sin telemetría
de tokens) y `energy_kwh` (total mensual), calculados como
`(tokens_frescos + cache_read × cache_read_energy_factor) × J/token del tier /
3.6e6`. Los campos son aditivos y jamás se mezclan con los costes USD (FPA-002).

#### Scenario: energía mensual por modelo presente

- **WHEN** el tracker genera `usage_report_v3.json` con el bloque
  `energy_coefficients` en config
- **THEN** cada mes incluye `energy_kwh_by_model` y `energy_kwh`, y la metadata
  incluye `energy_provenance` = "assumed", `energy_cache_read_factor` y
  `energy_method`, validables contra el schema extendido

#### Scenario: provenance siempre assumed y tier auditable

- **WHEN** se revisa la metadata y las entradas de `energy_kwh_by_model`
- **THEN** `energy_provenance` es siempre "assumed" (ningún proveedor reporta
  energía), la metadata incluye `energy_config_source`, y cada modelo registra
  su `energy_tier` efectivo para auditar la clasificación

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

`config/fpa.json` SHALL (debe) tener un bloque `energy_coefficients`: tiers de
J/token entregado (mínimo `flash`, `mid`, `frontier`), mapping modelo→tier,
`default_tier`, `cache_read_energy_factor` y versionado por `effective`. Si
falta o está malformado, el tracker SHALL (debe) abortar con error no-cero
nombrando el campo (coffe-mbz).

#### Scenario: config válida

- **WHEN** el tracker corre con `energy_coefficients` completo
- **THEN** cada bucket mensual usa la versión con `effective` más reciente ≤
  primer día del mes (mismo patrón que `model_pricing.versions`) y el reporte
  registra `energy_config_source`

#### Scenario: variantes de nombre cubiertas

- **WHEN** el dataset trae variantes de nombre (`google/…`, `anthropic/…`,
  `:free`, `<synthetic>`)
- **THEN** el mapping las cubre todas; un modelo sin mapping usa `default_tier`
  con su `energy_tier` registrado (sin defaults silenciosos)

#### Scenario: un mes, una versión

- **WHEN** cambia la versión de coeficientes a mitad de un bucket mensual
- **THEN** el mes entero usa la versión vigente al primer día (el agregado
  mensual de tokens no permite partir el mes; a diferencia de
  `estimate_cost(when)`, que es por interacción)

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

- **WHEN** se ejecuta `python3 scripts/viz-gantt.py --validate`
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
kWh recomputados con `cache_read_energy_factor` 0.0 y 1.0, mismos buckets,
coeficientes versionados y `energy_version` que la emisión nominal de 7mj.1.
La metadata SHALL (debe) registrar `energy_band_cache_factors: [0.0, 1.0]`.

#### Scenario: extremos de banda coherentes con el nominal

- **WHEN** un mes tiene tokens con cache_read > 0 y telemetría completa
- **THEN** `energy_kwh_band.low ≤ energy_kwh ≤ energy_kwh_band.high` y los
  extremos coinciden con recomputar el método 7mj con factor 0.0 y 1.0

#### Scenario: mes solo con modelos sin telemetría

- **WHEN** un mes solo tiene modelos sin telemetría de tokens (p.ej. amp)
- **THEN** `energy_kwh_band` es `{low: 0.0, high: 0.0}` (igual que su
  `energy_kwh` 0.0) y las entradas por modelo conservan `null` con razón

#### Scenario: banda replica la semántica nominal

- **WHEN** un mes tiene modelos sin telemetría junto a modelos con tokens
- **THEN** los sin telemetría quedan excluidos de ambos extremos, igual que en
  el total nominal

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

### Requirement: sección context_metrics por sesión de Pi

El reporte SHALL (debe) incluir una sección `context_metrics` con una fila por
sesión de Pi in-scope, conteniendo como mínimo `month`, `project`, `model`,
`models_turns`, `provider`, `turns`, `peak_ctx_est`, `final_ctx_est`,
`compactions`, la atribución de chars por fuente, `big_results` por tool y
`cost_in/out/cr/cw`. La emisión es aditiva: ningún campo existente cambia de
semántica.

#### Scenario: sesión con atribución completa

- **WHEN** el tracker procesa una sesión de Pi con turnos de assistant, tool
  results y compaction
- **THEN** la fila de la sesión tiene el pico de contexto estimado, la atribución
  por fuente que suma al transcript observado y `compactions >= 1`

#### Scenario: atribución coherente con el transcript

- **WHEN** se suma la atribución por fuente de una sesión
- **THEN** el total es la suma de chars de user + assistant content + toolResults
  observados en el JSONL, sin duplicar ni omitir roles

#### Scenario: fuentes de la atribución

- **WHEN** se desglosa la atribución de chars por fuente
- **THEN** cubre `asst_text`, `asst_thinking`, `asst_args` y los resultados por
  `toolName`

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

### Requirement: granularidad proyecto × día con coste y tokens

El tracker SHALL (debe) emitir en `project_daily_spend[proj][day]` el coste
efectivo, el coste real (misma semántica que los buckets horarios/diarios/
mensuales) y los tokens `in`/`out`/`cache_read`/`cache_write` por proyecto
y día, con agregación pura e inyectable y emisión sparse igual que
`project_daily`.

#### Scenario: proyecto con gasto en un día

- **WHEN** una sesión del proyecto registra turnos con telemetría en un día
- **THEN** `project_daily_spend[proj][day]` suma el coste y los tokens de
  esas interacciones, y la suma sobre todos los proyectos del día cuadra con
  el bucket diario global (`daily[day].cost_effective`)

#### Scenario: día sin actividad en un proyecto

- **WHEN** un proyecto no tiene interacciones en un día
- **THEN** no existe la entrada `project_daily_spend[proj][day]`

#### Scenario: rollup mensual cuadra con project_monthly

- **WHEN** se suman los días de `project_daily_spend[proj]` de un mes
- **THEN** los tokens y el coste efectivo del mes cuadran con
  `project_monthly[proj]` (FPA-012) para ese proyecto

### Requirement: emisión aditiva de granularidades nuevas

La emisión de `project_daily_spend` y `session_spend` SHALL (debe) ser
aditiva: ningún campo existente de `project_daily` o `project_monthly`
cambia de semántica.

#### Scenario: el golden existente no cambia

- **WHEN** se regenera el golden con `REGEN_GOLDEN=1`
- **THEN** el diff solo muestra las claves nuevas; las cifras existentes son
  byte a byte idénticas

### Requirement: filas por sesión con coste y tokens

El tracker SHALL (debe) emitir en `session_spend` una fila por sesión
in-scope con, como mínimo: `session_id`, `project`, `day` (día local de la
primera interacción), `source` (tool proveedor), `model` dominante (o
`mixed` si cambió de modelo, ver atribución de turnos por modelo),
`cost_effective`, `cost_real`, `tokens` con `in`/`out`/`cache_read`/
`cache_write`, y `n_interactions`.

#### Scenario: sesión con coste completo

- **WHEN** el tracker procesa una sesión con telemetría `usage.cost` que no
  cruza medianoche
- **THEN** la fila trae coste y tokens consistentes con la suma de sus
  interacciones, y la suma de `session_spend` del día cuadra con
  `project_daily_spend` y con el bucket diario global

#### Scenario: sesión sin identificador nativo

- **WHEN** el log de una sesión no trae identificador de sesión nativo
- **THEN** la fila se emite igual con un `session_id` de reserva derivado de
  la ruta del archivo (hash), marcando `session_id_fallback: true`

### Requirement: coste ausente en filas de sesión es null con razón

Cuando el coste no esté disponible para una sesión (turnos abortados o
proveedor sin billing por token), los campos de coste de su fila en
`session_spend` SHALL (debe) ser `null` con una `cost_reason` en la fila —
mismo patrón "null con razón" del coste por sesión existente, reutilizando
las mismas cadenas de razón del tracker — nunca 0 fake.

#### Scenario: sesión sin telemetría de coste

- **WHEN** una sesión no tiene `usage.cost` en ningún turno
- **THEN** los campos de coste son `null` con `cost_reason`, y el dashboard
  muestra la razón en vez de un cero silencioso

### Requirement: sesión que cruza medianoche

La fila de sesión SHALL (debe) atribuirse al día local de su primera
interacción; los buckets diarios reparten por interacción, por lo que la
asimetría se documenta.

#### Scenario: sesión abarca dos días

- **WHEN** una sesión abarca dos días locales
- **THEN** la fila se atribuye al día de su primera interacción, la suma
  `session_spend` del día difiere de `project_daily_spend` exactamente en
  las sesiones que cruzan medianoche, y esta asimetría se documenta en
  `README.md`

### Requirement: schema y golden actualizados

El schema contractual `specs/usage-report-v3.schema.json` SHALL (debe)
incluir `project_daily_spend` y `session_spend` con sus campos obligatorios
(`session_id`, `project`, `day`), y el golden snapshot SHALL (debe)
regenerarse de forma que el diff solo contenga los campos nuevos.

#### Scenario: validación del reporte regenerado

- **WHEN** se ejecuta `--validate` (jsonschema) contra un reporte con las
  secciones nuevas
- **THEN** valida sin errores, y un reporte sin `project_daily_spend` o
  `session_spend` falla la validación

### Requirement: semántica de día heredada

El día de `project_daily_spend` y de `session_spend` SHALL (debe) usar la
misma TZ local de extracción (`LOCAL_TZ`) que los buckets diarios
existentes, heredando su advertencia documentada de que cambiar de máquina
de extracción desplaza los límites de día.

#### Scenario: sesión cerca de medianoche

- **WHEN** una interacción cae a segundos del límite de día local
- **THEN** se bucketea con la misma TZ que `daily`, sin conversión adicional
