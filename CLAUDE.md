# CLAUDE.md

Este archivo provee orientación a Claude Code (claude.ai/code) cuando trabaja con el código de este repositorio.

> Para el detalle completo de decisiones de arquitectura, justificaciones y estrategia de migración ver **`docs/ARQUITECTURA.md`**.

---

## Estructura del proyecto

```
app/
├── main.py                  # App FastAPI, CORS, sirve archivos estáticos en /uploads
│
├── api/
│   └── routes.py            # Router central — registra todos los prefijos /api/*
│                            # importando desde modules/*/routes.py
│
├── auth/
│   ├── router.py            # /auth/login, /auth/logout, /auth/refresh, SSO legacy
│   ├── deps.py              # Dependencias JWT: get_current_user, require_scope
│   └── permissions.py       # get_effective_permission_codes() (RBAC)
│
├── common/                  # Utilidades compartidas (reemplaza utils/)
│   ├── dates.py             # normalizar_periodo_flexible(), _parse_date(), separar_anio_mes()
│   └── files.py             # save_upload_for_medico(), UPLOAD_ROOT, helpers de archivos
│
├── core/
│   ├── config.py            # Settings vía pydantic-settings, lee desde .env
│   └── security.py          # JWT encode/decode
│
├── db/
│   ├── base.py              # Base (DeclarativeBase) + AuditMixin
│   ├── database.py          # Motor async + sessionmaker + dependencia get_db()
│   └── models.py            # Todos los modelos ORM (archivo único, ~1,174 líneas)
│
├── modules/                 # Dominios de negocio — organización principal del código
│   ├── medicos/             # routes.py · schemas.py · helpers.py
│   ├── liquidacion/         # routes.py · schemas.py
│   ├── debitos/             # routes.py · schemas.py
│   ├── deducciones/         # routes.py · routes_descuentos.py · schemas.py
│   ├── padrones/            # routes.py · schemas.py  (incluye asignaciones)
│   ├── contenido/           # routes_noticias.py · routes_publicidad.py · schemas.py
│   ├── solicitudes/         # routes.py · schemas.py
│   ├── catalogs/            # routes_especialidades.py · routes_obras_sociales.py ·
│   │                        # routes_periodos.py · routes_valores.py · schemas.py
│   ├── exports/             # routes.py · service.py
│   └── rbac/                # routes.py
│
└── services/                # Servicios compartidos / lógica de negocio compleja
    ├── liquidaciones.py     # Núcleo del cálculo de liquidaciones (importado por modules/liquidacion)
    ├── email.py             # Envío de emails vía Resend API
    ├── mail_templates.py    # Templates HTML de emails
    ├── deducciones_calc.py  # Cálculos de deducciones
    └── medicos_register_service.py  # Flujo de registro de médicos
```

---

## Conceptos y consideraciones claves segun modulo

#### Medicos

- **Modelo central**: `ListadoMedico` — `NRO_SOCIO` es la credencial pública (login), `ID` es la PK interna para FK.
- **Especialidades**: Hay columnas `NRO_ESPECIALIDAD[1-6]` + campo JSON `conceps_espec` con metadatos `{conceps: [], espec: [{id_colegio, n_resolucion, fecha_resolucion, adjunto}]}`. El JSON y las 6 columnas tienen que estar sincronizadas, es decir, no puede existir una especialidad en una columna NRO_ESEPECIALIDAD y no el JSON, y viceversa.
- **Documentos**: tabla `Documento` (label, path, content_type) + referencias a adjuntos dentro de `conceps_espec`. Eliminar un médico hace cascade delete de sus documentos.
- **Campos attach**: `attach_titulo`, `attach_matricula_prov`, `attach_dni`, `attach_cbu`, etc. Se mapean con `LABEL_TO_FIELD` en `helpers.py`.
- **Coerción de datos**: `existe="S|N"`, `sexo="M|F"` — usar `_coerce_existe()` / `_coerce_sexo()` de `schemas.py`.
- **Vencimientos**: `VENCIMIENTO_ANSSAL`, `VENCIMIENTO_MALAPRAXIS`, `VENCIMIENTO_COBERTURA` (Date). El endpoint `/all` permite filtrar por "por vencer en N días".
- **Registro**: El flujo público crea una `SolicitudRegistro` en estado `pendiente`; el flujo admin crea directamente el médico y la aprueba.
- **Helpers**: `parse_conceps_espec()`, `SPECIALTY_SLOTS`, `_find_free_slot()` en `modules/medicos/helpers.py`. La lógica de registro vive en `services/medicos_register_service.py`.

