"""Flujo del nomenclador en 4 etapas (código → quién factura → alta en O.S. → precio).

Lo que se prueba es lo nuevo de la etapa 3 (`nm_codigo_obra_social`): dar de alta
un código SIN precio, qué ve facturación en cada estado, el bloqueo duro (no hay
precio sin alta), la sincronización con las variantes de precio, "Actualizar en
obras sociales" y revalorizar lo cargado en $0.

Sesión sin commit (`commit` → `flush`, `rollback` → no-op): el fixture `db`
descarta todo al cerrar.
"""
import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.db.models.medico import ListadoMedico
from app.db.models.nomenclador_cmc import NomencladorCMC, Valor
from app.modules.facturacion import revalorizar as reval
from app.modules.facturacion import service as fact
from app.modules.nomenclador import alta_os, aplicar_plantilla, service
from app.modules.nomenclador.routes_valores import _crear_valor_con_componentes, create_valor
from app.modules.nomenclador.schemas import (
    AltaCodigoItem,
    AltaCodigosIn,
    CodigoObraSocialUpdate,
    ValorComponenteIn,
    ValorCreate,
)

OS = 990_091
OS_2 = 990_092
# `detalle_facturacion.cod_obr` es SMALLINT: el test de facturación usa una O.S. que entre.
OS_FACT = 32_091
NOM_ID = 897
HOY = datetime.date.today()


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    return db


async def _os(db, *nros):
    for nro in nros:
        db.add(ObrasSociales(NRO_OBRASOCIAL=nro, OBRA_SOCIAL=f"PRUEBA ETAPAS {nro}", MARCA="S", cuit="0"))
    await db.flush()


async def _esps(db, n=2) -> list[int]:
    return list((await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).order_by(Especialidad.ID_COLEGIO_ESPE).limit(n)
    )).scalars())


async def _alta(db, os_nro=OS, **kw):
    [r] = await alta_os.dar_de_alta(
        db, AltaCodigosIn(items=[AltaCodigoItem(obra_social_nro=os_nro, nomenclador_id=NOM_ID, **kw)]), "test",
    )
    return r


async def _precio_fijo(db, os_nro=OS, honorarios="1000", gastos="500"):
    return await _crear_valor_con_componentes(
        db=db, obra_social_nro=os_nro, nomenclador_id=NOM_ID, origen="NE", vigencia_desde=HOY,
        componentes_in=[
            ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal(honorarios)),
            ValorComponenteIn(concepto="Gastos", valor_unitario=Decimal(gastos)),
        ],
        descripcion="DESC", nivel=None, complejidad=None, especialidad_id_colegio=None,
        observacion=None, sin_restriccion_especialidad=True,
    )


async def _medico(db) -> ListadoMedico:
    return (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO > 0).limit(1))).scalar_one()


# ─── Etapa 3: alta sin precio ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alta_sin_precio_copia_la_plantilla_y_queda_sin_precio(s):
    await _os(s, OS)
    e1, e2 = await _esps(s)
    nom = await s.get(NomencladorCMC, NOM_ID)
    nom.sin_restriccion_especialidad = None
    await aplicar_plantilla.reemplazar_plantilla(s, nom.codigo, [e1, e2])

    r = await _alta(s)
    assert r.estado == "creado" and not r.sin_quien_factura
    assert await service.especialidades_habilitadas_de(s, nom.codigo, OS) == {e1, e2}
    detalle = await alta_os.detalle_par(s, OS, NOM_ID)
    assert detalle.estado == "sin_precio" and detalle.descripcion == nom.descripcion

    listado = await alta_os.listar_por_os(s, OS, estado="sin_precio", q=nom.codigo)
    assert [i.codigo for i in listado.items] == [nom.codigo]
    assert (await _alta(s)).estado == "ya_existia"


@pytest.mark.asyncio
async def test_alta_no_pisa_especialidades_ya_configuradas(s):
    await _os(s, OS)
    e1, e2 = await _esps(s)
    nom = await s.get(NomencladorCMC, NOM_ID)
    nom.sin_restriccion_especialidad = None
    await aplicar_plantilla.reemplazar_plantilla(s, nom.codigo, [e1, e2])
    await service.reemplazar_especialidades(s, OS, nom.codigo, [e2])

    await _alta(s)
    assert await service.especialidades_habilitadas_de(s, nom.codigo, OS) == {e2}


