"""Alta de código repetido: 409 con mensaje útil, no 500 genérico.

Regresión de audit_log id 134733 (prod, 2026-09-14): un POST /api/nomenclador/ con
codigo='320101' — ya existente en el catálogo compartido — chocaba contra
`uq_nm_nomenclador_codigo_os`. El IntegrityError subía como SQLAlchemyError y el
handler global lo devolvía como 500 "Error al acceder a la base de datos", sin
decirle al operador que el número ya estaba tomado.

Pruebas puras: la sesión es un doble que solo responde el SELECT del chequeo.
"""
import pytest
from fastapi import HTTPException

from app.modules.nomenclador.routes_nomenclador import create_nomenclador
from app.modules.nomenclador.schemas import NomencladorCreate


class _Resultado:
    def __init__(self, fila):
        self._fila = fila

    def scalar_one_or_none(self):
        return self._fila


class _SesionFalsa:
    """Devuelve `fila` en el SELECT del chequeo y registra si se intentó insertar."""

    def __init__(self, fila=None):
        self._fila = fila
        self.agregados = []

    async def execute(self, _stmt):
        return _Resultado(self._fila)

    def add(self, obj):
        self.agregados.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


class _FilaExistente:
    id = 3747
    descripcion = "ATENCION PREMATURO HASTA 1500 GRS."


def _body(**extra):
    datos = {"codigo": "320101", "descripcion": "Consulta nueva Pediatria"}
    datos.update(extra)
    return NomencladorCreate(**datos)


@pytest.mark.asyncio
async def test_codigo_compartido_repetido_da_409_y_no_inserta():
    db = _SesionFalsa(_FilaExistente())
    with pytest.raises(HTTPException) as exc:
        await create_nomenclador(_body(), db)
    assert exc.value.status_code == 409
    assert "320101" in exc.value.detail
    assert "3747" in exc.value.detail
    assert "catálogo compartido" in exc.value.detail
    assert db.agregados == []  # nunca llegó al INSERT


@pytest.mark.asyncio
async def test_repetido_dentro_de_una_obra_social_nombra_la_os():
    db = _SesionFalsa(_FilaExistente())
    with pytest.raises(HTTPException) as exc:
        await create_nomenclador(_body(obra_social_nro=81), db)
    assert exc.value.status_code == 409
    assert "OS 81" in exc.value.detail


@pytest.mark.asyncio
async def test_codigo_libre_se_inserta():
    db = _SesionFalsa(None)
    await create_nomenclador(_body(codigo="999901"), db)
    assert len(db.agregados) == 1
    assert db.agregados[0].codigo == "999901"


class _SesionQueChoca(_SesionFalsa):
    """Pasa el chequeo previo (no hay fila) pero el commit choca con la unique key.

    Simula la carrera: otra petición tomó el número entre el SELECT y el commit.
    """

    async def commit(self):
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("INSERT ...", {}, Exception("Duplicate entry"))

    async def rollback(self):
        self.revertido = True


@pytest.mark.asyncio
async def test_carrera_en_el_commit_tambien_da_409_y_revierte():
    db = _SesionQueChoca(None)
    with pytest.raises(HTTPException) as exc:
        await create_nomenclador(_body(), db)
    assert exc.value.status_code == 409
    assert "320101" in exc.value.detail
    assert getattr(db, "revertido", False) is True
