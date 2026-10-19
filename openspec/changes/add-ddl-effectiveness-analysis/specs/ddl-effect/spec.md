# add-ddl-effectiveness-analysis — Spec delta

## ADDED Requirements

### Requirement: fleet y unidad de análisis declaradas

El tracker SHALL (debe) definir la flota como los repos charly-vibes con actividad
en las sesiones in-scope de coffee (filtro charly), listar en `metadata` qué repos
entran y por qué, y marcar cada repo como tratamiento (`is_control: false`) o
control (`is_control: true`, definido por ausencia de `.ddl/` y de topic ddl de
GitHub — nunca por dosis). El repo `charly-dulce-de-leche` entra marcado como
outlier declarado.

#### Scenario: repo control por ausencia, no por dosis cero

- **WHEN** un repo tiene `.ddl/` pero 0 herramientas sanas en `ddl status`
- **THEN** `is_control` es `false` (es tratamiento con dosis 0) y el dashboard lo
  muestra como tratamiento

#### Scenario: fleet reproducible

- **WHEN** el tracker corre dos veces sobre los mismos insumos
- **THEN** la lista de repos y su clasificación control/tratamiento es idéntica

### Requirement: línea de adopción por repo

Para cada repo de la flota el tracker SHALL (debe) emitir `adoption` con: fecha
del primer commit que introduce `.ddl/` (fuente git), fecha de primer uso de cada
herramienta gestionada en sesiones cuyo cwd matchea el repo (mapping explícito en
config `repo_aliases`, sin fuzzy matching), y ausencia de fecha como `null` con
provenance — nunca fecha estimada.

#### Scenario: primer uso posterior a migración

- **WHEN** el primer commit de `.ddl/` precede al primer uso registrado de `wai`
  en ese repo
- **THEN** ambas fechas aparecen tal cual, sin ajuste ni imputación

#### Scenario: repo sin señal de ddl

- **WHEN** un repo no tiene `.ddl/` ni uso registrado de herramientas gestionadas
- **THEN** `adoption.first_ddl_commit` es `null` con provenance `missing` y el
  repo puede ser elegible como control

### Requirement: dosis de cobertura de herramientas

El tracker SHALL (debe) calcular la dosis de cada repo como fracción de las 13
herramientas del registry gestionadas que están presentes y sanas, con jerarquía
de fuentes explícita: `ddl status` primero; fallback a archivos-canario bajo
`.ddl/` (git) y a primer uso en sesiones. Cuando la fuente efectiva no es
`ddl status`, la dosis lleva provenance que lo declare. `dose_detail` lista
present/sanas/faltantes por nombre.

#### Scenario: dosis desde ddl status

- **WHEN** `ddl status` corre contra un repo y reporta 10 de 13 herramientas sanas
- **THEN** `dose` es 10/13 con provenance `reported` y `dose_detail.healthy`
  lista exactamente esas 10

#### Scenario: ddl no instalado

- **WHEN** el binario `ddl` no está disponible o falla para un repo
- **THEN** la dosis se calcula por canarios git/usos de sesión y su provenance lo
  declara (no cambia silenciosamente de significado)

### Requirement: métricas de lazo de feedback

El tracker SHALL (debe) emitir por repo × mes: `recovery_p50_interactions`
(mediana de interacciones entre un error de tool/API y la siguiente invocación
exitosa del mismo tool dentro de sesiones Pi, con eventos censurados por fin de
sesión excluidos de la mediana y contados en `recovery_censored_rate`),
`session_commit_latency_p50_h` (latencia entre el último timestamp de sesión del
repo y el primer commit posterior asistido), y `ci_fail_rate` (runs fallidos /
runs totales del mes vía gh api, excluyendo runs de dependabot).

#### Scenario: recuperación con censura visible

- **WHEN** en un mes la mitad de los eventos de error terminan la sesión sin
  invocación exitosa posterior del mismo tool
- **THEN** la mediana se calcula solo sobre no censurados y
  `recovery_censored_rate` es ≥ 0.5, visible en el dashboard

#### Scenario: CI sin red degrada, no inventa

- **WHEN** `gh api` no está disponible o el rate limit agota
- **THEN** `ci_fail_rate` es `null` con provenance `missing` y razón — nunca 0

#### Scenario: cache incremental de gh api

- **WHEN** existe `data/cache/gh_runs/<repo>.json` con `fetched_until`
- **THEN** el tracker refetch solo runs posteriores a esa marca y nunca re-descarga
  el historial completo

### Requirement: métricas de tasa de error

El tracker SHALL (debe) emitir por repo × mes: `errors_api_per_100` (errores de
API por 100 interacciones, fuente `usage_report_v3.json`) y desglose de clases de
error de terapia por 100 interacciones (cache en `data/cache/terapia_errors.json`
con TTL semanal). Toda tasa lleva el denominador (interacciones) en el mismo row.

