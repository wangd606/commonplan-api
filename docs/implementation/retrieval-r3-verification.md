# Verify R3 grounded answers and citations

R3 consumes the authorization-filtered R2 evidence contract and produces structured claims whose
citation IDs must resolve to evidence from that same request. It does not add a LangGraph loop,
query rewriting, tool calls, or autonomous retries; those remain R4.

## Endpoint and contract

```text
POST /api/v1/workspaces/{workspace_id}/retrieval/answer
```

The request accepts the R2 scope and expansion controls plus `max_citations`. The response contains:

- a rendered answer for simple clients;
- structured `claims`, each with one or more citation IDs;
- bounded citation records with source target, excerpt, heading and page provenance;
- provider/model identity, grounded/insufficient-evidence status and timings;
- the complete R2 retrieval result for inspection.

Citation assembly happens only after workspace/Team/source lifecycle filtering, RRF, context
expansion and reranking. The assembler deduplicates stable citation targets, limits evidence size,
and never asks the answer provider to authorize or load product data. The provider cannot cite a
source outside the request because unknown IDs, uncited claims, excessive claims and malformed model
responses are rejected.

This is a provenance guarantee, not an independent semantic-entailment proof. Extractive mode is
grounded by construction because claim text is copied from evidence. Model-gateway mode constrains
and validates the citation contract, while R4's grader/evaluation loop remains responsible for
measuring whether a paraphrased claim is actually supported strongly enough by its cited passage.

## Local and model-backed modes

The default local mode is:

```text
RAG_ANSWER_PROVIDER=extractive
```

`deterministic-extractive-v1` selects literal evidence sentences and attaches their source IDs. It
requires no generation runtime, is suitable for local end-to-end verification, and must not be
represented as a generative summary.

After an approved generation deployment is registered behind LiteLLM, switch explicitly:

```text
RAG_ANSWER_PROVIDER=model_gateway
MODEL_GATEWAY_GENERATION_ALIAS=commonplan-generation-v1
```

The gateway adapter requests JSON claims at temperature zero. Its system contract treats retrieved
text as untrusted data, forbids instructions inside evidence, requires known citation IDs, and returns
an empty claim list when evidence is insufficient. There is no silent fallback between extractive and
model-backed modes.

## Automated checks

```bash
docker build --target test -t commonplan-api-r3-test backend
docker run --rm -e PYTHONPATH=/app \
  -v "$PWD/backend/tests:/app/tests:ro" \
  commonplan-api-r3-test pytest -q
```

Focused tests cover citation deduplication/bounds, missing and unknown citation rejection, prompt
injection separation, structured gateway responses, service composition and fail-closed endpoint
authentication.

The Web companion extends Retrieval Lab with **Ask with citations**, a claim-by-claim answer card,
source chips, expandable evidence and the original R2 channel diagnostics. Its Playwright test mocks
only network boundaries and verifies that a claim opens the exact cited evidence.

## Manual acceptance

1. In Retrieval Lab, ask a question that has indexed Issue, Comment, Project or attachment evidence.
2. Confirm each displayed claim has at least one `[S#]` chip.
3. Open the source list and verify the chip resolves to the returned target and excerpt.
4. Restrict Team scope and repeat; citations from excluded Teams must disappear from both answer and
   R2 diagnostics.
5. Ask an unsupported question. The response must state that authorized evidence is insufficient,
   with no invented claim or citation.

R3 is complete when the answer remains a deterministic projection of authorized evidence under the
local provider and the same structured validation gates a configured model provider. Answer quality
optimization, neural reranking and agentic retry decisions are separate measured changes.
