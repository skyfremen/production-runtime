import json
import os
import shutil
import tempfile
from pathlib import Path

from engine import check as dry_run


ARTIFACT_FILES = {
    "short.mp4": "preview.mp4",
    "frame-card-visible.png": "frame-card-visible.png",
    "frame-story.png": "frame-story.png",
    "story-card.png": "story-card.png",
    "branding.png": "branding.png",
    "captions.ass": "captions.ass",
    "render-metadata.json": "render-metadata.json",
}


def _artifact_dir():
    configured = os.environ.get("DRY_RUN_ARTIFACT_DIR")
    if not configured:
        raise RuntimeError("DRY_RUN_ARTIFACT_DIR must be set by the Dry Run workflow")
    return Path(configured)


def _export_visual_artifact(smoke_dir, artifact_dir, render_seconds, template_metrics, stage_counts):
    artifact_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for source_name, destination_name in ARTIFACT_FILES.items():
        source = smoke_dir / source_name
        if not source.exists():
            raise AssertionError(f"Expected Dry Run visual artifact file is missing: {source}")
        destination = artifact_dir / destination_name
        shutil.copy2(source, destination)
        exported.append(destination_name)

    summary = {
        "schema_version": 1,
        "fixture": "deterministic transformation regression",
        "validation": "passed",
        "render_seconds": round(float(render_seconds), 6),
        "template_metrics": template_metrics,
        "stage_counts": stage_counts,
        "files": exported,
        "safety": {
            "fake_tts": True,
            "local_background": True,
            "youtube_upload": False,
            "publication": False,
            "receipt_persisted": False,
            "analytics_write": False,
        },
    }
    (artifact_dir / "visual-validation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    dry_run.verify_workflow_drift_contract()
    artifact_dir = _artifact_dir()
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)

    with tempfile.TemporaryDirectory(prefix="runtime-dramas-dry-run-") as temp:
        root = Path(temp)
        first_request, stage_counts = dry_run.validate_production_shaped_batch(root)
        dry_run.verify_failure_isolation_fixture(root)
        render_seconds, template_metrics = dry_run.render_smoke(first_request, root)
        _export_visual_artifact(
            root / "render-smoke",
            artifact_dir,
            render_seconds,
            template_metrics,
            stage_counts,
        )

    print(
        "Exercise PASS: "
        "24 representative inputs used shared orchestration and explicit side-effect boundaries; "
        "one deterministic "
        "fixture then executed the actual transformation with synthetic local inputs, "
        f"passed canonical verification and template contracts ({render_seconds:.3f}s)."
    )
    print("Template metrics:", json.dumps(template_metrics, sort_keys=True))
    print("Stage counts:", json.dumps(stage_counts, sort_keys=True))
    print(f"Visual validation artifact prepared at: {artifact_dir}")


if __name__ == "__main__":
    main()
