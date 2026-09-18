"""lote_ajuste_multiples_refacturaciones

Revision ID: b8c9d5e6f7a8
Revises: a7b8c9d5e6f7
Create Date: 2026-09-14

Saca la UniqueConstraint uq_lote_origen(snap_origen_id) de lote_ajuste. Esa
constraint limitaba a UNA sola refacturación por lote origen, contradiciendo
el propio docstring de crear_lote_refacturacion ("sin restricción de
cantidad") y el negocio real: una obra social puede corregir el mismo lote
más de una vez (ver diagnóstico M6).

MySQL no deja borrar uq_lote_origen directamente: su índice es el único que
soporta la FK snap_origen_id -> lote_ajuste.id. Antes de borrarla hay que
crear un índice plano sobre snap_origen_id — que además es lo que ya declara
el modelo ORM (index=True en LoteAjuste.snap_origen_id); una migración previa
(p2q3r4s5t6u7) lo había borrado dejando solo el de la unique constraint.
"""
from alembic import op


revision = 'b8c9d5e6f7a8'
down_revision = 'a7b8c9d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_lote_ajuste_snap_origen_id", "lote_ajuste", ["snap_origen_id"])
    op.drop_constraint("uq_lote_origen", "lote_ajuste", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_lote_origen", "lote_ajuste", ["snap_origen_id"])
    op.drop_index("ix_lote_ajuste_snap_origen_id", table_name="lote_ajuste")
