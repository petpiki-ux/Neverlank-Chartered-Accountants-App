# Neverlank Audit App — Security Review

**Date:** 16 September 2026
**Scope:** Full application — `app.py`, `config.py`, `extensions.py`, `auth.py`, `models.py`, `clients.py`, `engagements.py`, `users.py`, `doc_templates.py`, `hr.py`, `financials.py`, `seed.py`, and all templates.
**Method:** Manual source review of the live codebase (not a black-box pentest). Stack: Flask 3.0.3 + Flask-SQLAlchemy 3.1.1 + Flask-Login 0.6.3, SQLite (dev) / Postgres (prod, via `DATABASE_URL`), hosted on Render.

This is a findings report, not applied fixes — nothing in the codebase was changed. Findings are ordered by severity. Each includes the file/line, why it matters for a firm handling confidential client financial data, and a concrete fix.

---

## Critical

### C1. No CSRF protection anywhere in the app
Nothing in `requirements.txt` or the code sets up CSRF defenses — no Flask-WTF `CSRFProtect`, no per-form token, no `SameSite` cookie policy that would substitute for one. Every state-changing action in the app is a plain `<form method="post">` authenticated only by the session cookie: deleting a client (`clients.py:80`) or engagement (`engagements.py:182`), changing a user's password or role (`users.py:78`), granting/revoking firm permissions (`users.py:101`), and — most importantly for this app's purpose — every **Reviewer** and **Partner sign-off** action (e.g. `engagements.py:300-361`, `:707-736`, `:1425-1454`, and the equivalent routes for tasks, risk assessments, materiality, entity understanding, analytical review, trial balances and adjustments).

**Why it matters here specifically:** a partner sign-off is meant to be an attestation that the partner actually looked at the work. Without CSRF protection, a malicious page visited by a logged-in partner (in the same browser, any tab) can silently submit a forged POST to `/engagements/checklist/<id>/partner-sign` and the app cannot tell the difference from a real click. That's not just "someone deletes a client they didn't mean to" — it's the audit trail itself becoming falsifiable.

**Fix:** add `Flask-WTF` (`pip install Flask-WTF`), initialize `CSRFProtect(app)` in `create_app()`, and add `{{ csrf_token() }}` as a hidden field to every `<form>` (a single Jinja macro/include in `base.html` covering all forms is the fastest path). This is the single highest-value change in this review.

### C2. Hardcoded fallback `SECRET_KEY`
`config.py:52`:
```python
SECRET_KEY = os.environ.get("SECRET_KEY", "neverlank-dev-secret-change-me")
```
If the `SECRET_KEY` environment variable is ever missing on Render (a fresh deploy, a misconfigured env group, a redeploy that drops it), the app silently falls back to this fixed string, which is now sitting in the repository/this review. `SECRET_KEY` is what signs the Flask-Login session cookie — anyone who knows it can hand-craft a valid session cookie for **any user id, including the admin account**, without ever knowing a password. This is a full authentication bypass, and it fails silently (no crash, no log, the app just runs "normally" but insecurely).

**Fix:** make the app refuse to start in production without a real `SECRET_KEY` — e.g. `SECRET_KEY = os.environ["SECRET_KEY"]` guarded by an explicit `if os.environ.get("RENDER") or not app.debug: require it`, or simply always require the env var and only use a dev fallback when `FLASK_DEBUG=1`. Also confirm right now, in the Render dashboard, that `SECRET_KEY` is actually set to a long random value (`python -c "import secrets; print(secrets.token_hex(32))"`) and isn't the default.

---

## High

### H1. No rate limiting or lockout on login
`auth.py:10-26` has no throttling of any kind. Combined with the seeded default admin account (`seed.py:336-339`, `admin` / `changeme123`, created automatically with zero setup steps on first run), an attacker who reaches the login page can attempt unlimited password guesses against `admin` or any known/guessed username.

**Fix:** add `Flask-Limiter` on the login route (e.g. 5 attempts per username+IP per 15 minutes, with a generic error message either way), and confirm the seeded admin password has actually been changed on the live deployment — the README should say to do this immediately after first login, and ideally the app should force a password change on first login rather than relying on someone remembering.

### H2. No access control tied to engagement staffing — every authenticated user can see and act on every client
Across `engagements.py`, `hr.py` and `clients.py`, the only gate on viewing or editing an engagement's data is `@login_required` — there is no check that `current_user` is on `engagement.team_members`, is `engagement.partner`/`manager`, or has any relationship to that client at all. A brand-new "staff" account can immediately view, upload/download documents for, and edit the risk assessment, materiality calculation, trial balance and financial statements of **every** client the firm has, not just engagements they're staffed on.

