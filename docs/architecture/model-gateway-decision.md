# AI gateway boundary: models, MCP tools, LiteLLM and Bifrost

Status: **local embedding path implemented; production control plane pending**. Docker Compose runs a
pinned LiteLLM Gateway between the ingestion worker and private Qwen/TEI. The worker authenticates and
uses `commonplan-embedding-v1`; online AI/RAG, generation/reranking aliases, gateway PostgreSQL,
restricted virtual keys, budgets, and production HA remain future work.

The repository also contains an optional, separately configured LiteLLM `tool-gateway` deployment.
It exposes the MCP control-plane boundary but intentionally registers no upstream MCP servers yet.
This is deployable scaffolding, not a claim that GitHub, Slack, or CommonPlan tools are implemented.

## Boundary and responsibilities

The model gateway is a private infrastructure service. It owns provider credentials, stable model
aliases, protocol translation, routing, same-model retries, approved fallbacks, budgets/rate limits,
and model-call telemetry. Product services receive service-scoped credentials and never receive an
upstream provider key.

The gateway does **not** own prompts, RAG orchestration, workspace/team ACL decisions, retrieval,
citations, or answer policy. Those remain in AI/RAG Service. It also does not parse or chunk source
documents; those remain ingestion responsibilities.

```text
Ingestion Worker ───────────────┐
                               ├── private Model Gateway ── Qwen TEI replicas
AI/RAG Service (query + answer) ┘                         └─ approved generation/rerank providers
```

MCP is a different traffic and security plane. LiteLLM and Bifrost can both package model and MCP
gateway capabilities, but a shared product does not make them one responsibility:

```text
Agent / AI-RAG Service ── model requests ── Model Gateway ── model runtimes/providers
                       └─ tool requests ─── MCP Tool Gateway ── reviewed MCP servers
```

The Model Gateway holds model-provider credentials and serves embedding/generation/rerank aliases.
The MCP Tool Gateway discovers and invokes tools/resources/prompts, may carry per-user OAuth context,
and needs tool-specific allowlists and audit records. Neither gateway decides CommonPlan workspace or
resource authorization; CommonPlan establishes the immutable caller scope before either is called.

Business API and Web BFF do not call model providers directly. They call AI/RAG Service over an
authenticated service channel and pass the immutable caller/workspace/team/resource scope established
by Business authorization. AI/RAG Service must still apply that scope as mandatory corpus filters and
is responsible for ensuring that only authorized retrieved content reaches the gateway; caller-supplied
scope values are never trusted directly.

## CommonPlan comparison

| Criterion | LiteLLM Gateway | Bifrost Gateway |
| --- | --- | --- |
| Runtime | Python gateway; can be deployed monolithically or as gateway/backend/UI services | Go gateway with an HTTP transport and optional embedded Go SDK |
| Provider breadth | Broadest compatibility surface (100+ providers in current documentation) | Smaller but substantial provider set (20+ in current documentation) |
| Client contract | OpenAI-compatible proxy plus provider translation | OpenAI-compatible unified API plus native-compatible provider endpoints |
| MCP | MCP gateway for tools, prompts and resources with key/team/organization access controls | Acts as MCP client and server with tool aggregation, approvals and multiple upstream auth modes |
| Routing | Model groups, load balancing, retries and configured fallbacks | Multi-provider/key routing, failover and load balancing; adaptive routing is promoted for advanced deployments |
| Governance | Mature virtual keys, per-key/user/team budgets, rate limits, spend tracking and admin UI | Virtual keys, budgets, rate limits, governance and UI; advanced identity/HA controls are part of its enterprise positioning |
| Production state | PostgreSQL for keys/teams/spend/config and Redis for distributed limits/cache in multi-replica deployments | Quick start persists configuration and logs in local SQLite; production clustering and HA require an explicit deployment decision |
| Performance profile | More control-plane features and a larger Python ecosystem; gateway latency is still small relative to remote model latency | Go implementation is designed for very low gateway overhead and high request concurrency |
| Ecosystem/maturity | Larger community, provider matrix and operational knowledge base | Newer and smaller ecosystem, with a simpler high-performance core |
| Open-source license | MIT for the open-source tree, with separately licensed enterprise content | Apache 2.0 core, with additional enterprise capabilities/services |
| Best fit here | Fastest low-risk path to provider portability and governance for the Python-based CommonPlan stack | Strong alternative if measured gateway throughput, memory footprint, or Go-native extension becomes a limiting factor |

Published performance numbers are not used as a selection proof: vendor benchmarks use different
configurations and model calls normally dominate end-to-end latency. CommonPlan should compare both
gateways with the same provider mock, payload distribution, concurrency, logging, and policy settings
before changing the selected implementation.

## Deployment recommendation

Adopt **LiteLLM as the common AI-gateway distribution**, but use two independently deployable units:

1. **Model Gateway** — private-only `/v1/*` model traffic, provider credentials, stable aliases,
   budgets and model telemetry.
2. **MCP Tool Gateway** — `/mcp` and MCP REST traffic, upstream server registry, per-user/workspace
   credentials, tool allowlists, approval policy and tool-call audit.

For local development, both units can run in the same Compose project. They still use separate ports,
configuration files and credentials. Production should keep them as separate workloads because MCP
connects to external systems and may require public/OAuth callback reachability, while model provider
credentials should remain on the most restrictive private network. They may share the LiteLLM code
base and operational expertise without sharing a failure domain or credential boundary.

LiteLLM has the broader provider surface and mature virtual-key, budget, routing and operational
model; it also fits the current Python/FastAPI team and minimizes integration risk while CommonPlan's
traffic is far below the point where gateway microseconds are material.

