# CommonPlan API

The server platform for CommonPlan. This repository contains the Business API and Web BFF, the independent Auth Service, PostgreSQL migrations, Redis-backed application-session infrastructure, and the local Docker Compose stack.

## Service boundaries

- `backend/`: Business API and browser-facing BFF on port `8000`
- `auth_service/`: identity, Google OAuth, access-token signing, refresh-token rotation, and JWKS on port `8001`
- PostgreSQL: separate `zhitong` and `zhitong_auth` databases
- Redis: disposable cache for application sessions; it is not an authentication authority
- Attachment storage: a signed, persistent local file volume in development; private S3-compatible storage in deployment

Every business route is mounted behind the shared fail-closed access-token guard. In the current
half-BFF topology, the browser keeps a short-lived access JWT in memory and receives an opaque
HTTP-only BFF session cookie; the raw refresh token remains server-managed. The target BFF split will
also remove access-token visibility from JavaScript.

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

## GitHub pull-request linking

M7 links pull requests to issues when a PR title contains an exact item key. Local GitHub delivery requires a public forwarding URL; follow the [local GitHub webhook setup guide](docs/github-integration-local.md) for the smee daemon, environment variables, GitHub settings, and verification steps.

## Attachments

M9 stores attachment metadata and resource links in PostgreSQL while file bytes stay outside the database. Local Docker uses a persistent file volume behind signed Business API URLs; deployment can set `OBJECT_STORAGE_DRIVER=s3` to use a private S3-compatible bucket. The browser receives a short-lived upload URL, uploads directly, then calls the authenticated complete endpoint. Download URLs are also short lived and issued only after resource authorization is checked again. See [the attachment data/API contract](docs/data-model/07-attachments.md).

The KEY-11 ingestion foundation consumes those durable attachment events in a separate worker,
preserving parent pages, retryable chunk/embedding state, resource ACL metadata, and canonical
pgvector embeddings. Local Compose runs Docling plus a private LiteLLM Gateway backed by pinned
`Qwen/Qwen3-Embedding-0.6B` TEI. The worker calls the stable `commonplan-embedding-v1` alias rather
than a model runtime directly, while documents and model inference remain local with no per-token API
charge. See [the ingestion design](docs/data-model/08-rag-ingestion.md) and
[the ingestion verification runbook](docs/implementation/ingestion-verification.md).

LiteLLM also supports MCP, but CommonPlan keeps tool traffic in a separate logical and deployable
boundary. The optional local `tool-gateway` profile starts a second LiteLLM process on port `4001`
with no upstream MCP servers registered by default:

```bash
docker compose --profile mcp up -d tool-gateway
```

This empty registry is intentional. A GitHub, Slack, or CommonPlan MCP server is added only after its
OAuth/credential forwarding, workspace authorization, tool allowlist, and audit policy are defined.
The model gateway on port `4000` remains private and never executes tools. See the
[MCP gateway verification runbook](docs/implementation/mcp-gateway-verification.md).

## Tests

```bash
cd backend && pytest -q
cd ../auth_service && pytest -q
```

See the [code-backed current architecture](docs/architecture/commonplan-current-architecture.md),
[target architecture](docs/architecture/commonplan-target-architecture.md), and
[`docs/auth-service-plan.md`](docs/auth-service-plan.md) for system boundaries and authentication
flows. Product data/API design starts at [`docs/data-model/README.md`](docs/data-model/README.md), and
the staged implementation plan is in [`docs/implementation/milestones.md`](docs/implementation/milestones.md).
The AI boundary and LiteLLM/Bifrost decision are documented separately in the
[model-gateway decision](docs/architecture/model-gateway-decision.md). The local embedding path is
implemented through LiteLLM; production virtual keys, gateway persistence, generation, reranking, and
online retrieval and its vector/lexical indexes remain later delivery work.
