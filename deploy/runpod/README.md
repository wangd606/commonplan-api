# Runpod generation endpoint

CommonPlan uses a Runpod Serverless vLLM endpoint only through the private LiteLLM model gateway.
The Business API never receives the Runpod credential and the browser never calls Runpod.

## Endpoint

Create the endpoint from Runpod's maintained vLLM worker:

- model: `Qwen/Qwen3-14B-AWQ`;
- GPU priority: RTX 4090 PRO, then the 24 GB standard pool;
- active workers: `0`;
- max workers: `1` for development;
- GPUs per worker: `1`;
- idle timeout: `30` seconds;
- autoscaling: request count, scaler value `1`;
- FlashBoot: enabled;
- environment: copy the non-secret values from `endpoint.env.example`.

Runpod returns an endpoint ID. Its OpenAI-compatible base URL is:

```text
https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1
```

## CommonPlan configuration

Keep the API key only in the deployment secret store or ignored local `.env`:

```dotenv
MODEL_GATEWAY_CONFIG_PATH=./deploy/litellm/config.runpod.yaml
RUNPOD_OPENAI_BASE_URL=https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1
RUNPOD_API_KEY=<secret>
RUNPOD_SERVED_MODEL_NAME=commonplan-qwen
RAG_ANSWER_PROVIDER=model_gateway
RAG_ANSWER_STRATEGY=agentic
```

Restart `model-gateway` and `backend`, then verify the boundary without printing the key:

```bash
python scripts/verify-runpod-generation.py
```

The first request can include worker and model initialization. Record cold, FlashBoot-revival and
warm timings separately. The R4 response reports model-call/token totals so cost can be compared with
Runpod execution metrics.

## Security and cost controls

- Never commit the Runpod API key or expose it to the Web app.
- Limit the endpoint to one worker until concurrency data justifies more.
- Keep active workers at zero during development.
- Send bounded authorized citations, not whole documents.
- Configure Runpod account spend limits and CommonPlan gateway budgets before production.
- Rotate the Runpod key if it is ever printed, committed, or sent to a client.
