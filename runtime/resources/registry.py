"""Planning/library-maintenance tooling for Pexels-backed logical backgrounds.

ChatGPT owns semantic cache lookup and external source discovery. When cache lookup
misses, the planner writes a small immutable sourcing manifest containing reviewed
Pexels candidates. This module is the backend ingestion boundary: it uses the
GitHub-held PEXELS_API_KEY to fetch official video_files rendition metadata,
validates the production rendition budget, and appends the new logical assets to
the checked-in cache before any TTS/render/upload work begins.
"""

import argparse
import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from resources.policy import (
    production_rendition_policy,
    rendition_is_production_suitable,
)
from base.contract import atomic_write_json

BASE = Path(__file__).resolve().parents[1]
REGISTRY_PATH = BASE / "media-library" / "backgrounds.json"
API_ROOT = "https://api.pexels.com/v1/videos"
PEXELS_LICENSE = "Pexels License"
SOURCE_MANIFEST_VERSION = 1
SOURCE_MANIFEST_MAX_CANDIDATES = 48


def now():
    return datetime.now(timezone.utc).isoformat()


def api_key():
    value = (os.getenv("RUNTIME_SOURCE_KEY") or os.getenv("PEXELS_API_KEY", "")).strip()
    if not value:
        raise RuntimeError("PEXELS_API_KEY is required for Pexels cache ingestion")
    return value


