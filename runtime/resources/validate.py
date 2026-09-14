"""V1 compact trusted background registry and request validation."""
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BASE / "data" / "backgrounds.json"
EPS = 0.05


def validate_registry(data):
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise ValueError("Background registry must contain assets[]")
    seen = set()
    for index, asset in enumerate(data["assets"]):
        if not isinstance(asset, dict):
            raise ValueError(f"Background assets[{index}] must be an object")
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            raise ValueError(f"Background assets[{index}] has invalid id")
        if asset_id in seen:
            raise ValueError(f"Duplicate background ID: {asset_id}")
        seen.add(asset_id)
        if not str(asset.get("download_url") or "").strip():
            raise ValueError(f"Background {asset_id} has no download_url")
        try:
            duration = float(asset["duration_seconds"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Background {asset_id} has invalid duration_seconds") from None
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Background {asset_id} has invalid duration_seconds")
    return data


def load_registry(path=None):
    path = path or REGISTRY_PATH
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_registry(data)


def asset_map(data):
    return {str(asset["id"]): asset for asset in data.get("assets", [])}


def validate_request_backgrounds(request, registry):
    if request.get("request_version") != 1:
        raise ValueError("Only request_version 1 is supported")
    background = request.get("background") or {}
    segments = background.get("segments")
    if (
        background.get("mode") != "concatenated_fit_to_short"
        or not isinstance(segments, list)
        or len(segments) != 3
    ):
        raise ValueError("Invalid V1 background sequence")

    mapping = asset_map(registry)
    ids = []
    for segment in segments:
        background_id = str(segment.get("background_id") or "")
        ids.append(background_id)
        asset = mapping.get(background_id)
        if not asset:
            raise ValueError(f"Unknown background ID: {background_id}")
        try:
            source_duration = float(asset["duration_seconds"])
            start = float(segment["segment_start_seconds"])
            duration = float(segment["segment_duration_seconds"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Background {background_id} has invalid timing") from None
        if not all(math.isfinite(value) for value in (source_duration, start, duration)):
            raise ValueError(f"Background {background_id} has invalid timing")
        if start < 0 or duration <= 0 or start + duration > source_duration + EPS:
            raise ValueError(f"Background {background_id} selected range is invalid")

    if len(set(ids)) != 3:
        raise ValueError("V1 background IDs must be distinct")
    return segments


def main():
    data = load_registry()
    print(f"Registry PASS assets={len(data['assets'])}")


if __name__ == "__main__":
    main()
