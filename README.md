# Neverlank Audit, Assurance & Consulting App

An internal web app for tracking audit, assurance, consulting and
secretarial engagements: clients, engagements, working papers/documents,
risk assessments & checklists, and team task assignment.

It runs on one office computer and the rest of the team accesses it from
their own browser over your local network — no internet or cloud hosting
required.

## Windows: quick start (recommended, no typing required)

1. If Python isn't installed yet, you'll be prompted the first time you run
   the app below — it takes 2 minutes, one time only, on this one computer.
2. Double-click **`Start_Neverlank_App.bat`** (with this computer connected
   to the internet, just for this first run).
   - The first time, it quietly sets itself up (creates a local Python
     environment and downloads the app's few dependencies — small, standard
     Python libraries, a matter of seconds). If there's no internet
     available, it automatically falls back to the offline copies bundled
     in the `wheelhouse` folder instead.
   - It then starts the app and opens it in your browser automatically.
3. From now on, just double-click `Start_Neverlank_App.bat` whenever you
   want to use the app — internet is no longer needed once this first setup
   has completed. Keep that black window open while you're using it; closing
   it stops the app.

**Default login:** username `admin`, password `changeme123` — please change
this immediately from the Account page (top right) once you're in, and add
your team members from the "Team" menu.

The Neverlank logo appears on the sign-in screen and in the top-left of
every page (also used as the browser tab icon) — swap
`static/img/neverlank-logo.png` for a different image any time to rebrand.

### Want an actual .exe file instead?

Once `Start_Neverlank_App.bat` has been run successfully at least once,
double-click **`Build_Standalone_Exe.bat`**. This builds a real
`NeverlankApp.exe` in this same folder (takes a minute or two). After that:

- Double-click `NeverlankApp.exe` any time to run the app — no scripts, no
  Python knowledge needed.
- You can copy that single `.exe` file to other office computers and it will
  run there too, even without Python installed on them.

## Accessing it from other computers on the office network

Whichever way you started it (bat file or the built .exe), once it's
running:

- **On the computer running it:** open `http://localhost:5000`
- **On other computers on the same office network/WiFi:** find this
  computer's local IP address and open `http://<that-ip>:5000`, e.g.
  `http://192.168.1.42:5000`

To find this computer's local IP on Windows: open Command Prompt and type
`ipconfig`, then look for "IPv4 Address".

## Mac / Linux / manual setup

If you'd rather run it by hand (or you're on Mac/Linux):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

The app auto-creates its database and the admin login on first run — no
separate seed step needed (though `flask --app app seed` still works if you
ever want to re-add the starter checklist templates).

## Everyday use

- **Clients** — add each client once, including its industry (a standard
  list to pick from, with an "Other" free-text option) so engagements for
  that client can automatically align checklist/substantive-procedure
  content to the right sector — see Substantive Procedures below.
- **Engagements** — create one per assignment (e.g. "FY2026 Statutory Audit"),
  set its type, status, partner/manager, deadline, and team members. You can
  start it from a checklist template to pre-populate audit procedures.
  Choosing the **Secretarial** type reveals a subdivision field — Company
  Registrations, Trusts, or PVOs — shown alongside the type everywhere the
  engagement appears (list, detail page, dashboard) so secretarial work is
  clearly split out from audit/assurance/consulting.
  The engagement detail page's tabs follow the order a real audit is
  actually planned in: understand the entity, then analytical review, then
  risk assessment, then materiality/planning, then the checklist itself.
- **Three-tier sign-off everywhere** — every workpaper in the app (the
  checklist, tasks, Understanding the Entity, Analytical Review, Risk
  Assessment, Planning/materiality, the Trial Balance, audit adjustments,
  Financial Statements, and each Substantive Procedures area) carries the
  same three-tier sign-off: **Preparer** (auto-recorded whenever the work is
  saved), **Reviewer** (a supervisor, partner or admin other than the
  preparer), and **Engagement Partner sign-off** — a separate, discretionary
  final sign-off that a partner or admin can give on any workpaper "where
  they see fit", independently of whether it's been reviewed yet, but never
  on their own work. Editing a workpaper after it's been signed off clears
  both the review and the partner sign-off automatically, since it needs a
  fresh look once changed.
- **Understanding the Entity tab** — system-guided, not a blank text box:
  five structured prompts covering the nature of the entity, the industry
  and regulatory environment, accounting policies, objectives/strategies
  and related business risks, and how management measures performance.
  Fill each section in and save; this is background documentation rather
  than a score, but carries the same Preparer/Reviewer sign-off as the rest
  of the app once all five sections are complete, and re-saving clears any
  stale review.
