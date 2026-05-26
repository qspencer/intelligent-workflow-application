# Invoice Extraction — agent memory

Loaded by the engine into every agentic step's system prompt under
"Prior agent memory" (per G6 auto-load). Most of this content is for
the `extract` step; the three `notify_*` steps see it too but their
`goal:` field directs them at notification work, not field extraction.

## Dataset shape

Invoices in this workload come from the Tableau Superstore demo
dataset, rendered as single-page PDFs. The template is consistent:

```
INVOICE
# <invoice_number>
SuperStore
Bill To: <customer_name>
Ship To: <ship_to_city>, <ship_to_region>, <ship_to_country>
Date: <Month Day Year>          → e.g., "Dec 05 2012"
Ship Mode: <ship_mode>
Balance Due: $<total>
Item / Quantity / Rate / Amount
<one or more line items, each followed by category descriptors>
Subtotal: $<subtotal>
Shipping: $<shipping>
Total: $<total>
Order ID : <order_id>
```

## Fields to extract

Produce ONE single-line JSON object. No prose, no markdown fences. Use
exactly these field names:

```
{
  "invoice_number": "<digits after the leading #>",
  "customer_name": "<full name as it appears under 'Bill To'>",
  "invoice_date": "<ISO date YYYY-MM-DD>",
  "ship_mode": "<e.g. 'Same Day', 'Second Class', 'Standard Class'>",
  "ship_to_city": "<first comma-separated component of Ship To>",
  "ship_to_country": "<last comma-separated component of Ship To>",
  "subtotal": <number, no currency symbol>,
  "discount": <number; see Discount section below — emit 0.0 when no discount line>,
  "shipping": <number>,
  "total": <number>,
  "order_id": "<full alphanumeric id including dashes>",
  "line_items": [
    {
      "item": "<product description, the first comma-separated chunk before category breadcrumbs>",
      "category": "<the trailing breadcrumbs, e.g. 'Phones, Technology, TEC-PH-3147'>",
      "quantity": <integer>,
      "rate": <number>,
      "amount": <number>
    }
  ]
}
```

## Discount

Some invoices in this dataset include a `Discount (X%):` line between
Subtotal and Shipping — for example:

```
Subtotal:        $437.72
Discount (17%):  $74.41
Shipping:        $85.40
Total:           $448.71
```

When the Discount line is present, extract the dollar amount (not the
percentage) into the `discount` field. The percentage in parentheses
is informational only — don't try to recover it as a separate field.

When the Discount line is **absent** (most invoices don't have one),
emit `"discount": 0.0`. Do not omit the field. The downstream invariant
check is `total = subtotal - discount + shipping`; an absent discount
treated as 0.0 makes that math correct for both cases.

## Date parsing

The PDF format uses `Mon DD YYYY` (e.g. `Dec 05 2012`). Convert to ISO
`YYYY-MM-DD`. The month abbreviations are the standard three-letter
English forms. December 5 2012 → `2012-12-05`. February 14 2014 →
`2014-02-14`.

If the date string is missing or unparseable, emit
`"invoice_date": ""` rather than guessing or omitting the field.

## Line items

The text layout puts each line item's product description on one line,
followed by category breadcrumbs (`Phones, Technology, TEC-PH-3147`),
then quantity / rate / amount on subsequent lines. Some invoices have
multiple line items; treat each block of (product → category →
qty/rate/amount) as one entry.

If a line item field can't be parsed cleanly, prefer dropping that
item over emitting partial garbage.

## Numbers

All currency values are floats in USD. Strip the `$` and any commas.
`"$1,234.56"` → `1234.56`. Quantities are integers.

## Ship-to address

Format is `City, Region, Country` separated by commas. The country is
always the last segment; the city is the first. The region (state /
state-equivalent) is the middle segment — extract it as part of the
city block if you can, but it's optional. We only require
`ship_to_city` and `ship_to_country` for downstream routing.

## What this rubric is NOT for

- Validating math. The `record_invoice_extraction` step checks
  `subtotal + shipping == total` and `sum(line_items) == subtotal`
  using the values you produce. Your job is to extract; the function
  checks invariants.
- Inferring missing data. If a field genuinely isn't in the text, omit
  it (or emit empty string for required strings). Don't hallucinate.
- Catching fraud or anomalies. This is a structured-extraction task
  applied to known-good template data.

## Output discipline

Respond with ONLY the JSON object on a single line. No prose before or
after. No `\`\`\`json` fences. No explanation of what you extracted.

If the text is empty, garbled, or genuinely unparseable, return:
```
{"invoice_number": "", "customer_name": "", "invoice_date": "",
 "ship_mode": "", "ship_to_city": "", "ship_to_country": "",
 "subtotal": 0, "discount": 0, "shipping": 0, "total": 0,
 "order_id": "", "line_items": []}
```

> **Implementer note** (for the rubric author, not the model): Haiku 4.5
> has a strong training prior to wrap JSON in `\`\`\`json ... \`\`\``
> fences regardless of how strongly the rubric forbids it. The
> `_extract_invoice_fields` regex parses around the fences, so the
> downstream `steps.record.*` fields stay clean — only the raw
> `steps.extract.output_text` field carries the noise. Tried a tightened
> rubric with explicit positive + negative examples on 2026-05-26; did
> not change the behavior. Don't waste tokens trying again unless you
> swap models.

The `record_invoice_extraction` step will mark this as `parse_ok=True`
with invariant checks failing — visible in the audit log without
breaking the workflow.
