# Arquitectura propuesta — CMC API

> Documento de referencia para la reestructuración del proyecto.
> Fecha: 2026-02-17 | Rama: `tester`

---

## 1. Problemas de la estructura actual

| Problema | Dónde | Impacto |
|----------|-------|---------|
| Archivo monolítico de modelos | `app/db/models.py` (1,174 líneas, 48 modelos) | Difícil de navegar, merge conflicts frecuentes |
| Lógica de negocio en routes | `app/api/v1/medicos.py` (~400 lín.) | Imposible testear sin levantar el servidor |
| Utilidades cajón de sastre | `app/utils/main.py` (~250 lín.) | Mezcla RBAC, fechas, uploads, parseo |
| Código muerto | `app/db/crud.py` (332 lín., 99% comentado) | Ruido, confusión |
| Naming inconsistente en schemas | `periodo.py` vs `periodo_schema.py` (duplicados) | Ambigüedad en imports |
| `Base` duplicada | `database.py` declara una, `models.py` declara otra | Solo se usa la de `models.py` |
| Router duplicado | `padrones_router` registrado 2 veces en `routes.py` (líneas 33 y 44) | Endpoints montados doble |
| Sin tests | No existe directorio `tests/` | Sin red de seguridad para refactors |

---

## 2. Estructura propuesta

