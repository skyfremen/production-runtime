#!/usr/bin/env python3
"""Collect Wacky Dramas YouTube analytics and refresh private planner context."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WINDOW_DAYS = 30
CID_RE = re.compile(r"^wd-[0-9a-f]{24}$")
VIDEO_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ANALYTICS_METRICS = "views,engagedViews,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return dt.astimezone(timezone.utc)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def credential(name: str) -> str:
    value = str(os.environ.get(name, "")).strip()
    if not value:
        raise RuntimeError(f"Missing required secret {name}")
    return value


def access_token() -> str:
    body = urlencode({
        "client_id": credential("RUNTIME_AUTH_A"),
        "client_secret": credential("RUNTIME_AUTH_B"),
        "refresh_token": credential("RUNTIME_AUTH_C"),
        "grant_type": "refresh_token",
    }).encode()
    req = Request("https://oauth2.googleapis.com/token", data=body, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=30) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"YouTube OAuth refresh failed: {type(exc).__name__}") from None
    token = str(data.get("access_token", "")).strip()
    if not token:
        raise RuntimeError("YouTube OAuth refresh returned no access_token")
    return token


def get_json(url: str, token: str):
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(req, timeout=45) as response:
        return json.load(response)


def youtube_data(path: str, params: dict, token: str):
    return get_json("https://www.googleapis.com/youtube/v3/" + path + "?" + urlencode(params), token)


def analytics_report(params: dict, token: str):
    return get_json("https://youtubeanalytics.googleapis.com/v2/reports?" + urlencode(params), token)


def duration_seconds(value: str) -> float | None:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not m:
        return None
    h, mins, sec = m.groups()
    return round((int(h or 0) * 3600) + (int(mins or 0) * 60) + float(sec or 0), 3)


def load_creative_map(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted((root / "content" / "requests").glob("*.json")):
        try:
            doc = read_json(path)
        except Exception:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            items = doc["items"]
        elif isinstance(doc, dict) and CID_RE.fullmatch(str(doc.get("content_id", ""))):
            items = [doc]
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("content_id", ""))
            if not CID_RE.fullmatch(cid):
                continue
            story = item.get("story") if isinstance(item.get("story"), dict) else {}
            youtube = item.get("youtube") if isinstance(item.get("youtube"), dict) else {}
            out[cid] = {
                "title": str(youtube.get("title", "")),
                "premise": str(story.get("premise", "")),
                "category": str(story.get("category", "")),
                "conflict": str(story.get("conflict", "")),
                "twist": str(story.get("twist", "")),
                "hook": str(story.get("hook", "")),
                "payoff": str(story.get("punchline", "")),
                "story_tone": str(story.get("story_tone", "")),
                "lead_gender": str(story.get("lead_gender", "")),
            }
    return out


def eligible_videos(root: Path, creative: dict[str, dict], now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    rows = []
    for path in sorted((root / "content" / "results").glob("wd-*.json")):
        try:
            result = read_json(path)
            cid = str(result.get("content_id", ""))
            vid = str(result.get("youtube_video_id", ""))
            published = parse_dt(str(result.get("publish_at") or result.get("published_at") or ""))
        except Exception:
            continue
        if not CID_RE.fullmatch(cid) or not VIDEO_RE.fullmatch(vid):
            continue
        if published > now or published < cutoff:
            continue
        rows.append({
            "content_id": cid,
            "youtube_video_id": vid,
            "publish_at": iso_z(published),
            "age_hours": round((now - published).total_seconds() / 3600, 2),
            "creative": creative.get(cid, {}),
        })
    return rows


def collect_data_api(rows: list[dict], token: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for batch in chunks([r["youtube_video_id"] for r in rows], 50):
        data = youtube_data("videos", {"part": "statistics,contentDetails", "id": ",".join(batch), "maxResults": 50}, token)
        for item in data.get("items", []):
            vid = str(item.get("id", ""))
            stats = item.get("statistics") or {}
            details = item.get("contentDetails") or {}
            out[vid] = {
                "views": int(stats.get("viewCount", 0) or 0),
                "likes": int(stats.get("likeCount", 0) or 0),
                "comments": int(stats.get("commentCount", 0) or 0),
                "duration_seconds": duration_seconds(details.get("duration")),
            }
    return out


def collect_analytics_api(rows: list[dict], token: str, now: datetime) -> tuple[dict[str, dict], bool, str | None]:
    if not rows:
        return {}, True, None
    out: dict[str, dict] = {}
    start = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    end = now.date().isoformat()
    try:
        for batch_rows in chunks(rows, 200):
            ids = [r["youtube_video_id"] for r in batch_rows]
            report = analytics_report({
                "ids": "channel==MINE",
                "startDate": start,
                "endDate": end,
                "metrics": ANALYTICS_METRICS,
                "dimensions": "video",
                "filters": "video==" + ",".join(ids),
                "sort": "-views",
                "maxResults": len(ids),
            }, token)
            headers = [str(h.get("name")) for h in report.get("columnHeaders", [])]
            for values in report.get("rows", []) or []:
                row = dict(zip(headers, values))
                vid = str(row.pop("video", ""))
                if vid:
                    out[vid] = row
        return out, True, None
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return {}, False, "OAuth token lacks YouTube Analytics read access; current Data API metrics were still collected."
        raise


def snapshot(root: Path) -> dict:
    now = now_utc()
    creative = load_creative_map(root)
    rows = eligible_videos(root, creative, now)
    token = access_token()
    current = collect_data_api(rows, token)
    detailed, detailed_ok, warning = collect_analytics_api(rows, token, now)
    videos = []
    for row in rows:
        vid = row["youtube_video_id"]
        base = current.get(vid, {})
        rich = detailed.get(vid, {})
        metrics = {
            "views": base.get("views"),
            "likes": base.get("likes"),
            "comments": base.get("comments"),
            "engaged_views": rich.get("engagedViews"),
            "average_view_duration": rich.get("averageViewDuration"),
            "average_view_percentage": rich.get("averageViewPercentage"),
            "shares": rich.get("shares"),
            "subscribers_gained": rich.get("subscribersGained"),
        }
        videos.append({
            **row,
            "duration_seconds": base.get("duration_seconds"),
            "metrics": metrics,
        })
    return {
        "analytics_version": 1,
        "collected_at": iso_z(now),
        "window_days": WINDOW_DAYS,
        "detailed_analytics_available": detailed_ok,
        "warning": warning,
        "videos": videos,
    }


def all_snapshots(root: Path, current: dict) -> list[dict]:
    docs = []
    for path in sorted((root / "content" / "analytics" / "snapshots").glob("analytics-*.json")):
        try:
            d = read_json(path)
            if isinstance(d, dict) and isinstance(d.get("videos"), list):
                docs.append(d)
        except Exception:
            pass
    docs.append(current)
    return docs


def closest_checkpoint(observations: list[dict], target: float, tolerance: float = 7.0):
    candidates = [o for o in observations if abs(float(o.get("age_hours", -9999)) - target) <= tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda o: abs(float(o["age_hours"]) - target))


def med(values):
    vals = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(float(v))]
    if not vals:
        return None
    return round(statistics.median(vals), 2)


def duration_bucket(seconds):
    if not isinstance(seconds, (int, float)):
        return "unknown"
    if seconds < 120:
        return "under_120"
    if seconds < 140:
        return "120_139"
    if seconds < 160:
        return "140_159"
    if seconds <= 178:
        return "160_178"
    return "over_178"


def performance_rows(snapshots: list[dict]) -> list[dict]:
    by_cid: dict[str, list[dict]] = {}
    for snap in snapshots:
        for item in snap.get("videos", []):
            cid = str(item.get("content_id", ""))
            if CID_RE.fullmatch(cid):
                by_cid.setdefault(cid, []).append(item)
    out = []
    for cid, obs in by_cid.items():
        obs.sort(key=lambda x: float(x.get("age_hours", 0)))
        latest = obs[-1]
        p6 = closest_checkpoint(obs, 6, tolerance=4)
        p24 = closest_checkpoint(obs, 24)
        p72 = closest_checkpoint(obs, 72)
        p7d = closest_checkpoint(obs, 168, tolerance=12)
        creative = latest.get("creative") or {}
        lm = latest.get("metrics") or {}
        out.append({
            "content_id": cid,
            "title": creative.get("title", ""),
            "premise": creative.get("premise", ""),
            "category": creative.get("category", "") or "unknown",
            "story_tone": creative.get("story_tone", "") or "unknown",
            "lead_gender": creative.get("lead_gender", "") or "unknown",
            "duration_bucket": duration_bucket(latest.get("duration_seconds")),
            "duration_seconds": latest.get("duration_seconds"),
            "views_6h": (p6.get("metrics") or {}).get("views") if p6 else None,
            "views_24h": (p24.get("metrics") or {}).get("views") if p24 else None,
            "views_72h": (p72.get("metrics") or {}).get("views") if p72 else None,
            "views_7d": (p7d.get("metrics") or {}).get("views") if p7d else None,
            "retention": lm.get("average_view_percentage") if float(latest.get("age_hours", 0)) >= 72 else None,
            "subscribers_gained": lm.get("subscribers_gained") if float(latest.get("age_hours", 0)) >= 72 else None,
            "shares": lm.get("shares") if float(latest.get("age_hours", 0)) >= 72 else None,
        })
    return out


def group_summary(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "unknown"), []).append(r)
    out = {}
    for name, items in sorted(groups.items()):
        out[name] = {
            "sample_size": len(items),
            "sample_6h": sum(v.get("views_6h") is not None for v in items),
            "median_views_6h": med([v.get("views_6h") for v in items]),
            "sample_24h": sum(v.get("views_24h") is not None for v in items),
            "median_views_24h": med([v.get("views_24h") for v in items]),
            "sample_72h": sum(v.get("views_72h") is not None for v in items),
            "median_views_72h": med([v.get("views_72h") for v in items]),
            "sample_7d": sum(v.get("views_7d") is not None for v in items),
            "median_views_7d": med([v.get("views_7d") for v in items]),
            "mature_sample": sum(v.get("retention") is not None for v in items),
            "median_average_view_percentage": med([v.get("retention") for v in items]),
            "median_subscribers_gained": med([v.get("subscribers_gained") for v in items]),
            "median_shares": med([v.get("shares") for v in items]),
        }
    return out


def compact_example(row: dict) -> dict:
    return {
        "content_id": row["content_id"],
        "title": row.get("title", ""),
        "premise": row.get("premise", ""),
        "category": row.get("category", ""),
        "story_tone": row.get("story_tone", ""),
        "duration_seconds": row.get("duration_seconds"),
        "views_6h": row.get("views_6h"),
        "views_24h": row.get("views_24h"),
        "views_72h": row.get("views_72h"),
        "views_7d": row.get("views_7d"),
        "average_view_percentage": row.get("retention"),
    }


def compact_group_for_planner(group: dict) -> dict:
    out = {}
    for name, metrics in sorted((group or {}).items()):
        if not isinstance(metrics, dict):
            continue
        item = {"sample_size": int(metrics.get("sample_size", 0) or 0)}
        for label in ("6h", "24h", "72h", "7d"):
            sample = int(metrics.get(f"sample_{label}", 0) or 0)
            median = metrics.get(f"median_views_{label}")
            if sample > 0 and median is not None:
                item[f"views_{label}"] = {"sample": sample, "median": median}
        mature = int(metrics.get("mature_sample", 0) or 0)
        if mature > 0:
            mature_data = {"sample": mature}
            for source, target in (
                ("median_average_view_percentage", "average_view_percentage"),
                ("median_subscribers_gained", "subscribers_gained"),
                ("median_shares", "shares"),
            ):
                if metrics.get(source) is not None:
                    mature_data[target] = metrics[source]
            item["mature"] = mature_data
        out[name] = item
    return out


def compact_example_for_planner(example: dict) -> dict:
    keys = (
        "content_id", "title", "premise", "category", "story_tone",
        "duration_seconds", "views_6h", "views_24h", "views_72h", "views_7d", "average_view_percentage",
    )
    return {k: example[k] for k in keys if k in example and example[k] not in (None, "")}


def learning_summary(rows: list[dict]) -> dict:
    sample_6h = sum(r.get("views_6h") is not None for r in rows)
    sample_24h = sum(r.get("views_24h") is not None for r in rows)
    sample_72h = sum(r.get("views_72h") is not None for r in rows)
    sample_7d = sum(r.get("views_7d") is not None for r in rows)
    if sample_24h < 30 or sample_72h < 15:
        stage, weight, minimum = "cold_start", "low", 5
    elif sample_24h < 100 or sample_72h < 50 or sample_7d < 20:
        stage, weight, minimum = "early_learning", "medium", 8
    else:
        stage, weight, minimum = "established", "normal", 12
    return {
        "stage": stage,
        "analytics_weight": weight,
        "minimum_pattern_sample": minimum,
        "checkpoint_samples": {
            "6h": sample_6h,
            "24h": sample_24h,
            "72h": sample_72h,
            "7d": sample_7d,
        },
    }


def planner_projection(summary: dict) -> dict:
    projection = {
        "analytics_version": summary.get("analytics_version", 1),
        "generated_at": summary.get("generated_at"),
        "window_days": summary.get("window_days"),
        "videos_analyzed": summary.get("videos_analyzed", 0),
        "detailed_analytics_available": bool(summary.get("detailed_analytics_available")),
        "learning": summary.get("learning", {}),
        "category_performance": compact_group_for_planner(summary.get("category_performance", {})),
        "tone_performance": compact_group_for_planner(summary.get("tone_performance", {})),
        "lead_gender_performance": compact_group_for_planner(summary.get("lead_gender_performance", {})),
        "duration_performance": compact_group_for_planner(summary.get("duration_performance", {})),
        "top_examples": [compact_example_for_planner(x) for x in summary.get("top_examples", []) if isinstance(x, dict)],
    }
    weak = [compact_example_for_planner(x) for x in summary.get("weak_retention_examples", []) if isinstance(x, dict)]
    if weak:
        projection["weak_retention_examples"] = weak
    return projection


def build_summary(root: Path, current: dict) -> dict:
    rows = performance_rows(all_snapshots(root, current))
    rank_key = None
    for key in ("views_7d", "views_72h", "views_24h", "views_6h"):
        if sum(r.get(key) is not None for r in rows) >= 5:
            rank_key = key
            break
    if rank_key is None:
        rank_key = next((key for key in ("views_7d", "views_72h", "views_24h", "views_6h") if any(r.get(key) is not None for r in rows)), None)
    ranked = [r for r in rows if rank_key and r.get(rank_key) is not None]
    top = sorted(ranked, key=lambda r: float(r.get(rank_key) or 0), reverse=True)[:5] if rank_key else []
    mature = [r for r in rows if r.get("retention") is not None]
    weak = sorted(mature, key=lambda r: float(r.get("retention") or 0))[:5]
    categories = sorted({r.get("category", "unknown") for r in rows if r.get("category")})
    counts = {c: sum(r.get("category") == c for r in rows) for c in categories}
    summary = {
        "analytics_version": 1,
        "generated_at": current["collected_at"],
        "window_days": WINDOW_DAYS,
        "videos_analyzed": len(rows),
        "detailed_analytics_available": bool(current.get("detailed_analytics_available")),
        "learning": learning_summary(rows),
        "category_performance": group_summary(rows, "category"),
        "tone_performance": group_summary(rows, "story_tone"),
        "lead_gender_performance": group_summary(rows, "lead_gender"),
        "duration_performance": group_summary(rows, "duration_bucket"),
        "top_examples": [compact_example(r) for r in top],
        "weak_retention_examples": [compact_example(r) for r in weak],
        "low_sample_categories": [{"category": c, "sample_size": counts[c]} for c in categories if counts[c] < 5],
    }
    return planner_projection(summary)


def patch_context(root: Path, summary: dict) -> None:
    path = root / "content" / "context.json"
    context = read_json(path) if path.exists() else {}
    if not isinstance(context, dict):
        raise RuntimeError("content/context.json must be an object")
    context["context_version"] = max(int(context.get("context_version", 1)), 2)
    context["analytics_summary"] = summary
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(raw.encode()) > 60000:
        raise RuntimeError("Planner context exceeds 60000 bytes after analytics summary")
    path.write_text(raw, encoding="utf-8")


def run(root: Path) -> tuple[Path, Path, Path]:
    current = snapshot(root)
    stamp = current["collected_at"].replace("-", "").replace(":", "")
    snap_path = root / "content" / "analytics" / "snapshots" / f"analytics-{stamp}.json"
    write_json(snap_path, current)
    summary = build_summary(root, current)
    summary_path = root / "content" / "analytics-summary.json"
    write_json(summary_path, summary)
    patch_context(root, summary)
    print(f"Analytics PASS videos={len(current['videos'])} detailed={current['detailed_analytics_available']}")
    if current.get("warning"):
        print(f"::warning::{current['warning']}")
    return snap_path, summary_path, root / "content" / "context.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", required=True)
    args = ap.parse_args()
    try:
        run(Path(args.state_dir).resolve())
    except Exception as exc:
        print(f"::error::ANALYTICS_FAILED {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
