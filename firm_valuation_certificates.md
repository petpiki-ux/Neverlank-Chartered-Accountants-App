# Valuation Certificates (d57)

Valuations in the top menu: a valuation certificate is written in the app, previewed, downloaded as **Word or PDF on the firm letterhead**, and signed by the Managing Partner with the same **Sign as Managing Partner** button as invoices, receipts and letters to ZIMRA.

First use: the Broxmen Investments coal claim, Binga (NCA/DVC/2026/001, US$1,000,000, range US$500,000 to US$2,000,000, US$56 / 111 / 222 per hectare).

## Files

- `models.py` - `ValuationCertificate` (with `MPSignedMixin`). New table, created by `db.create_all()`; no migration needed.
- `valuations.py` - blueprint `/valuations`: list, new (pick a starting point), edit, view/preview, download (docx, pdf), delete.
- `valuation_docs.py` - the starting texts (`DESKTOP_TEMPLATE`, `BLANK_TEMPLATE`), the worked-out conclusion, the value table, `build_docx`, `build_pdf`.
- `templates/valuations/` - `list.html`, `pick.html`, `form.html`, `view.html`.
- `mpsign.py` - `KINDS["valuation"]`, and `can_sign` refuses a certificate whose signatory is not the Managing Partner, whose figures are inconsistent, or that still has a `[CONFIRM: ...]` placeholder.
- `brand_docs.content_key` - fingerprint of the certificate (reference, dates, currency, area, three figures and every text field).
- `smoke_test_valuations.py` - 63 checks.

## How it works

- **Starting points** (Valuations > New certificate): *Desktop Valuation Certificate - Broxmen coal claim, Binga* (everything pre-filled) or *Blank certificate* (same layout, standard closing wording, the rest to complete). The reference is the next free `NCA/DVC/<year>/<NNN>`.
- **Figures** are typed once in whole units: low, adopted, high, and the area in hectares. From them the app works out the conclusion sentence ("US$1,000,000 (one million United States dollars), within a range of US$500,000 to US$2,000,000"), the Low / Adopted / High table, and the per-hectare column (only when an area is given). Checks: low <= adopted <= high; low and high go together; adopted is required. Currency is USD or ZWG.
- **Text** fields use two small conventions: `**Lead-in.**` makes a bold lead-in; in the assumptions an opening sentence, a blank line and then one condition per line starting `- `; sources are `- [Title](https://link)`.
- **Placeholders**: whatever the app cannot know stays as a visible `[CONFIRM: ...]` (yellow in Word and on screen, red in the PDF). The letterhead shows "DRAFT - placeholders to complete", and **the certificate cannot be signed until they are completed**. The Broxmen wording carries two: the title reference (claim or special grant number, registration date and registering office) and the statement "I have no interest in the subject asset", which only the signatory can confirm.
- **Layout** follows the firm's other documents: navy letterhead band with logo, details strip (certificate no., valuation date, issued, currency), a "Prepared for" panel, gold-ruled headings, a particulars table with tinted labels, a navy-header value table with the Adopted row highlighted and a navy Adopted value panel, bullets for the assumptions, a signature block (signature, rule, name and credentials, title, address, date), the sources, and the navy footer with page numbers. A4.

## Signing

- Only an Admin (the Managing Partner) signs or removes the signature.
- The signature image and "Signed electronically by the Managing Partner on <date>" appear on the preview, the PDF and the Word file, only when the certificate is signed, unchanged since, and **Signed by** is the Managing Partner.
- Changing any figure or wording after signing drops the signature (the page says so); it has to be signed again. Re-saving unchanged content keeps it.
- A signed certificate cannot be deleted (remove the signature first).

## Who can do what

Partners and Admins write and change certificates. Everyone who can see the linked client (or any user, when the certificate is not linked to a client) can open and download one.

## Not done / for later

- Linking a certificate to an engagement or filing it in the client's file automatically.
- A fee/invoice link, and a version history of earlier wordings.
- Per-hectare values for a unit other than hectares.
