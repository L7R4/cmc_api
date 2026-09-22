"""Homologación de códigos de OSPJN (O.S. 151).

Mismo mecanismo que `obras/sancor/homologador.py` — ver ese archivo para el
razonamiento completo. Acá el resumen aplicado a OSPJN:

## Qué cambia y qué no

El código del Colegio —el que el médico eligió— sigue siendo el que se
cotiza, el que se graba en `detalle_facturacion.cod_nom` y el que se
factura. La homologación afecta **sólo la categoría** que se le informa a
OSPJN (`CodigoPrestacion`, que para esta obra social no es un código sino
'CON'/'OTR' — ver `obras/ospjn/cliente.py::categoria_de_codigo`).

## Casos homologados

- `320101` ("Atención prematuro hasta 1500 grs.") es categoría 'OTR' por
  número (no empieza con '42'). OSPJN no reconoce esa práctica como una
  consulta y no hay forma de validarla en línea con su categoría real. El
  Colegio resolvió homologarla contra `420351` ("Consulta especializada"),
  que sí es 'CON' — el médico sigue facturando y cobrando 320101, pero la
  validación de elegibilidad contra OSPJN se hace como si fuera una consulta.
- `320002` — mismo criterio, homologado contra `420232`.

Un código que **no** figure acá no tiene homologación: usa la categoría por
defecto de `validador.py` (hoy, siempre 'CON' — ver el comentario ahí sobre
por qué 'OTR' no está activado todavía).
"""

# Mismo formato que `obras/sancor/homologador.py`: clave = código del
# Colegio, valor = lista de entradas por especialidad principal (`None` =
# cualquier especialidad).
HOMOLOGACIONES: dict[str, list[dict]] = {
    "320101": [{"codigo_homologado": "420351", "especialidad": None}],
    "320002": [{"codigo_homologado": "420232", "especialidad": None}],
}


def _validar(tabla: dict[str, list[dict]]) -> None:
    """Corta al importar si la tabla quedó ambigua — mismo criterio que Sancor:
    se edita a mano, así que un error tiene que romper el arranque."""
    for codigo, entradas in tabla.items():
        if not entradas:
            raise RuntimeError(f"Homologación de OSPJN sin entradas para {codigo!r}.")

        vistas: set[int | None] = set()
        for entrada in entradas:
            destino = entrada.get("codigo_homologado")
            especialidad = entrada.get("especialidad")

            if not destino:
                raise RuntimeError(
                    f"Homologación de OSPJN para {codigo!r} sin `codigo_homologado`."
                )
            if destino == codigo:
                raise RuntimeError(
                    f"Homologación de OSPJN de {codigo!r} a sí mismo: sacala de la tabla."
                )
            if especialidad is not None and not isinstance(especialidad, int):
                raise RuntimeError(
                    f"Homologación de OSPJN para {codigo!r}: `especialidad` tiene que "
                    f"ser int o None, no {type(especialidad).__name__}."
                )
            if especialidad in vistas:
                cual = "sin especialidad" if especialidad is None else f"especialidad {especialidad}"
                raise RuntimeError(
                    f"Homologación de OSPJN duplicada para {codigo!r} ({cual})."
                )
            vistas.add(especialidad)


_validar(HOMOLOGACIONES)


def homologar(codigo: str, especialidad: int | None) -> tuple[str, str | None]:
    """Traduce el código del Colegio al que hay que usar para categorizar la
    validación ante OSPJN.

    Devuelve `(código a enviar, código del Colegio si hubo homologación)` —
    mismo contrato que `ValidadorOS.homologar()`. Gana la entrada de la
    especialidad del médico; si no hay, la de `especialidad: None`; si
    tampoco, el código se usa tal cual (sin homologación).
    """
    entradas = HOMOLOGACIONES.get(codigo)
    if not entradas:
        return (codigo, None)

    por_defecto: str | None = None
    for entrada in entradas:
        esp = entrada.get("especialidad")
        if esp is None:
            por_defecto = entrada["codigo_homologado"]
        elif especialidad is not None and esp == especialidad:
            return (entrada["codigo_homologado"], codigo)

    if por_defecto:
        return (por_defecto, codigo)
    return (codigo, None)
