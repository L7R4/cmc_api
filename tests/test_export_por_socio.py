"""Export con la misma estructura y orden que la vista del listado.

Puro (sin DB): arma `FilaExport` a mano y revisa la estructura de `armar`, y que
Excel y PDF se generen con ella.
"""
import dataclasses
import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from app.modules.facturacion.export.armado import armar
from app.modules.facturacion.export.datos import FilaExport
from app.modules.facturacion.export.encabezado import EncabezadoExport
from app.modules.facturacion.export.excel import build_excel_detalle
from app.modules.facturacion.export.pdf import build_pdf_detalle
from app.modules.facturacion.export.schemas import ExportOpciones


def _fila(id_, cod, nombre, tipo, fecha, afiliado, *, equipo=None, monto="100", created=None,
          clinica=None, clinica_nombre=None):
    return FilaExport(
        id=id_, cod_medico=cod, prestador_nombre=nombre, cod_obr="411", obra_social_nombre=None,
        matricula=None, autorizacion=None, fecha_practica=datetime.date(2026, 9, fecha),
        codigo="420101", nro_afiliado=None, afiliado=afiliado, cantidad=1, sesion=1, porcentaje=100,
        honorarios=Decimal(monto), gastos=Decimal("0"), ayudante=Decimal("0"), coseguro=Decimal("0"),
        subtotal=Decimal(monto), tipo=tipo, tipo_prestador=None, diagnostico=None, via=None,
        especialidad_nombre=None, estado_validacion=None, revisado=False, grupo_equipo_id=equipo,
        created=created, cod_clinica=clinica, clinica_nombre=clinica_nombre,
    )


def _filas():
    filas = [
        _fila(1, "20", "ZAPATA, JUAN", "Consulta", 3, "B"),
        _fila(2, "10", "ACOSTA, MARIA", "Practica", 4, "SOSA"),
        _fila(3, "10", "ACOSTA, MARIA", "Consulta", 2, "RUIZ"),
        _fila(4, "10", "ACOSTA, MARIA", "Honorarios individuales", 12, "PEREYRA"),
        _fila(5, "10", "ACOSTA, MARIA", "Consulta", 9, "BENITEZ"),
        _fila(6, "10", "ACOSTA, MARIA", "Practica", 18, "ALVAREZ"),
        _fila(7, "10", "ACOSTA, MARIA", "Honorarios individuales", 22, "DIAZ"),
        _fila(8, "90", "SOSA, ANA", "Sanatorio", 15, "ARCE", clinica=501, clinica_nombre="DEL SUR"),
        _fila(9, "80", "LEIVA, PABLO", "Sanatorio", 1, "LOPEZ", clinica=502, clinica_nombre="MODELO"),
        _fila(10, "80", "LEIVA, PABLO", "Sanatorio", 11, "CASTRO", clinica=502, clinica_nombre="MODELO"),
        _fila(12, "80", "LEIVA, PABLO", "Sanatorio", 5, "BRAVO", clinica=501, clinica_nombre="DEL SUR"),
        # Ayudante (otro socio) de la práctica 6: va ÚNICAMENTE bajo su cabeza.
        _fila(11, "30", "BRAVO, LUIS", "Practica", 18, "ALVAREZ", equipo=6, monto="20"),
    ]
    filas[5].grupo_equipo_id = 6
    return filas


def _armado(agrupacion="por_socio", **kw):
    return armar(_filas(), ExportOpciones(agrupacion=agrupacion, **kw))


def _grupos(a):
    return {g.cod_medico: g for g in a.secciones[0].grupos}


def test_por_socio_orden_fijo_y_equipo_solo_bajo_su_cabeza():
    a = _armado(orden="importe_desc")
    assert [s.titulo for s in a.secciones] == [None]  # una sola sección, sin cortes
    grupos = a.secciones[0].grupos

    # Socios A-Z (ignora `orden`). El ayudante (30) no tiene grupo propio: va bajo su cabeza.
    assert [g.cod_medico for g in grupos] == ["10", "80", "90", "20"]
    acosta = grupos[0]
    assert acosta.titulo == "10 - ACOSTA, MARIA"
    # Consultas y prácticas: más nueva primero; honorarios: paciente A-Z.
    assert [l.fila.id for l in acosta.lineas] == [5, 3, 6, 2, 7, 4]
    assert [l.subtitulo for l in acosta.lineas] == [
        "CONSULTAS", None, "PRACTICAS", None, "HONORARIOS INDIVIDUALES", None,
    ]
    assert [h.id for h in acosta.lineas[2].hijos] == [11]
    assert acosta.total_general == Decimal("620.00")  # incluye al ayudante, una sola vez

    assert a.una_hoja
    assert a.total_prestaciones == 12
    assert sum(g.total_general for g in grupos) == a.resumen.total_general == Decimal("1120.00")


def test_sanatorios_llevan_subtitulo_con_el_nombre_de_la_clinica():
    leiva = _grupos(_armado())["80"]
    # Tramo de Sanatorios: una clínica detrás de otra (A-Z) y, dentro, por paciente.
    assert [l.fila.id for l in leiva.lineas] == [12, 10, 9]  # DEL SUR: BRAVO · MODELO: CASTRO, LOPEZ
    assert [l.subtitulo_clinica for l in leiva.lineas] == ["CLINICA DEL SUR", "CLINICA MODELO", None]
    assert leiva.lineas[0].subtitulo == "SANATORIOS"


