#!/usr/bin/env python3
"""Deterministic Zodiac draft validator. ChatGPT owns all creative choices.

No network, model, secrets, music, YouTube, analytics or background downloads.
"""
from __future__ import annotations
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys

SIGNS = ("aries", "taurus", "gemini", "cancer", "leo", "virgo", "libra",
         "scorpio", "sagittarius", "capricorn", "aquarius", "pisces")
ELEMENTS = {
    "fire": ("aries", "leo", "sagittarius"),
    "earth": ("taurus", "virgo", "capricorn"),
    "air": ("gemini", "libra", "aquarius"),
    "water": ("cancer", "scorpio", "pisces"),
}
FORMATS = ("sign_results", "ranking", "grouped_elements", "trait_matches")
SCORES = {"hook": 30, "curiosity": 25, "emotion": 20, "answers": 15, "originality": 10}
DRAFT_FIELDS = {"winner_count", "winners"}
WINNER_FIELDS = {"id", "format", "title", "duration_seconds",
                 "editorial_scores", "editorial_reason", "rows"}
ROW_FIELDS = {"label", "answer"}
MAX_DRAFT_BYTES = 250_000

class Rejected(ValueError):
    pass

def require(condition, explanation):
    if not condition:
        raise Rejected(explanation)

def plain(value, low=1, high=200):
    return (isinstance(value, str) and low <= len(value.strip()) <= high
            and not any(ord(c) < 32 for c in value))

def norm(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))

def read_json(path):
    p = Path(path)
    require(p.is_file() and p.stat().st_size <= MAX_DRAFT_BYTES, "MISSING_OR_OVERSIZED_DRAFT")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as err:
        raise Rejected("INVALID_JSON") from err

def previous_items(draft_path, root):
    past = []
    for file in sorted(Path(root).glob("draft-*.json")):
        if file.resolve() == Path(draft_path).resolve():
            continue
        past_draft = read_json(file)
        if not isinstance(past_draft, dict):
            continue
        for item in past_draft.get("winners", []):
            if isinstance(item, dict) and isinstance(item.get("title"), str) and isinstance(item.get("id"), str):
                past.append(item)
    return past

