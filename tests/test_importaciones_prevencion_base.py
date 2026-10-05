"""Import de Prevención contra la base, en la O.S. 888 (la de prueba).

Sesión sin commit (`commit` → `flush`): elegir socio de una matrícula repetida,
el aviso de lo cargado a mano en el período, el aviso de $0 y la obra social
permitida.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.importaciones.prevencion.servicio import procesar
from app.modules.importaciones.schemas import FilaReporte

OS_PRUEBA = 888
PERIODO = "209912"  # sin cabecera: el gate de período cerrado no interviene
CODIGO = "180104"   # NN, sin restricción de especialidad; en la 888 vale $0


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


async def _matricula(db, socios: int) -> int:
    # MySQL 5.7 no admite LIMIT dentro de un IN (subquery): se trae la matrícula sola.
    return (await db.execute(
        select(ListadoMedico.MATRICULA_PROV)
        .where(ListadoMedico.MATRICULA_PROV > 0)
        .group_by(ListadoMedico.MATRICULA_PROV)
        .having(func.count() == socios)
        .limit(1)
    )).scalar_one()


async def _socios(db, mat: int) -> list[ListadoMedico]:
    return list((await db.execute(
        select(ListadoMedico).where(ListadoMedico.MATRICULA_PROV == mat).order_by(ListadoMedico.NRO_SOCIO)
    )).scalars().all())


async def _socio_que_factura(db, socios_en_la_matricula: int) -> ListadoMedico:
    """Un socio que ya facturó el código en la 103 (tiene la especialidad que lo
    habilita) y cuya matrícula comparten `socios_en_la_matricula` socios."""
    M = DetalleFacturacionCMC
    nros = (await db.execute(
        select(M.cod_med).where(M.cod_obr == "103", M.cod_nom == CODIGO).distinct()
    )).scalars().all()
    for nro in nros:
        med = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(nro))
        )).scalar_one_or_none()
        if med is not None and med.MATRICULA_PROV and len(await _socios(db, med.MATRICULA_PROV)) == socios_en_la_matricula:
            return med
    pytest.skip(f"No hay un socio que facture {CODIGO} con matrícula de {socios_en_la_matricula} socios.")


def _fila(mat, autorizacion="99100001", **kw) -> FilaReporte:
    base = dict(
        nro_autorizacion=autorizacion, fecha=datetime.date(2026, 9, 10), afiliado="PRUEBA",
        matricula=str(mat), codigo=CODIGO, estado="AUTORIZADA",
    )
    base.update(kw)
    return FilaReporte(**base)


async def _procesar(s, filas, grabar=False):
    return await procesar(
        s, filas=filas, periodo=PERIODO, usuario_carga="test", grabar=grabar, obra_social=OS_PRUEBA,
    )


@pytest.mark.asyncio
async def test_solo_103_u_888():
    with pytest.raises(HTTPException) as exc:
        await procesar(None, filas=[], periodo=PERIODO, usuario_carga="t", grabar=False, obra_social=81)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_matricula_repetida_pide_elegir_y_con_eleccion_entra(s):
    b = await _socio_que_factura(s, 2)
    mat = b.MATRICULA_PROV

    out = await _procesar(s, [_fila(mat)])
    f = out.filas[0]
    assert f.resultado == "elegir_socio" and out.resumen.por_elegir == 1
    assert b.NRO_SOCIO in {c.nro_socio for c in f.candidatos} and len(f.candidatos) == 2

    with pytest.raises(HTTPException) as exc:
        await _procesar(s, [_fila(mat)], grabar=True)
    assert exc.value.status_code == 422

    elegida = _fila(mat, nro_socio_elegido=b.NRO_SOCIO)
    ok = await _procesar(s, [elegida], grabar=True)
    assert ok.filas[0].resultado == "grabada", ok.filas[0].motivo
    assert ok.filas[0].nro_socio == b.NRO_SOCIO
    row = (await s.execute(select(DetalleFacturacionCMC).where(
        DetalleFacturacionCMC.id_detalle_prestaciones == ok.filas[0].id_detalle
    ))).scalar_one()
    assert str(row.cod_obr) == str(OS_PRUEBA) and str(row.cod_med) == str(b.NRO_SOCIO)


@pytest.mark.asyncio
async def test_socio_elegido_que_no_tiene_esa_matricula_no_entra(s):
    mat = await _matricula(s, 2)
    otro = (await _socios(s, await _matricula(s, 1)))[0]
    out = await _procesar(s, [_fila(mat, nro_socio_elegido=otro.NRO_SOCIO)])
    assert out.filas[0].resultado == "omitida" and "no tiene la matrícula" in out.filas[0].motivo


@pytest.mark.asyncio
async def test_avisa_lo_cargado_a_mano_y_el_cero(s):
    med = await _socio_que_factura(s, 1)
    s.add(DetalleFacturacionCMC(
        periodo=PERIODO, version=1, estado="A", cod_obr=str(OS_PRUEBA), cod_med=str(med.NRO_SOCIO),
        cod_nom=CODIGO, cantidad=3, sesion=1, porc=100, nro_orden=0, tpo_funcion="H", manual="A",
        ayudante=Decimal("0"), honorarios=Decimal("0"), gastos=Decimal("0"), coseguro=Decimal("0"),
        importe_total=Decimal("0"), dni_p="", nom_ape_p="", fecha_practica=datetime.date(2026, 9, 1),
        usuario="test",
    ))
    await s.flush()

    out = await _procesar(s, [_fila(med.MATRICULA_PROV)])
    f = out.filas[0]
    assert f.resultado == "grabable", f.motivo
    assert "cargado a mano" in f.aviso and "cantidad 3" in f.aviso
    assert f.importe_total == Decimal("0.00") and "$0" in f.aviso
    assert out.resumen.con_aviso == 1


@pytest.mark.asyncio
async def test_sin_matricula_no_ofrece_elegir(s):
    out = await _procesar(s, [_fila("NO INFORMADO")])
    assert out.filas[0].resultado == "omitida" and out.resumen.por_elegir == 0
