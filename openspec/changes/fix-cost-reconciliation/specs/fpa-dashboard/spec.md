## MODIFIED Requirements

### Requirement: alertas con evidencia y umbrales configurables

El Dashboard SHALL (debe) listar alertas con severity, nombre de regla y
valores de evidencia, cubriendo: verify plan (>25×), reconciliación cash real
vs cargas implícitas del calendario (FPA-082), over-budget, unit-cost +15% MoM,
shift de mix premium > 5pts en 3 meses, concentración top-3 > 50% y staleness
> 14 días. Todos los umbrales SHALL (debe) ser configurables. `verify-plan`
SHALL (debe) usar `subscription_fee_reported` si existe; si no, el fee asumido,
y la evidencia SHALL (debe) mostrar ambos y el `source`.

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

#### Scenario: verify-plan con ledger

- **WHEN** mayo 2026 tiene efectivo Claude $900.67 y el ledger reporta una
  cuota de $100 (config dice $20)
- **THEN** la alerta usa $100 (9.0×), la evidencia muestra ambas cuotas y no
  dispara el falso positivo de 45×

### Requirement: economía de suscripción

El Dashboard SHALL (debe) calcular utilización por plan (efectivo ÷ precio),
break-even mensual y headroom, comparar cash cost bajo 4 casos (actual, todo
pay-per-token, todo Pro, todo Max) y declarar que la equivalencia por coste
efectivo ignora usage limits del plan. La economía SHALL (debe) operar sobre
el calendario de suscripciones del config, corregido a las facturas
(coffe-a31), con provenance *assumed*: un panel por periodo del calendario.
Cuando el ledger tenga una cuota `subscription` para el mes, el panel SHALL
(debe) mostrar también la cuota reportada y su provenance.

#### Scenario: break-even visible

- **WHEN** se muestra el panel de un plan
- **THEN** aparecen utilización, break-even y headroom calculados del config

#### Scenario: sin suscripción activa al cierre

- **WHEN** el reporte termina después de la última factura de
  suscripción (p.ej. claude cancelado tras jun 2026)
- **THEN** el plan default del forecast es "sin suscripción" (fee $0) y
  los planes históricos del calendario siguen listables como escenarios

#### Scenario: cuota reportada vs asumida

- **WHEN** el ledger de mayo 2026 reporta $100 y el config asume $20
- **THEN** el panel muestra ambas con sus tags (*reported* / *assumed*) y no
  las suma

### Requirement: vista Gasto diario: total y desglose por proyecto

El Dashboard SHALL (debe) incluir una vista "Gasto diario" que, a partir de
`project_daily_spend` del reporte, muestre para el día (o rango) seleccionado
el gasto total con delta vs. media de los 7 días previos y el desglose por
proyecto en tabla (coste efectivo *assumed*, coste real tracker y tokens),
siguiendo las convenciones del dashboard: provenance, lenguaje llano,
deep-link, export CSV y pre-cálculo en Python. La serie `cost_real` de la
vista NO SHALL (debe) incluir fees de suscripción; el cash del ledger se
concilia a nivel mensual.

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

#### Scenario: total 0.0 con tokens positivos

- **WHEN** el día tiene tokens > 0 pero coste efectivo 0.0 (modelos sin
  pricing, FPA-008)
- **THEN** la vista muestra la razón junto al total, no un cero silencioso

#### Scenario: alcance del cash real

- **WHEN** se consulta la vista buscando el cash del ledger de cargos
- **THEN** la vista declara que el desglose diario usa la serie tracker
  (sin fees) y que el cash del ledger se concilia a nivel mensual (CRG-F1),
  sin mezclar ambas series en la misma columna

#### Scenario: coste real sin fees

- **WHEN** se suma el `cost_real` de la vista de un mes
- **THEN** coincide con `cost_real_total_tracker` (sin fees) y no con el
  `monthly[].cost_real` que incluye la cuota