def test_agrupar_equipo_apagado_el_ayudante_es_un_socio_mas():
    a = _armado(agrupar_equipo=False)
    grupos = _grupos(a)
    assert all(not l.hijos for g in a.secciones[0].grupos for l in g.lineas)
    assert [l.fila.id for l in grupos["30"].lineas] == [11] and grupos["30"].total_general == Decimal("20.00")
    assert grupos["10"].total_general == Decimal("600.00")


def test_ayudante_sin_su_cabeza_en_el_documento_es_una_linea_comun():
    filas = _filas()
    filas[5] = dataclasses.replace(filas[5], fuera_de_filtro=True)  # la cabeza (id 6) quedó afuera de los filtros
    a = armar(filas, ExportOpciones(agrupacion="por_socio"))
    grupos = _grupos(a)
    assert [l.fila.id for l in grupos["30"].lineas] == [11]  # no se pierde: figura como un socio más
    assert grupos["10"].total_general == Decimal("500.00")


def test_equipo_filtrado_igual_va_completo_bajo_la_cabeza():
    filas = _filas()
    filas[11] = dataclasses.replace(filas[11], fuera_de_filtro=True)  # el ayudante (id 11) quedó afuera del filtro
    a = armar(filas, ExportOpciones(agrupacion="por_socio"))
    grupos = _grupos(a)
    assert "30" not in grupos
    assert [h.id for h in grupos["10"].lineas[2].hijos] == [11]
    assert a.total_prestaciones == 12


def test_por_tipo_subtotal_por_medico_y_orden_con_direccion():
    a = _armado("por_tipo", orden="fecha_practica", direccion="desc")
    assert [s.titulo for s in a.secciones] == ["CONSULTA", "PRACTICA", "HONORARIO", "SANATORIO"]
    consultas = a.secciones[0]
    assert [g.cod_medico for g in consultas.grupos] == ["10", "20"]  # socios A-Z
    assert all(g.subtotal_medico and not g.mostrar_resumen for g in consultas.grupos)
    assert [l.fila.id for l in consultas.grupos[0].lineas] == [5, 3]  # fecha, de la más nueva a la más vieja
    assert consultas.grupos[0].total_general == Decimal("200.00")

    # El ayudante cuenta en el subtotal de su cabeza (sección de Prácticas) y no tiene sección propia.
    practicas = a.secciones[1]
    assert [g.cod_medico for g in practicas.grupos] == ["10"]
    assert practicas.grupos[0].total_general == Decimal("220.00")

    asc = _armado("por_tipo", orden="fecha_practica", direccion="asc")
    assert [l.fila.id for l in asc.secciones[0].grupos[0].lineas] == [3, 5]

    # Sanatorio en por_tipo: clínicas seguidas con su subtítulo, dentro de cada socio.
    sanatorios = {g.cod_medico: g for g in a.secciones[3].grupos}
    assert [l.subtitulo_clinica for l in sanatorios["80"].lineas] == ["CLINICA DEL SUR", "CLINICA MODELO", None]


def test_plana_ordena_por_fecha_de_carga_en_ambos_sentidos():
    base = datetime.datetime(2026, 10, 3, 9, 0)
    filas = _filas()[:4]
    for fila, minutos in zip(filas, (30, 10, 20, 20)):  # ids 3 y 4 empatan: desempata el id
        fila.created = base + datetime.timedelta(minutes=minutos)

    def ids(direccion):
        a = armar(filas, ExportOpciones(agrupacion="plana", orden="fecha_carga", direccion=direccion))
        return [l.fila.id for l in a.secciones[0].grupos[0].lineas]

    assert ids("asc") == [2, 3, 4, 1]
    assert ids("desc") == [1, 4, 3, 2]


def test_excel_en_una_sola_hoja_y_pdf_se_generan():
    a = _armado()
    opciones = ExportOpciones(agrupacion="por_socio")
    enc = EncabezadoExport(lineas=["CMC", "OS 411"])
    wb = load_workbook(BytesIO(build_excel_detalle(a, opciones, enc)))
    assert wb.sheetnames == ["Detalle"]
    textos = [r[0] for r in wb["Detalle"].iter_rows(values_only=True) if r and r[0]]
    for esperado in ("10 - ACOSTA, MARIA", "CONSULTAS", "SANATORIOS", "80 - LEIVA, PABLO",
                     "CLINICA MODELO", "CLINICA DEL SUR"):
        assert esperado in textos
    assert not any("CLINICAS / SANATORIOS" == t for t in textos)
    assert build_pdf_detalle(a, opciones, enc).startswith(b"%PDF")


def test_por_tipo_excel_y_pdf_llevan_el_subtotal_de_cada_medico():
    a = _armado("por_tipo")
    opciones = ExportOpciones(agrupacion="por_tipo")
    enc = EncabezadoExport(lineas=["CMC", "OS 411"])
    wb = load_workbook(BytesIO(build_excel_detalle(a, opciones, enc)))
    assert wb.sheetnames[:2] == ["CONSULTA", "PRACTICA"]
    textos = [r[0] for r in wb["CONSULTA"].iter_rows(values_only=True) if r and r[0]]
    assert "SUBTOTAL SOCIO 10 ACOSTA, MARIA (2): 200.00" in textos
    assert "SUBTOTAL SOCIO 20 ZAPATA, JUAN (1): 100.00" in textos
    assert build_pdf_detalle(a, opciones, enc).startswith(b"%PDF")
