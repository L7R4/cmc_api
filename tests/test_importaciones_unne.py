"""Importador del Excel de UNNE (`importaciones/unne/servicio.py`).

Funciones puras (partir el importe, sugerir socio) y el camino completo contra la
base en una sesión sin commit (`commit` → `flush`): grabar, duplicados por orden y
filas que esperan que se elija el socio.
"""
import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.importaciones.schemas import FilaUnne
from app.modules.importaciones.unne import NRO_UNNE
from app.modules.importaciones.unne.servicio import partir_importe, procesar, sugerir_socio

D = Decimal
PERIODO = "209912"  # sin cabecera: el gate de período cerrado no interviene


# ── Puras ────────────────────────────────────────────────────────────────────

def test_hon_gto_se_parte_con_la_variante_que_suma_lo_mismo():
    h, g, aviso = partir_importe(D("105700"), True, [(D("98431"), D("0")), (D("79800"), D("25900"))])
    assert (h, g, aviso) == (D("79800.00"), D("25900.00"), "")


def test_hon_gto_sin_valor_o_que_no_cuadra_va_todo_a_honorarios_con_aviso():
    assert partir_importe(D("105700"), True, [])[:2] == (D("105700.00"), D("0.00"))
    assert "sin valor" in partir_importe(D("105700"), True, [])[2]
    h, g, aviso = partir_importe(D("105700"), True, [(D("80000"), D("20000"))])
    assert (h, g) == (D("105700.00"), D("0.00")) and "distinto" in aviso


def test_especialista_va_todo_a_honorarios_y_solo_avisa_si_difiere():
    assert partir_importe(D("30000"), False, []) == (D("30000.00"), D("0.00"), "")
    assert partir_importe(D("30000"), False, [(D("30000"), D("0"))]) == (D("30000.00"), D("0.00"), "")
    assert "distinto" in partir_importe(D("30000"), False, [(D("28000"), D("0"))])[2]


def test_sugiere_el_unico_activo_que_ya_factura_unne():
    a = SimpleNamespace(NRO_SOCIO=1, EXISTE="S")
    b = SimpleNamespace(NRO_SOCIO=2, EXISTE="N")
    c = SimpleNamespace(NRO_SOCIO=3, EXISTE="S")
    assert sugerir_socio([a, b], {1, 2}) is a
    assert sugerir_socio([a, c], {1, 3}) is None  # dos activos: que elija la persona
    assert sugerir_socio([a, c], set()) is None


# ── Con base ─────────────────────────────────────────────────────────────────

@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _medico_con_matricula_unica(db) -> ListadoMedico:
    # MySQL 5.7 no admite LIMIT dentro de un IN (subquery): dos consultas.
    mat = (await db.execute(
        select(ListadoMedico.MATRICULA_PROV)
        .where(ListadoMedico.MATRICULA_PROV > 0)
        .group_by(ListadoMedico.MATRICULA_PROV)
        .having(func.count() == 1)
        .limit(1)
    )).scalar_one()
    return (await db.execute(
        select(ListadoMedico).where(ListadoMedico.MATRICULA_PROV == mat)
    )).scalar_one()


def _fila(medico, orden="99000001", reg=1, codigo="420101", **kw) -> FilaUnne:
    base = dict(
        orden=orden, reg=reg, matricula=str(medico.MATRICULA_PROV), prestador="X",
        provincia="Corrientes", fecha=datetime.date(2026, 9, 2), cantidad=1, codigo=codigo,
        funcion="ESPECIALISTA", dni="20266048", paciente="PRUEBA UNNE", importe=D("30000"),
    )
    base.update(kw)
    return FilaUnne(**base)


async def _grabadas(db, orden):
    return (await db.execute(select(DetalleFacturacionCMC).where(
        DetalleFacturacionCMC.cod_obr == str(NRO_UNNE), DetalleFacturacionCMC.autorizacion == orden,
    ))).scalars().all()


@pytest.mark.asyncio
async def test_graba_y_al_reimportar_sale_duplicada(s):
    med = await _medico_con_matricula_unica(s)
    filas = [_fila(med), _fila(med, reg=2, codigo="220101", cantidad=2, importe=D("15309"))]

    prev = await procesar(s, filas=filas, periodo=PERIODO, usuario_carga="test", grabar=False)
    assert [f.resultado for f in prev.filas] == ["grabable", "grabable"]
    assert await _grabadas(s, "99000001") == []  # previsualizar no escribe

    out = await procesar(s, filas=filas, periodo=PERIODO, usuario_carga="test", grabar=True)
    assert [f.resultado for f in out.filas] == ["grabada", "grabada"]
    rows = sorted(await _grabadas(s, "99000001"), key=lambda r: r.cod_nom)
    assert [(r.cod_nom, r.cantidad, r.honorarios, r.importe_total) for r in rows] == [
        ("220101", 2, D("7654.50"), D("15309.00")),
        ("420101", 1, D("30000.00"), D("30000.00")),
    ]
    assert all(str(r.cod_med) == str(med.NRO_SOCIO) and r.periodo == PERIODO and r.dni_p == "20266048" for r in rows)
    assert all(str(r.nro_orden) == str(r.id_detalle_prestaciones) for r in rows)

    otra = await procesar(s, filas=filas, periodo=PERIODO, usuario_carga="test", grabar=False)
    assert [f.resultado for f in otra.filas] == ["duplicada", "duplicada"]


@pytest.mark.asyncio
async def test_orden_e_item_repetidos_en_el_archivo(s):
    med = await _medico_con_matricula_unica(s)
    out = await procesar(s, filas=[_fila(med), _fila(med)], periodo=PERIODO, usuario_carga="t", grabar=False)
    assert [f.resultado for f in out.filas] == ["grabable", "duplicada"]


@pytest.mark.asyncio
async def test_matricula_sin_socio_pide_elegir_y_no_deja_confirmar(s):
    med = await _medico_con_matricula_unica(s)
    sin = _fila(med, orden="99000002", matricula="999999999")
    out = await procesar(s, filas=[sin], periodo=PERIODO, usuario_carga="t", grabar=False)
    assert out.filas[0].resultado == "elegir_socio" and out.resumen.por_elegir == 1

    with pytest.raises(HTTPException) as exc:
        await procesar(s, filas=[sin], periodo=PERIODO, usuario_carga="t", grabar=True)
    assert exc.value.status_code == 422

    # Elegido a mano: entra con ese socio.
    elegida = sin.model_copy(update={"nro_socio_elegido": med.NRO_SOCIO})
    ok = await procesar(s, filas=[elegida], periodo=PERIODO, usuario_carga="t", grabar=True)
    assert ok.filas[0].resultado == "grabada"
    assert str((await _grabadas(s, "99000002"))[0].cod_med) == str(med.NRO_SOCIO)
