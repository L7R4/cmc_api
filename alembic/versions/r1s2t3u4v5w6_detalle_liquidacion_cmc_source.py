"""detalle_liquidacion_cmc_source

Revision ID: r1s2t3u4v5w6
Revises: o5p6q7r8s9t0
Create Date: 2026-05-03

Agrega soporte para poblar DetalleLiquidacion desde detalle_facturacion (CMC):
- Quita FK de prestacion_id a guardar_atencion (la hace nullable sin FK)
- Agrega columna fuente ENUM('ga','cmc') con default 'ga'
- Agrega cmc_detalle_id para guardar el id_detalle_prestaciones del CMC
- Agrega columnas de enriquecimiento para registros CMC
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'r1s2t3u4v5w6'
down_revision = 'o5p6q7r8s9t0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Eliminar la unique constraint que incluye prestacion_id (necesario antes de cambiar el tipo)
    op.drop_constraint("uq_det_prest_en_liq", "detalle_liquidacion", type_="unique")

    # 2. Eliminar la FK de prestacion_id → guardar_atencion.ID
    #    Nota: el nombre exacto del constraint en MySQL puede variar; si falla,
    #    inspeccionar con SHOW CREATE TABLE detalle_liquidacion y ajustar.
    try:
        op.drop_constraint("detalle_liquidacion_ibfk_1", "detalle_liquidacion", type_="foreignkey")
    except Exception:
        # Intentar nombre alternativo generado por SQLAlchemy
        try:
            op.drop_constraint("fk_detliq_prestacion", "detalle_liquidacion", type_="foreignkey")
        except Exception:
            pass  # Si ya no existe, continuar

    # 3. Hacer prestacion_id nullable (sin FK)
    op.alter_column(
        "detalle_liquidacion",
        "prestacion_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # 4. Agregar columna fuente
    op.add_column(
        "detalle_liquidacion",
        sa.Column(
            "fuente",
            sa.Enum("ga", "cmc", name="detliq_fuente"),
            nullable=False,
            server_default="ga",
        ),
    )

    # 5. Agregar cmc_detalle_id
    op.add_column(
        "detalle_liquidacion",
        sa.Column("cmc_detalle_id", sa.Integer(), nullable=True),
    )
    op.create_index("idx_detliq_cmc_id", "detalle_liquidacion", ["cmc_detalle_id"])

    # 6. Agregar columnas de enriquecimiento para registros CMC
    op.add_column("detalle_liquidacion", sa.Column("fecha_practica", sa.Date(), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("codigo_prestacion_cmc", sa.String(20), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("nro_orden_cmc", sa.String(30), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("paciente_nombre_cmc", sa.String(100), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("paciente_nro_afiliado_cmc", sa.String(30), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("sesion_cmc", sa.Integer(), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("cantidad_cmc", sa.Integer(), nullable=True))
    op.add_column("detalle_liquidacion", sa.Column("porcentaje_cmc", sa.Integer(), nullable=True))

    # 7. Recrear unique constraint (ahora prestacion_id puede ser NULL para CMC)
    op.create_unique_constraint(
        "uq_det_prest_en_liq",
        "detalle_liquidacion",
        ["prestacion_id", "liquidacion_id", "medico_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_detliq_cmc_id", "detalle_liquidacion")
    op.drop_constraint("uq_det_prest_en_liq", "detalle_liquidacion", type_="unique")

    op.drop_column("detalle_liquidacion", "porcentaje_cmc")
    op.drop_column("detalle_liquidacion", "cantidad_cmc")
    op.drop_column("detalle_liquidacion", "sesion_cmc")
    op.drop_column("detalle_liquidacion", "paciente_nro_afiliado_cmc")
    op.drop_column("detalle_liquidacion", "paciente_nombre_cmc")
    op.drop_column("detalle_liquidacion", "nro_orden_cmc")
    op.drop_column("detalle_liquidacion", "codigo_prestacion_cmc")
    op.drop_column("detalle_liquidacion", "fecha_practica")
    op.drop_column("detalle_liquidacion", "cmc_detalle_id")
    op.drop_column("detalle_liquidacion", "fuente")

    op.alter_column(
        "detalle_liquidacion",
        "prestacion_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.create_foreign_key(
        "detalle_liquidacion_ibfk_1",
        "detalle_liquidacion",
        "guardar_atencion",
        ["prestacion_id"],
        ["ID"],
        ondelete="RESTRICT",
    )

    op.create_unique_constraint(
        "uq_det_prest_en_liq",
        "detalle_liquidacion",
        ["prestacion_id", "liquidacion_id", "medico_id"],
    )
