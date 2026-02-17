# CLAUDE.md

Este archivo le da contexto a Claude Code (claude.ai/code) sobre cómo trabajar con el código de este repositorio.

### Estructura de capas

```
app/
├── main.py              # App FastAPI, CORS, monta archivos estáticos en /uploads
├── api/
│   ├── routes.py        # Router central — todos los prefijos /api/* se registran acá
│   └── v1/              # Un archivo por dominio (medicos, liquidacion, debitos, etc.)
├── auth/
│   ├── router.py        # /auth/login, /auth/logout, /auth/refresh, SSO legacy
│   └── deps.py          # Dependencias JWT: get_current_user, require_scope
├── db/
│   ├── models.py        # Todos los modelos ORM de SQLAlchemy (archivo único, ~600+ líneas)
│   ├── database.py      # Engine async + fábrica de sesiones + dependencia get_db()
│   └── cruds/           # Helpers de consultas crudas (actualmente solo debitos.py)
├── services/            # Lógica de negocio (liquidaciones.py es el más complejo)
├── schemas/             # Modelos Pydantic de request/response
├── core/
│   ├── config.py        # Configuración via pydantic-settings, lee desde .env
│   └── security.py      # Encode/decode JWT
└── utils/               # Helpers de RBAC (get_effective_permission_codes)
```

### Conceptos clave del dominio

- **ListadoMedico** — modelo central del médico; `NRO_SOCIO` es la credencial de login / ID público; `ID` es la PK interna usada para relaciones
- **Periodo** — formato de string `YYYY-MM` usado en todo el sistema para operaciones mensuales (normalizado por `normalizar_periodo_flexible()` en `services/liquidaciones.py`)
- **Liquidacion / DetalleLiquidacion** — cabecera de liquidación + ítems por médico por período
- **Debito_Credito** — ajustes financieros aplicados antes/después de la liquidación
- **Padron** — snapshot del registro de médicos por período (vincula `ListadoMedico` con una `ObraSocial` para un período dado)

### Flujo de autenticación

- **Access token** (15 min por defecto) enviado como `Authorization: Bearer <token>`
- **Refresh token** (15 días) almacenado en cookie `HttpOnly`
- El payload JWT contiene `sub` (NRO_SOCIO), `type` ("access"/"refresh"), `scopes` (lista), `role`
- `require_scope("some.scope")` — usar como dependencia de FastAPI para proteger endpoints
- `get_current_user_with_scopes_and_role` — dependencia completa que retorna `(user, scopes, role)`

### Configuración / Entorno

Todas las configuraciones se cargan via `app/core/config.py` → `Settings` (pydantic-settings). Las variables requeridas están en `.env.example`. La URL async de MySQL se construye automáticamente a partir de las variables `MYSQL_*` individuales. Los orígenes de CORS van separados por coma en `CORS_ORIGINS`.

### Base de datos

- MySQL 5.7, driver async `aiomysql`
- Todos los modelos están en `app/db/models.py` con el estilo `Mapped`/`mapped_column` de SQLAlchemy 2.0
- Los nombres de columnas están en **MAYÚSCULAS** (convención del esquema legacy) — no es necesario mantener esta convención al agregar columnas nuevas
- Migraciones manejadas con Alembic (`alembic/`)

### Agregar un nuevo módulo de API

1. Crear `app/api/v1/<modulo>.py` con un `router = APIRouter()`
2. Agregar la lógica de negocio en `app/services/<modulo>.py`
3. Agregar los schemas Pydantic en `app/schemas/<modulo>_schema.py`
4. Registrar el router en `app/api/routes.py`
5. Si se necesitan nuevas tablas en la DB, agregar modelos en `app/db/models.py` y ejecutar `alembic revision --autogenerate`
