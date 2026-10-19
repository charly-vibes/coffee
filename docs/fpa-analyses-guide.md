# Los análisis del dashboard, explicados a cuatro niveles

Compañero de `fpa-dashboard-ears-spec.md` (spec del generador, en inglés). Cada
análisis se explica cuatro veces, de lo más simple a lo más técnico.

| Nivel | Lector | Qué te da |
|---|---|---|
| **1 · ELI5** | Cualquiera | Una imagen que podés recordar |
| **2 · Cotidiano** | Alguien que usa el dashboard | Qué es y cómo leerlo |
| **3 · Practicante** | Quien lo construye y mantiene | Cómo se computa, trampas, decisiones |
| **4 · Experto** | Un analista o estadístico | Supuestos, límites, métodos mejores |

Las cifras citadas (efectivo {{fig:efectivo_total}}, cash real
{{fig:cash_real_total}}, {{fig:top3_share}} del top-3 de proyectos,
{{fig:agent_share}} de sesiones con agente, etc.) provienen del reporte actual
`data/usage_report_v3.json` (periodo {{fig:periodo}}, filtro {{fig:filtro}},
{{fig:interacciones_total}} interacciones) — se derivan del reporte vigente en
cada generación (tokens `fig:*` resueltos por `viz_fpa_guide.py`) y
`python3 scripts/viz-fpa.py --check-docs` verifica lo acoplado al README. Son una foto del momento de la
traducción; el dashboard calcula las cifras vivas en cada corrida.

Los IDs de requisito (FPA-xxx) apuntan a la spec.

---

## A. Costo y economía unitaria

### 1. Costo efectivo, costo real y apalancamiento (FPA-002, 030–032)

- **1 · ELI5:** Imaginá un buffet libre. El "costo efectivo" es lo que habría
  costado tu plato si pagarás por plato. El "costo real" es la entrada que
  efectivamente pagaste. El apalancamiento es cuántas veces más barato salió
  la entrada.
- **2 · Cotidiano:** Algunas herramientas cobran por mes y otras por uso. El
  costo efectivo re-precia todo como si fuera pago-por-uso, para que las
  herramientas se comparen en limpio y veas cuánto valor te da una suscripción.
  El apalancamiento es efectivo ÷ real. En el reporte actual:
  {{fig:efectivo_total}} efectivo contra {{fig:cash_real_total}} real, unos
  **{{fig:apalancamiento}}**.
- **3 · Practicante:** Efectivo = Σ tokens × precio de lista por modelo
  (input, output, cache read, cache write — con los rates del config
  `model_pricing`, versionados por fecha para que los meses viejos no cambien
  retroactivamente). Real = cargos de suscripción en sus fechas de facturación
  (ledger `data/charges.json`, *reported*) + cargos pay-per-token reales.
  **Jamás se suman los dos** (FPA-002). Además: el árbol de tiempo usa el
  costo *real* (cash), para que sea comparable con el presupuesto cash.
- **4 · Experto:** El costo efectivo es una valuación contrafactual a precio
  de lista, no un gasto. Sobreestima el ahorro cuando los rate limits del plan
  atarían el consumo, y cuando el uso pay-per-token habría sido menor
  (elasticidad de la demanda). El apalancamiento es un cociente entre una
  magnitud modelada y una observada, así que en meses de cash bajo lo domina
  el denominador. Reportarlo como distribución por mes, no como un punto.
  Reconciliar el ledger (real) por separado del medidor de uso (efectivo),
  como manda la buena práctica financiera.

### 2. Costo por 1k interacciones (FPA-033, 034)

- **1 · ELI5:** Cuánto cuesta en promedio cada "toque" al robot.
- **2 · Cotidiano:** Costo total ÷ interacciones × 1,000. Si baja, cada acción
  salió más barata, aunque la factura total subiera. Actualmente el claim del
  dashboard muestra **{{fig:per_1k_efectivo}} efectivo por 1k** contra un
  objetivo de {{fig:target_per_1k}} (config `target_per_1k`, *assumed*): por ahora fuera de objetivo, con
  métrica y umbral visibles en el claim.
