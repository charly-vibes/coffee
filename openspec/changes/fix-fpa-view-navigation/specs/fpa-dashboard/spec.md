## MODIFIED Requirements

### Requirement: navegación de vistas efectiva a todo ancho

El Dashboard SHALL (debe) mostrar exactamente una vista a la vez a cualquier
ancho de viewport (no solo <600px), incluidas las vistas Summary, Cost,
Breakdown, Gasto diario, Habits, Outlook, Energía y Data & method. Las tabs
SHALL (debe) cambiar la vista visible sin recargar, actualizar `aria-current`
y el parámetro `view` del share-URL (query params — `?view=…` —, contrato
vigente de share-URL; sin hash). La carga inicial SHALL (debe) mostrar
exactamente una vista. Con JS deshabilitado, las tabs SHALL (debe) seguir
resolviendo la vista por ancla (fallback degradado). El smoke SHALL (debe)
derivar la lista de vistas de las tabs renderizadas, no de una constante.

#### Scenario: tab switch en desktop

- **WHEN** se carga el dashboard a 1280×900 y se clickea la tab "Hábitos"
- **THEN** solo la vista Habits es visible, la altura de página es < 2× el
  viewport y la tab activa lleva `aria-current="true"`

#### Scenario: deep-link de vista

- **WHEN** se abre el dashboard con `?view=habits` (parámetro de share-URL)
- **THEN** la vista Habits está activa al cargar, sin scroll de llegada

#### Scenario: carga inicial

- **WHEN** se abre el dashboard sin `?view`
- **THEN** solo Summary es visible; Gasto diario no se renderiza junto a ella

#### Scenario: vista nueva registrada

- **WHEN** se agrega una tab/vista nueva
- **THEN** el smoke la incluye automáticamente y verifica que el hide/show
  la cubre; una vista sin regla CSS falla el test