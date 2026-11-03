"""Homologación de códigos de Nobis Salud (O.S. 62).

Mismo mecanismo que `obras/sancor/homologador.py` — ver ese archivo para el
detalle de qué cambia y qué no. En una línea: la homologación afecta **sólo el
código que se le manda** a Nobis; lo que se cotiza, se graba en
`detalle_facturacion.cod_nom` y se factura es siempre el del Colegio.

## Casos homologados

- `320101` — Nobis no la reconoce tal cual: el Colegio resolvió mandarle
  `420132`, para cualquier especialidad. Es el mismo código que recibe Sancor.
  `420132` vive bajo el nomenclador `99` (forma ORIGEN, ver `cliente.py`), que es
  con la que Nobis lo autoriza.

Un código que **no** figure acá no tiene homologación: se envía tal cual.
"""

# Mismo formato que `obras/sancor/homologador.py`: clave = código del Colegio;
# valor = a qué código se traduce para hablar con Nobis, según la especialidad
# principal. `especialidad: None` aplica a cualquier especialidad.
HOMOLOGACIONES: dict[str, list[dict]] = {
    "320101": [{"codigo_homologado": "420132", "especialidad": None}],
}


def _validar(tabla: dict[str, list[dict]]) -> None:
    """Corta al importar si la tabla quedó ambigua (se edita a mano)."""
    for codigo, entradas in tabla.items():
        if not entradas:
            raise RuntimeError(f"Homologación de Nobis sin entradas para {codigo!r}.")

        vistas: set[int | None] = set()
        for entrada in entradas:
            destino = entrada.get("codigo_homologado")
            especialidad = entrada.get("especialidad")

            if not destino:
                raise RuntimeError(
                    f"Homologación de Nobis para {codigo!r} sin `codigo_homologado`."
                )
            if destino == codigo:
                raise RuntimeError(
                    f"Homologación de Nobis de {codigo!r} a sí mismo: sacala de la tabla."
                )
            if especialidad is not None and not isinstance(especialidad, int):
                raise RuntimeError(
                    f"Homologación de Nobis para {codigo!r}: `especialidad` tiene que "
                    f"ser int o None, no {type(especialidad).__name__}."
                )
            if especialidad in vistas:
                cual = "sin especialidad" if especialidad is None else f"especialidad {especialidad}"
                raise RuntimeError(
                    f"Homologación de Nobis duplicada para {codigo!r} ({cual})."
                )
            vistas.add(especialidad)


_validar(HOMOLOGACIONES)


def homologar(codigo: str, especialidad: int | None) -> tuple[str, str | None]:
    """Traduce el código del Colegio al que hay que mandarle a Nobis.

    Devuelve `(código a enviar, código del Colegio si hubo homologación)` —
    mismo contrato que `ValidadorOS.homologar()`. Gana la entrada de la
    especialidad del médico; si no hay, la de `especialidad: None`; si tampoco,
    el código se manda tal cual.
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
