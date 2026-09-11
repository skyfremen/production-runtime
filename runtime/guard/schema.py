import argparse
import json
from datetime import datetime
from pathlib import Path

from profile.config import (
    ANTAGONIST_ROLES,
    CANONICAL_TIMEZONE,
    CATEGORIES,
    EDITORIAL_WEIGHTS,
    EMOTIONS,
    ENDING_STYLES,
    OPENING_STYLES,
    PROTAGONIST_ROLES,
    TITLE_STYLES,
    TITLE_WEIGHTS,
)
from base.contract import ensure_request_path_matches, load_json

SCHEMA_VERSION = 4
SUPPORTED_SCHEMA_VERSIONS = {3, 4}
FORBIDDEN_KEYS = {
    "setup", "payoff", "cta", "comedy_mechanism", "series_role", "series_id",
    "series_title", "part_number", "part_total", "next_part_slot", "duration_seconds",
    "background_url", "background_source_url", "background_license", "background_creator",
    "privacy_status", "publishAt", "publish_at", "music_url", "music_source_url",
    "music_title", "music_artist", "music_license", "queue_slot",
}
TOP_LEVEL_KEYS = {
    "schema_version", "content_id", "channel", "story", "narration", "visual",
    "youtube", "publication", "planning",
}
STORY_KEYS = {"category", "story_type", "hook", "script", "card_emojis"}
STORY_V4_KEYS = STORY_KEYS | {"lead_gender", "story_tone"}
NARRATION_KEYS = {"engine", "voice", "speed"}
VISUAL_KEYS = {"background_primary_id", "background_backup_id"}
YOUTUBE_KEYS = {"title", "description", "hashtags", "tags", "category_id", "made_for_kids"}
PUBLICATION_KEYS = {"mode", "timezone", "publish_at"}
PLANNING_KEYS = {
    "plan_date", "editorial_score", "editorial_components", "analytics_score",
    "analytics_weight", "final_score", "title_candidates", "selected_title_score",
    "hook_score", "selection_class", "selection_reason", "similarity", "attributes",
    "target_duration_seconds",
}
ATTRIBUTE_KEYS = {
    "subtype", "conflict", "primary_emotion", "protagonist_role", "antagonist_role",
    "opening_style", "title_style", "ending_style",
}
TITLE_CANDIDATE_KEYS = {"title", "style", "truthful", "score", "score_components"}
RETIRED_NAME = "Wa" + "cky " + "Insights"
RETIRED_HANDLE = "@WA" + "CKY" + "INSIGHTS"
RETIRED_HASHTAG = "#wa" + "cky" + "insights"
RETIRED_BRANDING = (RETIRED_HANDLE, RETIRED_HASHTAG, RETIRED_NAME)
LEAD_GENDERS = {"female", "male"}
NATURAL_TONES = {"natural", "general", "conversational", "warm", "calm"}
EXPRESSIVE_TONES = {"comedy", "dramatic", "sarcastic", "dramatic_comedy", "absurd", "expressive"}
STORY_TONES = NATURAL_TONES | EXPRESSIVE_TONES
APPROVED_VOICES = {"af_heart", "af_bella", "am_echo", "am_fenrir"}


def expected_voice(lead_gender, story_tone):
    if lead_gender not in LEAD_GENDERS or story_tone not in STORY_TONES:
        return None
    expressive = story_tone in EXPRESSIVE_TONES
    if lead_gender == "female":
        return "af_bella" if expressive else "af_heart"
    return "am_fenrir" if expressive else "am_echo"


def _nonempty(value):
    return bool(str(value or "").strip())


def _score(value, label, errors, allow_none=False):
    if value is None and allow_none:
        return
    try:
        value = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be numeric" + (" or null" if allow_none else ""))
        return
    if not 0 <= value <= 100:
        errors.append(f"{label} must be between 0 and 100")


def _validate_component_scores(components, expected_keys, label, errors):
    if not isinstance(components, dict) or set(components) != set(expected_keys):
        errors.append(f"{label} must contain exactly the configured score components")
        return
    for key in expected_keys:
        _score(components.get(key), f"{label}.{key}", errors)