def api_get(path, key=None):
    request = Request(
        f"{API_ROOT}/{path.lstrip('/')}",
        headers={
            "Authorization": key or api_key(),
            "Accept": "application/json",
            "User-Agent": "ProductionRuntime/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def rendition_from_api(item):
    width, height = item.get("width"), item.get("height")
    direct_url = item.get("link")
    if not isinstance(width, int) or not isinstance(height, int) or not direct_url:
        return None
    result = {
        "id": str(item.get("id")),
        "width": width,
        "height": height,
        "fps": float(item["fps"]) if item.get("fps") is not None else None,
        "file_type": str(item.get("file_type") or ""),
        "quality": str(item.get("quality") or ""),
        "direct_url": str(direct_url),
    }
    size = item.get("file_size") or item.get("size")
    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
        result["file_size_bytes"] = size
    return result


def renditions_from_video(video):
    seen, renditions = set(), []
    for item in video.get("video_files", []):
        rendition = rendition_from_api(item)
        if not rendition:
            continue
        key = (rendition["id"], rendition["direct_url"])
        if key in seen:
            continue
        seen.add(key)
        renditions.append(rendition)
    return sorted(
        renditions,
        key=lambda item: (
            item["width"] * item["height"],
            float(item.get("fps") or 999),
            item["id"],
        ),
    )


def pexels_video_id(asset):
    if asset.get("provider_asset_id"):
        return str(asset["provider_asset_id"])
    for field in ("source_page", "direct_url"):
        matches = re.findall(r"(?<!\d)(\d{3,})(?!\d)", str(asset.get(field, "")))
        if matches:
            return matches[-1]
    raise ValueError(f"Cannot derive Pexels video ID for {asset.get('id')}")


def logical_id_for_pexels(video_id):
    raw = str(video_id or "").strip()
    if not raw.isdigit():
        raise ValueError("Pexels video ID must be numeric")
    return f"satisfying-px-{raw}"


def orientation(width, height):
    if width > height:
        return "horizontal"
    if height > width:
        return "vertical"
    if width == height and width:
        return "square"
    return "unknown"


def enrich_asset(asset, video, retrieved_at=None):
    if str(video.get("id")) != pexels_video_id(asset):
        raise ValueError(f"Pexels response does not match {asset['id']}")
    renditions = renditions_from_video(video)
    if not renditions:
        raise ValueError(
            f"Pexels returned no usable physical renditions for {asset['id']}"
        )
    if not any(rendition_is_production_suitable(item) for item in renditions):
        raise ValueError(
            f"Pexels returned no <=1080p production rendition within bounded crop-fill upscale for {asset['id']}"
        )
    asset["provider_asset_id"] = str(video["id"])
    asset["source_page"] = str(video.get("url") or asset["source_page"])
    asset["creator"] = str(
        video.get("user", {}).get("name") or asset.get("creator") or "Unknown"
    )
    asset["orientation"] = orientation(
        int(video.get("width") or 0), int(video.get("height") or 0)
    )
    asset["width"] = int(
        video.get("width") or max(r["width"] for r in renditions)
    )
    asset["height"] = int(
        video.get("height") or max(r["height"] for r in renditions)
    )
    if video.get("duration"):
        asset["duration_seconds"] = float(video["duration"])
    # Keep provider rendition metadata for audit/fallback ordering. Production's
    # shared policy guarantees UHD entries are never selected or downloaded.
    asset["renditions"] = renditions
    asset["renditions_retrieved_at"] = retrieved_at or now()
    asset["renditions_source"] = "Pexels API v1 video_files"
    return asset


@contextmanager
def registry_lock(path):
    lock_path = Path(path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load_registry_raw(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def enrich_existing(path=REGISTRY_PATH, key=None):
    failures, changed = [], 0
    with registry_lock(path):
        registry = load_registry_raw(path)
        registry["schema_version"] = 3
        registry["rendition_policy"] = production_rendition_policy()
        for asset in registry.get("assets", []):
            asset.setdefault("renditions", [])
            if asset.get("source") != "Pexels":
                continue
            try:
                video = api_get(f"videos/{pexels_video_id(asset)}", key=key)
                enrich_asset(asset, video)
                changed += 1
            except Exception as exc:
                failures.append(f"{asset.get('id')}: {exc}")
        atomic_write_json(path, registry)
    return changed, failures


def next_logical_id(assets):
    numbers = [
        int(match.group(1))
        for asset in assets
        if (
            match := re.fullmatch(
                r"satisfying-(\d{3,})", str(asset.get("id", ""))
            )
        )
    ]
    return f"satisfying-{max(numbers, default=0) + 1:03d}"


def _metadata_score(metadata, key):
    value = metadata.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be numeric")
    if not 0 <= number <= 100:
        raise ValueError(f"{key} must be between 0 and 100")
    return number


def _validate_metadata(metadata):
    if metadata.get("verified_preview") is not True:
        raise ValueError("New backgrounds require explicit verified_preview=true")
    if not str(metadata.get("title") or "").strip():
        raise ValueError("New background title is required")
    tags = metadata.get("visual_tags")
    if not isinstance(tags, list) or not tags or any(
        not str(tag or "").strip() for tag in tags
    ):
        raise ValueError("New background requires non-empty visual_tags")
    if not str(metadata.get("motion_type") or "").strip():
        raise ValueError("New background motion_type is required")
    if metadata.get("motion_intensity") not in {"low", "medium", "high"}:
        raise ValueError("New background motion_intensity must be low, medium, or high")
    for field in (
        "loopability_score",
        "visual_satisfaction_score",
        "caption_readability_score",
    ):
        _metadata_score(metadata, field)


def _build_asset(logical_id, video_id, metadata, video, retrieved_at):
    _validate_metadata(metadata)
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    asset = {
        "id": logical_id,
        "type": "video",
        "title": str(metadata["title"]),
        "source": "Pexels",
        "source_page": str(video.get("url") or metadata.get("source_page") or ""),
        "direct_url": f"https://www.pexels.com/download/video/{video_id}/",
        "provider_asset_id": str(video_id),
        "creator": str(video.get("user", {}).get("name") or "Unknown"),
        "license": PEXELS_LICENSE,
        "commercial_use": True,
        "attribution_required": False,
        "verified": True,
        "last_verified_at": retrieved_at,
        "status": "active",
        "orientation": orientation(width, height),
        "visual_tags": [str(tag).strip() for tag in metadata["visual_tags"]],
        "motion_type": str(metadata["motion_type"]).strip(),
        "motion_intensity": metadata["motion_intensity"],
        "loopability_score": metadata["loopability_score"],
        "visual_satisfaction_score": metadata["visual_satisfaction_score"],
        "caption_readability_score": metadata["caption_readability_score"],
        "has_embedded_text": False,
        "has_watermark": False,
        "content_verified_by": "chatgpt_planner_visual_review",
        "renditions": [],
    }
    return enrich_asset(asset, video, retrieved_at=retrieved_at)


def register_video(video_id, metadata, path=REGISTRY_PATH, key=None):
    _validate_metadata(metadata)
    video = api_get(f"videos/{video_id}", key=key)
    retrieved_at = now()
    with registry_lock(path):
        registry = load_registry_raw(path)
        assets = registry.setdefault("assets", [])
        for existing in assets:
            try:
                same_provider_id = pexels_video_id(existing) == str(video_id)
            except ValueError:
                same_provider_id = False
            if same_provider_id:
                raise ValueError(
                    f"Pexels video {video_id} is already registered as {existing['id']}"
                )
        logical_id = next_logical_id(assets)
        asset = _build_asset(
            logical_id, str(video_id), metadata, video, retrieved_at
        )
        assets.append(asset)
        registry["schema_version"] = 3
        registry["rendition_policy"] = production_rendition_policy()
        atomic_write_json(path, registry)
    return asset


def _pexels_source_page(value):
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"pexels.com", "www.pexels.com"}
        and "/video/" in parsed.path
    )


def validate_sourcing_manifest(data):
    errors = []
    if not isinstance(data, dict):
        return ["background sourcing manifest must be an object"]
    expected = {"schema_version", "plan_date", "provider", "candidates"}
    if set(data) != expected:
        errors.append(
            "background sourcing manifest must contain exactly schema_version, plan_date, provider, candidates"
        )
    if data.get("schema_version") != SOURCE_MANIFEST_VERSION:
        errors.append(f"background sourcing schema_version must be {SOURCE_MANIFEST_VERSION}")
    if data.get("provider") != "Pexels":
        errors.append("automatic background sourcing provider must be Pexels")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("plan_date") or "")):
        errors.append("background sourcing plan_date must be YYYY-MM-DD")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= SOURCE_MANIFEST_MAX_CANDIDATES:
        errors.append(
            f"background sourcing candidates must contain 1-{SOURCE_MANIFEST_MAX_CANDIDATES} entries"
        )
        return errors

    seen_ids, seen_provider = set(), set()
    expected_fields = {
        "logical_id",
        "provider_asset_id",
        "source_page",
        "title",
        "visual_tags",
        "motion_type",
        "motion_intensity",
        "loopability_score",
        "visual_satisfaction_score",
        "caption_readability_score",
        "verified_preview",
        "required_by_content_ids",
    }
    for index, candidate in enumerate(candidates):
        label = f"candidate {index}"
        if not isinstance(candidate, dict) or set(candidate) != expected_fields:
            errors.append(f"{label}: invalid fields")
            continue
        provider_id = str(candidate.get("provider_asset_id") or "").strip()
        if not provider_id.isdigit():
            errors.append(f"{label}: provider_asset_id must be numeric")
        else:
            expected_id = logical_id_for_pexels(provider_id)
            if candidate.get("logical_id") != expected_id:
                errors.append(f"{label}: logical_id must be {expected_id}")
        if candidate.get("logical_id") in seen_ids:
            errors.append(f"{label}: duplicate logical_id")
        seen_ids.add(candidate.get("logical_id"))
        if provider_id in seen_provider:
            errors.append(f"{label}: duplicate provider_asset_id")
        seen_provider.add(provider_id)
        if not _pexels_source_page(candidate.get("source_page")):
            errors.append(f"{label}: source_page must be a Pexels video page")
        try:
            _validate_metadata(candidate)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
        required_by = candidate.get("required_by_content_ids")
        if not isinstance(required_by, list) or not required_by or any(
            not str(value or "").strip() for value in required_by
        ):
            errors.append(f"{label}: required_by_content_ids must be a non-empty list")
    return errors


