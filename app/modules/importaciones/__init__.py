"""Importaciones masivas de prestaciones desde reportes de obras sociales.

## Por qué existe, separado de `validaciones/`

`validaciones/` resuelve el caso normal: el médico valida **una** prestación
contra la obra social, en línea, desde su panel. Cada alta pasa por un
`ValidadorOS` y termina en `grabar_prestacion`.

Hay obras sociales que no funcionan así. Prevención Salud no expone validación
por prestación: manda un reporte mensual del Colegio entero, con las prácticas
ya autorizadas de todos los médicos, y hay que repartirlo. Eso no es una
validación —no hay nada que preguntarle a nadie— sino una importación: cientos
de filas, de cientos de médicos, cargadas por un administrativo.

Meterlo en `validaciones/` habría roto las dos premisas de ese módulo: que la
prestación es del médico logueado y que hay un validador que responde.

## Cómo se reparte el trabajo con el front

El **archivo lo parsea el front**, con el mismo lector que ya usa para mostrar
el reporte en pantalla. Acá llegan las filas en JSON. No es una delegación de
confianza: el archivo no es autoridad sobre nada. El backend vuelve a resolver
el médico (por matrícula, contra `listado_medico`), el código (contra el
nomenclador de la obra social) y el precio (con el mismo `resolver_precio` que
usa facturación). Del reporte sólo se toman los datos que únicamente él tiene:
la autorización, la fecha, el paciente y el estado.

La alternativa —parsear el .xlsx acá— habría significado un segundo parser en
otro lenguaje, que se desincroniza del primero, más una dependencia nueva para
los `.xls` legacy y los `.csv` que el front ya acepta.

## Dos pasos, siempre

`previsualizar` no escribe nada: devuelve, fila por fila, qué se grabaría y qué
no y por qué. `confirmar` recibe lo mismo y graba. El administrativo ve el
reparto antes de que exista, que es donde se detecta una matrícula que no cayó
en ningún médico o un código que la obra social no tiene cotizado.
"""