def _validate_publication(publication, errors):
    if not isinstance(publication, dict) or set(publication) != PUBLICATION_KEYS:
        errors.append("publication must contain exactly mode, timezone, publish_at")
        return
    if publication.get("mode") != "scheduled":
        errors.append("publication.mode must be scheduled")
    if publication.get("timezone") != CANONICAL_TIMEZONE:
        errors.append(f"publication.timezone must be {CANONICAL_TIMEZONE}")
    raw = str(publication.get("publish_at", ""))
    if not raw.endswith("Z"):
        errors.append("publication.publish_at must be an RFC3339 UTC timestamp ending Z")
        return
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
    except ValueError:
        errors.append("publication.publish_at must be a valid UTC timestamp")


def _validate_planning(planning, yt_title, errors):
    if not isinstance(planning, dict):
        errors.append("planning must be an object")
        return
    missing, extra = PLANNING_KEYS - set(planning), set(planning) - PLANNING_KEYS
    if missing:
        errors.append("planning missing fields: " + ", ".join(sorted(missing)))
    if extra:
        errors.append("planning unexpected fields: " + ", ".join(sorted(extra)))
    for key in ("editorial_score", "final_score", "selected_title_score", "hook_score"):
        _score(planning.get(key), f"planning.{key}", errors)
    _score(planning.get("analytics_score"), "planning.analytics_score", errors, allow_none=True)
    _validate_component_scores(
        planning.get("editorial_components"), EDITORIAL_WEIGHTS,
        "planning.editorial_components", errors,
    )
    try:
        analytics_weight = float(planning.get("analytics_weight"))
        if not 0 <= analytics_weight <= 0.75:
            errors.append("planning.analytics_weight must be between 0 and 0.75")
    except (TypeError, ValueError):
        errors.append("planning.analytics_weight must be numeric")
    if planning.get("selection_class") not in {"exploit", "explore"}:
        errors.append("planning.selection_class must be exploit or explore")
    if not _nonempty(planning.get("selection_reason")):
        errors.append("planning.selection_reason must be non-empty")

    similarity = planning.get("similarity")
    if not isinstance(similarity, dict) or "max_recent_similarity" not in similarity:
        errors.append("planning.similarity must record max_recent_similarity")
    else:
        try:
            value = float(similarity["max_recent_similarity"])
            if not 0 <= value <= 1:
                errors.append("planning.similarity.max_recent_similarity must be between 0 and 1")
        except (TypeError, ValueError):
            errors.append("planning.similarity.max_recent_similarity must be numeric")

    try:
        duration = float(planning.get("target_duration_seconds"))
        if not 120 <= duration <= 175:
            errors.append("planning.target_duration_seconds must be between 120 and 175")
    except (TypeError, ValueError):
        errors.append("planning.target_duration_seconds must be numeric")

    attrs = planning.get("attributes")
    if not isinstance(attrs, dict) or set(attrs) != ATTRIBUTE_KEYS:
        errors.append("planning.attributes must contain the controlled attribute fields")
    else:
        if not _nonempty(attrs.get("subtype")) or not _nonempty(attrs.get("conflict")):
            errors.append("planning subtype/conflict must be non-empty")
        if attrs.get("primary_emotion") not in EMOTIONS:
            errors.append("planning.attributes.primary_emotion is not a controlled value")
        if attrs.get("protagonist_role") not in PROTAGONIST_ROLES:
            errors.append("planning.attributes.protagonist_role is not a controlled value")
        if attrs.get("antagonist_role") not in ANTAGONIST_ROLES:
            errors.append("planning.attributes.antagonist_role is not a controlled value")
        if attrs.get("opening_style") not in OPENING_STYLES:
            errors.append("planning.attributes.opening_style is not a controlled value")
        if attrs.get("title_style") not in TITLE_STYLES:
            errors.append("planning.attributes.title_style is not a controlled value")
        if attrs.get("ending_style") not in ENDING_STYLES:
            errors.append("planning.attributes.ending_style is not a controlled value")

    titles = planning.get("title_candidates")
    if not isinstance(titles, list) or len(titles) < 5:
        errors.append("planning.title_candidates must contain at least 5 candidates")
    else:
        selected_seen = False
        styles_seen = set()
        for index, item in enumerate(titles):
            if not isinstance(item, dict) or set(item) != TITLE_CANDIDATE_KEYS:
                errors.append(f"planning.title_candidates[{index}] has invalid fields")
                continue
            if item.get("truthful") is not True:
                errors.append(f"planning.title_candidates[{index}] must be truthful")
            style = item.get("style")
            if style not in TITLE_STYLES:
                errors.append(f"planning.title_candidates[{index}].style is not controlled")
            styles_seen.add(style)
            _score(item.get("score"), f"planning.title_candidates[{index}].score", errors)
            _validate_component_scores(
                item.get("score_components"), TITLE_WEIGHTS,
                f"planning.title_candidates[{index}].score_components", errors,
            )
            if item.get("title") == yt_title:
                selected_seen = True
        if len(styles_seen) < 3:
            errors.append("planning.title_candidates must explore at least 3 materially different title styles")
        if not selected_seen:
            errors.append("youtube.title must exactly match one planning.title_candidates entry")