@pytest.mark.asyncio
async def test_lookup_distingue_sin_alta_sin_precio_y_suspendido(s):
    await _os(s, OS)
    medico = await _medico(s)

    with pytest.raises(service.LookupError) as e:
        await service.lookup_precio(NOM_ID, OS, HOY, medico.ID, s)
    assert "no está dado de alta" in e.value.message

    await _alta(s, sin_restriccion_especialidad=True)
    with pytest.raises(service.LookupError) as e:
        await service.lookup_precio(NOM_ID, OS, HOY, medico.ID, s)
    assert e.value.sin_precio and "todavía no tiene precio" in e.value.message

    await alta_os.cambiar_estado(s, OS, NOM_ID, "suspendido")
    with pytest.raises(service.LookupError) as e:
        await service.lookup_precio(NOM_ID, OS, HOY, medico.ID, s)
    assert "suspendido" in e.value.message


# ─── Etapa 4: el precio exige el alta ────────────────────────────────────────

@pytest.mark.asyncio
async def test_precio_manual_sin_alta_da_409_y_con_alta_queda_con_precio(s):
    await _os(s, OS)
    body = ValorCreate(
        obra_social_nro=OS, nomenclador_id=NOM_ID, origen="NE", vigencia_desde=HOY,
        descripcion="DESC", sin_restriccion_especialidad=True,
        componentes=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("100"))],
    )
    with pytest.raises(HTTPException) as e:
        await create_valor(body, s)
    assert e.value.status_code == 409

    await _alta(s, sin_restriccion_especialidad=True)
    await create_valor(body, s)
    assert (await alta_os.detalle_par(s, OS, NOM_ID)).estado == "con_precio"


@pytest.mark.asyncio
async def test_crear_precio_por_herramienta_deja_el_alta(s):
    """Las herramientas que crean precio sin pasar por la etapa 3 (replicar, CSV,
    Completar NN) dejan el código dado de alta igual."""
    await _os(s, OS)
    await _precio_fijo(s)
    par = await alta_os.get_par(s, OS, NOM_ID)
    assert par is not None and par.estado == "activo" and par.sin_restriccion_especialidad


# ─── Sincronización par ⇄ variantes ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_editar_el_alta_se_copia_a_las_variantes(s):
    await _os(s, OS)
    await _alta(s, sin_restriccion_especialidad=True)
    v = await _precio_fijo(s)
    await alta_os.actualizar_par(s, OS, NOM_ID, CodigoObraSocialUpdate(
        descripcion="NOMBRE EN LA OS", requiere_autorizacion=True, cantidad_ayudantes=2,
    ))
    await s.refresh(v)
    assert v.descripcion == "NOMBRE EN LA OS" and v.requiere_autorizacion and v.cantidad_ayudantes == 2


@pytest.mark.asyncio
async def test_sin_restriccion_lo_decide_el_alta(s):
    await _os(s, OS)
    e1, _ = await _esps(s)
    await _alta(s, sin_restriccion_especialidad=True, especialidades=[e1])
    nom = await s.get(NomencladorCMC, NOM_ID)
    assert await service.par_sin_restriccion(s, nom.codigo, OS)
    await alta_os.actualizar_par(s, OS, NOM_ID, CodigoObraSocialUpdate(sin_restriccion_especialidad=False))
    assert not await service.par_sin_restriccion(s, nom.codigo, OS)


# ─── Etapa 2 → "Actualizar en obras sociales" ────────────────────────────────

