from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session
from typing import Sequence, Union


revision: str = 'a20b0a0e1941'
down_revision: Union[str, Sequence[str], None] = '367ab16f66a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE_ID_DEFAULT = 2  # el que pediste

def _detect_table_name(bind, candidates: tuple[str, ...]) -> str:
    insp = sa.inspect(bind)
    names = {n.lower(): n for n in insp.get_table_names()}
    for c in candidates:
        if c.lower() in names:
            return names[c.lower()]
    raise RuntimeError(f"No encontré ninguna de {candidates!r}")

def upgrade():
    bind = op.get_bind()
    session = Session(bind=bind)
    ctx = op.get_context()

    # Detectar nombres reales (sensibles a mayúsculas en Linux)
    t_medicos = _detect_table_name(bind, ("listado_medico", "ListadoMedico"))
    t_user_role = _detect_table_name(bind, ("user_role", "UserRole", "user_roles"))

    # Inserta (user_id, role_id=1) sólo cuando NO exista ya ese par para ese user
    sql = sa.text(f"""
        INSERT INTO {t_user_role} (user_id, role_id)
        SELECT m.ID, :rid
        FROM {t_medicos} AS m
        LEFT JOIN {t_user_role} AS ur
               ON ur.user_id = m.ID AND ur.role_id = :rid
        WHERE ur.user_id IS NULL
    """)

    res = session.execute(sql, {"rid": ROLE_ID_DEFAULT})
    # En MySQL res.rowcount devuelve insertados; en algunos drivers puede ser -1
    inserted = res.rowcount if (res.rowcount is not None and res.rowcount >= 0) else 0
    session.commit()
    ctx.impl.static_output(f"[user_role] insertados con role_id={ROLE_ID_DEFAULT}: {inserted}")

def downgrade():
    bind = op.get_bind()
    session = Session(bind=bind)

    t_medicos = _detect_table_name(bind, ("listado_medico", "ListadoMedico"))
    t_user_role = _detect_table_name(bind, ("user_role", "UserRole", "user_roles"))

    # Borra únicamente los vínculos (user_id, role_id=1) que insertamos
    sql = sa.text(f"""
        DELETE ur FROM {t_user_role} ur
        WHERE ur.role_id = :rid
          AND ur.user_id IN (SELECT ID FROM {t_medicos})
    """)
    session.execute(sql, {"rid": ROLE_ID_DEFAULT})
    session.commit()

