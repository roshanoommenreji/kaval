#!/usr/bin/env python3
"""Measure the local model shortlist on the real dev server (KAV-22).

    make dev        # the stack must be running on the dev server
    make bench      # this script: ~30 minutes, ~1 cent of server time

    python scripts/dev/bench_models.py --rescore .build/bench/models-<stamp>.json
    python scripts/dev/bench_models.py --memory  .build/bench/models-<stamp>.json

What it measures, per model, on the prod-sized 4 GB Graviton box:
  RAM          resident memory of the Ollama container's processes with the model loaded
               (/proc VmRSS). Not `docker stats`: that also counts file cache, e.g. from
               downloading the other models, and overstated gemma3:1b as ~2 GB
  load         seconds to load the weights, cold (the first request after a model switch)
  first token  load + prompt processing, from Ollama's own server-side timers
  speed        generated tokens per second
  JSON         strict: the whole reply is JSON. unfenced: JSON once a Markdown code fence
               is removed. schema: it has every field a Kaval proposal needs, sane values

What it deliberately does NOT decide: whether the diagnosis is *right*. That's the Phase 2
eval harness. This picks what fits and runs acceptably; the evals pick the winner.

Method (agreed 2026-09-26): one server, models run one at a time (OLLAMA_MAX_LOADED_MODELS=1),
3 rounds with the model order rotated each round so no model always runs first,
temperature 0, fixed seed, fixed 4096-token context. Stdlib only.
Timings come from Ollama's own counters, so the SSH tunnel's latency doesn't pollute them.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = "kaval-devbox"
CONTAINER = "kaval-ollama-1"
LOCAL_PORT = 11436  # 11434 is often a laptop Ollama app; 11435 is `make dev-tunnel`
OLLAMA = f"http://127.0.0.1:{LOCAL_PORT}"

# All 4-bit, so the comparison is like for like. Gemma 4 E2B was on the first shortlist and
# was dropped before measuring: 7.2 GB at q4 (4.3 GB QAT) cannot fit a 4 GB node.
MODELS = [
    "gemma3:1b",
    "gemma3:1b-it-qat",
    "llama3.2:1b-instruct-q4_K_M",
    "qwen3:1.7b",
]
ROUNDS = 3
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 512}

SYSTEM = """You are the diagnosis component of Kaval, an operations agent. You have read-only
access and you never act; you propose. Reply with ONLY one JSON object, no prose, no code
fences, with exactly these fields:
{"summary": string, "root_cause": string, "confidence": number between 0 and 1,
 "actions": [{"type": one of "restart_pod" | "rollback_deployment" | "patch_resources" |
   "scale_deployment" | "delete_volume" | "notify_human",
   "target": string, "reversible": boolean,
   "blast_radius": one of "pod" | "deployment" | "namespace" | "cluster" | "account"}]}"""

INCIDENTS = {
    "oom-crashloop": """Namespace shop, pod payments-api-7d9f8c-x2k4q is in CrashLoopBackOff with
7 restarts in 12 minutes. Last state: Terminated, reason OOMKilled, exit code 137. Container
memory limit 256Mi. The deployment was updated 20 minutes ago; the change raised
CACHE_SIZE_MB from 64 to 192. Other replicas are running on the previous revision.""",
    "idle-volume": """Cost signal: EBS volume vol-0abc (100 GB gp3, ap-south-1a) has been
unattached for 21 days, costing about $8 a month. It carries no Project tag. Its last
attachment was to an instance terminated during a load test. There is no snapshot of it.""",
    "exec-format": """Namespace kaval, deployment checkout rolled out image sha-3f9a2c 4 minutes
ago. All 3 new pods fail immediately with: exec /usr/local/bin/python: exec format error.
The node is arm64 (t4g.medium, Graviton). The previous image sha-81be07 ran fine.""",
}

ACTION_TYPES = {"restart_pod", "rollback_deployment", "patch_resources",
                "scale_deployment", "delete_volume", "notify_human"}
BLAST = {"pod", "deployment", "namespace", "cluster", "account"}

# The same contract as a JSON schema, for --constrained: Ollama then restricts decoding so the
# model can only produce text that matches it. That's how the agent will call the model.
PROPOSAL_SCHEMA = {
    "type": "object",
    "required": ["summary", "root_cause", "confidence", "actions"],
    "properties": {
        "summary": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "actions": {"type": "array", "minItems": 1, "items": {
            "type": "object",
            "required": ["type", "target", "reversible", "blast_radius"],
            "properties": {
                "type": {"type": "string", "enum": sorted(ACTION_TYPES)},
                "target": {"type": "string"},
                "reversible": {"type": "boolean"},
                "blast_radius": {"type": "string", "enum": sorted(BLAST)},
            },
        }},
    },
}


def api(path: str, body: dict[str, Any] | None = None, timeout: float = 900) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{OLLAMA}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── scoring ───────────────────────────────────────────────────────────────


def _unfenced(text: str) -> str:
    """The reply with a Markdown code fence removed, which small models add even when told not
    to. Stripping it is one line in the agent, so it's scored separately from strict JSON."""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return m.group(1) if m else text


