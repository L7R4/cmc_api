# Migración — `debitos`/`debito_detalles` → `lote_ajuste` (sin_factura)

> Fecha: 2026-04-04 (actualizado 2026-04-05 — campo `monto` reemplazado por `honorarios` + `gastos`)
> Fuente: `colegio_orig` — contenedor `renzo_mysql` (phpMyAdmin: red `db_renzo`)
> Destino: `coleg185_anexo` — contenedor `mysql` (phpMyAdmin: red `cmc_api`)

---

## Flujo de trabajo

```
[SCRIPT 1]  →  ejecutar en colegio_orig
               crea tabla _mig_debitos_staging

[phpMyAdmin renzo]   →  Exportar tabla _mig_debitos_staging (formato SQL)
[phpMyAdmin cmc_api] →  Importar en coleg185_anexo

[SCRIPT 2]  →  ejecutar en coleg185_anexo
               diagnóstico: revisar matches, montos, duplicados

[SCRIPT 3]  →  ejecutar en coleg185_anexo (solo si diagnóstico es OK)
               crea el lote sin_factura y vuelca los ajustes
```

---

## Estructura de tablas relevadas

### `colegio_orig.facturaciones`

- `id` PK
- `obra_social_id` FK → `obra_sociales.id`
- `periodo_id` FK → `periodos.id` — el período no está como mes/anio directo sino como FK a `periodos`
- Es la tabla que vincula un período+OS con sus débitos. **El parámetro de OS es el `codigo` de `obra_sociales`, no el `id`.**

### `colegio_orig.periodos`

- `id` PK
- `mes` int
- `anio` int
- Se usa para resolver el período: dado `@p_mes` y `@p_anio` se obtiene el `id` y se filtra en `facturaciones.periodo_id`

### `colegio_orig.debitos`

- `facturacion_id` FK → `facturaciones.id` — **el período y OS se obtienen desde acá, no desde `debitos` directamente**
- `grupo_id` int — **filtrar `grupo_id = 1`**
- `estado_id`: 2 = activo/procesado (mayoría), 4 y 5 = histórico, 1 = vacío
- `deleted` datetime — NULL = vigente

### `colegio_orig.debito_detalles`

- `debito_id` FK → `debitos.id`
- `socio_id` FK → `socios.id`
- `grupo_id` int — **filtrar `grupo_id = 1`** para traer solo los del grupo correcto
- `tipo_movimiento` varchar(1): `'D'` débito / `'C'` crédito
- `honorarios` decimal — monto principal
- `gastos` decimal — complemento (0 en el 99.97% de los registros)
- `deleted` datetime — NULL = vigente

### `colegio_orig.obra_sociales`

- `codigo` int — **coincide exactamente con `NRO_OBRASOCIAL` de `coleg185_anexo`**

### `colegio_orig.socios`

- `nro_socio` int → match con `coleg185_anexo.listado_medico.NRO_SOCIO` (prioritario)
- `matricula` int → fallback con `coleg185_anexo.listado_medico.MATRICULA_PROV`

---

## SCRIPT 1 — Staging en `colegio_orig`

> Ejecutar en phpMyAdmin apuntando a **`colegio_orig`**