More specifically for the sign-off workflow you asked about: `review_checklist_item`, `partner_sign_checklist_item` and every equivalent route only check `current_user.role in REVIEWER_ROLES` / `PARTNER_SIGNOFF_ROLES` (`engagements.py:300-361` etc.) — never that the reviewer/partner is the one actually assigned to that engagement (`engagement.partner_id`, `engagement.manager_id`, `engagement.team_members`). The app does correctly stop someone from reviewing their *own* work, but it does not stop, say, Partner B from signing off Partner A's engagement that they've never seen. If your firm's quality-control expectation is "the engagement partner reviews their own engagements," the system currently can't enforce or evidence that — it only proves "*a* partner-role account clicked the button."

**Fix (needs a product decision, not just a patch):** either (a) treat this as accepted firm-wide-visibility-by-design (common in small firms) and document it as such, or (b) add a per-engagement authorization check (team membership, or partner/manager identity for the partner-sign routes specifically) before allowing view/edit/review/sign actions.

### H3. Self-service password change requires no re-authentication
`auth.py:37-51` (`/auth/account`) lets the logged-in user set `new_password` directly from the form with no check of the *current* password first. If a session is ever hijacked (stolen cookie, shared/unlocked computer, XSS in some future change), the attacker's first move — changing the password to lock the real owner out — requires no proof they know the existing credentials.

**Fix:** require and verify the current password (`check_password`) before accepting `new_password`. Consider also invalidating other active sessions when the password changes.