```
app/
├── main.py                              # Entrada FastAPI, CORS, static files
│
├── common/                              # Utilidades compartidas (reemplaza utils/)
│   ├── __init__.py
│   ├── dates.py                         # normalizar_periodo(), _parse_date(), separar_anio_mes()
│   └── files.py                         # save_upload_for_medico(), UPLOAD_ROOT, helpers de archivos
│
├── core/                                # Configuración global (sin cambios estructurales)
│   ├── __init__.py
│   ├── config.py                        # Settings vía pydantic-settings
│   ├── security.py                      # JWT encode/decode
│   └── passwords.py                     # Hashing de contraseñas
│
├── db/                                  # Infraestructura de base de datos
│   ├── __init__.py
│   ├── base.py                          # Base (DeclarativeBase) + AuditMixin
│   ├── database.py                      # Engine async, sessionmaker, get_db()
│   └── models/                          # Modelos ORM divididos por dominio
│       ├── __init__.py                  # Re-exporta TODOS los modelos (punto único para Alembic)
│       ├── legacy.py                    # ~29 modelos de solo lectura (tablas legacy)
│       ├── medico.py                    # ListadoMedico, Documento
│       ├── liquidacion.py              # Liquidacion, LiquidacionResumen, DetalleLiquidacion, GuardarAtencion
│       ├── financiero.py               # Debito_Credito, Descuentos, SocioDescuento, Deduccion, DeduccionSaldo, DeduccionAplicacion
│       ├── catalogs.py                  # Especialidad, ObrasSociales, Periodos, PeriodosDoctor, ValoresBoletin, ValoresBoletinHistorial, ValoresObrasocial, Padron
│       ├── rbac.py                      # Role, Permission, UserRole, RolePermission, UserPermission
│       ├── contenido.py                 # Noticia, DocumentoNoticias, PublicidadMedico
│       └── solicitud.py                # SolicitudRegistro
│
├── auth/                                # Autenticación y autorización (cross-cutting)
│   ├── __init__.py
│   ├── router.py                        # /auth/login, /auth/logout, /auth/refresh
│   ├── deps.py                          # get_current_user, require_scope
│   └── permissions.py                   # get_effective_permission_codes() (desde utils/main.py)
│
├── modules/                             # Módulos de dominio
│   ├── medicos/                         # Gestión de médicos
│   │   ├── __init__.py
│   │   ├── routes.py                    # Endpoints CRUD + especialidades + documentos + stats
│   │   ├── service.py                   # Lógica extraída de los routes actuales
│   │   ├── schemas.py                   # Todos los Pydantic models del médico
│   │   ├── helpers.py                   # SPECIALTY_SLOTS, parse_conceps_espec, etc.
│   │   └── register.py                  # Flujo de registro (desde medicos_register_service.py)
│   │
│   ├── liquidacion/                     # Generación de liquidaciones
│   │   ├── __init__.py
│   │   ├── routes.py                    # Endpoints (incluye detalles_liquidaciones)
│   │   ├── service.py                   # Lógica core (desde services/liquidaciones.py)
│   │   └── schemas.py                   # Schemas de liquidación
│   │
│   ├── debitos/                         # Débitos y créditos
│   │   ├── __init__.py
│   │   ├── routes.py                    # Endpoints de ajustes financieros
│   │   ├── service.py                   # Queries (desde db/cruds/debitos.py)
│   │   └── schemas.py                   # Schemas de débitos/créditos
│   │
│   ├── deducciones/                     # Deducciones + descuentos (acoplados por dominio)
│   │   ├── __init__.py
│   │   ├── routes.py                    # Endpoints de deducciones
│   │   ├── routes_descuentos.py         # Endpoints de descuentos (separados por claridad)
│   │   ├── service.py                   # Cálculos (desde deducciones_calc.py)
│   │   └── schemas.py                   # Schemas combinados
│   │
│   ├── padrones/                        # Padrón + asignaciones
│   │   ├── __init__.py
│   │   ├── routes.py                    # Endpoints de padrones + asignaciones médico
│   │   └── schemas.py
│   │
│   ├── contenido/                       # Noticias + publicidad (ambos son contenido/media)
│   │   ├── __init__.py
│   │   ├── routes_noticias.py           # Endpoints de noticias
│   │   ├── routes_publicidad.py         # Endpoints de publicidad médica
│   │   └── schemas.py                   # Schemas combinados
│   │
│   ├── solicitudes/                     # Solicitudes de registro
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── schemas.py
│   │
│   ├── catalogs/                        # Datos de referencia agrupados (CRUD simple)
│   │   ├── __init__.py
│   │   ├── routes_especialidades.py     # GET lista de especialidades
│   │   ├── routes_obras_sociales.py     # CRUD obras sociales
│   │   ├── routes_periodos.py           # CRUD periodos
│   │   ├── routes_valores.py            # CRUD valores boletín
│   │   └── schemas.py                   # Schemas combinados de catálogos
│   │
│   ├── exports/                         # Exportaciones Excel
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   └── service.py                   # Generación de Excel (desde services/exports.py)
│   │
│   └── rbac/                            # Admin de roles y permisos
│       ├── __init__.py
│       └── routes.py                    # Endpoints CRUD de roles/permisos
│
├── services/                            # Servicios compartidos (cross-domain)
│   ├── __init__.py
│   ├── email.py                         # Envío de emails vía Resend API
│   └── mail_templates.py               # Templates HTML de emails
│
├── api/                                 # Agregación de routers
│   ├── __init__.py
│   └── routes.py                        # Registro central: importa desde modules/*/routes.py
│
├── scripts/                             # Scripts one-off
│   ├── seed_local.py
│   └── backfill_medicos.py
│
└── tests/                               # Suite de tests
    ├── __init__.py
    ├── conftest.py                      # Fixtures: async DB session, test client, auth helpers
    ├── factories.py                     # Factories para datos de test
    ├── modules/
    │   ├── __init__.py
    │   ├── test_medicos.py
    │   ├── test_liquidacion.py
    │   ├── test_debitos.py
    │   ├── test_deducciones.py
    │   ├── test_padrones.py
    │   ├── test_contenido.py
    │   ├── test_solicitudes.py
    │   ├── test_catalogs.py
    │   ├── test_exports.py
    │   └── test_rbac.py
    └── test_auth.py
```

---

## 3. Decisiones de diseño y justificación

### 3.1 Organización híbrida: módulos por dominio + capas compartidas

**Qué:** Los dominios de negocio se agrupan en `modules/`, mientras que la infraestructura (`db/`, `core/`, `auth/`) se mantiene centralizada.

