"""Explicit live preflight: sends one small request to each configured real backend."""
import argparse
import json
from proxy import forward, load_router
from router import Router, RoutingError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    router = load_router(args.config)
    failed = False
    for backend in router.backends:
        try:
            status, body, _ = forward(Router([backend]), {
                "model": args.model, "messages": [{"role": "user", "content": "Reply OK"}],
                "stream": False, "max_tokens": 8})
            response = json.loads(body)
            if not 200 <= status < 300 or not isinstance(response, dict) or not isinstance(response.get("choices"), list) or not response["choices"]:
                raise ValueError("invalid completion response")
            print(f"{backend.id}: HTTP {status}, completion response received")
        except (RoutingError, ValueError, UnicodeError):
            print(f"{backend.id}: live probe failed; check endpoint, model and credentials")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