```sql
-- ══════════════════════════════════════════════════════════════════
-- PARÁMETROS — modificar antes de ejecutar
-- ══════════════════════════════════════════════════════════════════
SET @p_os_codigo = 411;    -- codigo de obra_social (campo 'codigo' de obra_sociales)
SET @p_mes       = 2;     -- mes del período  (1-12)
SET @p_anio      = 2025;   -- año del período

-- ══════════════════════════════════════════════════════════════════
-- Crear tabla de staging
-- ══════════════════════════════════════════════════════════════════
DROP TABLE IF EXISTS _mig_debitos_staging;

CREATE TABLE _mig_debitos_staging AS
SELECT
    dd.id                                                       AS ext_id,
    dd.debito_id                                                AS legacy_debito_id,
    d.estado_id                                                 AS legacy_debito_estado,

    -- Tipo de movimiento: normalizar a minúscula
    LOWER(dd.tipo_movimiento)                                   AS tipo,

    -- Montos discriminados tal como están en la tabla origen
    dd.honorarios                                               AS honorarios,
    COALESCE(dd.gastos, 0.00)                                   AS gastos,

    -- Observación como referencia del registro de origen
    CONCAT(
        'Orden:', COALESCE(dd.nro_orden, '-'),
        ' Prest:', COALESCE(dd.nomenclador_codigo, '-')
    )                                                           AS observacion,

    -- Obra social: guardar el codigo (= NRO_OBRASOCIAL en coleg185_anexo)
    os.codigo                                                   AS obra_social_id,

    -- Período (viene de periodos a través de facturaciones)
    p.mes                                                       AS mes_periodo,
    p.anio                                                      AS anio_periodo,

    -- Médico legacy: se exportan nro_socio y matricula para que
    -- el match se haga en coleg185_anexo contra listado_medico
    s.nro_socio                                                 AS legacy_nro_socio,
    s.matricula                                                 AS legacy_matricula,
    s.apellido_nombre                                           AS legacy_nombre

FROM debito_detalles dd

JOIN debitos d
    ON d.id = dd.debito_id

JOIN facturaciones f
    ON f.id = d.facturacion_id

JOIN periodos p
    ON p.id = f.periodo_id

JOIN obra_sociales os
    ON os.id = f.obra_social_id

JOIN socios s
    ON s.id = dd.socio_id

WHERE
    os.codigo       = @p_os_codigo
    AND p.mes       = @p_mes
    AND p.anio      = @p_anio
    AND d.grupo_id  = 1
    AND dd.grupo_id = 1
    AND d.deleted   IS NULL
    AND dd.deleted  IS NULL
    AND (dd.honorarios > 0 OR COALESCE(dd.gastos, 0) > 0)
;

-- Resumen rápido para confirmar que el staging se generó bien
SELECT
    obra_social_id,
    mes_periodo,
    anio_periodo,
    COUNT(*)                                                                        AS total_filas,
    SUM(CASE WHEN tipo = 'd' THEN 1 ELSE 0 END)                                    AS cant_debitos,
    SUM(CASE WHEN tipo = 'c' THEN 1 ELSE 0 END)                                    AS cant_creditos,
    ROUND(SUM(CASE WHEN tipo = 'd' THEN honorarios + gastos ELSE 0 END), 2)        AS suma_debitos,
    ROUND(SUM(CASE WHEN tipo = 'c' THEN honorarios + gastos ELSE 0 END), 2)        AS suma_creditos,
    COUNT(DISTINCT legacy_nro_socio)                                                AS medicos_unicos
FROM _mig_debitos_staging
GROUP BY obra_social_id, mes_periodo, anio_periodo;
```

**Después de ejecutar:** exportar la tabla `_mig_debitos_staging` desde phpMyAdmin de `renzo_mysql` (Exportar → formato SQL, sin estructura si ya existe en destino, o con estructura para crearla).

---

## SCRIPT 2 — Diagnóstico en `coleg185_anexo`

> Ejecutar en phpMyAdmin apuntando a **`coleg185_anexo`**, después de importar la tabla

