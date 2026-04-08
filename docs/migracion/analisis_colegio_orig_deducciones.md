# Análisis — DB `colegio_orig`: Dominio de Deducciones y Servicios

> Fecha: 2026-03-23
> Base analizada: `colegio_orig` en contenedor `renzo_mysql` (red `db_renzo_renzo_net`)
> Credenciales: `root / root`
> Propósito: entender qué hay que migrar al nuevo sistema para poblar `descuentos`, `socio_descuento`, `deduccion_programa` y `deducciones`.

---

## 1. Mapa conceptual del dominio

El sistema legacy maneja dos conceptos que en el nuevo sistema convergen en **"descuentos"**:

```
LEGACY                          NUEVO SISTEMA
──────────────────────────────────────────────────────
conceptos (catálogo base)    →  descuentos.nombre / precio / porcentaje
servicios (definición)       →  descuentos (tabla unificada)
servicio_grupos              →  _(no existe equivalente directo, usar nombre del grupo)_
socio_servicios              →  socio_descuento (qué médico tiene qué descuento)
socio_servicio_detalles      →  deduccion_programa (cuota mensual generada, con estado)
deducciones                  →  deducciones (snapshot aplicado en liquidación)
export_descuentos            →  _(tabla de exportación — NO migrar, es derivada)_
```

---

## 2. Tablas involucradas — descripción y rol

### 2.1 `servicios` — El catálogo de descuentos

Es el equivalente directo de `descuentos` en el nuevo sistema. Define **qué se cobra** y **cómo se calcula**.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | PK | ID del servicio legacy |
| `nombre` | text | Nombre del concepto (ej: "Cuota Societaria") |
| `precio` | decimal(10,2) | Monto fijo. `0` si es porcentual |
| `porcentaje` | decimal(10,2) | Porcentaje. `0` si es fijo |
| `es_porcentual` | int(1) | `1` = se calcula como % de la liquidación |
| `basado_en_liquidacion` | int(1) | `1` = el base de cálculo es el total liquidado al médico |
| `recursivo` | int(1) | `1` = se genera automáticamente mes a mes |
| `concepto_id` | FK → `conceptos.id` | Clasificación contable del servicio |
| `servicio_grupo_id` | FK → `servicio_grupos.id` | Grupo al que pertenece (Malapraxis, Legal, etc.) |
| `deleted` | datetime | Soft delete — `NULL` = activo |

**Hay 50 servicios activos** (sin `deleted`).

#### Tipos de cálculo identificados:

| Tipo | Ejemplo | Mapeo nuevo sistema |
|------|---------|---------------------|
| Precio fijo mensual | Cuota Societaria $10.000 | `descuentos.precio` |
| Porcentaje sobre liquidación | Contribución s/honorarios 7% | `descuentos.porcentaje` |
| Precio fijo no recursivo | Pileta(invitados) | `descuentos.precio` + `aplica_a_todos=0` |
| Sin precio ni porcentaje | ASPROSAC | Precio $0 (histórico / sin uso) |

---

### 2.2 `servicio_grupos` — Agrupación de servicios

Tabla simple de 5 grupos:

| id | nombre |
|----|--------|
| 1 | Ammeco |
| 2 | Aport. Jub. |
| 3 | Legal |
| 4 | Malapraxis |
| 5 | Sociedades |

No tiene equivalente directo en el nuevo sistema. Se puede incorporar al `nombre` del descuento o agregar una columna `grupo` en `descuentos` si se necesita para filtros en UI.

---

### 2.3 `conceptos` — Clasificación contable

Catálogo de conceptos contables. El campo `es_deduccion = 1` indica que ese concepto genera un débito en el recibo del médico. Prácticamente todos son `es_deduccion = 1`.

**Rol en migración:** es información de clasificación, no es necesario migrarla como tabla separada. El `concepto_id` sirve para mapear `servicios → descuentos`.

---

### 2.4 `socio_servicios` — Asignación de servicio a médico

Equivalente de `socio_descuento` en el nuevo sistema. Registra **qué médico tiene asignado qué servicio**.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | PK | — |
| `servicio_id` | FK → `servicios.id` | Qué servicio |
| `socio_id` | FK → `socios.id` | A qué médico |
| `socio_modelo` | varchar | Siempre `"Socios"` en la práctica |
| `mes` / `anio` | int | Período de inicio (puede ser NULL para servicios permanentes) |
| `monto` | decimal | Monto asignado (puede diferir del precio base del servicio) |
| `saldo` | decimal | Saldo pendiente del cargo cabecera |
| `estado_id` | int | `1` = activo, `0` = baja |
| `liquidacion_id` | FK → `liquidaciones.id` | Liquidación donde se cobró |
| `paga_por_caja` | int | `1` = se paga en caja, no se descuenta de liquidación |
| `grupo` | JSON | Metadata de cuotas si es un pago en cuotas |