Keep application code gateway-neutral by using only an internal OpenAI-compatible contract and stable
aliases. Bifrost remains a qualified alternative, not a second gateway to operate in parallel. Revisit
it if a reproducible CommonPlan benchmark shows that LiteLLM's resource use or throughput is a real
constraint, or if Bifrost-only routing/governance capabilities become a requirement.

## Stable internal model aliases

| Alias | Consumer | Routing rule |
| --- | --- | --- |
| `commonplan-embedding-v1` | Ingestion Worker and AI/RAG query embedding | Pinned Qwen model, immutable revision and 1024 dimensions |
| `commonplan-generation-v1` | R3/R4 grounded-answer and agentic workflows | Runpod Serverless Qwen in the R4 deployment profile; every returned claim is revalidated against request-local citation IDs and the R4 grader |
| `commonplan-rerank-v1` | AI/RAG candidate reranking | Future explicit reranker profile; never inferred from the generation alias |

The alias is a product contract, not permission to change vector space. `commonplan-embedding-v1` may
retry or load-balance only across replicas serving the exact same model revision, tokenizer behavior,
normalization, and dimensions. It must never silently fall back to a different embedding model. A model
change creates `commonplan-embedding-v2`, a schema/index migration, dual-read or rebuild plan, and full
re-ingestion. Generation fallbacks are allowed only between models that pass the answer/citation
regression suite.

## Security and data ownership

- The gateway is reachable only on the private service network and authenticates each workload with a
  distinct service key or workload identity.
- Provider secrets live in Secret Manager/KMS and are never stored in Business, Auth, or RAG tables.
- Gateway PostgreSQL/Redis, if enabled, are gateway-owned stores; no CommonPlan service queries them.
- Prompts can contain private issue and attachment content. Raw request/response logging is disabled by
  default; approved diagnostics use redaction, access controls and a bounded retention period.
- Trace IDs, model alias, provider/deployment, token counts, latency, status, retry/fallback decision and
  cost are recorded without treating prompt bodies as ordinary application logs.
- AI/RAG Service performs ACL-filtered retrieval before the gateway call. The gateway is not an
  authorization substitute.
- Model and MCP gateway credentials are distinct. Possessing permission to call an embedding alias
  never grants permission to discover or execute tools.
- Every MCP server requires an explicit owner, allowed tools/resources, upstream authentication mode,
  caller-scope propagation rule, approval policy, audit fields and egress policy before registration.
- User OAuth tokens for GitHub/Slack-style MCP servers belong in a dedicated encrypted credential
  store or secrets system; they are not model-provider credentials and do not enter model prompts.

## Migration plan

1. Keep the direct TEI adapter as a diagnostic conformance baseline, not the default worker path.
2. **Implemented locally:** run pinned LiteLLM privately and configure `commonplan-embedding-v1` to
   reach the same pinned Qwen/TEI runtime. The live conformance test requires identical dimensions,
   finite normalized vectors, and cosine similarity above `0.99999` against the direct path.
3. **Implemented for ingestion:** switch Ingestion Worker to the `EmbeddingProvider` model-client
   interface targeting the gateway. The worker knows only the gateway URL, service credential, and
   stable alias; LiteLLM alone knows the TEI URL. The future query embedder must reuse this path.
4. **R3 client contract implemented:** the application can call `commonplan-generation-v1` for
   structured claims, treats retrieved evidence as untrusted prompt data, and rejects uncited or
   unknown citations. Register an actual generation deployment only with quality, timeout,
   error-mapping, cost and privacy tests. Enable retries and fallbacks per alias rather than globally.
5. **R4 orchestration and route implemented:** LangGraph bounds evidence grading, one query rewrite,
   cited generation, answer grading and one answer retry. The optional Runpod profile routes
   `commonplan-generation-v1` to `Qwen/Qwen3-14B-AWQ` through LiteLLM. Account endpoint creation,
   secret injection, cold/warm latency measurement, quality evaluation and spend limits remain
   deployment gates.
6. Before final production selection, run the same conformance and load suite against Bifrost. A switch
   changes deployment/configuration, not the service-facing API.
7. **Implemented as deployable scaffolding:** keep a separate optional LiteLLM `tool-gateway` process
   with an empty MCP registry. Add each upstream only after its auth/authorization and tool policy are
   reviewed; do not populate the registry merely to demonstrate connectivity.

The local stack currently authenticates with one explicitly marked development key because LiteLLM
virtual keys require its database-backed control plane. Production must add gateway-owned PostgreSQL,
persist the salt, generate a restricted ingestion service key, and keep the master key out of
application workloads before this boundary is production-ready.

## References

- [LiteLLM overview](https://docs.litellm.ai/docs/)
- [LiteLLM request architecture](https://docs.litellm.ai/docs/proxy/architecture)
- [LiteLLM production deployment](https://docs.litellm.ai/docs/proxy/deploy)
- [LiteLLM MCP overview](https://docs.litellm.ai/docs/mcp/)
- [LiteLLM MCP deployment topologies](https://github.com/BerriAI/litellm-docs/blob/main/docs/mcp_deployment.md)
- [Bifrost overview](https://github.com/maximhq/bifrost/blob/dev/docs/overview.mdx)
- [Bifrost MCP server connections](https://github.com/maximhq/bifrost/blob/dev/docs/mcp/connecting-to-servers.mdx)
- [Bifrost gateway setup and persistence](https://github.com/maximhq/bifrost/blob/dev/docs/quickstart/gateway/setting-up.mdx)
- [Bifrost repository and license](https://github.com/maximhq/bifrost)
