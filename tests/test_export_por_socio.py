"""Export "Separado por socio": orden fijo, médicos primero y clínicas al final.

Puro (sin DB): arma `FilaExport` a mano y revisa la estructura de `armar`, y que
Excel y PDF se generen con ella.
"""
import datetime
from decimal import Decimal

from openpyxl import load_workbook

from app.modules.facturacion.export.armado import SECCION_CLINICAS, SECCION_MEDICOS, armar
from app.modules.facturacion.export.datos import FilaExport
from app.modules.facturacion.export.encabezado import EncabezadoExport
from app.modules.facturacion.export.excel import build_excel_detalle
from app.modules.facturacion.export.pdf import build_pdf_detalle
from app.modules.facturacion.export.schemas import ExportOpciones


def _fila(id_, cod, nombre, tipo, fecha, afiliado, *, equipo=None, monto="100"):
    return FilaExport(
        id=id_, cod_medico=cod, prestador_nombre=nombre, cod_obr="411", obra_social_nombre=None,
        matricula=None, autorizacion=None, fecha_practica=datetime.date(2026, 9, fecha),
        codigo="420101", nro_afiliado=None, afiliado=afiliado, cantidad=1, sesion=1, porcentaje=100,
        honorarios=Decimal(monto), gastos=Decimal("0"), ayudante=Decimal("0"), coseguro=Decimal("0"),
        subtotal=Decimal(monto), tipo=tipo, tipo_prestador=None, diagnostico=None, via=None,
        especialidad_nombre=None, estado_validacion=None, revisado=False, grupo_equipo_id=equipo,
    )


FILAS = [
    _fila(1, "20", "ZAPATA, JUAN", "Consulta", 3, "B"),
    _fila(2, "10", "ACOSTA, MARIA", "Practica", 4, "SOSA"),
    _fila(3, "10", "ACOSTA, MARIA", "Consulta", 2, "RUIZ"),
    _fila(4, "10", "ACOSTA, MARIA", "Honorarios individuales", 12, "PEREYRA"),
    _fila(5, "10", "ACOSTA, MARIA", "Consulta", 9, "BENITEZ"),
    _fila(6, "10", "ACOSTA, MARIA", "Practica", 18, "ALVAREZ"),
    _fila(7, "10", "ACOSTA, MARIA", "Honorarios individuales", 22, "DIAZ"),
    _fila(8, "90", "SANATORIO DEL SUR", "Sanatorio", 15, "ARCE"),
    _fila(9, "80", "CLINICA MODELO", "Sanatorio", 1, "LOPEZ"),
    _fila(10, "80", "CLINICA MODELO", "Sanatorio", 11, "CASTRO"),
    # Ayudante (otro socio) de la práctica 6: va anidado bajo su cabeza.
    _fila(11, "30", "BRAVO, LUIS", "Practica", 18, "ALVAREZ", equipo=6, monto="20"),
]
FILAS[5].grupo_equipo_id = 6


def _armado():
    return armar(FILAS, ExportOpciones(orden="importe_desc", agrupacion="por_socio"))


def test_medicos_primero_y_clinicas_al_final_con_orden_fijo():
    a = _armado()
    assert [s.titulo for s in a.secciones] == [SECCION_MEDICOS, SECCION_CLINICAS]
    medicos, clinicas = a.secciones

    assert [g.cod_medico for g in medicos.grupos] == ["10", "20"]  # A-Z, ignora `orden`
    acosta = medicos.grupos[0]
    assert acosta.titulo == "10 - ACOSTA, MARIA"
    # Consultas y prácticas: más nueva primero; honorarios: paciente A-Z.
    assert [l.fila.id for l in acosta.lineas] == [5, 3, 6, 2, 7, 4]
    assert [l.subtitulo for l in acosta.lineas] == [
        "CONSULTAS", None, "PRACTICAS", None, "HONORARIOS INDIVIDUALES", None,
    ]
    assert [h.id for h in acosta.lineas[2].hijos] == [11]
    assert acosta.total_general == Decimal("620.00")  # incluye al ayudante

    assert [g.nombre for g in clinicas.grupos] == ["CLINICA MODELO", "SANATORIO DEL SUR"]
    assert [l.fila.id for l in clinicas.grupos[0].lineas] == [10, 9]  # CASTRO, LOPEZ
    assert a.una_hoja


def test_excel_en_una_sola_hoja_y_pdf_se_generan():
    a = _armado()
    opciones = ExportOpciones(agrupacion="por_socio")
    enc = EncabezadoExport(lineas=["CMC", "OS 411"])
    from io import BytesIO
    wb = load_workbook(BytesIO(build_excel_detalle(a, opciones, enc)))
    assert wb.sheetnames == ["Detalle"]
    textos = [r[0] for r in wb["Detalle"].iter_rows(values_only=True) if r and r[0]]
    for esperado in (SECCION_MEDICOS, "10 - ACOSTA, MARIA", "CONSULTAS", SECCION_CLINICAS, "80 - CLINICA MODELO"):
        assert esperado in textos
    assert textos.index(SECCION_MEDICOS) < textos.index(SECCION_CLINICAS)

    assert build_pdf_detalle(a, opciones, enc).startswith(b"%PDF")
