# RPA Challenge OCR — agent memory

Loaded into the system prompt of every agentic step in this workflow
(per G6 auto-load). Edit freely; `memory_hash` on each run lets you
correlate behavior changes with edits.

## Page model

The target site (https://rpachallengeocr.azurewebsites.net/) is a small
SPA. The invoice table is rendered client-side (jQuery DataTables) into
`<table id="tableSandbox">` — the `<tbody>` is empty in the initial
HTML and only populates after the user clicks `#buttonStart` and the JS
finishes its async fetch. **Always wait for `#tableSandbox tr` to be
visible before reading the table.**

Each row in the populated table has three columns: an ID, a due date,
and a link to a JPG image of the invoice. The link text says
"Download invoice"; its `href` may be an absolute URL or a relative
path (don't assume).

## OCR rubric (invoice extraction)

Invoice JPGs are scans of a single-page template. Extract exactly
these four fields per invoice. Names in the OCR text vary in casing,
spacing, and label punctuation; treat the labels case- and
whitespace-insensitively.

- **`invoice_number`** — the value next to "Invoice Number" / "Invoice
  No" / "Invoice #". Strip the label, surrounding whitespace, and any
  stray punctuation. It's a short alphanumeric ID, typically 5–12
  characters. Don't include leading zeros that the OCR layer
  sometimes hallucinates as "O"s — keep only `[A-Za-z0-9-]+`.
- **`invoice_date`** — the date on the invoice itself (NOT the due
  date from the table). Look for labels "Invoice Date" / "Date" /
  "Date Issued". Normalize to `YYYY-MM-DD`. The template formats
  vary; the most common are `DD/MM/YYYY` and `MMM DD, YYYY`.
- **`company_name`** — the name of the issuing company. Usually
  appears in the header section, above the address block. Strip
  trailing punctuation. If multiple plausible candidates exist, prefer
  the one labelled "From:" or "Bill To:" — they correspond to issuer
  and recipient respectively, and the issuer is what we want.
- **`total_due`** — the final total payable. Look for labels "Total"
  / "Total Due" / "Amount Due" / "Balance Due". Strip currency
  symbols and thousands separators; keep the decimal as a period.
  Return as a plain number string (e.g. `1234.56`, not `$1,234.56`).

## When OCR text is ambiguous

OCR layer can produce: doubled characters, mistaken 0/O and 1/I/l
substitutions, missing punctuation, and rare line reorderings on
multi-column templates.

- If two reasonable values exist for the same field, pick the one
  more consistent with the labelled context (proximity to the label
  matters more than position on the page).
- If a field is genuinely absent from the OCR output, emit the empty
  string `""` for that field — never invent. The downstream CSV will
  carry through.
- Never confuse `due_date` (from the table, already in the row dict)
  with `invoice_date` (from the image). They are distinct fields.

## Response format

Each `extract_invoices` iteration emits a single JSON array — one
object per kept row, in the same order as kept_rows. No prose,
no markdown fences. Keys exactly:

```
[{"id":"...", "due_date":"...", "invoice_number":"...",
  "invoice_date":"...", "company_name":"...", "total_due":"..."}, ...]
```

The deterministic `write_csv` step downstream consumes this output
directly. Any extra prose around the JSON breaks the parse.
