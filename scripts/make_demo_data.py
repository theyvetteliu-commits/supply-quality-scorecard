#!/usr/bin/env python3
"""Make six weeks of invented supply data in data/snapshots/ and data/complaints/.

80 fictional partners across 4 markets and 3 supply types. Each partner has
a hidden "trouble" level that drifts week to week and drives its metrics
and complaints. A few stories are built in so the report has something to
find:

  - Japan stays: a cleaning issue shows up in the last two weeks.
  - Southeast Asia experiences: late starts creep up over the period.
  - India services: a cluster of late cancellations.
  - One experience raises a safety flag.

Partners who get coached (ladder step 1+) slowly improve; holdout partners
don't get coached. That is the effect the report's "Is the ladder working?"
panel is built to measure. Everything here is invented.
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import EXPERIENCES, RED, SERVICES, STAYS, History, decide, in_holdout, score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snapshots"
COMP = ROOT / "data" / "complaints"
WEEKS = [date(2026, 9, 14) + timedelta(weeks=i) for i in range(6)]

MARKETS = {
    "Japan": ["Tokyo", "Kyoto", "Osaka"],
    "Korea": ["Seoul", "Busan"],
    "India": ["Goa", "Jaipur", "Mumbai"],
    "Southeast Asia": ["Bali", "Bangkok", "Hanoi"],
}
COUNTS = {STAYS: 10, EXPERIENCES: 6, SERVICES: 4}
NOUN = {STAYS: "stay", EXPERIENCES: "experience", SERVICES: "service"}

COMPLAINTS = {
    STAYS: {
        "Cleanliness": ["Bathroom was dirty when we arrived", "Hair on the sheets and dust everywhere",
                        "Kitchen smelled and the floor had stains", "Not cleaned properly between guests"],
        "Check-in": ["Lockbox code didn't work, waited outside 40 minutes", "Key wasn't where the host said",
                     "Couldn't get in until midnight"],
        "Not as described": ["Much smaller than the photos", "Not as described, no view at all"],
        "Noise": ["Construction next door from 7am", "Very noisy street all night"],
        "Amenities": ["No hot water for two days", "Wifi didn't work", "Air con broken"],
        "Host communication": ["Host never replied to messages", "Host ignored us when the shower broke"],
    },
    EXPERIENCES: {
        "Late start / no-show": ["Guide was 30 minutes late", "Started 45 minutes late, no apology",
                                 "Host didn't show, cancelled on the day", "We waited at the start for ages"],
        "Guide quality": ["Guide seemed rushed and bored", "Guide's English was hard to follow"],
        "Safety": ["Felt unsafe, no helmets were given"],
        "Meeting point": ["Couldn't find the meeting point", "Wrong address in the booking"],
        "Group size": ["Group was too big to hear anything"],
        "Value": ["Overpriced for what it was", "Hidden fee for the boat"],
    },
    SERVICES: {
        "Punctuality": ["Chef arrived an hour late", "Photographer rescheduled the same day", "No-show, had to rebook"],
        "Quality of work": ["Photos were blurry", "Food was bland and undercooked"],
        "Communication": ["No reply for two days before the booking"],
        "Hygiene": ["Left the kitchen messy"],
        "Pricing": ["Overcharged for ingredients"],
    },
}


def partners() -> list[dict]:
    out, rng = [], random.Random("partners")
    for market, cities in MARKETS.items():
        code = {"Japan": "JP", "Korea": "KR", "India": "IN", "Southeast Asia": "SEA"}[market]
        for kind, n in COUNTS.items():
            for i in range(1, n + 1):
                pid = f"{code}-{ {STAYS: 'ST', EXPERIENCES: 'EX', SERVICES: 'SV'}[kind] }-{i:02d}"
                trouble = rng.uniform(0.55, 0.85) if rng.random() < 0.28 else rng.uniform(0.05, 0.35)
                if in_holdout(pid) and i % 2 == 0:
                    trouble = rng.uniform(0.5, 0.75)  # make sure the holdout has struggling partners too
                out.append({"partner_id": pid, "name": f"{rng.choice(cities)} {NOUN[kind]} {i}",
                            "market": market, "supply_type": kind, "trouble": trouble,
                            "volume": rng.randint(8, 34)})
    return out


def event(p: dict, wk: int) -> tuple[float, str | None]:
    """(extra trouble, forced complaint theme) for the built-in stories."""
    if p["market"] == "Japan" and p["supply_type"] == STAYS and wk >= 4 and p["partner_id"] < "JP-ST-06":
        return 0.35, "Cleanliness"
    if p["market"] == "Southeast Asia" and p["supply_type"] == EXPERIENCES:
        return 0.06 * wk, "Late start / no-show"
    if p["market"] == "India" and p["supply_type"] == SERVICES and wk >= 2:
        return 0.25, "Punctuality"
    return 0.0, None


def metrics(p: dict, t: float, rng: random.Random) -> dict:
    t = max(0.0, min(1.0, t)) ** 1.5  # most partners are fine; trouble bites at the top end
    n = max(2, round(p["volume"] * rng.uniform(0.8, 1.2)))
    j = lambda x: x * rng.uniform(0.85, 1.15)  # noqa: E731
    row = {"partner_id": p["partner_id"], "name": p["name"], "market": p["market"],
           "supply_type": p["supply_type"], "bookings_28d": n,
           "host_cancel_rate": "", "response_rate": "", "complaints_per_100": "",
           "late_or_no_show_rate": "", "refund_rate": "", "safety_flags": "",
           "late_cancel_rate": "", "rebook_rate": ""}
    if p["supply_type"] == STAYS:
        row.update(host_cancel_rate=round(j(0.004 + 0.05 * t), 4), response_rate=round(min(1, 0.99 - 0.24 * t * rng.uniform(.8, 1.2)), 3),
                   avg_rating=round(4.93 - 0.62 * t * rng.uniform(.85, 1.15), 2), complaints_per_100=round(j(0.8 + 8 * t), 1))
    elif p["supply_type"] == EXPERIENCES:
        row.update(late_or_no_show_rate=round(j(0.008 + 0.12 * t), 4), avg_rating=round(4.95 - 0.5 * t * rng.uniform(.85, 1.15), 2),
                   refund_rate=round(j(0.008 + 0.08 * t), 4), safety_flags=0)
    else:
        row.update(late_cancel_rate=round(j(0.004 + 0.08 * t), 4), avg_rating=round(4.95 - 0.5 * t * rng.uniform(.85, 1.15), 2),
                   rebook_rate=round(max(0, 0.42 - 0.34 * t * rng.uniform(.85, 1.15)), 3))
    return row


def complaints_for(p: dict, row: dict, t: float, forced: str | None, monday: date, rng: random.Random) -> list[dict]:
    k = max(0, round(t * 3.2 + rng.uniform(-0.6, 0.6)))
    bank = COMPLAINTS[p["supply_type"]]
    out = []
    for _ in range(k):
        pool = [x for x in bank if x != "Safety"]  # safety complaints only come with a safety flag
        theme = forced if forced and rng.random() < 0.65 else rng.choice(pool)
        out.append({"date": monday.isoformat(), "partner_id": p["partner_id"], "market": p["market"],
                    "supply_type": p["supply_type"], "text": rng.choice(bank[theme])})
    return out


def main() -> None:
    SNAP.mkdir(parents=True, exist_ok=True)
    COMP.mkdir(parents=True, exist_ok=True)
    ps, hist = partners(), {}
    for wk, monday in enumerate(WEEKS):
        rng = random.Random(f"week-{wk}")
        rows, comps = [], []
        for p in ps:
            extra, forced = event(p, wk)
            h = hist.get(p["partner_id"])
            # coaching works, slowly: coached partners shed trouble each week
            if h and h.step >= 1 and not in_holdout(p["partner_id"]):
                p["trouble"] *= 0.93
            # natural drift back toward normal: some partners recover with no help at all,
            # which is exactly why the report compares against a holdout
            p["trouble"] = max(0.02, p["trouble"] + 0.08 * (0.3 - p["trouble"]) + rng.uniform(-0.05, 0.05))
            t = p["trouble"] + extra
            row = metrics(p, t, rng)
            if p["partner_id"] == "SEA-EX-03" and wk == 5:
                row["safety_flags"] = 1
            band, _, safety = score(row)
            _, hist[p["partner_id"]] = decide(p["partner_id"], band, safety, h, monday, in_holdout(p["partner_id"]))
            rows.append(row)
            comps += complaints_for(p, row, t, forced, monday, rng)
            if row["safety_flags"] == 1:
                comps.append({"date": monday.isoformat(), "partner_id": p["partner_id"], "market": p["market"],
                              "supply_type": p["supply_type"], "text": "Felt unsafe, no helmets were given"})
        for path, data in ((SNAP / f"{monday}.csv", rows), (COMP / f"{monday}.csv", comps)):
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
        print(f"{monday}: {len(rows)} partners, {len(comps)} complaints, "
              f"{sum(score(r)[0] == RED for r in rows)} red")


if __name__ == "__main__":
    main()
