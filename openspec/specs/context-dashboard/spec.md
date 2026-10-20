# context-dashboard Specification

## Purpose
Dashboard de contexto de sesiones Pi (`data/context-dashboard.html`):
higiene de sesiones traducida a presupuesto de contexto — distribución del
pico por sesión (por segmento de compactación), composición por fuente,
tiempo mes×modelo, coste por sesión (claude-cli separado, nunca sumado),
ley de crecimiento (pico vs turnos, OLS) y patologías visibles. Generador
stdlib-only autocontenido que solo renderiza lo que el tracker pre-calcula
en `context_metrics` (Pi-only por decisión D1 del epic).

## Requirements

### Requirement: alcance Pi-only declarado

El dashboard SHALL (debe) cubrir solo las métricas de contexto de Pi
(`context_metrics`), documentando el alcance (Claude/Amp/Codex quedan fuera) y el
carácter no-goal de la integración de marginalia de guía (patrón
`viz_fpa_guide.py`), para fijar expectativas.

#### Scenario: alcance visible

- **WHEN** se abre el dashboard
- **THEN** el header indica que las métricas son de sesiones de Pi únicamente

### Requirement: generador stdlib-only autocontenido

El dashboard de contexto SHALL (debe) generarse con un script Python stdlib-only
(`scripts/viz-ctx.py`) como un único archivo HTML autocontenido con SVG inline,
sin dependencias externas ni build step, reusando el tema compartido
(`scripts/site_theme.py`).

#### Scenario: HTML sin dependencias externas

- **WHEN** se genera el dashboard y se inspecciona el HTML
- **THEN** no hay `<script src>` ni `<link href>` externos y los charts son SVG
  inline con el tema compartido

### Requirement: provenancia en toda cifra

Toda cifra derivada SHALL (debe) llevar tag de procedencia: *reported* (del JSON),
*assumed* (del config o del factor chars/4) o *subscription* (sesiones claude-cli
sin billing por token). Las cifras de claude-cli SHALL (deben) mostrarse separadas,
sin mezclarse en percentiles de coste.

#### Scenario: factor de estimación marcado

- **WHEN** se muestra cualquier cifra de contexto estimado
- **THEN** lleva el tag *assumed* referenciando el factor chars/4 del meta

### Requirement: vista distribución con banda de percentiles

El dashboard SHALL (debe) incluir una vista con el histograma del pico de contexto
por sesión y la banda p50/p75/p90/p95/p99, calculada sobre el set limpio
(sin patológicas, solo `turns > 0`) con el conteo de excluidas y stubs visible,
y umbrales tomados de config.

#### Scenario: histograma con exclusión visible

- **WHEN** el reporte contiene sesiones patológicas
- **THEN** el histograma usa el set limpio y muestra el número de excluidas junto
  al título

### Requirement: vista composición por fuente

El dashboard SHALL (debe) mostrar el share de contexto por fuente (assistant
text/thinking/args, tool results por nombre, user), global y dentro de sesiones
>100k, como SVG stacked con tooltips.

#### Scenario: composición global vs sesiones grandes

- **WHEN** se alterna entre el agregado global y el de sesiones >100k
- **THEN** ambas composiciones son visibles con sus shares y ninguna fuente
  desaparece de la leyenda

### Requirement: vista tiempo por mes x modelo

El dashboard SHALL (debe) mostrar por mes × modelo: pico mediano, % de sesiones
>100k y tok/turno por fuente, marcando los meses parciales con días transcurridos
sobre el total como fpa-dashboard.

#### Scenario: mes parcial marcado

- **WHEN** el reporte termina antes del fin de su mes
- **THEN** ese mes aparece marcado como parcial con `días/total` y sus tasas usan
  daily rates

### Requirement: vista coste con split por componente

El dashboard SHALL (debe) mostrar el spend por modelo, el split in/out/cache del
gasto, el spend mensual y el coste por sesión; las sesiones de suscripción
(claude-cli) SHALL (deben) aparecer con provenancia *subscription* y nunca sumadas
al gasto de pago.

#### Scenario: gasto separado de suscripción

- **WHEN** se muestra cualquier total agregado de coste
- **THEN** el gasto de proveedores de pago y el de suscripción aparecen como cifras
  distintas, sin suma combinada

### Requirement: vista ley de crecimiento

El dashboard SHALL (debe) mostrar el dispersivo pico de contexto vs turnos, con una
serie por modelo y su pendiente pre-calculada (OLS sobre sesiones limpias) en
tok/turno, de modo que la tasa de crecimiento sea comparable entre eras.

#### Scenario: pendiente por modelo visible

- **WHEN** se selecciona un modelo con suficientes sesiones limpias
- **THEN** su pendiente tok/turno se muestra junto al dispersivo y permite comparar
  entre modelos

### Requirement: vista patologías con razón visible

El dashboard SHALL (debe) listar las sesiones patológicas excluidas (glitches,
usage inválido) con la razón de la exclusión, sin números silenciosamente faltantes
(consistente con FPA-008). Las sesiones con coste `null` SHALL (debe)n mostrar la
razón en vez de un cero silencioso.

#### Scenario: outlier listado con razón

- **WHEN** una sesión fue excluida por un bloque assistant >500k chars
- **THEN** aparece en la vista Patologías con su tamaño, modelo y razón

### Requirement: cross-cutting de interacción

Las vistas SHALL (debe) soportar deep-link por hash, export CSV/SVG y mostrar una
vista a la vez a todo ancho (tabs), como las vistas de fpa-dashboard.

#### Scenario: deep-link a una vista

- **WHEN** se abre `context-dashboard.html#cost`
- **THEN** la vista de coste queda activa al cargar
