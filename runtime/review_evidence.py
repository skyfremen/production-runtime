"""Create compact exact-source background review evidence for ChatGPT/Work.

This is a stateless public-runtime transport helper. It reads one immutable Pexels
discovery result from the private state repository at an exact SHA, derives small
representative visual evidence with ffmpeg, and writes only ephemeral artifact
files. It never approves footage, sets verified_preview, or mutates private state.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

USER_AGENT = "WackyDramas/1.0 review-evidence"
FRAME_COUNT = 5
MOTION_SAMPLE_SECONDS = 6.0
FRAME_WIDTH = 480
FFMPEG_TIMEOUT_SECONDS = 150
MAX_IMAGE_BYTES = 12 * 1024 * 1024
REQUEST_ID_RE = re.compile(r"^dr-[A-Za-z0-9._-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _pexels_https_url(value, field):
    value = str(value or "").strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname:
        raise ValueError(f"{field} must be an https URL")
    if hostname != "pexels.com" and not hostname.endswith(".pexels.com"):
        raise ValueError(f"{field} must use a pexels.com host")
    return value


def _provider_id(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError("provider_asset_id must be numeric")
    return value


def _sha256_file(path):
    digest = hashlib.sha256()
    total = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    if total <= 0:
        raise ValueError(f"empty evidence file: {path}")
    return {"path": str(path), "bytes": total, "sha256": digest.hexdigest()}


def _git_blob_sha(raw):
    return hashlib.sha1(f"blob {len(raw)}".encode() + b"\0" + raw).hexdigest()


def _read_private_json(path, source_sha):
    repository = os.environ.get("PRIVATE_STATE_REPOSITORY", "").strip()
    token = os.environ.get("PRIVATE_STATE_TOKEN", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("PRIVATE_STATE_REPOSITORY is invalid")
    if not token:
        raise ValueError("PRIVATE_STATE_TOKEN is required")
    if not SHA_RE.fullmatch(source_sha):
        raise ValueError("source_sha must be a 40-character lowercase SHA")
    url = (
        f"https://api.github.com/repos/{repository}/contents/"
        f"{quote(path, safe='/')}?ref={source_sha}"
    )
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise ValueError("private discovery result is not a base64 file")
    raw = base64.b64decode("".join(str(payload.get("content") or "").split()), validate=True)
    if payload.get("sha") != _git_blob_sha(raw):
        raise ValueError("private discovery result failed Git blob verification")
    return json.loads(raw), payload["sha"]


def _download(url, destination, max_bytes=MAX_IMAGE_BYTES):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with urlopen(request, timeout=45) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("preview image exceeds byte limit")
            digest.update(chunk)
            handle.write(chunk)
    if total <= 0:
        raise ValueError("empty preview image")
    return {"path": str(destination), "bytes": total, "sha256": digest.hexdigest()}


def representative_timestamps(duration_seconds, count=FRAME_COUNT):
    duration = float(duration_seconds)
    if duration <= 0:
        raise ValueError("duration_seconds must be positive")
    if count < 3 or count > 9:
        raise ValueError("frame count must be between 3 and 9")
    return [round(duration * (index + 1) / (count + 1), 3) for index in range(count)]


def _run_ffmpeg(args):
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg failed").strip()
        raise RuntimeError(detail[-2000:])


def _derive_visual_bundle(candidate, output_dir):
    provider_id = _provider_id(candidate.get("provider_asset_id"))
    video_url = _pexels_https_url(candidate.get("preview_video_url"), "preview_video_url")
    source_page = _pexels_https_url(candidate.get("source_page"), "source_page")
    duration = float(candidate.get("duration_seconds"))
    if duration <= 0:
        raise ValueError("duration_seconds must be positive")

    target = Path(output_dir) / provider_id
    target.mkdir(parents=True, exist_ok=True)
    timestamps = representative_timestamps(duration)
    frames = []
    for index, timestamp in enumerate(timestamps, 1):
        frame = target / f"frame-{index:02d}.jpg"
        _run_ffmpeg(
            [
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                video_url,
                "-frames:v",
                "1",
                "-vf",
                f"scale={FRAME_WIDTH}:-2",
                "-q:v",
                "5",
                str(frame),
            ]
        )
        frames.append({**_sha256_file(frame), "timestamp_seconds": timestamp})

    contact_sheet = target / "contact-sheet.jpg"
    _run_ffmpeg(
        [
            "-framerate",
            "1",
            "-i",
            str(target / "frame-%02d.jpg"),
            "-vf",
            "tile=3x2:nb_frames=5:padding=4:margin=4",
            "-frames:v",
            "1",
            str(contact_sheet),
        ]
    )

    sample_seconds = min(MOTION_SAMPLE_SECONDS, duration)
    sample_start = max(0.0, (duration - sample_seconds) / 2.0)
    motion = target / "motion-sample.mp4"
    _run_ffmpeg(
        [
            "-ss",
            f"{sample_start:.3f}",
            "-i",
            video_url,
            "-t",
            f"{sample_seconds:.3f}",
            "-vf",
            f"scale={FRAME_WIDTH}:-2",
            "-r",
            "12",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(motion),
        ]
    )

    preview_image = None
    preview_image_error = None
    image_url = candidate.get("preview_image_url")
    if image_url:
        try:
            image_url = _pexels_https_url(image_url, "preview_image_url")
            preview_image = {
                **_download(image_url, target / "provider-preview.jpg"),
                "source_url": image_url,
            }
        except Exception as exc:
            preview_image_error = str(exc)

    return {
        "provider_asset_id": provider_id,
        "source_page": source_page,
        "source_video_url": video_url,
        "source_duration_seconds": duration,
        "representative_timestamps_seconds": timestamps,
        "frames": frames,
        "contact_sheet": _sha256_file(contact_sheet),
        "motion_sample": {
            **_sha256_file(motion),
            "start_seconds": round(sample_start, 3),
            "duration_seconds": round(sample_seconds, 3),
        },
        "provider_preview": preview_image,
        "provider_preview_error": preview_image_error,
    }


def materialize(request_id, source_sha, discovery_result, output_dir):
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("request_id is invalid")
    expected_path = (
        "youtube-shorts-bot/content/background-sourcing/discovery-results/"
        f"{request_id}.json"
    )
    if discovery_result != expected_path:
        raise ValueError("discovery_result path does not match request_id")

    data, private_blob_sha = _read_private_json(discovery_result, source_sha)
    if data.get("request_id") != request_id:
        raise ValueError("discovery result request_id mismatch")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("discovery result candidates must be a non-empty list")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    evidence = []
    failures = []
    for candidate in candidates:
        provider_id = str((candidate or {}).get("provider_asset_id") or "").strip()
        try:
            evidence.append(_derive_visual_bundle(candidate, output))
        except Exception as exc:
            failures.append({"provider_asset_id": provider_id, "error": str(exc)[:2000]})

    manifest = {
        "schema_version": 1,
        "transport": "public_stateless_exact_source_review_evidence",
        "request_id": request_id,
        "source_sha": source_sha,
        "discovery_result": discovery_result,
        "discovery_result_blob_sha": private_blob_sha,
        "candidate_count": len(candidates),
        "evidence_count": len(evidence),
        "failure_count": len(failures),
        "representative_frame_count": FRAME_COUNT,
        "motion_sample_seconds": MOTION_SAMPLE_SECONDS,
        "evidence": evidence,
        "failures": failures,
        "rule": (
            "Transport/extraction only. ChatGPT/Work must inspect the actual evidence "
            "and remains the sole authority for visual/editorial approval. This workflow "
            "must never set verified_preview or mutate private planning state."
        ),
    }
    (output / "evidence-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not evidence:
        raise RuntimeError("no exact-source review evidence could be materialized")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--discovery-result", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    manifest = materialize(
        args.request_id,
        args.source_sha,
        args.discovery_result,
        args.output_dir,
    )
    print(
        json.dumps(
            {
                "request_id": manifest["request_id"],
                "candidate_count": manifest["candidate_count"],
                "evidence_count": manifest["evidence_count"],
                "failure_count": manifest["failure_count"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
