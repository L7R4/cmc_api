"""Canal Traditum — transporte compartido entre financiadores.

Traditum es una pasarela multi-financiador: la misma API transporta mensajes
para Medicus, Swiss Medical, OBSBA y Medifé, y quién los recibe lo decide el
propio mensaje (MSH-5/MSH-6), no el transporte.

Por eso esto no es una obra social: no se registra en `obras.VALIDADORES` ni
implementa `ValidadorOS`. Es la capa que usan los validadores que hablan por
este canal — hoy sólo `obras/medicus/`.
"""
