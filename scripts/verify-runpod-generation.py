#!/usr/bin/env python3
import json
import os
import sys
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(url: str, api_key: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=240) as response:
        return json.load(response)


def main() -> int:
    base_url = os.environ.get("RUNPOD_OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("RUNPOD_API_KEY", "")
    model = os.environ.get("RUNPOD_SERVED_MODEL_NAME", "commonplan-qwen")
    if not base_url or not api_key:
        print("Set RUNPOD_OPENAI_BASE_URL and RUNPOD_API_KEY first.", file=sys.stderr)
        return 2

    started = perf_counter()
    models = request_json(f"{base_url}/models", api_key)
    model_ids = {item.get("id") for item in models.get("data", [])}
    if model not in model_ids:
        print(f"Expected served model {model!r}; endpoint returned {sorted(model_ids)}", file=sys.stderr)
        return 1

    result = request_json(
        f"{base_url}/chat/completions",
        api_key,
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 80,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": "Return {\"status\":\"ok\"}."},
            ],
        },
    )
    content = result["choices"][0]["message"]["content"]
    if json.loads(content).get("status") != "ok":
        print("Endpoint returned valid JSON but failed the expected contract.", file=sys.stderr)
        return 1
    usage = result.get("usage", {})
    print(json.dumps({
        "status": "ok",
        "model": model,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Runpod verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