- **3 · Practicante:** El claim compara el costo *efectivo* por 1k contra el
  objetivo del config. El costo real por 1k hoy es {{fig:per_1k_real}} — útil para mirar
  los dos por separado, nunca sumados. Baja la cifra cuando se aligera la
  mezcla de modelos; combinalo con el bridge (análisis 14). Depende de cómo
  se defina "interacción": los tool calls inflan el denominador, así que
  mirá también costo por sesión y por token.
- **4 · Experto:** Es un estimador de razón sobre eventos heterogéneos y de
  cola pesada, así que la media es inestable. Reportar medianas o cuantiles
  por sesión donde sea posible. La paradoja de Simpson aplica: la tasa
  agregada puede subir mientras la de cada modelo baja, puramente por mezcla.
  Un cambio de logging o de comportamiento del agente crea un quiebre
  estructural en el denominador: indexar a un periodo base y anotar los
  quiebres.

### 3. Costo por sesión (FPA-035, 117)

- **1 · ELI5:** El precio de una sentada con el robot.
- **2 · Cotidiano:** Costo ÷ sesiones. Hoy: {{fig:efectivo_total}} efectivo ÷
  {{fig:sesiones_total}} sesiones ≈ **{{fig:por_sesion}} por sesión**. Las sesiones largas cuestan más porque el
  asistente re-lee toda la conversación en cada turno.
- **3 · Practicante:** El tracker no emite coste por sesión como dato
  primario (el dashboard lo deriva del total, FPA-116/117). Lo que el
  dashboard sí despliega es el costo promedio y las sesiones más largas por
  turnos. Mejora pendiente: mediana y percentil 90, no solo la media. Las
  sesiones largas sin `/clear` son las sospechosas usuales.
- **4 · Experto:** Sin caché, los tokens de input crecen aproximadamente
  cuadráticamente en los turnos; con prompt caching el crecimiento es más
  cercano a lineal pero con saltos cuando la caché expira. El costo por
  sesión es de cola pesada: usar escalas logarítmicas, medias recortadas o
  un ajuste lognormal.

### 4. Concentración: share del top-3 y Pareto (FPA-028, 036)

- **1 · ELI5:** Algunos juguetes se comen la mayor parte de tu mesada.
- **2 · Cotidiano:** Ordenar proyectos por costo y ver qué share se llevan
  los primeros. Hoy los tres primeros ({{fig:top3_nombres_cifras}})
  son el **{{fig:top3_share}}** del costo efectivo. La alerta de
  concentración está configurada para disparar si el top-3 pasa el **50%**
  (`concentration_top3_pct`).
- **3 · Practicante:** El dashboard muestra la curva acumulada (Pareto) con
  la cola larga agrupada en una fila y el top-3 aparte (FPA-028/036). La
  concentración no es automáticamente mala: comparala con los resultados por
  proyecto (análisis 12).
- **4 · Experto:** Se puede usar HHI o Gini como número único, pero los
  límites de proyecto son arbitrarios y el índice depende de la taxonomía.
  Los costos compartidos (tooling, sesiones de setup) deben asignarse de
  forma explícita. La prueba real es share de costo vs share de resultados
  (curvas tipo Lorenz), que revela mala asignación.

### 5. Mezcla de modelos y share premium (FPA-037, 068, 085)

- **1 · ELI5:** Elegir entre el autito barato y el auto caro y rápido en
  cada viaje.
- **2 · Cotidiano:** Qué share del costo viene de modelos premium (hoy, la
  lista del config marca a Opus como premium). Si crece, cada acción sale
  más cara aunque hagas lo mismo.
- **3 · Practicante:** Gráfico apilado 100% de share de costo por mes, más
  una alerta cuando el share premium sube **5 puntos en 3 meses**
  (`premium_share_rise_pts`). El share premium se computa con el costo
  *efectivo* (la lista premium del config es una decisión declarada,
  *assumed*). Útil para rutear: modelos baratos para tareas rutinarias.
