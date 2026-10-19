"""Tag guest complaints with a theme, so the report shows WHY quality moves.

With ANTHROPIC_API_KEY set, complaints are sent to Claude in batches and
tagged against a fixed theme list for that supply type. The model can only
choose from the list (anything else becomes "Other"), so the counts stay
comparable week to week.

Without a key, a keyword matcher does the same job, less precisely. The
weekly run never fails because tagging failed: it falls back to keywords.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

THEMES = {
    "Stays": {
        "Cleanliness": r"dirty|clean|dust|hair|stain|smell|mould|mold",
        "Check-in": r"check.?in|key|lockbox|code|couldn.t get in|waited outside|access",
        "Not as described": r"not as (?:described|pictured)|photos|smaller|misleading|different from",
        "Noise": r"noise|noisy|loud|party|construction",
        "Amenities": r"wifi|wi-fi|air ?con|ac |hot water|shower|towels|kitchen|heater",
        "Host communication": r"host (?:didn.t|never|ignored)|no reply|unresponsive|rude",
    },
    "Experiences": {
        "Late start / no-show": r"late|no.?show|didn.t show|waited|cancelled on the day|started \d+",
        "Guide quality": r"guide|host (?:was|seemed)|rushed|bored|uninterested|english",
        "Safety": r"unsafe|safety|injur|dangerous|helmet|life ?jacket",
        "Meeting point": r"meeting point|couldn.t find|wrong address|directions",
        "Group size": r"crowd|too many|group (?:was )?(?:too )?(?:big|large)",
        "Value": r"overpriced|not worth|value|expensive|hidden fee",
    },
    "Services": {
        "Punctuality": r"late|no.?show|didn.t show|rescheduled|waited",
        "Quality of work": r"quality|blurry|undercooked|bland|sloppy|poor|amateur",
        "Communication": r"no reply|unresponsive|rude|ignored|confus",
        "Hygiene": r"hygien|dirty|unclean|gloves|messy",
        "Pricing": r"overcharg|extra charge|hidden|price|expensive",
    },
}
MODEL = os.environ.get("THEMES_MODEL", "claude-haiku-5-5")


def keyword_theme(kind: str, text: str) -> str:
    t = text.lower()
    for theme, pattern in THEMES[kind].items():
        if re.search(pattern, t):
            return theme
    return "Other"


def _ai_batch(kind: str, texts: list[str]) -> list[str]:
    allowed = list(THEMES[kind]) + ["Other"]
    prompt = (
        f"Tag each guest complaint about a {kind[:-1].lower()} with exactly one theme from this list: "
        f"{allowed}. Reply with a JSON array of theme strings, same order, nothing else.\n\n"
        + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    )
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": MODEL, "max_tokens": 2000,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)["content"][0]["text"]
    tags = json.loads(out[out.find("["): out.rfind("]") + 1])
    if len(tags) != len(texts):
        raise ValueError("tag count mismatch")
    return [t if t in allowed else "Other" for t in tags]


def tag_all(complaints: list[dict]) -> tuple[list[str], str]:
    """Return (theme per complaint, source) where source is 'ai' or 'keywords'."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            tags = [""] * len(complaints)
            for kind in THEMES:
                idx = [i for i, c in enumerate(complaints) if c["supply_type"] == kind]
                for start in range(0, len(idx), 40):
                    chunk = idx[start:start + 40]
                    for i, t in zip(chunk, _ai_batch(kind, [complaints[i]["text"] for i in chunk])):
                        tags[i] = t
            return tags, "ai"
        except Exception as e:  # noqa: BLE001
            print(f"AI tagging failed ({e}), using keywords.")
    return [keyword_theme(c["supply_type"], c["text"]) for c in complaints], "keywords"
