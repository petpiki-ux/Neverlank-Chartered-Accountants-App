# Before opening the app to clients - checklist

Tick each line. Anything marked **(you)** can only be done by you on Render or GitHub; the rest is already built into the app.

## 1. Accounts and sign-in
- [ ] **(you)** Sign in as admin and change the admin password under *My Account* (10+ characters). If it is still `changeme123`, change it today.
- [ ] **(you)** Everyone with partner, manager or admin rights turns on two-factor sign-in: *My Account > Two-factor sign-in > Set up*, scan the picture with Microsoft/Google Authenticator, keep the 8 recovery codes somewhere safe (printed, not on the same phone).
- [ ] **(you)** To make it compulsory for the senior roles, add this environment variable on Render, then redeploy: `NL_REQUIRE_2FA_ROLES` = `admin,partner,manager`. Until a person has set it up, every page sends them to the set-up screen. Leave it empty to keep it optional.
- [ ] **(you)** A colleague who loses their phone and recovery codes: an admin opens *Team > Edit* and ticks "Switch off two-factor sign-in", then the colleague sets it up again.
- [ ] Idle sign-out: people are signed out after 2 hours without a click. Change with `NL_IDLE_MINUTES` on Render (`0` switches it off).

## 2. Keys and settings on Render (Environment tab)
- [ ] `SECRET_KEY` is set (the blueprint generates one). Never share it.
- [ ] `PUBLIC_BASE_URL` is your real https address (it also marks cookies "Secure").
- [ ] `ANTHROPIC_API_KEY` is set only if you want the AI features (valuation report writer, ZIMRA letter AI).
- [ ] Keep the web service on **1 worker** (`--workers 1 --threads 8`): the sign-in lockout and the call signalling live in memory.

## 3. Data safety
- [ ] **(you)** Download a backup from the *Backup* page now, and again before every big import. Keep copies off the server.
- [ ] **(you)** Move the database to PostgreSQL before clients log in: follow the steps in `database_notes.md` (create the Render database, run `tools/sqlite_to_postgres.py --dry-run`, then for real, then set `DATABASE_URL`). Keep the old SQLite file untouched for a few weeks.
- [ ] The Render disk is only 1 GB: look at *Render > Disks* monthly; raise it before it is 80% full (uploads and backups live there).

## 4. Try the AI features once, live (cannot be tested from here)
Do this after the `ANTHROPIC_API_KEY` is set, with a dummy client:
- [ ] Valuation: open a corporate-finance valuation, *Report > Writer*, press the AI draft button. It should fill the sections; if it says the AI is off, the key is missing or wrong.
- [ ] ZIMRA letters: open any ZIMRA correspondence, use the AI panel, ask one question about a section of the Income Tax Act. The answer should quote the section from the filed Act.
- [ ] If either fails, open Render > Logs, search for `anthropic` or `Traceback`, and send the lines to your developer. Check the Anthropic account has credit.

## 5. Automatic checks
- [ ] The GitHub **Tests** workflow (Actions tab) is green on the latest commit. It runs every smoke test on SQLite and on PostgreSQL on each push. A red cross means do not rely on that version: open the run and read which test failed.

## 6. Still to decide later (not blocking)
- A professional penetration test by an outside firm before you advertise the app to the public.
- A Content-Security-Policy header (needs the inline scripts in the pages moved into files first).
- Daily automatic off-server backups (Render's own database backups cover PostgreSQL once you move).