**Por qué:** Una organización 100% por capas técnicas (todos los routes en una carpeta, todos los services en otra) escala mal: al agregar un feature hay que tocar 4-5 carpetas distintas. Con módulos por dominio, todo lo relacionado a "médicos" vive junto. Pero infraestructura como la DB y auth son cross-cutting y duplicarlas por módulo sería redundante.

**Referencia:** Este patrón es conocido como *"screaming architecture"* — la estructura del proyecto grita el dominio, no el framework.

---

### 3.2 Modelos divididos en `db/models/` con `__init__.py` de re-export

**Qué:** Los 48 modelos se dividen en 8 archivos por dominio. Un `__init__.py` re-exporta todo.

**Por qué:**
- **Navegabilidad** — Encontrar `Liquidacion` es ir a `db/models/liquidacion.py`, no buscar en 1,174 líneas
- **Merge conflicts** — Dos devs trabajando en dominios distintos no colisionan
- **Alembic** — Necesita descubrir todos los modelos desde un punto. El `__init__.py` con `from .medico import *` etc. resuelve esto sin cambiar `env.py`

**No se ponen los modelos dentro de cada module/ porque:**
- Las FK cruzan dominios (`DetalleLiquidacion` referencia `Debito_Credito`)
- SQLAlchemy necesita que todos los modelos compartan el mismo `Base`
- Centralizar modelos en `db/models/` mantiene un solo punto de verdad para el schema de DB

---

### 3.3 `common/` en lugar de `utils/`

**Qué:** `utils/main.py` (250 líneas) se divide en `common/dates.py` y `common/files.py`. La lógica RBAC se mueve a `auth/permissions.py`.

**Por qué:**
- `utils/main.py` mezcla 4 responsabilidades distintas — viola el Principio de Responsabilidad Única
- `dates.py` agrupa todo lo de parseo de periodos y fechas (usado por liquidaciones, médicos, padrones)
- `files.py` agrupa upload y manejo de archivos (usado por médicos, noticias, publicidad)
- El nombre `common/` es más descriptivo que `utils/` (que tiende a convertirse en cajón de sastre)

---

### 3.4 `db/base.py` separado

**Qué:** `Base` (DeclarativeBase) y `AuditMixin` se extraen a `db/base.py`.

**Por qué:** Actualmente `database.py` declara su propia `Base = declarative_base()` que no se usa, y `models.py` define `class Base(DeclarativeBase)`. Esto genera confusión. Con `base.py`:
- `database.py` importa `Base` de `base.py` (si lo necesita)
- Todos los archivos de `models/` importan `Base` y `AuditMixin` de `base.py`
- Se elimina la `Base` duplicada de `database.py`

---

### 3.5 Agrupación de dominios pequeños en `catalogs/`

**Qué:** `especialidades`, `obras_sociales`, `periodos` y `valores_boletin` se agrupan bajo `modules/catalogs/`.

**Por qué:** Son dominios de CRUD simple (listar, crear, actualizar) sin lógica de negocio. Por ejemplo, `especialidades.py` actual tiene solo 17 líneas. Crear un módulo completo con 5 archivos para 17 líneas de código es over-engineering. Al agruparlos:
- Se reduce la cantidad de carpetas de 16 a 10
- Se mantiene la navegación limpia
- Si alguno crece, se puede extraer a su propio módulo

---

### 3.6 Deducciones + descuentos juntos

**Qué:** Se combinan en `modules/deducciones/` con rutas separadas (`routes.py` y `routes_descuentos.py`).

**Por qué:** Están acoplados por dominio — `SocioDescuento` vincula descuentos con médicos, y las deducciones dependen de los descuentos configurados. Comparten el servicio de cálculo (`deducciones_calc.py`). Mantener rutas separadas preserva la organización de la API sin forzar un acoplamiento artificial en la interfaz HTTP.

---

### 3.7 Padrones absorbe asignaciones

**Qué:** `asignaciones.py` se fusiona dentro de `modules/padrones/routes.py`.

**Por qué:** Las asignaciones son la acción de asignar un médico a un padrón para un periodo. Comparten el mismo modelo (`Padron`) y contexto de dominio. En `routes.py` actual, ambos routers ya se montan bajo el mismo prefijo `/medicos` y `/padrones`.