**Hay 7.591 registros activos** (sin `deleted`). **2.053 médicos distintos** tienen al menos un servicio asignado.

---

### 2.5 `socio_servicio_detalles` — Cuotas generadas mes a mes

**Es la tabla más importante para la migración.** Equivale a `deduccion_programa` en el nuevo sistema: cada fila es una cuota mensual para un médico+servicio.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | PK | — |
| `servicio_id` | FK → `servicios.id` | Qué servicio |
| `socio_servicio_id` | FK → `socio_servicios.id` | Cabecera de asignación |
| `socio_id` | FK → `socios.id` | Médico |
| `mes` / `anio` | int | Período de esta cuota |
| `monto` | decimal | Monto original de la cuota |
| `saldo` | decimal | Saldo pendiente (0 = pagado) |
| `pre_saldo` | decimal | Saldo antes del último pago |
| `estado_id` | int | `1` = pendiente, `2` = cobrado |
| `liquidacion_id` | FK → `liquidaciones.id` | Liquidación donde se incluyó |
| `cerro_liquidacion_id` | FK → `liquidaciones.id` | Liquidación donde se cerró/pagó |
| `ultimo` | int(1) | `1` = es la cuota más reciente del servicio para ese médico |
| `paga_por_caja` | int | `1` = no va a liquidación, se cobra en caja |
| `ammeco_encabezado_id` | FK → `ammeco_encabezados.id` | Solo para servicios AMMECO |

**Hay 131.556 registros activos.** Distribución de estado:
- `estado_id = 1` (pendiente): **25.550**
- `estado_id = 2` (cobrado): **106.006**

**Saldo pendiente total: $93.325.631,91** en 768 médicos distintos con deuda.

Pago vía liquidación vs caja:
- `paga_por_caja = 0` (liquida): **129.953** registros
- `paga_por_caja = 1` (caja): **1.603** registros

---

### 2.6 `deducciones` — Snapshot aplicado en liquidación

Equivalente de `deducciones` en el nuevo sistema. Cada fila registra lo que se descontó a un médico en una liquidación.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | PK | — |
| `socio_id` | FK → `socios.id` | Médico |
| `socio_modelo` | varchar | Siempre `"Socios"` |
| `mes` / `anio` | int | Período |
| `concepto_id` | FK → `conceptos.id` | Clasificación contable |
| `tipo_movimiento` | varchar(1) | `"D"` = débito |
| `adeudado` | decimal | Monto original calculado |
| `cobrado` | decimal | Monto efectivamente cobrado |
| `saldo` | decimal | Remanente sin cobrar |
| `estado_id` | int | `1` = pendiente, `2` = cobrado |
| `liquidacion_id` | FK → `liquidaciones.id` | Liquidación de origen |
| `tabla_relacionada` | varchar | `"SocioServicioDetalles"` o `NULL` |
| `tabla_relacionada_id` | int | FK al ID del `socio_servicio_detalle` que lo originó |

**258.801 registros totales.** Distribución:
- `estado_id = 1` (pendiente): **8.467** — saldo pendiente total: **$81.151.890,38**
- `estado_id = 2` (cobrado): **250.334**

**Vínculo:** `tabla_relacionada = 'SocioServicioDetalles'` en 133.527 registros → indica qué `socio_servicio_detalle` originó ese descuento. 125.274 no tienen vínculo (son deducciones de porcentaje sobre liquidación).

---

### 2.7 `export_descuentos` — Tabla derivada de exportación

**NO migrar.** Es una tabla de resumen/exportación generada por el proceso de liquidación. Contiene:
- `ssd_id` = ID del `socio_servicio_detalle`
- `nro_socio`, `anio`, `mes`, `monto`, `saldo`
- `servicio_nro_colegio` = número de colegio (siempre 400 en los datos)

Tiene 125.051 registros pero es datos derivados, no fuente de verdad.

---

### 2.8 `liquidaciones` — Períodos de liquidación

Equivalente de `Pago` en el nuevo sistema.

| Campo | Descripción |
|-------|-------------|
| `id` | PK |
| `mes` / `anio` | Período |
| `nro_liquidacion` | Número correlativo |
| `estado_id` | `1` = abierta, `2` = cerrada |
| `calculo_deducciones` | `2` = deducciones calculadas |
| `proceso_id` | FK → `procesos.id` |

**94 liquidaciones en total.** La más reciente: liquidación 94, mes 3, año 2026.

