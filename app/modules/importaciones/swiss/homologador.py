"""Traducción de los códigos de Swiss Medical a los del Colegio.

## Por qué hace falta

Swiss no usa el nomenclador del Colegio. En el reporte de liquidación de
agosto/septiembre de 2026 hay 70 códigos distintos: 37 vienen en el formato
del Colegio (seis dígitos) y 33 en el suyo, de ocho. Los de ocho no existen en
`nm_nomenclador` — buscarlos ahí devuelve 404 y la fila no se puede cotizar.

## Dónde vive el mapeo

En este archivo y no en la base, por la misma decisión que se tomó para
Medicus: un homologador es una tabla de traducción entre dos convenios, se
revisa en el código y se versiona con él. `nm_homologador` existe en el
esquema pero está vacía, y llenarla implicaría una migración de datos para
algo que cambia cuando cambia el convenio, no cuando cambia el sistema.

## La regla, y por qué es provisional

Los ocho dígitos son el código del Colegio más un sufijo de dos: `42010100` es
`420101` + `00`, `18010401` es `180104` + `01`. Verificado contra el archivo
real: de los 58 códigos que salen de truncar, 57 existen en `nm_nomenclador`.

**Truncar pierde información.** `42010100` y `42010104` caen los dos en
`420101`, y no sabemos si Swiss los factura distinto. Por eso `EXCEPCIONES`
está arriba: cuando el Colegio confirme qué significa cada sufijo, las
variantes que no sean equivalentes se listan ahí una por una, y la regla
general queda sólo para las que sí lo son.
"""
import re

# Códigos de Swiss cuya traducción NO es "los primeros seis dígitos".
# Se cargan a mano a medida que el Colegio los confirme con la obra social.
EXCEPCIONES: dict[str, str] = {}

_SOLO_DIGITOS = re.compile(r"^\d+$")


def homologar(codigo: str) -> str:
    """El código del Colegio equivalente. Devuelve "" si no se puede traducir.

    Los de seis dígitos ya vienen en formato del Colegio y pasan tal cual.
    """
    c = (codigo or "").strip()
    if not c:
        return ""

    if c in EXCEPCIONES:
        return EXCEPCIONES[c]

    # Un código no numérico no se adivina: mejor que la fila quede sin cotizar
    # y se vea en la previsualización, a inventarle una traducción.
    if not _SOLO_DIGITOS.match(c):
        return ""

    if len(c) <= 6:
        return c

    return c[:6]


def fue_truncado(codigo: str) -> bool:
    """`True` si hubo que recortar: la fila merece una nota en la revisión."""
    c = (codigo or "").strip()
    return (
        c not in EXCEPCIONES
        and bool(_SOLO_DIGITOS.match(c))
        and len(c) > 6
    )