---

### 3.8 Contenido agrupa noticias + publicidad

**Qué:** `noticias` y `publicidad_medicos` se agrupan en `modules/contenido/`.

**Por qué:** Ambos son gestión de contenido/media: tienen modelos con adjuntos (imágenes, archivos), CRUD similar, y no tienen lógica de negocio compleja. Al agruparlos se reduce el número de módulos manteniendo coherencia semántica.

---

### 3.9 `services/` solo para servicios compartidos

**Qué:** Solo quedan `email.py` y `mail_templates.py` en `services/`. El resto se mueve a sus módulos.

**Por qué:** El email es usado por múltiples dominios (médicos en registro, solicitudes en aprobación). No tiene un dueño claro. Mantenerlo centralizado evita que un módulo dependa internamente de otro.

---

### 3.10 Cada módulo tiene su `service.py` solo si lo necesita

**Qué:** Solo `medicos`, `liquidacion`, `debitos`, `deducciones` y `exports` tienen `service.py`.

**Por qué:** No todos los dominios tienen lógica de negocio. Los catálogos, RBAC, solicitudes y contenido son CRUD directo contra la DB. Forzar un `service.py` vacío para "ser consistente" es boilerplate innecesario. Si en el futuro crece la lógica, se agrega el archivo.

---

## 4. Distribución de modelos ORM

### `db/models/legacy.py` — Modelos de solo lectura (~29)

Tablas del sistema legacy que solo se consultan, nunca se crean/actualizan desde la API:

```
Avisos, Clinicas, CodigoDescripcion, CodigoNomenclador,
Codigoprestacionswiss, Consulta, EspeCod, EspeCodSwiss,
GuardarIoscor, GuardarRefacturacion, MedicoObraSocial,
Nomenclador, NomencladorIoscor, Paciente,
UnidadNomenclador, UnidadNomenclador10, UnidadNomenclador7,
UnidadNomencladorInf, UsuarioColegio, ValidarUsuario,
ValorFijo, ValorNomencladoFijo, ValorNomencladoSwiss,
ValorNomencladorNacional, ValorPrestacion, ValorPrestacion10,
ValorPrestacion7, ValorPrestacionInf, ValoresObrasocial
```

### `db/models/medico.py` — Médicos (2)
```
ListadoMedico    — Modelo central del médico (NRO_SOCIO = login, ID = PK interna)
Documento        — Archivos adjuntos del médico
```

### `db/models/liquidacion.py` — Liquidaciones (4)
```
Liquidacion          — Cabecera de liquidación por período
LiquidacionResumen   — Resumen mensual de liquidación
DetalleLiquidacion   — Ítems de detalle por médico
GuardarAtencion      — Registros de atención médica
```

### `db/models/financiero.py` — Financiero (6)
```
Debito_Credito       — Ajustes de débito/crédito
Descuentos           — Tipos de descuento
SocioDescuento       — Descuentos asignados a médicos
Deduccion            — Deducciones mensuales
DeduccionSaldo       — Saldos de deducciones
DeduccionAplicacion  — Aplicaciones de deducciones en períodos
```

### `db/models/catalogs.py` — Catálogos (8)
```
Especialidad              — Especialidades médicas
ObrasSociales             — Obras sociales
Periodos                  — Períodos mensuales
PeriodosDoctor            — Períodos por doctor
Padron                    — Padrón de médicos por período
ValoresBoletin            — Valores del boletín
ValoresBoletinHistorial   — Historial de valores
ValoresObrasocial         — Valores por obra social
```

### `db/models/rbac.py` — Control de acceso (5)
```
Role, Permission, UserRole, RolePermission, UserPermission
```

### `db/models/contenido.py` — Contenido (3)
```
Noticia, DocumentoNoticias, PublicidadMedico
```

### `db/models/solicitud.py` — Solicitudes (1)
```
SolicitudRegistro
```

---

## 5. Convenciones de nombres

