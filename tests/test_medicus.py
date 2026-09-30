"""Lectura de las respuestas de Medicus.

Los casos salen textuales del anexo «Mensajería HL7 (Anexo Medicus)», rev. 03:
cada transacción del documento trae su input y sus salidas autorizada, parcial y
rechazada, así que sirven de fixtures sin inventar nada.

El caso que importa de verdad es `test_parcial_usa_el_zau_de_la_practica`: es el
único donde quedarse con un solo ZAU —el primero o el último— da un resultado
incorrecto.
"""
from decimal import Decimal

from app.modules.validaciones.obras._traditum import hl7
from app.modules.validaciones.obras.medicus import cliente


def _msg(*lineas: str) -> str:
    return "\r\n".join(lineas)


# ── Elegibilidad (ZQI^Z01), pág. 8 ────────────────────────────────────────────

ELEGIBILIDAD_OK = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161503||ZPI^Z01^ZPI_Z01|18543798|P|2.4|||NE|NE|ARG",
    "MSA|AA|19071016132559977199",
    "ZAU||18543798|B000^SOCIO VALIDO",
    "PRD|PS~HL70454|PRESTADOR, PRUEBA^|||||2007374865901^PR",
    "PID|1||09227263000^^^^HC||GENERICO CAPITAL^GRAVADO",
    "IN1|1|C1_9999^CARTILLA AZUL SIEMEN|610109",
    "ZIN|Y|GRAV^GRAVADO",
)

ELEGIBILIDAD_RECHAZADA = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20110512145849||ZPI^Z01^ZPI_Z01|6491647|P|2.4|||NE|NE|ARG",
    "MSA|AA|11040313092558877199",
    "ZAU||6491647|M005^SOCIO INEXISTENTE",
    "PRD|PS~HL70454||||||2007374865901^PR",
    "PID|1||00000000000^^^^HC||^",
    "IN1|1||610109",
    "ZIN|N|",
)


def test_elegibilidad_socio_valido():
    res = cliente.interpretar(ELEGIBILIDAD_OK)
    assert res.autorizada is True
    assert res.codigo_resultado == "B000"
    assert res.estado_detalle == "SOCIO VALIDO"
    assert res.nro_transaccion == "18543798"
    assert res.nombre_afiliado == "GENERICO CAPITAL GRAVADO"
    assert res.plan == "C1_9999 CARTILLA AZUL SIEMEN"


def test_elegibilidad_socio_inexistente():
    res = cliente.interpretar(ELEGIBILIDAD_RECHAZADA)
    assert res.autorizada is False
    assert res.codigo_resultado == "M005"
    assert res.estado_detalle == "SOCIO INEXISTENTE"
    # PID-5 viene como "^": no hay nombre que mostrar.
    assert res.nombre_afiliado == ""


# ── Autorización de una práctica (ZQA^Z02), pág. 9 ────────────────────────────

AUTORIZADA = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161634||ZPA^Z02^ZPA_Z02|18543817|P|2.4|||NE|NE|ARG",
    "MSA|AA|19071016152599540697",
    "AUT|C1_9999^CARTILLA AZUL SIEMEN|610109||20190710||18543817",
    "ZAU||18543817|B000^AUTORIZADO",
    "PRD|PS~HL70454|PRESTADOR, PRUEBA^|||||2007374865901^CU",
    "PID|1||09227263000^^^^HC||GENERICO CAPITAL^GRAVADO",
    "IN1|1|C1_9999^CARTILLA AZUL SIEMEN|610109",
    "ZIN|Y|GRAV^GRAVADO",
    "PR1|1||9042500200^CONSULTA COMPLETA OFTALMOLOGIC^||20190710",
    "AUT||610109||||||1|1",
    "ZAU||197618424|B000^AUTORIZADO",
)

NO_AUTORIZADA = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20110512151310||ZPA^Z02^ZPA_Z02|6491756|P|2.4|||NE|NE|ARG",
    "MSA|AA|11042512395299540697",
    "AUT|C1_9999^CARTILLA AZUL SIEMEN|610109||20110425||6491756",
    "ZAU||18543817|M000^NO AUTORIZADO",
    "PRD|PS~HL70454|PRESTADOR, PRUEBA^|||||2007374865901^CU",
    "PID|1||09227263000^^^^HC||GENERICO CAPITAL^GRAVADO",
    "IN1|1|C1_9999^CARTILLA AZUL SIEMEN|610109",
    "ZIN|N|GRAV^GRAVADO",
    "PR1|1||90425002^||20110425",
    "AUT||610109||||||1|0",
    "ZAU|||M000^Prestacion de homologacion 90425002 Inexistente o no activa",
)


def test_autorizacion_simple():
    res = cliente.interpretar(AUTORIZADA)
    assert res.autorizada is True
    assert res.codigo_resultado == "B000"
    # El número que sirve para anular es el de la PRÁCTICA (197618424), no el de
    # la cabecera (18543817).
    assert res.nro_transaccion == "197618424"
    assert res.cantidad_aprobada == 1


def test_autorizacion_rechazada():
    res = cliente.interpretar(NO_AUTORIZADA)
    assert res.autorizada is False
    assert res.codigo_resultado == "M000"
    assert "Inexistente o no activa" in res.estado_detalle
    assert res.cantidad_aprobada == 0


# ── Autorización parcial con dos prácticas, pág. 10 ───────────────────────────

