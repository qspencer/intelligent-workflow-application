# PDF classifier — agent memory

Loaded by the engine into the classifier step's system prompt under
"Prior agent memory". Edit freely; structured Markdown is the storage format.

## Categories

| document_type | Signals |
|---|---|
| invoice | Has an invoice number, line items, total due, payment terms. Often "INVOICE" at the top. |
| receipt | Records a payment that already happened. Has "Paid" / "Thank you for your purchase" / a transaction id. Smaller and lower-detail than invoices. |
| contract | Multi-page agreement language. Section/article numbering. Signature blocks at the end. |
| report | Multi-page narrative or analytical content. Charts, figures, executive summary. No transactional fields. |
| letter | Single-recipient correspondence. Salutation ("Dear …") + signoff. Date + return address near top. |
| form | Pre-printed structure with blank fields for the user to complete. Lots of horizontal rules / fillable boxes. |
| other | None of the above with confidence. Better to file here than misfile. |

## Tie-break rules

- Invoice vs receipt: if the document says it's a request for payment, it's an invoice. If it confirms a payment that has already been made, it's a receipt.
- Invoice vs form: invoices have specific numbers and totals. Forms have placeholders.
- Letter vs report: letters are addressed to one party. Reports are not.
- Contract vs report: contracts impose obligations; reports describe state.

## Output discipline

- Respond with **only** the JSON object. No prose. No markdown fences.
- `summary`: one short sentence. Avoid restating obvious header text.
- `key_fields`: include things like vendor, recipient, date, total, document number when they're clearly present. Skip fields you'd have to guess at.

## Failure modes to avoid

- Don't classify obvious junk pages (blank, scan errors, single-line OCR garbage) as "other" — let the human decide. Return `{"document_type": "other", "summary": "extraction was empty or unintelligible", ...}` so a downstream step can flag it.
- Don't invent dates, totals, or vendor names that aren't in the extracted text.