def ingest_manifest(manifest_path, path=REGISTRY_PATH, key=None):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    errors = validate_sourcing_manifest(manifest)
    if errors:
        raise ValueError("Background sourcing manifest invalid:\n- " + "\n- ".join(errors))

    added, cached = [], []
    retrieved_at = now()
    with registry_lock(path):
        registry = load_registry_raw(path)
        assets = registry.setdefault("assets", [])
        by_provider = {
            str(asset.get("provider_asset_id")): asset
            for asset in assets
            if asset.get("source") == "Pexels" and asset.get("provider_asset_id")
        }
        by_id = {str(asset.get("id")): asset for asset in assets}
        for candidate in manifest["candidates"]:
            provider_id = str(candidate["provider_asset_id"])
            logical_id = str(candidate["logical_id"])
            existing = by_provider.get(provider_id) or by_id.get(logical_id)
            if existing:
                if (
                    str(existing.get("provider_asset_id")) != provider_id
                    or str(existing.get("id")) != logical_id
                ):
                    raise ValueError(
                        f"Background sourcing identity collision: {logical_id} / Pexels {provider_id}"
                    )
                cached.append(logical_id)
                continue
            video = api_get(f"videos/{provider_id}", key=key)
            if str(video.get("id")) != provider_id:
                raise ValueError(f"Pexels API returned wrong asset for {logical_id}")
            asset = _build_asset(
                logical_id,
                provider_id,
                candidate,
                video,
                retrieved_at,
            )
            assets.append(asset)
            by_provider[provider_id] = asset
            by_id[logical_id] = asset
            added.append(logical_id)
        registry["schema_version"] = 3
        registry["rendition_policy"] = production_rendition_policy()
        if added:
            atomic_write_json(path, registry)
    return {"added": added, "already_cached": cached, "manifest": str(manifest_path)}


