"""A local billing service. All mutations stay in this synthetic trial directory."""

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "service-state.json"
CALLS = HERE / "service.calls.jsonl"

REVENUE_CONTRACT = """Billing revenue contract v1:
1. Ledger exports contain replayed events and corrections, including sandbox accounts.
   Collapse rows by event_id using the greatest numeric revision across the ENTIRE export,
   before applying any account, status, or date filter. Equal-revision replays are identical.
   A later revision can void an event or move it out of the reporting month.
2. Resolve the production account with find-customer; use its customer_id only.
3. Include only status=settled. Pending and voided rows contribute nothing.
4. Month boundaries are UTC: include occurred_at >= first day 00:00:00Z and
   occurred_at < next month's first day 00:00:00Z. Parse offsets; do not slice the date string.
5. amount_minor is a positive integer magnitude for BOTH capture and refund events.
   Add captures, subtract refunds. Keep each currency separate; do not convert currencies.
6. Report totals_minor by currency and sorted distinct included_event_ids.
API: python3 ops.py find-customer 'CUSTOMER NAME'
     python3 ops.py ledger-export --customer PRODUCTION_ID --output ledger.json
     python3 ops.py revenue-contract
"""


def record(action, ok, detail=None):
    with CALLS.open("a") as handle:
        handle.write(json.dumps({"action": action, "ok": ok, "detail": detail}) + "\n")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        record(sys.argv[1] if len(sys.argv) > 1 else "missing-command", False, message)
        super().error(message)


def perform(state, action, args):
    if action == "revenue-contract":
        return {"contract": REVENUE_CONTRACT}
    if action == "ledger-export":
        if not any(customer["id"] == args.customer for customer in state["customers"]):
            raise ValueError("Unknown customer ID")
        path = HERE / args.output
        if Path(args.output).is_absolute() or not path.resolve().is_relative_to(HERE):
            raise ValueError("Output must be a relative path inside this trial directory")
        # The upstream raw feed is not account-filtered; the contract documents this.
        path.write_text(json.dumps(state["ledger"], indent=2) + "\n")
        return {"file": args.output, "rows": len(state["ledger"]), "contract": "revenue-contract"}
    if action == "find-customer":
        return [customer for customer in state["customers"] if args.query.lower() in customer["name"].lower()]
    if action == "begin-export":
        if not any(customer["id"] == args.customer for customer in state["customers"]):
            raise ValueError("Unknown customer ID. Resolve the account with find-customer.")
        jid = f"export-job-{len(state['jobs']) + 1}"
        state["jobs"][jid] = {"customer_id": args.customer, "polls": 0, "artifact_id": f"artifact-{len(state['jobs']) + 1}"}
        return {"job_id": jid, "state": "accepted"}
    if action == "export-status":
        job = state["jobs"].get(args.job_id)
        if job is None:
            raise ValueError("Unknown export job ID")
        job["polls"] += 1
        if job["polls"] < 2:
            return {"job_id": args.job_id, "state": "running"}
        rows = [invoice for invoice in state["invoices"] if invoice["customer_id"] == job["customer_id"] and invoice["status"] == "open"]
        return {"job_id": args.job_id, "state": "ready", "artifact_id": job["artifact_id"], "row_count": len(rows)}
    if action == "fetch-artifact":
        job = next((job for job in state["jobs"].values() if job["artifact_id"] == args.artifact_id), None)
        if job is None:
            raise ValueError("Expected an artifact_id from a ready export-status response, not a job_id")
        if job["polls"] < 2:
            raise ValueError("Export is not ready; poll export-status until its state is ready")
        path = HERE / args.output
        if Path(args.output).is_absolute() or not path.resolve().is_relative_to(HERE):
            raise ValueError("Output must be a relative path inside this trial directory")
        rows = [invoice for invoice in state["invoices"] if invoice["customer_id"] == job["customer_id"] and invoice["status"] == "open"]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["invoice_id", "customer_id", "amount_minor", "status"])
            writer.writeheader()
            writer.writerows(rows)
        return {"file": args.output, "rows": len(rows)}
    if action == "charges":
        rows = [charge for charge in state["charges"] if charge["customer_id"] == args.customer]
        offset = int(args.cursor.removeprefix("cursor-")) if args.cursor else 0
        end = min(offset + 2, len(rows))
        return {"items": rows[offset:end], "next_cursor": f"cursor-{end}" if end < len(rows) else None}
    if action == "refund-charge":
        prior = state["idempotency"].get(args.idempotency_key)
        if prior:
            if prior["charge_id"] != args.charge_id or prior["amount_minor"] != args.amount_minor:
                raise ValueError("Idempotency key was used for a different refund")
            return {**prior, "replayed": True}
        charge = next((charge for charge in state["charges"] if charge["id"] == args.charge_id), None)
        if charge is None or charge["status"] != "captured":
            raise ValueError("Only captured charges can be refunded")
        remaining = charge["amount_minor"] - charge["refunded_minor"]
        if not 0 < args.amount_minor <= remaining:
            raise ValueError(f"Refund amount exceeds the remaining refundable amount ({remaining} minor units)")
        charge["refunded_minor"] += args.amount_minor
        operation = {"charge_id": args.charge_id, "amount_minor": args.amount_minor, "idempotency_key": args.idempotency_key}
        state["refunds"].append(operation)
        state["idempotency"][args.idempotency_key] = operation
        return {**operation, "state": "refunded"}
    raise ValueError("Unknown action")


def main():
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    search = sub.add_parser("find-customer", help="Search accounts by name; choose the required environment")
    search.add_argument("query")
    sub.add_parser("revenue-contract", help="Read the canonical revenue accounting rules")
    ledger = sub.add_parser("ledger-export", help="Download the raw ledger; apply revenue-contract to reconcile it")
    ledger.add_argument("--customer", required=True)
    ledger.add_argument("--output", required=True)
    export = sub.add_parser("begin-export", help="Start an asynchronous open-invoices CSV export")
    export.add_argument("--customer", required=True)
    export.add_argument("--dataset", choices=["open-invoices"], required=True)
    status = sub.add_parser("export-status", help="Poll a job until ready, then use its artifact_id")
    status.add_argument("job_id")
    fetch = sub.add_parser("fetch-artifact", help="Download a ready artifact to a relative filename")
    fetch.add_argument("artifact_id")
    fetch.add_argument("--output", required=True)
    charges = sub.add_parser("charges", help="List charges; follow next_cursor until null")
    charges.add_argument("--customer", required=True)
    charges.add_argument("--cursor")
    refund = sub.add_parser("refund-charge", help="Refund a positive remaining amount on a captured charge")
    refund.add_argument("charge_id")
    refund.add_argument("--amount-minor", type=int, required=True)
    refund.add_argument("--idempotency-key", required=True)
    args = parser.parse_args()
    state = json.loads(STATE.read_text())
    try:
        result = perform(state, args.command, args)
    except (ValueError, KeyError) as error:
        record(args.command, False, str(error))
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    record(args.command, True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