- **4 · Experto:** La mezcla es el segundo término de la descomposición del
  bridge. La elección de modelo es endógena: las tareas más duras reciben
  modelos premium, así que un share premium alto no es necesariamente
  desperdicio. Una prueba válida de eficiencia necesita calidad por modelo
  (aceptación o retrabajo) y una frontera de costo-efectividad.

### 6. Cache-hit rate y ratio de tokens (FPA-043, 044)

- **1 · ELI5:** Releer una página que acabás de leer es más rápido y más
  barato que leer una nueva.
- **2 · Cotidiano:** Los proveedores cobran mucho menos por texto que ya
  vieron hace poco. El cache-hit rate muestra cada cuánto te beneficiás.
  Actualmente: **{{fig:cache_hit}}** de los tokens de entrada salieron de caché.
- **3 · Practicante:** `cache_read ÷ (input + cache_read + cache_write)` —
  exactamente esa fórmula computa el KPI del dashboard. Baja cuando las
  sesiones se reinician, cuando el prefijo del prompt cambia o cuando la
  caché expira. El ratio salida/entrada (hoy ≈ {{fig:ratio_out_in}}: {{fig:tokens_out_m}}
  de salida contra {{fig:tokens_in_m}} de entrada) muestra si mayormente alimentás contexto o pedís
  salida. El factor de cache write está en el config
  (`cache_write_factor` 1.25).
- **4 · Experto:** El precio efectivo de entrada es una mezcla de los
  precios de lectura, escritura y entrada sin caché, ponderada por el hit
  rate. Las lecturas se cobran muy por debajo de la entrada sin caché
  (aproximadamente una décima) y las escrituras por encima, así que cambios
  chicos en el hit rate pueden dominar el costo. Verificá los ratios contra
  tu configuración de precios. Las escrituras de caché solo se pagan si hay
  lecturas dentro de la vida de la caché.

---

## B. Patrones de trabajo

### 7. Share de autonomía (FPA-038, 121)

- **1 · ELI5:** Cada cuánto el robot trabaja solo mientras vos hacés otra
  cosa.
- **2 · Cotidiano:** El share de sesiones que usan un agente. Hoy: **{{fig:agent_share}}**
  ({{fig:sesiones_agent}} de {{fig:sesiones_total}} sesiones).
- **3 · Practicante:** Graficarlo por mes y leerlo junto al costo por sesión
  y los resultados. Autonomía sin resultados es solo costo.
- **4 · Experto:** "Usó la tool Agent" es un proxy débil de autonomía.
  Construcciones mejores: tool calls por prompt humano, tiempo de ejecución
  no atendido, y tasa de intervención humana. El tipo de tarea confunde a
  todos.

### 8. Multitasking (FPA-039, 120)

- **1 · ELI5:** Hacer malabares con varias pelotas a la vez.
- **2 · Cotidiano:** El share de horas activas donde tocaste dos o más
  proyectos. Hoy: **{{fig:multitask_pct}}** de {{fig:horas_activas}} horas
  activas, con un promedio de **{{fig:avg_proj_hora}}
  proyectos por hora** y un pico de **{{fig:pico_proyectos}}** proyectos en
  una misma hora.
- **3 · Practicante:** El dashboard lo parte en tres medidas separadas: el
  promedio de proyectos por hora y el pico de sesiones simultáneas miden
  *agentes paralelos* (medida `parallel-agent`); solo los **cambios de
  proyecto por hora activa** (medida `human-context-switching`, hoy {{fig:switches_hora}})
  miden tu propio cambio de contexto, que tiene un costo cognitivo real.
  Cuidado: el contador de cambios puede inflarse con agentes paralelos
  corriendo en el mismo minuto — el propio dataset lo advierte.
- **4 · Experto:** Los buckets horarios ocultan el entrelazado sub-horario:
  para concurrencia verdadera, usar solapamiento de intervalos de sesión
  (una línea de barrido sobre horarios de inicio y fin). La ley de Little
  (trabajo en progreso = throughput × tiempo de ciclo) enmarca la
  concurrencia. Los costos de cambio de tarea (residuo de atención) pueden
  probarse relacionando la tasa de cambio con la salida.

