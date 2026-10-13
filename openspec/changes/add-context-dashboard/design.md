# design — add-context-dashboard

## Decisión 1: página nueva vs vista de fpa-dashboard

**Elegido: página nueva** (`data/context-dashboard.html`), precedido por el Gantt.

- fpa-dashboard ya tiene 31 requirements y 6 vistas; las métricas de contexto son
  Pi-only, por-sesión y con serie por turno — granularidad distinta a la de
  interacciones. Meterlas en fpa añadiría una 7ª vista con modelo de datos propio.
- El repo ya tiene el patrón "viz secundaria que lee el mismo reporte"
  (`viz-gantt.py`), así que la página nueva cuesta poco y no agranda el monolito.
- La investigación que motiva esto es Pi-only (Claude/Amp/Codex no participaron);
  extender a otros extractores es un cambio futuro explícito, no un default.

## Decisión 2: dónde vive el cálculo

**Elegido: el tracker emite, la viz solo renderiza.**

- El tracker ya camina las sesiones de Pi y lee `usage` por mensaje de assistant
  (v4.1 cuenta cache). El cálculo de pico/atribución es un reducer **puro e
  inyectable** — `reduce_context(entries) -> metrics`, mismo estilo que
  `aggregate()`: no toca disco, se testea con líneas JSONL sintéticas — y el
  extractor lo alimenta con la caminata por archivo que ya hace
  (`extract_pi_sessions`, coffe-i31: 1 archivo JSONL = 1 sesión).
- `viz-ctx.py` pre-calcula en Python (histogramas, shares, pendientes por regresión
  lineal simple por modelo) y el JS solo re-escala — mismo patrón que FPA-145.
- La estimación de contexto es `chars / chars_per_token` acumulado por turno sobre
  el transcript (user + assistant content + toolResult). El factor vive en `meta`
  con tag *assumed* (default 4). Dirección de bias documentada: subestima el
  contexto real (español/code ~3 chars/token, más overhead de serialización).

### Pico por segmento de compactación (review R5, CORR-001)

Los archivos de sesión de Pi **retienen todos los mensajes previos** a la
compactación (verificado: 12 entradas `type=="compaction"` con los 36 mensajes
completos en el archivo). Un pico calculado como máximo del acumulador continuo
sobreestima el contexto real post-compactación. Por eso:

- el acumulador **se resetea** en cada entrada `type=="compaction"`;
- `peak_ctx_est` = máximo del acumulador **dentro del segmento** de mayor uso
  (entre compactaciones), no del archivo completo;
- se emiten además `compactions` (conteo) y `segment_count` si el segmento difiere.

### Atribución por modelo (review R5, CORR-002)

Las sesiones contienen `model_change` (66 entradas en 40 archivos muestreados):
un solo modelo por fila es insuficiente. Se emite:

- `models_turns`: mapa modelo → turnos de esa sesión (fuente de verdad);
- `model`: dominante por conteo de turnos (para agregados `monthly_model`);
- `monthly_model` agrega por (mes, modelo dominante) — sesiones mixtas quedan
  visibles vía `models_turns` y pueden filtrarse en el dashboard.

## Decisión 3: claude-cli y patologías — visibles, no silenciosas

- **claude-cli**: su `usage` es inválido (cacheRead no monótono: 34k→766k→171k) y su
  coste es $0 (suscripción). El emisor marca esas sesiones con
  `context_source: "claude-cli"` y el dashboard las muestra con provenancia propia
  ("estimated", "subscription") — nunca mezcladas en percentiles de coste.
- **Coste faltante**: cuando `usage.cost` no existe (turnos abortados, proveedor sin
  billing por token) se emite `null` con razón — mismo patrón que "modelos sin
  telemetría de tokens emiten null con razón" (`usage-report`), no 0 fake.
- **Glitches**: sesiones con un bloque assistant > umbral de config
  (`context_thresholds.pathological_chars`, default 500k) se cuentan en
  `meta.excluded_pathological` y se listan en la vista Patologías; los percentiles
  se calculan sobre el set limpio, con el conteo visible.

## Decisión 4: crecimiento por turno como vista propia

El hallazgo central de la investigación (pico ≈ turnos × ~850 tok en todas las eras)
merece vista propia: dispersivo pico vs turnos, una serie por modelo, pendiente
pre-calculada en Python (OLS sobre sesiones limpias). Es la cifra que traduce
higiene de sesiones a presupuesto.

## Decisión 5: nueva granularidad por sesión en el reporte (review R5, DRAFT-001)

El reporte hoy es agregado (la sección `sessions` es un dict de totales/promedios).
`context_metrics.sessions[]` introduce **filas por sesión** (~1.8k hoy, crece ~10/día):

- tamaño asumido: <1MB JSON — aceptable en el reporte (ya es 1.0MB);
- aditivo: ninguna sección existente cambia; el schema declara el array nuevo;
- la granularidad por sesión es lo que permite percentiles, ley de crecimiento y
  detección de patologías — los agregados no alcanzan.

## Trade-offs aceptados

- chars/4 subestima ~40% el contexto real (validado contra usage de proveedores
  confiables; ratio persistido en `meta.validation_ratio_median`). Se acepta porque
  es consistente entre eras y es la única métrica robusta cuando el proveedor
  miente; el ratio y su dirección se documentan en la guía.
- Coste por sesión usa los `cost.*` del JSONL tal cual (estimación local de Pi):
  comparaciones cross-provider son aproximadas; se marca en provenancia.
- Sesiones sin turnos (stubs, ~27% del corpus hoy) se excluyen de los percentiles
  (`turns > 0`) y se cuentan en `meta.stubs` — nunca sumadas como 0 (patrón
  "loss visible").
- El walk extra sobre el dir de sesiones (~1.4GB) agrega duración al nightly: se
  mide en el task de integración y, si pesa, se limita con la ventana `--since`
  ya existente.
