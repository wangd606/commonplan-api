# CommonPlan API

The server platform for CommonPlan. This repository contains the Business API and Web BFF, the independent Auth Service, PostgreSQL migrations, Redis-backed application-session infrastructure, and the local Docker Compose stack.

## Service boundaries

- `backend/`: Business API and browser-facing BFF on port `8000`
- `auth_service/`: identity, Google OAuth, access-token signing, refresh-token rotation, and JWKS on port `8001`
- PostgreSQL: separate `zhitong` and `zhitong_auth` databases
- Redis: disposable cache for application sessions; it is not an authentication authority

Every business route is mounted behind the shared fail-closed access-token guard. The browser receives only an opaque HTTP-only BFF session cookie; access and refresh credentials remain server-managed.

## Local development

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Business API docs: <http://localhost:8000/docs>
- Auth Service docs: <http://localhost:8001/docs>
- Auth Service JWKS: <http://localhost:8001/.well-known/jwks.json>

Run the companion [`commonplan-web`](https://github.com/LeonQC/commonplan-web) repository at <http://localhost:5173>.

## Tests

```bash
cd backend && pytest -q
cd ../auth_service && pytest -q
```

See [`docs/system-architecture.md`](docs/system-architecture.md) and [`docs/auth-service-plan.md`](docs/auth-service-plan.md) for the current architecture and authentication flows. Product data/API design starts at [`docs/data-model/README.md`](docs/data-model/README.md), and the staged implementation plan is in [`docs/implementation/milestones.md`](docs/implementation/milestones.md).
