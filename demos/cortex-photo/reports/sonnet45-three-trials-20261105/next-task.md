# Proposed next task: invoice PDFs to a reconciled CSV

A user can run this directly in Claude Code: give it a small folder of invoice PDFs and ask it to extract every line item into a CSV, preserve invoice IDs/descriptions, calculate amounts with decimal arithmetic, and reconcile the line items against each invoice total. Keep original PDFs unchanged.

Use realistic variations: a normal text invoice, a two-page invoice with repeated table headers and wrapped descriptions, and an invoice with an image-only page. Put Poppler (`pdftotext`, `pdftoppm`) and Tesseract in the demo container so the host needs only Docker and API access.

This provides several concrete strategies Evolve could learn from observed feedback: inspect text extraction before choosing OCR; preserve table layout during extraction; distinguish continuation lines from new items and repeated headers; reconcile totals to detect missing/duplicated rows; use decimal arithmetic for currency. These are hypotheses about useful lessons, not promised generated guidelines.

Training: complete one invoice packet, check it against an independently prepared reference, provide corrections for actual missing or wrong rows, let Evolve generate guidelines, and review/freeze the reusable procedure advice. Do not include reference answers, invoice-specific values, or a ready-made parser in promoted guidelines.

Evaluation: three fresh baseline/guided pairs on different invoice packets with unseen vendor names, layouts and amounts. Score exact row/field accuracy, total reconciliation, and tool calls; verify injection. Keep the model, tools and prompts fixed between arms. The reference CSVs remain outside the agent workspace.

Suggested prompt:

> Extract all line items from the invoices in input/ into items.csv. Include invoice ID, vendor, description, quantity, unit price and line amount. Preserve descriptions, retain negative adjustments, and use exact decimal amounts. Reconcile each invoice's items, tax and adjustments against its printed total and report discrepancies. Keep the source PDFs unchanged; use the installed local tools and do not install packages.

Compared with the one-photo focal task, this task has more consequential tool-selection and verification decisions, plus a clear cross-document transfer test. It would require preparing the PDF fixtures and independent reference CSVs before running; no live invoice experiment has been executed yet.
