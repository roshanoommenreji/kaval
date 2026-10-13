"""Draw the time-to-restore chart from the recorded game days (KAV-83).

    python scripts/dev/mttr_chart.py            # rewrites docs/architecture/mttr.svg
    python scripts/dev/mttr_chart.py --check    # exit 1 if the picture is out of date

The numbers live in docs/architecture/mttr-runs.json, copied by hand from each post-mortem,
because staging is destroyed after every game day and takes its database (and so `make mttr`)
with it. The picture is plain SVG written here, with no plotting library, so it is the same on
every machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs" / "architecture" / "mttr-runs.json"
OUT = ROOT / "docs" / "architecture" / "mttr.svg"

COLOURS = {"detect": "#4c78a8", "diagnose": "#9ecae9", "human": "#f58518", "queue": "#b8b8b8",
           "execute": "#54a24b", "selfheal": "#8e6bbf"}
LEFT, TOP, ROW, BAR, SCALE = 150, 50, 44, 26, 1.3  # SCALE: pixels per second


def check(data: dict) -> list[str]:
    """Return what is wrong with the recorded numbers; empty when they add up."""
    bad: list[str] = []
    names = data["stage_names"]
    for run in data["runs"]:
        s, name = run["stages"], run["name"]
        if run["kind"] == "fix":
            if len(s) != len(names):
                bad.append(f"{name}: needs {len(names)} stages")
                continue
            human = s[names.index("human")]
            if sum(s) - human != run["machine_s"]:
                bad.append(f"{name}: machine time {run['machine_s']} != stages without the person")
            if sum(s) != run["wall_s"]:
                bad.append(f"{name}: wall-clock {run['wall_s']} != sum of stages {sum(s)}")
        elif run["kind"] == "alert":
            if len(s) != 1 or run["machine_s"] is not None:
                bad.append(f"{name}: an alert-only run has just a detect time")
        elif run["kind"] == "selfheal":
            if s or run["machine_s"] != run["wall_s"]:
                bad.append(f"{name}: a self-heal has one time and no stages")
        else:
            bad.append(f"{name}: unknown kind {run['kind']!r}")
    return bad


def _segments(run: dict, names: list[str]) -> list[tuple[str, int]]:
    if run["kind"] == "selfheal":
        return [("selfheal", run["wall_s"])]
    return list(zip(names, run["stages"], strict=False))


def draw(data: dict) -> str:
    names, runs = data["stage_names"], data["runs"]
    width = LEFT + int(max(r["wall_s"] or r["stages"][0] for r in runs) * SCALE) + 150
    height = TOP + ROW * len(runs) + 70
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
         f'height="{height}" font-family="sans-serif" font-size="12" role="img" '
         'aria-label="Time to restore by stage for each chaos run">',
         '<rect width="100%" height="100%" fill="#ffffff"/>',
         f'<text x="{LEFT}" y="22" font-size="14" font-weight="bold">Time to restore, by stage '
         '(seconds, from the recorded game days)</text>']
    for i, run in enumerate(runs):
        y = TOP + i * ROW
        o.append(f'<text x="{LEFT - 10}" y="{y + 17}" text-anchor="end">{run["name"]}</text>')
        x = float(LEFT)
        for stage, secs in _segments(run, names):
            w = secs * SCALE
            o.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{BAR}" '
                     f'fill="{COLOURS[stage]}"><title>{stage}: {secs} s</title></rect>')
            if w > 28:
                o.append(f'<text x="{x + w / 2:.1f}" y="{y + 17}" text-anchor="middle" '
                         f'fill="#000">{secs}</text>')
            x += w
        total = run["wall_s"] if run["wall_s"] is not None else run["stages"][0]
        label = (f"{total} s" if run["kind"] != "alert" else f"{total} s to tell a person")
        o.append(f'<text x="{x + 6:.1f}" y="{y + 17}">{label}</text>')
    ly = TOP + ROW * len(runs) + 16
    lx = float(LEFT)
    for stage in [*names, "selfheal"]:
        text = "lost server: no person, no approval" if stage == "selfheal" else stage
        o.append(f'<rect x="{lx:.0f}" y="{ly}" width="12" height="12" fill="{COLOURS[stage]}"/>')
        o.append(f'<text x="{lx + 17:.0f}" y="{ly + 11}">{text}</text>')
        lx += 17 + len(text) * 7 + 14
    o.append("</svg>")
    return "\n".join(o) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    bad = check(data)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    svg = draw(data)
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != svg:
            print("docs/architecture/mttr.svg is out of date: run scripts/dev/mttr_chart.py",
                  file=sys.stderr)
            return 1
        return 0
    OUT.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
