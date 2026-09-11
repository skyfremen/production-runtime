"""Planning-time background cache safety, recency and shortlist helpers.

The canonical daily planner (ChatGPT) owns semantic story-to-background judgment.
It must inspect the checked-in cache first and only source externally when the
cache cannot supply two genuinely suitable fresh assets. This module provides
mechanical quality/recency/rendition checks and a deterministic shortlist; it is
not a replacement for the AI's semantic review.
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from resources.policy import rendition_is_production_suitable
from resources.validate import REGISTRY_PATH, asset_map, load_registry

BASE = Path(__file__).resolve().parents[1]
RESULTS_DIR = BASE / "content" / "results"

HARD_AVOID_SHORTS = 10
RECENCY_PENALTY_SHORTS = 30
MIN_SEMANTIC_SCORE = 0.45
MIN_QUALITY_SCORE = 0.78
MIN_FRESH_STRONG_CANDIDATES = 2
NEVER_USED_BONUS = 8.0


def _normal(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _timestamp(record):
    raw = record.get("receipt_created_at") or record.get("youtube_verified_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def is_successful_receipt(record):
    """Count canonical verified public, historical private and scheduled successes."""
    if not (
        isinstance(record, dict)
        and record.get("background_asset_id")
        and record.get("verification", {}).get("passed") is True
    ):
        return False
    state = record.get("verification_state")
    if state == "verified_public":
        return bool(
            record.get("privacy_status") == "public"
            and record.get("publish_at_absent") is True
        )
    if state == "verified_private":
        return bool(
            record.get("privacy_status") == "private"
            and record.get("publish_at_absent") is True
        )
    if state in {"verified_scheduled", "verified_scheduled_published"}:
        return bool(
            record.get("publication_mode") == "scheduled"
            and record.get("publish_at")
            and record.get("privacy_status") in {"private", "public"}
        )
    return False


def load_successful_receipts(results_dir=RESULTS_DIR):
    records = []
    for path in Path(results_dir).glob("*.json") if Path(results_dir).exists() else ():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if is_successful_receipt(record):
            records.append(record)
    return sorted(
        records,
        key=lambda item: (_timestamp(item), item.get("content_id", "")),
        reverse=True,
    )


def derive_usage_history(receipts):
    """Return immutable-receipt-derived recency; index zero is the latest Short."""
    ordered = sorted(
        (record for record in receipts if is_successful_receipt(record)),
        key=lambda item: (_timestamp(item), item.get("content_id", "")),
        reverse=True,
    )
    history = {}
    counts = defaultdict(int)
    for shorts_ago, record in enumerate(ordered):
        asset_id = record["background_asset_id"]
        counts[asset_id] += 1
        if asset_id not in history:
            history[asset_id] = {
                "shorts_ago": shorts_ago,
                "last_content_id": record.get("content_id"),
                "last_receipt_created_at": record.get("receipt_created_at"),
            }
    for asset_id, count in counts.items():
        history[asset_id]["successful_use_count"] = count
    return history


def semantic_score(asset, requirements):
    """Cheap metadata score used for cache shortlist ordering, not AI truth."""
    required_tags = {
        _normal(tag) for tag in requirements.get("visual_tags", []) if _normal(tag)
    }
    asset_tags = {
        _normal(tag) for tag in asset.get("visual_tags", []) if _normal(tag)
    }
    asset_tags.update(_normal(asset.get("title", "")).split("-"))
    if required_tags:
        tag_score = len(required_tags & asset_tags) / len(required_tags)
    else:
        tag_score = 0.65

    motion_type = _normal(requirements.get("motion_type"))
    type_score = 1.0 if not motion_type else float(
        motion_type == _normal(asset.get("motion_type"))
    )
    intensity = _normal(requirements.get("motion_intensity"))
    intensity_score = 1.0 if not intensity else float(
        intensity == _normal(asset.get("motion_intensity"))
    )
    orientation = _normal(requirements.get("orientation"))
    orientation_score = 1.0 if not orientation else float(
        orientation == _normal(asset.get("orientation"))
    )
    return round(
        0.70 * tag_score
        + 0.12 * type_score
        + 0.10 * intensity_score
        + 0.08 * orientation_score,
        4,
    )


def quality_score(asset):
    values = (
        float(asset.get("visual_satisfaction_score") or 0),
        float(asset.get("loopability_score") or 0),
        float(asset.get("caption_readability_score") or 0),
    )
    return round((values[0] * 0.40 + values[1] * 0.25 + values[2] * 0.35) / 100.0, 4)


def _has_production_rendition(asset):
    return any(rendition_is_production_suitable(r) for r in asset.get("renditions", []))


def rank_assets(registry, requirements, receipts):
    usage = derive_usage_history(receipts)
    ranked = []
    for asset in registry.get("assets", []):
        if asset.get("status") != "active" or asset.get("verified") is not True:
            continue
        semantic = semantic_score(asset, requirements)
        quality = quality_score(asset)
        recent = usage.get(asset["id"])
        shorts_ago = recent.get("shorts_ago") if recent else None
        never_used = recent is None
        hard_avoided = shorts_ago is not None and shorts_ago < HARD_AVOID_SHORTS
        if hard_avoided:
            recency_penalty = 1000.0
        elif shorts_ago is None:
            recency_penalty = -NEVER_USED_BONUS
        elif shorts_ago < 20:
            recency_penalty = 48.0 - (shorts_ago - 10) * 2.0
        elif shorts_ago < RECENCY_PENALTY_SHORTS:
            recency_penalty = 24.0 - (shorts_ago - 20) * 2.0
        else:
            recency_penalty = 0.0
        rendition_ready = _has_production_rendition(asset)
        strong_match = bool(
            semantic >= MIN_SEMANTIC_SCORE
            and quality >= MIN_QUALITY_SCORE
            and rendition_ready
        )
        base = semantic * 62.0 + quality * 38.0
        ranked.append({
            **asset,
            "semantic_score": semantic,
            "quality_score": quality,
            "base_score": round(base, 3),
            "recency_penalty": round(recency_penalty, 3),
            "score": round(base - recency_penalty, 3),
            "shorts_ago": shorts_ago,
            "never_used": never_used,
            "hard_avoided": hard_avoided,
            "rendition_ready": rendition_ready,
            "strong_match": strong_match,
        })
    return sorted(ranked, key=lambda item: (item["score"], item["quality_score"]), reverse=True)


def select_logical_backgrounds(registry, requirements, receipts):
    ranked = rank_assets(registry, requirements, receipts)
    fresh = [item for item in ranked if item["strong_match"] and not item["hard_avoided"]]
    if len(fresh) < MIN_FRESH_STRONG_CANDIDATES:
        return {
            "primary": None,
            "backup": None,
            "expansion_required": True,
            "reason": "fewer than two strong fresh cache backgrounds",
            "ranked_candidates": ranked,
        }
    return {
        "primary": fresh[0]["id"],
        "backup": fresh[1]["id"],
        "expansion_required": False,
        "reason": "cache supplied at least two strong fresh backgrounds",
        "ranked_candidates": ranked,
    }


def audit_ai_selection(registry, primary_id, backup_id, receipts, requirements=None):
    """Mechanical gate for IDs chosen semantically by the AI planner."""
    requirements = requirements or {}
    assets = asset_map(registry)
    errors = []
    if primary_id == backup_id:
        errors.append("primary and backup background IDs must differ")
    ranked = {item["id"]: item for item in rank_assets(registry, requirements, receipts)}
    selected = []
    for label, asset_id in (("primary", primary_id), ("backup", backup_id)):
        asset = assets.get(asset_id)
        if not asset:
            errors.append(f"{label} background {asset_id} is missing from cache")
            continue
        audit = ranked.get(asset_id)
        if not audit:
            errors.append(f"{label} background {asset_id} is not active and verified")
            continue
        if audit["quality_score"] < MIN_QUALITY_SCORE:
            errors.append(f"{label} background {asset_id} is below the quality floor")
        if audit["hard_avoided"]:
            errors.append(f"{label} background {asset_id} was used within the last 10 Shorts")
        if not audit["rendition_ready"]:
            errors.append(f"{label} background {asset_id} has no qualifying post-crop 1080x1920 rendition")
        selected.append((label, audit))
    return {
        "passed": not errors,
        "errors": errors,
        "primary": next((x for label, x in selected if label == "primary"), None),
        "backup": next((x for label, x in selected if label == "backup"), None),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements")
    parser.add_argument("--primary-id")
    parser.add_argument("--backup-id")
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args()
    registry = load_registry(args.registry)
    receipts = load_successful_receipts(args.results_dir)
    requirements = {}
    if args.requirements:
        requirements = json.loads(Path(args.requirements).read_text(encoding="utf-8"))
    if args.primary_id or args.backup_id:
        if not args.primary_id or not args.backup_id:
            raise SystemExit("Both --primary-id and --backup-id are required for AI-selection audit")
        result = audit_ai_selection(registry, args.primary_id, args.backup_id, receipts, requirements)
    else:
        result = select_logical_backgrounds(registry, requirements, receipts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("expansion_required") or result.get("passed") is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