#### Debitos

- **Un DC por detalle**: cada `DetalleLiquidacion` puede tener cero o un `Debito_Credito` vinculado por `debito_credito_id`.
- **Tipo**: `"d"` = débito (resta al neto), `"c"` = crédito (suma al neto), `"n"` = quitar DC.
- **Upsert**: `POST /by_detalle/{detalle_id}` crea o actualiza el DC del detalle; `tipo="n"` o `monto<=0` lo elimina.
- **Recálculo automático**: cada operación llama `recalcular_totales_de_liquidacion()` y devuelve en la respuesta el `DebCreResumenOut` con los totales actualizados de la liquidación.
- **Solo liquidaciones abiertas**: valida `liquidacion.estado == "A"`; rechaza con 409 si está cerrada.
- **`prestacion_id` como string**: la FK a `GuardarAtencion` se extrae con `_parse_atencion_id()` (toma dígitos iniciales del string).

#### Deducciones

- **Dos flujos**: (1) **descuentos** = catálogo configurado (`Descuentos`, precio/porcentaje + médicos asignados vía `SocioDescuento`); (2) **deducciones** = aplicación de esos descuentos al resumen de liquidación.
- **`bulk_generar_descuento`**: calcula el monto para cada médico asignado al descuento en el resumen y hace UPSERT en `Deduccion` (snapshot del mes) y en `DeduccionSaldo` (saldo acumulado). Usa `INSERT ... ON DUPLICATE KEY UPDATE` de MySQL.
- **`aplicar`**: descuenta del `DeduccionSaldo` respetando el "disponible por médico" (bruto − débitos + créditos). Lo que no entra queda en saldo. Registra en `DeduccionAplicacion`.
- **Prioridad de cálculo**: porcentaje > precio fijo. Si ambos son 0, monto = 0 y el médico se omite.
- **`resumen_id` en URL**: las rutas de aplicación dependen de que el `LiquidacionResumen` exista.

#### Liquidacion

- **Jerarquía**: `LiquidacionResumen` (mes+año) → `Liquidacion` (por obra social) → `DetalleLiquidacion` (por médico/prestación).
- **Totales calculados on-the-fly**: `recalcular_resumen_liquidacion()` y `recalcular_totales_de_liquidacion()` corren en cada GET y en cada mutación, nunca se confía en los valores persistidos como fuente de verdad en pantalla.
- **Estado A / C**: abierta / cerrada. Solo se pueden editar DCs y detalles en estado `"A"`. Cerrar sella `cierre_timestamp`.
- **`nro_factura`**: viene de `Periodos.NRO_FACT_1 + NRO_FACT_2` al crear; se puede refacturar con `POST /refacturar` que crea una nueva `Liquidacion` con nuevo número.
- **`build_detalles_liquidacion()`**: al crear una liquidación, llama a este servicio (en `services/liquidaciones.py`) que copia las atenciones del período al detalle.
- **Vista detalles**: `GET /detalles_vista` llama a `vista_detalles_liquidacion()` que hace un JOIN complejo y devuelve `DetalleVistaRow` con datos enriquecidos (nombre médico, afiliado, DCs anidados). Soporta búsqueda full-text por NRO_SOCIO / NOMBRE / CODIGO_PRESTACION.

#### Padrones

- **Modelo**: `MedicoObraSocial` (legacy UPPERCASE) vincula `ListadoMedico` con obra social + período.
- **Asignaciones fusionadas**: las rutas de asignaciones de conceptos y especialidades a un médico se encuentran en este mismo módulo (`/{medico_id}/asignaciones/...`), montadas bajo el prefijo `/padrones`.
- **`catalogo_obras_sociales`**: filtra obras sociales con `MARCA = "S"` (habilitadas para el padrón).
- **Paginación**: `list_medicos_por_obra_social` soporta page + size para listar médicos de una OS.
- **`_ensure_json()`**: helper que garantiza que el campo `conceps_espec` del médico sea un dict válido antes de mutar asignaciones.

#### RBAC

- **Tres niveles**: Role → Permission (many-to-many vía `RolePermission`) + override por usuario (`UserPermission` con `allow: bool`).
- **Resolución efectiva**: `get_effective_permission_codes(user_id, db)` en `auth/permissions.py` calcula los códigos finales sumando role perms + overrides.
- **Scopes en JWT**: los códigos de permiso se inyectan en el token al hacer login; `require_scope("codigo")` los valida como dependencia FastAPI.
- **Override de permiso**: `UserPermission.allow=True` concede aunque el rol no lo tenga; `allow=False` deniega aunque el rol sí lo tenga.
- **Todos los endpoints requieren** scope `rbac:gestionar`.

