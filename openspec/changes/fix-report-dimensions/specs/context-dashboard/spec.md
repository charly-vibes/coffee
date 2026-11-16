## MODIFIED Requirements

### Requirement: vista patologías con razón visible

El dashboard SHALL (debe) listar las sesiones patológicas excluidas (glitches,
usage inválido) con la razón de la exclusión, sin números silenciosamente
faltantes (consistente con FPA-008). Las sesiones con coste `null` SHALL
(debe)N mostrar la razón en vez de un cero silencioso. El proyecto SHALL (debe)
mostrarse con el label normalizado del reporte (`ak-catitas`), no con el path
mungido de Pi (`--var-home-...`); el path crudo, si se conserva, va en un campo
separado.

#### Scenario: outlier listado con razón

- **WHEN** una sesión fue excluida por un bloque assistant >500k chars
- **THEN** aparece en la vista Patologías con su tamaño, modelo y razón

#### Scenario: label de proyecto normalizado

- **WHEN** una sesión pertenece a `--var-home-sasha-para-areas-dev-gh-ak-catitas--`
- **THEN** la tabla muestra `ak-catitas` y no el path mungido