def validate(draft, previous=()):
    require(type(draft) is dict and set(draft) == DRAFT_FIELDS, "DRAFT_SCHEMA")
    n = draft["winner_count"]
    require(type(n) is int and 1 <= n <= 10, "WINNER_COUNT")
    winners = draft["winners"]
    require(type(winners) is list and len(winners) == n, "WINNER_COUNT_MISMATCH")
    ids, titles, signatures = set(), [], []
    for index, w in enumerate(winners):
        tag = f"WINNER_{index+1}"
        require(type(w) is dict and set(w) == WINNER_FIELDS, tag + "_SCHEMA")
        cid = w["id"]
        require(plain(cid) and re.fullmatch(r"za-[a-z0-9-]{8,64}", cid)
                and cid not in ids, tag + "_ID")
        ids.add(cid)
        require(w["format"] in FORMATS, tag + "_FORMAT")
        title = w["title"]
        require(plain(title, 12, 90) and 3 <= len(title.split()) <= 18, tag + "_HOOK")
        require(type(w["duration_seconds"]) in (int, float) and
                6 <= w["duration_seconds"] <= 25, tag + "_DURATION")
        require(plain(w["editorial_reason"], 28, 500), tag + "_REASON")
        scores = w["editorial_scores"]
        require(type(scores) is dict and set(scores) == set(SCORES), tag + "_SCORE_SCHEMA")
        require(all(type(scores[k]) is int and 0 <= scores[k] <= cap
                    for k, cap in SCORES.items()), tag + "_SCORE_BOUNDS")
        require(sum(scores.values()) >= 80, tag + "_SCORE_LOW")
        rows = w["rows"]
        require(type(rows) is list, tag + "_ROWS")
        limit = (8, 18) if w["format"] == "trait_matches" else (12, 12)
        require(limit[0] <= len(rows) <= limit[1], tag + "_ROW_COUNT")
        labels, answers = [], []
        for idx, row in enumerate(rows):
            require(type(row) is dict and set(row) == ROW_FIELDS, tag + "_ROW_SCHEMA")
            label, answer = row["label"], row["answer"]
            require(plain(label, 2, 48) and plain(answer, 2, 68),
                    tag + "_ROW_TEXT_TOO_LONG_OR_EMPTY")
            labels.append(norm(label))
            answers.append(norm(answer))
        require(len(set(labels)) == len(labels), tag + "_DUPLICATE_LABEL")
        if w["format"] != "trait_matches":
            require(set(labels) == set(SIGNS), tag + "_MISSING_OR_EXTRA_SIGN")
            require(len(set(answers)) >= 9, tag + "_BORING_REPEATED_ANSWERS")
        else:
            for row in rows:
                parts = [norm(s) for s in row["answer"].split(",")]
                require(1 <= len(parts) <= 3 and len(parts) == len(set(parts)) and
                        all(p in SIGNS for p in parts),
                        tag + "_TRAIT_MUST_MATCH_ZODIAC_SIGNS")
            require(len(set(answers)) >= max(5, len(rows)//2),
                    tag + "_BORING_TRAIT_MATCHES")
        # Prefer short readable lines rather than attempting to shrink to tiny fonts.
        require(all(len(row["label"] + ": " + row["answer"]) <= 78 for row in rows),
                tag + "_MOBILE_LINE_TOO_LONG")
        title_sig = norm(title)
        content_sig = "|".join(labels) + "||" + "|".join(answers)
        for old in titles:
            require(SequenceMatcher(None, title_sig, old).ratio() < .82,
                    tag + "_SIMILAR_TITLES")
        for old in signatures:
            require(content_sig != old, tag + "_REPEATED_LIST")
        titles.append(title_sig)
        signatures.append(content_sig)
    for old in previous:
        if not isinstance(old, dict):
            continue
        other_title = old.get("title")
        other_id = old.get("id")
        if other_id in ids:
            raise Rejected("HISTORY_REUSED_ID")
        if isinstance(other_title, str):
            for title in titles:
                require(SequenceMatcher(None, title, norm(other_title)).ratio() < .86,
                        "HISTORY_SIMILAR_TOPIC")
        previous_rows = old.get("rows")
        if isinstance(previous_rows, list):
            old_sig = "|".join(norm(r.get("label", "")) for r in previous_rows if isinstance(r, dict))
            old_sig += "||" + "|".join(norm(r.get("answer", "")) for r in previous_rows if isinstance(r, dict))
            require(old_sig not in signatures, "HISTORY_REPEATED_LIST")
    return {"status": "approved_for_black_preview", "winner_count": n,
            "lane": "zodiac", "youtube_upload_enabled": False,
            "winners": winners}

def validate_submission(draft, previous=()):
    result = validate(draft, previous)
    for index, winner in enumerate(draft['winners']):
        require(winner['duration_seconds'] == 6, f'WINNER_{index+1}_DURATION_MUST_BE_6')
    return result

def main(argv=None):
    p=argparse.ArgumentParser(description="Offline Zodiac draft validation")
    p.add_argument("--draft", required=True)
    p.add_argument("--history-root", default="content/drafts")
    p.add_argument("--output", required=True)
    args=p.parse_args(argv)
    try:
        data=read_json(args.draft)
        history=previous_items(args.draft, args.history_root)
        result=validate_submission(data, history)
        output=Path(args.output)
        require(output.resolve() != Path(args.draft).resolve(), "OUTPUT_OVERWRITE")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "winners": result["winner_count"],
                          "previous_winners_checked": len(history), "youtube_upload_enabled": False}))
        return 0
    except (Rejected, OSError, TypeError, KeyError) as exc:
        print("ZODIAC_DRAFT_REJECTED: "+str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
