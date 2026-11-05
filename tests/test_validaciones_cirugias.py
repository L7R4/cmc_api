"""Un médico no puede cargar cirugías (categoría 'Honorarios individuales').

Las carga el Colegio por facturación. La regla corta en tres lugares:

* `validaciones/core/pipeline.py::crear_prestacion` — antes de hablar con la obra social
  (Sancor y las demás en línea consumen una autorización o un token);
* `validaciones/core/consultas.py::buscar_codigos` — el buscador del médico no las ofrece;
* `facturacion/service.py::guardar_prestaciones` con `actor=medico` — la carga del portal.

El Colegio operando en nombre del médico NO queda alcanzado: sigue pudiendo cargarlas.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.facturacion import service
from app.modules.facturacion.schemas import PrecioResponse
from app.modules.validaciones.core import consultas, pipeline
from app.modules.validaciones.schemas import PrestacionCreate

OSPM = 433
CIRUGIA = "030801"  # categoría 'Honorarios individuales' en la base de desarrollo
CONSULTA = "420351"


class _Cortar(Exception):
    """Frena `crear_prestacion` justo después de la compuerta, sin tocar la base."""


# ── Helper contra la base ─────────────────────────────────────────────────────

async def test_detecta_la_cirugia_y_no_la_consulta(db):
    assert await service.es_honorarios_individuales(db, CIRUGIA, str(OSPM)) is True
    assert await service.es_honorarios_individuales(db, CONSULTA, str(OSPM)) is False


async def test_exigir_levanta_422_con_el_codigo_en_el_mensaje(db):
    with pytest.raises(HTTPException) as exc:
        await service.exigir_no_honorarios_individuales(db, CIRUGIA, str(OSPM))
    assert exc.value.status_code == 422
    assert CIRUGIA in exc.value.detail and "cirugía" in exc.value.detail
    # Un código común pasa sin levantar nada.
    await service.exigir_no_honorarios_individuales(db, CONSULTA, str(OSPM))


# ── Panel de Validaciones: la compuerta va antes de la obra social ────────────

@pytest.fixture
def pipeline_falso(monkeypatch):
    llamadas = {"compuerta": [], "validar": 0}

    async def _compuerta(_db, codigo, cod_obra):
        llamadas["compuerta"].append((codigo, cod_obra))
        if codigo == CIRUGIA:
            raise HTTPException(422, "es una cirugía")

    async def _medico(_db, _nro):
        raise _Cortar()  # llegar acá = la compuerta dejó pasar

    class _Obra:
        nro = OSPM
        entrada = None

        async def validar(self, *_a, **_k):
            llamadas["validar"] += 1

    monkeypatch.setattr(pipeline, "exigir_no_honorarios_individuales", _compuerta)
    monkeypatch.setattr(pipeline, "get_medico", _medico)
    monkeypatch.setattr(pipeline.obras, "obtener_o_error", lambda _nro: _Obra())
    monkeypatch.setattr(pipeline, "parsear_entrada", lambda _modelo, _payload: SimpleNamespace(cantidad=1))
    return llamadas


def _payload(codigo):
    return PrestacionCreate(obra_social=OSPM, codigo=codigo)


async def test_el_medico_no_carga_una_cirugia_y_no_se_consulta_a_la_obra_social(pipeline_falso):
    with pytest.raises(HTTPException) as exc:
        await pipeline.crear_prestacion(None, payload=_payload(CIRUGIA), nro_socio=55, usuario_carga=55)
    assert exc.value.status_code == 422
    assert pipeline_falso["validar"] == 0
    assert pipeline_falso["compuerta"] == [(CIRUGIA, str(OSPM))]


async def test_el_medico_si_carga_un_codigo_comun(pipeline_falso):
    with pytest.raises(_Cortar):  # pasó la compuerta y siguió con el flujo
        await pipeline.crear_prestacion(None, payload=_payload(CONSULTA), nro_socio=55, usuario_carga=55)


async def test_el_colegio_cargando_por_el_medico_no_pasa_por_la_compuerta(pipeline_falso):
    with pytest.raises(_Cortar):
        await pipeline.crear_prestacion(None, payload=_payload(CIRUGIA), nro_socio=55, usuario_carga=900)
    assert pipeline_falso["compuerta"] == []


# ── Buscador de códigos ───────────────────────────────────────────────────────

HABILITADOS = [
    {"codigo": CIRUGIA, "descripcion": "PAROTIDECTOMIA TOTAL"},
    {"codigo": CONSULTA, "descripcion": "CONSULTA ESPECIALIZADA"},
]


class _DbFalsa:
    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [CIRUGIA, CONSULTA]))


@pytest.fixture
def buscar(monkeypatch):
    async def _medico(_db, _nro):
        return SimpleNamespace(ID=1)

    async def _habilitados(_db, _medico, _q, _os):
        return HABILITADOS

    async def _precio(_db, _os, _medico, codigo, _hoy):
        return PrecioResponse(
            honorarios=Decimal("1000"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion=codigo, fuente="nm", admitido=True,
        )

    async def _es_cirugia(_db, codigo, _os):
        return codigo == CIRUGIA

    async def _sin_documentacion(_db, _os, _codigos):
        return set()

    monkeypatch.setattr(consultas, "get_medico", _medico)
    monkeypatch.setattr(consultas.service_nm, "listar_codigos_habilitados", _habilitados)
    monkeypatch.setattr(consultas, "resolver_precio", _precio)
    monkeypatch.setattr(consultas, "es_honorarios_individuales", _es_cirugia)
    monkeypatch.setattr(consultas.documentacion, "codigos_que_requieren", _sin_documentacion)

    async def _buscar(**kw):
        filas = await consultas.buscar_codigos(_DbFalsa(), OSPM, 55, "", 20, **kw)
        return [f["codigo"] for f in filas]

    return _buscar


async def test_el_buscador_del_medico_no_ofrece_cirugias(buscar):
    assert await buscar(sin_cirugias=True) == [CONSULTA]


async def test_el_buscador_del_colegio_las_sigue_ofreciendo(buscar):
    assert await buscar() == [CIRUGIA, CONSULTA]


# ── Carga del portal (facturacion/medico/prestaciones) ────────────────────────

async def test_la_carga_del_portal_del_medico_rechaza_una_cirugia(db):
    from sqlalchemy import select

    from app.db.models import ListadoMedico
    from app.modules.facturacion.schemas import PrestacionItem

    socio = (await db.execute(
        select(ListadoMedico.NRO_SOCIO).where(ListadoMedico.estado_cuenta == "activo").limit(1)
    )).scalar_one()
    item = PrestacionItem(cod_medico=str(socio), cod_nomenclador=CIRUGIA, dni_paciente="1")
    with pytest.raises(HTTPException) as exc:
        await service.preparar_equipo(
            db, cod_obra=str(OSPM), periodo="209912", version_destino=1,
            items=[item], usuario=str(socio), actor=service.ORIGEN_MEDICO,
        )
    assert exc.value.status_code == 422
    assert CIRUGIA in exc.value.detail and "cirugía" in exc.value.detail