```sql
-- ── 2.1 Resumen general ────────────────────────────────────────────────────
SELECT
    obra_social_id,
    mes_periodo,
    anio_periodo,
    COUNT(*)                                                                        AS total_filas,
    SUM(CASE WHEN tipo = 'd' THEN 1 ELSE 0 END)                                    AS cant_debitos,
    SUM(CASE WHEN tipo = 'c' THEN 1 ELSE 0 END)                                    AS cant_creditos,
    ROUND(SUM(CASE WHEN tipo = 'd' THEN honorarios + gastos ELSE 0 END), 2)        AS suma_debitos,
    ROUND(SUM(CASE WHEN tipo = 'c' THEN honorarios + gastos ELSE 0 END), 2)        AS suma_creditos
FROM _mig_debitos_staging
GROUP BY obra_social_id, mes_periodo, anio_periodo;


-- ── 2.2 Match de médicos ───────────────────────────────────────────────────
SELECT
    CASE
        WHEN lm_nro.ID IS NOT NULL THEN 'nro_socio'
        WHEN lm_mat.ID IS NOT NULL THEN 'matricula_prov'
        ELSE 'sin_match'
    END                                             AS criterio,
    COUNT(*)                                        AS cantidad,
    ROUND(SUM(s.honorarios + s.gastos), 2)          AS total
FROM _mig_debitos_staging s
LEFT JOIN listado_medico lm_nro
    ON lm_nro.NRO_SOCIO = s.legacy_nro_socio
LEFT JOIN listado_medico lm_mat
    ON lm_mat.MATRICULA_PROV = s.legacy_matricula
   AND lm_nro.ID IS NULL
GROUP BY criterio
ORDER BY cantidad DESC;


-- ── 2.3 Detalle de registros SIN match (serán ignorados en volcado) ────────
SELECT
    s.ext_id,
    s.legacy_nro_socio,
    s.legacy_matricula,
    s.legacy_nombre,
    s.tipo,
    ROUND(s.honorarios, 2)          AS honorarios,
    ROUND(s.gastos, 2)              AS gastos,
    ROUND(s.honorarios + s.gastos, 2) AS total,
    s.observacion
FROM _mig_debitos_staging s
LEFT JOIN listado_medico lm_nro
    ON lm_nro.NRO_SOCIO = s.legacy_nro_socio
LEFT JOIN listado_medico lm_mat
    ON lm_mat.MATRICULA_PROV = s.legacy_matricula
   AND lm_nro.ID IS NULL
WHERE lm_nro.ID IS NULL AND lm_mat.ID IS NULL
ORDER BY (s.honorarios + s.gastos) DESC;


-- ── 2.4 Verificar que la obra social existe en coleg185_anexo ─────────────
SELECT
    s.obra_social_id,
    os.OBRA_SOCIAL
FROM _mig_debitos_staging s
LEFT JOIN obras_sociales os ON os.NRO_OBRASOCIAL = s.obra_social_id
GROUP BY s.obra_social_id, os.OBRA_SOCIAL;
-- Si OBRA_SOCIAL sale NULL → el codigo no existe en obras_sociales y el volcado fallará por FK


-- ── 2.5 Detección de corrida previa (duplicados por ext_id) ───────────────
SELECT
    s.ext_id,
    s.legacy_nombre,
    s.tipo,
    ROUND(s.honorarios, 2)              AS honorarios,
    ROUND(s.gastos, 2)                  AS gastos,
    ROUND(s.honorarios + s.gastos, 2)   AS total
FROM _mig_debitos_staging s
JOIN ajuste a ON a.ext_id = s.ext_id;
-- Si devuelve filas → ya existe un volcado previo para estos datos.
-- NO ejecutar el Script 3 en ese caso (ya están cargados).


-- ── 2.6 Desglose por estado del debito de origen ──────────────────────────
SELECT
    legacy_debito_estado,
    COUNT(*)                                    AS cantidad,
    ROUND(SUM(honorarios + gastos), 2)          AS total
FROM _mig_debitos_staging
GROUP BY legacy_debito_estado
ORDER BY cantidad DESC;
```

**Criterio para continuar:** la query 2.4 debe mostrar la obra social con nombre (no NULL), y la query 2.5 no debe devolver filas. Los sin-match de 2.3 son aceptables siempre que el monto ignorado sea razonable.

---

## SCRIPT 3 — Volcado definitivo en `coleg185_anexo`

> Ejecutar en phpMyAdmin apuntando a **`coleg185_anexo`**, solo si el diagnóstico es OK