@pytest.mark.asyncio
async def test_propagar_agregar_e_igualar_conserva_lo_que_tiene_precio(s):
    await _os(s, OS, OS_2)
    e1, e2, e3 = await _esps(s, 3)
    nom = await s.get(NomencladorCMC, NOM_ID)
    nom.sin_restriccion_especialidad = None
    await _alta(s, especialidades=[e1, e3], sin_restriccion_especialidad=False)
    await _alta(s, os_nro=OS_2, sin_restriccion_especialidad=True)
    # e3 tiene precio propio en OS.
    await _crear_valor_con_componentes(
        db=s, obra_social_nro=OS, nomenclador_id=NOM_ID, origen="NE", vigencia_desde=HOY,
        componentes_in=[ValorComponenteIn(concepto="Honorarios", valor_unitario=Decimal("10"))],
        descripcion="DESC", nivel=None, complejidad=None, especialidad_id_colegio=e3, observacion=None,
    )
    await aplicar_plantilla.reemplazar_plantilla(s, nom.codigo, [e1, e2])

    prev = await alta_os.propagar_plantilla(s, nom, [OS, OS_2], "igualar", dry_run=True)
    r_os = next(r for r in prev if r.obra_social_nro == OS)
    assert r_os.agrega == [e2] and r_os.quita == [] and r_os.conserva_por_precio == [e3]
    assert next(r for r in prev if r.obra_social_nro == OS_2).estado == "salteada"
    assert await service.especialidades_habilitadas_de(s, nom.codigo, OS) == {e1, e3}  # dry run

    await alta_os.propagar_plantilla(s, nom, [OS], "agregar", dry_run=False)
    assert await service.especialidades_habilitadas_de(s, nom.codigo, OS) == {e1, e2, e3}


# ─── Facturación: carga sin precio + revalorizar ─────────────────────────────

@pytest.mark.asyncio
async def test_carga_sin_precio_y_revalorizar(s, monkeypatch):
    monkeypatch.setattr(fact.settings, "CARGA_SIN_PRECIO", True)
    await _os(s, OS_FACT)
    medico = await _medico(s)
    nom = await s.get(NomencladorCMC, NOM_ID)
    await _alta(s, os_nro=OS_FACT, sin_restriccion_especialidad=True, descripcion="EN LA OS")

    precio = await fact.resolver_precio(s, str(OS_FACT), medico, nom.codigo, HOY)
    assert precio.admitido and precio.sin_precio and precio.descripcion == "EN LA OS"

    row = DetalleFacturacionCMC(
        periodo=HOY.strftime("%Y%m"), cod_obr=str(OS_FACT), cod_med=str(medico.NRO_SOCIO),
        nro_orden="0", cod_nom=nom.codigo, nomenclador_id=nom.id, cantidad=1, sesion=1,
        honorarios=Decimal("0"), gastos=Decimal("0"), ayudante=Decimal("0"),
        importe_total=Decimal("0"), coseguro=Decimal("0"), manual="A", porc=100,
        fecha_practica=HOY, estado="A", usuario="test", sin_valorizar="HG", tpo_funcion="H",
    )
    s.add(row)
    await s.flush()

    # Todavía sin precio: la vista previa lo informa y no cambia nada.
    out = await reval.revalorizar(s, reval.RevalorizarIn(cod_obra=str(OS_FACT), codigo=nom.codigo, dry_run=True))
    assert [i.estado for i in out.items] == ["sin_precio"]

    await _precio_fijo(s, os_nro=OS_FACT, honorarios="1000", gastos="500")
    prev = await reval.revalorizar(s, reval.RevalorizarIn(cod_obra=str(OS_FACT), codigo=nom.codigo, dry_run=True))
    [item] = prev.items
    assert item.estado == "revalorizada" and item.importe_despues == Decimal("1500.00")
    assert row.importe_total == Decimal("0")  # dry run

    await reval.revalorizar(s, reval.RevalorizarIn(cod_obra=str(OS_FACT), codigo=nom.codigo))
    assert row.importe_total == Decimal("1500.00") and row.sin_valorizar is None
    assert row.honorarios == Decimal("1000.00") and row.gastos == Decimal("500.00")


def test_conceptos_sin_precio_por_defecto():
    from types import SimpleNamespace as N

    base = dict(honorarios=Decimal("0"), gastos=Decimal("0"), ayudante=Decimal("0"), rol=None)
    assert fact.conceptos_sin_precio(N(**base, concepto_sin_precio=None)) == "HG"
    assert fact.conceptos_sin_precio(N(**{**base, "rol": "pediatra"}, concepto_sin_precio=None)) == "H"
    assert fact.conceptos_sin_precio(N(**base, concepto_sin_precio="A")) == "A"
