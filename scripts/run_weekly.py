#!/usr/bin/env python3
"""Weekly supply quality run.

1. Read this week's partner metrics (data/snapshots/<date>.csv) and guest
   complaints (data/complaints/<date>.csv).
2. Score every partner Green / Amber / Red with the rules for its supply type.
3. Apply the escalation ladder (data/escalation-history.csv).
4. Tag every complaint with a theme (Claude if a key is set, else keywords).
5. Write the week's files to out/<date>/:
     scores.csv     every partner with band, reasons, step and action
     actions.csv    ready-to-send messages for partners who need one
     summary.md     the one-page read for the regional lead
     report.html    the same, as a page
6. Save the history, the band log and the run log.

Usage:
  python scripts/run_weekly.py --date 2026-10-19
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import (AMBER, EXPERIENCES, GREEN, HISTORY_FIELDS, LOW, RED, SERVICES, STAYS,  # noqa: E402
                   History, decide, in_holdout, score)
from themes import tag_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HISTORY = DATA / "escalation-history.csv"
BANDS = DATA / "band-history.csv"
RUN_LOG = DATA / "run-log.csv"
TYPES = [STAYS, EXPERIENCES, SERVICES]
MARKETS = ["Japan", "Korea", "India", "Southeast Asia"]
ORDER = {RED: 0, AMBER: 1, GREEN: 2, LOW: 3}

TIPS = {
    "host cancellations": "Keep your calendar up to date and block dates you can't host.",
    "response rate": "Turn on notifications and use saved replies so guests hear back within the hour.",
    "rating": "Read your last five reviews; fix the one thing that comes up most.",
    "complaints per 100 stays": "Check the cleaning standard and check-in steps; these drive most complaints.",
    "late starts / no-shows": "Arrive 15 minutes early and message guests the meeting point the day before.",
    "refunds": "Set clear expectations in the listing so guests know exactly what's included.",
    "late cancellations": "Only accept bookings you can definitely make; release dates early instead.",
    "repeat bookings": "Follow up after each booking and offer a simple rebooking link.",
    "safety flag raised": "Our team will contact you today about the safety report.",
}


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def message(r: dict) -> str:
    labels = [x.rsplit(" ", 1)[0] if x[-1].isdigit() or x[-1] == "%" else x for x in r["reasons"]]
    tips = [TIPS[l] for l in labels if l in TIPS][:2]
    first = r["name"]
    if r["this_week"] == "Safety review":
        head = f"Hi, {first} has been paused from search while we look into a safety report."
    elif r["step_after"] == 3:
        head = f"Hi, {first} is paused from search pending a quality review."
    elif r["step_after"] == 2 and r["this_week"].startswith("New"):
        head = f"Hi, {first} is still below our quality bar. Let's book a 20-minute coaching call this week."
    elif r["band"] == RED:
        head = f"Hi, {first} dropped below our quality bar this month ({'; '.join(r['reasons'][:2])})."
    else:
        head = f"Hi, a heads-up: {first} is close to our quality bar ({'; '.join(r['reasons'][:2])})."
    return " ".join([head] + tips)


# ---------- the "is the ladder working" check ----------

def ladder_effect(band_log: list[dict]) -> dict:
    """Partners red in the first logged week: what share is still red now, ladder vs holdout."""
    by_date = defaultdict(dict)
    for r in band_log:
        by_date[r["date"]][r["partner_id"]] = r
    dates = sorted(by_date)
    first, last = by_date[dates[0]], by_date[dates[-1]]
    cohort = {pid: r["holdout"] == "yes" for pid, r in first.items() if r["band"] == RED}
    out = {}
    for group, flag in (("ladder", False), ("holdout", True)):
        ids = [p for p, h in cohort.items() if h == flag]
        still = sum(1 for p in ids if last.get(p, {}).get("band") == RED)
        out[group] = {"n": len(ids), "still_red": still, "share": still / len(ids) if ids else 0,
                      "trend": [sum(1 for p in ids if by_date[d].get(p, {}).get("band") == RED) / len(ids)
                                if ids else 0 for d in dates]}
    out["dates"] = dates
    out["start"] = dates[0]
    return out


# ---------- report ----------

def svg_lines(effect: dict) -> str:
    w, h, pad = 300, 170, 34
    n = len(effect["dates"])
    left = pad + 14  # room for the "100%" label
    x = lambda i: left + i * (w - left - pad) / max(n - 1, 1)  # noqa: E731
    y = lambda v: h - pad - v * (h - 2 * pad)  # noqa: E731
    parts = [f'<line x1="{left}" y1="{y(0)}" x2="{w - pad}" y2="{y(0)}" stroke="var(--border)"/>',
             f'<line x1="{left}" y1="{y(1)}" x2="{w - pad}" y2="{y(1)}" stroke="var(--border)" stroke-dasharray="3 3"/>',
             f'<text x="{left - 6}" y="{y(1) + 4}" text-anchor="end">100%</text>',
             f'<text x="{left - 6}" y="{y(0) + 4}" text-anchor="end">0%</text>']
    for group, color in (("holdout", "var(--muted)"), ("ladder", "var(--accent)")):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(effect[group]["trend"]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        lv = effect[group]["trend"][-1]
        parts.append(f'<circle cx="{x(n - 1):.1f}" cy="{y(lv):.1f}" r="3.5" fill="{color}"/>')
    for i, d in enumerate(effect["dates"]):
        if i in (0, n - 1):
            parts.append(f'<text x="{x(i):.1f}" y="{h - 8}" text-anchor="middle">{date.fromisoformat(d):%d %b}</text>')
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Share still red over time">{"".join(parts)}</svg>'


def report_html(today: date, rows: list[dict], prev: dict[str, str], themes: dict, theme_src: str,
                effect: dict) -> str:
    e = html.escape
    pill = {GREEN: "g", AMBER: "a", RED: "r", LOW: "n"}

    # tiles per supply type
    tiles = ""
    for t in TYPES:
        rs = [r for r in rows if r["supply_type"] == t and r["band"] != LOW]
        c = Counter(r["band"] for r in rs)
        n = len(rs) or 1
        was = sum(1 for r in rs if prev.get(r["partner_id"]) == RED)
        delta = c[RED] - was
        seg = "".join(f'<i class="{pill[b]}" style="flex:{c[b]}"></i>' for b in (GREEN, AMBER, RED))
        tiles += (f'<div class="card tile"><div class="lab">{t}</div><div class="big">{c[RED]} <span>red of {len(rs)}</span></div>'
                  f'<div class="bar">{seg}</div><div class="sub">{c[GREEN] / n:.0%} green · {c[AMBER] / n:.0%} amber · '
                  f'<b class="{"up" if delta > 0 else "down" if delta < 0 else ""}">{delta:+d} red vs last week</b></div></div>')

    # heat table market x type
    head = "".join(f"<th>{t}</th>" for t in TYPES)
    body = ""
    for m in MARKETS:
        cells = ""
        for t in TYPES:
            rs = [r for r in rows if r["market"] == m and r["supply_type"] == t and r["band"] != LOW]
            share = sum(r["band"] == RED for r in rs) / len(rs) if rs else 0
            was = sum(prev.get(r["partner_id"]) == RED for r in rs) / len(rs) if rs else 0
            arrow = "▲" if share > was else "▼" if share < was else ""
            bg = f"rgba(214,69,61,{min(0.9, 0.08 + share * 2.2):.2f})" if share else "#F3F4F6"
            fg = "#fff" if share >= 0.15 else "var(--text)"
            cells += (f'<td><span class="heat" style="background:{bg};color:{fg}">{share:.0%}</span>'
                      f'<span class="arr {"up" if arrow == "▲" else "down"}">{arrow}</span></td>')
        body += f"<tr><th class='mk'>{m}</th>{cells}</tr>"

    # themes
    theme_html = ""
    for t in TYPES:
        cur, old = themes[t]["now"], themes[t]["prev"]
        total = sum(cur.values()) or 1
        bars = ""
        for name, k in cur.most_common(4):
            d = k - old.get(name, 0)
            bars += (f'<div class="tb"><span class="tn">{e(name)}</span><span class="track"><i style="width:{k / total:.0%}"></i></span>'
                     f'<span class="tk">{k}</span><span class="td {"up" if d > 0 else "down" if d < 0 else ""}">{d:+d}</span></div>')
        theme_html += f'<div><div class="lab">{t} · {total} complaints</div>{bars}</div>'

    # actions table
    act = [r for r in rows if r["band"] == RED or r["this_week"] in ("Safety review",)]
    trs = "".join(
        f'<tr><td><b>{e(r["name"])}</b><br><span class="muted">{e(r["partner_id"])} · {e(r["market"])}</span></td>'
        f'<td>{r["supply_type"]}</td><td><span class="pill {pill[r["band"]]}">{r["band"]}</span></td>'
        f'<td class="steps">{"".join(f"<i class={chr(34)}{"lit" if i <= r["step_after"] else ""}{chr(34)}></i>" for i in (1, 2, 3))}</td>'
        f'<td>{e(r["this_week"])}</td><td class="muted">{e("; ".join(r["reasons"]))}</td></tr>'
        for r in act)
    steps = Counter(r["this_week"] for r in rows)
    L, H = effect["ladder"], effect["holdout"]

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Supply Quality</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#F6F7F9;--card:#fff;--text:#14161B;--muted:#6B7280;--border:#E5E7EB;--accent:#E0484E;
--g:#2F9E6A;--g-bg:#E8F6EF;--a:#D99A1E;--a-bg:#FEF3C7;--r:#D6453D;--r-bg:#FDE8E6;--n:#9CA3AF}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding:24px 16px 48px}} h1{{font-size:22px;margin:0}} h2{{font-size:14px;margin:0 0 12px}}
.muted{{color:var(--muted)}} .badge{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;background:#FEF3C7;color:#92400E;margin-left:8px;vertical-align:middle}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}}
.row3{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0 12px}}
.row2{{display:grid;grid-template-columns:1.25fr 1fr;gap:12px;margin-bottom:12px}}
.lab{{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}}
.big{{font-size:28px;font-weight:700;line-height:1.1}} .big span{{font-size:13px;font-weight:500;color:var(--muted)}}
.bar{{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:10px 0 8px;background:var(--border)}}
.bar i.g{{background:var(--g)}} .bar i.a{{background:var(--a)}} .bar i.r{{background:var(--r)}}
.sub{{font-size:12px;color:var(--muted)}} .up{{color:var(--r)}} .down{{color:var(--g)}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{padding:9px 8px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:500;font-size:12px}} th.mk{{color:var(--text);font-weight:600;font-size:13px}}
.heat{{display:inline-block;min-width:52px;text-align:center;padding:3px 8px;border-radius:6px;font-weight:600}}
.arr{{font-size:10px;margin-left:6px}}
.tb{{display:grid;grid-template-columns:130px 1fr 28px 30px;align-items:center;gap:8px;margin:5px 0;font-size:13px}}
.track{{height:8px;background:#F1F2F4;border-radius:4px;overflow:hidden}} .track i{{display:block;height:100%;background:var(--accent);border-radius:4px}}
.tk{{text-align:right;font-weight:600}} .td{{font-size:12px;text-align:right}}
.themes{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}}
.chip{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;background:#EEF2FF;color:#3B5BDB;margin-left:6px}}
.effect{{display:grid;grid-template-columns:1fr 1.15fr;gap:12px;align-items:center}}
.effect svg{{width:100%;height:auto}} .effect svg text{{font-size:12px;fill:var(--muted)}}
.stat b{{font-size:26px;display:block;line-height:1.1}} .stat{{margin-bottom:10px}} .key{{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.pill{{font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;white-space:nowrap}}
.pill.g{{background:var(--g-bg);color:var(--g)}} .pill.a{{background:var(--a-bg);color:#92400E}} .pill.r{{background:var(--r-bg);color:var(--r)}}
.steps{{white-space:nowrap}} .steps i{{display:inline-block;width:14px;height:6px;border-radius:3px;background:var(--border);margin-right:3px}} .steps i.lit{{background:var(--r)}}
.ladder{{display:flex;gap:8px;flex-wrap:wrap;font-size:12px;margin-top:10px}} .ladder div{{flex:1;min-width:150px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px}}
.tw{{overflow-x:auto}} td:nth-child(5){{white-space:nowrap}}
@media (max-width:900px){{.row3,.row2,.themes,.effect{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>Supply quality · week of {today:%d %b %Y} <span class="badge">Demo data</span></h1>
<div class="muted">Stays, experiences and services across four markets · last 28 days · generated every Monday</div>

<div class="row3">{tiles}</div>

<div class="row2">
  <div class="card"><h2>Where quality is slipping <span class="muted" style="font-weight:400">· share of partners in red</span></h2>
    <table><tr><th></th>{head}</tr>{body}</table></div>
  <div class="card"><h2>Do the warnings actually work?</h2>
    <div class="effect"><div>
      <div class="stat"><span class="key" style="background:var(--accent)"></span>Partners we warned<b>{L['share']:.0%}</b><span class="muted">still red ({L['still_red']} of {L['n']} red on {date.fromisoformat(effect['start']):%d %b})</span></div>
      <div class="stat"><span class="key" style="background:var(--muted)"></span>Partners we left alone<b>{H['share']:.0%}</b><span class="muted">still red ({H['still_red']} of {H['n']})</span></div>
    </div>{svg_lines(effect)}</div>
    <div class="muted" style="font-size:12px">Some partners recover on their own, so we leave 15% alone on purpose and compare. Small sample in this demo.</div></div>
</div>

<div class="card" style="margin-bottom:12px"><h2>Why: what guests complain about <span class="chip">{"Tagged by AI" if theme_src == "ai" else "Keyword-tagged"}</span> <span class="muted" style="font-weight:400">· count this week, change vs last week</span></h2>
  <div class="themes">{theme_html}</div></div>

<div class="card" style="margin-bottom:12px"><h2>This week's actions
  <span class="muted" style="font-weight:400">· {steps["New step 1"]} warnings, {steps["New step 2"]} coaching calls, {steps["New step 3"] + steps["Safety review"] + steps["Review again"]} listing reviews, {steps["Heads-up"]} heads-ups</span></h2>
  <div class="tw"><table><tr><th>Partner</th><th>Type</th><th>Band</th><th>Step</th><th>This week</th><th>Why</th></tr>{trs}</table></div>
  <div class="ladder"><div><b>Step 1</b><br>Warning with fix tips</div><div><b>Step 2</b><br>Coaching call + 30-day plan</div>
  <div><b>Step 3</b><br>Listing review, hidden from search</div><div><b>Safety flag</b><br>Straight to step 3, same week</div></div></div>
</div></body></html>"""