### 9. Ritmo: heatmap, after-hours, semana a semana (FPA-110–112)

- **1 · ELI5:** Un calendario que muestra cuándo jugás.
- **2 · Cotidiano:** Una grilla día × hora de actividad, más el share de
  trabajo fuera de horario o en fines de semana, y cuánto difiere cada
  semana de la anterior.
- **3 · Practicante:** El heatmap usa la **timezone del reporte**
  (`metadata.timezone`, hoy `{{fig:timezone}}`); el horario laboral está en el config
  (lunes a viernes, 09:00–18:00). Las semanas son ISO, con week-over-week y
  **varianza poblacional excluyendo las semanas de borde parciales** (no
  distorsionar la varianza con semanas incompletas). Hay una alerta de
  staleness (14 días) que avisa si los datos envejecieron, y el timeline de
  tool/model marca pausas mayores que `gap_days` (7 días). La actividad
  nocturna puede ser runs agendados de agentes, no vos.
- **4 · Experto:** El bucketing por hora local se rompe en los cambios de
  horario de verano. La hora del día es una variable circular. Usar métodos
  de change-point (CUSUM) para detectar shifts de ritmo, no inspección
  visual.

### 10 (a). Largo de sesión y `/clear` (FPA-116–118)

- **1 · ELI5:** Un chat largo se pesa como una mochila llena, y `/clear` la
  vacía.
- **2 · Cotidiano:** Sesiones agrupadas por cantidad de turnos (1–10, 11–50,
  51–100, 101–300, 301–500, 500+), el costo de cada grupo, cada cuánto
  limpiás contexto (`/clear` por cada 100 sesiones) y la lista de sesiones
  largas que nunca limpiaron.
- **3 · Practicante:** El umbral de "sesión larga" es configurable
  (`long_turns`, default 100). Mejora pendiente: mediana y percentil 90 de
  costo por grupo; el dashboard hoy lista las sesiones más largas por
  turnos. Compará `/clear` con la compactación como formas de resetear.
- **4 · Experto:** El crecimiento del contexto maneja los tokens de input,
  así que el costo de sesión es convexo en turnos sin caché. Un análisis de
  supervivencia de sesiones (el riesgo de terminar en cada turno) puede
  mostrar en qué cantidad de turnos conviene resetear.

### 10 (b). Adopción de skills y comandos (FPA-113–115)

- **1 · ELI5:** Qué herramientas de tu caja de herramientas agarrás de
  verdad.
- **2 · Cotidiano:** Las skills más usadas (hoy: {{fig:skills_top1}},
  {{fig:skills_top2}}, …), las que se usaron exactamente una vez, y las nunca usadas.
- **3 · Practicante:** "Nunca usadas" requiere que el tracker emita la lista
  de skills instaladas (`skills_installed`); si no la emite, el dashboard
  muestra la categoría como **n/a con la razón** en lugar de inventar
  ceros — hoy es el caso. "Usada una vez" es una señal de prueba-que-se-abandona.
  Podar skills sin uso y mirar tendencias (el uso por mes tampoco se emite
  hoy: n/a razonada).
- **4 · Experto:** Tratar la adopción como un funnel (instalada → probada →
  repetida) y usar curvas de retención por cohorte. El uso está confundido
  por la mezcla de tareas, y atribuir una mejora de resultados a una skill
  requiere un contrafactual o un experimento.

### 11. Ciclo de vida de proyectos (FPA-122, 123)

- **1 · ELI5:** Algunos proyectos están despiertos y otros dormidos. Los
  dormidos no deberían seguir costándote.
- **2 · Cotidiano:** Cada proyecto es nuevo, activo o dormido según los días
  desde su última actividad (umbral del config: 30 días para ambos). El
  dashboard muestra cuánto costo efectivo está sentado en proyectos
  dormidos.
- **3 · Practicante:** La referencia es la **fecha fin del periodo** (para
  que la clasificación sea determinista: el mismo reporte siempre produce la
  misma clasificación). Usalo para triage: terminar, pausar o archivar.
