"""Formato del PDF del detalle: totales por socio, rótulos de paciente/clínica y ancho de columnas.

Puro (sin DB): arma las filas con los helpers de `test_export_por_socio`.
"""
import re
from decimal import Decimal

from fpdf import FPDF

from app.modules.facturacion.export import armado as armado_mod
from app.modules.facturacion.export import pdf as pdf_mod
from app.modules.facturacion.export.armado import armar, spec_columnas
from app.modules.facturacion.export.encabezado import EncabezadoExport
from app.modules.facturacion.export.schemas import ExportOpciones
from test_export_por_socio import _fila, _filas

ENC_NUEVO = EncabezadoExport(
    lineas=["COLEGIO MEDICO DE CORRIENTES", "CARLOS PELLEGRINI 1785", "Tipo y Nº de Factura/s: B - 1"],
    periodo="Septiembre 2026", obra_social="ASOCIACION MUTUAL SANCOR",
)


def test_resumen_socio_con_cantidad_y_monto_por_tipo_y_total():
    a = armar(_filas(), ExportOpciones(agrupacion="por_socio"))
    texto = armado_mod.texto_resumen_socio(a.secciones[0].grupos[0])  # socio 10
    assert re.fullmatch(
        r"Consulta: \d+ - \$[\d,]+\.\d{2}, Practica: \d+ - \$[\d,]+\.\d{2}, Honorario: \d+ - \$[\d,]+\.\d{2}, "
        r"Total: \$[\d,]+\.\d{2}", texto), texto
    assert "RESUMEN SOCIO" not in texto and "HONORARIOS SOCIO" not in texto and "GASTOS" not in texto


def test_resumen_socio_incluye_coseguro_solo_si_hay():
    a = armar(_filas(), ExportOpciones(agrupacion="por_socio"))
    grupo = a.secciones[0].grupos[0]
    assert "Coseguro" not in armado_mod.texto_resumen_socio(grupo)
    grupo.total_coseguro = Decimal("50")
    texto = armado_mod.texto_resumen_socio(grupo)
    assert texto.endswith(f"Coseguro: $50.00, Total: ${grupo.total_general:,.2f}")


def test_rotulo_de_paciente_en_honorarios_y_sanatorios():
    f = _filas()
    a = armar(f, ExportOpciones(agrupacion="por_tipo", orden_honorarios="paciente", orden_sanatorio="paciente"))
    secciones = {s.titulo: s for s in a.secciones}
    # Sanatorios: la clínica va en su propia línea y el paciente aparte.
    san = [l for g in secciones["SANATORIO"].grupos for l in g.lineas]
    assert [l.subtitulo_clinica for l in san if l.subtitulo_clinica] == ["CLINICA DEL SUR", "CLINICA MODELO"]
    assert all(armado_mod.rotulo_paciente(l).startswith("Paciente: ") for l in san if l.subtitulo_paciente)
    # Honorarios individuales: «<clínica> - Paciente: <nombre>»; sin clínica, solo el paciente.
    hon = [l for g in secciones["HONORARIO"].grupos for l in g.lineas][0]
    assert armado_mod.rotulo_paciente(hon) == "Paciente: DIAZ"
    hon.fila.cod_clinica, hon.fila.clinica_nombre = 501, "DEL SUR"
    assert armado_mod.rotulo_paciente(hon) == "DEL SUR - Paciente: DIAZ"


def test_las_columnas_ocupan_todo_el_ancho_con_pocas_o_muchas_columnas():
    for cols in (["fecha", "afiliado"], ["prestador", "matricula", "autorizacion", "fecha", "codigo", "nro_afiliado",
                                          "afiliado", "cantidad", "porcentaje", "honorarios", "gastos", "coseguro"]):
        pdf = FPDF(orientation="L", unit="mm", format="A4")
        pdf.set_margins(6, 8, 6)
        tabla = pdf_mod._Tabla(pdf, spec_columnas(cols), ENC_NUEVO)
        assert abs(sum(tabla.anchos) - pdf.epw) < 1e-6


def test_pdf_con_encabezado_nuevo_y_agrupaciones():
    for agrup, extra in (("por_socio", {}), ("por_tipo", dict(orden_honorarios="paciente", orden_sanatorio="paciente")),
                         ("todo_junto", {}), ("plana", {})):
        opciones = ExportOpciones(agrupacion=agrup, **extra)
        a = armar(_filas(), opciones)
        assert pdf_mod.build_pdf_detalle(a, opciones, ENC_NUEVO).startswith(b"%PDF")
    # Los títulos de sección van con el nombre completo.
    assert armado_mod.titulo_seccion("HONORARIO") == "HONORARIOS INDIVIDUALES"


def test_subtotal_de_socio_en_por_tipo_solo_lleva_el_total():
    a = armar(_filas(), ExportOpciones(agrupacion="por_tipo"))
    grupo = a.secciones[0].grupos[0]
    assert armado_mod.texto_subtotal_socio(grupo) == f"Total: ${grupo.total_general:,.2f}"


def test_total_de_paciente_con_el_mismo_estilo_que_el_del_socio():
    a = armar(_filas(), ExportOpciones(agrupacion="por_socio"))
    linea = next(l for g in a.secciones[0].grupos for l in g.lineas if l.total_paciente)
    texto = armado_mod.texto_total_paciente(linea)
    assert re.fullmatch(r"Total paciente .+: \$[\d,]+\.\d{2}", texto), texto
    assert texto != texto.upper()


def test_obra_social_del_encabezado_lleva_el_numero_entre_parentesis():
    from app.modules.facturacion.export.encabezado import _texto_obra_social
    assert _texto_obra_social("ASOCIACION MUTUAL SANCOR", "411") == "ASOCIACION MUTUAL SANCOR (411)"
    assert _texto_obra_social("411", "411") == "(411)"  # sin nombre cargado, el nombre cae al código


def test_nro_de_registro_es_una_columna_mas_que_se_puede_sacar():
    con = [c.key for c in spec_columnas(["nro_registro", "fecha"])]
    sin = [c.key for c in spec_columnas(["fecha"])]
    assert con[0] == "id" and "id" not in sin
    # Por defecto sigue saliendo (también en el detalle por médico).
    assert "nro_registro" in ExportOpciones().columnas


def test_la_clinica_es_una_columna_del_export_con_el_numero_de_la_clinica():
    from app.modules.facturacion.export.armado import valores_fila
    from app.modules.facturacion.export.schemas import COLUMNAS_DEFAULT

    assert "clinica" in COLUMNAS_DEFAULT and "clinica" in ExportOpciones().columnas
    # Va entre la matrícula y la autorización, igual que en la planilla de importación.
    cols = spec_columnas(["matricula", "clinica", "autorizacion"], con_tipo=False)
    assert [c.header for c in cols] == ["SOCIO", "MATRI.", "CLINICA", "AUTORIZACION", "SUB. TOTAL"]

    f = _filas()[0]
    f.cod_clinica = 9830
    assert valores_fila(cols, f)[2] == 9830
    f.cod_clinica = None
    assert valores_fila(cols, f)[2] == ""
