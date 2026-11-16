## ADDED Requirements

### Requirement: coste reportado cero no es coste ausente

El tracker SHALL (debe) modelar el coste como `float | None`: `None` es
desconocido y `0` es reportado. Un `usage.cost.total == 0` SHALL (debe)
conservarse como 0 y NO SHALL (debe) disparar estimación. La estimación SHALL
(debe) aplicarse solo con coste ausente/`totalFromProvider:false` y pricing
conocido; si no hay pricing, el coste es `null` con razón. Modelos `:free` y
planes SHALL (debe)N tener `cost_real=0`.

#### Scenario: modelo free con coste 0

- **WHEN** un turno de `openrouter/free` reporta `{"cost": {"total": 0}}`
- **THEN** `cost_effective` y `cost_real` son 0, sin estimación

#### Scenario: plan con coste 0 y tokens

- **WHEN** un turno de `qwen-token-plan-individual` reporta
  `totalFromProvider: false` con tokens
- **THEN** `cost_real` es 0; `cost_effective` solo se estima si hay pricing
  configurado y lleva provenance `assumed`

#### Scenario: coste desconocido

- **WHEN** un turno no trae `usage.cost` y el modelo no tiene pricing
- **THEN** el coste es `null` con razón, nunca `default_rates` silencioso

### Requirement: provenance del coste efectivo

Toda fila y agregado SHALL (debe) llevar `cost_effective_provenance` ∈
`reported|derived|assumed|mixed`. `reported` solo si todos los componentes
vienen de factura/proveedor; `derived` si es tokens × pricing configurado;
`assumed` si usa default rates/plan/config; `mixed` si combina. `metadata`
SHALL (debe) incluir el desglose por provenance. Un agregado con estimaciones
NO SHALL (debe) etiquetarse `reported`.

#### Scenario: agregado mixto

- **WHEN** un mes suma llamadas reportadas y estimadas
- **THEN** su `cost_effective_provenance` es `mixed` y la metadata muestra
  cuánto aporta cada tag

#### Scenario: todo reportado

- **WHEN** todas las filas del agregado traen coste del proveedor
- **THEN** la provenance es `reported`

#### Scenario: estimación de suscripción

- **WHEN** un plan no factura por token y se estima su API-equivalente
- **THEN** la provenance es `assumed` y `cost_real` sigue separado