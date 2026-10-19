"""Quality bands for each supply type, plus the escalation ladder.

Pure functions, no file or network access, so the rules are tested on
their own (tests/test_rules.py).

Three supply types, each scored on what actually hurts guests for that type:

  Stays        host cancellations, response rate, rating, complaints
  Experiences  no-shows and late starts, rating, refunds, safety flags
  Services     late cancellations, rating, rebooking rate

Every metric covers the last 28 days. A partner with too few bookings is
not scored ("Not enough data"), so one bad booking can't put a small host
in red.

Bands: Red if any red rule is hit. Amber if any amber rule is hit. Else Green.

Escalation ladder (red partners only):
  Step 1  Warning, with the specific fix tips for what went wrong
  Step 2  Coaching call and a 30-day quality plan
  Step 3  Listing review: hidden from search until the review is done
- Next step only if still red 14+ days after the last step. Still-red
  partners get a weekly reminder in between.
- A safety flag skips the ladder: straight to step 3, the same week.
- Amber gets a heads-up only, never a step.
- The ladder clears after 8 weeks with no red week.
- Holdout: a fixed 15% of partners get no ladder actions (safety flags
  excepted). Comparing them with everyone else shows whether the ladder
  itself improves quality, rather than quality just drifting on its own.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

GREEN, AMBER, RED, LOW = "Green", "Amber", "Red", "Not enough data"
STAYS, EXPERIENCES, SERVICES = "Stays", "Experiences", "Services"

STEP_GAP_DAYS = 14
CLEAR_DAYS = 56
HOLDOUT_SHARE = 0.15

STEP_ACTION = {
    1: "Warning with fix tips",
    2: "Coaching call + 30-day quality plan",
    3: "Listing review (hidden from search until reviewed)",
}

# metric: (direction, red threshold, amber threshold, label)
# direction "high" = higher is worse; "low" = lower is worse
RULES = {
    STAYS: {
        "min_bookings": 5,
        "host_cancel_rate": ("high", 0.03, 0.015, "host cancellations"),
        "response_rate": ("low", 0.80, 0.90, "response rate"),
        "avg_rating": ("low", 4.50, 4.70, "rating"),
        "complaints_per_100": ("high", 5.0, 3.0, "complaints per 100 stays"),
    },
    EXPERIENCES: {
        "min_bookings": 6,
        "late_or_no_show_rate": ("high", 0.08, 0.04, "late starts / no-shows"),
        "avg_rating": ("low", 4.60, 4.80, "rating"),
        "refund_rate": ("high", 0.06, 0.03, "refunds"),
    },
    SERVICES: {
        "min_bookings": 4,
        "late_cancel_rate": ("high", 0.05, 0.02, "late cancellations"),
        "avg_rating": ("low", 4.60, 4.80, "rating"),
        "rebook_rate": ("low", 0.15, 0.25, "repeat bookings"),
    },
}


def _fmt(metric: str, v: float) -> str:
    if metric.endswith("rate"):
        return f"{v:.0%}" if v >= 0.1 or v == 0 else f"{v:.1%}"
    if metric == "avg_rating":
        return f"{v:.2f}"
    return f"{v:.1f}"


def score(row: dict) -> tuple[str, list[str], bool]:
    """Return (band, reasons, safety_flag) for one partner's 28-day metrics."""
    kind = row["supply_type"]
    rules = RULES[kind]
    if int(row["bookings_28d"]) < rules["min_bookings"]:
        return LOW, [f"only {row['bookings_28d']} bookings"], False

    safety = kind == EXPERIENCES and int(row.get("safety_flags") or 0) > 0
    red, amber = [], []
    for metric, spec in rules.items():
        if metric == "min_bookings":
            continue
        direction, r, a, label = spec
        v = float(row[metric])
        worse = (lambda x, t: x >= t) if direction == "high" else (lambda x, t: x < t)
        if worse(v, r):
            red.append(f"{label} {_fmt(metric, v)}")
        elif worse(v, a):
            amber.append(f"{label} {_fmt(metric, v)}")
    if safety:
        red.insert(0, "safety flag raised")
    if red:
        return RED, red + amber, safety
    if amber:
        return AMBER, amber, False
    return GREEN, [], False


def in_holdout(partner_id: str) -> bool:
    """Stable 10% holdout: the same partner is always in or out."""
    h = int(hashlib.sha256(partner_id.encode()).hexdigest()[:8], 16)
    return (h % 1000) < HOLDOUT_SHARE * 1000


# ---------- ladder ----------

HISTORY_FIELDS = ["partner_id", "step", "last_step_date", "last_red_date", "updated_on"]


@dataclass
class History:
    partner_id: str
    step: int = 0
    last_step_date: date | None = None
    last_red_date: date | None = None
    updated_on: date | None = None

    @classmethod
    def from_row(cls, r: dict) -> "History":
        d = lambda v: date.fromisoformat(v) if v else None  # noqa: E731
        return cls(r["partner_id"], int(r.get("step") or 0), d(r.get("last_step_date")),
                   d(r.get("last_red_date")), d(r.get("updated_on")))

    def to_row(self) -> dict:
        s = lambda v: v.isoformat() if v else ""  # noqa: E731
        return {"partner_id": self.partner_id, "step": self.step, "last_step_date": s(self.last_step_date),
                "last_red_date": s(self.last_red_date), "updated_on": s(self.updated_on)}


@dataclass
class Decision:
    partner_id: str
    band: str
    step_before: int
    step_after: int
    this_week: str
    action: str
    notes: list[str] = field(default_factory=list)


def decide(partner_id: str, band: str, safety: bool, hist: History | None, today: date,
           holdout: bool = False) -> tuple[Decision, History]:
    h = hist or History(partner_id)
    before = h.step
    notes: list[str] = []

    if h.step and band != RED and h.last_red_date and (today - h.last_red_date).days >= CLEAR_DAYS:
        notes.append(f"Ladder cleared (8 weeks with no red, was step {h.step})")
        h.step, h.last_step_date = 0, None

    this_week, action = "", ""
    if band == RED:
        h.last_red_date = today
        if safety:
            # safety never waits and is never held out
            h.step, h.last_step_date = 3, today
            this_week, action = "Safety review", STEP_ACTION[3]
        elif holdout:
            this_week, action = "Holdout", "No action (holdout group)"
        else:
            due = h.last_step_date is None or (today - h.last_step_date).days >= STEP_GAP_DAYS
            if due and h.step < 3:
                h.step += 1
                h.last_step_date = today
                this_week, action = f"New step {h.step}", STEP_ACTION[h.step]
            elif due:
                h.last_step_date = today
                this_week, action = "Review again", STEP_ACTION[3]
            else:
                this_week, action = "Reminder", f"Weekly reminder (step {h.step} in place)"
    elif band == AMBER and not holdout:
        this_week, action = "Heads-up", "Heads-up with fix tips, no step"
    elif notes:
        this_week = "Cleared"
    elif h.step and band == GREEN:
        this_week = "Back to green"

    h.updated_on = today
    return Decision(partner_id, band, before, h.step, this_week, action, notes), h
