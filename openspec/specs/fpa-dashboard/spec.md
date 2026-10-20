# fpa-dashboard Specification

## Purpose
Dashboard FP&A de uso y costos de IA (`data/fpa-dashboard.html`, generado por
`scripts/viz-fpa.py`): cinco vistas por pregunta (Summary, Cost, Breakdown,
Habits, Outlook) con resumen ejecutivo, árboles con roll-up, KPIs, presupuesto
con varianza, bridge precio-volumen-mix, forecast con escenarios, alertas con
evidencia, economía de suscripción, patrones de uso, energía estimada y guía
de análisis marginalia. Generador Python stdlib-only que produce un único HTML
autocontenido sobre el dataset del contrato `usage-report`, en el tema
compartido del sitio, con grounding y check-docs de sus cifras.
## Requirements
### Requirement: Generador stdlib-only autocontenido

El Dashboard SHALL (debe) generarse con un script Python stdlib-only
(`scripts/viz-fpa.py`) como un único archivo HTML autocontenido con SVG inline.

#### Scenario: HTML sin dependencias externas

- **WHEN** se genera el dashboard y se inspecciona el HTML
- **THEN** no hay `<script src>` ni `<link href>` externos y los charts son SVG inline

### Requirement: separación de coste efectivo y cash

El Dashboard SHALL (debe) mostrar coste efectivo y cash cost como medidas separadas
y nunca sumarlas.

#### Scenario: totales por separado

- **WHEN** se muestra cualquier total agregado de coste
- **THEN** efectivo y cash aparecen como cifras distintas, sin suma combinada

### Requirement: provenance en toda figura

Toda figura SHALL (debe) llevar un tag de procedencia: *reported* (del JSON) o
*assumed* (de la configuración).

#### Scenario: cifra asumida marcada

- **WHEN** se renderiza una cifra derivada de config (p.ej. precio de plan)
- **THEN** lleva el tag *assumed* junto al valor

### Requirement: meses parciales marcados

Cuando la última fecha del reporte cae antes del fin de su mes, el Dashboard SHALL
(debe) marcar ese mes como parcial y mostrar los días transcurridos sobre el total.

#### Scenario: reporte a medio mes

- **WHEN** el reporte termina el 10 de junio
- **THEN** junio aparece como parcial con `10/30` días y sus comparaciones usan
  daily rates (FPA-041)

### Requirement: resumen ejecutivo con fallback n/a

La banda de KPIs SHALL (debe) ser el resumen ejecutivo: 3–5 cifras KPI con
interpretación de una línea derivada de los datos (anatomía label → cifra →
delta → contexto). Si una cifra no puede computarse SHALL (debe) mostrar
"n/a" con la razón, nunca un resumen vacío. Un periodo sin datos SHALL (debe)
excluirse de trends y coste unitario, marcado "n/a" (FPA-017). No SHALL (debe)
renderizarse un bloque separado de headline cards que duplique la banda.

#### Scenario: headline sin datos

- **WHEN** una métrica headline carece de datos (p.ej. sin commits)
- **THEN** el card KPI muestra "n/a" con la razón en lugar de vacío o cero

#### Scenario: cifra en un solo card

- **WHEN** se renderiza la vista Summary
- **THEN** cada cifra KPI aparece en exactamente un card; los claims
  narrativos conservan sus cifras (no son cards)

### Requirement: claims con métrica y threshold visibles

El Dashboard SHALL (debe) mostrar un claim narrativo solo si se computa de una
métrica nombrada y un threshold, ambos visibles junto al claim.

#### Scenario: claim sin soporte se rechaza

- **WHEN** el generador evalúa un claim sin métrica+threshold asociados
- **THEN** el claim no se renderiza

### Requirement: árboles con roll-up verificado

El Dashboard SHALL (debe) proveer árboles expandibles Time (Año→Trimestre→Mes),
Tool (Tool→Model) y Portfolio (Categoría→Proyecto), con columnas coste, % del
total, interacciones, coste por 1k y delta vs prior. Todo padre SHALL (debe)
igualar la suma de hijos ($0.01 / 1 interacción) y los árboles y KPIs SHALL
(debe) recomputar al cambiar de periodo (FPA-026).

