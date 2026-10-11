"""El equipo de una cirugía cuenta con el tipo de su cabeza: secciones, resumen por socio, resumen
general y carátula dan los mismos importes por tipo. Y una fecha legacy inválida no rompe el armado."""
from decimal import Decimal

from app.modules.facturacion.export.armado import armar
from app.modules.facturacion.export.schemas import ExportOpciones
from test_export_por_socio import _fila


def _filas():
    cab = _fila(1, "10", "ACOSTA, MARIA", "Sanatorio", 5, "ARCE", clinica=501, clinica_nombre="DEL SUR", monto="1000")
    cab.grupo_equipo_id = 1
    ayu = _fila(2, "30", "BRAVO, LUIS", "Practica", 5, "ARCE", equipo=1, monto="200")  # su fila dice Práctica
    cons = _fila(3, "20", "ZAPATA, JUAN", "Consulta", 6, "B", monto="50")
    prac = _fila(4, "20", "ZAPATA, JUAN", "Practica", 7, "C", monto="300")
    return [cab, ayu, cons, prac]


def test_el_equipo_suma_en_el_tipo_de_su_cabeza_en_el_resumen_general():
    a = armar(_filas(), ExportOpciones(agrupacion="por_tipo"))
    resumen = dict(a.resumen.por_tipo)
    assert resumen["Sanatorio"] == Decimal("1200.00")   # cirugía + su ayudante
    assert resumen["Practica"] == Decimal("300.00")     # solo la práctica propia
    assert a.resumen.total_general == Decimal("1550.00")
    # Cada subtotal de sección es el mismo número que el del resumen general.
    assert {s.titulo: s.total for s in a.secciones} == {"CONSULTA": Decimal("50.00"), "PRACTICA": Decimal("300.00"),
                                                        "SANATORIO": Decimal("1200.00")}
    assert sum(m for _, m in a.resumen.por_tipo) == a.resumen.total_general


def test_las_agrupaciones_dan_el_mismo_resumen_general():
    por_agrupacion = {
        agrup: dict(armar(_filas(), ExportOpciones(agrupacion=agrup)).resumen.por_tipo)
        for agrup in ("por_tipo", "por_socio", "todo_junto", "plana")
    }
    assert len({tuple(sorted(r.items())) for r in por_agrupacion.values()}) == 1


def test_fecha_legacy_invalida_no_rompe_el_orden():
    from app.modules.facturacion.export.datos import FilaExport  # noqa: F401
    filas = _filas()
    filas[2].fecha_practica = None  # lo que deja datos.py con '0000-00-00'
    for agrup in ("por_tipo", "por_socio", "todo_junto", "plana"):
        armar(filas, ExportOpciones(agrupacion=agrup))
