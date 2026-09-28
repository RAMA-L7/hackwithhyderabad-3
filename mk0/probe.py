"""MK0 runtime verification for Mukul's side (docs/phase1-mukul-m0-plan.md).

Stdlib only; imports nothing from rama-m0.

    python3 mk0/probe.py                  # uses .env.live
    python3 mk0/probe.py --env .env.local
    python3 mk0/probe.py --selftest       # offline check of parser/validator/classifier
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PROBE_TIMEOUT_S = 60  # generous on purpose: we are measuring latency, not enforcing it
MAX_TOKENS = 1500  # 060033Z: ~550 needed once reasoning is controlled; 3000 was all reasoning
# Per-provider reasoning control found by S6/S8 (060033Z, 061029Z). Without it both models
# reason until max_tokens and return no JSON.
PROVIDER_OPTIONS = {
    "openrouter": {"reasoning": {"effort": "low"}},
    "baseten": {"chat_template_kwargs": {"thinking": False}},
}
DEMO_BUDGET_MS = 15000
BACKUP_MODEL = "deepseek/deepseek-v4.1-flash"  # F4: same OpenRouter key, supports structured_outputs
FAILOVER_ELIGIBLE = {"TIMEOUT", "UNREACHABLE", "RATE_LIMITED", "UNAVAILABLE", "BAD_RESPONSE"}
REQUIRED_VARS = [
    f"LLM_{role}_{field}"
    for role in ("PRIMARY", "FALLBACK")
    for field in ("PROVIDER", "MODEL", "BASE_URL", "API_KEY")
]

# ---------------------------------------------------------------- real MK5 contract

HYP_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypothesis", "supporting_case_ids", "refutation_conditions", "recommended_next_step"],
    "properties": {
        "hypothesis": {"type": "string"},
        "supporting_case_ids": {"type": "array", "items": {"type": "string"}},
        "refutation_conditions": {"type": "array", "items": {"type": "string"}},
        "recommended_next_step": {"type": "string"},
    },
}
HYP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "items": HYP_ITEM}},
}
# Same schema plus keywords some strict-mode providers reject (S1 decides what may be sent).
HYP_SCHEMA_EXTRA = json.loads(json.dumps(HYP_SCHEMA))
HYP_SCHEMA_EXTRA["properties"]["hypotheses"].update({"minItems": 1, "maxItems": 3})
HYP_SCHEMA_EXTRA["properties"]["hypotheses"]["items"]["properties"]["hypothesis"]["minLength"] = 1

# Synthetic cases shaped like rama-m0 seeds; ids in both real forms (16-hex key, seed-00N).
CANDIDATES = [
    {
        "case_id": "3f9a1c07b2e4d815",
        "relevance_class": "relevant",
        "environment": {"service": "media-uploader", "proxy": "nginx-1.25", "runtime": "node20"},
        "text": "Uploads above 2MB reset the connection; small uploads succeed. Investigation: payload "
        "size sweep, compared threshold with proxy config, read nginx error log (413 entries). Failed "
        "approach: raised client socket timeout to 60s - no effect, proxy closed the stream first. "
        "Root cause: reverse proxy client_max_body_size was 2m. Resolution: raised to 10m and re-ran "
        "the sweep. Outcome: resolved.",
    },
    {
        "case_id": "seed-001",
        "relevance_class": "partial",
        "environment": {"service": "orders-api", "proxy": "envoy-1.4", "runtime": "python3.10"},
        "text": "Intermittent ECONNRESET for payloads above 2MB. Investigation: payload sweep, envoy "
        "upstream log. Failed approach: raised client timeout - proxy closed the stream first. Root "
        "cause: envoy max_request_bytes limit truncated the stream. Resolution: raised to 8MB. "
        "Outcome: resolved.",
    },
    {
        "case_id": "9b21e6d4c0a7f352",
        "relevance_class": "contradictory",
        "environment": {"service": "orders-api", "proxy": "envoy-1.4", "runtime": "python3.10"},
        "text": "HTTP 502 for oversized request bodies. Investigation: traced to upstream dependency, "
        "not the proxy. Failed approach: raised envoy limits - 502 persisted. Root cause: upstream "
        "dependency rejected bodies above its own limit with 502. Resolution: chunked the upload. "
        "Outcome: workaround.",
    },
]
ALLOWED_IDS = {c["case_id"] for c in CANDIDATES}
CURRENT_ISSUE = (
    "photo-api resets connections on uploads over 2 MB; uploads under 2 MB succeed.\n"
    "service=photo-api proxy=nginx-1.24 runtime=python3.11\n"
    "measurement: 20/20 uploads of 2.5MB fail with ECONNRESET, 20/20 uploads of 1MB succeed"
)
RULES = (
    "You assist a debugging engineer. Propose 2-3 hypotheses for the CURRENT ISSUE.\n"
    "Rules:\n"
    "- Past cases are prior experience, NOT evidence about the current system.\n"
    "- supporting_case_ids may contain ONLY case_id values from PAST CASES, copied exactly. "
    "Use [] for a hypothesis not based on a past case.\n"
    "- refutation_conditions: observations that would prove the hypothesis wrong.\n"
    "- Return only JSON matching the schema: "
    '{"hypotheses":[{"hypothesis":str,"supporting_case_ids":[str],'
    '"refutation_conditions":[str],"recommended_next_step":str}]}\n'
)


def memory_prompt() -> str:
    cases = "\n\n".join(
        f"[case_id={c['case_id']}] relevance={c['relevance_class']}; original environment: "
        + ", ".join(f"{k}={v}" for k, v in c["environment"].items())
        + f"\n{c['text']}"
        for c in CANDIDATES
    )
    return f"{RULES}\nCURRENT ISSUE:\n{CURRENT_ISSUE}\n\nPAST CASES:\n{cases}\n"


def abstain_prompt() -> str:
    return (
        f"{RULES}\nCURRENT ISSUE:\n{CURRENT_ISSUE}\n\nPAST CASES: none. Memory abstained (no relevant "
        "history), so every hypothesis must have supporting_case_ids = [].\n"
    )


# ---------------------------------------------------------------- env

def load_env(path: str) -> dict:
    values: dict[str, str] = {}
    file = Path(path)
    if file.exists():
        for line in file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in REQUIRED_VARS:  # a non-empty shell variable wins over the file
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def provider(env: dict, role: str) -> dict:
    return {
        "name": env.get(f"LLM_{role}_PROVIDER", ""),
        "model": env.get(f"LLM_{role}_MODEL", ""),
        "base_url": env.get(f"LLM_{role}_BASE_URL", "").rstrip("/"),
        "api_key": env.get(f"LLM_{role}_API_KEY", ""),
    }


# ---------------------------------------------------------------- http + classification

def classify(status: int | None, body) -> str | None:
    if status is None:
        return None
    if status == 200:
        if not isinstance(body, dict) or body.get("error"):
            return "BAD_RESPONSE"
        return None
    mapped = {400: "BAD_REQUEST", 401: "AUTH", 402: "BILLING", 403: "AUTH",
              404: "MODEL_NOT_FOUND", 408: "TIMEOUT", 429: "RATE_LIMITED"}
    return mapped.get(status, "UNAVAILABLE" if status >= 500 else "UNKNOWN")


def http(url: str, key: str = "", payload: dict | None = None, timeout: int = PROBE_TIMEOUT_S) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    out = {"status": None, "body": None, "text": "", "error_class": None, "headers": {}}
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            out.update(status=response.status, text=response.read().decode("utf-8", "replace"),
                       headers=dict(response.headers))
    except urllib.error.HTTPError as exc:
        out.update(status=exc.code, text=exc.read().decode("utf-8", "replace"),
                   headers=dict(exc.headers or {}))
    except TimeoutError:
        out["error_class"] = "TIMEOUT"
    except urllib.error.URLError as exc:
        reason = exc.reason
        out["error_class"] = ("TIMEOUT" if isinstance(reason, TimeoutError)
                              else "TLS" if isinstance(reason, ssl.SSLError) else "UNREACHABLE")
        out["text"] = str(reason)
    out["ms"] = int((time.perf_counter() - started) * 1000)
    if out["text"]:
        try:
            out["body"] = json.loads(out["text"])
        except json.JSONDecodeError:
            pass
    if out["error_class"] is None:
        out["error_class"] = classify(out["status"], out["body"])
    return out


def chat(p: dict, prompt: str, schema: dict | None = None, mode: str = "strict",
         extra: dict | None = None, max_tokens: int = MAX_TOKENS, tuned: bool = True, **override) -> dict:
    p = {**p, **override}
    payload: dict = {"model": p["model"], "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": max_tokens}
    if schema is not None:
        payload["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "hypotheses", "strict": True, "schema": schema}}
        if mode == "strict" and p["name"] == "openrouter":
            payload["provider"] = {"require_parameters": True}
        if tuned:
            payload.update(PROVIDER_OPTIONS.get(p["name"], {}))
    payload.update(extra or {})
    return http(f"{p['base_url']}/chat/completions", p["api_key"], payload)


# ---------------------------------------------------------------- parsing + validation

def parse_json(text: str):
    """Return (object, how). how: clean | fenced | prose_wrapped | unparseable | None (empty)."""
    s = (text or "").strip()
    if not s:
        return None, None
    try:
        return json.loads(s), "clean"
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if fence:
        try:
            return json.loads(fence.group(1)), "fenced"
        except json.JSONDecodeError:
            pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(s[start:end + 1]), "prose_wrapped"
        except json.JSONDecodeError:
            pass
    return None, "unparseable"


_PY = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    kind = schema.get("type")
    if kind in _PY and not isinstance(value, _PY[kind]):
        return [f"{path}: expected {kind}"]
    errors: list[str] = []
    if kind == "string" and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: shorter than minLength")
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            errors.append(f"{path}: item count out of range")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    if kind == "object":
        props = schema.get("properties", {})
        errors += [f"{path}.{k}: missing" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: not allowed" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
    return errors


def examine(resp: dict, schema: dict = HYP_SCHEMA) -> dict:
    """Reduce one structured call to the facts MK4/MK5 need."""
    body = resp["body"] if isinstance(resp["body"], dict) else {}
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    quirks = []
    if isinstance(content, list):
        quirks.append("content_list")
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if any(message.get(k) for k in ("reasoning", "reasoning_content", "reasoning_details")):
        quirks.append("reasoning_field")
    if choice.get("finish_reason") == "length":
        quirks.append("finish_length")
    if resp["status"] == 200 and body.get("error"):
        quirks.append("error_in_200")
    if resp["status"] == 200 and not content:
        quirks.append("empty_content")
    parsed, how = parse_json(content or "")
    if how in ("fenced", "prose_wrapped", "unparseable"):
        quirks.append(how)
    errors = validate(parsed, schema) if parsed is not None else ["unparseable or empty"]
    cited = []
    if isinstance(parsed, dict) and isinstance(parsed.get("hypotheses"), list):
        for h in parsed["hypotheses"]:
            if isinstance(h, dict) and isinstance(h.get("supporting_case_ids"), list):
                cited += [str(c) for c in h["supporting_case_ids"]]
    usage = body.get("usage") or {}
    return {
        "status": resp["status"], "error_class": resp["error_class"], "ms": resp["ms"],
        "finish_reason": choice.get("finish_reason"), "served_model": body.get("model"),
        "provider_behind": body.get("provider"), "valid": not errors, "errors": errors[:5],
        "cited": cited, "invented": [c for c in cited if c not in ALLOWED_IDS], "quirks": quirks,
        "tokens": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens")},
        "sample": (content if content else resp["text"])[:700],
    }


# ---------------------------------------------------------------- probes

class Run:
    def __init__(self, env: dict):
        self.env = env
        self.primary = provider(env, "PRIMARY")
        self.fallback = provider(env, "FALLBACK")
        self.routes = {"primary": self.primary, "fallback": self.fallback}
        self.results: list[dict] = []
        self.calls: list[dict] = []  # every examined structured call, for S3/S5
        self.catalog: dict = {}

    def record(self, pid, status, observed, decision="", data=None):
        self.results.append({"id": pid, "status": status, "observed": observed,
                             "decision": decision, "data": data or {}})
        print(f"  {pid:<4} {status:<8} {observed}", flush=True)

    def by_name(self, name: str):
        return next(((r, p) for r, p in self.routes.items() if p["name"] == name and p["api_key"]), (None, None))

    def default_modes(self) -> dict:
        return {r: "hint" if p["name"] == "openrouter" else "strict" for r, p in self.routes.items()}

    def keyed(self, *roles) -> bool:
        return all(self.routes[r]["api_key"] for r in roles)

    def structured(self, route: str, label: str, prompt: str, **kwargs) -> dict:
        rec = examine(chat(self.routes[route], prompt, HYP_SCHEMA, **kwargs))
        rec.update(route=route, label=label)
        self.calls.append(rec)
        return rec

    def repeat(self, route: str, label: str, prompt: str, n: int, **kwargs) -> list[dict]:
        """n identical calls in parallel: wall time ~= one call instead of n."""
        print(f"  ..   {label}: {n} parallel calls on {route}", flush=True)
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(lambda _: self.structured(route, label, prompt, **kwargs), range(n)))

    # -- A. environment
    def e1(self):
        v = sys.version_info
        hosts = {"openrouter": "https://openrouter.ai/api/v1/models",
                 "fallback": f"{self.fallback['base_url'] or 'https://inference.baseten.co/v1'}/models"}
        tls = {}
        for name, url in hosts.items():
            r = http(url, timeout=20)
            tls[name] = "ok" if r["status"] is not None else r["error_class"]
            if name == "openrouter" and isinstance(r["body"], dict):
                self.catalog = {m["id"]: m for m in r["body"].get("data", [])}
        ok = v >= (3, 10) and all(s == "ok" for s in tls.values())
        self.record("E1", "PASS" if ok else "FAIL", f"python {v.major}.{v.minor}.{v.micro}; tls={tls}",
                    "" if ok else "fix certificates / python before anything else")

    def e2(self):
        missing = [k for k in REQUIRED_VARS if not self.env.get(k)]
        self.record("E2", "FAIL" if missing else "PASS",
                    f"missing: {missing}" if missing else "all LLM vars present (values not printed)",
                    "fill keys in the env file; S/X/D2/D3/E3 are BLOCKED until then" if missing else "")

    def e3(self):
        _, orp = self.by_name("openrouter")
        if not orp:
            return self.record("E3", "BLOCKED", "no OpenRouter route with a key")
        r = http(f"{orp['base_url']}/key", orp["api_key"], timeout=20)
        data = (r["body"] or {}).get("data") if isinstance(r["body"], dict) else None
        if not isinstance(data, dict):
            return self.record("E3", "FAIL", f"status={r['status']} class={r['error_class']}")
        data.pop("label", None)
        self.record("E3", "PASS", f"limit={data.get('limit')} usage={data.get('usage')} "
                    f"free_tier={data.get('is_free_tier')} rate_limit={data.get('rate_limit')}",
                    "budget rehearsal runs against these limits", data)

    # -- B. catalog
    def d1(self):
        if not self.catalog:
            return self.record("D1", "FAIL", "OpenRouter catalog not readable")
        found = {}
        route_model = next((p["model"] for p in self.routes.values() if p["name"] == "openrouter"), BACKUP_MODEL)
        for mid in (route_model, BACKUP_MODEL):
            m = self.catalog.get(mid)
            found[mid] = None if m is None else {
                "structured_outputs": "structured_outputs" in (m.get("supported_parameters") or []),
                "response_format": "response_format" in (m.get("supported_parameters") or []),
                "max_completion_tokens": (m.get("top_provider") or {}).get("max_completion_tokens"),
                "expiration_date": m.get("expiration_date")}
        listed = found.get(route_model) is not None
        self.record("D1", "PASS" if listed else "FAIL", json.dumps(found),
                    "" if listed else f"swap the OpenRouter model to {BACKUP_MODEL}", found)

    def d2(self):
        _, bp = self.by_name("baseten")
        if not bp:
            return self.record("D2", "BLOCKED", "no Baseten route with a key")
        r = http(f"{bp['base_url']}/models", bp["api_key"], timeout=20)
        ids = [m.get("id") for m in (r["body"] or {}).get("data", [])] if isinstance(r["body"], dict) else []
        ok = bp["model"] in ids
        self.record("D2", "PASS" if ok else "FAIL",
                    f"status={r['status']} listed={ok} ({len(ids)} models)",
                    "" if ok else "correct the Baseten model id", {"ids": ids[:50]})

    def d3(self):
        _, orp = self.by_name("openrouter")
        if not orp:
            return self.record("D3", "BLOCKED", "no OpenRouter key")
        rec = examine(chat({**orp, "model": BACKUP_MODEL}, memory_prompt(), HYP_SCHEMA))
        rec.update(route="backup", label="D3")
        self.calls.append(rec)
        self.record("D3", "PASS" if rec["valid"] else "FAIL",
                    f"{BACKUP_MODEL}: status={rec['status']} valid={rec['valid']} {rec['ms']}ms",
                    "" if rec["valid"] else "no same-key backup route", rec)

    # -- C. contract
    def s1(self):
        if not self.keyed("primary", "fallback"):
            return self.record("S1", "BLOCKED", "keys missing")
        out, self.mode = {}, {}
        for route in ("primary", "fallback"):
            for name, schema in (("base", HYP_SCHEMA), ("extra_keywords", HYP_SCHEMA_EXTRA)):
                rec = examine(chat(self.routes[route], memory_prompt(), schema, mode="strict"), schema)
                rec.update(route=route, label=f"S1-strict-{name}")
                self.calls.append(rec)
                out[f"{route}/strict/{name}"] = rec
            if out[f"{route}/strict/base"]["status"] == 200:
                self.mode[route] = "strict"
            else:
                rec = examine(chat(self.routes[route], memory_prompt(), HYP_SCHEMA, mode="hint"))
                rec.update(route=route, label="S1-hint-base")
                self.calls.append(rec)
                out[f"{route}/hint/base"] = rec
                self.mode[route] = "hint"
        summary = {k: f"{v['status']}/{'valid' if v['valid'] else 'invalid'}" for k, v in out.items()}
        extra_ok = all(out[f"{r}/strict/extra_keywords"]["status"] == 200
                       for r, m in self.mode.items() if m == "strict")
        ok = all(out[f"{r}/{m}/base"]["status"] == 200 for r, m in self.mode.items())
        self.record("S1", "PASS" if ok else "FAIL", json.dumps(summary),
                    f"modes={self.mode}; send extra keywords={'yes' if extra_ok else 'no, local validation only'}",
                    {"summary": summary, "modes": self.mode, "extra_keywords_accepted": extra_ok,
                     "errors": {k: v["sample"][:300] for k, v in out.items() if v["status"] != 200}})

    def s2(self, runs=5):
        if not self.keyed("primary", "fallback"):
            return self.record("S2", "BLOCKED", "keys missing")
        mode = getattr(self, "mode", None) or self.default_modes()
        per = {}
        for route in ("primary", "fallback"):
            recs = self.repeat(route, "S2", memory_prompt(), runs, mode=mode[route])
            per[route] = recs
        counts = {r: sum(x["valid"] for x in recs) for r, recs in per.items()}
        ok = all(c >= runs - 1 for c in counts.values())
        self.s2_runs = per
        self.record("S2", "PASS" if ok else "FAIL", f"valid per route: {counts} of {runs}",
                    "" if ok else "raise retry budget or route invalid primary output to fallback",
                    {r: [{k: x[k] for k in ("status", "valid", "errors", "ms")} for x in recs]
                     for r, recs in per.items()})

    def s4(self, runs=3):
        if not self.keyed("primary", "fallback"):
            return self.record("S4", "BLOCKED", "keys missing")
        mode = getattr(self, "mode", None) or self.default_modes()
        cited = {}
        for route in ("primary", "fallback"):
            recs = self.repeat(route, "S4", abstain_prompt(), runs, mode=mode[route])
            cited[route] = [x["cited"] for x in recs]
        ok = not any(c for lists in cited.values() for c in lists)
        self.record("S4", "PASS" if ok else "FAIL", f"citations when abstained: {cited}",
                    "code forces supporting_case_ids=[] when abstained (already planned)", cited)

    def s3(self):
        memory_calls = [c for c in self.calls if c["label"] not in ("S4",) and c["status"] == 200]
        if not memory_calls:
            return self.record("S3", "BLOCKED", "no successful structured calls")
        invented = {f"{c['route']}/{c['label']}": c["invented"] for c in memory_calls if c["invented"]}
        cited_total = sum(len(c["cited"]) for c in memory_calls)
        self.record("S3", "FAIL" if invented else "PASS",
                    f"{cited_total} citations across {len(memory_calls)} calls; invented/mangled: {invented or 'none'}",
                    "use C1/C2 aliases in the prompt, map back in code" if invented else "real ids are safe to show the model",
                    {"invented": invented})

    def s5(self):
        if not self.calls:
            return self.record("S5", "BLOCKED", "no structured calls")
        seen: dict[str, list[str]] = {}
        for c in self.calls:
            for q in c["quirks"]:
                seen.setdefault(q, []).append(f"{c['route']}/{c['label']}")
        samples = {q: next(c["sample"] for c in self.calls if q in c["quirks"]) for q in seen}
        self.record("S5", "INFO", f"quirks seen: {({q: len(v) for q, v in seen.items()}) or 'none'}",
                    "each quirk seen gets a parser branch + fake-transport test in MK4",
                    {"where": seen, "samples": samples})

    def s6(self):
        per = getattr(self, "s2_runs", None)
        if not per:
            return self.record("S6", "BLOCKED", "S2 did not run")
        stats = {}
        for route, recs in per.items():
            ms = [x["ms"] for x in recs if x["status"] == 200]
            stats[route] = {"p50": int(statistics.median(ms)) if ms else None, "max": max(ms) if ms else None,
                            "completion_tokens": [x["tokens"]["completion_tokens"] for x in recs]}
        worst = (stats["primary"]["max"] or 0) + (stats["fallback"]["max"] or 0)
        ok = stats["primary"]["max"] is not None and stats["primary"]["max"] <= DEMO_BUDGET_MS
        self.record("S6", "PASS" if ok else "FAIL",
                    f"primary={stats['primary']} fallback={stats['fallback']} failover worst-case={worst}ms",
                    f"timeouts ≈ 1.5× max: primary {int(1.5 * (stats['primary']['max'] or 0) / 1000) + 1}s, "
                    f"fallback {int(1.5 * (stats['fallback']['max'] or 0) / 1000) + 1}s", stats)

    def s7(self):
        strict = {c["route"]: c for c in self.calls if c["label"] == "S1-strict-base"}
        if not strict:
            return self.record("S7", "BLOCKED", "S1 did not run")
        wasted = {r: c["ms"] for r, c in strict.items() if c["status"] != 200}
        self.record("S7", "INFO", f"strict on each route: { {r: (c['status'], c['ms']) for r, c in strict.items()} }",
                    f"never send strict to {list(wasted)} (costs {wasted} ms each)" if wasted
                    else "strict works on every route", {"wasted_ms": wasted})

    def s8(self, runs=3):
        """Reasoning control on the fallback: the 060033Z run showed every invalid output was
        reasoning exhausting max_tokens (finish=length), never a schema violation."""
        route, bp = self.by_name("baseten")
        if not bp:
            return self.record("S8", "BLOCKED", "no Baseten route with a key")
        variants = {"none": {}, "reasoning_effort_low": {"reasoning_effort": "low"},
                    "thinking_off": {"chat_template_kwargs": {"thinking": False}},
                    "openrouter_style_low": {"reasoning": {"effort": "low"}}}
        jobs = [(name, extra) for name, extra in variants.items() for _ in range(runs)]
        print(f"  ..   S8: {len(jobs)} parallel calls on {route} (baseten)", flush=True)
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            recs = list(pool.map(lambda j: {**self.structured(route, f"S8-{j[0]}", memory_prompt(), mode="strict",
                                                              extra=j[1], tuned=False), "variant": j[0]}, jobs))
        table = {}
        for name in variants:
            rs = [r for r in recs if r["variant"] == name]
            ok = [r for r in rs if r["status"] == 200]
            table[name] = {"http": sorted({r["status"] for r in rs}), "valid": sum(r["valid"] for r in rs),
                           "p50_ms": int(statistics.median([r["ms"] for r in ok])) if ok else None,
                           "tokens": [r["tokens"]["completion_tokens"] for r in rs],
                           "reasoning_field": sum("reasoning_field" in r["quirks"] for r in rs)}
        best = min((n for n, t in table.items() if t["valid"] == runs and t["p50_ms"]),
                   key=lambda n: table[n]["p50_ms"], default=None)
        self.record("S8", "PASS" if best and best != "none" else "FAIL", json.dumps(table),
                    f"baseten reasoning control = {best}" if best else "no variant reliably valid on baseten",
                    table)

    # -- D. errors
    def x1(self):
        if not self.keyed("primary", "fallback"):
            return self.record("X1", "BLOCKED", "keys missing")
        out = {}
        for route, p in self.routes.items():
            bad_model = chat(p, "Reply OK", max_tokens=5, model="mk0/does-not-exist")
            bad_key = chat(p, "Reply OK", max_tokens=5, api_key="sk-invalid-mk0-probe")
            out[route] = {"bad_model": (bad_model["status"], bad_model["error_class"]),
                          "bad_key": (bad_key["status"], bad_key["error_class"])}
        ok = all(v["bad_model"][1] in ("BAD_REQUEST", "MODEL_NOT_FOUND") and v["bad_key"][1] == "AUTH"
                 for v in out.values())
        self.record("X1", "PASS" if ok else "FAIL", json.dumps(out),
                    "" if ok else "fix the status map for the provider that differs", out)

    def x2(self, burst=5):
        if not self.keyed("primary"):
            return self.record("X2", "BLOCKED", "no primary key")
        seen = []
        for _ in range(burst):
            r = chat(self.primary, "Reply OK", max_tokens=5)
            seen.append((r["status"], r["headers"].get("Retry-After")))
        limited = [s for s in seen if s[0] == 429]
        self.record("X2", "FAIL" if limited else "PASS", f"{burst} back-to-back calls: {seen}",
                    "pace rehearsal calls; 429 stays failover-eligible" if limited else "", {"seen": seen})

    def x3(self):
        if not self.keyed("fallback"):
            return self.record("X3", "BLOCKED", "no fallback key")
        mode = getattr(self, "mode", None) or self.default_modes()
        first = chat(self.primary, memory_prompt(), HYP_SCHEMA, mode=mode["primary"],
                     base_url="http://127.0.0.1:9")
        attempts = [{"route": "primary", "error_class": first["error_class"]}]
        rec = None
        if first["error_class"] in FAILOVER_ELIGIBLE:
            rec = self.structured("fallback", "X3", memory_prompt(), mode=mode["fallback"])
            attempts.append({"route": "fallback", "status": rec["status"], "valid": rec["valid"]})
        ok = rec is not None and rec["valid"]
        self.record("X3", "PASS" if ok else "FAIL", f"attempts={attempts} fallback_used={rec is not None}",
                    "" if ok else "router design blocks MK4", {"attempts": attempts})

    def run_all(self, save, only=None):
        order = [self.e1, self.e2, self.e3, self.d1, self.d2, self.d3, self.s1, self.s2, self.s4,
                 self.s6, self.s8, self.s3, self.s5, self.s7, self.x1, self.x2, self.x3]
        if only:
            order = [self.e1, self.e2] + [p for p in order if p.__name__.upper() in only and p.__name__ not in ("e1", "e2")]
        for probe in order:
            try:
                probe()
            except Exception as exc:  # a crashing probe is recorded, never silently skipped
                self.record(probe.__name__.upper(), "ERROR", f"{type(exc).__name__}: {exc}")
            save()  # after every probe, so Ctrl-C never loses completed results


# ---------------------------------------------------------------- output

def scrub(text: str, env: dict) -> str:
    for key, value in env.items():
        if key.endswith("_API_KEY") and len(value) > 6:
            text = text.replace(value, "***")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=".env.live")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--only", default="", help="comma list of probe ids, e.g. S8,D1")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    env = load_env(args.env)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"MK0 run {run_id} (env file: {args.env})")
    run = Run(env)
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"mk0-{run_id}.json"
    report: dict = {}

    def save():
        report.update(run_id=run_id, env_file=args.env,
                      models={r: {"provider": p["name"], "model": p["model"], "base_url": p["base_url"]}
                              for r, p in run.routes.items()},
                      totals={s: sum(r["status"] == s for r in run.results)
                              for s in ("PASS", "FAIL", "BLOCKED", "INFO", "ERROR")},
                      results=run.results, calls=run.calls)
        out.write_text(scrub(json.dumps(report, indent=2, default=str), env))

    run.run_all(save, {x.strip().upper() for x in args.only.split(",") if x.strip()})
    print(f"\ntotals: {report['totals']}\nwritten: {out}")
    return 0 if not report["totals"]["FAIL"] and not report["totals"]["ERROR"] else 1


def selftest() -> None:
    assert parse_json('{"a":1}') == ({"a": 1}, "clean")
    assert parse_json('```json\n{"a":1}\n```') == ({"a": 1}, "fenced")
    assert parse_json('Here you go: {"a":1} done') == ({"a": 1}, "prose_wrapped")
    assert parse_json("") == (None, None) and parse_json("nope")[1] == "unparseable"
    good = {"hypotheses": [{"hypothesis": "h", "supporting_case_ids": ["seed-001"],
                            "refutation_conditions": [], "recommended_next_step": "s"}]}
    assert validate(good, HYP_SCHEMA) == []
    assert validate({"hypotheses": [{"hypothesis": "h"}]}, HYP_SCHEMA)
    assert validate({"hypotheses": [], "x": 1}, HYP_SCHEMA) == ["$.x: not allowed"]
    assert validate({"hypotheses": []}, HYP_SCHEMA_EXTRA) == ["$.hypotheses: item count out of range"]
    assert classify(200, {"error": {"code": 502}}) == "BAD_RESPONSE"
    assert classify(200, {"choices": []}) is None
    assert classify(401, None) == "AUTH" and classify(429, None) == "RATE_LIMITED"
    assert classify(503, None) == "UNAVAILABLE" and classify(404, None) == "MODEL_NOT_FOUND"
    rec = examine({"status": 200, "error_class": None, "ms": 1, "text": "",
                   "body": {"choices": [{"message": {"content": json.dumps(
                       {"hypotheses": [{**good["hypotheses"][0], "supporting_case_ids": ["seed-01"]}]})},
                       "finish_reason": "stop"}]}})
    assert rec["valid"] and rec["invented"] == ["seed-01"]
    print("selftest ok")


if __name__ == "__main__":
    sys.exit(main())