#### Scenario: denominador siempre presente

- **WHEN** se lee cualquier tasa de error del panel
- **THEN** `interactions` del mismo row permite recalcular el numerador absoluto

### Requirement: proxies de calidad, los cuatro, sin score compuesto

El tracker SHALL (debe) emitir por repo × mes los cuatro proxies: `revert_ratio`,
`fix_ratio`, `test_commit_ratio` (fuente git, definiciones de design.md) y
`ah_check_findings` (serie temporal desde cache local de ah). El dashboard SHALL
(debe) mostrarlos lado a lado y SHALL NOT (no debe) combinarlos en un score
compuesto ponderado. Cada proxy declara su bias conocido en la guía.

#### Scenario: sin score inventado

- **WHEN** se abre la vista de calidad del dashboard
- **THEN** hay 4 métricas separadas y ningún agregado ponderado de "calidad"

### Requirement: estimadores ITS, DiD y dosis-respuesta

El tracker SHALL (debe) emitir en `analysis`: serie interrumpida por repo (media
móvil trimestral pre/post adopción con IC bootstrap, seed fija en
`metadata.seed_bootstrap`), diferencia-en-diferencias 2×2 por métrica contra el
grupo control, y dosis-respuesta (Spearman dosis × outcome). Celdas con n < 3
SHALL NOT (no deben) graficarse. Sin regresión multivariable en v1.

#### Scenario: celda chica no graficada

- **WHEN** una métrica × período tiene n < 3 repos
- **THEN** el punto no aparece en el dashboard y el JSON lo marca

#### Scenario: bootstrap reproducible

- **WHEN** el tracker corre dos veces con los mismos insumos
- **THEN** los IC bootstrap son idénticos (seed fija)

### Requirement: disclosure de confusores estructural

El dashboard SHALL (debe) reservar una banda fija no-colapsable que declare: (1)
el diseño es asociativo, no causal; (2) madurez del repo y mezcla de modelos como
confusores medidos; (3) que la adopción de ddl correlaciona con atención del
mantenedor — confusor no controlable. La guía a 4 niveles repite el disclaimer.

#### Scenario: disclaimer no removible

- **WHEN** se genera el dashboard con cualquier set de datos
- **THEN** la banda de disclosure está presente aunque todas las métricas tengan
  provenance missing

### Requirement: provenancia en toda cifra

Toda cifra SHALL (debe) llevar tag de procedencia: *reported* (fuente primaria),
*derived* (calculada de otras cifras del JSON), *cached* (gh/terapia con fecha de
fetch), *missing* (null con razón). Meses parciales marcados. Sin cifra sin origen.

#### Scenario: cifra cacheada con fecha

- **WHEN** `ci_fail_rate` proviene del cache de gh
- **THEN** su provenance incluye la fecha de fetch del cache

### Requirement: dashboard autocontenido stdlib-only

`scripts/viz-ddl.py` SHALL (debe) generar `data/ddl-dashboard.html` como único
HTML autocontenido, SVG inline, sin `<script src>`/`<link href>` externos, tema
compartido (`site_theme.py`), 6 vistas (adopción, dosis-respuesta, pre/post+DiD,
lazo de feedback, errores, calidad) con deep-link por hash, export CSV/SVG y
`--validate` contra `specs/ddl-effect.schema.json`.

#### Scenario: HTML sin dependencias externas

- **WHEN** se genera el dashboard y se inspecciona
- **THEN** no hay recursos externos y los charts son SVG inline con el tema

#### Scenario: schema valida

- **WHEN** `viz-ddl.py --validate` corre contra `data/ddl_effect.json`
- **THEN** el JSON pasa la validación (jsonschema, opcional como en los viz existentes)

### Requirement: integración nightly, guía y README

`nightly-update.sh` SHALL (debe) regenerar tracker + dashboard (después del
reporte de usage, que es insumo); `docs/fpa-analyses-guide.md` SHALL (debe)
sumar el análisis #22 a 4 niveles (ELI5 → experto, español) con grounding
verificado (mapeo de cifras y umbrales vs config, estilo `--check-docs`); README
SHALL (debe) documentar layout de `data/ddl_effect.json`, cache y gaps conocidos.

#### Scenario: orden de regeneración respetado

- **WHEN** el nightly corre de cero
- **THEN** ddl-effect-tracker corre después de usage-tracker y viz-ddl después
  del tracker

#### Scenario: grounding de la guía verificado

- **WHEN** el check de grounding de la guía corre
- **THEN** cada cifra citada del análisis #22 mapea a un campo real del JSON o
  umbral de config, y los umbrales coinciden
