# Design: add-session-spend-report

## Contexto

El reporte ya tiene la semántica de coste por sesión definida (null con
razón, atribución de turnos por modelo, `usage.cost` de Pi) pero solo la
agrega en buckets globales por día y por proyecto × mes. El gasto diario por
proyecto y por sesión no se emite: la especificación lo excluyó
explícitamente en su momento. Este cambio levanta esa exclusión.

## Decisiones

### D1: dos secciones nuevas, no una

- `project_daily_spend[proj][day]` → agregado que alimenta la vista y los
  totales; sparse (sin días vacíos).
- `session_spend[]` → filas por sesión que permiten el drill-down y el
  re-agregado posterior sin re-parsear logs.

Se emiten ambas porque la vista necesita el drill-down y el cuadre, y
embarcar las filas evita que el dashboard dependa de los logs crudos. Coste:
~4.4k filas → tamaño del JSON crece de forma acotada.

### D2: aditivo, semántica intacta

Ningún campo existente cambia. `project_daily` (Gantt: actividad) sigue sin
coste; `project_daily_spend` es una clave nueva. El golden debe diffear solo
en claves nuevas — es el check de regresión principal.

### D3: atribución de la sesión a un día

La fila de sesión se atribuye al día local de su **primera interacción**.
Los buckets diarios globales reparten por interacción, así que una sesión
que cruza medianoche no cuadra 1:1 con `project_daily_spend`+`daily`. Se
acepta la asimetría (la fila de sesión es una unidad, no una serie) y se
documenta en `README.md` — alternativa descartada: partir la fila por día,
que duplicaría sesiones en el drill-down.

### D4: coste ausente = null con razón

Reutiliza el patrón existente de "modelos sin telemetría" y "coste por
sesión con telemetría faltante": `null` + `cost_reason`, el dashboard
muestra la razón. Prohibido el 0 fake.

### D5: vista en el sistema de vistas existente

La vista "Gasto diario" se registra como una vista más de `viz-fpa.py`
(provenance, deep-link, export CSV, nomenclatura llana, pre-cálculo en
Python). No se crea un dashboard nuevo (cf. consolidación de dashboards).

## Riesgos

- Tamaño del reporte: mitigado con sesiones in-scope solamente (charly-filter
  ya aplica) y campos compactos.
- Modelos sin pricing → `cost_effective` 0.0 con tokens > 0: mismo
  tratamiento que la vista Proyectos (mostrar razón, no área vacía).
