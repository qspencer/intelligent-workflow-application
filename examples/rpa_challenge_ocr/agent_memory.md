# RPA Challenge OCR — agent memory

Loaded into the system prompt of every agentic step in this workflow
(per G6 auto-load). Edit freely; `memory_hash` on each run lets you
correlate behavior changes with edits.

## Page model

The target site (https://rpachallengeocr.azurewebsites.net/) is a small
SPA. The invoice table is rendered client-side (jQuery DataTables) into
`<table id="tableSandbox">` — the `<tbody>` is empty in the initial
HTML and only populates after the user clicks `#start` (note: the
button's id is `start`, not `buttonStart`) and the JS finishes its
async fetch. **Always wait for `#tableSandbox tr` to be visible
before reading the table.**

**The table is paginated.** DataTables shows ~4 rows per page; the
challenge has more rows spread across multiple pages. Reading only
page 1 gives an incomplete result, which the server rejects with
`{"success":false}`. To walk pages:

- The pagination strip is `#tableSandbox_paginate` — its text
  contains all page numbers (e.g. `"Previous 1 2 3 Next"`).
- Page-number buttons are `<a>` tags inside that strip with the page
  number as their visible text. Click selectors like
  `#tableSandbox_paginate a:has-text("2")`.
- After clicking a page button, DataTables synchronously re-renders
  `<tbody>` — the next `browser_read_table` call returns the new
  page's rows.
- The currently-active page has class `paginate_button current`.
  "Previous" / "Next" buttons get class `disabled` when at the
  respective edge.

The submit form lives inside `<div id="submit">` and contains:
- `<input type="file" name="csv">` — the file-upload target.
- A styled `<div class="btn btn-block btn-md btn-success btn-start">`
  wrapping a `<span>SUBMIT</span>`. **This is the submit "button"** —
  not an actual `<button>`. Click selector: `#submit .btn` (or
  `#submit .btn-start`). The div has a JS handler that POSTs the form.

The Invoice column cells contain an icon-only link: `<a href="...">
<span class="glyphicon"></span></a>`. The visible text is empty —
`browser_read_table` now also returns the URL under a sibling key
`Invoice_href` (the parser adds `<col>_href` for cells containing a
single `<a>`). The hrefs are absolute URLs resolved against the page,
so they're directly usable with `browser_fetch_url`.

`browser_fetch_url` is the right tool for grabbing each invoice JPG:
the page uses `target="_blank"` links that don't fire a browser-level
download event, so `browser_download` (which wraps `expect_download`)
would time out. `browser_fetch_url` issues a direct GET via the
browser's session context and saves the body to the downloads dir.

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

## CSV format constraints

The challenge validates the uploaded CSV against an exact format
(see https://rpachallengeocr.azurewebsites.net/invoices/example.csv).
**Column names are CamelCase, not snake_case**, and the order matters:

```
ID,DueDate,InvoiceNo,InvoiceDate,CompanyName,TotalDue
```

Dates use **DD-MM-YYYY** (e.g. `25-02-2019`), not ISO `YYYY-MM-DD`.
Totals are plain decimal numbers like `1234.40` — no currency symbol,
no thousands separator. The example's first row:

```
5jef1y8yx4t8yupbpo3fzg,25-02-2019,10021,13-02-2019,Sit Amet Corp.,1234.40
```

## Response format

Each `extract_invoices` iteration emits a single JSON array — one
object per kept row, in the same order as kept_rows. No prose,
no markdown fences. Keys exactly match the CSV columns:

```
[{"ID":"...", "DueDate":"...", "InvoiceNo":"...",
  "InvoiceDate":"...", "CompanyName":"...", "TotalDue":"..."}, ...]
```

The deterministic `write_csv` step downstream consumes this output
directly. Any extra prose around the JSON is now parsed out tolerantly
(D8b), but raw JSON is still preferred.
