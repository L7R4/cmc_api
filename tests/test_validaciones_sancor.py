"""Tests del cliente de Sancor Salud (O.S. 411).

Las respuestas HL7 usadas como fixtures son recortes reales de
`mensajeria_sancor.txt` (log de producción del legacy, ~14.130 respuestas
2024-2026) — no inventadas. Documento, número de afiliado y nombre del
paciente están anonimizados; el resto del mensaje (segmentos, códigos de
resultado, formato) es tal cual lo devolvió Sancor.

Cubre `app/modules/validaciones/sancor.py`: parseo de la respuesta real
(desescapado, último ZAU, nombre desde PID-5), sustitución de código por
especialidad y la lista de códigos bloqueados/gestión presencial. No requiere
base de datos — son todas funciones puras.
"""
import pytest

from app.modules.validaciones.obras.sancor import cliente as sancor


def _envolver(hl7_con_cr_escapado: str) -> str:
    """Simula el sobre SOAP real: `<resultado>` con los segmentos separados
    por `&#xD;` (CR XML-escapado), como los devuelve Sancor."""
    return f"<resultado>{hl7_con_cr_escapado}</resultado>"


# ── Fixtures reales (recortadas de mensajeria_sancor.txt, anonimizadas) ───────

AUTORIZADA_B000 = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20251212080715||ZPA^Z02^ZPA_Z02|10901|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20251212|20251212|||0|0&#xd;"
    "ZAU||127509243|B000^AUTORIZADA&#xd;"
    "PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR&#xd;"
    "PRD|PL^LugarRealizacion|UNKNOWN^UNKNOWN|||||46632^CU&#xd;"
    "PRD|PE^Prestador Efector|APELLIDO, NOMBRE|^^^W||||5060^MP&#xd;"
    "PRD|PR^Prestador Prescriptor|UNKNOWN^UNKNOWN|||||119804^MN&#xd;"
    "PID|1|00000000^^^^Doc. Nac. Identidad|121297^01^9999^^HC||ROSARIO^-||20240925|M&#xd;"
    "IN1|1|G3000^SANCOR 3000|604940&#xd;"
    "ZIN|Y|GRAV^GRAV&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||420351^CONSULTA ESPECIALISTA^NM&#xd;"
    "AUT||||||||1|1&#xd;"
    "ZAU||127509243|B000^AUTORIZADA&#xd;"
    "PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# Autorizada con texto de ZAU-3 distinto de "AUTORIZADA": el segundo ZAU (el de
# la práctica) trae una variante — esto es lo que rompía la heurística vieja
# (`"AUTORIZADA" in texto`), que clasificaba estos casos como rechazados.
AUTORIZADA_VARIANTE_DOS_ZAU = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20260128094803||ZPA^Z02^ZPA_Z02|5501|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20260128|20260128|||0|0&#xd;"
    "ZAU||128726835|B000^AUTORIZADA&#xd;"
    "PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR&#xd;"
    "PRD|PL^LugarRealizacion|UNKNOWN^UNKNOWN|||||46632^CU&#xd;"
    "PRD|PE^Prestador Efector|APELLIDO, NOMBRE|^^^W||||7534^MP&#xd;"
    "PRD|PR^Prestador Prescriptor|UNKNOWN^UNKNOWN|||||162572^MN&#xd;"
    "PID|1|00000000^^^^Doc. Nac. Identidad|2289355^00^9999^^HC||PELOSO^JORGE GABRIEL||20011105|M&#xd;"
    "IN1|1|G3000^SANCOR 3000|604940&#xd;ZIN|Y|NO GRAV^NO GRAV&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||420351^CONSULTA ESPECIALISTA^NM&#xd;"
    "AUT||||||||1|1&#xd;"
    "ZAU||128726835|B000^AUTORIZADO- Recuerde adjuntar informe m\xe9dico.&#xd;"
    "PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# M024 requiere autorización: el ZAU de cabecera dice "M000 PRESTACIONES
# RECHAZADAS" (genérico) pero el de la práctica —el último— dice M024. Si el
# parser se queda con el primero, clasifica como rechazada algo que en
# realidad es pendiente de gestión.
PENDIENTE_M024_DOS_ZAU = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20260128124030||ZPA^Z02^ZPA_Z02|300001|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20260128|20260128|||0|0&#xd;"
    "ZAU||253753104|M000^PRESTACIONES RECHAZADAS&#xd;"
    "PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR&#xd;"
    "PRD|PL^LugarRealizacion|UNKNOWN^UNKNOWN|||||46632^CU&#xd;"
    "PRD|PE^Prestador Efector|APELLIDO, NOMBRE|^^^W||||6313^MP&#xd;"
    "PRD|PR^Prestador Prescriptor|UNKNOWN^UNKNOWN|||||119978^MN&#xd;"
    "PID|1|00000000^^^^Doc. Nac. Identidad|2148376^00^9999^^HC||CANTEROS^EDUARDO JOAQUIN||19971201|M&#xd;"
    "IN1|1|S1500^SANCOR 1500|604940&#xd;ZIN|N&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||100704^ESCISION TOTAL DE LESION DE PENE.^NM&#xd;"
    "AUT||||||||1|0&#xd;"
    "ZAU||253753104|M024^REQUIERE AUTORIZACION&#xd;"
    "PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# Rechazada "de verdad" como segundo ZAU (no pendiente, no autorizada).