- **4 · Experto:** El dormancy es un outcome censurado (los proyectos pueden
  revivir), así que el análisis de supervivencia ajusta mejor que un corte
  fijo. El costo hundido es solo un diagnóstico. Las decisiones deben
  apoyarse en el valor marginal, para evitar la falacia del costo hundido.

### 12. Resultados: costo por commit y por release (FPA-014, 040)

- **1 · ELI5:** No cuánto le diste de comer al robot, sino qué construyó.
- **2 · Cotidiano:** Costo efectivo dividido por commits, y por releases.
- **3 · Practicante:** Unir el uso a commits y releases de GitHub por
  proyecto y mes (el tracker emite los outcomes cuando existen). Los commits
  difieren mucho en tamaño: usar la razón como tendencia y comparar como con
  como.
- **4 · Experto:** Los commits son un proxy débil e invitan a la ley de
  Goodhart. Medidas mejores: PRs mergeados, lead time, change-failure rate
  (métricas DORA). El uso normalmente precede a los commits: desfasar la
  relación (lag). Las razones con denominadores chicos son inestables.

---

## C. Presupuesto, bridge, forecast y planes

### 13. Varianza de presupuesto y alertas (FPA-050–056, 080–088)

- **1 · ELI5:** Planeaste gastar 10 caramelos y gastaste 12: vas 2 de más.
  Una alarma de humo suena cuando eso pasa.
- **2 · Cotidiano:** La varianza es actual − presupuesto, donde positivo
  significa over. Las alertas se encienden por overspend, saltos de costo
  repentinos o cifras que no reconcilian. Ejemplo real: la alerta
  verify-plan (uso vs precio del plan) disparó en **mayo** (efectivo muy por
  encima del precio del plan) y en **junio** (uso sin plan activo, tras
  cancelar el Pro).
- **3 · Practicante:** El dashboard pro-ratea el presupuesto para meses
  parciales, y muestra un presupuesto efectivo (soft, informativo:
  `effective_monthly` — su overage se ve en la tabla de varianza, no
  alerta) y un presupuesto real **cash** (el activo del config:
  {{fig:budget_cash}}/mes desde {{fig:budget_start}}; solo este dispara
  overspend). Cada alerta muestra su regla, severidad y
  evidencia, y tiene exactamente una acción. Los umbrales salen del
  config (`alert_thresholds`): 25× para verify-plan, 10% para la
  reconciliación cash, 15% de subida de costo unitario mes a mes (con
  base previa mayor a $5/1k — el MoM sobre base casi nula es falso
  positivo), +5 puntos
  de share premium en 3 meses, 50% de concentración top-3, 14 días de
  staleness.
- **4 · Experto:** Un presupuesto estático mezcla volumen y eficiencia. Un
  presupuesto flexible, ajustado al volumen real, los separa. Usar reglas de
  materialidad (umbral relativo y absoluto a la vez) para reducir ruido.
  Para costo unitario, los control charts (EWMA, z-scores robustos) le ganan
  a los umbrales fijos. Desduplicar alertas correlacionadas para evitar
  fatiga.

### 14. Bridge precio-volumen-mix (FPA-060–068)

- **1 · ELI5:** Tu factura subió. ¿Compraste más, compraste cosas más caras,
  o las mismas cosas se pusieron más caras?
- **2 · Cotidiano:** El bridge parte el cambio de costo en tres partes.
  *Volumen*: más acciones. *Mix*: otra mezcla de modelos. *Rate*: otro
  precio por acción dentro del mismo modelo.
- **3 · Practicante:** Las fórmulas exactas del generador (`build_pvm`):
  Volume = (Q1 − Q0) × rate₀, con rate₀ = costo medio previo por
  interacción; Mix = Σ q1ᵢ·p0ᵢ − Q1 × rate₀; Rate = Σ q1ᵢ·(p1ᵢ − p0ᵢ). Los
  tres suman el cambio de costo con identidad verificada dentro de $0.01
  (FPA-065); el residuo de redondeo se absorbe en Mix con falla ruidosa si
  no cuadra. Con mes parcial se usan valores FME y se etiquetan (FPA-066).
  Un modelo nuevo este mes contribuye **solo a Mix** (FPA-064). Ejemplo
  real, derivado del reporte vigente: en {{fig:bridge_mes}} el bridge dio
  Volumen {{fig:bridge_v}}, Mix {{fig:bridge_m}}, Rate {{fig:bridge_r}}.