def summary_md(today: date, rows: list[dict], themes: dict, effect: dict) -> str:
    c = Counter(r["band"] for r in rows)
    lines = [f"# Supply quality · week of {today:%d %b %Y}", "",
             f"**{c[RED]} partners in red, {c[AMBER]} amber, {c[GREEN]} green** ({c[LOW]} with too few bookings to score).", ""]
    lines.append("## Biggest complaint themes")
    for t in TYPES:
        top = themes[t]["now"].most_common(1)
        if top:
            name, k = top[0]
            d = k - themes[t]["prev"].get(name, 0)
            lines.append(f"- **{t}:** {name}, {k} complaints ({d:+d} vs last week)")
    L, H = effect["ladder"], effect["holdout"]
    lines += ["", "## Do the warnings work?",
              f"Of partners red on {effect['start']}: {L['share']:.0%} of partners we warned are still red, "
              f"vs {H['share']:.0%} of those we left alone.", "", "## Safety and listing reviews"]
    rv = [r for r in rows if r["this_week"] in ("Safety review", "New step 3", "Review again")]
    lines += [f"- {r['name']} ({r['partner_id']}): {r['this_week']} · {'; '.join(r['reasons'])}" for r in rv] or ["- None this week."]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    today = date.fromisoformat(args.date)

    log = read(RUN_LOG)
    if any(r["date"] == args.date for r in log) and not args.force:
        sys.exit(f"Already ran for {args.date}. Use --force to run again (steps would be applied twice).")

    snapshot = read(DATA / "snapshots" / f"{args.date}.csv")
    complaints = read(DATA / "complaints" / f"{args.date}.csv")
    history = {r["partner_id"]: History.from_row(r) for r in read(HISTORY)}
    band_log = read(BANDS)
    last_date = max((r["date"] for r in band_log), default=None)
    prev = {r["partner_id"]: r["band"] for r in band_log if r["date"] == last_date}

    rows = []
    for p in snapshot:
        band, reasons, safety = score(p)
        hold = in_holdout(p["partner_id"])
        d, h = decide(p["partner_id"], band, safety, history.get(p["partner_id"]), today, hold)
        history[p["partner_id"]] = h
        rows.append({**p, "band": band, "reasons": reasons + d.notes, "holdout": hold,
                     "step_before": d.step_before, "step_after": d.step_after,
                     "this_week": d.this_week, "action": d.action})
    rows.sort(key=lambda r: (ORDER[r["band"]], -r["step_after"], r["supply_type"], r["name"]))

    # complaint themes, this week vs last week
    tags, src = tag_all(complaints)
    prev_comp = read(DATA / "complaints" / f"{last_date}.csv") if last_date else []
    prev_tags, _ = tag_all(prev_comp) if prev_comp else ([], src)
    themes = {t: {"now": Counter(), "prev": Counter()} for t in TYPES}
    for c, tg in zip(complaints, tags):
        themes[c["supply_type"]]["now"][tg] += 1
    for c, tg in zip(prev_comp, prev_tags):
        themes[c["supply_type"]]["prev"][tg] += 1

    band_log += [{"date": args.date, "partner_id": r["partner_id"], "market": r["market"],
                  "supply_type": r["supply_type"], "band": r["band"], "holdout": "yes" if r["holdout"] else ""}
                 for r in rows]
    effect = ladder_effect(band_log)

    out = ROOT / "out" / args.date
    fields = ["partner_id", "name", "market", "supply_type", "bookings_28d", "band", "why", "holdout",
              "step", "this_week", "action"]
    write(out / "scores.csv", [{**{k: r[k] for k in fields if k in r}, "why": "; ".join(r["reasons"]),
                                "holdout": "yes" if r["holdout"] else "", "step": r["step_after"]} for r in rows], fields)
    msgs = [{"partner_id": r["partner_id"], "name": r["name"], "action": r["action"], "message": message(r)}
            for r in rows if r["action"] and r["this_week"] not in ("Holdout", "")]
    write(out / "actions.csv", msgs, ["partner_id", "name", "action", "message"])
    (out / "summary.md").write_text(summary_md(today, rows, themes, effect))
    write(out / "complaints_tagged.csv",
          [{"partner_id": c["partner_id"], "market": c["market"], "supply_type": c["supply_type"],
            "theme": tg, "text": c["text"]} for c, tg in zip(complaints, tags)],
          ["partner_id", "market", "supply_type", "theme", "text"])
    (out / "report.html").write_text(report_html(today, rows, prev, themes, src, effect))


    write(HISTORY, [h.to_row() for h in sorted(history.values(), key=lambda h: h.partner_id)], HISTORY_FIELDS)
    write(BANDS, band_log, ["date", "partner_id", "market", "supply_type", "band", "holdout"])
    c = Counter(r["band"] for r in rows)
    log.append({"date": args.date, "partners": len(rows), "green": c[GREEN], "amber": c[AMBER], "red": c[RED],
                "complaints": len(complaints), "themes_by": src})
    write(RUN_LOG, log, list(log[0].keys()))
    import build_site  # refresh the interactive page's data
    build_site.main()
    print(f"{args.date}: {c[RED]} red, {c[AMBER]} amber, {c[GREEN]} green · {len(msgs)} messages · themes by {src}")


if __name__ == "__main__":
    main()
