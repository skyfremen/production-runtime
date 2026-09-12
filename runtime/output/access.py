"""Cheap read-only remote-service readiness preflight for production workflows.

This deliberately reuses the production credential/client/identity code. It never
creates or mutates a remote resource; one read request proves token refresh, API
access, and the pinned production identity before expensive media work begins.
"""
import os
import socket

from output.state import RecoveryBlocked
from output.transfer import authenticated_channel, make_client

REQUIRED = (
    "RUNTIME_AUTH_A",
    "RUNTIME_AUTH_B",
    "RUNTIME_AUTH_C",
)


def _http_detail(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = str(exc)
    lowered = text.lower()
    if status == 401:
        kind = "authentication"
    elif status == 403 and any(token in lowered for token in ("quota", "dailylimit", "rate limit")):
        kind = "quota"
    elif status == 403:
        kind = "permission/API configuration"
    elif status == 429 or (status is not None and status >= 500):
        kind = "transient external API"
    else:
        kind = "external API"
    return f"{kind} failure" + (f" (HTTP {status})" if status is not None else "")


def run_preflight():
    missing = [name for name in REQUIRED if not str(os.environ.get(name) or "").strip()]
    if missing:
        raise SystemExit("Credential preflight failed: missing " + ", ".join(missing))

    try:
        channel = authenticated_channel(make_client())
    except RecoveryBlocked as exc:
        raise SystemExit(f"Remote identity readiness preflight failed: {exc}") from None
    except (TimeoutError, OSError, socket.timeout) as exc:
        raise SystemExit(
            f"Remote readiness preflight failed: transient network failure ({type(exc).__name__})"
        ) from None
    except Exception as exc:
        # The client library exposes resp.status for API errors. Avoid importing
        # the external package at module import time so this guard stays easy to test.
        if getattr(getattr(exc, "resp", None), "status", None) is not None:
            raise SystemExit(f"Remote readiness preflight failed: {_http_detail(exc)}") from None
        # Credential refresh failures commonly surface through client exceptions;
        # preserve the type without leaking credential values.
        raise SystemExit(
            f"Remote readiness preflight failed during credential/client setup: {type(exc).__name__}: {exc}"
        ) from None

    print(
        "Remote readiness preflight OK: credential refresh/API read succeeded; "
        f"pinned identity={channel['id']}."
    )
    return channel


if __name__ == "__main__":
    run_preflight()
