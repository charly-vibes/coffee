## ADDED Requirements

### Requirement: reconciliación de cuota de suscripción con el ledger

Cada `monthly[m]` SHALL (debe) emitir `subscription_fee_assumed` (calendario),
`subscription_fee_reported` (kind `subscription` del ledger, nullable) y
`subscription_fee_source` (`ledger|config|none`). `cost_real` SHALL (debe) usar
el fee reportado cuando exista; si no, el asumido. El cash SHALL (debe) seguir
siendo ledger-only y no se suma al coste efectivo.

#### Scenario: ledger con cuota distinta al config

- **WHEN** mayo 2026 tiene claude Max $100 en el ledger y $20 en el config
- **THEN** `subscription_fee_reported=100`, `source="ledger"` y `cost_real`
  usa 100; la evidencia conserva el asumido $20

#### Scenario: mes sin factura de suscripción

- **WHEN** un mes no tiene entradas `subscription` en el ledger
- **THEN** `subscription_fee_reported=null`, `source="config"` y el fee
  asumido se usa con provenance `assumed`

### Requirement: alcance del total del ledger

La metadata SHALL (debe) distinguir `charges_total_real` (todas las fechas del
ledger, *reported*) de `charges_total_real_period` (restringido a los meses del
reporte). README y dashboard SHALL (debe) declarar que el cash del período
excluye cargos pre-período.

#### Scenario: cargo pre-período

- **WHEN** el ledger incluye un crédito de $200 de oct-2025 y el reporte
  empieza en dic-2025
- **THEN** `charges_total_real` lo incluye y `charges_total_real_period` no;
  ambos campos están presentes con su alcance

## MODIFIED Requirements

### Requirement: granularidad proyecto × día con coste y tokens

El tracker SHALL (debe) emitir en `project_daily_spend[proj][day]` el coste
efectivo, el coste real tracker (sin fees de suscripción) y los tokens
`in`/`out`/`cache_read`/`cache_write` por proyecto y día, con agregación pura
e inyectable y emisión sparse igual que `project_daily`. La suma de
`cost_real` sobre todos los proyectos/días de un mes SHALL (debe) coincidir con
`cost_real_total_tracker`; los fees SHALL (debe)N vivir solo en
`monthly[].cost_real`.

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

#### Scenario: suma diaria de coste real sin fees

- **WHEN** se suman `daily[].cost_real` de todos los días del reporte
- **THEN** coincide con `metadata.cost_real_total_tracker`; el fee mensual
  aparece una sola vez en `monthly[].cost_real`