"""Cómo se graban los estados que devuelven las obras sociales.

Desde 2026-08-18 ningún validador emite `"pendiente"`: lo que la obra social
deja a la espera de que el afiliado lo gestione se graba como `"rechazada"`,
con el motivo adelante en `validacion_detalle`. El desenlace para el Colegio ya
era el mismo —importe 0, `estado='X'`, fuera de la factura—, y un estado propio
hacía leer como "todavía puede salir" algo que no iba a salir.

Lo que estos tests cuidan:

* que la clasificación del **cliente** siga distinguiendo el caso (es lo que
  decide el motivo, y en Nobis además si hay una orden que anular allá);
* que `"pendiente"` siga siendo un estado válido del contrato, porque hay filas
  grabadas antes del cambio;
* que las facturables no se hayan movido.
"""
import pytest

from app.modules.validaciones.core.grabado import (
    ESTADO_PENDIENTE_HISTORICO,
    ESTADOS_FACTURABLES,
)
from app.modules.validaciones.obras.nobis import cliente as nobis
from app.modules.validaciones.obras.sancor import cliente as sancor


# ── Contrato compartido ───────────────────────────────────────────────────────

def test_pendiente_no_es_facturable_ni_dejo_de_ser_un_estado_valido():
    """Renombrar el estado no puede cambiar qué se factura, ni romper la
    lectura de las filas viejas."""
    assert ESTADOS_FACTURABLES == ("autorizada", "cargada")
    assert ESTADO_PENDIENTE_HISTORICO not in ESTADOS_FACTURABLES

    from app.modules.validaciones.schemas import EstadoPrestacion

    assert ESTADO_PENDIENTE_HISTORICO in EstadoPrestacion.__args__


# ── Sancor ────────────────────────────────────────────────────────────────────

def test_sancor_sigue_distinguiendo_el_pendiente_de_un_rechazo():
    """`requiere_gestion` no se tocó: es lo que hace que el motivo diga
    "Pendiente de autorización" en vez de pasar el rechazo tal cual."""
    assert "M024" in sancor.CODIGOS_PENDIENTE
    assert "M022" not in sancor.CODIGOS_PENDIENTE


# ── Nobis ─────────────────────────────────────────────────────────────────────

def _respuesta_nobis(estado: str, cod: str = "C123", num: str = "N456") -> str:
    return (
        "<Autorizacion>"
        f"<Estado>{estado}</Estado>"
        f"<Cod>{cod}</Cod>"
        f"<Num>{num}</Num>"
        "<Cose_Total>0</Cose_Total>"
        "</Autorizacion>"
    )


@pytest.mark.parametrize(
    "estado, autorizada, requiere_gestion, letra",
    [
        ("A-Autorizado", True, False, "A"),
        ("P-Pendiente", False, True, "P"),
        ("R-Rechazada", False, False, "R"),
    ],
)
def test_nobis_clasifica_las_tres_letras(estado, autorizada, requiere_gestion, letra):
    r = nobis.interpretar_autorizacion(_respuesta_nobis(estado))
    assert r.autorizada is autorizada
    assert r.requiere_gestion is requiere_gestion
    assert r.estado == letra


def test_nobis_pendiente_trae_orden_para_anular():
    """El punto delicado del renombre: un `P-Pendiente` **crea la orden** en
    Nobis. Como ahora se graba igual que un rechazo, `ValidadorNobis.anular()`
    ya no puede filtrar por `validacion_estado` — filtra por esta letra, que
    queda en `traza["estado"]`. Si se perdiera, la orden quedaría viva allá al
    eliminar la prestación."""
    from app.modules.validaciones.obras.nobis.validador import _LETRAS_CON_ORDEN

    r = nobis.interpretar_autorizacion(_respuesta_nobis("P-Pendiente"))
    assert r.estado in _LETRAS_CON_ORDEN
    assert r.cod_autorizacion == "C123"  # lo que exige AnularOrdenNroCod
    assert r.nro_orden == "N456"

    rechazada = nobis.interpretar_autorizacion(_respuesta_nobis("R-Rechazada"))
    assert rechazada.estado not in _LETRAS_CON_ORDEN
