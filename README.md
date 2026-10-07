# OptimizerOS — local development (Windows)

Phase 1 setup: four datastores in Docker, a FastAPI backend + worker running
natively on the host, and a Next.js frontend. This is the minimum needed to
log in, create a project, and watch a job stream progress live.

## Prerequisites

- Docker Desktop, installed and **running**.
- Python 3.11 or 3.12, with [`uv`](https://docs.astral.sh/uv/) on `PATH`.
- Node.js 20+ and npm.

## 1. Start the datastores

```powershell
docker compose up -d
docker compose ps
```

`mysql` and `redis` must show `healthy` before continuing (`qdrant` and
`neo4j` are provisioned for later phases and are not used yet — their health
status doesn't block Phase 1).

## 2. Configure environment variables

```powershell
Copy-Item backend\.env.example backend\.env
```

Edit `backend\.env`:

- Set `APP_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` to real random values
  (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`, run
  once per key).
- Set `MYSQL_PORT` / `MYSQL_PASSWORD` to match the ports and password in
  `docker-compose.yml` if you changed them.
- Set `SEED_USER_EMAIL` and `SEED_USER_PASSWORD` — this creates your login on
  first boot. Seeding is idempotent: changing these after the user already
  exists in MySQL has no effect until that row is removed.

`backend\.env` is gitignored and must never be committed.

## 3. Backend: install, migrate, run

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 6001
```

The seeded user is created automatically the first time the API boots.
Check `http://localhost:6001/api/v1/health` — it should report `mysql` and
`redis` as `up`.

## 4. Worker: run in a second terminal

```powershell
cd backend
uv run python -m app.worker
```

The worker blocks on the Redis queue and processes jobs one at a time. No
job the API accepts can complete without this running.

## 5. Frontend: run in a third terminal

```powershell
cd frontend
npm install
Copy-Item .env.local.example .env.local -ErrorAction SilentlyContinue
npm run dev
```

`frontend\.env.local` sets `NEXT_PUBLIC_API_BASE_URL` (defaults to
`http://localhost:6001/api/v1`, matching step 3 above). It's gitignored.

Open `http://localhost:6001`, log in with the seeded user, create a project
(it defaults to `AUDIT_ONLY`), open it, and run the health-check job — the
progress bar advances live over SSE, and the "force failure" variant renders
as failed with its error.

## Running the backend tests

```powershell
cd backend
uv run pytest
```

## Troubleshooting

- **`docker compose ps` never reports `mysql`/`redis` healthy** — Docker
  Desktop may still be starting; wait and re-check. If a port is already in
  use on the host, edit the port mapping in `docker-compose.yml` and the
  matching value in `backend\.env`.
- **Login fails with a correct-looking password** — the seeded user is only
  created once. If `backend\.env` was edited after the first boot, the live
  user still has the original credentials.
- **The job progress view never updates** — the worker (step 4) isn't
  running, or crashed. A queued job stays `queued` forever without it; it
  is never falsely reported as `succeeded`.
