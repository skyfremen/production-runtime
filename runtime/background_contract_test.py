"""Offline tests for compact trusted background inventory and acquisition fallback."""

from resources.media import pexels_fallback_url
from resources.policy import crop_fill_geometry, rendition_is_production_suitable
from resources.resolve import _media_sources
from resources.validate import validate_registry, validate_request_backgrounds


def main():
    registry = {
        "assets": [
            {
                "id": "px-5872317",
                "category": "pov_movement",
                "duration_seconds": 162.133,
                "source_url": "https://www.pexels.com/video/example-5872317/",
                "download_url": "https://videos.pexels.com/video-files/5872317/example.mp4",
            },
            {
                "id": "px-14551003",
                "category": "city_motion",
                "duration_seconds": 63.38,
                "source_url": "https://www.pexels.com/video/example-14551003/",
                "download_url": "https://videos.pexels.com/video-files/14551003/example.mp4",
            },
            {
                "id": "px-32024102",
                "category": "city_motion",
                "duration_seconds": 87.921,
                "source_url": "https://www.pexels.com/video/example-32024102/",
                "download_url": "https://videos.pexels.com/video-files/32024102/example.mp4",
            },
        ]
    }
    validate_registry(registry)

    fallback = pexels_fallback_url(registry["assets"][0])
    assert fallback == "https://www.pexels.com/download/video/5872317"
    assert pexels_fallback_url({"id": "custom-5872317"}) is None

    sources = _media_sources(registry["assets"][0])
    assert sources == [
        (
            "registry_download_url",
            "https://videos.pexels.com/video-files/5872317/example.mp4",
            False,
        ),
        (
            "pexels_original_fallback",
            "https://www.pexels.com/download/video/5872317",
            True,
        ),
    ]

    # Suitability is determined by the actual 9:16 crop, not total source pixels.
    # A 4096x2160 landscape source yields a ~1215x2160 crop and safely downscales
    # to 1080x1920, even though its total pixel count exceeds 3840x2160.
    geometry_4096 = crop_fill_geometry(4096, 2160)
    assert round(geometry_4096["effective_crop_width"], 3) == 1215.0
    assert round(geometry_4096["effective_crop_height"], 3) == 2160.0
    assert geometry_4096["scale_factor"] < 1.0
    assert rendition_is_production_suitable(
        {"file_type": "video/mp4", "width": 4096, "height": 2160},
        max_source_pixels=3840 * 2160,
    )
    assert rendition_is_production_suitable(
        {"file_type": "video/mp4", "width": 3840, "height": 2160}
    )
    assert rendition_is_production_suitable(
        {"file_type": "video/mp4", "width": 1080, "height": 1920}
    )
    assert not rendition_is_production_suitable(
        {"file_type": "video/mp4", "width": 1920, "height": 1080}
    )

    request = {
        "request_version": 1,
        "background": {
            "mode": "concatenated_fit_to_short",
            "segments": [
                {
                    "background_id": "px-5872317",
                    "segment_start_seconds": 0.0,
                    "segment_duration_seconds": 30.0,
                },
                {
                    "background_id": "px-14551003",
                    "segment_start_seconds": 0.0,
                    "segment_duration_seconds": 30.0,
                },
                {
                    "background_id": "px-32024102",
                    "segment_start_seconds": 0.0,
                    "segment_duration_seconds": 30.0,
                },
            ],
        },
    }
    validate_request_backgrounds(request, registry)
    print("BACKGROUND_CONTRACT_TEST_PASS")


if __name__ == "__main__":
    main()
