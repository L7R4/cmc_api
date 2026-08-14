"""Lo que cada obra social escribe distinto en `guardar_atencion`.

Este archivo es el **único** lugar donde vive el conocimiento de las rarezas del
sistema viejo. Es un registro propio, paralelo al de `obras/` y con la misma
clave (el número de obra social), a propósito: `ValidadorOS` no se toca para
agregarle nada del legacy, así que el día que el espejo se apague alcanza con
borrar `legacy/` entero y no queda un solo campo huérfano en el sistema nuevo.

## De dónde salen estos valores

De contrastar el PHP (`grabar_prestacion_*.php`, `graba_atencion_*.php`) contra
las filas reales de producción, que es lo que manda cuando los dos difieren —
hay varios caminos de carga por obra social y no todos escriben lo mismo.

Sólo se parametriza lo que **de verdad varía**. El resto del mapeo es común y
vive en `mapeo.py`.
"""
from typing import NamedTuple, Optional

# `SANATORIO` y `PACIENTE` son varchar NOT NULL que el legacy nunca dejó vacíos:
# cuando no hay clínica ni paciente que poner, mete un carácter de relleno. El
# valor concreto ("a", "A", "1") no significa nada y cambia según qué PHP grabó
# la fila; se replica igual para no introducir un valor que el legacy nunca vio.
RELLENO_A_MINUSCULA = "a"
RELLENO_A_MAYUSCULA = "A"
RELLENO_UNO = "1"


class PerfilLegacy(NamedTuple):
    """Cómo asienta una obra social en `guardar_atencion`.

    **No hay traducción de números.** Los dos sistemas usan el mismo
    `NRO_OBRASOCIAL` para todas, así que el espejo copia `cod_obr` tal cual.
    Hubo un momento en que Nobis divergía —el sistema nuevo la registraba como
    402 y el viejo como 62— y este perfil llegó a tener un campo `nro_legacy`
    para traducirla; se arregló el número en `obras/nobis/validador.py` y el
    campo se sacó a propósito: sin él es imposible que el espejo escriba una
    obra social distinta de la que se cargó.
    """

    # `PACIENTE`: algunas obras sociales repiten ahí el nombre del afiliado y
    # otras dejan relleno. `None` = repetir el nombre del afiliado.
    paciente: Optional[str]

    # `SANATORIO`: siempre relleno — estas prestaciones no tienen clínica.
    sanatorio: str

    # `FECHASUSPENSION`: varchar(10), no date. Las obras sociales que validan en
    # línea guardan ahí la fecha de la prestación; las de carga manual, relleno.
    fechasuspension_con_fecha: bool

    # `NOMBRE_ARCHIVO`: 'A1' salvo OSPM, que reusa la columna como marca de
    # estado de su propia pantalla.
    nombre_archivo: str = "A1"


_COMUN = {
    "paciente": RELLENO_A_MAYUSCULA,
    "sanatorio": RELLENO_A_MAYUSCULA,
    "fechasuspension_con_fecha": False,
}

# Carga manual y en línea comparten perfil cuando la obra social repite el
# nombre del afiliado en `PACIENTE`, que es lo que hacen las tres de abajo.
_CON_NOMBRE_EN_PACIENTE = {
    "paciente": None,
    "sanatorio": RELLENO_A_MAYUSCULA,
    "fechasuspension_con_fecha": False,
}

PERFILES: dict[int, PerfilLegacy] = {
    # Sancor — valida en línea. Único caso con relleno en minúscula.
    411: PerfilLegacy(
        paciente=RELLENO_A_MINUSCULA,
        sanatorio=RELLENO_A_MINUSCULA,
        fechasuspension_con_fecha=True,
    ),
    # OSPJN · Poder Judicial — valida en línea. Rellena con "1", no con "A".
    151: PerfilLegacy(
        paciente=RELLENO_UNO,
        sanatorio=RELLENO_UNO,
        fechasuspension_con_fecha=True,
    ),
    # OSPM — validación contra padrón propio. Reusa NOMBRE_ARCHIVO como estado.
    433: PerfilLegacy(nombre_archivo="VALIDADO", **_COMUN),
    # Boreal — carga manual.
    285: PerfilLegacy(**_CON_NOMBRE_EN_PACIENTE),
    # Omint — carga manual.
    243: PerfilLegacy(**_CON_NOMBRE_EN_PACIENTE),
    # Nobis — valida en línea contra el WSGeCROS.
    62: PerfilLegacy(**_CON_NOMBRE_EN_PACIENTE),
}


def perfil_de(obra_social_id: int) -> Optional[PerfilLegacy]:
    """Perfil de esa obra social, o `None` si no está espejada.

    Devolver `None` en vez de levantar es deliberado: una obra social que se
    sume a `obras/` y todavía no tenga perfil acá tiene que poder cargarse en el
    sistema nuevo igual. El espejo se saltea y queda registrado en el log —
    nunca bloquea la carga. Ver `espejo.replicar_alta`.
    """
    return PERFILES.get(obra_social_id)
