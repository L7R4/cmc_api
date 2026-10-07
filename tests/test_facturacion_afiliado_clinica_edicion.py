"""Afiliado con sólo nombre o sólo número, edición de afiliado y clínica (lápiz de la
carga) y la etiqueta del precio.

Sesión sin commit (`commit` → `flush`): todo se descarta al cerrar. Prestaciones
sintéticas en una O.S. que no existe (29996).
"""
import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.facturacion import service
from app.modules.facturacion.schemas import (
    AfiliadoCreate, AfiliadoUpdate, ClinicaCreate, ClinicaUpdate, PrestacionUpdate,
)

OS = "29996"


@pytest.fixture
def s(db, monkeypatch):
    monkeypatch.setattr(db, "commit", db.flush)
    return db


def _fila(periodo, dni, nombre):
    return DetalleFacturacionCMC(
        periodo=periodo, version=1, estado="A", cod_obr=OS, cod_med="1", cod_nom="420101",
        cantidad=1, sesion=1, porc=100, nro_orden="0", manual="A", tpo_funcion="H",
        honorarios=0, gastos=0, ayudante=0, importe_total=0, dni_p=dni, nom_ape_p=nombre,
        usuario="test",
    )


async def _facturas(db):
    """209901 abierta, 209902 cerrada."""
    for p in ("209901", "209902"):
        await service._ensure_factura_abierta(db, OS, p, "test")
    await db.flush()
    (await service._get_factura(db, OS, "209902")).estado = "C"
    await db.flush()


def test_afiliado_exige_nombre_o_numero():
    assert AfiliadoCreate(nombre=" PEREZ JUAN ").dni is None
    assert AfiliadoCreate(dni="12345678").nombre is None
    with pytest.raises(ValidationError):
        AfiliadoCreate(dni="  ", nombre="")


@pytest.mark.asyncio
async def test_afiliado_sin_numero_se_carga_y_se_elige_por_id(s):
    af = await service.crear_afiliado(s, AfiliadoCreate(nombre="SIN NUMERO TEST"), "test")
    otro = await service.crear_afiliado(s, AfiliadoCreate(nombre="OTRO SIN NUMERO"), "test")
    assert af.dni is None and otro.dni is None  # varios sin número conviven

    item = type("I", (), {"afiliado_id": af.id, "dni_paciente": None, "nombre_paciente": None})()
    assert await service.paciente_de(s, item) == (None, "SIN NUMERO TEST")
    suelto = type("I", (), {"afiliado_id": None, "dni_paciente": None, "nombre_paciente": "SUELTO"})()
    assert await service.paciente_de(s, suelto) == (None, "SUELTO")


@pytest.mark.asyncio
async def test_editar_afiliado_corrige_las_prestaciones_de_facturas_abiertas(s):
    await _facturas(s)
    af = await service.crear_afiliado(s, AfiliadoCreate(dni="T-9990001", nombre="VIEJO"), "test")
    abierta, cerrada = _fila("209901", "T-9990001", "VIEJO"), _fila("209902", "T-9990001", "VIEJO")
    s.add_all([abierta, cerrada])
    await s.flush()

    _, corregidas = await service.actualizar_afiliado(s, af.id, AfiliadoUpdate(dni="T-9990002", nombre="NUEVO"))
    assert corregidas == 1  # sólo la de la factura abierta
    await s.refresh(abierta)
    await s.refresh(cerrada)
    assert (abierta.dni_p, abierta.nom_ape_p) == ("T-9990002", "NUEVO")
    assert (cerrada.dni_p, cerrada.nom_ape_p) == ("T-9990001", "VIEJO")

    # Número repetido: 409.
    await service.crear_afiliado(s, AfiliadoCreate(dni="T-9990003", nombre="X"), "test")
    with pytest.raises(HTTPException) as exc:
        await service.actualizar_afiliado(s, af.id, AfiliadoUpdate(dni="T-9990003"))
    assert exc.value.status_code == 409

    # Con prestaciones no anuladas no se borra; sin ellas, sí (por id).
    with pytest.raises(HTTPException) as exc:
        await service.eliminar_afiliado_por_id(s, af.id)
    assert exc.value.status_code == 409
    libre = await service.crear_afiliado(s, AfiliadoCreate(nombre="LIBRE"), "test")
    await service.eliminar_afiliado_por_id(s, libre.id)
    assert await s.get(type(libre), libre.id) is None


@pytest.mark.asyncio
async def test_editar_prestacion_con_afiliado_por_id(s, monkeypatch):
    await _facturas(s)
    af = await service.crear_afiliado(s, AfiliadoCreate(nombre="ELEGIDO POR ID"), "test")
    row = _fila("209901", "T-1", "ANTES")
    s.add(row)
    await s.flush()
    await s.refresh(row)  # carga los server_default, como una fila leída de la base
    # Sólo interesa el paciente: el resto de la edición no se toca.
    data = PrestacionUpdate(afiliado_id=af.id).model_dump(exclude_unset=True)
    assert data == {"afiliado_id": af.id}
    monkeypatch.setattr(service, "_gate_edicion", lambda *_: None)
    await service.editar_prestacion(s, row.id_detalle_prestaciones, PrestacionUpdate(afiliado_id=af.id))
    await s.flush()
    await s.refresh(row)
    assert (row.dni_p, row.nom_ape_p) == (None, "ELEGIDO POR ID")


@pytest.mark.asyncio
async def test_editar_clinica_cambia_el_nombre_y_no_repite(s):
    a = await service.crear_clinica(s, ClinicaCreate(nombre="CLINICA TEST EDICION A"))
    await service.crear_clinica(s, ClinicaCreate(nombre="CLINICA TEST EDICION B"))
    out = await service.actualizar_clinica(s, a["cod"], ClinicaUpdate(nombre=" clinica test editada "))
    assert out["nombre"] == "CLINICA TEST EDITADA"
    with pytest.raises(HTTPException) as exc:
        await service.actualizar_clinica(s, a["cod"], ClinicaUpdate(nombre="CLINICA TEST EDICION B"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_el_precio_dice_de_donde_sale(s):
    # Un socio que ya facturó un código con precio en la 103.
    M = DetalleFacturacionCMC
    fila = (await s.execute(select(M.cod_med, M.cod_nom).where(
        M.cod_obr == "103", M.estado == "A", M.manual == "A", M.honorarios > 0,
    ).limit(1))).first()
    if fila is None:
        pytest.skip("Sin prestaciones cotizadas en la 103")
    med = (await s.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == int(fila.cod_med)))).scalar_one()
    p = await service.resolver_precio(s, "103", med, fila.cod_nom, datetime.date.today())
    if not p.admitido or p.sin_precio:
        pytest.skip("El código ya no tiene precio")
    assert p.origen in ("NN", "NE") and p.tipo_valor in ("fijo", "calculable")
    if p.tipo_valor == "calculable":
        assert p.galeno_nombre
