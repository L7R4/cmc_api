"""La vigencia de un precio / galeno / valor siempre la elige quien lo carga: ningún pedido que cambie
precios puede traerla opcional ni con un valor por defecto (antes `alta_ne_cero` asumía 01/01/2026).

Son chequeos de contrato sobre los schemas y las rutas: no tocan la base.
"""
import datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.main import app
from app.modules.nomenclador import schemas as sch

# Cada pedido de escritura del nomenclador que lleva `vigencia_desde`.
PEDIDOS_CON_VIGENCIA = [
    sch.GalenoCreate,
    sch.GalenoActualizarPrecioIn,
    sch.GalenoCrearNivelesIn,
    sch.GalenoActualizarPrecioMasivoIn,
    sch.GalenosPrecioLoteIn,
    sch.GalenoActualizarUnidadesIn,
    sch.GalenosImportarIn,
    sch.GalenoImportarLoteIn,
    sch.AltaNECeroIn,
    sch.ValorCreate,
    sch.ValorCreateMulti,
    sch.ValorCerrarYCrearIn,
    sch.ReplicaAltaIn,
    sch.ReplicarFamiliaGalenoIn,
    sch.ActualizarPorcentajeIn,
    sch.ActualizarPorCodigosIn,
    sch.GenerarValoresNNIn,
    sch.ReplicarEstructuraIn,
    sch.ReplicarObrasSocialesIn,
    sch.RecalcularNiveladoIn,
    sch.AplicarNiveladoIn,
    sch.ImportarFijosIn,
]


@pytest.mark.parametrize("modelo", PEDIDOS_CON_VIGENCIA, ids=lambda m: m.__name__)
def test_el_pedido_exige_vigencia_sin_default(modelo):
    campo = modelo.model_fields["vigencia_desde"]
    assert campo.is_required(), f"{modelo.__name__}.vigencia_desde no puede tener default"
    # Tampoco acepta null: `Optional[date]` sin default seguiría siendo «requerido».
    with pytest.raises(ValidationError):
        modelo.model_validate({"vigencia_desde": None})


def test_alta_ne_cero_rechaza_vigencia_ausente_o_vacia():
    base = {"codigo": "420101", "obra_social_nros": [1]}
    sch.AltaNECeroIn(**base, vigencia_desde=datetime.date(2026, 11, 1))
    for malo in ({}, {"vigencia_desde": ""}, {"vigencia_desde": None}):
        with pytest.raises(ValidationError):
            sch.AltaNECeroIn(**base, **malo)


def test_replica_de_precios_en_lote_exige_vigencia():
    pedido = {
        "origen_obra_social_nro": 1, "destinos": [2], "operacion": "precios",
        "items": [{"codigo": "G1", "nuevo_valor_unitario": 10}],
    }
    with pytest.raises(ValidationError):
        sch.ReplicarFamiliaGalenosLoteIn(**pedido)
    sch.ReplicarFamiliaGalenosLoteIn(**pedido, vigencia_desde=datetime.date(2026, 11, 1))
    # La visibilidad no es un cambio de precio: no lleva vigencia.
    sch.ReplicarFamiliaGalenosLoteIn(
        origen_obra_social_nro=1, destinos=[2], operacion="visibilidad",
        items=[{"codigo": "G1", "visible": True}],
    )


def _escrituras_con_vigencia():
    """(ruta, parámetro) de toda ruta que escribe y recibe `vigencia_desde` suelto (query o form)."""
    for ruta in app.routes:
        metodos = getattr(ruta, "methods", None) or set()
        dependant = getattr(ruta, "dependant", None)
        if dependant is None or not metodos & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        for p in [*dependant.query_params, *dependant.body_params]:
            if p.name == "vigencia_desde":
                yield f"{sorted(metodos)} {ruta.path}", p


def test_las_rutas_de_escritura_con_vigencia_suelta_la_exigen():
    encontradas = list(_escrituras_con_vigencia())
    assert encontradas, "no se encontró ninguna ruta: el chequeo dejó de mirar"
    for donde, p in encontradas:
        assert p.field_info.is_required(), f"{donde}: vigencia_desde tiene que ser obligatoria"


def test_las_rutas_con_pedido_json_usan_modelos_que_exigen_vigencia():
    """Cualquier modelo del cuerpo de una ruta de escritura que tenga `vigencia_desde` no la deja opcional,
    salvo los de reportes/habilitaciones de médico, que no son precios."""
    no_son_precio = {"MedicoHabilitacionCreate", "MedicoHabilitacionUpdate", "ReplicarFamiliaGalenosLoteIn"}
    for ruta in app.routes:
        metodos = getattr(ruta, "methods", None) or set()
        dependant = getattr(ruta, "dependant", None)
        if dependant is None or not metodos & {"POST", "PUT", "PATCH"}:
            continue
        for p in dependant.body_params:
            modelo = getattr(p.field_info, "annotation", None)
            if not (isinstance(modelo, type) and issubclass(modelo, BaseModel)):
                continue
            campo = modelo.model_fields.get("vigencia_desde")
            if campo is None or modelo.__name__ in no_son_precio:
                continue
            assert campo.is_required(), f"{ruta.path}: {modelo.__name__}.vigencia_desde es opcional"
