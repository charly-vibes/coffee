# design — add-context-dashboard

## Decisión 1: página nueva vs vista de fpa-dashboard

**Elegido: página nueva** (`data/context-dashboard.html`), precedido por el Gantt.

- fpa-dashboard ya tiene 31 requirements y 6 vistas; las métricas de contexto son
  Pi-only, por-sesión y con serie por turno — granularidad distinta a la de
  interacciones. Meterlas en fpa añadiría una 7ª vista con modelo de datos propio.
- El repo ya tiene el patrón "viz secundaria que lee el mismo reporte"
  (`viz-gantt.py`), así que la página nueva cuesta poco y no agranda el monolito.

## Decisión 2: dónde vive el cálculo

**Elegido: el tracker emite, la viz solo renderiza.**

- El tracker ya camina las sesiones de Pi y lee `usage` por mensaje de assistant
  (v4.1 cuenta cache). El cálculo de pico/atribución es un `reduce` sobre esa misma
  caminata: emisión aditiva, sin segunda lectura de disco.
- `viz-ctx.py` pre-calcula en Python (histogramas, shares, pendientes por regresión
  lineal simple por modelo) y el JS solo re-escala — mismo patrón que FPA-145.
- La estimación de contexto es `chars / 4` acumulado por turno sobre el transcript
  (user + assistant content + toolResult). El factor vive en `meta` con tag
  *assumed*, y el reporte guarda también el estimado validado contra `usage` cuando
  el proveedor es confiable (ratio mediano ~1.4 documentado).

## Decisión 3: claude-cli y patologías — visibles, no silenciosas

- **claude-cli**: su `usage` es inválido (cacheRead no monótono: 34k→766k→171k) y su
  coste es $0 (suscripción). El emisor marca esas sesiones con
  `context_source: "claude-cli"` y el dashboard las muestra con provenancia propia
  ("estimated", "subscription") — nunca mezcladas en percentiles de coste.
- **Glitches**: sesiones con un bloque assistant >500k chars (DSML roto de DeepSeek)
  se cuentan en `meta.excluded_pathological` y se listan en la vista Patologías;
  los percentiles se calculan sobre el set limpio, con el conteo visible.

## Decisión 4: crecimiento por turno como vista propia

El hallazgo central de la investigación (pico ≈ turnos × ~850 tok en todas las eras)
merece vista propia: dispersivo pico vs turnos, una serie por modelo, pendiente
pre-calculada en Python (OLS sobre sesiones limpias). Es la cifra que traduce
higiene de sesiones a presupuesto.

## Trade-offs aceptados

- chars/4 subestima ~40% el contexto real (validado contra usage de proveedores
  confiables). Se acepta porque es consistente entre eras y es la única métrica
  robusta cuando el proveedor miente; el ratio se documenta en la guía.
- Coste por sesión usa los `cost.*` del JSONL tal cual (estimación local de Pi):
  comparaciones cross-provider son aproximadas; se marca en provenancia.