#### Scenario: roll-up consistente

- **WHEN** se expande un nodo padre
- **THEN** la suma de sus hijos difiere del padre en ≤ $0.01 y ≤ 1 interacción
  (verificado por test dorado FPA-101)


#### Scenario: drill de Portfolio

- **WHEN** un proyecto del árbol Portfolio tiene datos por modelo
- **THEN** el nodo se expande con drill a Model

#### Scenario: Portfolio sin project-by-month

- **WHEN** el reporte no trae datos project-by-month
- **THEN** el árbol Portfolio se limita al periodo completo del reporte con esa
  limitación visible (FPA-027)

### Requirement: KPI strip con sparkline y delta

El Dashboard SHALL (debe) calcular los KPIs del periodo seleccionado —costes
efectivo/cash con daily-rate delta, leverage, coste por 1k, coste por sesión,
concentración top-3, share premium, autonomous share, multitasking, outcome,
cache-hit rate y ratio out/in— cada uno con sparkline mensual y delta vs prior.
Con denominador cero SHALL (debe) mostrar "n/a", nunca cero o infinito. Con mes
parcial SHALL (debe) comparar daily rates (FPA-045).

#### Scenario: denominador cero

- **WHEN** un KPI tiene denominador 0 (p.ej. 0 interacciones)
- **THEN** la card muestra "n/a" con la razón


#### Scenario: KPIs de tokens excluidos por tool sin datos

- **WHEN** faltan datos de tokens para una tool
- **THEN** sus KPIs de tokens se marcan "n/a" para esa tool, se excluyen del
  cálculo y se muestra el share excluido (FPA-045)

### Requirement: presupuesto y varianza interactivos

El Dashboard SHALL (debe) aceptar presupuestos mensuales (cash, efectivo, objetivo
por 1k) aplicados desde un mes de inicio configurable, pro-ratear meses parciales,
calcular varianza con signo (positivo = over budget), marcar favorable/desfavorable
con color Y texto/símbolo, mostrar tabla mensual con YTD, y recalcular al editar un
input sin recargar la página. El presupuesto de coste efectivo SHALL (debe)
tratarse como informativo (soft): el presupuesto gestionado es el de cash cost.

#### Scenario: edición de presupuesto

- **WHEN** el usuario edita el input de presupuesto mensual
- **THEN** todas las varianzas se recalculan sin recarga de página

### Requirement: bridge precio-volumen-mix con identidad

El Dashboard SHALL (debe) mostrar un waterfall del coste efectivo del mes prior al
seleccionado, descompuesto en Volume = (Q1−Q0)×rate₀, Mix = Σ(q1ᵢ·p0ᵢ) − Q1·rate₀ y
Rate = Σ q1ᵢ·(p1ᵢ−p0ᵢ), usando rate actual como prior para modelos nuevos (solo Mix),
con identidad Volume+Mix+Rate = Δcoste dentro de $0.01, FME y etiqueta cuando haya
meses parciales, eje truncado etiquetado, y un chart 100% stacked de mix por mes.

#### Scenario: identidad del bridge

- **WHEN** se computa el bridge para dos meses cualesquiera
- **THEN** Volume + Mix + Rate = Δcoste dentro de $0.01 (test dorado FPA-101)

### Requirement: forecast con escenarios

El Dashboard SHALL (debe) proyectar el resto del año desde un run-rate base
(media FME de los últimos 3 meses; con menos de 3 meses, "n/a" con razón),
aceptar escenarios y calcular efectivo/cash con las fórmulas FPA-072/073.
SHALL (debe) mostrar YTD+outlook vs presupuesto con varianza y fila separada
para el resto del mes parcial, y distinguir actual de forecast con patrón/
label, no solo color (FPA-071).

#### Scenario: cambio de escenario

- **WHEN** el usuario ajusta el growth % del escenario
- **THEN** el forecast se recalcula sin recarga y las cifras forecast quedan
  distinguidas de las actuales por patrón/label

#### Scenario: plan default sin suscripción activa

- **WHEN** ninguna suscripción del calendario cubre la fecha de fin del
  reporte
