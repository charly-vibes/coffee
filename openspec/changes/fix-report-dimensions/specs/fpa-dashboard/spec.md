## MODIFIED Requirements

### Requirement: calidad de datos y documentación

El Dashboard SHALL (debe): definir "interaction" en Data y mostrar el share de
tool-calls; mostrar el share filtrado por el charly-filter con unidades
separadas; mostrar la timezone registrada o marcar vistas hora/día como
unverified; renderizar las cifras citadas en README/docs desde el JSON o
fallar en `--check-docs`; numerar figuras y tablas sin duplicados; documentar
el método de render; no renderizar placeholders vacíos; derivar labels de
periodo decorativos del periodo real de los datos; y usar un glosario
canónico de `source`/`provider`/`plan` en labels, índice y guía.

#### Scenario: check-docs detecta cifra obsoleta

- **WHEN** una cifra citada en el README difiere del JSON y se corre `--check-docs`
- **THEN** el generador falla listando la cifra en desacuerdo

#### Scenario: glosario de dimensiones

- **WHEN** se muestra un conteo de "proveedores" o "herramientas"
- **THEN** el label indica la dimensión real (`source`, `provider` o `plan`)
  y el índice deriva el número de la misma dimensión que el dashboard

#### Scenario: unidades del filtro

- **WHEN** se muestra el share filtrado
- **THEN** se ven interacciones, sesiones y archivos por separado; el share
  usa interacciones y declara el denominador