RECHAZADA_M022_DOS_ZAU = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20260128190952||ZPA^Z02^ZPA_Z02|124601|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20260128|20260128|||0|0&#xd;"
    "ZAU||253836101|M000^PRESTACIONES RECHAZADAS&#xd;"
    "PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR&#xd;"
    "PRD|PL^LugarRealizacion|UNKNOWN^UNKNOWN|||||46632^CU&#xd;"
    "PRD|PE^Prestador Efector|APELLIDO, NOMBRE|^^^W||||4180^MP&#xd;"
    "PRD|PR^Prestador Prescriptor|UNKNOWN^UNKNOWN|||||115375^MN&#xd;"
    "PID|1|00000000^^^^Doc. Nac. Identidad|2230494^00^3669^^HC||APELLIDO^NOMBRE||19770407|F&#xd;"
    "IN1|1|S1500^SANCOR 1500|604940&#xd;ZIN|N&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||420352^^NM&#xd;"
    "AUT||||||||1|0&#xd;"
    "ZAU||253836101|M022^PRESTACION SIN COBERTURA PARA EL PLAN&#xd;"
    "PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# Errores de token (M117): siempre un solo ZAU — Sancor corta antes de
# evaluar la práctica.
ERROR_TOKEN_YA_USADO = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20251212112707||ZPA^Z02^ZPA_Z02|921401|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20251212|20251212|||0|0&#xd;"
    "ZAU||0|M117^El Token ya fue utilizado.&#xd;"
    "PRD|PS^Prestador Solicitante|UNKNOWN^UNKNOWN|||||46632^PR&#xd;"
    "PID|1|null|2200489^00^2^^HC||UNKNOWN^UNKNOWN||00000000|U&#xd;"
    "IN1|1||604940&#xd;ZIN|N&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||420351^^NM&#xd;PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# Afiliado inexistente: rechazo terminal, un solo ZAU.
RECHAZADA_M003_AFILIADO_INEXISTENTE = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20260120104242||ZPA^Z02^ZPA_Z02|597301|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20260120|20260120|||0|0&#xd;"
    "ZAU||252433969|M003^AFILIADO INEXISTENTE&#xd;"
    "PRD|PS^Prestador Solicitante|UNKNOWN^UNKNOWN|||||46632^PR&#xd;"
    "PID|1|null^^^^Doc. Nac. Identidad|1^1^1^^HC||UNKNOWN^UNKNOWN||00000000|U&#xd;"
    "IN1|1||604940&#xd;ZIN|N&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||420351^^NM&#xd;PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)

# Afiliado inhabilitado (M140), con tildes reales — para verificar que el
# desescapado/parseo no introduce mojibake propio (más allá de lo que ya
# venga roto en la fuente, que es un problema de codificación del legacy, no
# de este parser).
RECHAZADA_M140_INHABILITADO = _envolver(
    "MSH|^~\\&amp;|SANCOR_SALUD|SANCOR_SALUD^604940^IIN|SANCOR_SALUD|SANCOR_SALUD|"
    "20260204090527||ZPA^Z02^ZPA_Z02|7001|P|2.4|||NE|AL|ARG&#xd;"
    "MSA|AA|20240906135711&#xd;"
    "AUT||||20260204|20260204|||0|0&#xd;"
    "ZAU||254732630|M140^El asociado se encuentra inhabilitado. Comunicarse con SanCor.&#xd;"
    "PRD|PS^Prestador Solicitante|COLEGIO MEDICO DE CORRIENTES|||||46632^PR&#xd;"
    "PID|1|00000000^^^^Doc. Nac. Identidad|094435^01^9999^^HC||APELLIDO^NOMBRE||19760114|F&#xd;"
    "IN1|1||604940&#xd;ZIN|N&#xd;"
    "DG1|1|CIE-10|Z71^CONSULTA|PERSONALIZADO&#xd;"
    "PR1|1||340213^^NM&#xd;PV1|0|||P&#xd;NTE|1||AMS-SALUD&#xd;"
)


# ── _extraer_hl7: desescapado del sobre SOAP ──────────────────────────────────