---

## 3. Relaciones técnicas entre tablas

```
servicios ──────────────────┐
    │                       │
    │ 1:N                   │ 1:N
    ▼                       ▼
socio_servicios         conceptos
    │
    │ 1:N
    ▼
socio_servicio_detalles ──── liquidaciones
    │                            │
    │ referenciado por           │ 1:N
    ▼                            ▼
deducciones ◄──────── liquidacion_detalles
```

**Flujo de datos en el sistema legacy:**

```
1. Se define el servicio en `servicios`
2. Se asigna a un médico en `socio_servicios`
3. Cada mes, el proceso genera una cuota en `socio_servicio_detalles`
4. Al procesar la liquidación, la cuota pasa a `deducciones` (snapshot)
5. `deducciones.tabla_relacionada_id` apunta al `socio_servicio_detalle` de origen
6. `socio_servicio_detalles.liquidacion_id` apunta a la liquidación donde se cobró
```

---

## 4. Mapeo legacy → nuevo sistema

### 4.1 Tabla `descuentos` (nuevo)

**Fuente:** `servicios` JOIN `servicio_grupos`

```sql
INSERT INTO descuentos (nro_colegio, nombre, precio, porcentaje)
SELECT
    400 AS nro_colegio,
    s.nombre,
    s.precio,
    s.porcentaje
FROM servicios s
WHERE s.deleted IS NULL
ORDER BY s.id;
```

**Campos a mapear:**

| Legacy (`servicios`) | Nuevo (`descuentos`) | Notas |
|----------------------|---------------------|-------|
| `nombre` | `nombre` | Directo |
| `precio` | `precio` | Directo |
| `porcentaje` | `porcentaje` | Directo |
| `servicio_grupo_id` | _(sin columna)_ | Incorporar al nombre si se necesita |
| `recursivo = 1` | `aplica_a_todos` depende | Solo si es global |
| `deleted` | `fecha_baja` | Si `deleted IS NOT NULL` → inactivo |

**Necesitamos una tabla de mapping** `servicios.id → descuentos.id` para las siguientes etapas.

---

### 4.2 Tabla `socio_descuento` (nuevo)

**Fuente:** `socio_servicios` + join con `socios` para obtener `nro_socio` → `listado_medico.ID`

```sql
-- Concepto: un registro por (socio, servicio) activo
SELECT
    ss.socio_id,         -- mapear a listado_medico.ID via socios.nro_socio
    ss.servicio_id,      -- mapear a descuentos.id via tabla de mapping
    ss.created           -- como fecha_alta
FROM socio_servicios ss
WHERE ss.deleted IS NULL
  AND ss.estado_id = 1;  -- solo activos
```

**Problema conocido:** `socios.id` ≠ `listado_medico.ID`. El join correcto es:
```
socios.nro_socio = listado_medico.NRO_SOCIO
```

---

### 4.3 Tabla `deduccion_programa` (nuevo)

**Fuente:** `socio_servicio_detalles` — las cuotas pendientes son las que más interesan para la operación actual.

**Estrategia:** migrar solo las cuotas con `estado_id = 1` (pendiente) y `saldo > 0`.

```sql
SELECT
    ssd.id,
    ssd.servicio_id,        -- → descuentos.id
    ssd.socio_id,           -- → listado_medico.ID via socios.nro_socio
    ssd.monto  AS monto_total,
    ssd.monto  AS monto_cuota,   -- cuota única
    1          AS cuotas_total,
    1          AS cuota_nro,
    ssd.mes    AS mes_aplicar,
    ssd.anio   AS anio_aplicar,
    'pendiente' AS estado,
    ssd.created
FROM socio_servicio_detalles ssd
WHERE ssd.deleted IS NULL
  AND ssd.estado_id = 1
  AND ssd.saldo > 0
  AND ssd.paga_por_caja = 0;   -- excluir los de caja
```

**Registros a migrar:** aprox. **25.550** cuotas pendientes.

---

### 4.4 Tabla `deducciones` (nuevo — historial)

**Fuente:** `deducciones` legacy (solo las ya cobradas, `estado_id = 2`)

Esta migración es **opcional** y sirve solo para tener historial. Las activas (`estado_id = 1`) estarían cubiertas por `deduccion_programa`.

---

## 5. Consideraciones y problemas a resolver

### 5.1 Join de médicos

El sistema legacy usa `socios.id` internamente, pero el nuevo sistema usa `listado_medico.ID`. El puente es:

```
colegio_orig.socios.nro_socio = cmc_api.listado_medico.NRO_SOCIO
```

Hay que verificar que todos los `socio_id` de `socio_servicios` tengan un `nro_socio` que exista en `listado_medico`.

