"""Tests del cliente de Nobis Salud (O.S. 62) — WSGeCROS de Gecros.

Las respuestas usadas como fixtures son recortes reales del `soap.log` del
servidor de producción (`public_html/nobis/logs/`, 341 requests con su
respuesta, relevado el 2026-08-19 y re-cruzado por código el 2026-08-30) — no
inventadas.

El foco está en la forma del `<Item>`, que es lo único que separa una
autorización de un rechazo y que no está documentado en ningún lado salvo en el
PHP que corre en producción. Nobis homologa el código de su lado —no hay tabla
de homologación acá, a diferencia de Sancor—; lo único que varía por código es
bajo qué nomenclador anunciarlo. Ver `CODIGOS_FORMA_DIRECTA` en
`obras/nobis/cliente.py`, que documenta cómo se distingue "forma equivocada"
(el mensaje `Nomenclador o Practica <código> Inexistente`) de un rechazo de
negocio normal.

No requiere base de datos: son todas funciones puras.
"""
import datetime
import re

import pytest

from app.modules.validaciones.obras.nobis import cliente as nobis


def _orden(codigo: str, **kw) -> str:
    return nobis.construir_xml_orden(
        numero_afiliado=kw.get("afiliado", "38407571"),
        mat_prov=kw.get("mat", "7328"),
        tipo_solic=kw.get("tipo", "12221"),
        cod_entidad_efectora=kw.get("entidad", "90692"),
        codigo_practica=codigo,
        token=kw.get("token", "290894"),
        cantidad=kw.get("cantidad", 1),
        fecha_prescripcion=datetime.date(2026, 3, 20),
        fecha_realizacion=datetime.date(2026, 3, 20),
    )


def _item(xml: str) -> str:
    return re.search(r"<Item>.*</Item>", xml, re.DOTALL).group(0)


# ── La forma del <Item> ───────────────────────────────────────────────────────

def test_420101_va_en_la_forma_directa():
    """Copia exacta del `<Item>` de una orden real que volvió `A-Autorizado`
    (9 de 14 intentos con esta forma). Con `OrigenTipoNomCod=99` en cambio
    vuelve "Nomenclador o Practica 420101 Inexistente"."""
    assert _item(_orden("420101")) == (
        "<Item>"
        "<TipoNomenclador>1</TipoNomenclador>"
        "<CodPractica>420101</CodPractica>"
        "<Cantidad>1</Cantidad>"
        "<MatEfector>7328</MatEfector>"
        "<TipoEfector>12221</TipoEfector>"
        "</Item>"
    )


def test_420112_tambien_va_en_la_forma_directa():
    """Faltaba en `CODIGOS_FORMA_DIRECTA` hasta el 2026-08-30. Con
    `OrigenTipoNomCod=99` volvió "Nomenclador o Practica 420112 Inexistente"
    las 5 veces que se probó — nunca autorizó con `99`; con la forma directa
    (o `OrigenTipoNomCod=1`, la misma familia) Nobis sí lo reconoce, y ahí lo
    rechaza por "Código sin valor en convenio" — un motivo de negocio, no de
    formato."""
    assert _item(_orden("420112")) == (
        "<Item>"
        "<TipoNomenclador>1</TipoNomenclador>"
        "<CodPractica>420112</CodPractica>"
        "<Cantidad>1</Cantidad>"
        "<MatEfector>7328</MatEfector>"
        "<TipoEfector>12221</TipoEfector>"
        "</Item>"
    )


def test_el_resto_va_en_la_forma_origen_con_99():
    """`420351` autorizó 5 de 5 veces así, y fue rechazado 27 con
    `OrigenTipoNomCod=1`. El `99` es el hallazgo, no un detalle de formato."""
    assert _item(_orden("420351")) == (
        "<Item>"
        "<TipoNomenclador></TipoNomenclador>"
        "<CodPractica></CodPractica>"
        "<Cantidad>1</Cantidad>"
        "<MatEfector>7328</MatEfector>"
        "<TipoEfector>12221</TipoEfector>"
        "<OrigenTipoNomCod>99</OrigenTipoNomCod>"
        "<OrigenPracticaCod>420351</OrigenPracticaCod>"
        "</Item>"
    )