def validate_request_data(data, request_path=None):
    errors = []
    if not isinstance(data, dict):
        return ["request root must be an object"]
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append("schema_version must be 3 or 4")

    missing_top, extra_top = TOP_LEVEL_KEYS - set(data), set(data) - TOP_LEVEL_KEYS
    if missing_top:
        errors.append("request missing fields: " + ", ".join(sorted(missing_top)))
    if extra_top:
        errors.append("unexpected top-level fields: " + ", ".join(sorted(extra_top)))
    forbidden = set(data) & FORBIDDEN_KEYS
    if forbidden:
        errors.append("obsolete/forbidden fields present: " + ", ".join(sorted(forbidden)))

    _validate_publication(data.get("publication"), errors)

    channel = data.get("channel")
    if not isinstance(channel, dict):
        errors.append("channel must be an object")
    else:
        if set(channel) != {"name", "handle"}:
            errors.append("channel must contain exactly name and handle")
        if channel.get("name") != "Wa" + "cky" + " Dramas":
            errors.append("channel.name does not match the pinned identity")
        if channel.get("handle") != "@WA" + "CKY" + "DRAMAS":
            errors.append("channel.handle does not match the pinned identity")

    story = data.get("story")
    if not isinstance(story, dict):
        errors.append("story must be an object")
    else:
        required_story_keys = STORY_V4_KEYS if schema_version == 4 else STORY_KEYS
        missing, extra = required_story_keys - set(story), set(story) - required_story_keys
        if missing:
            errors.append("story missing fields: " + ", ".join(sorted(missing)))
        if extra:
            errors.append("story unexpected fields: " + ", ".join(sorted(extra)))
        for key in ("category", "story_type", "hook", "script"):
            if not _nonempty(story.get(key)):
                errors.append(f"story.{key} must be non-empty")
        hook = str(story.get("hook") or "").strip()
        script = str(story.get("script") or "").strip()
        if hook and script.startswith(hook):
            errors.append("story.script must not repeat story.hook at the beginning")
        if story.get("category") not in CATEGORIES:
            errors.append("story.category is not a controlled planning category")
        emojis = story.get("card_emojis")
        if not isinstance(emojis, list) or not 4 <= len(emojis) <= 6:
            errors.append("story.card_emojis must contain 4-6 emojis")
        elif any(not _nonempty(x) for x in emojis):
            errors.append("story.card_emojis entries must be non-empty")
        if any(term.lower() in str(story.get("script", "")).lower() for term in RETIRED_BRANDING):
            errors.append("story script contains obsolete channel branding")
        if schema_version == 4:
            if story.get("lead_gender") not in LEAD_GENDERS:
                errors.append("story.lead_gender must be female or male")
            if story.get("story_tone") not in STORY_TONES:
                errors.append("story.story_tone is not an approved controlled tone")

    narration = data.get("narration")
    if not isinstance(narration, dict):
        errors.append("narration must be an object")
    else:
        if set(narration) != NARRATION_KEYS:
            errors.append("narration must contain exactly engine, voice, speed")
        if narration.get("engine") != "kokoro":
            errors.append("narration.engine must be kokoro")
        voice = narration.get("voice")
        if voice not in APPROVED_VOICES:
            errors.append("narration.voice is not in the approved voice pool")
        elif schema_version == 3 and voice != "af_heart":
            errors.append("legacy schema-v3 narration.voice must be af_heart")
        elif schema_version == 4 and isinstance(story, dict):
            required_voice = expected_voice(story.get("lead_gender"), story.get("story_tone"))
            if required_voice and voice != required_voice:
                errors.append(
                    f"narration.voice must be {required_voice} for the frozen lead gender and tone"
                )
        try:
            speed = float(narration.get("speed"))
            if abs(speed - 1.75) > 0.001:
                errors.append("narration.speed must be 1.75")
        except (TypeError, ValueError):
            errors.append("narration.speed must be numeric")

    visual = data.get("visual")
    if not isinstance(visual, dict):
        errors.append("visual must be an object")
    else:
        if set(visual) != VISUAL_KEYS:
            errors.append("visual must contain exactly background_primary_id and background_backup_id")
        primary = str(visual.get("background_primary_id", "")).strip()
        backup = str(visual.get("background_backup_id", "")).strip()
        if not primary or not backup:
            errors.append("visual primary and backup background IDs are required")
        elif primary == backup:
            errors.append("visual primary and backup background IDs must differ")

    youtube = data.get("youtube")
    youtube_title = ""
    if not isinstance(youtube, dict):
        errors.append("youtube must be an object")
    else:
        missing, extra = YOUTUBE_KEYS - set(youtube), set(youtube) - YOUTUBE_KEYS
        if missing:
            errors.append("youtube missing fields: " + ", ".join(sorted(missing)))
        if extra:
            errors.append("youtube unexpected fields: " + ", ".join(sorted(extra)))
        youtube_title = str(youtube.get("title", "")).strip()
        if not youtube_title or len(youtube_title) > 100 or "#Shorts" not in youtube_title:
            errors.append("youtube.title must be non-empty, <=100 chars, and contain #Shorts")
        if not _nonempty(youtube.get("description")):
            errors.append("youtube.description must be non-empty")
        hashtags = youtube.get("hashtags")
        if not isinstance(hashtags, list) or not 3 <= len(hashtags) <= 8:
            errors.append("youtube.hashtags must contain 3-8 entries")
        elif any(not str(x).strip().startswith("#") for x in hashtags):
            errors.append("youtube.hashtags entries must start with #")
        semantic_tags = youtube.get("tags")
        if not isinstance(semantic_tags, list) or not 4 <= len(semantic_tags) <= 12:
            errors.append("youtube.tags must contain 4-12 concise semantic tags")
        elif any(not _nonempty(x) or str(x).strip().startswith("#") for x in semantic_tags):
            errors.append("youtube.tags entries must be non-empty backend keywords without #")
        elif len({str(x).strip().lower() for x in semantic_tags}) != len(semantic_tags):
            errors.append("youtube.tags must not contain case-insensitive duplicates")
        if not _nonempty(youtube.get("category_id")):
            errors.append("youtube.category_id must be non-empty")
        if youtube.get("made_for_kids") is not False:
            errors.append("youtube.made_for_kids must be false for this workflow")

    _validate_planning(data.get("planning"), youtube_title, errors)

    serialized = json.dumps(data, ensure_ascii=False)
    for term in RETIRED_BRANDING:
        if term.lower() in serialized.lower():
            errors.append("request contains obsolete channel branding")
    if request_path and not errors:
        try:
            ensure_request_path_matches(request_path, data)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def validate_request(path):
    path = Path(path)
    data = load_json(path)
    errors = validate_request_data(data, request_path=path)
    if errors:
        raise SystemExit("Input validation failed:\n- " + "\n- ".join(errors))
    print(f"Request valid: {path.name}; schema={data['schema_version']}")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    validate_request(args.request)


if __name__ == "__main__":
    main()