PARCIAL = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161634||ZPA^Z02^ZPA_Z02|18543817|P|2.4|||NE|NE|ARG",
    "MSA|AA|19071016152599540697",
    "AUT|C1_9999^CARTILLA AZUL SIEMEN|610109||20190710||18543817",
    "ZAU||18543817|B001^AUTORIZADO PARCIALMENTE",
    "PRD|PS~HL70454|PRESTADOR, PRUEBA^|||||2007374865901^CU",
    "PID|1||09227263000^^^^HC||GENERICO CAPITAL^GRAVADO",
    "IN1|1|C1_9999^CARTILLA AZUL SIEMEN|610109",
    "ZIN|Y|GRAV^GRAVADO",
    "PR1|1||9042500200^CONSULTA COMPLETA OFTALMOLOGIC^||20110425",
    "AUT||610109||||||1|0",
    "ZAU||95807142|P245^Ya existe un consumo de la consulta 9042500200, en la fecha",
    "PR1|2||1218012300^ECOMETRIA^||20110425",
    "AUT||610109||||||1|1",
    "ZAU||95807143|B000^AUTORIZADO",
)


def test_parcial_empareja_cada_practica_con_su_zau():
    cabecera, practicas = hl7.emparejar_por_practica(PARCIAL)

    assert cabecera.codigo == "B001"
    assert len(practicas) == 2

    assert practicas[0].codigo == "9042500200"
    assert practicas[0].estado.codigo == "P245"
    assert practicas[0].estado.transaccion == "95807142"
    assert practicas[0].cantidad_aprobada == 0

    assert practicas[1].codigo == "1218012300"
    assert practicas[1].estado.codigo == "B000"
    assert practicas[1].estado.transaccion == "95807143"
    assert practicas[1].cantidad_aprobada == 1


def test_parcial_usa_el_zau_de_la_practica_y_no_el_de_cabecera():
    """El caso que rompe cualquier atajo de "un solo ZAU".

    La cabecera dice B001 (autorizado parcialmente) y el ÚLTIMO ZAU del mensaje
    dice B000 (autorizado): quedarse con cualquiera de los dos daría la primera
    práctica por autorizada cuando Medicus la rechazó por consumo previo.
    """
    res = cliente.interpretar(PARCIAL)

    assert res.autorizada is False
    assert res.codigo_resultado == "P245"
    assert res.nro_transaccion == "95807142"
    assert "consumo previo" in res.estado_detalle


# ── Anulación (ZQA^Z04), pág. 13 ──────────────────────────────────────────────

ANULADA = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161846||ZPA^Z04^ZPA_Z02|18543843|P|2.4|||NE|NE|ARG",
    "MSA|AA|19071016175059016789",
    "AUT||610109",
    "ZAU||18543843|B000^ANULADO",
    "PRD|PS~HL70454||||||2007374865901^CU",
    "PID|1||09227263000^^^^HC||UNKNOWN^UNKNOWN",
    "IN1|1||610109",
)

NO_ANULADA = _msg(
    "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161846||ZPA^Z04^ZPA_Z02|18543843|P|2.4|||NE|NE|ARG",
    "MSA|AA|19071016175059016789",
    "AUT||610109",
    "ZAU||6491867|M000^NO ANULADO",
    "PRD|PS~HL70454||||||2007374865901^CU",
    "PID|1||09227263000^^^^HC||UNKNOWN^UNKNOWN",
)


def test_anulacion_confirmada():
    res = cliente.interpretar(ANULADA)
    assert res.autorizada is True
    assert res.estado_detalle == "ANULADO"
    # PID-5 trae el relleno UNKNOWN: no es un nombre.
    assert res.nombre_afiliado == ""


def test_anulacion_rechazada():
    res = cliente.interpretar(NO_ANULADA)
    assert res.autorizada is False
    assert res.codigo_resultado == "M000"


# ── Detalles del formato ──────────────────────────────────────────────────────

def test_copago_se_lee_de_zau_6():
    con_copago = _msg(
        "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161634||ZPA^Z02^ZPA_Z02|1|P|2.4|||NE|NE|ARG",
        "ZAU||18543817|B000^AUTORIZADO",
        "PR1|1||9042500200^CONSULTA^||20190710",
        "AUT||610109||||||1|1",
        "ZAU||197618424|B000^AUTORIZADO|||9987.50^ARS",
    )
    assert cliente.interpretar(con_copago).copago == Decimal("9987.50")


def test_acepta_lf_y_crlf():
    """Los ejemplos del documento del canal usan CRLF y los del anexo CR: el
    lector tiene que tragarse las dos formas, y también LF suelto."""
    plano = ELEGIBILIDAD_OK.replace("\r\n", "\n")
    assert cliente.interpretar(plano).codigo_resultado == "B000"


def test_sin_zau_no_revienta():
    """Una respuesta sin ZAU no debe tirar: se cae al texto de MSA/ERR."""
    sin_zau = _msg(
        "MSH|^~\\&|HL7MED|^610109^IIN|TRIA0100M|TRIA00000001|20190710161634||ZPA^Z02^ZPA_Z02|1|P|2.4|||NE|NE|ARG",
        "MSA|AE|123|Mensaje mal formado",
    )
    res = cliente.interpretar(sin_zau)
    assert res.autorizada is False
    assert res.codigo_resultado == ""
    assert "Mensaje mal formado" in res.estado_detalle
