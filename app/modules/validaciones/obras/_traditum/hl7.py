"""Lectura de mensajes HL7 v2.4 que vuelven por el canal Traditum.

Es genérico a propósito: los financiadores del canal comparten la gramática
(segmentos separados por CR/LF, campos por `|`, componentes por `^`) y lo que
cambia es qué segmentos usa cada uno. El significado de negocio —qué código de
ZAU autoriza y cuál rechaza— vive en el módulo de la obra social.

Diferencia con el lector de Sancor, que hace lo mismo pero no se puede reusar:
allá la respuesta viene envuelta en SOAP con los CR escapados como `&#xD;` y se
toma **el último** segmento de cada tipo. Acá el mensaje llega plano y, sobre
todo, hay que leer **todos** los ZAU y en orden, porque Medicus manda uno de
cabecera y uno por cada práctica (ver `emparejar_por_practica`).
"""
from dataclasses import dataclass, field


def segmentos(hl7: str) -> list[list[str]]:
    """El mensaje como lista de segmentos, cada uno ya partido en campos.

    `campos[0]` es el tipo (`MSH`, `ZAU`, …) y `campos[i]` el campo HL7 `i` —
    ojo que la numeración del estándar es 1-based sobre lo que sigue al tipo,
    así que `ZAU-2` es `campos[2]`. Se aceptan CR, LF y CRLF como separador
    porque los ejemplos del documento usan las tres formas.
    """
    salida: list[list[str]] = []
    for linea in hl7.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        linea = linea.strip()
        if linea:
            salida.append(linea.split("|"))
    return salida


def campo(seg: list[str], i: int) -> str:
    """Campo `i` del segmento, o "" si el mensaje lo omitió."""
    return seg[i].strip() if i < len(seg) else ""


def componente(valor: str, i: int) -> str:
    """Componente `i` (1-based) de un campo separado por `^`."""
    partes = valor.split("^")
    return partes[i - 1].strip() if i <= len(partes) else ""


def primero(hl7: str, tipo: str) -> list[str] | None:
    for seg in segmentos(hl7):
        if seg and seg[0] == tipo:
            return seg
    return None


@dataclass
class EstadoZau:
    """Lo que dice un segmento ZAU: número de transacción, código y texto.

    `ZAU-2` es el número de transacción y `ZAU-3` viene como
    `<código>^<descripción>` (por ejemplo `B000^AUTORIZADO`).
    """

    transaccion: str = ""
    codigo: str = ""
    descripcion: str = ""
    # Copago de la práctica: ZAU-6.1.1.1, con la moneda en .2.
    copago: str = ""

    @classmethod
    def desde(cls, seg: list[str]) -> "EstadoZau":
        estado = campo(seg, 3)
        crudo = campo(seg, 2)
        return cls(
            # "0" es el centinela de "sin número" que ya usa Sancor; se descarta.
            transaccion=crudo if crudo and crudo != "0" else "",
            codigo=componente(estado, 1),
            descripcion=componente(estado, 2),
            copago=componente(campo(seg, 6), 1),
        )


@dataclass
class PracticaRespondida:
    """Una práctica de la respuesta, con el ZAU que le corresponde."""

    codigo: str = ""
    descripcion: str = ""
    cantidad_aprobada: int = 0
    estado: EstadoZau = field(default_factory=EstadoZau)


def emparejar_por_practica(hl7: str) -> tuple[EstadoZau, list[PracticaRespondida]]:
    """Separa el ZAU de cabecera de los ZAU por práctica.

    **Esta es la parte delicada del protocolo.** Medicus devuelve un ZAU global
    con el resultado de la transacción y después, por cada `PR1`, su propio
    `AUT` y su propio `ZAU`. En una autorización parcial la cabecera dice
    `B001^AUTORIZADO PARCIALMENTE`, la primera práctica puede venir rechazada
    (`P245`) y la segunda autorizada (`B000`) — ejemplo de la pág. 10 del anexo.

    Quedarse con un solo ZAU (el primero o el último, como alcanza para Sancor)
    da un resultado incorrecto en cuanto haya más de una práctica. Y el número
    de transacción de cada ítem es el que después hace falta para anularlo
    individualmente.

    Devuelve `(cabecera, practicas)`. Si el mensaje no trae `PR1` —elegibilidad,
    anulación— la lista viene vacía y todo el resultado está en la cabecera.
    """
    cabecera = EstadoZau()
    practicas: list[PracticaRespondida] = []
    actual: PracticaRespondida | None = None
    vio_pr1 = False

    for seg in segmentos(hl7):
        if not seg:
            continue
        tipo = seg[0]

        if tipo == "PR1":
            vio_pr1 = True
            prestacion = campo(seg, 3)
            actual = PracticaRespondida(
                codigo=componente(prestacion, 1),
                descripcion=componente(prestacion, 2),
            )
            practicas.append(actual)

        elif tipo == "AUT" and actual is not None:
            # AUT-9 es la cantidad aprobada de la práctica que se está leyendo.
            aprobada = campo(seg, 9)
            actual.cantidad_aprobada = int(aprobada) if aprobada.isdigit() else 0

        elif tipo == "ZAU":
            estado = EstadoZau.desde(seg)
            if not vio_pr1:
                # Todavía no empezaron las prácticas: es el ZAU de cabecera.
                cabecera = estado
            elif actual is not None:
                actual.estado = estado

    return cabecera, practicas


def nombre_afiliado(hl7: str) -> str:
    """PID-5, `APELLIDO^NOMBRE`. "" cuando viene el relleno `UNKNOWN`."""
    seg = primero(hl7, "PID")
    if not seg:
        return ""
    crudo = campo(seg, 5).replace("^", " ").strip()
    return "" if crudo.upper().startswith("UNKNOWN") else crudo[:100]


def plan_afiliado(hl7: str) -> str:
    """IN1-2, `<código>^<descripción>` del plan."""
    seg = primero(hl7, "IN1")
    if not seg:
        return ""
    return campo(seg, 2).replace("^", " ").strip()[:100]


def motivo_alternativo(hl7: str) -> str:
    """Texto de respaldo cuando el ZAU no trae descripción.

    Mismo criterio que Sancor: antes de dar un genérico se mira `ERR` y `MSA`,
    para no perder en silencio el motivo real.
    """
    for tipo in ("ERR", "MSA"):
        seg = primero(hl7, tipo)
        if seg:
            texto = " ".join(c.strip() for c in seg[1:] if c.strip())
            if texto:
                return texto[:250]
    return ""
