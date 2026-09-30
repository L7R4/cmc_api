"""NE sin especialidad en un par (OS, código) sin restricción de especialidad.

Es el precio para cualquier médico: el gate de habilitación lo abre
`sin_restriccion_especialidad` y `lookup_precio` la toma aunque el médico no tenga la
especialidad (o no tenga ninguna). Fuera de un par sin restricción, una NE sin
especialidad no es válida (`validar_reglas_origen`) ni cotiza.

Los datos se siembran en la sesión con `flush` y NUNCA se commitean: el fixture
`db` cierra la sesión sin commit y MySQL descarta todo. La OS es un número que no
existe, para no cruzarse con ninguna variante real del código.
"""
import datetime
from decimal import Decimal

import pytest

from app.db.models.nomenclador_cmc import Valor
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import validar_reglas_origen

FECHA = datetime.date(2026, 9, 1)
VIGENCIA = datetime.date(2026, 7, 1)
NOMENCLADOR_ID = 897           # 030801 — cualquier código real sirve
OS_SINTETICA = 990_001         # no existe: ninguna variante real del código acá
MEDICO_ORL = 135               # NRO_ESPECIALIDAD=36
MEDICO_SIN_ESPECIALIDAD = 2358  # NRO_ESPECIALIDAD=0


async def _sembrar_ne_sin_especialidad(db, sin_restriccion: bool) -> Valor:
    nom = await db.get(service.NomencladorCMC, NOMENCLADOR_ID)
    valor = Valor(
        obra_social_nro=OS_SINTETICA, nomenclador_id=NOMENCLADOR_ID, origen="NE",
        codigo=nom.codigo, descripcion="PRUEBA NE SIN ESPECIALIDAD",
        especialidad_id_colegio=None, sin_restriccion_especialidad=sin_restriccion,
        vigencia_desde=VIGENCIA, estado="activo",
    )
    return await service.persistir_valor(
        db, valor,
        [
            dict(concepto="Honorarios", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("24000.00"), orden=0),
            dict(concepto="Gastos", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=1),
            dict(concepto="Ayudante", galeno_id=None, cantidad=Decimal("0"),
                 valor_unitario=Decimal("0"), orden=2),
        ],
        motivo="carga_inicial", fecha_corte=None,
    )


def test_reglas_origen_ne_sin_especialidad_solo_si_sin_restriccion():
    with pytest.raises(ValueError):
        validar_reglas_origen("NE", None, False, False)
    validar_reglas_origen("NE", None, False, False, sin_restriccion=True)
    with pytest.raises(ValueError):
        validar_reglas_origen("NN", 7, False, True, sin_restriccion=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("medico_id", [MEDICO_ORL, MEDICO_SIN_ESPECIALIDAD])
async def test_ne_sin_especialidad_cotiza_para_cualquier_medico(db, medico_id):
    await _sembrar_ne_sin_especialidad(db, sin_restriccion=True)
    r = await service.lookup_precio(
        nomenclador_id=NOMENCLADOR_ID, obra_social_nro=OS_SINTETICA, fecha=FECHA,
        medico_id=medico_id, db=db,
    )
    assert r.origen == "NE"
    assert r.variante_especialidad_id is None
    assert r.precio_total == 24_000


@pytest.mark.asyncio
async def test_ne_sin_especialidad_fuera_de_sin_restriccion_no_cotiza(db):
    await _sembrar_ne_sin_especialidad(db, sin_restriccion=False)
    with pytest.raises(service.LookupError):
        await service.lookup_precio(
            nomenclador_id=NOMENCLADOR_ID, obra_social_nro=OS_SINTETICA, fecha=FECHA,
            medico_id=MEDICO_ORL, db=db,
        )


@pytest.mark.asyncio
async def test_no_se_apaga_sin_restriccion_con_ne_sin_especialidad(db):
    valor = await _sembrar_ne_sin_especialidad(db, sin_restriccion=True)
    with pytest.raises(ValueError):
        await service.fijar_sin_restriccion_par(db, OS_SINTETICA, valor.codigo, False)
