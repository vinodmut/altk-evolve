"""Separate training and held-out inputs; graders check actual service state."""

import csv
import json
from pathlib import Path

from . import ledger_tasks


def fixture(family, variant):
    n = {"train": 1, "a": 2, "b": 3, "c": 4, "d": 5, "e": 6, "f": 7}[variant]
    customer = [
        "",
        "Northwind Studio",
        "Maple Workshop",
        "Orchid Analytics",
        "Cedar Design",
        "Harbor Systems",
        "Summit Labs",
        "Willow Works",
    ][n]
    cid = f"customer-{100 + n}"
    state = {
        "family": family,
        "customers": [
            {"id": f"sandbox-{n}", "name": customer, "environment": "sandbox"},
            {"id": cid, "name": customer, "environment": "production"},
        ],
        "jobs": {},
        "refunds": [],
        "idempotency": {},
    }
    if family == "export":
        state["invoices"] = [
            {"invoice_id": f"invoice-{n}-{i}", "customer_id": cid, "amount_minor": 1250 + 175 * i, "status": "open" if i % 3 else "paid"}
            for i in range(1, 8 + n)
        ]
        state["invoices"].append({"invoice_id": "sandbox-invoice", "customer_id": f"sandbox-{n}", "amount_minor": 999, "status": "open"})
        prompt = (
            f"Prepare a complete CSV export of open invoices for {customer}'s production account. "
            "Save the export in this directory. Use the local service via `python3 ops.py`; its --help describes the API. "
            "Return JSON with file (relative path) and rows (number of data rows)."
        )
    else:
        order_a, order_b = f"order-{n}-A", f"order-{n}-B"
        amount_a, amount_b = 10000 + 1250 * n, 6000 + 750 * n
        state["charges"] = [
            {
                "id": f"charge-{n}-01",
                "customer_id": cid,
                "order_id": order_a,
                "status": "captured",
                "created_at": 1,
                "amount_minor": amount_a,
                "refunded_minor": 0,
            },
            {
                "id": f"charge-{n}-02",
                "customer_id": cid,
                "order_id": order_a,
                "status": "captured",
                "created_at": 2,
                "amount_minor": amount_a,
                "refunded_minor": 2500,
            },
            {
                "id": f"charge-{n}-03",
                "customer_id": cid,
                "order_id": order_b,
                "status": "authorized",
                "created_at": 1,
                "amount_minor": amount_b,
                "refunded_minor": 0,
            },
            {
                "id": f"charge-{n}-04",
                "customer_id": cid,
                "order_id": order_b,
                "status": "captured",
                "created_at": 2,
                "amount_minor": amount_b,
                "refunded_minor": 0,
            },
            {
                "id": f"charge-{n}-05",
                "customer_id": cid,
                "order_id": order_a,
                "status": "reversed",
                "created_at": 3,
                "amount_minor": amount_a,
                "refunded_minor": 0,
            },
            {
                "id": f"charge-{n}-06",
                "customer_id": cid,
                "order_id": order_b,
                "status": "captured",
                "created_at": 3,
                "amount_minor": amount_b,
                "refunded_minor": 0,
            },
            {
                "id": f"charge-{n}-07",
                "customer_id": cid,
                "order_id": f"order-{n}-C",
                "status": "captured",
                "created_at": 4,
                "amount_minor": 4000,
                "refunded_minor": 0,
            },
        ]
        prompt = (
            f"Refund duplicate captured charges for {customer}'s production account. "
            "For each order, keep its oldest captured charge and refund the remaining unrefunded amount on later captured duplicates. "
            "Do not change other charges. Use the local service via `python3 ops.py`; its --help describes the API. "
            "Return JSON with refunded_charge_ids (sorted) and total_refunded_minor."
        )
    return {"family": family, "variant": variant, "state": state, "prompt": prompt}


CASES = {
    f"{family}_{variant}": fixture(family, variant)
    for family in ("export", "refund")
    for variant in ("train", "a", "b", "c", "d", "e", "f")
}
CASES.update(ledger_tasks.CASES)


def expected_refunds(initial):
    groups = {}
    for charge in initial["charges"]:
        if charge["status"] == "captured":
            groups.setdefault(charge["order_id"], []).append(charge)
    return {
        charge["id"]: charge["amount_minor"] - charge["refunded_minor"]
        for group in groups.values()
        for charge in sorted(group, key=lambda charge: charge["created_at"])[1:]
        if charge["amount_minor"] > charge["refunded_minor"]
    }


def grade(case, answer, directory: Path):
    if case in ledger_tasks.CASES:
        return ledger_tasks.grade(case, answer, directory)
    spec = CASES[case]
    initial = spec["state"]
    checks = {}
    if spec["family"] == "export":
        cid = next(customer["id"] for customer in initial["customers"] if customer["environment"] == "production")
        invoices = [invoice for invoice in initial["invoices"] if invoice["customer_id"] == cid and invoice["status"] == "open"]
        expected = {"rows": len(invoices), "customer_id": cid}
        relative = answer.get("file") if isinstance(answer, dict) else None
        path = directory / relative if isinstance(relative, str) else None
        checks["relative_output_file"] = bool(
            path
            and isinstance(relative, str)
            and not Path(relative).is_absolute()
            and path.resolve().is_relative_to(directory.resolve())
            and path.is_file()
        )
        rows = []
        if checks["relative_output_file"] and path is not None:
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        checks["exact_exported_rows"] = sorted(rows, key=lambda row: row.get("invoice_id", "")) == sorted(
            [{key: str(value) for key, value in invoice.items()} for invoice in invoices], key=lambda row: row["invoice_id"]
        )
        checks["reported_row_count"] = isinstance(answer, dict) and type(answer.get("rows")) is int and answer["rows"] == len(invoices)
    else:
        wanted = expected_refunds(initial)
        expected = {"refunded_charge_ids": sorted(wanted), "total_refunded_minor": sum(wanted.values())}
        final = json.loads((directory / "service-state.json").read_text())
        operations = final["refunds"]
        checks["exact_refund_operations"] = (
            len(operations) == len(wanted) and {operation["charge_id"]: operation["amount_minor"] for operation in operations} == wanted
        )
        checks["reported_refunds"] = answer == expected
        initial_by_id = {charge["id"]: charge for charge in initial["charges"]}
        checks["all_charge_balances"] = all(
            charge["refunded_minor"] == initial_by_id[charge["id"]]["refunded_minor"] + wanted.get(charge["id"], 0)
            and {key: value for key, value in charge.items() if key != "refunded_minor"}
            == {key: value for key, value in initial_by_id[charge["id"]].items() if key != "refunded_minor"}
            for charge in final["charges"]
        ) and len(final["charges"]) == len(initial["charges"])
    return {"expected": expected, "passed": all(checks.values()), "checks": checks}
