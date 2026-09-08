#!/usr/bin/env python3
"""Generate the Kaval progress dashboard.

Reads the repository and renders docs/dashboard.html. Nothing here records
status: ROADMAP.md is the single source of truth, and this only reflects it.
That is deliberate -- a dashboard holding its own copy of "what's done" drifts
within two sessions and then actively misleads.

    python scripts/dashboard.py
    make dashboard

Python 3.11 stdlib only. No pip install.
"""

from __future__ import annotations

import html
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "dashboard.html"


# ─────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────

STATUS_LABEL = {" ": "not started", "~": "in progress", "x": "done"}


@dataclass
class Phase:
    num: int
    title: str
    weeks: str
    marker: str
    tasks: list[tuple[bool, str]] = field(default_factory=list)
    exit_gate: str = ""

    @property
    def done(self) -> int:
        return sum(1 for ok, _ in self.tasks if ok)

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def pct(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0

    @property
    def state(self) -> str:
        return STATUS_LABEL.get(self.marker, "not started")


def read_roadmap(path: Path) -> list[Phase]:
    head = re.compile(r"^## Phase (\d+) — (.+?) · weeks? (.+?) · `\[(.)\]`")
    task = re.compile(r"^- \[([ x])\] (.+)")
    gate = re.compile(r"^\*\*Exit gate:\*\* (.+)")

    phases: list[Phase] = []
    current: Phase | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        if m := head.match(line):
            current = Phase(int(m[1]), m[2].strip(), m[3].strip(), m[4])
            phases.append(current)
        elif current is None:
            continue
        elif m := task.match(line):
            current.tasks.append((m[1] == "x", m[2].strip()))
        elif m := gate.match(line):
            current.exit_gate = m[1].strip()

    return phases


def read_adrs(d: Path) -> list[dict]:
    num_re = re.compile(r"^# ADR-(\d+) — (.+)")
    st_re = re.compile(r"^- \*\*Status:\*\* (.+)")

    out = []
    for f in sorted(d.glob("[0-9]*.md")):
        text = f.read_text(encoding="utf-8")
        n = num_re.search(text, re.M) or re.search(num_re.pattern, text, re.M)
        s = re.search(st_re.pattern, text, re.M)
        if not n:
            continue
        status = s[1].strip() if s else "unknown"
        out.append({
            "id": f"ADR-{n[1]}",
            "title": n[2].strip(),
            "status": status,
            "open": status.lower().startswith("proposed"),
            "file": f.name,
        })
    return out


def read_labs(d: Path) -> list[dict]:
    title_re = re.compile(r"^# Lab (\d+) — (.+)", re.M)
    meta_re = re.compile(r"^\*\*Phase:\*\* (\S+).*?\*\*Cost:\*\* (\S+)", re.M)

    out = []
    for f in sorted(d.glob("lab-*.md")):
        text = f.read_text(encoding="utf-8")
        t = title_re.search(text)
        if not t:
            continue
        meta = meta_re.search(text)
        done = len(re.findall(r"^- \[x\]", text, re.M))
        total = done + len(re.findall(r"^- \[ \]", text, re.M))
        out.append({
            "id": f"Lab {t[1]}",
            "title": t[2].strip(),
            "phase": meta[1] if meta else "?",
            "cost": meta[2] if meta else "?",
            "done": done,
            "total": total,
            "file": f.name,
        })
    return out


def read_journal(d: Path) -> tuple[list[dict], list[str]]:
    """Return (sessions newest-first, open threads from the newest entry)."""
    sessions = []
    for f in sorted(d.glob("[0-9]*.md"), reverse=True):
        text = f.read_text(encoding="utf-8")
        phase = re.search(r"^\*\*Phase:\*\* (.+)", text, re.M)
        done = len(re.findall(r"^- ", text, re.M))
        sessions.append({
            "date": f.stem,
            "phase": phase[1].strip() if phase else "",
            "lines": done,
            "text": text,
            "file": f.name,
        })

    threads: list[str] = []
    if sessions:
        block = re.search(
            r"^## Open threads\s*\n(.*?)(?=\n## |\Z)", sessions[0]["text"], re.M | re.S
        )
        if block:
            # Bullets may wrap across lines; join continuations back on.
            for raw in re.split(r"\n(?=- )", block[1].strip()):
                item = " ".join(raw.strip().lstrip("- ").split())
                if item:
                    threads.append(item)
    return sessions, threads


def git_log(n: int = 8) -> list[dict]:
    try:
        raw = subprocess.run(
            ["git", "log", f"-{n}", "--date=short", "--format=%h\x1f%ad\x1f%s"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    out = []
    for line in raw.strip().splitlines():
        if line.count("\x1f") == 2:
            h, d, s = line.split("\x1f")
            out.append({"hash": h, "date": d, "subject": s})
    return out


# ─────────────────────────────────────────────────────────────
# Markdown
# ─────────────────────────────────────────────────────────────
#
# Hand-rolled against stdlib only. The input is our own documents, not
# arbitrary Markdown, so a focused converter is enough -- and it keeps the
# zero-dependency property that lets this run on a machine with no pip.
# Supports: headings, paragraphs, lists, fenced code, tables, blockquotes,
# horizontal rules, and inline code/bold/italic/links.

def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    # Code first: its contents must not be touched by the other rules.
    holds: list[str] = []

    def stash(m: re.Match) -> str:
        holds.append(f"<code>{m[1]}</code>")
        return f"\x00{len(holds) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)

    def link(m: re.Match) -> str:
        text, href = m[1], m[2]
        # Repo-relative links are dead once this is published as a single page.
        # Keep the reference visible as a path instead of a broken anchor.
        if not re.match(r"^(https?://|#|mailto:)", href):
            return f"<code>{text}</code>"
        return f'<a href="{href}">{text}</a>'

    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: holds[int(m[1])], s)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def markdown_to_html(md: str) -> str:
    out: list[str] = []
    lines = md.split("\n")
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        # fenced code
        if line.startswith("```"):
            i += 1
            body = []
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(body))}</code></pre>")
            continue

        # table -- header, separator, then rows
        if line.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            head = _cells(line)
            i += 2
            rows = []
            while i < n and lines[i].startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            tb = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>')
            continue

        # heading
        if m := re.match(r"^(#{1,4}) +(.+)", line):
            lvl = len(m[1])
            out.append(f"<h{lvl}>{_inline(m[2])}</h{lvl}>")
            i += 1
            continue

        # horizontal rule
        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        # blockquote
        if line.startswith(">"):
            body = []
            while i < n and lines[i].startswith(">"):
                body.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + "<br>".join(_inline(b) for b in body if b) + "</blockquote>")
            continue

        # list -- ordered or unordered, one level
        if re.match(r"^(\d+\.|[-*]) +", line):
            ordered = bool(re.match(r"^\d+\.", line))
            items: list[str] = []
            while i < n and re.match(r"^(\d+\.|[-*]) +", lines[i]):
                items.append(re.sub(r"^(\d+\.|[-*]) +", "", lines[i]))
                i += 1
                # fold wrapped continuation lines into the current item
                while i < n and lines[i].strip() and not re.match(r"^(\d+\.|[-*]|#|\||>|```)", lines[i]):
                    items[-1] += " " + lines[i].strip()
                    i += 1
            tag = "ol" if ordered else "ul"
            body = "".join(f"<li>{_inline(x)}</li>" for x in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        # blank
        if not line.strip():
            i += 1
            continue

        # paragraph
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4} |[-*] |\d+\.|\||>|```|-{3,}$)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(x.strip() for x in para))}</p>")

    return "\n".join(out)


@dataclass
class Learn:
    num: int
    slug: str
    title: str
    written_from: str
    body: str
    terms: int


def read_learn(d: Path) -> list[Learn]:
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("phase-*.md")):
        text = f.read_text(encoding="utf-8")
        t = re.search(r"^# Phase (\d+) — (.+)", text, re.M)
        if not t:
            continue
        wf = re.search(r"\*\*Written from:\*\* *(\w+)", text)
        # Glossary rows: table lines under the Glossary heading.
        gl = re.search(r"^## Glossary\s*\n(.*?)(?=\n## |\Z)", text, re.M | re.S)
        terms = len(re.findall(r"^\| \*\*", gl[1], re.M)) if gl else 0
        out.append(Learn(
            num=int(t[1]),
            slug=f.stem,
            title=t[2].strip(),
            written_from=(wf[1] if wf else "theory"),
            body=markdown_to_html(text),
            terms=terms,
        ))
    return sorted(out, key=lambda x: x.num)


# ─────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────

def e(s: str) -> str:
    return html.escape(str(s), quote=True)


def md_inline(s: str) -> str:
    """Escape, then honour the small subset of Markdown used in these docs."""
    s = e(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\w)", r"<em>\1</em>", s)
    return s


CSS = """
:root{
  --paper:#F5F6F3; --surface:#FFFFFF; --raised:#FBFCFA;
  --ink:#10161A; --ink-2:#47555A; --ink-3:#7C8A8E;
  --rule:#DDE3E0; --rule-strong:#C3CCC8;
  --accent:#0E6E63; --accent-soft:#E2EFEC;
  --ok:#2F7A46; --warn:#9A6B12; --crit:#A63B2C;
  --crit-soft:#FBEBE8; --warn-soft:#FAF1DF;
  --track:#E7EBE8;
  --shadow:0 1px 2px rgba(16,22,26,.06);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#0D1113; --surface:#151B1E; --raised:#1A2225;
    --ink:#E8EDEB; --ink-2:#9AAAA8; --ink-3:#6B7B7A;
    --rule:#263033; --rule-strong:#37454A;
    --accent:#4FBFAE; --accent-soft:#16302D;
    --ok:#5BB273; --warn:#D2A24C; --crit:#E0705C;
    --crit-soft:#2E1A17; --warn-soft:#2A2314;
    --track:#232C30;
    --shadow:0 1px 2px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"]{
  --paper:#0D1113; --surface:#151B1E; --raised:#1A2225;
  --ink:#E8EDEB; --ink-2:#9AAAA8; --ink-3:#6B7B7A;
  --rule:#263033; --rule-strong:#37454A;
  --accent:#4FBFAE; --accent-soft:#16302D;
  --ok:#5BB273; --warn:#D2A24C; --crit:#E0705C;
  --crit-soft:#2E1A17; --warn-soft:#2A2314;
  --track:#232C30;
  --shadow:0 1px 2px rgba(0,0,0,.3);
}

*{box-sizing:border-box}
body{
  background:var(--paper); color:var(--ink);
  font-family:Archivo,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1040px; margin:0 auto; padding:32px 20px 72px}
code,.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
code{font-size:.88em; background:var(--track); padding:.1em .35em; border-radius:3px}
a{color:var(--accent)}
h1,h2,h3{text-wrap:balance; margin:0}
.eyebrow{
  font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:500;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ink-3);
}
section{margin-top:44px}
.sec-head{
  display:flex; align-items:baseline; gap:12px;
  padding-bottom:10px; border-bottom:1px solid var(--rule-strong); margin-bottom:18px;
}
.sec-head h2{font-size:15px; font-weight:600; letter-spacing:.01em}
.sec-head .count{
  font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-3);
  margin-left:auto; font-variant-numeric:tabular-nums;
}

/* ── masthead ─────────────────────────────────────────── */
.mast{display:flex; flex-wrap:wrap; gap:24px; align-items:flex-end; justify-content:space-between}
.mast h1{font-size:30px; font-weight:700; letter-spacing:-.02em; line-height:1.1}
.mast .mal{color:var(--accent); font-weight:600}
.mast .sub{color:var(--ink-2); font-size:13.5px; margin-top:6px; max-width:56ch}

.readout{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(112px,1fr));
  gap:1px; background:var(--rule); border:1px solid var(--rule);
  border-radius:6px; overflow:hidden; margin-top:26px;
}
.readout div{background:var(--surface); padding:12px 14px}
.readout dt{
  font-family:"IBM Plex Mono",monospace; font-size:10px; font-weight:500;
  letter-spacing:.12em; text-transform:uppercase; color:var(--ink-3);
}
.readout dd{
  margin:5px 0 0; font-family:"IBM Plex Mono",monospace;
  font-size:20px; font-weight:500; font-variant-numeric:tabular-nums;
  letter-spacing:-.01em;
}
.readout dd small{font-size:12px; color:var(--ink-3); font-weight:400}

/* ── blockers ─────────────────────────────────────────── */
.blockers{
  border:1px solid var(--crit); border-left:4px solid var(--crit);
  background:var(--crit-soft); border-radius:5px; padding:16px 18px;
}
.blockers.clear{border-color:var(--ok); border-left-color:var(--ok); background:var(--accent-soft)}
.blockers h2{font-size:13px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; color:var(--crit)}
.blockers.clear h2{color:var(--ok)}
.blockers ul{margin:12px 0 0; padding-left:20px; display:flex; flex-direction:column; gap:9px}
.blockers li{font-size:14px; line-height:1.5}

/* ── phase ladder ─────────────────────────────────────── */
.ladder{display:flex; flex-direction:column; gap:1px; background:var(--rule);
        border:1px solid var(--rule); border-radius:6px; overflow:hidden}
.ph{background:var(--surface); padding:13px 16px; display:grid;
    grid-template-columns:34px 1fr 132px 74px; gap:14px; align-items:center}
.ph.now{background:var(--raised); box-shadow:inset 3px 0 0 var(--accent)}
.ph.done .pname{color:var(--ink-2)}
.pnum{font-family:"IBM Plex Mono",monospace; font-size:17px; font-weight:500;
      color:var(--ink-3); font-variant-numeric:tabular-nums}
.ph.now .pnum{color:var(--accent); font-weight:600}
.pname{font-size:14.5px; font-weight:500}
.pmeta{font-size:12px; color:var(--ink-3); margin-top:2px}
.bar{height:5px; background:var(--track); border-radius:3px; overflow:hidden}
.bar span{display:block; height:100%; background:var(--accent); border-radius:3px}
.ph.done .bar span{background:var(--ok)}
.ptally{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-2);
        text-align:right; font-variant-numeric:tabular-nums}
.pill{display:inline-block; font-family:"IBM Plex Mono",monospace; font-size:10px;
      font-weight:500; letter-spacing:.08em; text-transform:uppercase;
      padding:2px 7px; border-radius:3px; border:1px solid currentColor}
.pill.local{color:var(--ink-3)}
.pill.paused{color:var(--warn)}
.pill.alwayson{color:var(--accent)}

.detail{background:var(--raised); padding:4px 16px 18px 64px; border-top:1px dashed var(--rule)}
.tasks{list-style:none; margin:14px 0 0; padding:0; display:flex; flex-direction:column; gap:7px}
.tasks li{display:flex; gap:10px; font-size:13.5px; align-items:baseline}
.tasks .box{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-3); flex:none}
.tasks li.ok .box{color:var(--ok)}
.tasks li.ok span:last-child{color:var(--ink-3); text-decoration:line-through}
.gate{margin-top:16px; padding:10px 13px; background:var(--surface);
      border:1px solid var(--rule); border-radius:4px; font-size:13px}
.gate b{font-family:"IBM Plex Mono",monospace; font-size:10px; font-weight:500;
        letter-spacing:.12em; text-transform:uppercase; color:var(--ink-3);
        display:block; margin-bottom:4px}

/* ── two-up ───────────────────────────────────────────── */
.two{display:grid; grid-template-columns:1fr 1fr; gap:40px}
.rows{display:flex; flex-direction:column}
.row{display:flex; gap:12px; align-items:baseline; padding:9px 0; border-bottom:1px solid var(--rule)}
.row:last-child{border-bottom:0}
.row .k{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-3); flex:none; min-width:56px}
.row .v{flex:1; font-size:13.5px}
.row .t{font-family:"IBM Plex Mono",monospace; font-size:11.5px; color:var(--ink-3);
        margin-left:auto; flex:none; font-variant-numeric:tabular-nums}
.tag{font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.07em;
     text-transform:uppercase; padding:2px 6px; border-radius:3px; flex:none}
.tag.open{background:var(--warn-soft); color:var(--warn)}
.tag.settled{background:var(--accent-soft); color:var(--accent)}

/* ── cost ─────────────────────────────────────────────── */
.gauge{border:1px solid var(--rule); border-radius:6px; background:var(--surface); padding:18px}
.gauge .top{display:flex; align-items:baseline; gap:10px}
.gauge .amt{font-family:"IBM Plex Mono",monospace; font-size:30px; font-weight:500;
            font-variant-numeric:tabular-nums; letter-spacing:-.02em}
.gauge .of{color:var(--ink-3); font-size:13px}
.track{height:9px; background:var(--track); border-radius:5px; margin:14px 0 6px; position:relative; overflow:hidden}
.track span{display:block; height:100%; background:var(--ok); border-radius:5px}
.ticks{display:flex; justify-content:space-between; font-family:"IBM Plex Mono",monospace;
       font-size:10.5px; color:var(--ink-3); font-variant-numeric:tabular-nums}
.costnote{margin-top:14px; font-size:13px; color:var(--ink-2); line-height:1.55}

/* ── links ────────────────────────────────────────────── */
.links{display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:1px;
       background:var(--rule); border:1px solid var(--rule); border-radius:6px; overflow:hidden}
.links a,.links div.dead{background:var(--surface); padding:13px 15px; text-decoration:none; display:block}
.links a:hover{background:var(--accent-soft)}
.links .lab{font-size:13.5px; font-weight:500; color:var(--ink)}
.links a .lab{color:var(--accent)}
.links .note{font-size:12px; color:var(--ink-3); margin-top:3px}
.links .dead .lab{color:var(--ink-3)}
.links .dead .lab::after{content:" — not yet created"; font-family:"IBM Plex Mono",monospace;
                         font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--warn)}

/* ── learn: link on a phase row ────────────────────────── */
.learn-link{font-family:"IBM Plex Mono",monospace; font-size:10px; font-weight:500;
  letter-spacing:.09em; text-transform:uppercase; text-decoration:none;
  color:var(--accent); border:1px solid var(--accent); border-radius:3px;
  padding:2px 7px; white-space:nowrap}
.learn-link:hover{background:var(--accent); color:var(--surface)}
.pmeta .learn-link{margin-left:6px}

/* ── learn: the concept page itself ───────────────────── */
.view[hidden]{display:none}
.lnav{display:flex; align-items:center; gap:14px; flex-wrap:wrap;
      padding-bottom:14px; border-bottom:1px solid var(--rule-strong); margin-bottom:8px}
.lnav a{font-family:"IBM Plex Mono",monospace; font-size:11.5px; letter-spacing:.06em;
        text-transform:uppercase; text-decoration:none}
.lnav a:hover{text-decoration:underline}
.lnav .spacer{margin-left:auto}
.lnav .off{color:var(--ink-3); pointer-events:none}
.wf{font-family:"IBM Plex Mono",monospace; font-size:10px; letter-spacing:.08em;
    text-transform:uppercase; padding:2px 7px; border-radius:3px}
.wf.experience{background:var(--accent-soft); color:var(--accent)}
.wf.theory{background:var(--warn-soft); color:var(--warn)}

.prose{max-width:68ch}
.prose h1{font-size:27px; font-weight:700; letter-spacing:-.02em; margin:22px 0 0}
.prose h2{font-size:19px; font-weight:600; letter-spacing:-.01em; margin:42px 0 0;
          padding-bottom:7px; border-bottom:1px solid var(--rule)}
.prose h3{font-size:15.5px; font-weight:600; margin:30px 0 0; color:var(--accent)}
.prose h4{font-size:14px; font-weight:600; margin:22px 0 0}
.prose p{margin:13px 0 0}
.prose ul,.prose ol{margin:13px 0 0; padding-left:24px; display:flex;
                    flex-direction:column; gap:7px}
.prose li{padding-left:3px}
.prose blockquote{margin:16px 0 0; padding:11px 15px; background:var(--raised);
  border-left:3px solid var(--accent); border-radius:0 4px 4px 0;
  font-size:13px; color:var(--ink-2)}
.prose blockquote code{background:transparent; padding:0}
.prose pre{margin:16px 0 0; padding:13px 15px; background:var(--raised);
  border:1px solid var(--rule); border-radius:5px; overflow-x:auto; font-size:12.5px;
  line-height:1.5}
.prose pre code{background:transparent; padding:0; font-size:inherit}
.prose hr{margin:38px 0 0; border:0; border-top:1px solid var(--rule)}
.prose .tw{margin:16px 0 0; overflow-x:auto; border:1px solid var(--rule); border-radius:5px}
.prose table{border-collapse:collapse; width:100%; font-size:13px}
.prose th{text-align:left; font-family:"IBM Plex Mono",monospace; font-size:10.5px;
  font-weight:500; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-3);
  padding:9px 13px; background:var(--raised); border-bottom:1px solid var(--rule)}
.prose td{padding:9px 13px; border-bottom:1px solid var(--rule); vertical-align:top}
.prose tbody tr:last-child td{border-bottom:0}
.prose a{text-decoration:underline; text-underline-offset:2px}

.index{display:grid; grid-template-columns:repeat(auto-fit,minmax(232px,1fr)); gap:1px;
       background:var(--rule); border:1px solid var(--rule); border-radius:6px; overflow:hidden}
.index a{background:var(--surface); padding:13px 15px; text-decoration:none; display:block}
.index a:hover{background:var(--accent-soft)}
.index .n{font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--ink-3)}
.index .t{font-size:13.5px; font-weight:500; color:var(--ink); margin-top:2px}
.index .m{font-size:11.5px; color:var(--ink-3); margin-top:4px}

footer{margin-top:56px; padding-top:16px; border-top:1px solid var(--rule);
       font-size:12px; color:var(--ink-3); display:flex; flex-wrap:wrap; gap:14px}

a:focus-visible,summary:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
@media (max-width:720px){
  .wrap{padding:22px 15px 56px}
  .mast h1{font-size:25px}
  .ph{grid-template-columns:26px 1fr; row-gap:9px}
  .ph .barwrap{grid-column:1/-1}
  .ptally{text-align:left; grid-column:1/-1}
  .detail{padding-left:22px}
  .two{grid-template-columns:1fr; gap:34px}
  .row{flex-wrap:wrap}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
"""


def render(cfg: dict, phases: list[Phase], adrs: list[dict], labs: list[dict],
           sessions: list[dict], threads: list[str], commits: list[dict],
           learn: list[Learn]) -> str:
    proj = cfg["project"]
    cost = cfg["cost"]
    posture = cfg.get("posture", {})

    today = date.today()
    started = date.fromisoformat(proj["started"])
    target = date.fromisoformat(proj["target_v1"])
    elapsed = (today - started).days
    remaining = (target - today).days

    total_tasks = sum(p.total for p in phases)
    done_tasks = sum(p.done for p in phases)
    overall = round(100 * done_tasks / total_tasks) if total_tasks else 0

    current = next((p for p in phases if p.marker == "~"),
                   next((p for p in phases if p.marker == " "), phases[-1]))

    open_adrs = [a for a in adrs if a["open"]]
    # An open thread that already names the ADR covers it; don't say it twice.
    blockers = list(threads) + [
        f'{a["id"]} <em>{e(a["title"])}</em> is still Proposed — the decision is not settled.'
        for a in open_adrs
        if not any(a["id"].lower() in t.lower() for t in threads)
    ]

    mtd = float(cost.get("mtd_usd", 0))
    ceiling = float(cost["ceiling_usd"])
    pct_cost = min(100, round(100 * mtd / ceiling)) if ceiling else 0

    by_num = {l.num: l for l in learn}

    # ── masthead ──
    parts = [f"""<div class="wrap">
<div class="view" id="view-board">
<header class="mast">
  <div>
    <div class="eyebrow">Progress · phase {current.num} of {len(phases)}</div>
    <h1>{e(proj["name"])} <span class="mal">{e(proj["tagline"].split("—")[0].strip())}</span></h1>
    <p class="sub">Autonomous ops agent with human-in-the-loop approval. Watches a Kubernetes
    cluster and an AWS bill, diagnoses with a self-hosted Gemma&nbsp;3&nbsp;1B, and acts only
    after a tap on a phone.</p>
  </div>
</header>

<dl class="readout">
  <div><dt>Complete</dt><dd>{overall}<small>%</small></dd></div>
  <div><dt>Tasks</dt><dd>{done_tasks}<small>/{total_tasks}</small></dd></div>
  <div><dt>Current</dt><dd>{current.num}<small>&nbsp;{e(current.state)}</small></dd></div>
  <div><dt>Day</dt><dd>{elapsed}<small>&nbsp;since start</small></dd></div>
  <div><dt>To v1</dt><dd>{remaining}<small>&nbsp;days</small></dd></div>
  <div><dt>Spend MTD</dt><dd>${mtd:.2f}</dd></div>
</dl>"""]

    # ── blockers ──
    if blockers:
        items = "\n".join(f"    <li>{md_inline(b) if '<em>' not in b else b}</li>" for b in blockers)
        parts.append(f"""
<section>
  <div class="blockers">
    <h2>Blocked on — {len(blockers)}</h2>
    <ul>
{items}
    </ul>
  </div>
</section>""")
    else:
        parts.append("""
<section><div class="blockers clear"><h2>Nothing blocked</h2></div></section>""")

    # ── phase ladder ──
    rows = []
    for p in phases:
        pst = posture.get(str(p.num), {})
        cls = "ph"
        if p is current:
            cls += " now"
        if p.marker == "x":
            cls += " done"
        pill = pst.get("state", "")
        pill_cls = pill.replace("-", "")
        lrn = by_num.get(p.num)
        lnk = f'<a class="learn-link" href="#phase-{p.num}">Learn</a>' if lrn else ""
        rows.append(f"""  <div class="{cls}">
    <div class="pnum">{p.num}</div>
    <div>
      <div class="pname">{e(p.title)}</div>
      <div class="pmeta">weeks {e(p.weeks)} · <span class="pill {pill_cls}">{e(pill)}</span> {e(pst.get("cost",""))}{lnk}</div>
    </div>
    <div class="barwrap"><div class="bar"><span style="width:{p.pct}%"></span></div></div>
    <div class="ptally">{p.done}/{p.total}</div>
  </div>""")
        if p is current:
            tasks = "\n".join(
                f'      <li class="{"ok" if ok else ""}"><span class="box">{"[x]" if ok else "[ ]"}</span>'
                f"<span>{md_inline(t)}</span></li>"
                for ok, t in p.tasks
            )
            rows.append(f"""  <div class="detail">
    <ul class="tasks">
{tasks}
    </ul>
    <div class="gate"><b>Exit gate</b>{md_inline(p.exit_gate)}</div>
  </div>""")

    parts.append(f"""
<section>
  <div class="sec-head"><div class="eyebrow">Sequential · gated</div><h2>Phases</h2>
    <div class="count">{done_tasks}/{total_tasks} tasks</div></div>
  <div class="ladder">
{chr(10).join(rows)}
  </div>
</section>""")

    # ── learning index ──
    if learn:
        from_exp = sum(1 for l in learn if l.written_from == "experience")
        cards = "\n".join(
            f'  <a href="#phase-{l.num}"><div class="n">Phase {l.num}</div>'
            f'<div class="t">{e(l.title)}</div>'
            f'<div class="m">{l.terms} terms · written from {e(l.written_from)}</div></a>'
            for l in learn
        )
        parts.append(f"""
<section>
  <div class="sec-head"><div class="eyebrow">Concepts, not steps</div><h2>Learn</h2>
    <div class="count">{from_exp}/{len(learn)} from experience</div></div>
  <div class="index">
{cards}
  </div>
</section>""")

    # ── cost ──
    prov = "Nothing provisioned yet." if not cost.get("provisioned") else "Live."
    parts.append(f"""
<section>
  <div class="sec-head"><div class="eyebrow">Guardrails before compute</div><h2>Cost</h2>
    <div class="count">ceiling ${ceiling:.0f}/mo</div></div>
  <div class="gauge">
    <div class="top"><span class="amt">${mtd:.2f}</span><span class="of">month to date, of ${ceiling:.0f} ceiling</span></div>
    <div class="track"><span style="width:{pct_cost}%"></span></div>
    <div class="ticks"><span>$0</span><span>$18 alert</span><span>$22 alert</span><span>$24 hard stop</span></div>
    <p class="costnote">{prov} Projected total for the whole project,
    {started:%b&nbsp;%Y} → {target:%b&nbsp;%Y}: <strong>~${cost["projected_total"]}</strong>;
    budget <strong>${cost["budget_with_buffer"]}</strong> to absorb one mistake. Phase
    {current.num} run posture is <strong>{e(posture.get(str(current.num),{}).get("state","—"))}</strong>
    at {e(posture.get(str(current.num),{}).get("cost","—"))}.</p>
  </div>
</section>""")

    # ── decisions + labs ──
    adr_rows = "\n".join(
        f"""    <div class="row"><span class="k">{e(a["id"])}</span>
      <span class="v">{e(a["title"])}</span>
      <span class="tag {"open" if a["open"] else "settled"}">{e(a["status"].split()[0])}</span></div>"""
        for a in adrs
    ) or '    <div class="row"><span class="v">None yet.</span></div>'

    lab_rows = "\n".join(
        f"""    <div class="row"><span class="k">{e(l["id"])}</span>
      <span class="v">{e(l["title"])}</span>
      <span class="t">{l["done"]}/{l["total"]}</span></div>"""
        for l in labs
    ) or '    <div class="row"><span class="v">None yet.</span></div>'

    parts.append(f"""
<section class="two">
  <div>
    <div class="sec-head"><h2>Decisions</h2>
      <div class="count">{len(open_adrs)} open</div></div>
    <div class="rows">
{adr_rows}
    </div>
  </div>
  <div>
    <div class="sec-head"><h2>Labs</h2>
      <div class="count">{len(labs)} written</div></div>
    <div class="rows">
{lab_rows}
    </div>
  </div>
</section>""")

    # ── activity ──
    commit_rows = "\n".join(
        f"""    <div class="row"><span class="k">{e(c["date"])}</span>
      <span class="v">{e(c["subject"])}</span>
      <span class="t">{e(c["hash"])}</span></div>"""
        for c in commits
    ) or '    <div class="row"><span class="v">No commits yet.</span></div>'

    session_rows = "\n".join(
        f"""    <div class="row"><span class="k">{e(s["date"])}</span>
      <span class="v">{e(s["phase"])}</span></div>"""
        for s in sessions
    ) or '    <div class="row"><span class="v">No sessions logged.</span></div>'

    last = sessions[0]["date"] if sessions else "—"
    gap = (today - date.fromisoformat(last)).days if sessions else 0

    parts.append(f"""
<section class="two">
  <div>
    <div class="sec-head"><h2>Commits</h2><div class="count">{len(commits)} recent</div></div>
    <div class="rows">
{commit_rows}
    </div>
  </div>
  <div>
    <div class="sec-head"><h2>Sessions</h2>
      <div class="count">{"today" if gap == 0 else f"{gap}d since last"}</div></div>
    <div class="rows">
{session_rows}
    </div>
  </div>
</section>""")

    # ── links ──
    link_cards = []
    for l in cfg.get("links", []):
        if l.get("url"):
            link_cards.append(
                f'  <a href="{e(l["url"])}"><div class="lab">{e(l["label"])}</div>'
                f'<div class="note">{e(l.get("note",""))}</div></a>'
            )
        else:
            link_cards.append(
                f'  <div class="dead"><div class="lab">{e(l["label"])}</div>'
                f'<div class="note">{e(l.get("note",""))}</div></div>'
            )

    parts.append(f"""
<section>
  <div class="sec-head"><h2>Links &amp; notes</h2></div>
  <div class="links">
{chr(10).join(link_cards)}
  </div>
</section>
</div>""")

    # ── concept pages ──
    for idx, l in enumerate(learn):
        prev = learn[idx - 1] if idx > 0 else None
        nxt = learn[idx + 1] if idx + 1 < len(learn) else None
        pv = (f'<a href="#phase-{prev.num}">← Phase {prev.num}</a>'
              if prev else '<a class="off">← Phase 0</a>')
        nx = (f'<a href="#phase-{nxt.num}">Phase {nxt.num} →</a>'
              if nxt else '<a class="off">End →</a>')
        parts.append(f"""
<div class="view" id="view-phase-{l.num}" hidden>
  <nav class="lnav">
    <a href="#board">↑ Board</a>
    <span class="wf {e(l.written_from)}">written from {e(l.written_from)}</span>
    <span class="spacer"></span>
    {pv}
    {nx}
  </nav>
  <article class="prose">
{l.body}
  </article>
  <nav class="lnav" style="border:0;border-top:1px solid var(--rule-strong);
       padding:14px 0 0;margin:40px 0 0">
    <a href="#board">↑ Board</a><span class="spacer"></span>{pv}{nx}
  </nav>
</div>""")

    parts.append(f"""
<footer>
  <span>Generated {today:%Y-%m-%d} from the repository.</span>
  <span>Status lives in <code>ROADMAP.md</code> — this page only reflects it.</span>
  <span>Regenerate with <code>make dashboard</code>.</span>
</footer>
</div>
<script>
(function () {{
  var views = document.querySelectorAll('.view');
  function show() {{
    var id = (location.hash || '#board').slice(1);
    var target = document.getElementById('view-' + id) || document.getElementById('view-board');
    views.forEach(function (v) {{ v.hidden = (v !== target); }});
    window.scrollTo(0, 0);
  }}
  window.addEventListener('hashchange', show);
  show();
}})();
</script>""")

    return f"""<title>Kaval Watchtower</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
{"".join(parts)}
"""


def main() -> None:
    cfg = tomllib.loads((ROOT / "dashboard.toml").read_text(encoding="utf-8"))
    phases = read_roadmap(ROOT / "ROADMAP.md")
    adrs = read_adrs(ROOT / "docs" / "adr")
    labs = read_labs(ROOT / "docs" / "labs")
    sessions, threads = read_journal(ROOT / "docs" / "journal")
    commits = git_log()
    learn = read_learn(ROOT / "docs" / "learn")

    OUT.write_text(
        render(cfg, phases, adrs, labs, sessions, threads, commits, learn), encoding="utf-8"
    )

    done = sum(p.done for p in phases)
    total = sum(p.total for p in phases)
    exp = sum(1 for l in learn if l.written_from == "experience")
    print(f"  {OUT.relative_to(ROOT)}")
    print(f"  {len(phases)} phases · {done}/{total} tasks · {len(adrs)} ADRs "
          f"({sum(a['open'] for a in adrs)} open) · {len(labs)} labs · {len(threads)} open threads")
    print(f"  {len(learn)} concept pages ({exp} from experience) · "
          f"{sum(l.terms for l in learn)} glossary terms")


if __name__ == "__main__":
    main()