- **4 · Experto:** Esta descomposición depende del orden (volumen al rate
  medio previo, mix a rates previos, rate a cantidades actuales), y los
  términos de interacción caen en Rate. Una descomposición de punto medio o
  de Shapley elimina la dependencia del orden. El efecto de mix también
  depende de qué tan fino definís la dimensión modelo: categorías más
  gruesas esconden mix.

### 15. Forecast y escenarios (FPA-070–077)

- **1 · ELI5:** Si seguís comiendo galletitas al ritmo de esta semana,
  ¿cuántas para Navidad?
- **2 · Cotidiano:** Tomar el ritmo reciente, proyectarlo hacia adelante, y
  ajustar crecimiento y precio para ver escenarios what-if.
- **3 · Practicante:** Run-rate base = **media de los FME de los últimos 3
  meses con datos**; con menos de 3 meses el forecast es n/a con la razón
  (nunca un forecast inventado). Se muestra actual + outlook contra
  presupuesto, con el resto del mes parcial como fila separada. Los
  escenarios (crecimiento, cambio de precio) se recalculan en el navegador
  sin recargar; los planes futuros salen del calendario de suscripciones
  del config.
- **4 · Experto:** Extrapolando tres puntos hay incertidumbre amplia y se
  pierden los cambios de régimen (un modelo nuevo, un proyecto que termina).
  Presentar rangos o escenarios, no un número único. Considerar
  estacionalidad, evitar ajustar tendencias sobre meses parciales, y usar
  bootstrapping sobre meses para bandas.

### 16. Economía de suscripción (FPA-081, 130–133)

- **1 · ELI5:** Una entrada mensual al buffet solo vale si comés suficiente.
- **2 · Cotidiano:** La utilización es el valor que usaste ÷ el precio del
  plan. El break-even es el uso al que pagar por uso costaría lo mismo que
  el plan.
- **3 · Practicante:** Se computa por periodo del calendario de suscripciones
  (cada entrada con start/end del config): utilización = efectivo del
  periodo ÷ precio del plan **pro-rateado por calendario**; break-even = el
  precio mensual del plan; headroom = fee − efectivo consumido. Se comparan
  4 casos de cash: el cronograma actual, todo pay-per-token (pricing del
  config), todo Pro, todo Max (por tool, con cash no-Claude). La alerta
  verify-plan dispara arriba de **25×** de utilización, y hay un disclaimer
  explícito: la equivalencia por costo efectivo ignora los usage limits del
  plan.
- **4 · Experto:** La elección de plan es una decisión discreta bajo uso
  incierto, con valor de opción. El consumo en un plan con tope está
  censurado: el costo real de un plan incluye el throttling. Un break-even
  distribucional (la probabilidad de que el uso supere el umbral) es más
  honesto que un estimador puntual. El precio de lista no es el costo
  marginal del proveedor: el valor efectivo es una medida del lado del
  usuario.

---

## D. Estructura y calidad de datos

### 17. Roll-up jerárquico (FPA-020–028)

- **1 · ELI5:** Matrioskas: el número grande contiene números más chicos.
- **2 · Cotidiano:** Tres árboles que podés abrir: tiempo (año → trimestre →
  mes), tool (tool → modelo) y portfolio (categoría → proyecto, con drill
  proyecto → modelo).
- **3 · Practicante:** Los padres tienen que dar la suma de sus hijos — el
  roll-up se **verifica en el generador** (FPA-101) y falla ruidosa si no
  cuadra. Cada nivel muestra las mismas columnas. Los proyectos sin
  categoría caen en "Unclassified" (fallback del config). El árbol de tiempo
  usa el costo *real* (cash), comparable con el presupuesto; los otros usan
  el efectivo.
