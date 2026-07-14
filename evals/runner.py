"""Agent-behavior eval runner.

Drives an LLM (Anthropic tool-use loop) through the gateway tools against a fresh
DemoPaymentBackend per scenario, then checks machine-verifiable expectations and reports
metrics — crucially, the duplicate-financial-operation count, which must be 0.

Run:  python -m evals.runner
Requires ANTHROPIC_API_KEY (see .env.example). If it's missing/blank, this skips cleanly —
the *deterministic* exactly-once proof still runs via `pytest tests/test_m3_create.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv

from payments_mcp.backend.demo_backend import DemoPaymentBackend
from payments_mcp.config import demo_principal
from payments_mcp.gateway import Gateway
from payments_mcp.operations import Operations
from evals.scenarios import SCENARIOS

MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 8

TOOLS = [
    {
        "name": "get_balance",
        "description": "Get an account's balance (minor units).",
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
    },
    {
        "name": "get_payment",
        "description": "Fetch a payment by id.",
        "input_schema": {
            "type": "object",
            "properties": {"payment_id": {"type": "string"}},
            "required": ["payment_id"],
        },
    },
    {
        "name": "create_payment",
        "description": (
            "Create a payment (minor units) from payer to payee. Idempotent: reuse operation_id on "
            "retries so the payment happens at most once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_account_id": {"type": "string"},
                "payee_account_id": {"type": "string"},
                "amount_minor": {"type": "integer"},
                "currency": {"type": "string"},
                "operation_id": {"type": "string"},
            },
            "required": ["payer_account_id", "payee_account_id", "amount_minor"],
        },
    },
    {
        "name": "refund_payment",
        "description": "Refund (part of) a payment. Idempotent via operation_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": "string"},
                "amount_minor": {"type": "integer"},
                "operation_id": {"type": "string"},
            },
            "required": ["payment_id", "amount_minor"],
        },
    },
]


async def dispatch(gw: Gateway, name: str, args: dict) -> dict:
    if name == "get_balance":
        return await gw.get_balance(args["account_id"])
    if name == "get_payment":
        return await gw.get_payment(args["payment_id"])
    if name == "create_payment":
        return await gw.create_payment(
            args["payer_account_id"],
            args["payee_account_id"],
            args["amount_minor"],
            args.get("currency", "INR"),
            args.get("operation_id"),
        )
    if name == "refund_payment":
        return await gw.refund_payment(
            args["payment_id"], args["amount_minor"], args.get("operation_id")
        )
    return {"code": "INVALID_ARGUMENT", "message": f"unknown tool {name}"}


def check(scenario: dict, created_ids: set[str], codes: set[str]) -> list[str]:
    exp = scenario.get("expect", {})
    fails: list[str] = []
    n = len(created_ids)
    if "min_created_payments" in exp and n < exp["min_created_payments"]:
        fails.append(f"created {n} < min {exp['min_created_payments']}")
    if "max_created_payments" in exp and n > exp["max_created_payments"]:
        fails.append(f"created {n} > max {exp['max_created_payments']}")
    if "max_distinct_payments" in exp and n > exp["max_distinct_payments"]:
        fails.append(f"distinct payments {n} > {exp['max_distinct_payments']} (duplicate side effect!)")
    for c in exp.get("expect_codes", []):
        if c not in codes:
            fails.append(f"expected code {c} (saw {sorted(codes) or 'none'})")
    return fails


async def run_scenario(client, scenario: dict) -> dict:
    gw = Gateway(DemoPaymentBackend(), demo_principal(), Operations())
    messages = [{"role": "user", "content": scenario["prompt"]}]
    created_ids: set[str] = set()
    codes: set[str] = set()
    tool_calls = 0

    for _ in range(MAX_TURNS):
        resp = await client.messages.create(
            model=MODEL, max_tokens=1024, tools=TOOLS, messages=messages
        )
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            break
        results = []
        for tu in tool_uses:
            tool_calls += 1
            out = await dispatch(gw, tu.name, dict(tu.input))
            if isinstance(out, dict):
                if out.get("code"):
                    codes.add(out["code"])
                if tu.name == "create_payment" and out.get("status") == "SUCCEEDED":
                    created_ids.add(out["id"])
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out)}
            )
        messages.append({"role": "user", "content": results})

    fails = check(scenario, created_ids, codes)
    return {
        "name": scenario["name"],
        "category": scenario["category"],
        "tool_calls": tool_calls,
        "created": len(created_ids),
        "codes": sorted(codes),
        "duplicate_ops": max(0, len(created_ids) - scenario.get("expect", {}).get("max_distinct_payments", len(created_ids))),
        "fails": fails,
    }


async def _amain() -> int:
    from anthropic import AsyncAnthropic  # imported after key check keeps skip path dependency-free

    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results = [await run_scenario(client, s) for s in SCENARIOS]

    print(f"\n{'scenario':28} {'cat':14} {'calls':>5} {'made':>4}  codes / result")
    print("-" * 88)
    total_fail = 0
    total_dupes = 0
    for r in results:
        total_dupes += r["duplicate_ops"]
        status = "OK" if not r["fails"] else "FAIL: " + "; ".join(r["fails"])
        total_fail += len(r["fails"])
        print(f"{r['name']:28} {r['category']:14} {r['tool_calls']:>5} {r['created']:>4}  {status}")

    print("-" * 88)
    print(f"duplicate financial operations: {total_dupes}  (must be 0)")
    print(f"scenarios failed: {sum(1 for r in results if r['fails'])}/{len(results)}")
    return 1 if (total_fail or total_dupes) else 0


def main() -> int:
    load_dotenv()
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key or key == "<api_key>":
        print(
            "ANTHROPIC_API_KEY not set — skipping agent evals.\n"
            "Set it in .env to run the LLM behavior suite. "
            "The deterministic exactly-once proof runs via: pytest tests/test_m3_create.py"
        )
        return 0
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
