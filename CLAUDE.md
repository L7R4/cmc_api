# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the stack (detached)
make up
# or: docker compose up --build

# Stop
make down

# Run database migrations
docker compose exec fastapi alembic upgrade head

# Generate a new migration after model changes
docker compose exec fastapi alembic revision --autogenerate -m "descripcion"

# Seed local database
make seed
# or: docker compose exec fastapi python app/scripts/seed_local.py

# View logs
docker compose logs -f fastapi

# Rebuild without cache
make build
```

**Local URLs:**
- Swagger UI: `http://localhost:8000/docs`
- phpMyAdmin: `http://localhost:8080`

**Default credentials:** `nro_socio: 9999` / `password: admin123`

## Architecture

### Layer structure

```
app/
├── main.py              # FastAPI app, CORS, mounts /uploads static files
├── api/
│   ├── routes.py        # Central router — all /api/* prefixes registered here
│   └── v1/              # One file per domain (medicos, liquidacion, debitos, etc.)
├── auth/
│   ├── router.py        # /auth/login, /auth/logout, /auth/refresh, legacy SSO
│   └── deps.py          # JWT dependencies: get_current_user, require_scope
├── db/
│   ├── models.py        # All SQLAlchemy ORM models (single file, ~600+ lines)
│   ├── database.py      # Async engine + session factory + get_db() dependency
│   └── cruds/           # Raw query helpers (currently only debitos.py)
├── services/            # Business logic (liquidaciones.py is the most complex)
├── schemas/             # Pydantic request/response models
├── core/
│   ├── config.py        # Settings via pydantic-settings, reads from .env
│   └── security.py      # JWT encode/decode
└── utils/               # RBAC helpers (get_effective_permission_codes)
```

### Key domain concepts

- **ListadoMedico** — central physician model; `NRO_SOCIO` is the login credential / public ID; `ID` is the internal PK used for relations
- **Periodo** — `YYYY-MM` string format used throughout for month-based operations (normalised by `normalizar_periodo_flexible()` in `services/liquidaciones.py`)
- **Liquidacion / DetalleLiquidacion** — payroll header + line items per physician per period
- **Debito_Credito** — financial adjustments applied before/after liquidation
- **Padron** — physician registry snapshot per period (links `ListadoMedico` to an `ObraSocial` for a given period)

### Auth flow

- **Access token** (15 min default) sent as `Authorization: Bearer <token>`
- **Refresh token** (15 days) stored in `HttpOnly` cookie
- JWT payload carries `sub` (NRO_SOCIO), `type` ("access"/"refresh"), `scopes` (list), `role`
- `require_scope("some.scope")` — use as a FastAPI dependency to gate endpoints
- `get_current_user_with_scopes_and_role` — full dependency that returns `(user, scopes, role)`

### Config / Environment

All settings are loaded via `app/core/config.py` → `Settings` (pydantic-settings). Required vars are in `.env.example`. The MySQL async URL is constructed automatically from individual `MYSQL_*` vars. CORS origins are comma-separated in `CORS_ORIGINS`.

### Database

- MySQL 5.7, async driver `aiomysql`
- All models live in `app/db/models.py` with SQLAlchemy 2.0 `Mapped`/`mapped_column` style
- Column names are **UPPER_CASE** (legacy schema convention) — keep this consistent when adding columns
- Migrations managed with Alembic (`alembic/`)

### Adding a new API module

1. Create `app/api/v1/<module>.py` with a `router = APIRouter()`
2. Add the service logic in `app/services/<module>.py`
3. Add Pydantic schemas in `app/schemas/<module>_schema.py`
4. Register the router in `app/api/routes.py`
5. If new DB tables are needed, add models to `app/db/models.py` and run `alembic revision --autogenerate`
