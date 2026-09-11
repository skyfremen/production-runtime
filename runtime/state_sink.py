"""Write one validated observation snapshot to the canonical private state."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

TARGET = ".state/observations/latest.json"


class SinkError(RuntimeError):
    pass


def _blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def _request(endpoint, *, method="GET", body=None):
    repository = os.environ["PRIVATE_STATE_REPOSITORY"]
    token = os.environ["PRIVATE_STATE_TOKEN"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SinkError("Invalid private state configuration")
    url = f"https://api.github.com/repos/{repository}/{endpoint}"
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as exc:
        if method == "GET" and exc.code == 404:
            return None
        raise SinkError(f"Private state operation failed ({exc.code})") from None
    except (URLError, TimeoutError):
        raise SinkError("Private state operation failed (network)") from None


def _validate(payload):
    if payload.get("schema_version") != 1:
        raise SinkError("Invalid observation schema")
    if not isinstance(payload.get("captured_at"), str):
        raise SinkError("Invalid observation timestamp")
    window = payload.get("window")
    if not isinstance(window, dict) or not window.get("start_date") or not window.get("end_date"):
        raise SinkError("Invalid observation window")
    for key in ("aggregate", "recent"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            raise SinkError("Invalid observation rows")
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("video", "")).strip():
                raise SinkError("Invalid observation row")
    return payload


def store(path):
    payload = _validate(json.loads(Path(path).read_text(encoding="utf-8")))
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    encoded = quote(TARGET, safe="/")
    current = _request(f"contents/{encoded}?ref=main")
    if current is not None:
        current_raw = base64.b64decode(
            "".join(str(current.get("content", "")).split()), validate=True
        )
        if _blob_sha(current_raw) != current.get("sha"):
            raise SinkError("Private state integrity check failed")
        if current_raw == raw:
            print("Store PASS unchanged")
            return
    body = {
        "branch": "main",
        "message": "[observation] refresh runtime snapshot",
        "content": base64.b64encode(raw).decode(),
    }
    if current is not None:
        body["sha"] = current["sha"]
    _request(f"contents/{encoded}", method="PUT", body=body)
    print("Store PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/tmp/runtime-observation.json")
    args = parser.parse_args()
    store(args.input)


if __name__ == "__main__":
    try:
        main()
    except (SinkError, KeyError, OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("E_STATE_002") from None