- **THEN** el forecast arranca con "sin suscripción" (cash = p2p escalado)
  y no inventa una suscripción que no existe

### Requirement: alertas con evidencia y umbrales configurables

El Dashboard SHALL (debe) listar alertas con severity, nombre de regla y
valores de evidencia, cubriendo: verify plan (>25×), reconciliación cash real
vs cargas implícitas del calendario (FPA-082), over-budget, unit-cost +15% MoM,
shift de mix premium > 5pts en 3 meses, concentración top-3 > 50% y staleness
> 14 días. Todos los umbrales SHALL (debe) ser configurables.

#### Scenario: reconciliación de suscripción

- **WHEN** el cash real del ledger difiere de las cargas implícitas del
  calendario más allá de la tolerancia
- **THEN** se emite una alerta de reconciliación mostrando ambas cifras
  (real *reported* vs implícito *assumed*), con la corrección del
  calendario de coffe-a31 el caso motivador queda resuelto: jun 2026 real
  $0 (sin factura) vs implícito $0

#### Scenario: calendario desalineado con facturas

- **WHEN** el calendario del config afirma una suscripción que las
  facturas no respaldan (o viceversa)
- **THEN** la alerta de reconciliación lo señala con los dos montos, que es
  la señal para corregir `config/fpa.json`

### Requirement: vistas por pregunta con CTAs

El Dashboard SHALL (debe) organizarse en las vistas Summary, Cost, Breakdown,
Habits, Outlook y Data & method, cada una encabezada por la pregunta que
responde, con presets de periodo (YTD, Q1–Q3, rango custom, FPA-090),
navegación de vistas a todo ancho, un period selector único fijo, top-5 +
"Show all" en listas, máximo un CTA primario por vista y labels verb-first
≤4 palabras. La marginalia SHALL (debe) renderizar como un strip consistente
bajo el header de la vista.

#### Scenario: generador falla con CTA vacío

- **WHEN** un CTA configurado tiene target vacío
- **THEN** `viz-fpa.py` termina con exit non-zero nombrando el CTA

#### Scenario: marginalia consistente

- **WHEN** se renderiza cualquier vista con chips de marginalia
- **THEN** los chips ocupan un único strip bajo el header de la vista y no
  quedan chips flotando fuera de él


#### Scenario: nombres, idioma y Data & method

- **WHEN** se renderiza una vista
- **THEN** su nombre es el canónico en inglés renderizado en el idioma
  configurado (es), Data & method va colapsado y la marginalia no queda
  dispersa en columnas huérfanas

#### Scenario: CTAs y titles de charts

- **WHEN** se revisa una vista
- **THEN** hay ≤1 CTA primario (Summary: "Read the series") y ≤3 secundarios
  como fila compacta, los targets vienen de config con fail del generador si
  están vacíos (check activo desde que exista la clave `ctas`; obligatorio
  cuando existe), y los titles de charts son hallazgos (métrica+threshold con
  fallback al label descriptivo)

### Requirement: export y share de vista

El Dashboard SHALL (debe) ofrecer "Export CSV" en toda tabla y "Download SVG"
en todo chart (client-side, sin librerías externas) y un control "Share view"
que copia una URL con periodo, vista y expansión de árbol; al cargar, SHALL
(debe) restaurar el estado e ignorar parámetros inválidos con defaults.

#### Scenario: export de tabla

- **WHEN** el usuario activa "Export CSV" en una tabla
- **THEN** se descarga un CSV con las filas visibles

#### Scenario: URL compartida restaura estado

- **WHEN** se abre la URL con parámetros de periodo/vista/expansión válidos
- **THEN** el dashboard restaura exactamente ese estado

#### Scenario: parámetros inválidos ignorados

- **WHEN** la URL contiene parámetros inválidos
- **THEN** se ignoran y se cargan los defaults

### Requirement: patrones de uso y ciclo de vida

