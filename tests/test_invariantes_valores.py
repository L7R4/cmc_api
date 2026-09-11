"""Invariantes post-migración "eliminar NNE" (alembic f6a7b8c9d0e1).

Solo lectura, corre contra la base ya migrada (dev). No crea ni borra nada — sigue
la misma convención que el resto de `tests/` (ver conftest.py).
"""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_no_quedan_valores_nne(db):
    r = await db.execute(text("SELECT COUNT(*) FROM nm_valores WHERE origen = 'NNE'"))
    assert r.scalar_one() == 0


@pytest.mark.asyncio
async def test_no_queda_historial_nne(db):
    r = await db.execute(text("SELECT COUNT(*) FROM nm_historial_precio_codigo WHERE origen = 'NNE'"))
    assert r.scalar_one() == 0


@pytest.mark.asyncio
async def test_toda_ne_expandida_tiene_habilitacion(db):
    """Las NE que esta migración generó (están en mig_nne_map) tienen que apuntar a
    una especialidad que sigue activa en nm_nomenclador_especialidad."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM mig_nne_map m
        JOIN nm_valores v ON v.id = m.nuevo_valor_id
        LEFT JOIN nm_nomenclador_especialidad e
          ON e.nomenclador_id = v.nomenclador_id
         AND e.especialidad_id_colegio = v.especialidad_id_colegio
         AND e.activo = 1
        WHERE e.id IS NULL
    """))
    assert r.scalar_one() == 0


@pytest.mark.asyncio
async def test_toda_ne_expandida_tiene_componentes(db):
    """Componentes SIEMPRE se clonan 1:1 desde el valor original — a diferencia del
    historial (ver test siguiente), acá no hay excepción legítima."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM mig_nne_map m
        LEFT JOIN nm_valor_componentes c ON c.valor_id = m.nuevo_valor_id
        WHERE c.id IS NULL
    """))
    assert r.scalar_one() == 0, "hay NE expandidas sin ningún componente"


@pytest.mark.asyncio
async def test_ne_sin_historial_es_solo_la_heredada_del_original(db):
    """Una NE expandida puede legítimamente no tener historial: si el NNE original
    tampoco lo tenía (dato legacy migrado sin historial — no es un problema que esta
    migración haya creado, lo hereda tal cual). Lo que NO puede pasar es que la NE
    quede sin historial mientras el NNE que la originó sí lo tenía: eso sí sería un
    fallo del clonado."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM mig_nne_map m
        LEFT JOIN nm_historial_precio_codigo h_nueva ON h_nueva.valores_id = m.nuevo_valor_id
        JOIN nm_historial_precio_codigo_mig_nne h_original ON h_original.valores_id = m.old_valor_id
        WHERE h_nueva.id IS NULL
    """))
    assert r.scalar_one() == 0, "el NNE original tenía historial y la NE expandida no lo heredó"


@pytest.mark.asyncio
async def test_archivadas_no_expandidas_por_causa_conocida(db):
    """Una NNE queda archivada (sin fila en mig_nne_map) por exactamente dos motivos
    excluyentes: (a) su código no tiene NINGUNA especialidad activa, o (b) tiene
    especialidades activas pero TODAS colisionan con una NE ya existente (logueado en
    mig_nne_colisiones). No puede quedar archivada por ningún otro motivo — eso
    delataría una especialidad candidata que ni se expandió ni se logueó como
    colisión."""
    r = await db.execute(text("""
        SELECT COUNT(*) FROM nm_valores_mig_nne v
        JOIN nm_nomenclador_especialidad e ON e.nomenclador_id = v.nomenclador_id AND e.activo = 1
        LEFT JOIN mig_nne_map m
          ON m.old_valor_id = v.id AND m.especialidad_id_colegio = e.especialidad_id_colegio
        LEFT JOIN mig_nne_colisiones col
          ON col.old_valor_id = v.id AND col.especialidad_id_colegio = e.especialidad_id_colegio
        WHERE m.id IS NULL AND col.id IS NULL
    """))
    assert r.scalar_one() == 0, "hay una especialidad candidata que no se expandió ni se logueó como colisión"


@pytest.mark.asyncio
async def test_respaldo_nne_completo(db):
    """Todo lo borrado de nm_valores está en el respaldo (para el downgrade y el
    reporte de códigos archivados)."""
    r = await db.execute(text("SELECT COUNT(*) FROM nm_valores_mig_nne"))
    respaldadas = r.scalar_one()
    assert respaldadas > 0

    r = await db.execute(text("""
        SELECT COUNT(*) FROM nm_valores_mig_nne v
        LEFT JOIN nm_valores actual ON actual.id = v.id
        WHERE actual.id IS NOT NULL
    """))
    assert r.scalar_one() == 0, "un id respaldado sigue vivo en nm_valores (debería haberse borrado)"
