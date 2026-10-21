## REMOVED Requirements

### Requirement: granularidad proyecto × día fuera de alcance

**Reason**: se levanta la exclusión — el gasto por proyecto y día pasa a
emitirse (ver ADDED "granularidad proyecto × día con coste y tokens").

## ADDED Requirements

### Requirement: granularidad proyecto × día con coste y tokens

El tracker SHALL (debe) emitir en `project_daily_spend[proj][day]` el coste
efectivo, el coste real (misma semántica que los buckets horarios/diarios/
mensuales) y los tokens `in`/`out`/`cache_read`/`cache_write` por proyecto
y día, con agregación pura e inyectable y emisión sparse igual que
`project_daily`.

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

### Requirement: emisión aditiva de granularidades nuevas

La emisión de `project_daily_spend` y `session_spend` SHALL (debe) ser
aditiva: ningún campo existente de `project_daily` o `project_monthly`
cambia de semántica.

#### Scenario: el golden existente no cambia

- **WHEN** se regenera el golden con `REGEN_GOLDEN=1`
- **THEN** el diff solo muestra las claves nuevas; las cifras existentes son
  byte a byte idénticas

### Requirement: filas por sesión con coste y tokens

El tracker SHALL (debe) emitir en `session_spend` una fila por sesión
in-scope con, como mínimo: `session_id`, `project`, `day` (día local de la
primera interacción), `source` (tool proveedor), `model` dominante (o
`mixed` si cambió de modelo, ver atribución de turnos por modelo),
`cost_effective`, `cost_real`, `tokens` con `in`/`out`/`cache_read`/
`cache_write`, y `n_interactions`.

#### Scenario: sesión con coste completo

- **WHEN** el tracker procesa una sesión con telemetría `usage.cost` que no
  cruza medianoche
- **THEN** la fila trae coste y tokens consistentes con la suma de sus
  interacciones, y la suma de `session_spend` del día cuadra con
  `project_daily_spend` y con el bucket diario global

#### Scenario: sesión sin identificador nativo

- **WHEN** el log de una sesión no trae identificador de sesión nativo
- **THEN** la fila se emite igual con un `session_id` de reserva derivado de
  la ruta del archivo (hash), marcando `session_id_fallback: true`

### Requirement: coste ausente en filas de sesión es null con razón

Cuando el coste no esté disponible para una sesión (turnos abortados o
proveedor sin billing por token), los campos de coste de su fila en
`session_spend` SHALL (debe) ser `null` con una `cost_reason` en la fila —
mismo patrón "null con razón" del coste por sesión existente, reutilizando
las mismas cadenas de razón del tracker — nunca 0 fake.

#### Scenario: sesión sin telemetría de coste

- **WHEN** una sesión no tiene `usage.cost` en ningún turno
- **THEN** los campos de coste son `null` con `cost_reason`, y el dashboard
  muestra la razón en vez de un cero silencioso

### Requirement: sesión que cruza medianoche

La fila de sesión SHALL (debe) atribuirse al día local de su primera
interacción; los buckets diarios reparten por interacción, por lo que la
asimetría se documenta.

#### Scenario: sesión abarca dos días

- **WHEN** una sesión abarca dos días locales
- **THEN** la fila se atribuye al día de su primera interacción, la suma
  `session_spend` del día difiere de `project_daily_spend` exactamente en
  las sesiones que cruzan medianoche, y esta asimetría se documenta en
  `README.md`

### Requirement: schema y golden actualizados

El schema contractual `specs/usage-report-v3.schema.json` SHALL (debe)
incluir `project_daily_spend` y `session_spend` con sus campos obligatorios
(`session_id`, `project`, `day`), y el golden snapshot SHALL (debe)
regenerarse de forma que el diff solo contenga los campos nuevos.

#### Scenario: validación del reporte regenerado

- **WHEN** se ejecuta `--validate` (jsonschema) contra un reporte con las
  secciones nuevas
- **THEN** valida sin errores, y un reporte sin `project_daily_spend` o
  `session_spend` falla la validación

### Requirement: semántica de día heredada

El día de `project_daily_spend` y de `session_spend` SHALL (debe) usar la
misma TZ local de extracción (`LOCAL_TZ`) que los buckets diarios
existentes, heredando su advertencia documentada de que cambiar de máquina
de extracción desplaza los límites de día.

#### Scenario: sesión cerca de medianoche

- **WHEN** una interacción cae a segundos del límite de día local
- **THEN** se bucketea con la misma TZ que `daily`, sin conversión adicional