El Dashboard SHALL (debe) mostrar: heatmap día×hora con timezone etiquetada,
shares after-hours/weekend configurables, WoW y varianza semanal, skills y
comandos con trends, buckets de longitud de sesión con coste/mediana/p90,
`/clear` por 100 sesiones, timeline por tool/model con gaps flag, concurrencia
etiquetada, share de Agent, clasificación new/active/dormant con coste dormante
y overlay de fechas de creación de repos en el trend mensual (FPA-098).

#### Scenario: timezone ausente marca vistas

- **WHEN** el tracker no registró timezone
- **THEN** las vistas hora/día se marcan "unverified" (FPA-142)


#### Scenario: gaps flag en el timeline

- **WHEN** el timeline por tool/model tiene un gap mayor al N de días
  configurado
- **THEN** el gap se marca con flag visible (no se oculta ni interpola)

#### Scenario: concurrencia etiquetada

- **WHEN** se muestran las métricas de concurrencia
- **THEN** distinguen paralelo real de context switching (agente en paralelo vs
  humano alternando)

### Requirement: economía de suscripción

El Dashboard SHALL (debe) calcular utilización por plan (efectivo ÷ precio),
break-even mensual y headroom, comparar cash cost bajo 4 casos (actual, todo
pay-per-token, todo Pro, todo Max) y declarar que la equivalencia por coste
efectivo ignora usage limits del plan. La economía SHALL (debe) operar sobre
el calendario de suscripciones del config, corregido a las facturas
(coffe-a31), con provenance *assumed*: un panel por periodo del calendario.

#### Scenario: break-even visible

- **WHEN** se muestra el panel de un plan
- **THEN** aparecen utilización, break-even y headroom calculados del config

#### Scenario: sin suscripción activa al cierre

- **WHEN** el reporte termina después de la última factura de
  suscripción (p.ej. claude cancelado tras jun 2026)
- **THEN** el plan default del forecast es "sin suscripción" (fee $0) y
  los planes históricos del calendario siguen listables como escenarios

### Requirement: calidad de datos y documentación

El Dashboard SHALL (debe): definir "interaction" en Data y mostrar el share de
tool-calls; mostrar el share filtrado por el charly-filter; mostrar la timezone
registrada o marcar vistas hora/día como unverified; renderizar las cifras
citadas en README/docs desde el JSON o fallar en `--check-docs`; numerar
figuras y tablas sin duplicados; documentar el método de render; no renderizar
placeholders vacíos; y derivar labels de periodo decorativos del periodo real
de los datos.

#### Scenario: check-docs detecta cifra obsoleta

- **WHEN** una cifra citada en el README difiere del JSON y se corre `--check-docs`
- **THEN** el generador falla listando la cifra en desacuerdo

### Requirement: accesibilidad y mobile

El Dashboard SHALL (debe): operar todo control por teclado con focus visible;
inspeccionar todo chart por tap/focus/hover con readout persistente; touch
targets ≥ 44×44 px; primera columna fija en tablas anchas con scroll
horizontal; single-column < 600px; usar el tema compartido sin modo oscuro;
formato USD con tabular nums; skip-to-content y `lang`; retro opcional por
config, sin animación bajo `prefers-reduced-motion`, contraste ≥ 4.5:1; y
funcionar sin storage.

#### Scenario: smoke mobile 390×844

- **WHEN** el smoke test carga la página a 390×844
- **THEN** el primer viewport contiene título, period selector y primera headline,
  sin scroll horizontal ni errores de consola

#### Scenario: contraste AA bajo el tema compartido

- **WHEN** se computan los pares texto/fondo de los tokens del tema compartido
  (fg, muted, accent sobre bg silver y panel blanco)
- **THEN** el texto normal alcanza contraste ≥ 4.5:1

### Requirement: verificación del dashboard

La suite SHALL (debe) incluir: goldens de agregaciones contra fixture fijo;
verificación de roll-up de árboles e identidad del bridge; tests de mes parcial
(FME, pro-rating, daily rates); goldens de utilización/break-even/comparación
de planes; smoke Playwright de página real; test de unicidad de numeración de
figuras; test de CTAs (≤1 primario por vista, sin target vacío) y restauración
de URL share; determinismo byte-a-byte salvo timestamp; y check de consistencia
de docs en CI.

#### Scenario: doble corrida determinista