def test_nunca_mas_el_origen_tipo_nom_cod_1():
    """Regresión del bug que hacía fallar 65 de 67 órdenes. Si alguien vuelve a
    poner `1` acá, este test rompe el build."""
    assert nobis.ORIGEN_TIPO_NOM_COD == "99"
    for codigo in ("420351", "420132", "420130", "150106"):
        assert "<OrigenTipoNomCod>1</OrigenTipoNomCod>" not in _orden(codigo)


@pytest.mark.parametrize("codigo", ["420351", "420132", "420130", "999999"])
def test_la_forma_origen_deja_vacios_los_dos_campos(codigo):
    """El código NO puede viajar además en `CodPractica`: mandarlo en los dos
    lugares es justamente lo que el PHP evita."""
    item = _item(_orden(codigo))
    assert "<TipoNomenclador></TipoNomenclador>" in item
    assert "<CodPractica></CodPractica>" in item
    assert f"<OrigenPracticaCod>{codigo}</OrigenPracticaCod>" in item


def test_la_forma_directa_no_manda_los_campos_de_origen():
    item = _item(_orden("420101"))
    assert "OrigenTipoNomCod" not in item
    assert "OrigenPracticaCod" not in item


def test_efector_y_solicitante_son_el_mismo_medico_en_las_dos_formas():
    """Regla del convenio: `MatEfector == MatProv` y `TipoEfector == TipoSolic`."""
    for codigo in ("420101", "420351"):
        item = _item(_orden(codigo, mat="5175", tipo="12221"))
        assert "<MatEfector>5175</MatEfector>" in item
        assert "<TipoEfector>12221</TipoEfector>" in item


def test_la_cantidad_viaja_en_las_dos_formas():
    for codigo in ("420101", "420351"):
        assert "<Cantidad>3</Cantidad>" in _item(_orden(codigo, cantidad=3))


# ── Cabecera de la orden ──────────────────────────────────────────────────────

def test_la_cabecera_lleva_los_datos_del_convenio():
    """`CodEntidadEfectora` y las fechas en dd/mm/YYYY. Los dos valores están
    confirmados contra respuestas reales: Nobis devuelve la entidad como
    "90692 - Colegio Medico de Corrientes"."""
    xml = _orden("420101")
    assert "<CodEntidadEfectora>90692</CodEntidadEfectora>" in xml
    assert "<TipoSolic>12221</TipoSolic>" in xml
    assert "<FechaPrescripcion>20/03/2026</FechaPrescripcion>" in xml
    assert "<FechaRealizacion>20/03/2026</FechaRealizacion>" in xml


def test_la_orden_lleva_el_token():
    """Las 130 órdenes del `soap.log` de producción lo llevan; ninguna va sin
    él, y Nobis contesta "Token incorrecto" 21 veces. Va entre `<Diagnostico>` y
    `<Items>`, que es la posición del builder real."""
    xml = _orden("420101", token="290894")
    assert "<Token>290894</Token>" in xml
    assert xml.index("<Diagnostico>") < xml.index("<Token>") < xml.index("<Items>")


def test_el_token_de_nobis_es_de_seis_digitos():
    """No es el de 4 de Sancor: son formatos distintos y el campo tiene que
    dejarlo pasar entero."""
    assert "<Token>666766</Token>" in _orden("420351", token="666766")


# ── El sobre SOAP ─────────────────────────────────────────────────────────────

def test_la_orden_viaja_en_pXml_no_en_pXmlOrden():
    """`pXmlOrden` no existe en el WSDL ni aparece en 331 requests reales. Con
    el nombre equivocado el `.asmx` recibe la orden como `null` y no da error de
    transporte, así que el fallo sería mudo."""
    assert nobis.ORDEN_PARAMETROS["InsertarAutorizacionAmb"] == ("pUsuario", "pClave", "pXml")

    sobre = nobis._envolver_soap(
        "InsertarAutorizacionAmb", {"pUsuario": "U", "pClave": "C", "pXml": "<Orden/>"}
    )
    assert "<pXml>" in sobre
    assert "pXmlOrden" not in sobre


