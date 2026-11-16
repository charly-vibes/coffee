## MODIFIED Requirements

### Requirement: provenance en toda figura

Toda figura SHALL (debe) llevar un tag de procedencia: *reported* (factura o
proveedor), *derived* (cálculo determinista con pricing configurado),
*assumed* (config, plan o default rates) o *mixed* (agregado con más de un
origen). Un agregado con cualquier componente estimado NO SHALL (debe)
etiquetarse *reported*. El coste efectivo SHALL (debe) llevar el tag que
corresponda a su composición real; el cash del ledger SHALL (debe) seguir
siendo *reported*.

#### Scenario: KPI con mezcla

- **WHEN** el KPI "Coste efectivo" suma coste reportado y estimado
- **THEN** muestra el tag *mixed* (o *assumed* si todo es estimado)

#### Scenario: cifra asumida marcada

- **WHEN** se renderiza una cifra derivada de config (p.ej. precio de plan)
- **THEN** lleva el tag *assumed* junto al valor

#### Scenario: cash del ledger

- **WHEN** se muestra el cash del ledger
- **THEN** lleva *reported* y no se suma al coste efectivo