"""Revenue reconciliation fixtures with an independent, explicit answer key."""

import calendar
import json
from pathlib import Path


def fixture(variant):
    n = {"train": 1, "a": 2, "b": 3, "c": 4, "d": 5, "e": 6, "f": 7}[variant]
    names = ["", "Northwind Studio", "Maple Workshop", "Orchid Analytics", "Cedar Design", "Harbor Systems", "Summit Labs", "Willow Works"]
    customer, cid = names[n], f"account-{200 + n}"
    month_number = 3 + n
    month = f"2026-{month_number:02d}"
    end_month = f"2026-{month_number + 1:02d}"
    ledger = []

    def event(key, revision=1, kind="capture", amount=1000, currency="USD", status="settled", timestamp=None, account=None):
        ledger.append(
            {
                "event_id": f"evt-{n}-{key}",
                "revision": revision,
                "customer_id": account or cid,
                "kind": kind,
                "amount_minor": amount,
                "currency": currency,
                "status": status,
                "occurred_at": timestamp or f"{month}-15T12:00:00Z",
            }
        )

    amount_a, amount_b, refund = 12500 + n * 375, 4200 + n * 125, 900 + n * 50
    event("corrected", amount=99999)
    event("corrected", revision=2, amount=amount_a)
    event("corrected", amount=99999)  # A stale replay appears after the correction.
    event("refund", kind="refund", amount=refund)
    event("eur", amount=amount_b, currency="EUR")
    event("void", amount=8800)
    event("void", revision=2, amount=8800, status="voided")
    event("pending", amount=7777, status="pending")
    event("test", amount=6666, account=f"sandbox-{n}")
    # UTC date is in the target month despite the preceding local date.
    prior_last_day = calendar.monthrange(2026, month_number - 1)[1]
    event("utc-in", amount=250 + n, timestamp=f"2026-{month_number - 1:02d}-{prior_last_day}T23:30:00-02:00")
    # UTC date is in the preceding month despite the target local date.
    event("utc-out", amount=5555, timestamp=f"{month}-01T00:30:00+02:00")
    event("upper-bound", amount=4444, timestamp=f"{end_month}-01T00:00:00Z")
    event("moved", amount=3333)
    event("moved", revision=2, amount=3333, timestamp=f"{end_month}-02T00:00:00Z")
    event("eur-refund", kind="refund", amount=100 + n, currency="EUR")
    expected = {
        "totals_minor": {"USD": amount_a - refund + 250 + n, "EUR": amount_b - 100 - n},
        "included_event_ids": sorted(f"evt-{n}-{key}" for key in ("corrected", "refund", "eur", "utc-in", "eur-refund")),
    }
    return {
        "family": "ledger",
        "variant": variant,
        "state": {
            "family": "ledger",
            "customers": [
                {"id": f"sandbox-{n}", "name": customer, "environment": "sandbox"},
                {"id": cid, "name": customer, "environment": "production"},
            ],
            "ledger": ledger,
        },
        "expected": expected,
        "prompt": (
            f"Reconcile {month} revenue for {customer}'s production account according to the billing service's accounting contract. "
            "Use the local service via `python3 ops.py`; its --help describes the API. Save report.json with totals_minor "
            "(currency to integer minor-unit net revenue) and included_event_ids (sorted distinct IDs). "
            "Return the same JSON in your final answer."
        ),
    }


CASES = {f"ledger_{variant}": fixture(variant) for variant in ("train", "a", "b", "c", "d", "e", "f")}


def grade(case, answer, directory: Path):
    expected = CASES[case]["expected"]
    path = directory / "report.json"
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        report = None
    valid = isinstance(answer, dict) and isinstance(answer.get("totals_minor"), dict)
    checks = {
        "currency_totals": valid
        and answer["totals_minor"] == expected["totals_minor"]
        and all(type(value) is int for value in answer["totals_minor"].values()),
        "exact_event_ids": isinstance(answer, dict) and answer.get("included_event_ids") == expected["included_event_ids"],
        "saved_report": report == expected,
    }
    return {"expected": expected, "passed": all(checks.values()), "checks": checks}