- **4 · Experto:** Las medidas aditivas se acumulan, pero las razones (como
  costo por 1k) deben recalcularse desde numeradores y denominadores
  sumados, jamás promediarse. Múltiples jerarquías forman un cubo OLAP, y
  el drill cruzado necesita hechos al grano más fino (proyecto × modelo ×
  día). La asignación de costos compartidos debe ser explícita.

### 18. Checks de calidad de datos (FPA-017, 041, 140–143)

- **1 · ELI5:** Antes de medir cualquier cosa, comprobá que la regla no
  esté torcida.
- **2 · Cotidiano:** Definir qué cuenta como interacción, mostrar cuántos
  datos quitó el filtro, registrar la timezone, y tratar con cuidado los
  meses incompletos.
- **3 · Practicante:** El dashboard parte las interacciones por tipo
  (prompt, turno del asistente, tool call) y muestra el share de tool
  calls. Reporta el share filtrado por el charly-filter (hoy: {{fig:filtro_count}}
  interacciones fuera del filtro, **{{fig:filtro_share}}**). La timezone
  del bucketing va registrada en el metadata (`{{fig:timezone}}`). Mes completo equivalente (FME) =
  valor × días del mes ÷ días transcurridos (`fme()` exactamente esa
  fórmula); los meses parciales se marcan y se comparan como tasas diarias.
- **4 · Experto:** Los cambios de logging o de comportamiento del agente
  crean quiebres estructurales. Un filtro crea sesgo de selección. El FME
  asume tasa diaria uniforme, que la estacionalidad semanal viola: una
  proyección ajustada por día de semana es mejor. Los periodos faltantes
  (como el 1–10 de enero de este dataset) deben tratarse como datos
  faltantes, no como cero.

### 19. Diseño de interfaz y CTAs (FPA-150–179)

- **1 · ELI5:** Una tienda buena pone lo más útil en la puerta y te dice
  qué hacer después.
- **2 · Cotidiano:** Seis vistas, cada una respondiendo una pregunta. Un
  selector de periodo. Cada alerta tiene un botón. Los lectores reciben una
  invitación principal ("Leer la serie").
