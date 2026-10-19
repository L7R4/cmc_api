"""Cuaderno de tareas: es personal, nadie ve las de otro.

Tests sin base de datos: verifican las garantías que no dependen de datos. El
aislamiento por usuario lo da el WHERE medico_id = <uid del token> de
app/modules/tareas/service.py; acá se fija que la ruta no admita elegir otro
usuario y que la auditoría no copie lo que cada uno escribe.
"""
import inspect

import pytest
from pydantic import ValidationError

from app.auth.authz import SCOPES_POR_RUTA, SOLO_AUTENTICADO
from app.middleware.audit import _SIN_CONTENIDO_PREFIXES
from app.modules.tareas import routes, service
from app.modules.tareas.schemas import ImportarIn, TareaCreate, TareaUpdate


def _rutas_tareas():
    return {k: v for k, v in SCOPES_POR_RUTA.items() if k[1].startswith("/api/tareas")}


def test_rutas_declaradas_y_solo_autenticado():
    rutas = _rutas_tareas()
    assert len(rutas) == 6
    assert all(v is SOLO_AUTENTICADO for v in rutas.values())


def test_ninguna_ruta_recibe_un_usuario_por_parametro():
    # El dueño sale del token: ningún handler declara un parámetro de usuario.
    for nombre, fn in inspect.getmembers(routes, inspect.iscoroutinefunction):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"medico_id", "usuario_id", "user_id", "uid"}, nombre


def test_el_servicio_filtra_siempre_por_dueño():
    # Toda función que busca una tarea por id exige el dueño como argumento.
    for nombre in ("obtener_o_404", "actualizar", "quitar", "limpiar_hechas", "listar", "importar", "crear"):
        assert "medico_id" in inspect.signature(getattr(service, nombre)).parameters, nombre


def test_la_auditoria_no_copia_el_contenido():
    assert "/api/tareas" in _SIN_CONTENIDO_PREFIXES


def test_texto_se_recorta_y_no_puede_quedar_vacio():
    assert TareaCreate(texto="  llamar a OSDE  ").texto == "llamar a OSDE"
    with pytest.raises(ValidationError):
        TareaCreate(texto="   ")
    with pytest.raises(ValidationError):
        TareaCreate(texto="x" * 141)
    assert TareaUpdate().texto is None


def test_prioridad_invalida_se_rechaza():
    with pytest.raises(ValidationError):
        TareaCreate(texto="algo", prioridad="urgente")


def test_importar_tiene_tope():
    ok = [{"texto": f"t{i}"} for i in range(30)]
    assert len(ImportarIn(tareas=ok).tareas) == 30
    with pytest.raises(ValidationError):
        ImportarIn(tareas=ok + [{"texto": "una más"}])