def search(query, orientation_value=None, per_page=15, key=None):
    params = f"search?query={quote_plus(query)}&per_page={int(per_page)}"
    if orientation_value:
        params += f"&orientation={quote_plus(orientation_value)}"
    return api_get(params, key=key)


def main():
    parser = argparse.ArgumentParser(
        description="Planning/backend Pexels background cache maintenance"
    )
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    enrich = sub.add_parser("enrich-existing")
    enrich.add_argument("--strict", action="store_true")
    enrich.add_argument("--require-any", action="store_true")

    find = sub.add_parser("search")
    find.add_argument("--query", required=True)
    find.add_argument("--orientation", choices=("portrait", "landscape", "square"))
    find.add_argument("--per-page", type=int, default=15)

    add = sub.add_parser("register")
    add.add_argument("--video-id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--tags", required=True)
    add.add_argument("--motion-type", required=True)
    add.add_argument(
        "--motion-intensity", choices=("low", "medium", "high"), required=True
    )
    add.add_argument("--loopability-score", type=int, required=True)
    add.add_argument("--visual-satisfaction-score", type=int, required=True)
    add.add_argument("--caption-readability-score", type=int, required=True)
    add.add_argument("--verified-preview", action="store_true")

    ingest = sub.add_parser("ingest-manifest")
    ingest.add_argument("--manifest", required=True)

    args = parser.parse_args()
    try:
        if args.command == "enrich-existing":
            changed, failures = enrich_existing(args.registry)
            print(json.dumps({"enriched": changed, "failures": failures}, indent=2))
            if args.require_any and changed == 0:
                raise SystemExit(4)
            if failures and args.strict:
                raise SystemExit(3)
        elif args.command == "search":
            print(
                json.dumps(
                    search(args.query, args.orientation, args.per_page), indent=2
                )
            )
        elif args.command == "ingest-manifest":
            print(
                json.dumps(
                    ingest_manifest(args.manifest, args.registry), indent=2
                )
            )
        else:
            metadata = {
                "title": args.title,
                "visual_tags": [
                    tag.strip() for tag in args.tags.split(",") if tag.strip()
                ],
                "motion_type": args.motion_type,
                "motion_intensity": args.motion_intensity,
                "loopability_score": args.loopability_score,
                "visual_satisfaction_score": args.visual_satisfaction_score,
                "caption_readability_score": args.caption_readability_score,
                "verified_preview": args.verified_preview,
            }
            print(
                json.dumps(
                    register_video(args.video_id, metadata, args.registry), indent=2
                )
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