### 5.2 Servicios sin precio ni porcentaje

Varios servicios tienen `precio = 0` y `porcentaje = 0`. Son servicios históricos inactivos o con montos definidos individualmente por socio (en `socio_servicios.monto`). Hay que decidir si:
- Migrar con precio 0 (se puede editar después)
- Excluir de la migración de `descuentos`

### 5.3 Servicios porcentuales basados en liquidación

Los servicios con `es_porcentual = 1` y `basado_en_liquidacion = 1` (ej: Contribución s/honorarios 7%, D.G.R. 2%) no se pueden programar como cuotas fijas: su monto depende del total liquidado al médico en cada período. En el nuevo sistema esto lo maneja el flujo `bulk_generar_descuento`.

**Conclusión:** estos servicios **no se migran a `deduccion_programa`** — solo a `descuentos` + `socio_descuento`. El monto se calcula cuando se procesa cada liquidación.

### 5.4 `paga_por_caja`

Los `socio_servicio_detalles` con `paga_por_caja = 1` (~1.603 registros) se cobran en caja, no en liquidación. No tienen equivalente en el nuevo sistema (que solo maneja el flujo de liquidación). **Excluir de la migración.**

### 5.5 Servicio recursivo vs no recursivo

`servicios.recursivo = 1` → se genera automáticamente cada mes. En el nuevo sistema, esto lo haría el proceso de generación de cuotas. En la migración, importar la asignación en `socio_descuento` es suficiente — el sistema nuevo generará las cuotas futuras.

`servicios.recursivo = 0` → servicios puntuales (Cena Día Médico, Pileta, etc.). Se deben importar como `deduccion_programa` con las cuotas pendientes específicas.

### 5.6 Grupos AMMECO

Algunos `socio_servicios` tienen `ammeco_encabezado_id` que apunta a una tabla de archivos de importación. Son irrelevantes para la migración funcional.

---

## 6. Orden de migración sugerido

```
Paso 1: servicios → descuentos
        (generar mapping servicios.id → descuentos.id)

Paso 2: socio_servicios (activos) → socio_descuento
        (requiere: listado_medico ya cargado + mapping del paso 1)

Paso 3: socio_servicio_detalles (pendientes, paga_liq)
        → deduccion_programa (estado = pendiente)
        (requiere: mapping del paso 1 + paso 2 completo)

Paso 4 (opcional): deducciones (historial ya cobrado)
        → deducciones (snapshots históricos)
        (solo si se necesita historial completo)
```

---

## 7. Conteos clave para validación post-migración

| Entidad | Origen (legacy) | Destino (nuevo) | Filtro aplicado |
|---------|----------------|-----------------|-----------------|
| Conceptos de descuento | 50 servicios activos | `descuentos` | `deleted IS NULL` |
| Asignaciones médico-descuento | 7.591 `socio_servicios` activos | `socio_descuento` | `deleted IS NULL AND estado_id = 1` |
| Cuotas pendientes | ~25.550 `ssd` pendientes | `deduccion_programa` | `estado_id=1 AND saldo>0 AND paga_por_caja=0` |
| Médicos con deuda activa | 768 médicos | — | `saldo > 0` |
| Saldo pendiente total | $93.325.631,91 | — | — |

---

## 8. Queries de verificación pre-migración

```sql
-- ¿Todos los socios con servicios existen en listado_medico?
SELECT COUNT(*) as sin_match
FROM (
    SELECT DISTINCT ss.socio_id
    FROM colegio_orig.socio_servicios ss
    WHERE ss.deleted IS NULL
) ss_distinct
LEFT JOIN colegio_orig.socios so ON so.id = ss_distinct.socio_id
WHERE so.nro_socio NOT IN (
    SELECT NRO_SOCIO FROM cmc_api.listado_medico
);

-- ¿Cuántos servicios legacy no tienen equivalente en descuentos aún?
SELECT COUNT(*) FROM colegio_orig.servicios WHERE deleted IS NULL;
-- → debería coincidir con COUNT(*) FROM cmc_api.descuentos tras migrar el paso 1

-- ¿Cuántas cuotas pendientes hay con socio sin match?
SELECT COUNT(DISTINCT ssd.socio_id) as socio_sin_match
FROM colegio_orig.socio_servicio_detalles ssd
WHERE ssd.deleted IS NULL AND ssd.estado_id = 1 AND ssd.saldo > 0
  AND ssd.socio_id NOT IN (
    SELECT so.id FROM colegio_orig.socios so
    WHERE so.nro_socio IN (SELECT NRO_SOCIO FROM cmc_api.listado_medico)
  );
```
