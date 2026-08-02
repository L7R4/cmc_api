# cmc_api

FastAPI backend for the Colegio Médico de Corrientes platform, providing secure APIs (JWT + RBAC), database migrations (Alembic), legacy SSO integration, and Dockerized deployments for local and production environments.

---

## What this project does

`cmc_api` is an API layer used to power administrative and public features for the Colegio Médico de Corrientes web platform. It includes:

- Core domain APIs (e.g., **users**, **obras sociales**, **physician payroll/liquidations**, **advertising sections**)
- **JWT authentication** and **RBAC (Role-Based Access Control)** for protected operations
- **SSO integration with a legacy system** to keep compatibility with existing workflows
- **Alembic migrations** to evolve the database safely over time
- **Docker / Docker Compose** setups for reproducible environments
- CI/CD automation via **GitHub Actions** (build/deploy pipelines)

---

## Latest changes

- **2026-07-24** — Mobile BFF (`/api/mobile`), benefits/agreements module and the
  data-correction request inbox. See [`docs/CAMBIOS-2026-07-24.md`](docs/CAMBIOS-2026-07-24.md).

---

## Tech Stack

- **Backend:** Python, FastAPI
- **Auth:** JWT, RBAC
- **Migrations:** Alembic
- **Infra:** Docker, Docker Compose, Caddy (TLS / reverse proxy)
- **CI/CD:** GitHub Actions
- **Database:** Relational DB (configured via environment)

---

## URLs

- API docs (Swagger): http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json
- phpMyAdmin: http://localhost:8080

## Quickstart (Docker)

### Prerequisites

- Docker + Docker Compose
- Git

### 1) Clone

```bash
git clone https://github.com/L7R4/cmc_api.git
cd cmc_api
```

### 2) Configure environment

Create a `.env` file based on `.env.example` (add one if you don’t have it yet).

```bash
# 1) Create your local environment file (only if it doesn't exist yet)
cp .env.example .env
```

### 3) Start the stack

```bash
docker compose up --build
```

or with make:

```bash
make up
```

### 4) Run database migrations (Alembic)

```bash
docker compose exec fastapi alembic revision --autogenerate -m "Initial"
docker compose exec fastapi alembic upgrade head
```

### 5) Run seed

```bash
docker compose exec fastapi python app/scripts/seed_local.py
```

or with make:

```bash
make seed
```

### 6) Open the API docs

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

---

## Default credentials:

- `nro_socio`: **9999**
- `password`: **admin123**

## Notes

- Legacy SSO is disabled by default in the local demo (`LEGACY_BASE_URL` / `LEGACY_SSO_SECRET` empty).
- Uploads are stored in a Docker volume and exposed at `http://localhost:8000/uploads/*`.