```sql
-- ══════════════════════════════════════════════════════════════════
-- 3.1 Crear el lote sin_factura
-- ══════════════════════════════════════════════════════════════════
INSERT INTO lote_ajuste
    (obra_social_id, mes_periodo, anio_periodo, tipo, estado, total_debitos, total_creditos)
SELECT
    obra_social_id,
    mes_periodo,
    anio_periodo,
    'sin_factura',
    'A',
    0,
    0
FROM _mig_debitos_staging
LIMIT 1;

SET @lote_id = LAST_INSERT_ID();
SELECT CONCAT('Lote creado con id = ', @lote_id) AS resultado;


-- ══════════════════════════════════════════════════════════════════
-- 3.2 Insertar ajustes (solo registros con match de médico)
-- ══════════════════════════════════════════════════════════════════
INSERT IGNORE INTO ajuste
    (lote_id, tipo, id_atencion, medico_id, obra_social_id, honorarios, gastos, observacion, origen, ext_id)
SELECT
    @lote_id,
    s.tipo,
    NULL,                                   -- sin_factura: siempre NULL
    COALESCE(lm_nro.ID, lm_mat.ID),        -- medico_id resuelto
    s.obra_social_id,
    s.honorarios,
    s.gastos,
    s.observacion,
    'importado',
    s.ext_id
FROM _mig_debitos_staging s
LEFT JOIN listado_medico lm_nro
    ON lm_nro.NRO_SOCIO = s.legacy_nro_socio
LEFT JOIN listado_medico lm_mat
    ON lm_mat.MATRICULA_PROV = s.legacy_matricula
   AND lm_nro.ID IS NULL
WHERE COALESCE(lm_nro.ID, lm_mat.ID) IS NOT NULL;  -- ignorar sin match

SELECT CONCAT('Ajustes insertados: ', ROW_COUNT()) AS resultado;


-- ══════════════════════════════════════════════════════════════════
-- 3.3 Recalcular totales del lote
-- ══════════════════════════════════════════════════════════════════
UPDATE lote_ajuste
SET
    total_debitos  = (
        SELECT COALESCE(SUM(honorarios + gastos), 0)
        FROM ajuste
        WHERE lote_id = @lote_id AND tipo = 'd'
    ),
    total_creditos = (
        SELECT COALESCE(SUM(honorarios + gastos), 0)
        FROM ajuste
        WHERE lote_id = @lote_id AND tipo = 'c'
    )
WHERE id = @lote_id;


-- ══════════════════════════════════════════════════════════════════
-- 3.4 Verificación final
-- ══════════════════════════════════════════════════════════════════
SELECT
    l.id,
    l.tipo,
    l.estado,
    os.OBRA_SOCIAL,
    l.mes_periodo,
    l.anio_periodo,
    l.total_debitos,
    l.total_creditos,
    COUNT(a.id)                                         AS cant_ajustes,
    SUM(CASE WHEN a.tipo = 'd' THEN 1 ELSE 0 END)       AS cant_d,
    SUM(CASE WHEN a.tipo = 'c' THEN 1 ELSE 0 END)       AS cant_c
FROM lote_ajuste l
LEFT JOIN ajuste a          ON a.lote_id = l.id
LEFT JOIN obras_sociales os ON os.NRO_OBRASOCIAL = l.obra_social_id
WHERE l.id = @lote_id
GROUP BY l.id, l.tipo, l.estado, os.OBRA_SOCIAL, l.mes_periodo, l.anio_periodo, l.total_debitos, l.total_creditos;


-- ══════════════════════════════════════════════════════════════════
-- 3.5 Limpiar staging (ejecutar cuando ya no se necesite)
-- ══════════════════════════════════════════════════════════════════
-- DROP TABLE IF EXISTS _mig_debitos_staging;
```

---

## Notas

| Tema                          | Detalle                                                                                                                                                                           |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Honorarios / Gastos**       | El staging exporta `honorarios` y `gastos` por separado. La columna `total` = honorarios + gastos se computa siempre on-the-fly; no se almacena.                                  |
| **Filtro de staging**         | Se incluyen filas donde `honorarios > 0 OR gastos > 0` y `grupo_id = 1`. El período y la obra social se resuelven a través de `facturaciones`, no desde campos de `debitos`. |
| **Idempotencia**              | `INSERT IGNORE` + constraint `UNIQUE (ext_id)` en `ajuste` previene duplicados si se importa y corre Script 3 dos veces. Sí creará un lote nuevo vacío — la query 2.5 lo detecta. |
| **Estado del lote**           | Queda en `'A'` (Abierto). Desde la UI se puede revisar, cerrar (`'C'`) y asignar a un pago (`'L'`).                                                                               |
| **Sin match de médico**       | Los registros ignorados quedan visibles en la query 2.3. Si necesitás recuperarlos, se pueden agregar manualmente desde la UI del lote.                                           |
| **Obra social no encontrada** | La query 2.4 lo detecta antes del volcado. Si el `codigo` no existe en `obras_sociales` de `coleg185_anexo`, el INSERT fallará por FK.                                            |
