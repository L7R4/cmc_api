"""Presets por vista: el exportable sale como se ve, así que se guardan las opciones de la vista."""
import pytest
from pydantic import ValidationError

from app.modules.facturacion.export.schemas import PresetIn


def test_preset_de_vista_guarda_solo_las_claves_de_la_vista():
    p = PresetIn(nombre="Por tipo", tipo_documento="vista", opciones={
        "orden": "fecha_carga", "direccion": "desc", "agrupacion": "por_tipo", "agruparEquipo": False,
        "columnas": ["fecha", "codigo"], "tipos": ["Consulta"], "basura": 1,
    })
    assert p.opciones["agrupacion"] == "por_tipo" and p.opciones["agruparEquipo"] is False
    assert "basura" not in p.opciones


def test_preset_de_detalle_historico_sigue_validandose():
    p = PresetIn(nombre="viejo", tipo_documento="detalle", opciones={"orden": "importe_desc", "agrupacion": "plana"})
    assert p.opciones["agrupacion"] == "plana" and p.opciones["direccion"] == "asc"
    with pytest.raises(ValidationError):
        PresetIn(nombre="roto", tipo_documento="detalle", opciones={"orden": "inexistente"})
