# Neverlank Audit, Assurance & Consulting App

An internal web app for tracking audit, assurance and consulting engagements:
clients, engagements, working papers/documents, risk assessments & checklists,
and team task assignment.

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

- **Clients** — add each audit/assurance/consulting client once.
- **Engagements** — create one per assignment (e.g. "FY2026 Statutory Audit"),
  set its type, status, partner/manager, deadline, and team members. You can
  start it from a checklist template to pre-populate audit procedures.
- **Checklist tab** — tick off procedures as they're completed; progress shows
  as a bar on the dashboard and engagement list.
- **Risk Assessment tab** — log risks with likelihood x impact scoring
  (auto-rated Low/Medium/High) and mitigation/response notes.
- **Documents tab** — automatically lists the Document Templates that match
  this engagement's type (Audit/Assurance/Consulting) right at the top, ready
  to download with one click — no need to go find the right one in the
  separate Document Templates library. Below that, upload working papers;
  re-uploading the same filename and category automatically creates a new
  version rather than overwriting.
- **Tasks tab** — assign specific pieces of work to team members with due
  dates and priority; each person's open tasks show on their dashboard.
- **Checklist Templates** (top menu) — build reusable audit programs once, use
  them on every new engagement of that type. Comes with 7 ready-made
  templates: Standard Statutory Audit, Full Statutory Audit Program
  (ISA-Aligned), Standard Assurance Engagement, Agreed-Upon Procedures
  Engagement, Consulting Engagement (General), Tax / CGT Advisory, and
  Forensic Audit / Investigation. Run `Add_More_Checklist_Templates.bat`
  any time to pull in newer built-in templates without affecting your
  existing clients, engagements or templates.
- **Document Templates** (top menu) — a library of ready-made, fillable Word
  and Excel workpapers (engagement letters, representation letters, planning
  memos, a CGT computation sheet with live formulas, findings reports, and
  more) grouped by Audit / Assurance / Consulting, each carrying the
  Neverlank letterhead/logo, a "File reference:" field, and a footer showing
  its template reference code (e.g. SA-01). Download a blank one, fill it in
  for the specific engagement, then upload the completed file to that
  engagement's Documents tab to keep it with the file. Admins and partners
  can also click **+ New Template** to add one, or **Edit** on any existing
  template to update its name/description or replace the master file itself
  (e.g. to tweak wording or update the letterhead) — the change applies
  immediately, with no need to wait for an app update. **Delete** removes a
  template from the library entirely. Staff can view and download but not
  edit the library.
- **Team** (admin only) — add/deactivate staff and set roles (staff, partner,
  admin).

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
