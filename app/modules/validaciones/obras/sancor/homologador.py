"""Homologación de códigos de Sancor Salud (O.S. 411).

Sancor no acepta ciertos códigos del nomenclador del Colegio tal cual: según la
especialidad del médico hay que mandarle otro. A eso el Colegio le llama
**homologar**.

## Qué cambia y qué no

La homologación afecta **sólo lo que se transmite** a la obra social. El código
del Colegio —el que el médico eligió— sigue siendo el que se cotiza, el que se
graba en `detalle_facturacion.cod_nom` y el que se factura. Es también el que se
usa para el chequeo de habilitación por especialidad: el médico factura lo que
le corresponde por su especialidad, y por detrás Sancor recibe otro código.

Antes de esto la sustitución vivía en `cliente.py` y el alta cotizaba **el
sustituto**, mientras que el buscador de códigos cotizaba el del Colegio. Un
médico con especialidad 41 elegía `420302`, veía $ 0,00 en pantalla y se le
facturaban $ 27.560 de `420351`. Al facturar el código del Colegio esa
divergencia no se arregla: desaparece, porque el buscador ya muestra el número
correcto.

## Cómo se edita

Es una tabla a mano, pensada para que el Colegio la vaya llenando a medida que
resuelve cada homologación con la obra social. `nm_homologador` existe en la
base pero está vacía y no sirve como destino directo: mapea `codigo_origen →
nomenclador_id` (la dirección inversa, para importar archivos de la O.S.) y no
tiene columna de especialidad.

Un código que **no** figure acá no tiene homologación: se envía tal cual.

## Ojo con el legacy

Los dos archivos vivos del sistema viejo —`grabar_prestacion_Sancor.php` y
`validarsancor_colegio.php`, ambos del 2026-03-10— no coinciden entre sí ni
entre slots de especialidad, y `validarsancor_1.php`, que es el que cita la
documentación, quedó desactualizado un año. Lo de acá es la unificación
acordada, no una copia de ninguno de los tres.

`070660 → 070715` (especialidad 16) **queda deliberadamente afuera**: el camino
del médico del legacy vivo no lo homologa y el del Colegio sí, con una
diferencia de precio de 3x. Se resuelve con el Colegio antes de agregarlo.
"""

# Clave: el código del Colegio que elige el médico.
# Valor: a qué código se traduce para hablar con Sancor, según la especialidad
# principal (`NRO_ESPECIALIDAD`). `especialidad: None` aplica a cualquier
# especialidad y se usa cuando no hay una entrada específica.
HOMOLOGACIONES: dict[str, list[dict]] = {
    "420302": [{"codigo_homologado": "420351", "especialidad": 41}],
    "420130": [{"codigo_homologado": "305001", "especialidad": 33}],
    # Encontrada al revisar `grabar_prestacion_Sancor.php`: viva desde marzo de
    # 2026 y sólo en el camino del médico. No estaba implementada.
    "420305": [{"codigo_homologado": "420101", "especialidad": 15}],
}


def _validar(tabla: dict[str, list[dict]]) -> None:
    """Corta al importar si la tabla quedó ambigua.

    Se edita a mano, así que un error tiene que romper el arranque y no
    resolverse eligiendo la primera entrada en silencio — mismo criterio que
    usa `validaciones/routes.py` al validar los prefijos de cada router.
    """
    for codigo, entradas in tabla.items():
        if not entradas:
            raise RuntimeError(f"Homologación de Sancor sin entradas para {codigo!r}.")

        vistas: set[int | None] = set()
        for entrada in entradas:
            destino = entrada.get("codigo_homologado")
            especialidad = entrada.get("especialidad")

            if not destino:
                raise RuntimeError(
                    f"Homologación de Sancor para {codigo!r} sin `codigo_homologado`."
                )
            if destino == codigo:
                raise RuntimeError(
                    f"Homologación de Sancor de {codigo!r} a sí mismo: sacala de la tabla."
                )
            if especialidad is not None and not isinstance(especialidad, int):
                raise RuntimeError(
                    f"Homologación de Sancor para {codigo!r}: `especialidad` tiene que "
                    f"ser int o None, no {type(especialidad).__name__}."
                )
            if especialidad in vistas:
                cual = "sin especialidad" if especialidad is None else f"especialidad {especialidad}"
                raise RuntimeError(
                    f"Homologación de Sancor duplicada para {codigo!r} ({cual})."
                )
            vistas.add(especialidad)


_validar(HOMOLOGACIONES)


def homologar(codigo: str, especialidad: int | None) -> tuple[str, str | None]:
    """Traduce el código del Colegio al que hay que mandarle a Sancor.

    Devuelve `(código a enviar, código del Colegio si hubo homologación)`. El
    segundo valor es `None` cuando no hubo, para que el llamador distinga sin
    comparar strings.

    Gana la entrada de la especialidad del médico; si no hay, la de
    `especialidad: None`; si tampoco, el código se manda tal cual.
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