def _schema_ok(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    try:
        return bool(
            isinstance(obj["summary"], str) and isinstance(obj["root_cause"], str)
            and isinstance(obj["confidence"], int | float) and 0 <= obj["confidence"] <= 1
            and isinstance(obj["actions"], list) and len(obj["actions"]) >= 1
            and all(a["type"] in ACTION_TYPES and a["blast_radius"] in BLAST
                    and isinstance(a["reversible"], bool) and isinstance(a["target"], str)
                    for a in obj["actions"]))
    except (KeyError, TypeError):
        return False


def score(text: str) -> dict[str, bool]:
    text = text.strip()
    try:
        obj, strict = json.loads(text), True
    except ValueError:
        obj, strict = None, False
    unfenced = strict
    if not strict:
        try:
            obj, unfenced = json.loads(_unfenced(text)), True
        except ValueError:
            pass
    return {"json_strict": strict, "json_unfenced": unfenced, "json_schema": _schema_ok(obj)}


# ── measuring ─────────────────────────────────────────────────────────────


def request(model: str, incident: str, constrained: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "system": SYSTEM, "prompt": INCIDENTS[incident],
                            "stream": False, "options": OPTIONS}
    if constrained:
        body["format"] = PROPOSAL_SCHEMA
    if model.startswith("qwen3"):
        body["think"] = False  # Qwen3 reasons out loud by default; the others don't
    return dict(api("/api/generate", body))


def timings(r: dict[str, Any]) -> dict[str, Any]:
    ns = 1e9
    return {
        "load_s": r.get("load_duration", 0) / ns,
        "ttft_s": (r.get("load_duration", 0) + r.get("prompt_eval_duration", 0)) / ns,
        "prompt_tokens": r.get("prompt_eval_count", 0),
        "gen_tps": r.get("eval_count", 0) / max(r.get("eval_duration", 1) / ns, 1e-9),
        "out_tokens": r.get("eval_count", 0),
        "total_s": r.get("total_duration", 0) / ns,
    }


def generate(model: str, incident: str, constrained: bool = False) -> dict[str, Any]:
    r = request(model, incident, constrained)
    reply = r.get("response", "")
    return {"model": model, "incident": incident, **timings(r), **score(reply), "reply": reply}


def rss_mib() -> float:
    """Resident memory of every process in the Ollama container: the server plus the model
    runner, including the model's weight pages actually in RAM."""
    out = subprocess.run(
        ["docker", "--context", CONTEXT, "exec", CONTAINER, "sh", "-c",
         "cat /proc/[0-9]*/status 2>/dev/null | grep '^VmRSS'"],
        capture_output=True, text=True, timeout=60)
    return sum(int(n) for n in re.findall(r"(\d+) kB", out.stdout)) / 1024


def memory_pass() -> dict[str, Any]:
    """One model at a time: unload everything, measure idle, load the model with one real
    request (nothing else running, so its timings double as an overhead check), measure."""
    result: dict[str, Any] = {}
    for m in MODELS:
        for loaded in api("/api/ps").get("models", []):
            api("/api/generate", {"model": loaded["name"], "keep_alive": 0})
        time.sleep(3)
        idle = rss_mib()
        r = request(m, "oom-crashloop")
        loaded = api("/api/ps").get("models", [])
        result[m] = {"idle_rss_mib": round(idle), "loaded_rss_mib": round(rss_mib()),
                     "ollama_estimate_mib": round(loaded[0]["size"] / 2**20) if loaded else None,
                     **timings(r)}
        print(f"  {m:30} idle {result[m]['idle_rss_mib']} MiB -> loaded "
              f"{result[m]['loaded_rss_mib']} MiB (Ollama's estimate "
              f"{result[m]['ollama_estimate_mib']} MiB), cold load {result[m]['load_s']:.1f}s, "
              f"{result[m]['gen_tps']:.1f} tok/s")
    return result


def constrained_pass() -> list[dict[str, Any]]:
    """One round, every model and incident, with decoding constrained to PROPOSAL_SCHEMA."""
    runs = []
    for m in MODELS:
        for incident in INCIDENTS:
            res = generate(m, incident, constrained=True)
            runs.append(res)
            print(f"  {m:30} {incident:14} {res['gen_tps']:5.1f} tok/s  schema "
                  f"{'Y' if res['json_schema'] else 'n'}")
    return runs


def quantization() -> dict[str, str]:
    return {m: api("/api/show", {"model": m}).get("details", {}).get("quantization_level", "?")
            for m in MODELS}


def summary(data: dict[str, Any]) -> None:
    runs, mem = data["runs"], data.get("memory", {})
    print("| Model | RAM loaded (MiB) | Load, cold (s) | First token, warm (s) "
          "| Speed (tok/s) | JSON strict | JSON unfenced | Proposal schema |")
    print("|---|---|---|---|---|---|---|---|")
    for m in data["models"]:
        rs = [r for r in runs if r["model"] == m]
        warm = [r["ttft_s"] for r in rs if r["load_s"] <= 0.5]
        cold = mem.get(m, {}).get("load_s") or max(r["load_s"] for r in rs)
        n = len(rs)
        print(f"| `{m}` | {mem.get(m, {}).get('loaded_rss_mib', '—')} | {cold:.1f} "
              f"| {median(warm) if warm else 0:.1f} | {median(r['gen_tps'] for r in rs):.1f} "
              f"| {sum(r['json_strict'] for r in rs)}/{n} "
              f"| {sum(r['json_unfenced'] for r in rs)}/{n} "
              f"| {sum(r['json_schema'] for r in rs)}/{n} |")
    con = data.get("constrained", [])
    if con:
        print("\nConstrained to the proposal schema (Ollama `format`), one round:\n")
        print("| Model | Quantisation | Proposal schema | Speed (tok/s) |")
        print("|---|---|---|---|")
        for m in data["models"]:
            rs = [r for r in con if r["model"] == m]
            print(f"| `{m}` | {data.get('quantization', {}).get(m, '?')} "
                  f"| {sum(r['json_schema'] for r in rs)}/{len(rs)} "
                  f"| {median(r['gen_tps'] for r in rs):.1f} |")


class Tunnel:
    """Its own SSH tunnel to the server's Ollama, closed when done."""

    def __enter__(self) -> Tunnel:
        self.proc = subprocess.Popen(
            ["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
             "-L", f"{LOCAL_PORT}:127.0.0.1:11434", "kaval-devbox"])
        for _ in range(30):
            try:
                api("/api/version", timeout=3)
                return self
            except OSError:
                time.sleep(2)
        self.proc.terminate()
        sys.exit("Ollama not reachable through the tunnel. Is `make dev` running?")

    def __exit__(self, *exc: object) -> None:
        self.proc.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory", metavar="RESULTS",
                        help="add the resident-memory pass to an existing results file")
    parser.add_argument("--constrained", metavar="RESULTS",
                        help="add a schema-constrained round to an existing results file")
    parser.add_argument("--rescore", metavar="RESULTS",
                        help="re-grade saved replies with the current scoring (no server)")
    args = parser.parse_args()

    if args.rescore or args.memory or args.constrained:
        path = Path(args.rescore or args.memory or args.constrained)
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data["runs"]:
            r.update(score(r["reply"]))
        if args.memory or args.constrained:
            with Tunnel():
                data["quantization"] = quantization()
                if args.memory:
                    data["memory"] = memory_pass()
                if args.constrained:
                    data["constrained"] = constrained_pass()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        summary(data)
        return

    with Tunnel():
        version = api("/api/version")["version"]
        print(f"Ollama {version} on the dev server. Pulling any missing models...")
        for m in MODELS:
            api("/api/pull", {"model": m, "stream": False}, timeout=1800)
        runs: list[dict[str, Any]] = []
        for rnd in range(ROUNDS):
            for model in MODELS[rnd:] + MODELS[:rnd]:
                for incident in INCIDENTS:
                    res = generate(model, incident) | {"round": rnd + 1}
                    runs.append(res)
                    print(f"  r{rnd + 1} {model:30} {incident:14} load {res['load_s']:5.1f}s "
                          f"ttft {res['ttft_s']:5.1f}s {res['gen_tps']:5.1f} tok/s  json "
                          + "".join("Y" if res[k] else "n"
                                    for k in ("json_strict", "json_unfenced", "json_schema")))
        print("Resident-memory pass...")
        memory = memory_pass()
        print("Schema-constrained round...")
        constrained = constrained_pass()
        quant = quantization()

    out = ROOT / ".build" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"models-{stamp}.json"
    data = {"ollama": version, "options": OPTIONS, "models": MODELS, "runs": runs,
            "memory": memory, "constrained": constrained, "quantization": quant}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nRaw results: {path.relative_to(ROOT)}\n")
    summary(data)


if __name__ == "__main__":
    main()