#### Solicitudes

- **Estados**: `pendiente` → `aprobada` / `rechazada`. El campo `estado` es un string literal.
- **Creación**: Se crea desde el flujo de registro público de médicos (`modules/medicos/routes.py → POST /register`).
- **Aprobación**: `POST /{id}/approve` actualiza estado, asigna `aprobado_por` y dispara email vía `send_email_resend()`.
- **Stats**: `GET /stats/counts` agrupa por estado; `GET /stats/monthly` devuelve conteo por mes.
- **Timestamps**: `created_at` / `updated_at` con `timezone=True` (almacenados como UTC).

#### Catalogos

- **Especialidades** (`routes_especialidades.py`): Solo `GET /` — lista de `Especialidad` (campos `ID`, `ID_COLEGIO_ESPE`, `ESPECIALIDAD`). Es read-only; las especialidades son importadas del sistema legacy.
- **Obras Sociales** (`routes_obras_sociales.py`): CRUD completo. `IntegrityError` en CREATE → 409. El campo clave es `NRO_OBRASOCIAL` (entero único).
- **Periodos** (`routes_periodos.py`): `GET /disponibles?obra_social_id=X` devuelve períodos cerrados (`CERRADO="C"`) que aún no tienen `Liquidacion` creada — usado para poblar el selector al crear una liquidación.
- **Valores Boletín** (`routes_valores.py`): Tabla de aranceles por nivel y obra social. Campos con `@field_serializer` para serializar `Decimal` como `float` en la respuesta.

#### Contenido

- **Noticias**: `Noticia` con adjuntos en `DocumentoNoticias`. Los archivos se guardan en `uploads/web_noticias/<uuid>.<ext>`. `selectinload` carga documentos junto a la noticia.
- **Publicidad**: `PublicidadMedico` con un único adjunto por registro (imagen/video para el portal). Se guarda en `uploads/medicos_publicidad/`.
- **Nombres de archivo**: se generan con `uuid4().hex + ext` para evitar colisiones. El nombre original se preserva en `adjunto_filename`.
- **Eliminación de archivo físico**: al borrar o reemplazar un adjunto, se llama `.unlink(missing_ok=True)` sobre el path absoluto.
- **Búsqueda de médicos**: `GET /medicos/buscar?q=` en publicidad hace LIKE sobre NOMBRE, NRO_SOCIO, MATRICULA_PROV y DOCUMENTO.

---

## Flujo de autenticación

- **Access token** (15 min por defecto) enviado como `Authorization: Bearer <token>`
- **Refresh token** (15 días) almacenado en cookie `HttpOnly`
- El payload JWT lleva `sub` (NRO_SOCIO), `type` ("access"/"refresh"), `scopes` (lista), `role`
- `require_scope("some.scope")` — usar como dependencia FastAPI para proteger endpoints
- `get_current_user_with_scopes_and_role` — dependencia completa que retorna `(user, scopes, role)`

---

## Configuración / Entorno

Todas las settings se cargan vía `app/core/config.py` → `Settings` (pydantic-settings). Las variables requeridas están en `.env.example`. La URL async de MySQL se construye automáticamente a partir de las variables `MYSQL_*` individuales. Los orígenes CORS van separados por comas en `CORS_ORIGINS`.

---

## Base de datos

- La base de datos esta dockerizada en mi red cmc_api. Las credenciales las tienes en el .env
- MySQL 5.7, driver async `aiomysql`
- Los modelos ORM viven en `app/db/models.py` con el estilo SQLAlchemy 2.0 `Mapped`/`mapped_column`
- `Base` y `AuditMixin` se definen en `app/db/base.py`; todos los modelos importan desde ahí
- Los nombres de columnas son **UPPER_CASE** en las tablas legacy — no replicar esta convención en columnas nuevas
- Migraciones gestionadas con Alembic (`alembic/`)

---

1. Crear `app/api/v1/<modulo>.py` con un `router = APIRouter()`
2. Agregar la lógica de servicio en `app/services/<modulo>.py`
3. Agregar schemas Pydantic en `app/schemas/<modulo>_schema.py`
4. Registrar el router en `app/api/routes.py`
5. Si se necesitan nuevas tablas en la DB, agregar modelos a `app/db/models.py` y ejecutar `alembic revision --autogenerate`
