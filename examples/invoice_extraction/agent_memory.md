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

Some invoices include a `Discount (X%):` line between Subtotal and
Shipping. When present, extract the dollar amount (not the percentage)
into the `discount` field. When absent, emit `"discount": 0.0`.

## The 3-or-4-value totals block (read this carefully)

**Most extraction errors come from misreading this block.** PyMuPDF's
text extraction lays out the totals area as:

When a discount line is present (about 45% of invoices), you'll see
**four dollar values immediately followed by four labels**:

```
$<subtotal>            ← position 1 of 4 → subtotal
$<discount>            ← position 2 of 4 → discount
$<shipping>            ← position 3 of 4 → shipping
$<total>               ← position 4 of 4 → total
Subtotal:
Discount (X%):
Shipping:
Total:
```

When no discount line is present, you'll see **three dollar values
followed by three labels**:

```
$<subtotal>            ← position 1 of 3 → subtotal
$<shipping>            ← position 2 of 3 → shipping
$<total>               ← position 3 of 3 → total
Subtotal:
Shipping:
Total:
```

### Critical extraction rule

To find the right block of values, locate the sequence of labels
(`Subtotal:` / `Discount (X%):` / `Shipping:` / `Total:`) and count
backwards from there. The values appearing IMMEDIATELY before that
label sequence are the order-level totals — not the per-item rates,
which appear earlier in the text with their own `Item / Quantity /
Rate / Amount` header.

Map by **position relative to the label sequence**, not by absolute
dollar magnitude. Many invoices in this dataset have a per-line-item
rate that happens to equal one of the order-level values (e.g. an
order with a single line item where the line-item rate = subtotal).
Don't get confused by repeated values — count positions from the
labels.

### How to count: walk backwards

1. Find the `Total:` label.
2. The dollar value immediately above the `Subtotal:` label section is
   the **first** value in the totals block (always the subtotal).
3. Count the label lines: 4 labels (`Subtotal`, `Discount`, `Shipping`,
   `Total`) → expect 4 values. 3 labels → expect 3 values.
4. Map by position: value N maps to label N.

### If you're unsure

If the dollar values don't cleanly partition into 3 or 4 in the
position immediately before the label block, emit your best guess for
each field and set `discount: 0.0`. The downstream `total_balanced`
invariant check (`total == subtotal - discount + shipping`) will catch
the mismatch and route the invoice for review without breaking the
pipeline.

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