- **Analytical Review tab** — system-based: log each key figure's prior-
  and current-year amount (revenue, gross profit, key expense lines, etc.)
  and the app computes the variance and variance % automatically, flagging
  anything at or above an adjustable significance threshold (10% by
  default) so it doesn't get missed. Add an explanation against any
  flagged line. Carries the same Preparer/Reviewer sign-off, and
  adding/editing/removing a line or changing the threshold clears any
  stale review.
- **Risk Assessment tab** — system-based: answer a ten-question
  questionnaire (five likelihood factors like transaction complexity,
  control environment and fraud indicators; five impact factors like
  materiality magnitude, going concern and regulatory consequences) and the
  app computes likelihood, impact, a score out of 25 and an overall
  Low/Medium/High rating automatically — no manual scoring. Re-answering
  the questionnaire updates the same assessment and recalculates the
  rating. Carries the same Preparer/Reviewer sign-off as the Checklist and
  Tasks tabs (a supervisor/partner/admin other than whoever completed it),
  and re-answering clears any stale review. Risk items logged the old
  manual way before this change are kept and shown read-only underneath,
  for reference.
- **Planning tab** — a materiality calculator: enter the client's total
  revenue, profit before tax and total assets, pick which basis to use (or
  let it take the highest), and the system computes suggested overall
  materiality, performance materiality and a "clearly trivial" threshold
  automatically from adjustable % benchmarks. Also shows a suggested audit
  approach (more/less substantive testing, staffing, etc.) driven by the
  current Risk Assessment rating. These are practical starting points, not
  a substitute for professional judgement or your firm's own methodology —
  review and adjust the benchmark percentages per engagement. Carries the
  same three-tier sign-off as everywhere else; re-saving the calculation
  clears any stale review/partner sign-off.
- **Checklist tab** — tick off procedures as they're completed; progress shows
  as a bar on the dashboard and engagement list. Each item records who
  **prepared** it (auto-recorded when marked Done/N/A) and has a separate
  **"Mark as reviewed"** action for a supervisor, partner or admin to sign
  off — never the same person who prepared it. Re-opening a completed item
  clears any earlier review, since it needs a fresh look once redone.
- **Documents tab** — automatically lists the Document Templates that match
  this engagement's type (Audit/Assurance/Consulting/Secretarial) right at
  the top, ready to download with one click — no need to go find the right one in the
  separate Document Templates library. Below that, upload working papers with
  an optional filing/working-paper reference (e.g. "A-1") alongside the
  category and notes, for your own indexing; re-uploading the same filename
  and category automatically creates a new version rather than overwriting.
- **Tasks tab** — assign specific pieces of work to team members with due
  dates and priority; each person's open tasks show on their dashboard. The
  same Preparer/Reviewer sign-off as the Checklist tab applies here too: a
  task auto-records who completed it, and a supervisor/partner/admin (other
  than whoever completed it) can mark it reviewed.