- **WHEN** el generador corre dos veces con el mismo input
- **THEN** el output es idéntico salvo la fecha de generación (FPA-104)


#### Scenario: smoke Playwright falla loud

- **WHEN** el smoke Playwright corre contra la página renderizada
- **THEN** falla ante cualquier error de consola, resumen vacío, placeholder sin
  valor o viewport mobile incompleto

### Requirement: cash cost desde el ledger de cargos reales

El Dashboard SHALL (debe) computar el cash cost como la suma de los cargos
reales del ledger `data/charges.json` (kinds IA `subscription|credits|refund`
del periodo; `api_cycle` aporta $0 y storage queda fuera). Toda cifra de cash
del ledger SHALL (debe) llevar el tag *reported*. Un proveedor sin facturas
en el periodo SHALL (debe) mostrarse "n/a" con razón (FPA-008), nunca vacío
ni cero inventado. Cash cost (ledger) y coste efectivo SHALL (debe) seguir
separados, jamás sumados (FPA-002).

#### Scenario: cash mensual del ledger

- **WHEN** se muestra el cash cost de un mes con facturas (p.ej. mayo 2026:
  claude Pro $20 + codex Plus $20 + gemini $19.99)
- **THEN** la cifra es la suma de los cargos del ledger de ese mes y lleva
  el tag *reported*

#### Scenario: proveedor sin facturas en el periodo

- **WHEN** un proveedor no registra cargos en el periodo consultado
  (p.ej. openrouter entre mar y may 2026)
- **THEN** su cash se muestra "n/a" con la razón (sin facturas en el
  periodo), no cero ni ausencia silenciosa

#### Scenario: refund descuenta

- **WHEN** el ledger incluye un refund negativo en el periodo
  (p.ej. claude-cli −$28.22 el 2026-02-19)
- **THEN** el cash del periodo lo descuenta (es dinero devuelto, no gastado)


#### Scenario: fees implícitos no son cash

- **WHEN** se revisa el calendario de suscripciones
- **THEN** sus fees implícitos no entran al cash cost: solo alimentan la
  reconciliación (FPA-082) y la economía de planes (FPA-130–133)

### Requirement: identidad visual del sitio

El Dashboard SHALL (debe) usar el tema compartido del sitio: los tokens
"90s corporate" (bg silver, acentos granate y navy, good teal, sombra dura,
Arial, terminales Courier) provistos por `scripts/site-theme.py`, único módulo
del que también toman su estilo `viz-index.py` y `viz-gantt.py`. Ningún
generador duplica definiciones divergentes; los requisitos de accesibilidad
previos SHALL seguir cumpliéndose bajo el tema; el generador SHALL ser
determinista.

#### Scenario: paridad de tokens entre generadores

- **WHEN** se generan `fpa-dashboard.html`, `index.html` y `gantt-multitasking.html`
  en la misma corrida
- **THEN** los tokens `:root` y las reglas compartidas del `<style>` provienen del
  módulo de tema y son idénticos en los tres HTML

#### Scenario: smoke visual del tema

- **WHEN** el smoke de Playwright abre `data/fpa-dashboard.html`
- **THEN** el `font-family` computado y el `background` del body corresponden al
  tema compartido, sin pageerrors

### Requirement: nomenclatura pública en lenguaje llano

Los labels visibles del sitio (headers, tabs, titles, índice, guía, README) SHALL
usar un nombre canónico en español llano, definido en una única clave de
`config/fpa.json` y consumido por todos los generadores. Los labels SHALL NOT
contener el término "FP&A" ni otra jerga corporativa no explicada. Los nombres de
archivo, IDs de requisito (FPA-xxx) y variables internas SHALL NOT cambiar.

#### Scenario: label sin jerga

- **WHEN** se generan `fpa-dashboard.html`, `index.html` y `data/fpa-guide.html`
- **THEN** ningún label visible contiene "FP&A" y el nombre mostrado proviene de
  la clave de config canónica

#### Scenario: test de nomenclatura falla loud

- **WHEN** un generador emitiera un label visible con "FP&A"
- **THEN** el test de nomenclatura falla nombrando el archivo y el label

### Requirement: guía de análisis versionada con grounding

