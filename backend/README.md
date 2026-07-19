# RetailSense AI — Backend

> Production-quality retail management platform backend built with **FastAPI**, **PostgreSQL**, and **SQLAlchemy (async)**.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | FastAPI 0.111 |
| ASGI Server | Uvicorn |
| Database | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 (async) |
| Driver | asyncpg |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Config | pydantic-settings + python-dotenv |
| Logging | structlog |

---

## Project Structure

```
backend/
├── app/
│   ├── api/                  # Route handlers (thin controllers)
│   │   ├── __init__.py
│   │   ├── router.py         # Central router registry
│   │   └── health.py         # GET /health
│   │
│   ├── core/                 # Cross-cutting concerns
│   │   ├── __init__.py
│   │   ├── config.py         # Pydantic Settings (all env vars)
│   │   ├── exceptions.py     # Global exception handlers
│   │   └── logging.py        # structlog configuration
│   │
│   ├── database/             # DB engine, session factory, Base
│   │   ├── __init__.py
│   │   └── session.py
│   │
│   ├── models/               # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   └── base.py           # TimestampMixin
│   │
│   ├── schemas/              # Pydantic request/response schemas
│   │   ├── __init__.py
│   │   └── base.py           # BaseSchema, TimestampSchema
│   │
│   ├── services/             # Business logic layer
│   │   └── __init__.py
│   │
│   ├── utils/                # Shared utilities
│   │   ├── __init__.py
│   │   └── helpers.py        # utc_now, paginate_query
│   │
│   └── main.py               # ASGI app factory & entry point
│
├── .env.example              # Environment variable template
├── requirements.txt          # Python dependencies
└── README.md
```

---

## Quickstart

### 1. Prerequisites

- Python 3.12+
- PostgreSQL 15+ running locally (or via Docker)

### 2. Create a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
# Open .env and update DATABASE_URL with your PostgreSQL credentials
```

### 5. Create the database

```sql
CREATE DATABASE retailsense;
```

### 6. Run the development server

```bash
# From the backend/ directory
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Verify

| URL | Purpose |
|---|---|
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |

---

## Health Check

```http
GET /health
```

```json
{
  "status": "healthy",
  "application": "RetailSense AI"
}
```

---

## Architecture Decisions

### Clean Layer Separation

```
Request → Router (api/) → Service (services/) → Repository/ORM (models/) → DB
                                   ↕
                             Schemas (schemas/)
```

- **Routers** are thin — they validate input, call services, and return responses.
- **Services** own all business logic and database interactions.
- **Models** define the database schema using SQLAlchemy ORM.
- **Schemas** define what the API accepts and returns using Pydantic.

### Async-First

All database I/O is non-blocking using `asyncpg` + SQLAlchemy async engine.
This allows the application to handle high concurrency without thread exhaustion.

### Structured Logging

`structlog` is used throughout.  In development, logs are coloured and human-readable.
In production (`APP_ENV=production`), logs are emitted as JSON for ingestion into
log aggregation platforms (Datadog, CloudWatch, ELK, etc.).

### Global Error Envelope

Every error response (validation, database, unhandled exception) follows the same shape:

```json
{
  "status": "error",
  "code": 422,
  "message": "Request validation failed.",
  "detail": [...]
}
```

### Security-Ready

The `SECRET_KEY`, `ALGORITHM`, and token expiry settings are pre-configured in
`app/core/config.py` and `.env.example`.  JWT middleware can be wired into
`app/core/` without touching the rest of the codebase.

---

## Adding a New Feature Module

1. **Model** — create `app/models/product.py`, inherit from `Base` + `TimestampMixin`.
2. **Schema** — create `app/schemas/product.py`, inherit from `BaseSchema`.
3. **Service** — create `app/services/product_service.py`.
4. **Router** — create `app/api/v1/products.py`, use `APIRouter`.
5. **Register** — import the router in `app/api/router.py`.
6. **Migration** — run `alembic revision --autogenerate -m "add products table"`.

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `RetailSense AI` | Application display name |
| `APP_ENV` | `development` | `development`, `staging`, `production` |
| `DEBUG` | `false` | Enable SQL echo and verbose logging |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `DATABASE_URL` | — | asyncpg PostgreSQL connection string |
| `DATABASE_POOL_SIZE` | `10` | Connection pool size |
| `SECRET_KEY` | — | **Change in production** — used for JWT signing |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT access token lifetime |
| `ALLOWED_ORIGINS` | `http://localhost:3000,...` | CORS allowed origins (comma-separated) |
