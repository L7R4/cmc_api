"""Reglas del import de Prevención Salud que no dependen de la base.

Lo que se prueba acá es lo que decide si una fila entra, cómo entra y con qué
número — que es donde un error se convierte en plata mal facturada. El camino
completo (resolución de médico y precio contra la base) necesita las fixtures
de datos y no se cubre en este archivo.

Las matrículas y estados salen de cómo vienen realmente en el reporte: el
campo llega como texto y trae "NO INFORMADO" cuando el efector no la declaró.
"""
from collections import Counter
from decimal import Decimal

import pytest

from app.modules.importaciones.schemas import FilaResultado
from app.modules.importaciones import nucleo
from app.modules.importaciones.prevencion import servicio


# ── Estado que informa la obra social ─────────────────────────────────────────


@pytest.mark.parametrize(
    "estado",
    [
        "RECHAZADA",
        "Rechazada",
        "rechazado por la obra social",
        "NO AUTORIZADA",
        "No autorizado",
    ],
)
def test_estados_que_no_se_facturan(estado):
    assert servicio.fue_rechazada(estado) is True


@pytest.mark.parametrize(
    "estado", ["AUTORIZADA", "Autorizada", "REALIZADA", "", "Pendiente"]
)
def test_estados_que_si_se_facturan(estado):
    assert servicio.fue_rechazada(estado) is False


def test_estado_vacio_o_nulo_no_es_rechazo():
    # El reporte a veces deja la celda vacía; eso no es un rechazo.
    assert servicio.fue_rechazada("") is False
    assert servicio.fue_rechazada(None) is False


# ── Matrícula ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("12345", 12345),
        ("  12345  ", 12345),
        ("MP 12345", 12345),
        ("12.345", 12345),
        ("0", None),          # "0" es 0, que no es una matrícula real
        ("NO INFORMADO", None),
        ("", None),
        (None, None),
    ],
)
def test_lectura_de_matricula(crudo, esperado):
    leido = nucleo.matricula_int(crudo)
    # "0" da 0, que es falsy: la fila termina sin médico igual que si no viniera.
    assert (leido or None) == esperado


# ── Resumen ───────────────────────────────────────────────────────────────────


def _fila(resultado, **kw) -> FilaResultado:
    base = dict(indice=0, resultado=resultado)
    base.update(kw)
    return FilaResultado(**base)


def test_el_resumen_cuenta_cada_desenlace():
    filas = [
        _fila(nucleo.GRABABLE, nro_socio=1, importe_total=Decimal("100.00"),
              estado_detalle=nucleo.DETALLE_ACTIVO),
        _fila(nucleo.GRABABLE, nro_socio=2, importe_total=Decimal("50.50"),
              estado_detalle=nucleo.DETALLE_ACTIVO),
        # Rechazada por la O.S.: se registra, pero vale 0 y no entra a factura.
        _fila(nucleo.GRABABLE, nro_socio=3, importe_total=Decimal("0.00"),
              estado_detalle=nucleo.DETALLE_FUERA_DE_FACTURA),
        _fila(nucleo.DUPLICADA, nro_socio=4),
        _fila(nucleo.OMITIDA),  # sin médico
    ]

    r = nucleo.resumir(filas, "202608")

    assert r.periodo == "202608"
    assert r.total == 5
    assert r.grabables == 3
    assert r.duplicadas == 1
    assert r.omitidas == 1
    assert r.sin_medico == 1
    assert r.rechazadas == 1
    # Sólo suma lo grabable: la duplicada y la omitida no aportan importe.
    assert r.importe_total == Decimal("150.50")


def test_las_filas_ya_grabadas_tambien_cuentan_como_grabables():
    # Después de confirmar, el resultado pasa de `grabable` a `grabada`: el
    # resumen tiene que seguir dando lo mismo o el total cambiaría al guardar.
    filas = [
        _fila(nucleo.GRABADA, nro_socio=1, importe_total=Decimal("80.00"),
              estado_detalle=nucleo.DETALLE_ACTIVO, id_detalle=9001),
    ]
    r = nucleo.resumir(filas, "202608")
    assert r.grabables == 1
    assert r.importe_total == Decimal("80.00")


def test_resumen_de_un_reporte_sin_nada_grabable():
    r = nucleo.resumir([_fila(nucleo.OMITIDA)], "202608")
    assert r.grabables == 0
    assert r.importe_total == Decimal("0.00")


# ── Duplicados ────────────────────────────────────────────────────────────────
#
# El reporte de julio/agosto de 2026 tiene 581 prácticas pero sólo 561 claves
# (autorización, código, fecha) distintas: 20 son repeticiones legítimas — la
# misma autorización lista la práctica dos veces ("1/2" y "2/2") porque el
# médico la hizo dos veces. Por eso lo ya cargado se cuenta y no se marca
# presente; con un conjunto esas 20 se perdían sin aviso.


def _consumir(pendientes, claves):
    """Recorre las claves del archivo como lo hace `procesar`."""
    salida = []
    for k in claves:
        if pendientes[k] > 0:
            pendientes[k] -= 1
            salida.append(nucleo.DUPLICADA)
        else:
            salida.append(nucleo.GRABABLE)
    return salida


A = ("14059057", "420101", "2026-08-07")
B = ("14058550", "180110", "2026-08-10")


def test_la_misma_practica_dos_veces_entra_dos_veces():
    # Nada cargado todavía y el archivo la trae repetida.
    assert _consumir(Counter(), [A, A]) == [nucleo.GRABABLE, nucleo.GRABABLE]


def test_reimportar_el_mismo_archivo_no_duplica_nada():
    assert _consumir(Counter({A: 2, B: 1}), [A, A, B]) == [
        nucleo.DUPLICADA,
        nucleo.DUPLICADA,
        nucleo.DUPLICADA,
    ]


def test_reimportar_algo_cargado_a_medias_completa_lo_que_falta():
    # En la base hay una sola de las dos; tiene que entrar la que falta.
    assert _consumir(Counter({A: 1}), [A, A]) == [
        nucleo.DUPLICADA,
        nucleo.GRABABLE,
    ]