- **3 · Practicante:** Títulos de hallazgo ("{{fig:mes_pico}} manejó el {{fig:mes_pico_share}}
  de las interacciones"), top-5 por defecto, sección de método colapsada, URLs
  compartibles, export CSV y SVG, targets táctiles de 44px, tap-to-inspect
  en los charts. Los CTAs viven en el config (`ctas`) y el generador falla
  loud si un target está vacío. Los rangos custom de periodo se
  pre-calculan en Python (los presets YTD/trimestre/rango se re-renderizan
  en JS sin reload, sin matemática nueva en el navegador).
- **4 · Experto:** El progressive disclosure reduce la carga cognitiva. Un
  CTA primario por vista mantiene el camino de decisión despejado. Los
  títulos generados deben salir de métricas y umbrales con nombre, o se
  vuelven las afirmaciones sin respaldo que el review detectó. El estado
  codificado en la URL hace las vistas reproducibles y revisables.

---

## E. Energía estimada

### 20. Energía estimada y banda de caché (FPA-180)

- **1 · ELI5:** Cuánta electricidad hicieron consumir tus conversaciones
  con el robot: hoy unas bombitas LED, no una estufa eléctrica.
- **2 · Cotidiano:** Los proveedores no reportan energía, así que el
  dashboard la **estima** a partir de los tokens. Hoy el periodo suma
  **{{fig:energia_total}} kWh**, con una banda de
  **{{fig:energia_low}}–{{fig:energia_high}} kWh** que muestra cuánto
  cambiaría la estimación si la caché hiciera su trabajo perfecto o
  pésimo. Cada barra mensual lleva
  su banda y la marca del escenario base; los meses sin telemetría de
  tokens se muestran con la razón, nunca como un cero medido.
- **3 · Practicante:** Fórmula del tracker (`energy_method` en metadata):
  `(input + output + cache_write + cache_read × factor) × J/token del tier
  ÷ 3.6e6`, con coeficientes de laboratorio versionados por fecha en el
  config (`energy_coefficients`: flash 0.03 / mid 0.1 / frontier 0.2
  J/token entregado). El factor de cache_read es **el parámetro dominante**:
  la banda recompone el mismo método con factor 0.0 y 1.0
  (`energy_band_cache_factors`), y el escenario base usa el nominal del
  config (hoy {{fig:cache_factor}}). Toda cifra lleva el tag *assumed* —
  son estimaciones de Luccioni et al. / AI Energy Score, jamás mediciones.
  Un ancla útil de contexto: Google reportó 0.24 Wh por prompt mediano de
  Gemini Apps, pero NO calibra uso CLI agéntico (contextos 10–100×
  mayores); usar per-token como método primario.
- **4 · Experto:** La estimación hereda los sesgos de los benchmarks de
  laboratorio (hardware y workload distintos del datacenter real); el
  multiplicador de cachés domina la incertidumbre y merece análisis de
  sensibilidad antes que afinar los coeficientes por modelo. La banda es
  determinista (mismos buckets y versión que el nominal), no un intervalo
  de confianza. Las cifras reportadas por proveedores (p.ej. la reducción
  per-query que reporta Google) usan límites de sistema distintos y no son
  directamente comparables; el disclosure obligatorio (EU AI Act Art. 53)
  va a mejorar la base de comparación con el tiempo.

---

## F. Efectividad del toolset (ddl)

### 21. Efecto del toolset ddl: lazo de feedback, errores y calidad (ddl-effect)

- **1 · ELI5:** Cuando tu caja de herramientas mejoró, ¿trabajabas mejor?
  Este panel compara los meses de antes y después en tres cosas que se
  pueden contar: cuánto tardás en recuperarte de un error, cuántos errores
  aparecen, y si el trabajo deja rastro limpio (tests, arreglos, menos
  vueltas atrás).
- **2 · Cotidiano:** El panel (`data/ddl-dashboard.html`) mira repo por
  repo y mes a mes. En el **lazo de feedback** mide la mediana de
  interacciones entre un error y la siguiente invocación exitosa del mismo
  tool (`recovery_p50_interactions`, con su tasa de censura visible — los
  eventos que terminan la sesión sin recuperarse no se cuentan en la
  mediana), la latencia sesión→commit en horas y la tasa de CI fallida
  (runs de GitHub Actions, excluyendo dependabot). En **errores** trae las
  tasas por 100 interacciones con el denominador a la vista. En
  **calidad** muestra cuatro proxies separados: ratio de reverts, ratio de
  fixes, ratio de commits con test y hallazgos de `ah check` — sin score
  compuesto, porque combinarlos inventaría una métrica que nadie midió.
  Los meses sin señal muestran la razón, nunca un cero disfrazado.
- **3 · Practicante:** Los estimadores del panel: serie interrumpida
  (ITS) pre/post adopción con media móvil trimestral e intervalo bootstrap
  de 10,000 resamples (seed fija, reproducible), diferencia-en-diferencias
  2×2 contra repos control (definidos por ausencia de `.ddl/` + topic,
  nunca por dosis 0) y dosis-respuesta con correlación de Spearman. Regla
  dura: celdas con n < 3 no se grafican. La dosis (0–1) viene de la
  fracción de herramientas del fleet con estado sano según `ddl status` y
  canarios git.
- **4 · Experto:** El diseño es **asociativo, no causal**: los ITS/DiD
  comparan ventanas dentro del mismo repo y contra control, pero la
  adopción de ddl correlaciona con la atención del mantenedor — confusor
  no controlable que el disclosure del panel declara en banda fija. Los
  confusores medidos son madurez del repo y mezcla de modelos. Cada proxy
  declara su sesgo (el revert ratio subestima — no todo revert usa `git
  revert`; la latencia sesión→commit hereda el sesgo de reloj entre
  máquina y servidor, clampeado a 0 y contado en metadata). Toda cifra
  lleva tag de procedencia: reported/derived (fuentes primarias y
  derivadas locales), cached (gh/terapia con fecha de fetch) o missing
  (null con razón). Los IC bootstrap con n ~3 son gruesos por diseño —
  es honestidad, no bug.