def test_la_anulacion_respeta_la_secuencia_del_wsdl():
    """`AnularOrdenNroCod` se declara dentro de un `<s:sequence>`: el orden es
    parte del contrato. `pNroOrden` va TERCERO, aunque el llamador lo agregue
    último."""
    sobre = nobis._envolver_soap(
        "AnularOrdenNroCod",
        # A propósito en el orden equivocado: es como lo armaba el bug.
        {"pUsuario": "U", "pClave": "C", "pCodAut": "2126063",
         "pObservaciones": "X", "pNroOrden": "5585364"},
    )
    assert re.findall(r"<(p\w+)>", sobre) == [
        "pUsuario", "pClave", "pNroOrden", "pCodAut", "pObservaciones",
    ]


def test_un_parametro_opcional_ausente_no_corre_a_los_demas():
    """Todos son `minOccurs="0"`: omitir `pNroOrden` es válido, y los que quedan
    tienen que seguir en su posición."""
    sobre = nobis._envolver_soap(
        "AnularOrdenNroCod",
        {"pUsuario": "U", "pClave": "C", "pCodAut": "2126063", "pObservaciones": "X"},
    )
    assert re.findall(r"<(p\w+)>", sobre) == ["pUsuario", "pClave", "pCodAut", "pObservaciones"]


def test_un_parametro_que_el_wsdl_no_declara_se_corta_antes_de_salir():
    """Un typo en el nombre llegaría al WS como `null` sin error de transporte —
    exactamente el modo de falla de `pXmlOrden`. Mejor romper acá."""
    with pytest.raises(nobis.NobisError, match="pXmlOrden"):
        nobis._envolver_soap(
            "InsertarAutorizacionAmb", {"pUsuario": "U", "pClave": "C", "pXmlOrden": "<Orden/>"}
        )


def test_una_operacion_sin_orden_declarado_no_se_puede_mandar():
    with pytest.raises(nobis.NobisError, match="orden de parámetros"):
        nobis._envolver_soap("OperacionInventada", {"pUsuario": "U"})


# ── Lectura de las respuestas reales ──────────────────────────────────────────

def _envolver(interno: str) -> str:
    return f"<DocumentElement><Autorizacion>{interno}</Autorizacion></DocumentElement>"


AUTORIZADA = _envolver(
    "<Mensaje /><Estado>A-Autorizado</Estado><Cod>2050158</Cod><Num>3799174</Num>"
    "<Cose_Neto>0,00</Cose_Neto><Cose_IVA>0,00</Cose_IVA><Cose_Total>0,00</Cose_Total>"
)

RECHAZADA_INEXISTENTE = _envolver(
    "<Mensaje>Nomenclador o Practica 420351 Inexistente</Mensaje>"
    "<Estado>R-Rechazada</Estado><Cod /><Num />"
)

PENDIENTE_DUPLICADA = _envolver(
    "<Mensaje>C&#243;digo ya solicitado en 5878035</Mensaje>"
    "<Estado>P-Pendiente</Estado><Cod>2126063</Cod><Num>5585364</Num>"
)


def test_autorizada_trae_orden_y_codigo():
    r = nobis.interpretar_autorizacion(AUTORIZADA)
    assert r.autorizada is True
    assert r.estado == "A"
    assert r.nro_orden == "3799174"
    # Lo que después pide AnularOrdenNroCod. Sin esto la orden no se puede anular.
    assert r.cod_autorizacion == "2050158"
    assert r.requiere_gestion is False


def test_rechazada_no_se_lee_como_autorizada():
    r = nobis.interpretar_autorizacion(RECHAZADA_INEXISTENTE)
    assert r.autorizada is False
    assert r.estado == "R"
    assert "Inexistente" in r.estado_detalle


def test_pendiente_crea_orden_igual_y_hay_que_poder_anularla():
    """El `P-Pendiente` es el caso normal en Nobis: la orden EXISTE allá. Si se
    perdiera `cod_autorizacion`, quedaría viva sin que nadie se entere."""
    r = nobis.interpretar_autorizacion(PENDIENTE_DUPLICADA)
    assert r.autorizada is False
    assert r.estado == "P"
    assert r.requiere_gestion is True
    assert r.cod_autorizacion == "2126063"
    assert r.nro_orden == "5585364"

    from app.modules.validaciones.obras.nobis.validador import _LETRAS_CON_ORDEN

    assert r.estado in _LETRAS_CON_ORDEN