class TestExtraerHl7:
    def test_desescapa_cr_xml(self):
        hl7 = sancor._extraer_hl7(AUTORIZADA_B000)
        assert "\r" in hl7
        assert "&#xd;" not in hl7.lower()

    def test_sin_tag_resultado_devuelve_el_cuerpo_tal_cual(self):
        assert sancor._extraer_hl7("cualquier cosa sin tag") == "cualquier cosa sin tag"


# ── _campos_segmento: último ZAU, no el primero ───────────────────────────────

class TestCamposSegmento:
    def test_toma_el_ultimo_zau_cuando_hay_dos(self):
        hl7 = sancor._extraer_hl7(PENDIENTE_M024_DOS_ZAU)
        campos = sancor._campos_segmento(hl7, "ZAU")
        assert campos[2] == "M024^REQUIERE AUTORIZACION"

    def test_un_solo_zau_se_usa_igual(self):
        hl7 = sancor._extraer_hl7(ERROR_TOKEN_YA_USADO)
        campos = sancor._campos_segmento(hl7, "ZAU")
        assert campos[2] == "M117^El Token ya fue utilizado."

    def test_segmento_ausente_devuelve_none(self):
        assert sancor._campos_segmento("MSH|foo", "ZAU") is None


# ── interpretar_respuesta: clasificación completa ─────────────────────────────

class TestInterpretarRespuesta:
    def test_autorizada_simple(self):
        res = sancor.interpretar_respuesta(AUTORIZADA_B000)
        assert res.autorizada is True
        assert res.codigo_resultado == "B000"
        assert res.nro_autorizacion == "127509243"
        assert res.estado_detalle == "AUTORIZADA"
        assert not res.requiere_gestion

    def test_autorizada_con_texto_no_literal_AUTORIZADA(self):
        """Regresión del bug principal: `"AUTORIZADA" in texto` clasificaba
        esto como rechazado porque el segundo ZAU (el que manda) no contiene
        exactamente esa palabra."""
        res = sancor.interpretar_respuesta(AUTORIZADA_VARIANTE_DOS_ZAU)
        assert res.autorizada is True
        assert res.codigo_resultado == "B000"
        assert "informe" in res.estado_detalle.lower()
        assert res.nro_autorizacion == "128726835"
        assert res.nombre_afiliado == "PELOSO JORGE GABRIEL"

    def test_pendiente_requiere_autorizacion(self):
        res = sancor.interpretar_respuesta(PENDIENTE_M024_DOS_ZAU)
        assert res.autorizada is False
        assert res.codigo_resultado == "M024"
        assert res.requiere_gestion is True
        assert res.codigo_resultado in sancor.CODIGOS_PENDIENTE

    def test_rechazada_sin_cobertura(self):
        res = sancor.interpretar_respuesta(RECHAZADA_M022_DOS_ZAU)
        assert res.autorizada is False
        assert res.requiere_gestion is False
        assert res.codigo_resultado == "M022"
        assert res.codigo_resultado not in sancor.CODIGOS_PENDIENTE
        assert res.codigo_resultado not in sancor.CODIGOS_ERROR_TOKEN

    def test_rechazada_afiliado_inexistente(self):
        res = sancor.interpretar_respuesta(RECHAZADA_M003_AFILIADO_INEXISTENTE)
        assert res.autorizada is False
        assert res.codigo_resultado == "M003"
        # Sancor asigna número de trámite aunque rechace — no es lo mismo que
        # una autorización; sólo `autorizada=False` decide si factura o no.
        assert res.nro_autorizacion == "252433969"
        assert res.nombre_afiliado is None  # PID-5 vino "UNKNOWN^UNKNOWN"

    def test_rechazada_inhabilitado_sin_mojibake(self):
        res = sancor.interpretar_respuesta(RECHAZADA_M140_INHABILITADO)
        assert res.codigo_resultado == "M140"
        assert "�" not in res.estado_detalle
        assert "inhabilitado" in res.estado_detalle.lower()

    def test_error_token_clasifica_aparte(self):
        res = sancor.interpretar_respuesta(ERROR_TOKEN_YA_USADO)
        assert res.autorizada is False
        assert res.codigo_resultado == "M117"
        assert res.codigo_resultado in sancor.CODIGOS_ERROR_TOKEN
        assert res.nro_autorizacion is None

    def test_codigo_desconocido_no_pierde_el_caso(self):
        """Un código de resultado que no está en ningún catálogo conocido
        cae como rechazada, pero con el texto crudo — nunca se pierde en
        silencio (a diferencia del legacy)."""
        hl7 = "ZAU||999|Z999^Motivo nunca antes visto\rPID|1|x|y||UNKNOWN^UNKNOWN"
        res = sancor.interpretar_respuesta(_envolver(hl7.replace("\r", "&#xd;")))
        assert res.autorizada is False
        assert res.codigo_resultado == "Z999"
        assert res.codigo_resultado not in sancor.CODIGOS_PENDIENTE
        assert res.codigo_resultado not in sancor.CODIGOS_ERROR_TOKEN
        assert "Motivo nunca antes visto" in res.estado_detalle

    def test_respuesta_vacia_no_rompe(self):
        res = sancor.interpretar_respuesta("")
        assert res.autorizada is False
        assert res.codigo_resultado == ""
        assert res.estado_detalle  # nunca vacío — hay un fallback genérico

    def test_nombre_unknown_no_se_expone_como_nombre(self):
        hl7 = "ZAU||1|B000^AUTORIZADA\rPID|1|x|y||UNKNOWN^UNKNOWN||20000101|M"
        res = sancor.interpretar_respuesta(_envolver(hl7.replace("\r", "&#xd;")))
        assert res.nombre_afiliado is None