El sistema SHALL (debe) versionar la guía de análisis en español
(`docs/fpa-analyses-guide.md`, niveles ELI5/Everyday/Practitioner/Expert) en el
repo como fuente de contenido, con un pase de **grounding** previo a la
publicación: cada fórmula, umbral y default citado SHALL verificarse contra la
implementación real (`viz-fpa.py`, `usage-tracker.py`, `config/fpa.json`), y
las cifras ilustrativas del snapshot original SHALL reemplazarse por cifras
derivadas en generación o eliminarse.

#### Scenario: grounding corrige el texto, no el código

- **WHEN** el pase de grounding encuentra una discrepancia guía↔código
- **THEN** se corrige el texto de la guía, nunca el código

### Requirement: generación de data/fpa-guide.html

El generador SHALL (debe) ingerir la guía (parse stdlib) y producir
`data/fpa-guide.html`: la guía completa, autocontenida y en el tema del sitio.

#### Scenario: guía completa en el tema del sitio

- **WHEN** se regenera `data/fpa-guide.html` desde `docs/fpa-analyses-guide.md`
- **THEN** el HTML es autocontenido, usa los tokens del tema compartido y
  cubre todos los análisis y niveles del markdown fuente

### Requirement: marginalia con progressive disclosure

En cada vista del Dashboard y en el Gantt, cada análisis mapeado SHALL (debe)
anunciarse con un affordance compacto (chip/botón de información, target
táctil de 44px) cuyo tooltip muestre el teaser del nivel 1 (ELI5); al
seleccionar el chip SHALL (debe) expandirse la nota in situ con el nivel 2
completo.

#### Scenario: marginalia cubre todos los análisis incluido el Gantt

- **WHEN** se renderiza el Dashboard o el Gantt
- **THEN** cada análisis mapeado aparece en la marginalia de al menos una
  superficie (las 6 vistas del Dashboard — coffe-8kx: incluida Energía — y el
  Gantt)

#### Scenario: expansión desde el chip

- **WHEN** se selecciona el chip de un análisis
- **THEN** la nota se expande in situ (nivel 2 completo, nivel 3 colapsado,
  enlace a la guía completa y a los IDs FPA-xxx); en touch funciona por tap
  sin depender de hover

### Requirement: mapeo análisis→superficie y check-docs

El mapeo análisis→superficie (vistas + Gantt) SHALL (debe) estar declarado en
el generador, anclado a los IDs FPA-xxx del texto de la guía, y cada análisis
SHALL (debe) aparecer en la marginalia de al menos una superficie. El
`--check-docs` SHALL (debe) fallar loud ante mapeo roto o umbral citado que
difiera del real.

#### Scenario: check-docs detecta mapeo o umbral roto

- **WHEN** `--check-docs` corre
- **THEN** falla loud si el mapeo referencia un análisis inexistente en la guía
  o si un umbral citado por la guía difiere de su valor real en config/código


### Requirement: consolidación de dashboards

El Dashboard SHALL (debe) ser el dashboard único del sitio con su nombre
llano. El dashboard de insights (`data/dashboard.html` y
`scripts/viz-dashboard.py`) SHALL eliminarse del repo y del deploy: fuera del
índice, documentado en README con su cobertura por el Dashboard; los URLs
externos al HTML eliminado SHALL romperse (riesgo aceptado). El Gantt SHALL
conservarse como visualización única de su tipo y adoptar el tema
compartido.

#### Scenario: índice sin dashboard de insights

- **WHEN** se genera `index.html`
- **THEN** lista el Dashboard (nombre llano) como dashboard principal y el Gantt,
  sin entrada para el dashboard de insights

#### Scenario: artefactos eliminados

- **WHEN** se inspecciona el repo y el deploy tras el change
- **THEN** `data/dashboard.html` y `scripts/viz-dashboard.py` no existen y la
  suite verde no los referencia

### Requirement: navegación de vistas efectiva a todo ancho