def test_afiliado_con_cobertura_se_lee_como_activo():
    """La respuesta real no dice "ACTIVO": dice "Con Cobertura al 03/06/2026"."""
    r = nobis.interpretar_afiliado(
        "<DocumentElement><ConsultaAfiliado>"
        "<Mensaje /><Afiliado>PRUEBA, PEREZ JUAN MANUEL</Afiliado>"
        "<Estado>Con Cobertura al 03/06/2026</Estado>"
        "</ConsultaAfiliado></DocumentElement>"
    )
    assert r.autorizada is True
    assert r.nombre_afiliado == "PRUEBA, PEREZ JUAN MANUEL"


def test_afiliado_inexistente_no_se_lee_como_activo():
    """70 casos en el log. El texto empieza con "A" — no alcanza con mirar la
    primera letra como en la autorización."""
    r = nobis.interpretar_afiliado(
        "<DocumentElement><ConsultaAfiliado>"
        "<Mensaje>Afiliado inexistente</Mensaje><Afiliado />"
        "<Estado>Afiliado inexistente</Estado>"
        "</ConsultaAfiliado></DocumentElement>"
    )
    assert r.autorizada is False


# ── verificar_prestador ────────────────────────────────────────────────────

def test_sin_matricula_no_llega_a_pedir_la_orden():
    """14 de 149 rechazos reales son "El Solicitante ingresado no existe":
    sin `MATRICULA_PROV`, `<MatProv>` sale vacío. Se corta acá, antes de
    gastar el request — mismo criterio que `ValidadorSancor.verificar_prestador`."""
    from fastapi import HTTPException

    from app.modules.validaciones.obras.nobis.validador import ValidadorNobis

    class _Medico:
        MATRICULA_PROV = None

    with pytest.raises(HTTPException) as exc:
        ValidadorNobis().verificar_prestador(_Medico())

    assert exc.value.status_code == 422


def test_con_matricula_no_corta_nada():
    from app.modules.validaciones.obras.nobis.validador import ValidadorNobis

    class _Medico:
        MATRICULA_PROV = 7328

    # No debe levantar.
    ValidadorNobis().verificar_prestador(_Medico())


# ── GET /nobis/afiliado — el aviso en vivo debajo del campo ────────────────

@pytest.mark.asyncio
async def test_afiliado_encontrado_y_activo(monkeypatch):
    from app.modules.validaciones.obras.nobis.routes import consultar_afiliado

    async def _consultar_afiliado(**_):
        return nobis.RespuestaNobis(
            autorizada=True,
            estado_detalle="Con Cobertura al 30/08/2026",
            nombre_afiliado="PRUEBA, PEREZ JUAN MANUEL",
        )

    monkeypatch.setattr(nobis, "consultar_afiliado", _consultar_afiliado)

    r = await consultar_afiliado(nro_afiliado="38407571", user={})

    assert r == {
        "encontrado": True,
        "activo": True,
        "nombre": "PRUEBA, PEREZ JUAN MANUEL",
        "estado": "Con Cobertura al 30/08/2026",
    }


@pytest.mark.asyncio
async def test_afiliado_no_encontrado(monkeypatch):
    """Nunca bloquea nada — sólo informa. `crear_prestacion()` no llama a este
    endpoint, así que un afiliado no encontrado acá no impide cargar (mismo
    criterio del legacy: `$nobis_require_active = false`)."""
    from app.modules.validaciones.obras.nobis.routes import consultar_afiliado

    async def _consultar_afiliado(**_):
        return nobis.RespuestaNobis(
            autorizada=False,
            estado_detalle="Afiliado inexistente",
            nombre_afiliado=None,
        )

    monkeypatch.setattr(nobis, "consultar_afiliado", _consultar_afiliado)

    r = await consultar_afiliado(nro_afiliado="0", user={})

    assert r["encontrado"] is False
    assert r["activo"] is False
    assert r["nombre"] is None
