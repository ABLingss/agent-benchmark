"""Real, non-streaming HTTP forwarding. No generated responses or fake inference."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from router import Backend, Router, RoutingError, prompt_tokens


MAX_RESPONSE = 2_000_000


def load_router(path):
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    backends = []
    for item in value["backends"]:
        address = item["base_url"].rstrip("/")
        parts = urlsplit(address)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("invalid backend base_url")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("remote backends require HTTPS")
        key_env = item.get("key_env", "")
        if key_env and not os.environ.get(key_env):
            raise ValueError(f"missing environment variable: {key_env}")
        backends.append(Backend(item["id"], address, item.get("capacity", 2), key_env, item.get("healthy", True)))
    if not backends:
        raise ValueError("configure at least one real backend")
    return Router(backends)


def validate(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str) or not payload["model"].strip():
        raise RoutingError(400, "invalid_request")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or any(
        not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") or not isinstance(m.get("content"), str)
        for m in messages
    ):
        raise RoutingError(400, "invalid_messages")
    if payload.get("stream", False) is not False:
        raise RoutingError(400, "stream_not_supported")
    return prompt_tokens(messages)


def forward(router, payload, policy="round_robin", timeout=30):
    tokens = validate(payload)
    backend, affinity = router.reserve(tokens, policy)
    success = False
    try:
        headers = {"Content-Type": "application/json"}
        if backend.key_env:
            headers["Authorization"] = "Bearer " + os.environ[backend.key_env]
        request = Request(backend.base_url + "/chat/completions", json.dumps(payload).encode(), headers)
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as exc:
            response = exc  # Relay upstream status and body unchanged, including 4xx/5xx.
        with response:
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise RoutingError(502, "upstream_response_too_large")
            status = response.status
            content_type = response.headers.get("Content-Type", "application/json")
        success = 200 <= status < 300
        return status, body, {"Content-Type": content_type, "X-Backend-Id": backend.id,
                              "X-Affinity-Tokens": str(affinity)}
    except (URLError, TimeoutError, OSError) as exc:
        raise RoutingError(502, "upstream_unreachable") from exc
    finally:
        router.release(backend, tokens, success)