El Dashboard SHALL (debe) mostrar exactamente una vista a la vez a cualquier
ancho de viewport (no solo <600px). Las tabs SHALL (debe) cambiar la vista
visible sin recargar, actualizar `aria-current` y el parámetro `view` del
share-URL (query params — `?view=…` —, contrato vigente de share-URL; sin
hash). Con JS deshabilitado, las tabs SHALL (debe) seguir resolviendo la
vista por ancla (fallback degradado).

#### Scenario: tab switch en desktop

- **WHEN** se carga el dashboard a 1280×900 y se clickea la tab "Hábitos"
- **THEN** solo la vista Habits es visible, la altura de página es < 2× el
  viewport y la tab activa lleva `aria-current="true"`

#### Scenario: deep-link de vista

- **WHEN** se abre el dashboard con `?view=habits` (parámetro de share-URL)
- **THEN** la vista Habits está activa al cargar, sin scroll de llegada

### Requirement: layout de contenedor y jerarquía visual

El Dashboard SHALL (debe) renderizar dentro de un contenedor con `max-width`
centrado. Los KPI cards SHALL (debe) disponerse en una grilla responsive (≥2
columnas ≥600px, 3–4 ≥1000px) con anatomía uniforme: label, cifra (tabular),
delta chip y contexto muted. Los CTAs SHALL (debe) ir como fila compacta de
botones. Los IDs FPA-xxx SHALL (debe) salir del cuerpo visible. El texto
secundario SHALL (debe) renderizar con el token muted, distinguible de la
cifra primaria.

#### Scenario: grilla de KPIs en desktop

- **WHEN** se muestra la banda de KPIs a 1280px
- **THEN** los cards se disponen en ≥3 columnas y ningún card excede la
  altura de la fila

#### Scenario: sin IDs FPA en el cuerpo

- **WHEN** se inspecciona el texto visible del HTML generado
- **THEN** ningún párrafo o celda contiene "FPA-\d\d\d" (los IDs solo
  aparecen en atributos o footnotes)

#### Scenario: CTAs compactos

- **WHEN** se renderiza la fila de CTAs del Summary
- **THEN** cada CTA mide ≤ 64px de alto y la fila ocupa una sola línea de
  layout a ≥600px


#### Scenario: contenedor sin columnas muertas

- **WHEN** el viewport es ≥ 600px a cualquier ancho
- **THEN** no quedan columnas muertas alrededor del contenedor

### Requirement: banda de KPI única sin duplicación

Cada cifra headline SHALL (debe) renderizar exactamente una vez en la vista
Summary: la banda de KPIs es la única superficie de cifras en cards; el
hallazgo narrativo correspondiente aparece como texto (claim con
métrica+threshold, según el requisito existente) y no como card repetida. Se
elimina el bloque de headline cards separado. Los claims conservan sus
cifras: la exclusión de duplicados aplica solo a cards, no al texto de
claims.

#### Scenario: cifra sin duplicar

- **WHEN** se renderiza la vista Summary
- **THEN** cada cifra KPI aparece en exactamente un card (verificado por
  test: ninguna cifra `fmt_usd` se repite entre cards visibles del primer
  viewport)

### Requirement: disclosure por defecto y summaries estilizados

Por vista, SHALL (debe) abrir expandida por defecto únicamente la sección
primaria (la que responde la pregunta de la vista); las demás secciones
SHALL (debe) renderizar colapsadas con su `summary` estilizado como group-box
del tema (marcador integrado al header, sin marcador nativo huérfano en línea
propia). El estado abierto/cerrado SHALL (debe) persistir en el share-URL
(sin cambio de contrato).

#### Scenario: disclosure inicial

- **WHEN** se carga la vista Cost sin parámetros
- **THEN** solo una de sus secciones está expandida y el resto renderiza
  colapsada con summary clickeable ≥44px de alto

#### Scenario: summary sin marcador huérfano

- **WHEN** se renderiza cualquier `<details>` de sección
- **THEN** el marcador de disclosure está integrado al summary y ningún
  marcador ocupa una línea propia fuera del header

### Requirement: bridge con selector de mes

