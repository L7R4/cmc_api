# alembic/versions/367ab16f66a5_backfill_conceps_y_password.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
import json

# === Identificadores de Alembic ===
revision = "367ab16f66a5"
down_revision = "7728154b4c66"
branch_labels = None
depends_on = None

def _detect_table_name(bind) -> str:
    insp = sa.inspect(bind)
    names = {n.lower(): n for n in insp.get_table_names()}
    # Probables nombres según tu proyecto
    for candidate in ("listado_medico", "ListadoMedico"):
        if candidate.lower() in names:
            return names[candidate.lower()]
    raise RuntimeError("No encontré la tabla de médicos (listado_medico / ListadoMedico)")

def _get_hasher():
    # 1) intentá usar tu función oficial
    try:
        from app.core.passwords import hash_password  # type: ignore
        return hash_password
    except Exception:
        pass
    # 2) fallback a passlib[bcrypt]
    try:
        from passlib.hash import bcrypt  # type: ignore
        def _hash(p: str) -> str:
            return bcrypt.hash(p)
        return _hash
    except Exception as e:
        raise RuntimeError(
            "No pude importar app.core.passwords.hash_password ni passlib[bcrypt]. "
            "Instalá passlib o corregí PYTHONPATH en alembic/env.py."
        ) from e

def upgrade():
    bind = op.get_bind()
    session = Session(bind=bind)
    ctx = op.get_context()

    table = _detect_table_name(bind)
    hash_password = _get_hasher()

    default_json = json.dumps({"espec": [], "conceps": []}, ensure_ascii=False)

    # --- Backfill conceps_espec ---
    # 1) Nulls
    upd1 = session.execute(
        sa.text(f"""
            UPDATE {table}
            SET conceps_espec = :val
            WHERE conceps_espec IS NULL
        """),
        {"val": default_json},
    )
    # 2) Vacíos o no-válidos (según versión de MySQL/MariaDB)
    try:
        upd2 = session.execute(
            sa.text(f"""
                UPDATE {table}
                SET conceps_espec = :val
                WHERE conceps_espec = ''
                   OR JSON_VALID(conceps_espec) = 0
            """),
            {"val": default_json},
        )
        touched_invalid = upd2.rowcount or 0
    except Exception:
        # fallback si no existe JSON_VALID
        upd2 = session.execute(
            sa.text(f"""
                UPDATE {table}
                SET conceps_espec = :val
                WHERE conceps_espec = ''
            """),
            {"val": default_json},
        )
        touched_invalid = upd2.rowcount or 0

    touched_nulls = upd1.rowcount or 0
    ctx.impl.static_output(f"[conceps_espec] NULL→{touched_nulls}, vacíos/invalid→{touched_invalid}")

    # --- Backfill hashed_password = hash(MATRICULA_PROV) ---
    rows = session.execute(
        sa.text(f"""
            SELECT ID, MATRICULA_PROV
            FROM {table}
            WHERE (hashed_password IS NULL OR TRIM(COALESCE(hashed_password,'')) = '')
              AND TRIM(COALESCE(MATRICULA_PROV,'')) <> ''
        """)
    ).fetchall()

    updated_pw = 0
    for (med_id, matricula) in rows:
        plain = str(matricula).strip()
        hp = hash_password(plain)
        session.execute(
            sa.text(f"UPDATE {table} SET hashed_password = :hp WHERE ID = :id"),
            {"hp": hp, "id": med_id},
        )
        updated_pw += 1

    session.commit()
    ctx.impl.static_output(f"[hashed_password] actualizados: {updated_pw}")

def downgrade():
    # Revertir sólo el JSON (opcional); no deshasheamos passwords.
    bind = op.get_bind()
    session = Session(bind=bind)
    table = _detect_table_name(bind)

    # Si querés revertir el JSON por NULL cuando esté exactamente al default
    try:
        session.execute(
            sa.text(f"""
                UPDATE {table}
                SET conceps_espec = NULL
                WHERE JSON_EXTRACT(conceps_espec, '$.espec') = JSON_ARRAY()
                  AND JSON_EXTRACT(conceps_espec, '$.conceps') = JSON_ARRAY()
            """)
        )
    except Exception:
        # fallback: no-op si tu versión no soporta JSON_EXTRACT
        pass
    session.commit()
