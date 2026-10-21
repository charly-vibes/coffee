## ADDED Requirements

### Requirement: vista Gasto diario: total y desglose por proyecto

El Dashboard SHALL (debe) incluir una vista "Gasto diario" que, a partir de
`project_daily_spend` del reporte, muestre para el día (o rango) seleccionado
el gasto total con delta vs. media de los 7 días previos y el desglose por
proyecto en tabla (coste efectivo, real y tokens), siguiendo las convenciones
del dashboard: provenance, lenguaje llano, deep-link, export CSV y
pre-cálculo en Python.

#### Scenario: gasto de un día por proyecto

- **WHEN** se selecciona un día con actividad en varios proyectos
- **THEN** la vista muestra el total del día y la tabla por proyecto, y la
  suma de la tabla cuadra con el bucket diario global del reporte

#### Scenario: deep-link de la vista

- **WHEN** se abre el dashboard con el hash de la vista y el día
- **THEN** la vista abre con ese día seleccionado

#### Scenario: día sin actividad

- **WHEN** se selecciona un día sin sesiones in-scope
- **THEN** la vista muestra el estado vacío con la razón (sin actividad o
  fuera del rango del reporte) en vez de un error o tabla vacía silenciosa

### Requirement: vista Gasto diario: drill-down por sesión

La vista "Gasto diario" SHALL (debe) incluir un drill-down con las sesiones
del día seleccionado: tabla por sesión con proyecto, hora de inicio,
source, model, interacciones y coste, filtrable por proyecto y exportable a
CSV.

#### Scenario: sesiones de un día por proyecto

- **WHEN** se expande el drill-down de un día con varias sesiones
- **THEN** la tabla lista cada sesión con su proyecto y coste, y la suma de
  la tabla cuadra con `project_daily_spend` del día

#### Scenario: sesión sin coste en el drill-down

- **WHEN** una sesión listada no tiene telemetría de coste
- **THEN** su fila muestra la razón (p.ej. "sin billing por token") en lugar
  de un cero, y el total excluye esas sesiones marcándolas como faltantes
