"""Run the eval suite and write a report.

    uv run python -m evals.run                         # offline, scripted model (CI)
    uv run python -m evals.run --provider gemini --smoke   # bounded 6-case live Gemini smoke run (costs money)
    uv run python -m evals.run --provider anthropic    # optional: bounded live run on Claude (costs more)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime

from agent_service.llm.provider import Usage
from agent_service.llm_agent import LoopLimits
from agent_service.paths import reports_directory

from .cases import CASES
from .harness import Case, run_case

REPORTS = reports_directory()
# Published prices (USD per million tokens: input, output incl. thinking), used only for a labelled estimate.
# Time-sensitive: claude-opus-5-5 from the Anthropic model table cached 2026-10-06; gemini-3.8-flash from
# https://ai.google.dev/gemini-api/docs/pricing read 2026-11-07, cached input read 2026-11-12 (intro rate through
# 2026-12-31; from 2027-01-01 1.50 / 7.50, cached 0.15). Override with AGENT_LLM_PRICE_INPUT_USD_PER_MTOK /
# AGENT_LLM_PRICE_OUTPUT_USD_PER_MTOK (and AGENT_LLM_PRICE_CACHED_INPUT_USD_PER_MTOK). No cached price for Claude
# here: its cache reads are priced as input until one is read from Anthropic's table.
# gemini-3.5-flash-lite from the same page read 2026-11-12 (no date change listed).
PRICES = {"claude-opus-5-5": (4.00, 20.00, None), "gemini-3.8-flash": (0.75, 3.75, 0.075),
          "gemini-3.5-flash-lite": (0.30, 2.50, 0.03)}


# The bounded live smoke suite (Task 14): Ukrainian field facts, constraint proposal, soil-moisture map action,
# FAO-56 with verified citations, unsupported drought scenario, synthetic rotation comparison. Tight limits.
SMOKE = ["ts_general_question", "ts_constraints_uk", "ts_map_layer", "kn_supported_uk", "int_live_drought_unavailable",
         "int_live_synthetic_comparison"]
SMOKE_LIMITS = LoopLimits(max_rounds=4, max_tool_calls=6, max_tool_calls_per_round=3, max_output_tokens=2048,
                          max_repairs=1, tool_timeout_s=20.0)


class UsageTap:
    """Wraps a provider to record what each real model call reports (tokens are the provider's numbers)."""

    def __init__(self, provider):
        self.provider, self.calls = provider, []
        self.name, self.model_id, self.capabilities = provider.name, provider.model_id, provider.capabilities

    @property
    def requests(self):
        return self.calls

    async def complete(self, **kwargs):
        started = time.monotonic()
        response = await self.provider.complete(**kwargs)
        self.calls.append({"ms": int((time.monotonic() - started) * 1000), "in": response.usage.input_tokens,
                           "cached": response.usage.cache_read_input_tokens,
                           "out": response.usage.output_tokens, "think": response.usage.thinking_tokens,
                           "model": response.model_id, "stop": response.raw_stop_reason or response.stop_reason,
                           "tools": [t for t in kwargs.get("tools", [])], "transcript": kwargs.get("transcript", [])})
        return response

    async def aclose(self):
        await self.provider.aclose()


async def run_all(cases: list[Case], provider_name: str, limits: LoopLimits | None = None) -> list[dict]:
    rows = []
    for case in cases:
        provider = None
        if provider_name == "gemini":
            from agent_service.llm.gemini_provider import GeminiProvider

            provider = UsageTap(GeminiProvider(
                model_id=os.environ.get("AGENT_GEMINI_MODEL", "gemini-3.8-flash"),
                thinking_level=os.environ.get("AGENT_GEMINI_THINKING_LEVEL", "low"), request_timeout_s=60, max_retries=1))
        if provider_name == "anthropic":
            from agent_service.llm.anthropic_provider import AnthropicProvider

            provider = UsageTap(AnthropicProvider(
                model_id=os.environ.get("AGENT_LLM_MODEL", "claude-opus-5-5"), effort="medium",
                request_timeout_s=90, max_retries=1, refusal_fallback=True, strict_tools=True))
        started = time.monotonic()
        run, failures = await run_case(case, provider=provider, live=provider_name != "scripted", limits=limits)
        rows.append({
            "id": case.id, "category": case.category, "title": case.title, "passed": not failures,
            "failures": [{"check": c, "reason": r} for c, r in failures],
            "terminal": run.terminal.event if run.terminal else None,
            "error": f"{run.error.code}/{run.error.message_key}" if run.error else None,
            "tools": [e.tool for e in run.events if e.event == "tool.started"],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model": run.message.meta.model_id if run.message else None,
            "requests": len(getattr(run.provider, "requests", [])) or None,
            **(_usage(provider) if isinstance(provider, UsageTap) else {}),
        })
        if provider is not None:
            await provider.aclose()
    return rows


def _price(model: str) -> tuple[float, float, float | None] | None:
    env = (os.environ.get("AGENT_LLM_PRICE_INPUT_USD_PER_MTOK"), os.environ.get("AGENT_LLM_PRICE_OUTPUT_USD_PER_MTOK"))
    if all(env):
        cached = os.environ.get("AGENT_LLM_PRICE_CACHED_INPUT_USD_PER_MTOK")
        return float(env[0]), float(env[1]), float(cached) if cached else None
    return next((p for m, p in PRICES.items() if model.startswith(m)), None)


def _usage(tap: UsageTap) -> dict:
    tin, tout, tthink, tcached = (sum(c[k] for c in tap.calls) for k in ("in", "out", "think", "cached"))
    models = sorted({c["model"] for c in tap.calls if c["model"]})
    price = _price(models[0]) if len(models) == 1 else None
    usage = Usage(tin, tout, cache_read_input_tokens=tcached, thinking_tokens=tthink)
    return {"calls": [{k: c[k] for k in ("ms", "in", "cached", "out", "think", "stop")} for c in tap.calls],
            "models": models, "input_tokens": tin, "cached_input_tokens": tcached, "output_tokens": tout,
            "thinking_tokens": tthink, "est_usd": round(usage.cost_usd(*price), 6) if price else None}


def report(rows: list[dict], provider: str) -> str:
    by_category: dict[str, list[dict]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(r)
    failure_kinds = Counter(f["check"] for r in rows for f in r["failures"])
    passed = sum(r["passed"] for r in rows)
    lines = [
        f"# Field Shift agent eval report ({provider})", "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}. Cases: {len(rows)}, passed: {passed}, "
        f"failed: {len(rows) - passed}.", "",
        "The scripted provider plays the model, so these results validate orchestration, tool validation, grounding "
        "and contract handling. They do not measure a real model's judgement." if provider == "scripted" else
        "Live model run: outcome checks only (scripts ignored).", "",
        "| Category | Passed | Total |", "|---|---|---|",
        *[f"| {c} | {sum(r['passed'] for r in rs)} | {len(rs)} |" for c, rs in by_category.items()], "",
    ]
    if any("input_tokens" in r for r in rows):
        cost = [r["est_usd"] for r in rows if r.get("est_usd") is not None]
        lines += ["## Usage (provider-reported tokens)", "",
                  "| Case | Model | Calls | Latency ms | Input | Of it cached | Output | Thinking | Est. USD |",
                  "|---|---|---|---|---|---|---|---|---|",
                  *[f"| {r['id']} | {', '.join(r.get('models') or ['-'])} | {len(r.get('calls', []))} | {r['latency_ms']} | "
                    f"{r.get('input_tokens', 0)} | {r.get('cached_input_tokens', 0)} | {r.get('output_tokens', 0)} | "
                    f"{r.get('thinking_tokens', 0)} | "
                    f"{r['est_usd'] if r.get('est_usd') is not None else 'n/a'} |" for r in rows], "",
                  f"Estimated total: {sum(cost):.6f} USD from published per-token prices (thinking billed as output, "
                  "cached input at the cached price where one is known); "
                  "an estimate, not a bill.", ""]
    if failure_kinds:
        lines += ["## Failure categories", "", "| Check | Count |", "|---|---|",
                  *[f"| {k} | {n} |" for k, n in failure_kinds.most_common()], ""]
    lines += ["## Cases", "", "| Case | Category | Result | Terminal | Tools | Details |", "|---|---|---|---|---|---|"]
    for r in rows:
        details = "; ".join(f"{f['check']}: {f['reason']}" for f in r["failures"]).replace("|", "\\|")[:300]
        lines.append(f"| {r['id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | "
                     f"{r['error'] or r['terminal']} | {', '.join(r['tools']) or '-'} | {details or '-'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["scripted", "gemini", "anthropic"], default="scripted")
    parser.add_argument("--only", nargs="*", help="case ids")
    parser.add_argument("--smoke", action="store_true", help="only the 6-case live smoke suite, with tight limits")
    args = parser.parse_args()
    cases = [c for c in CASES if (not args.only or c.id in args.only) and (not args.smoke or c.id in SMOKE)]
    if args.provider == "gemini":
        from agent_service.llm.gemini_provider import credentials_present

        if not credentials_present():
            print("No GEMINI_API_KEY / GOOGLE_API_KEY set: live Gemini run skipped (not faked).", file=sys.stderr)
            return 3
        cases = [c for c in cases if c.live]
    if args.provider == "anthropic":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("No ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN set: live run skipped (not faked).", file=sys.stderr)
            return 3
        cases = [c for c in cases if c.live]
    rows = asyncio.run(run_all(cases, args.provider, SMOKE_LIMITS if args.smoke else None))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.provider}-latest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / f"{args.provider}-latest.md").write_text(report(rows, args.provider), encoding="utf-8", newline="\n")
    passed = sum(r["passed"] for r in rows)
    print(f"{passed}/{len(rows)} passed -> {REPORTS / (args.provider + '-latest.md')}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