El bridge precio-volumen-mix SHALL (debe) renderizar un único waterfall con
un selector de mes (default: el último mes con datos de mix), no una figura
por mes. Los meses sin datos de mix SHALL (debe) omitirse del selector, no
renderizar marcos vacíos. La tabla 100% stacked de mix por mes se conserva.
La identidad Volume+Mix+Rate = Δcoste SHALL (debe) seguir verificándose por
mes (sin cambio del requisito de datos).

#### Scenario: un waterfall, selector de mes

- **WHEN** se muestra la sección bridge en la vista Cost
- **THEN** hay exactamente un waterfall visible y un control para elegir el
  mes; cambiar el mes re-renderiza el waterfall sin recargar

#### Scenario: mes sin datos de mix

- **WHEN** un mes no tiene datos suficientes para el bridge
- **THEN** ese mes no aparece en el selector y no se renderiza marco vacío

#### Scenario: ningún mes con datos de mix

- **WHEN** el periodo seleccionado no tiene ningún mes con datos de mix
- **THEN** la sección muestra "n/a" con la razón y no renderiza selector ni
  waterfall (sin placeholders vacíos)

### Requirement: vista de energía estimada con banda (FPA-180)

El Dashboard SHALL (debe) incluir una vista "Energía" (tab F8) con los kWh
estimados por mes como barras con banda low–high (`energy_kwh_band`), el total
del periodo con su banda (18/33/175 kWh en el dataset actual) y los meses
`null` visibles con su razón. Toda cifra derivada de coeficientes SHALL (debe)
llevar tag *assumed*. El caption SHALL (debe) explicar que los extremos son 0%
y 100% de acierto de caché y el escenario base el factor nominal (10%).

#### Scenario: banda visible en la vista energía

- **WHEN** se abre la vista Energía con el dataset que tiene meses con telemetría
- **THEN** cada mes muestra su kWh nominal dentro de la banda low–high y el
  total del periodo muestra la banda completa con tag *assumed*

#### Scenario: mes sin telemetría visible con razón

- **WHEN** un mes tiene `energy_kwh` 0.0 porque sus modelos emitieron `null`
  con razón (sin telemetría)
- **THEN** la vista lo marca como sin telemetría y expone las razones por
  modelo, no lo presenta como una medición de cero kWh

#### Scenario: smoke de la vista energía

- **WHEN** el smoke test carga el dashboard y activa el tab Energía
- **THEN** la vista renderiza sin errores de consola y su total es legible en
  el readout accesible


#### Scenario: accesibilidad y guía de la vista energía

- **WHEN** se revisa la vista Energía
- **THEN** cumple accesibilidad y mobile igual que las demás (readout
  persistente, touch targets ≥ 44×44 px, single-column < 600px), su análisis
  está explicado en la guía (análisis 20, coffe-8kx) y recibe marginalia como
  las demás vistas

### Requirement: vista Proyectos: timeline

El Dashboard SHALL (debe) incluir una vista "Proyectos: timeline" que
renderice, para un proyecto seleccionado, la serie mensual del reporte —
coste efectivo (*assumed*) y volumen de tokens por mes — como área SVG inline
pre-calculada en Python, con selector de proyecto, toggle coste/tokens,
tooltips, deep-link y export CSV/SVG.

#### Scenario: timeline de un proyecto

- **WHEN** se selecciona un proyecto con actividad en varios meses
- **THEN** la vista muestra la serie mensual completa (incluyendo meses sin
  actividad como gaps), con el tag *assumed* en el coste efectivo

#### Scenario: mes con coste 0.0 y tokens > 0

- **WHEN** un mes del proyecto tiene tokens > 0 y coste efectivo 0.0
- **THEN** la vista muestra la razón (modelos sin pricing) y no un área vacía

#### Scenario: deep-link de la vista

- **WHEN** se abre el dashboard con el hash de la vista y proyecto
- **THEN** la vista Proyectos: timeline abre con ese proyecto seleccionado


#### Scenario: composición de tokens y mes sin coste

- **WHEN** se lee el volumen de tokens de un mes o un mes tiene coste efectivo
  0.0 con tokens > 0
- **THEN** los tokens suman in + out + cache_read + cache_write, y el mes con
  coste 0.0 muestra la razón (FPA-008, p.ej. modelos sin pricing en config),
  no un área vacía silenciosa