- **Finalisation tab** — import the period's **preliminary** trial balance
  at planning, push audit adjustments on top of it as fieldwork progresses,
  and draft the IAS 1 financial statements from the resulting **final/
  adjusted** trial balance:
  - **Trial Balance** — download the **Neverlank_TB_Import_Template.xlsx**
    template, fill in one row per account with both the current year and
    prior year (comparative) debit/credit columns, and upload it (.xlsx or
    .csv both work) — or add accounts one at a time by hand instead. This is
    the **preliminary** trial balance — imported early (typically at
    planning) so it's available straight away for Analytical Review and
    Substantive Procedures, before any audit adjustments are proposed. Map
    each account to an IAS 1 category (property, plant and equipment,
    trade receivables, revenue, and so on) using the dropdown on each row;
    once an account is mapped, that mapping is remembered **for this
    client** and auto-applied the next time you import a trial balance for
    them, so a repeat engagement mostly maps itself. A stat row flags
    whether the current and prior year columns each balance (debits =
    credits) and how many accounts are still unmapped. Carries the same
    sign-off as the rest of the app, gated on every account being mapped
    first, and re-importing or editing a line clears any stale review/
    partner sign-off automatically (your category choices are kept and
    re-applied to matching accounts on re-import).
  - **Audit adjustments** — journal entries proposed against the
    preliminary trial balance as fieldwork/review turns up misstatements:
    give each one a reference (e.g. "AJE 1"), a description, and one or
    more debit/credit lines mapped to an IAS 1 category, the same way as
    trial balance lines. A running total flags whether each adjustment
    balances (debits = credits) before it can be reviewed or signed off.
    These affect the **current year only** — the prior year comparative is
    never touched by a current-year adjustment — and each adjustment
    carries its own full sign-off, same as any other workpaper. The
    financial statements below are always computed from the preliminary
    trial balance **plus** every adjustment proposed against it; nothing
    proposed here ever changes the preliminary trial balance itself, so
    Analytical Review and Substantive Procedures (which read the
    preliminary trial balance directly) are unaffected by adjustments made
    here.
  - **Financial statements** — once every account is mapped, the app
    computes a full set of IAS 1 primary statements live from the adjusted
    trial balance (preliminary trial balance plus every audit adjustment
    proposed against it): Statement of Financial Position, Statement of Profit or Loss
    and Other Comprehensive Income, Statement of Changes in Equity, and a
    Statement of Cash Flows (indirect method, current year only — a prior
    year comparative cash flow needs the trial balance from two years
    back). Retained earnings is treated as the standard working-trial-
    balance convention expects: the trial balance figure is the *opening*
    balance brought forward, and the app computes the closing balance
    itself (opening + profit for the year − dividends), checking that
    figure against the prior year's own computed closing and flagging any
    difference for you to investigate (dividends posted straight to
    retained earnings, a prior period adjustment, etc. are common causes).
    The cash flow statement similarly checks its computed closing cash
    position against the trial balance's actual cash balance and flags any
    gap. Investing activities are approximated as the net movement in each
    non-current asset's carrying value (so depreciation is folded into
    that net movement rather than added back separately) — review against
    the fixed asset register if there were disposals during the year. Add
    a basis of preparation note and carry the same sign-off as everywhere
    else in the app.

  As with the Risk Assessment and Planning tabs, this gives you a
  system-computed first draft built from your mapped trial balance, not a
  substitute for professional judgement, disclosure notes, or your firm's
  own review of the final financial statements.
- **Substantive Procedures tab** — system-based, organised by thirteen
  standard audit areas (Cash and Bank, Trade Receivables, Inventories,
  Property Plant and Equipment, Investments, Trade Payables and Accruals,
  Borrowings and Finance Costs, Revenue, Payroll and Employee Costs,
  Taxation, Equity and Reserves, Related Party Transactions, Going
  Concern). Click **Generate suggested procedures** and the app seeds each
  area with baseline procedures, plus extra procedures where the current
  Risk Assessment rating is **High** (more extensive testing, external
  confirmations, expert involvement, etc.) and/or the client's **industry**
  makes a particular procedure especially relevant (e.g. biological asset
  valuation for Agriculture, ECL/IFRS 9 testing for Banking, retention
  receivables for Construction). Safe to re-run any time — it only adds
  what's missing, never duplicates or removes anything, and won't disturb
  an area that's already signed off unless it actually has something new
  to add. Tick off each procedure's status, add notes, or add your own
  procedures by hand. Unlike other tabs, sign-off happens **per area**
  rather than per procedure — an area needs every procedure marked Done or
  N/A before it can be reviewed or partner-signed, same as a section of a
  paper audit file. As with every other system-based feature, treat the
  suggestions as a professionally-informed starting point, not a substitute
  for your own risk-based judgement.
- **Checklist Templates** (top menu) — build reusable audit programs once, use
  them on every new engagement of that type. Comes with 7 ready-made
  templates: Standard Statutory Audit, Full Statutory Audit Program
  (ISA-Aligned), Standard Assurance Engagement, Agreed-Upon Procedures
  Engagement, Consulting Engagement (General), Tax / CGT Advisory, and
  Forensic Audit / Investigation. Run `Add_More_Checklist_Templates.bat`
  any time to pull in newer built-in templates without affecting your
  existing clients, engagements or templates. Creating, editing or
  deleting templates is gated by the **Manage Checklist Templates**
  permission (unrestricted by default — see Team > Permissions below).
- **Document Templates** (top menu) — a library of ready-made, fillable Word
  and Excel workpapers (engagement letters, representation letters, planning
  memos, a CGT computation sheet with live formulas, findings reports, and
  more) grouped by Audit / Assurance / Consulting / Secretarial, each carrying the
  Neverlank letterhead/logo, a "File reference:" field, and a footer showing
  its template reference code (e.g. SA-01). Download a blank one, fill it in
  for the specific engagement, then upload the completed file to that
  engagement's Documents tab to keep it with the file. Admins and partners
  can also click **+ New Template** to add one, or **Edit** on any existing
  template to update its name/description or replace the master file itself
  (e.g. to tweak wording or update the letterhead) — the change applies
  immediately, with no need to wait for an app update. **Delete** removes a
  template from the library entirely. Everyone can view and download;
  editing the library is gated by the **Manage Document Templates**
  permission (admin/partner by default — see Team > Permissions below).