| Elemento | Convención | Ejemplo |
|----------|------------|---------|
| Archivo de rutas | `routes.py` o `routes_<sub>.py` | `routes.py`, `routes_descuentos.py` |
| Archivo de servicio | `service.py` (singular) | `modules/medicos/service.py` |
| Archivo de schemas | `schemas.py` (plural, dentro del módulo) | `modules/medicos/schemas.py` |
| Archivo de modelos | Nombre del dominio en singular | `db/models/medico.py` |
| Módulos | Plural, snake_case | `modules/medicos/`, `modules/deducciones/` |
| Variables de router | `router` (sin sufijo) | `router = APIRouter()` |
| Funciones de servicio | Verbos descriptivos, async | `async def calcular_liquidacion(...)` |
| Schemas Pydantic | `<Entidad><Acción>` | `MedicoCreate`, `LiquidacionRead` |

---

## 6. Archivos a eliminar

| Archivo | Razón |
|---------|-------|
| `app/db/crud.py` | 332 líneas, 99% código comentado — dead code |
| `app/schemas/main.py` | Archivo vacío (1 línea) |
| `app/schemas/periodo.py` | Duplicado de `periodo_schema.py` |
| `app/api/deps.py` | Wrapper trivial de 6 líneas — reemplazar con import directo a `get_db` |
| `app/utils/main.py` | Se divide en `common/dates.py`, `common/files.py`, `auth/permissions.py` |
| `app/db/cruds/` | Directorio completo — la lógica se mueve a `modules/debitos/service.py` |
| `app/api/v1/` | Directorio completo (después de migrar todo a `modules/`) |
| `app/schemas/` | Directorio completo (después de migrar a cada módulo) |
| `app/services/liquidaciones.py` | Se mueve a `modules/liquidacion/service.py` |
| `app/services/deducciones_calc.py` | Se mueve a `modules/deducciones/service.py` |
| `app/services/medicos_register_service.py` | Se mueve a `modules/medicos/register.py` |

---

## 7. Cómo queda `api/routes.py` (registro central)

```python
from fastapi import APIRouter

from app.modules.medicos.routes import router as medicos_router
from app.modules.liquidacion.routes import router as liquidacion_router
from app.modules.debitos.routes import router as debitos_router
from app.modules.deducciones.routes import router as deducciones_router
from app.modules.deducciones.routes_descuentos import router as descuentos_router
from app.modules.padrones.routes import router as padrones_router
from app.modules.contenido.routes_noticias import router as noticias_router
from app.modules.contenido.routes_publicidad import router as publicidad_router
from app.modules.solicitudes.routes import router as solicitudes_router
from app.modules.catalogs.routes_especialidades import router as especialidades_router
from app.modules.catalogs.routes_obras_sociales import router as obras_sociales_router
from app.modules.catalogs.routes_periodos import router as periodos_router
from app.modules.catalogs.routes_valores import router as valores_router
from app.modules.exports.routes import router as exports_router
from app.modules.rbac.routes import router as rbac_router

api_router = APIRouter()

api_router.include_router(medicos_router,         prefix="/medicos",            tags=["Medicos"])
api_router.include_router(liquidacion_router,     prefix="/liquidacion",        tags=["Liquidacion"])
api_router.include_router(debitos_router,         prefix="/debitos_creditos",   tags=["Debitos / Creditos"])
api_router.include_router(deducciones_router,     prefix="/deducciones",        tags=["Deducciones"])
api_router.include_router(descuentos_router,      prefix="/descuentos",         tags=["Descuentos"])
api_router.include_router(padrones_router,        prefix="/padrones",           tags=["Padrones"])
api_router.include_router(noticias_router,        prefix="/noticias",           tags=["Noticias"])
api_router.include_router(publicidad_router,      prefix="/publicidad-medicos", tags=["Publicidad"])
api_router.include_router(solicitudes_router,     prefix="/solicitudes",        tags=["Solicitudes"])
api_router.include_router(especialidades_router,  prefix="/especialidades",     tags=["Especialidades"])
api_router.include_router(obras_sociales_router,  prefix="/obras_social",       tags=["Obras Sociales"])
api_router.include_router(periodos_router,        prefix="/periodos",           tags=["Periodos"])
api_router.include_router(valores_router,         prefix="/valores",            tags=["ValoresBoletin"])
api_router.include_router(exports_router,         prefix="/exports",            tags=["Exports"])
api_router.include_router(rbac_router,            prefix="/admin/rbac",         tags=["RBAC"])
```

