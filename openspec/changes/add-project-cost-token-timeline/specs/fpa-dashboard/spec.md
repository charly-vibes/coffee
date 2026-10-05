## ADDED Requirements

### Requirement: vista Proyectos: timeline

El Dashboard SHALL (debe) incluir una vista "Proyectos: timeline" que renderice,
para un proyecto seleccionado, la serie mensual del reporte: coste efectivo
(*assumed*) y volumen de tokens (in + out + cache_read + cache_write) por mes,
como área SVG inline pre-calculada en Python, con selector de proyecto, toggle
coste/tokens, tooltips por mes, deep-link por hash y export CSV/SVG como las
demás vistas. Los meses con coste efectivo 0.0 y tokens > 0 SHALL (debe) mostrar
la razón (FPA-008) — p.ej. modelos sin pricing en config — y no un área vacía
silenciosa.

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