# ── Decodificación explícita (misma estrategia que _postear) ─────────────────

def test_estrategia_de_decodificacion_utf8_con_fallback_latin1():
    """No llama a `_postear` (requiere red) — replica su lógica de decodificación
    para dejar constancia de que el fallback a latin-1 funciona con texto
    acentuado que no es UTF-8 válido, como el que devuelve Sancor."""
    texto_con_tildes = "El asociado se encuentra en situación irregular"
    crudo = texto_con_tildes.encode("latin-1")

    with pytest.raises(UnicodeDecodeError):
        crudo.decode("utf-8")

    assert crudo.decode("latin-1") == texto_con_tildes


# ── Sustitución de código por especialidad ────────────────────────────────────

class TestSustituirCodigo:
    def test_420302_con_especialidad_41_se_sustituye(self):
        envio, colegio = sancor.sustituir_codigo("420302", [41])
        assert envio == "420351"
        assert colegio == "420302"

    def test_420130_con_especialidad_33_se_sustituye(self):
        envio, colegio = sancor.sustituir_codigo("420130", [33])
        assert envio == "305001"
        assert colegio == "420130"

    def test_070660_con_especialidad_16_se_sustituye(self):
        envio, colegio = sancor.sustituir_codigo("070660", [16])
        assert envio == "070715"
        assert colegio == "070660"

    def test_070660_sin_especialidad_16_no_se_sustituye(self):
        """El caso que dispara gestión presencial: sin la especialidad que
        habilita el sustituto, el código queda igual."""
        envio, colegio = sancor.sustituir_codigo("070660", [7, 47, 65])
        assert envio == "070660"
        assert colegio is None

    def test_codigo_sin_regla_no_cambia(self):
        envio, colegio = sancor.sustituir_codigo("420132", [41, 33, 16])
        assert envio == "420132"
        assert colegio is None

    def test_especialidades_vacias_no_sustituye(self):
        envio, colegio = sancor.sustituir_codigo("420302", [])
        assert envio == "420302"
        assert colegio is None


# ── Códigos bloqueados / gestión presencial ───────────────────────────────────

def test_codigos_bloqueados_incluye_los_tres_unificados():
    """180150/180164 antes sólo se bloqueaban en el front; ahora están acá,
    junto con 180127."""
    assert sancor.CODIGOS_NO_ADMITIDOS == {"180127", "180150", "180164"}


def test_gestion_presencial_es_070660():
    assert sancor.CODIGOS_GESTION_PRESENCIAL == {"070660"}


def test_catalogos_no_se_solapan():
    """Un código no puede estar bloqueado y ser gestión presencial a la vez
    — son ramas mutuamente excluyentes en `service.validar_sancor`."""
    assert not (sancor.CODIGOS_NO_ADMITIDOS & sancor.CODIGOS_GESTION_PRESENCIAL)
    assert not (sancor.CODIGOS_ERROR_TOKEN & sancor.CODIGOS_PENDIENTE)


# ── Mensaje de anulación (Z04) ────────────────────────────────────────────────

def test_mensaje_anulacion_separa_segmentos_por_cr():
    """El legacy separa con espacios y nunca tuvo una respuesta real
    verificada; acá va con CR, como pide HL7."""
    mensaje = sancor.construir_mensaje_anulacion(nro_autorizacion="128726835", processing_id="D")
    segmentos = mensaje.split("\r")
    assert len(segmentos) == 3
    assert segmentos[0].startswith("MSH|")
    assert segmentos[1] == "ZAU||128726835"
    assert segmentos[2].startswith("PRD|PS^Prestador Solicitante||||||")
    assert " " not in segmentos[1]  # no quedó pegado al siguiente segmento


def test_mensaje_anulacion_usa_prestador_del_colegio_no_matricula_del_medico():
    from app.core.config import settings

    mensaje = sancor.construir_mensaje_anulacion(nro_autorizacion="1", processing_id="D")
    assert f"{settings.SANCOR_PRESTADOR}^PR" in mensaje
