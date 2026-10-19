#!/usr/bin/env python3
"""Build site-data.json, the single data file behind the interactive page (index.html).

Reads every weekly run in out/<date>/ plus the snapshots and band log, and
packs them into one JSON file the page loads. Runs at the end of each weekly
run; can also be run on its own:  python scripts/build_site.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import RULES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
METRICS = sorted({m for r in RULES.values() for m in r if m != "min_bookings"}) + ["bookings_28d", "safety_flags"]


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def num(v: str):
    if v in ("", None):
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def main() -> None:
    import run_weekly  # reuse the same "is the ladder working" maths
    weeks = sorted(p.name for p in (ROOT / "out").iterdir() if (p / "scores.csv").exists())
    band_log = read(ROOT / "data" / "band-history.csv")
    partners, rows, themes, complaints = {}, {}, {}, {}
    for w in weeks:
        snap = {r["partner_id"]: r for r in read(ROOT / "data" / "snapshots" / f"{w}.csv")}
        rows[w] = []
        for r in read(ROOT / "out" / w / "scores.csv"):
            pid = r["partner_id"]
            partners[pid] = {"name": r["name"], "market": r["market"], "type": r["supply_type"],
                             "holdout": r["holdout"] == "yes"}
            m = {k: num(snap.get(pid, {}).get(k, "")) for k in METRICS}
            rows[w].append({"id": pid, "band": r["band"], "why": r["why"], "step": int(r["step"] or 0),
                            "week": r["this_week"], "action": r["action"],
                            "metrics": {k: v for k, v in m.items() if v is not None}})
        t = {}
        complaints[w] = {}
        for c in read(ROOT / "out" / w / "complaints_tagged.csv"):
            key = f'{c["market"]}|{c["supply_type"]}|{c["theme"]}'
            t[key] = t.get(key, 0) + 1
            complaints[w].setdefault(c["partner_id"], []).append({"theme": c["theme"], "text": c["text"]})
        themes[w] = t
    effect = run_weekly.ladder_effect(band_log) if band_log else None
    log = read(ROOT / "data" / "run-log.csv")
    out = {"weeks": weeks, "partners": partners, "rows": rows, "themes": themes, "complaints": complaints,
           "effect": effect, "themes_by": log[-1]["themes_by"] if log else "keywords",
           "rules": {k: {m: list(v) if isinstance(v, tuple) else v for m, v in r.items()} for k, r in RULES.items()}}
    (ROOT / "site-data.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"site-data.json: {len(weeks)} weeks, {len(partners)} partners")


if __name__ == "__main__":
    main()
