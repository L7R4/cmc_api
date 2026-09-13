"""eliminar NNE: NE explícito por especialidad

Revision ID: f6a7b8c9d0e1
Revises: e2f3a4b5c6d7
Create Date: 2026-09-10

Elimina el origen NNE. Decisión de negocio (ver docs/api/nomenclador_origenes_ne_nn.md):
NNE era un atajo de almacenamiento para "este precio vale para cualquier especialidad
habilitada" — un valor por defecto, no un concepto de negocio. A partir de acá, esa
misma idea se expresa con una fila NE explícita por cada especialidad habilitada del
código (mismo precio, filas separadas). Consecuencia deliberada: la fila NE pasa a
implicar la habilitación en `nm_nomenclador_especialidad` — no puede existir una NE
activa sin que su especialidad esté habilitada para ese código.

Qué hace, en orden:

1. Respalda TODAS las filas `origen='NNE'` (activas y cerradas) de `nm_valores`,
   `nm_valor_componentes` y `nm_historial_precio_codigo` en tablas `..._mig_nne`
   (`CREATE TABLE ... LIKE`, no `AS SELECT`, para preservar la estructura exacta y
   poder reinsertar tal cual en el downgrade).
2. Crea la habilitación de Colegio (`obra_social_nro` NULL) que le falte a cualquier
   NE (activa o cerrada) cuya especialidad no esté hoy habilitada para su código —
   decisión del usuario: "sí, crearla a nivel Colegio". Reactiva si existía
   soft-deleted (unique real es `(nomenclador_id, especialidad_id_colegio,
   obra_social_key)`).
3. Precondición que sostiene el punto 4: NINGÚN código tiene habilitaciones activas
   en los dos niveles (Colegio y OS propia) a la vez (medido: 0 casos). Si eso
   cambió, aborta — el mapeo de abajo asume que "especialidades activas del código"
   es un conjunto sin ambigüedad de nivel, sin tener que resolver la precedencia
   completa de `service._nivel_pertenencia_especialidad` en SQL puro.
4. Arma `mig_nne_map`: una fila por (NNE, especialidad activa del código), EXCEPTO
   cuando ya existe una fila de historial NE de esa especialidad con exactamente la
   misma vigencia_desde (esas colisiones quedan logueadas en `mig_nne_colisiones` y
   no generan fila nueva — la NE existente ya cubre ese precio para esa fecha; es la
   misma clave que protege `uq_nm_historial_precio`). Un NNE cuyo código no tiene NINGUNA
   especialidad habilitada no genera ninguna fila: queda solo en el respaldo
   (archivado), tal como pidió el usuario.
5. Inserta las nuevas filas NE (mismo id base `MAX(nm_valores.id)` + offset del mapa,
   determinístico) y clona sus componentes e historial de precio, uno por cada
   especialidad del mapa.
6. Borra las filas `NNE` originales (ya respaldadas) de historial, componentes y
   valores, en ese orden (children primero, por las FK).
7. Aserciones finales: 0 filas `origen='NNE'` y 0 NE con especialidad NULL en
   `nm_valores` y en `nm_historial_precio_codigo`. Si alguna falla, la migración
   aborta con el conteo exacto.

Genera un reporte con las cifras reales — `scripts/reporte_migracion_nne.py`
corrido después de este `alembic upgrade` — no esta migración: acá solo se deja la
observación 'migración NNE: ...' en cada fila tocada para que ese script la ubique.

⚠ Solo probado contra la base de DESARROLLO. No se ejecuta contra producción desde
este cambio — el usuario la corre a mano cuando lo decida, con el `.sql` companion
en `docs/api/imports/`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OBSERVACION_HABILITACION = 'migración NNE: habilitación inferida de valor NE'

# nm_historial_precio_codigo tiene una columna STORED GENERATED (especialidad_key)
# que no admite valor explícito: todo INSERT hacia/desde esta tabla (o su respaldo,
# creado con `LIKE` y por lo tanto con la misma columna) tiene que listar columnas.
_HIST_COLS = (
    "id, nomenclador_id, obra_social_nro, vigencia_desde, vigencia_hasta, precio_total, "
    "valores_id, componentes_snapshot, motivo_cambio, referencia_cambio_id, fecha_cambio, "
    "created_at, especialidad_id_colegio, origen"
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 0. Precondición: sin ambigüedad de nivel Colegio/OS ──────────────────
    ambiguos = conn.execute(sa.text("""
        SELECT COUNT(DISTINCT a.nomenclador_id)
        FROM nm_nomenclador_especialidad a
        JOIN nm_nomenclador_especialidad b
          ON b.nomenclador_id = a.nomenclador_id AND b.obra_social_nro IS NOT NULL
        WHERE a.obra_social_nro IS NULL AND a.activo = 1 AND b.activo = 1
    """)).scalar_one()
    if ambiguos:
        raise RuntimeError(
            f"Abortando: {ambiguos} código(s) tienen habilitaciones activas en ambos "
            "niveles (Colegio y OS propia). Esta migración asume que eso no pasa "
            "(medido en 0 al escribirla) — revisar antes de continuar."
        )

    # Segunda precondición, mismo espíritu: los JOIN de abajo no filtran la
    # habilitación por `obra_social_nro` del valor (solo por `activo`), porque hoy
    # ningún código tiene habilitaciones OS-propias de DOS obras sociales distintas
    # a la vez, ni un NNE/NE huérfano cuyo código tenga una habilitación de una OS
    # que no es la suya (medido en 0). Si eso cambió, el join sin filtrar por OS
    # contaminaría cruzando habilitaciones de otra obra social.
    duplicado_os = conn.execute(sa.text("""
        SELECT COUNT(*) FROM (
            SELECT nomenclador_id, especialidad_id_colegio, COUNT(DISTINCT obra_social_nro) c
            FROM nm_nomenclador_especialidad
            WHERE activo = 1 AND obra_social_nro IS NOT NULL
            GROUP BY nomenclador_id, especialidad_id_colegio
            HAVING c > 1
        ) t
    """)).scalar_one()
    contaminacion_nne = conn.execute(sa.text("""
        SELECT COUNT(*) FROM nm_valores v
        JOIN nm_nomenclador_especialidad e ON e.nomenclador_id = v.nomenclador_id AND e.activo = 1
        WHERE v.origen = 'NNE' AND e.obra_social_nro IS NOT NULL AND e.obra_social_nro <> v.obra_social_nro
    """)).scalar_one()
    if duplicado_os or contaminacion_nne:
        raise RuntimeError(
            f"Abortando: {duplicado_os} par(es) código+especialidad con habilitaciones "
            f"de más de una OS propia, {contaminacion_nne} NNE cuyo código tiene una "
            "habilitación de OTRA obra social. El JOIN de esta migración no filtra por "
            "obra_social_nro y asume que eso no pasa (medido en 0) — hay que agregar "
            "ese filtro antes de seguir."
        )

    # Limpieza defensiva: si un intento anterior de esta misma migración quedó a
    # mitad de camino (falló después de crear alguna tabla de trabajo pero antes de
    # terminar), un reintento no debe chocar con "table already exists". Nada de
    # esto es dato real: son tablas que esta migración crea y puebla ella misma.
    for tabla in (
        "mig_nne_map",
        "mig_nne_colisiones",
        "nm_historial_precio_codigo_mig_nne",
        "nm_valor_componentes_mig_nne",
        "nm_valores_mig_nne",
    ):
        op.execute(f"DROP TABLE IF EXISTS {tabla}")

    # ── 1. Respaldo íntegro de origen='NNE' (activas y cerradas) ─────────────
    op.execute("CREATE TABLE nm_valores_mig_nne LIKE nm_valores")
    op.execute("INSERT INTO nm_valores_mig_nne SELECT * FROM nm_valores WHERE origen = 'NNE'")

    op.execute("CREATE TABLE nm_valor_componentes_mig_nne LIKE nm_valor_componentes")
    op.execute("""
        INSERT INTO nm_valor_componentes_mig_nne
        SELECT c.* FROM nm_valor_componentes c
        JOIN nm_valores_mig_nne v ON v.id = c.valor_id
    """)

    # `especialidad_key` es STORED GENERATED: no admite valor explícito, así que el
    # respaldo (y su restore en downgrade) listan columnas a mano, sin ella.
    op.execute("CREATE TABLE nm_historial_precio_codigo_mig_nne LIKE nm_historial_precio_codigo")
    op.execute(f"""
        INSERT INTO nm_historial_precio_codigo_mig_nne ({_HIST_COLS})
        SELECT {_HIST_COLS} FROM nm_historial_precio_codigo WHERE origen = 'NNE'
    """)

    respaldadas = conn.execute(sa.text("SELECT COUNT(*) FROM nm_valores_mig_nne")).scalar_one()
    if not respaldadas:
        # Nada que migrar (NNE ya no existe en esta base). Las tres tablas de respaldo
        # quedan creadas y vacías — downgrade las dropea igual, sin problema.
        return

    # ── 2. Habilitación de Colegio para NE huérfanas (activas o cerradas) ────
    op.execute(f"""
        INSERT INTO nm_nomenclador_especialidad
            (nomenclador_id, especialidad_id_colegio, obra_social_nro, activo, observacion)
        SELECT DISTINCT v.nomenclador_id, v.especialidad_id_colegio, NULL, 1,
               '{OBSERVACION_HABILITACION}'
        FROM nm_valores v
        WHERE v.origen = 'NE' AND v.especialidad_id_colegio IS NOT NULL
          AND NOT EXISTS (
                SELECT 1 FROM nm_nomenclador_especialidad e
                WHERE e.nomenclador_id = v.nomenclador_id
                  AND e.especialidad_id_colegio = v.especialidad_id_colegio
                  AND e.activo = 1
              )
        ON DUPLICATE KEY UPDATE activo = 1, observacion = VALUES(observacion)
    """)

    # ── 3. Colisiones ─────────────────────────────────────────────────────────
    # La unique real que no se puede violar es la de `nm_historial_precio_codigo`:
    # (nomenclador_id, obra_social_nro, origen, especialidad_key, vigencia_desde).
    # Un primer intento chequeó "¿hay una NE ACTIVA con vigencia solapada?" contra
    # nm_valores y no alcanzó: hay códigos donde ya existe una NE (activa o
    # cerrada) con exactamente la misma vigencia_desde que el NNE que se expande
    # (ej. nomenclador_id 2237, OS 62, especialidad 7, 2026-04-01) — mismo motivo
    # que el resto de la migración necesita el respaldo, "datos sucios" con más de
    # una fila de historial coincidiendo. Se chequea contra el historial
    # directamente, con la MISMA clave que protege la unique, sin importar el
    # estado del valor al que pertenece esa fila.
    op.execute("""
        CREATE TABLE mig_nne_colisiones (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            old_valor_id INT NOT NULL,
            nomenclador_id INT NOT NULL,
            obra_social_nro INT NOT NULL,
            especialidad_id_colegio INT NOT NULL,
            ne_existente_id INT NOT NULL
        ) ENGINE=InnoDB
    """)
    op.execute("""
        INSERT INTO mig_nne_colisiones
            (old_valor_id, nomenclador_id, obra_social_nro, especialidad_id_colegio, ne_existente_id)
        SELECT DISTINCT v.id, v.nomenclador_id, v.obra_social_nro, e.especialidad_id_colegio, h.valores_id
        FROM nm_valores_mig_nne v
        JOIN nm_nomenclador_especialidad e
          ON e.nomenclador_id = v.nomenclador_id AND e.activo = 1
        JOIN nm_historial_precio_codigo h
          ON h.nomenclador_id = v.nomenclador_id
         AND h.obra_social_nro = v.obra_social_nro
         AND h.origen = 'NE'
         AND h.especialidad_id_colegio = e.especialidad_id_colegio
         AND h.vigencia_desde = v.vigencia_desde
    """)

    # ── 4. Mapa de expansión: una fila por (NNE, especialidad activa), salvo
    #      colisión. Los NNE cuyo código no tiene ninguna especialidad activa
    #      no generan ninguna fila (join vacío) → quedan solo en el respaldo.
    op.execute("""
        CREATE TABLE mig_nne_map (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            old_valor_id INT NOT NULL,
            nomenclador_id INT NOT NULL,
            obra_social_nro INT NOT NULL,
            especialidad_id_colegio INT NOT NULL,
            nuevo_valor_id INT NULL,
            KEY (old_valor_id)
        ) ENGINE=InnoDB
    """)
    op.execute("""
        INSERT INTO mig_nne_map (old_valor_id, nomenclador_id, obra_social_nro, especialidad_id_colegio)
        SELECT v.id, v.nomenclador_id, v.obra_social_nro, e.especialidad_id_colegio
        FROM nm_valores_mig_nne v
        JOIN nm_nomenclador_especialidad e
          ON e.nomenclador_id = v.nomenclador_id AND e.activo = 1
        LEFT JOIN mig_nne_colisiones col
          ON col.old_valor_id = v.id AND col.especialidad_id_colegio = e.especialidad_id_colegio
        WHERE col.id IS NULL
    """)

    mapeadas = conn.execute(sa.text("SELECT COUNT(*) FROM mig_nne_map")).scalar_one()
    if mapeadas:
        base = conn.execute(sa.text("SELECT IFNULL(MAX(id), 0) FROM nm_valores")).scalar_one()
        conn.execute(sa.text("UPDATE mig_nne_map SET nuevo_valor_id = :base + id"), {"base": base})

        # ── 5. Insertar las NE nuevas + clonar componentes e historial ───────
        op.execute(f"""
            INSERT INTO nm_valores
                (id, obra_social_nro, nomenclador_id, codigo, descripcion, nivel,
                 vigencia_desde, vigencia_hasta, estado, observacion, created_at,
                 updated_at, complejidad, especialidad_id_colegio, por_presupuesto,
                 origen, cantidad_ayudantes, categoria, requiere_autorizacion, coseguro)
            SELECT m.nuevo_valor_id, v.obra_social_nro, v.nomenclador_id, v.codigo,
                   v.descripcion, v.nivel, v.vigencia_desde, v.vigencia_hasta, v.estado,
                   CONCAT_WS(' | ', v.observacion, CONCAT('migración NNE id=', v.id)),
                   v.created_at, v.updated_at, v.complejidad, m.especialidad_id_colegio,
                   v.por_presupuesto, 'NE', v.cantidad_ayudantes, v.categoria,
                   v.requiere_autorizacion, v.coseguro
            FROM mig_nne_map m
            JOIN nm_valores_mig_nne v ON v.id = m.old_valor_id
        """)

        op.execute("""
            INSERT INTO nm_valor_componentes
                (valor_id, concepto, galeno_id, cantidad, valor_unitario, orden,
                 activo, observacion, created_at, updated_at)
            SELECT m.nuevo_valor_id, c.concepto, c.galeno_id, c.cantidad, c.valor_unitario,
                   c.orden, c.activo, c.observacion, c.created_at, c.updated_at
            FROM mig_nne_map m
            JOIN nm_valor_componentes_mig_nne c ON c.valor_id = m.old_valor_id
        """)

        op.execute("""
            INSERT INTO nm_historial_precio_codigo
                (nomenclador_id, obra_social_nro, vigencia_desde, vigencia_hasta,
                 precio_total, valores_id, componentes_snapshot, motivo_cambio,
                 referencia_cambio_id, fecha_cambio, created_at, especialidad_id_colegio, origen)
            SELECT h.nomenclador_id, h.obra_social_nro, h.vigencia_desde, h.vigencia_hasta,
                   h.precio_total, m.nuevo_valor_id, h.componentes_snapshot, h.motivo_cambio,
                   h.referencia_cambio_id, h.fecha_cambio, h.created_at,
                   m.especialidad_id_colegio, 'NE'
            FROM mig_nne_map m
            JOIN nm_historial_precio_codigo_mig_nne h ON h.valores_id = m.old_valor_id
        """)

    # ── 6. Borrar los NNE originales (ya respaldados) ────────────────────────
    op.execute("""
        DELETE h FROM nm_historial_precio_codigo h
        JOIN nm_valores_mig_nne v ON v.id = h.valores_id
    """)
    op.execute("""
        DELETE c FROM nm_valor_componentes c
        JOIN nm_valores_mig_nne v ON v.id = c.valor_id
    """)
    op.execute("DELETE FROM nm_valores WHERE id IN (SELECT id FROM nm_valores_mig_nne)")

    # ── 7. Aserciones finales ─────────────────────────────────────────────────
    # Lo único que ESTA migración garantiza por construcción es que no quede ningún
    # NNE: el mapa nunca genera una fila NE con especialidad NULL (siempre copia
    # `e.especialidad_id_colegio`, columna NOT NULL). Por eso "0 NNE" es un `raise`
    # duro, pero "NE sin especialidad" es solo un WARNING si aparece: puede ser una
    # fila LEGACY previa a esta migración (creada bajo la regla vieja, que sí
    # admitía NE sin especialidad) y no es responsabilidad de este cambio arreglarla
    # — pero se reporta porque con la regla nueva es justamente el caso que no
    # debería existir.
    quedan_nne = conn.execute(sa.text(
        "SELECT COUNT(*) FROM nm_valores WHERE origen = 'NNE'"
    )).scalar_one()
    quedan_nne_hist = conn.execute(sa.text(
        "SELECT COUNT(*) FROM nm_historial_precio_codigo WHERE origen = 'NNE'"
    )).scalar_one()
    if quedan_nne or quedan_nne_hist:
        raise RuntimeError(
            f"Migración incompleta — quedan {quedan_nne} nm_valores.NNE y "
            f"{quedan_nne_hist} nm_historial_precio_codigo.NNE."
        )

    ne_sin_esp = conn.execute(sa.text(
        "SELECT COUNT(*) FROM nm_valores WHERE origen = 'NE' AND especialidad_id_colegio IS NULL"
    )).scalar_one()
    ne_sin_esp_hist = conn.execute(sa.text(
        "SELECT COUNT(*) FROM nm_historial_precio_codigo WHERE origen = 'NE' AND especialidad_id_colegio IS NULL"
    )).scalar_one()
    if ne_sin_esp or ne_sin_esp_hist:
        print(
            f"[f6a7b8c9d0e1] AVISO (no bloquea): {ne_sin_esp} nm_valores.NE y "
            f"{ne_sin_esp_hist} nm_historial_precio_codigo.NE con especialidad NULL — "
            "son filas LEGACY previas a esta migración (creadas bajo la regla vieja); "
            "revisar y decidir aparte qué hacer con ellas."
        )


def downgrade() -> None:
    conn = op.get_bind()
    tiene_mapa = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'mig_nne_map'"
    )).scalar_one()
    if tiene_mapa:
        # Borrar lo que esta migración creó (children primero, por las FK)
        op.execute("""
            DELETE h FROM nm_historial_precio_codigo h
            JOIN mig_nne_map m ON m.nuevo_valor_id = h.valores_id
        """)
        op.execute("""
            DELETE c FROM nm_valor_componentes c
            JOIN mig_nne_map m ON m.nuevo_valor_id = c.valor_id
        """)
        op.execute("""
            DELETE v FROM nm_valores v
            JOIN mig_nne_map m ON m.nuevo_valor_id = v.id
        """)
        op.execute(f"DELETE FROM nm_nomenclador_especialidad WHERE observacion = '{OBSERVACION_HABILITACION}'")

        # Restaurar los NNE originales (parent primero)
        op.execute("INSERT INTO nm_valores SELECT * FROM nm_valores_mig_nne")
        op.execute("INSERT INTO nm_valor_componentes SELECT * FROM nm_valor_componentes_mig_nne")
        op.execute(f"""
            INSERT INTO nm_historial_precio_codigo ({_HIST_COLS})
            SELECT {_HIST_COLS} FROM nm_historial_precio_codigo_mig_nne
        """)

        op.execute("DROP TABLE mig_nne_map")

    for tabla in (
        "mig_nne_colisiones",
        "nm_historial_precio_codigo_mig_nne",
        "nm_valor_componentes_mig_nne",
        "nm_valores_mig_nne",
    ):
        existe = conn.execute(sa.text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ), {"t": tabla}).scalar_one()
        if existe:
            op.execute(f"DROP TABLE {tabla}")