> **Nota:** Se corrige el bug actual donde `padrones_router` estaba registrado 2 veces (líneas 33 y 44 del `routes.py` actual).

---

## 8. Estrategia de migración incremental

La migración se ejecuta en fases para evitar un commit masivo que rompa todo.

### Fase 1: Infraestructura (sin cambios de comportamiento)
1. Crear `app/db/base.py` con `Base` y `AuditMixin`
2. Crear `app/db/models/` con los 8 archivos + `__init__.py` re-export
3. Actualizar `alembic/env.py` para importar desde `app.db.models`
4. Eliminar `Base` duplicada de `database.py`
5. Eliminar `app/db/crud.py` (código muerto)
6. **Verificar:** `alembic check` no debe generar migraciones espurias

### Fase 2: Utilidades comunes
1. Crear `app/common/dates.py` y `app/common/files.py`
2. Mover `get_effective_permission_codes()` a `app/auth/permissions.py`
3. Actualizar todos los imports desde `app.utils.main`
4. Eliminar `app/utils/main.py` y `app/api/deps.py`

### Fase 3: Módulos (uno por uno, de menor a mayor riesgo)
1. `catalogs` → `rbac` → `solicitudes` → `contenido` → `exports`
2. `padrones` → `debitos` → `deducciones`
3. `liquidacion` (el más complejo — service de 500+ líneas)
4. `medicos` (el más grande — extraer lógica a service.py)

### Fase 4: Limpieza
1. Eliminar `app/api/v1/`, `app/schemas/`, servicios migrados
2. Limpiar imports huérfanos

### Fase 5: Tests
1. Configurar `tests/conftest.py` con fixtures async
2. Escribir tests por módulo, empezando por `liquidacion` y `medicos`

---

## 9. Estructura de tests

```
tests/
├── conftest.py              # Fixtures compartidos:
│                            #   - async_session: sesión de DB en memoria/test
│                            #   - client: TestClient de FastAPI
│                            #   - auth_headers: headers con JWT de test
├── factories.py             # Funciones factory para crear datos de test:
│                            #   - create_medico(), create_liquidacion(), etc.
├── modules/
│   ├── test_medicos.py      # Tests de endpoints + service de médicos
│   ├── test_liquidacion.py  # Tests del flujo completo de liquidación
│   ├── test_debitos.py
│   ├── test_deducciones.py
│   ├── test_padrones.py
│   ├── test_contenido.py
│   ├── test_solicitudes.py
│   ├── test_catalogs.py
│   ├── test_exports.py
│   └── test_rbac.py
└── test_auth.py             # Tests de login, refresh, scopes, permisos
```

**Stack de testing recomendado:**
- `pytest` + `pytest-asyncio` para tests async
- `httpx` con `ASGITransport` para TestClient async
- Base de datos de test separada (o SQLite en memoria para unit tests)

---

## 10. Diagrama de dependencias (nueva estructura)

```
main.py
  └── api/routes.py
        └── modules/*/routes.py
              ├── modules/*/service.py (si existe)
              │     └── db/models/*
              ├── modules/*/schemas.py
              ├── auth/deps.py
              │     ├── core/security.py → core/config.py
              │     └── auth/permissions.py → db/models/rbac.py
              ├── db/database.py → core/config.py
              └── common/dates.py | common/files.py

services/email.py ← usado por modules/medicos/ y modules/solicitudes/
  └── core/config.py
```

**Flujo de una request típica:**
```
Request HTTP
  → modules/<dominio>/routes.py     (validación de input con schemas)
  → modules/<dominio>/service.py    (lógica de negocio)
  → db/models/<dominio>.py          (acceso a datos vía SQLAlchemy)
  → Response con schema Pydantic
```