- **Team** — add/deactivate staff and set roles (staff, supervisor,
  partner, admin); visible to whoever has the **Manage Team Members**
  permission (admin only by default). Supervisors, partners and admins can
  review and sign off checklist items, tasks and timesheets prepared by
  others; staff cannot — this specific review/sign-off workflow is not
  affected by the Permissions screen below, it's a fixed part of how
  workpapers are reviewed everywhere in the app. Only an actual admin can
  ever grant someone the admin role, even if another role has been given
  the Manage Team Members permission.
  - **Permissions** (admin only, always) — a settings screen listing a
    handful of firm-administration capabilities (managing the Document
    Templates and Policies libraries, managing checklist templates,
    managing team members, and deleting whole clients/engagements/
    documents) with a checkbox per role. Toggle exactly what each role can
    do beyond the fixed Preparer/Reviewer/Partner sign-off workflow. Freshly
    installed or just-updated, every toggle reproduces this app's previous
    hardcoded behaviour exactly, so nothing changes until you open this
    screen and change something. Admin isn't shown as a column — it always
    has full access to everything, which can't be turned off, so a mistaken
    toggle can never lock every admin out of fixing it.
- **HR & Admin** (top menu) — a landing page for three sections:
  - **Policies and Procedures** — a firm document library (HR policies,
    firm procedures, quality control manual, IT & security, forms) that
    works just like Document Templates: everyone can view/download, editing
    is gated by the **Manage Policies & Procedures** permission (admin/
    partner by default).
  - **Time Sheets** — log hours for the week directly in the app (pick an
    engagement or mark it general/non-billable); regular vs overtime hours
    are calculated automatically (anything past 8 hours in a single day
    counts as overtime) and shown as running totals. Prefer Excel? Download
    the ready-made **Neverlank_Timesheet_Template.xlsx** template — it has
    the same live regular/overtime formulas built in — fill it in offline,
    and upload the completed file back on the same page. A supervisor,
    partner or admin approves each submitted timesheet (never their own);
    approving locks it from further edits until a reviewer reopens it.
  - **Project Management** — a firm-wide board listing every open task
    across *all* engagements in one place (not just one engagement's Tasks
    tab), filterable by staff member and status, with the same
    Preparer/Reviewer sign-off available inline.
  - **Planner** — an audit timetable showing every active engagement's key
    dates (start, period end, deadline) side by side, soonest deadline
    first, with a **Clash** badge flagging weeks where more than one
    engagement's deadline lands — plus a staffing grid (staff × week)
    showing who's booked on which engagement and at what % of their time.
    Book a staff member onto an engagement for a date range from the
    **Staffing** section on that engagement's Overview tab; it feeds
    straight into this grid, with over-100%-booked weeks flagged in red.

## Data & backups

All data lives in the `instance` folder (a `neverlank.db` SQLite file),
uploaded files in the `uploads` folder, and the editable master Document
Templates in the `document_templates_data` folder — all three sit right next
to `Start_Neverlank_App.bat` (or next to `NeverlankApp.exe`, if you built
that). To back up, copy those three folders somewhere safe (e.g. a shared
drive) periodically — there is no external database to manage.

## Hosting this online (Render)

Instead of running on one office computer, the app can also run as a real
website your team reaches from anywhere (home, phone, another office) — this
is done through [Render](https://render.com), a cloud hosting service:

1. Push this project's code to a GitHub repository.
2. In Render, choose **New > Blueprint**, point it at that repository, and it
   will read `render.yaml` in this folder and set everything up automatically
   — the web service, a persistent disk for your data, and a random secret
   key. Pick the **Starter** plan or above (needed for the persistent disk
   that keeps your database and files between deploys — Render's free plan
   doesn't support it).
3. Once deployed, Render gives you a `https://...onrender.com` address —
   that's your app's permanent web address. Log in with the default admin
   login the same as always and change the password immediately.

Behind the scenes this uses the `DATA_DIR` environment variable (set
automatically by `render.yaml`) to point the database, uploads, and Document
Templates library at the persistent disk instead of the app folder, so your
data survives every future code update/redeploy. This has no effect on the
Windows `.exe`/`.bat` way of running it described above — that still works
exactly as before.

## Notes on this being a first version

This is a working first version covering the four priorities you asked for:
engagement/client tracking, working papers, risk & checklists, and task
assignment. Natural next steps if useful later: per-client document folders
export, email reminders for approaching deadlines, and audit trail/history
logging. Let me know what you'd like added next.
