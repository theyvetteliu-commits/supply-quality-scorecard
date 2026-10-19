"""Run with: python -m pytest tests  (or just: python tests/test_rules.py)"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from rules import AMBER, EXPERIENCES, GREEN, LOW, RED, SERVICES, STAYS, decide, score  # noqa: E402
from themes import keyword_theme  # noqa: E402

D = date(2026, 10, 19)


def stay(**kw):
    base = dict(supply_type=STAYS, bookings_28d=20, host_cancel_rate=0.005, response_rate=0.97,
                avg_rating=4.9, complaints_per_100=1.0)
    return {**base, **kw}


def test_bands():
    assert score(stay())[0] == GREEN
    assert score(stay(avg_rating=4.6))[0] == AMBER
    assert score(stay(host_cancel_rate=0.04))[0] == RED
    assert score(stay(bookings_28d=3, avg_rating=3.0))[0] == LOW  # too few bookings to judge


def test_safety_flag_goes_straight_to_review():
    exp = dict(supply_type=EXPERIENCES, bookings_28d=10, late_or_no_show_rate=0.01, avg_rating=4.9,
               refund_rate=0.01, safety_flags=1)
    band, _, safety = score(exp)
    assert band == RED and safety
    d, h = decide("X", band, safety, None, D, holdout=True)  # holdout never blocks safety
    assert h.step == 3 and d.this_week == "Safety review"


def test_ladder_waits_14_days():
    _, h = decide("X", RED, False, None, D)
    d, h = decide("X", RED, False, h, D + timedelta(days=7))
    assert (h.step, d.this_week) == (1, "Reminder")
    d, h = decide("X", RED, False, h, D + timedelta(days=14))
    assert (h.step, d.this_week) == (2, "New step 2")


def test_holdout_gets_no_steps():
    d, h = decide("X", RED, False, None, D, holdout=True)
    assert h.step == 0 and d.this_week == "Holdout"


def test_clears_after_8_clean_weeks():
    _, h = decide("X", RED, False, None, D)
    d, h = decide("X", GREEN, False, h, D + timedelta(weeks=8))
    assert h.step == 0 and d.this_week == "Cleared"


def test_service_rules():
    svc = dict(supply_type=SERVICES, bookings_28d=8, late_cancel_rate=0.01, avg_rating=4.9, rebook_rate=0.10)
    assert score(svc)[0] == RED  # nobody rebooks


def test_keyword_themes():
    assert keyword_theme(STAYS, "Bathroom was dirty") == "Cleanliness"
    assert keyword_theme(EXPERIENCES, "Guide was 30 minutes late") == "Late start / no-show"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("all tests pass")