### H4. No explicit session-cookie hardening
Nothing in `config.py` sets `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, or `PREFERRED_URL_SCHEME`. Flask's defaults mean `HttpOnly` is on (good), but `Secure` is off and `SameSite` isn't pinned — so if Render (or a future misconfiguration) ever serves the app over plain HTTP even briefly, the session cookie could go out unencrypted, and the missing `SameSite=Lax/Strict` removes a browser-level backstop that would otherwise have partially compensated for C1.

**Fix:** in `config.py`: `SESSION_COOKIE_SECURE = True`, `SESSION_COOKIE_SAMESITE = "Lax"`, `PREFERRED_URL_SCHEME = "https"`, and if Render sits behind a proxy that terminates TLS, add Werkzeug's `ProxyFix` so Flask knows the original request was HTTPS.

---

## Medium

### M1. Uploads are validated by extension only, not content
Every upload path — engagement documents (`engagements.py:751-796`, extensions incl. `zip`), document templates (`doc_templates.py:48-50`, `.docx`/`.xlsx`), policies (`hr.py:66-68`), timesheet uploads (`hr.py:363-366`) — checks only the filename's extension via `secure_filename` + a hardcoded allow-list. Nothing verifies the uploaded bytes actually match the claimed type. Downloads are served via `send_from_directory(..., as_attachment=True)`, which forces a download rather than inline rendering, so this isn't an immediate stored-XSS vector — but it does mean a mislabeled or malicious file (including inside a `.zip`) can sit in the shared document store indefinitely with no content-based check or malware scanning, and it's shared firm-wide (see H2).

**Fix:** add a lightweight magic-byte check (e.g. `python-magic`) as a second gate alongside the extension check, and consider virus-scanning uploads (e.g. ClamAV) before they're written to the persistent disk, given these are real client financial records being shared across the firm.

### M2. No password policy
`users.py:70` falls back to the literal string `"changeme123"` if an admin creates a user without typing a password, and there is no minimum length/complexity check anywhere passwords are set (`users.py:70,93`, `auth.py:47`). Combined with H1 (no lockout), weak/guessable passwords are the easiest path in.

**Fix:** enforce a minimum length (e.g. 10+ characters) at the point passwords are set, and stop silently substituting a fixed fallback password — require one to be entered, or generate and display a random one-time password instead.

### M3. "Delete" doesn't delete the underlying files — data lives on past the point the UI says it's gone
`delete_client` (`clients.py:80-89`) and `delete_engagement` (`engagements.py:182-192`) rely on SQLAlchemy cascade deletes for the database rows, but never touch the actual files on disk (`Document.stored_filename` under `UPLOAD_FOLDER`, or template/policy files). Only the single-document `delete_document` route (`engagements.py:808-821`) explicitly `os.remove`s its file. The client-delete confirmation dialog says *"This cannot be undone"* (`templates/clients/detail.html:47`), which reads as "this client's data is gone" — but the uploaded working papers/financials for that client remain sitting on the persistent disk indefinitely with no way to reach or purge them through the app.

**Fix:** when cascading a client/engagement delete, also walk its `Document` rows (and, if applicable, orphaned template/policy files) and remove the files from disk — or explicitly change the UI copy to say documents are retained, if that's actually the intended retention behaviour.

### M4. No security response headers
No CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, or `Referrer-Policy` are set anywhere (no `flask-talisman`, no manual `after_request` hook). Current XSS exposure is low because Jinja2 auto-escaping is used consistently and no `|safe`/`render_template_string` was found anywhere in the templates or code — but there's no defense-in-depth layer to catch a future mistake, and the lack of `X-Frame-Options`/frame-ancestors leaves the login and every other page embeddable in a clickjacking iframe.

**Fix:** add `flask-talisman` (or a small manual `after_request`) setting at minimum `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Strict-Transport-Security` (once HTTPS is confirmed end-to-end), and a baseline CSP.

### M5. No dedicated audit/security log
Security-relevant events — failed logins, permission changes (`users.py:101-131`), role changes, deletions — aren't logged anywhere beyond the domain-specific `completed_by_id`/`reviewed_by_id`/`updated_by_id` columns already built into the sign-off workflow (which are good, and consistently applied). Those columns tell you who last touched a *record*, but there's no independent, append-only trail of security events (e.g. "user X's login failed 40 times", "admin Y turned off delete-protection for staff role at 3am") that you could review to detect misuse after the fact.

**Fix:** a simple `AuditLogEntry` table (actor, action, target, timestamp, IP) written on login attempts, permission changes, role changes and deletions would materially improve your ability to investigate an incident later — this matters more for an audit/assurance firm than most businesses, since your own credibility rests on being able to demonstrate control integrity.

---

## Low / Informational

### L1. Dynamic SQL in the startup auto-migration is currently safe, but fragile
`app.py:64-70` builds `ALTER TABLE`/`PRAGMA` statements with f-strings inside `text(...)`. Today the table/column names come only from a hardcoded dict (`additions` in `app.py:41-63`), so there's no injection here — but this pattern (string-interpolating identifiers into SQL) is exactly the shape of a future SQL-injection bug if anyone ever extends this function to read a name from a variable. Worth a comment flagging it as "identifiers only, never user input," or switching to an explicit allow-list check before interpolating.

### L2. Keep dependencies current
`requirements.txt` pins Flask 3.0.3, Werkzeug 3.0.4, Flask-Login 0.6.3, Flask-SQLAlchemy 3.1.1, openpyxl 3.1.5, gunicorn 23.0.0 — all reasonable versions as of this review, no known critical issues identified for this exact set. There's no automated dependency scanning (Dependabot, `pip-audit`, etc.) in the repo, though. Worth turning on `pip-audit` in CI or enabling Dependabot alerts so a future CVE in one of these gets flagged automatically rather than relying on someone remembering to check.

### L3. Confirm platform-level data-at-rest protections
The app itself does no application-level encryption of stored financial data or documents — reasonable for a Flask app at this scale, provided the hosting platform's protections are actually in place. Worth explicitly confirming (not a code finding, an infra checklist item): Render's persistent disk / managed Postgres encryption-at-rest is enabled, HTTPS is enforced end-to-end (ties into H4), and database/disk backups are themselves access-controlled.

---

## What's already solid (no action needed)

- **Passwords** are hashed with Werkzeug's `generate_password_hash`/`check_password_hash` (modern algorithm, proper salting) — never stored or compared in plaintext.
- **SQL injection risk is low.** Every query across all six blueprints goes through the SQLAlchemy ORM with parameter binding; no raw string-built queries incorporating user input were found anywhere (the one f-string-built SQL, L1, only ever interpolates hardcoded identifiers).
- **XSS risk is low.** Jinja2's autoescaping is relied on consistently; no `|safe` filter and no `render_template_string` call exist anywhere in the codebase.
- **Role escalation to admin is correctly blocked**: `users.py:55-63,86-89` explicitly prevents a non-admin (even one holding the delegated "Manage Team Members" permission) from granting themselves or anyone else the admin role.
- **Preparer-cannot-review-their-own-work is enforced everywhere**, consistently, across every sign-off workflow in the app (checklist items, tasks, risk assessment, materiality, entity understanding, analytical review, trial balance, adjustments) — this is exactly the kind of segregation-of-duties control you'd want baked in, and it's applied uniformly rather than missed in a few spots.
- **File uploads are size-capped** (`MAX_CONTENT_LENGTH = 25 MB`) and stored filenames are randomized/namespaced (`uuid4` + `secure_filename`), so path traversal via a crafted filename isn't possible and one user can't collide/overwrite another's file by name.

---

## Suggested priority order

1. **C1** — add CSRF protection (highest impact, and directly undermines your sign-off audit trail while missing).
2. **C2** — confirm/enforce a real `SECRET_KEY` in production; make the app fail loudly if it's missing.
3. **H3** — require current password to change password.
4. **H1** — rate-limit login; confirm the seeded admin password has been rotated on the live deployment.
5. **H4** — session cookie hardening (quick config change).
6. **H2** — decide firm policy on cross-engagement visibility, then implement if you want it restricted.
7. Everything else (M1–M5, L1–L3) as time allows — none are urgent on their own, but M3 (files outliving "deleted" records) is worth fixing soon given the client-confidentiality angle.

Happy to implement any or all of these directly — C1, C2, H1, H3 and H4 in particular are all small, mechanical, low-risk changes I can make and verify in one pass if you'd like.
