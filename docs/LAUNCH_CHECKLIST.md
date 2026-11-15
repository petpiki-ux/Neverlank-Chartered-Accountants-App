# Running the app safely - checklist

Last brought up to date: 15 November 2026. Tick each line. Anything marked **(you)** can only be done by you on Render or GitHub.

## Already done (no action)
- The app runs on Render from the `main` branch and redeploys on every push.
- The database is **PostgreSQL 16** (`neverlank-db`, Basic 1 GB). Outside connections are blocked.
- The disk is **4 GB** (uploads and backups live there).
- One worker (`--workers 1 --threads 8`) - keep it that way: the sign-in lockout and call signalling live in memory.
- Idle sign-out after 2 hours (`NL_IDLE_MINUTES` changes it; `0` switches it off).

## 1. Accounts and sign-in
- [ ] **(you)** Admin password changed under *My Account* (10+ characters). If it is still `changeme123`, change it today.
- [ ] **(you)** Everyone with partner, manager or admin rights turns on two-factor sign-in: *My Account > Two-factor sign-in > Set up*; keep the 8 recovery codes printed, not on the same phone.
- [ ] **(you)** To make it compulsory for the senior roles, add `NL_REQUIRE_2FA_ROLES` = `admin,partner,manager` on Render and redeploy.
- [ ] A colleague who loses their phone and recovery codes: an admin opens *Team > Edit* and ticks "Switch off two-factor sign-in".

## 2. Keys and settings (Render > Environment)
- [ ] `SECRET_KEY` set; never share it.
- [ ] `PUBLIC_BASE_URL` is the real https address.
- [ ] `ANTHROPIC_API_KEY` set (AI features), with credit on the Anthropic account.
- [ ] `DATABASE_URL` points at `neverlank-db` (it does today).
- [ ] **(you)** The database password was shown in a chat earlier: **reset it** (Render > neverlank-db > Info) and let the service pick up the new connection string.

## 3. Data safety
- [ ] **(you)** Confirm Render's own database backups are on for `neverlank-db` (Render > neverlank-db > Backups) and that you can see a recent one.
- [ ] **(you)** Download an app backup from the *Backup* page before every big import, and keep a copy off the server.
- [ ] Look at *Render > Disks* monthly; raise the disk before it is 80% full.

## 4. Automatic tests
- [ ] The GitHub **Tests** workflow (Actions tab) is green on the latest commit. It runs every smoke test on SQLite and on PostgreSQL for each push. A red cross means do not rely on that version: open the run and read which test failed.

## 5. Try the AI features once, live
- [ ] *Corporate Finance > Valuation > Report > Writer*: the AI draft button fills the sections.
- [ ] *ZIMRA correspondence*: ask the AI panel about a section of the Income Tax Act; the answer should quote the filed Act.
- [ ] *Legislative Updates > Ask the library*: ask "What is the standard rate of VAT?"; the answer shows a short answer, numbered sources and a confidence label. *Search the library > Draft advice* produces a memo that downloads as Word.
- [ ] If any fails: Render > Logs, search for `anthropic` or `Traceback`.

## 6. Still to decide later (not blocking)
- A professional penetration test by an outside firm before advertising the app publicly.
- A Content-Security-Policy header (the inline scripts in the pages must move into files first).
- Daily automatic off-server backups.
