"""Cheap read-only YouTube readiness preflight for production workflows.

This deliberately reuses the production OAuth/client/channel code. It never
creates or mutates a YouTube resource; one channels.list request proves token
refresh, Data API access, and the pinned production channel before expensive
media/TTS/render work begins.
"""
import os
import socket

from publishing.recovery_state import RecoveryBlocked
from publishing.upload import authenticated_channel, make_client

REQUIRED = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")


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
        kind = "transient YouTube API"
    else:
        kind = "YouTube API"
    return f"{kind} failure" + (f" (HTTP {status})" if status is not None else "")


def run_preflight():
    missing = [name for name in REQUIRED if not str(os.environ.get(name, "")).strip()]
    if missing:
        raise SystemExit("YouTube credential preflight failed: missing " + ", ".join(missing))

    try:
        channel = authenticated_channel(make_client())
    except RecoveryBlocked as exc:
        raise SystemExit(f"YouTube channel readiness preflight failed: {exc}") from None
    except (TimeoutError, OSError, socket.timeout) as exc:
        raise SystemExit(
            f"YouTube readiness preflight failed: transient network failure ({type(exc).__name__})"
        ) from None
    except Exception as exc:
        # googleapiclient HttpError exposes resp.status. Avoid importing the Google
        # package at module import time so this tiny guard remains unit-testable
        # in the lightweight dry-run environment.
        if getattr(getattr(exc, "resp", None), "status", None) is not None:
            raise SystemExit(f"YouTube readiness preflight failed: {_http_detail(exc)}") from None
        # OAuth refresh failures commonly surface through google-auth exceptions;
        # preserve the type without leaking credential values.
        raise SystemExit(
            f"YouTube readiness preflight failed during OAuth/client setup: {type(exc).__name__}: {exc}"
        ) from None

    print(
        "YouTube readiness preflight OK: OAuth refresh/Data API read succeeded; "
        f"pinned channel={channel['id']}."
    )
    return channel


if __name__ == "__main__":
    run_preflight()

