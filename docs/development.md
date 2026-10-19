# Development setup

## Tools

- Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/) (`pip install uv` works).
- Node.js 24 (to copy the USWDS, htmx and PDF.js files into place, bundle the shared-document editor, and run `collab/`, whose browser test needs `npm --prefix collab ci`).
- PostgreSQL 18. With Docker: `docker compose -f compose.dev.yaml up -d`. Without Docker, install it natively. The tests rely on PostgreSQL features (triggers, range types, and an exclusion constraint that needs the `btree_gist` extension), so SQLite isn't supported. `btree_gist` comes with the official Docker images and the Debian, Ubuntu and EDB Windows packages. Minimal builds, such as the one in the `pgserver` Python package, leave it out and can't run the migrations.

## First run

```bash
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
cp .env.example .env        # then set DATABASE_URL and a long random SYMPRAXI_SECRET_KEY
uv run python manage.py migrate
uv run python manage.py createcachetable
uv run python manage.py seed_demo
uv run python manage.py runserver
```

`seed_demo` creates a demo center with a director, front desk worker, tutor and student, and prints a password for them. It also loads the topic vocabulary and resource catalog from `catalog/` and gives the demo student three past sessions, two of them written up. Without the demo, load the catalog with `uv run python manage.py load_catalog`; it is safe to run again. Staff accounts are asked to set up two-factor login on first sign-in; any authenticator app works. To skip that while developing, set `SYMPRAXI_REQUIRE_STAFF_MFA=false` in `.env`.

Single sign-on is off until its variables are set (see `.env.example` and "Single sign-on" in [install.md](install.md)). Testing against a real tenant needs an app registration whose redirect URI points at `http://localhost:8000/accounts/oidc/entra/login/callback/`; the automated tests use a fake provider instead.

Emails print to the console in development. Set `SYMPRAXI_QUEUE_EMAIL=false` to send them inline, or run `uv run python manage.py db_worker` alongside `runserver` to process the queue.

`seed_demo` also gives the demo student a late cancellation and a possible no-show, and gives a second demo student, `student2@demo.test`, three no-shows and a booking block.

It also sets up a registration form and an intake form with a follow-up question (under **Forms** on the center page). The demo student answered both, and the intake form was reworded afterward, so the student's earlier answers show the wording of version 1 and the later ones version 2.

It also sets up session-report questions, answered in the demo student's two written-up records (the student sees one of the answers on My writing), and a survey with six anonymous responses this term, so **Survey results** has something to show. It prints a survey link for the demo student that opens without signing in and works once.

The demo's appointments give the pages under **Reports** something to show. A full export of the center is built by `db_worker` and saved under `media/center-exports/`; without the worker running, it waits in the queue.

## The scheduler

Reminder emails, survey invitations and possible no-show flags come from a third process, beside `runserver` and `db_worker`:

```bash
uv run python manage.py scheduler
```

It sweeps at the start of every minute until you stop it. `--once` runs one sweep and exits. It needs `SYMPRAXI_BASE_URL` in `.env` (for development, `http://localhost:8000`), because reminder and survey links are written outside a page request. Running two at once is safe: neither sends anything the other already sent.

Survey emails are sent by `db_worker` even when `SYMPRAXI_QUEUE_EMAIL=false`, because the worker makes each survey link's token at the moment it sends the email, so the link is never stored. Without `db_worker` running, invitations are recorded and their emails wait.

## Checks

```bash
uv run ruff check .
uv run ruff format .
uv run mypy sympraxi
uv run pytest                                  # unit and integration tests
uv run playwright install chromium             # once
uv run pytest -m e2e                           # axe accessibility and CSP checks in a real browser
```

The tests need `DATABASE_URL` set; they create and drop their own test database.

The booking engine's property tests (`tests/test_booking_properties.py`) run a short set of random sequences by default. Before a release, or after changing a booking rule, run the long set:

```bash
HYPOTHESIS_PROFILE=thorough uv run pytest tests/test_booking_properties.py
```

## Load test

The Phase 1 exit needs a first-Monday rush, about 500 students booking within ten minutes, with no double bookings and acceptable response times. `tests/load/` runs one against a real server and PostgreSQL:

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:5432/sympraxi_loadtest uv run python -m tests.load run
```

It takes about twelve minutes. The database's name must contain `load`, because the test drops and recreates it. Then it:

1. Migrates, collects the static files and builds a center (`tests/load/world.py`): three schedules (in person, online, and groups of four), 24 tutors on overlapping shifts, eight of them on two schedules, three front desk workers and 500 students, all already signed in.
2. Starts three web workers the way the Dockerfile runs Gunicorn (separate processes sharing one socket, each answering one request at a time), plus `db_worker` and the scheduler. Gunicorn doesn't run on Windows, so `tests/load/server.py` does this with Django's own request handler. The settings are the production ones, without HTTPS, and emails go to a dummy backend.
3. Sends the students through the real pages, 30 percent of them in the first minute: the schedules page, open times, the booking page, then the form. They want the same days and hours, some want two or three appointments, and 5 percent double-click the button. The front desk books for students and opens the day sheet the whole time.
4. Checks the database with its own SQL (`tests/load/check.py`): no tutor or student double-booked, no group over its size, no tutor cap or student limit exceeded when each booking was made, notice and horizon kept, every appointment inside its tutor's hours, and the database holding exactly the appointments people were told they had. Every refusal must have shown a message.

`report.md` in the output folder (`sympraxi-load` in the system temp folder unless you pass `--out`) gives the p50, p95 and p99 response time of each page, the refusals by message, lock waits and deadlocks from PostgreSQL's statistics, and the email queue. The run exits with 1 if any check fails, any request gets a server error or no answer, a refusal has no message, or a page's p95 is over 1 second or its p99 over 2 seconds (`--p95-ms`, `--p99-ms`).

Useful options: `--students`, `--duration` (seconds over which students arrive), `--think 0` (no pauses between pages, a harder run), `--workers`, `--sign-in 0.5` (half the students sign in with a password first) and `--seed`. `--students 20 --duration 30 --think 0.2` checks the tool itself in about two minutes. To book against a server you start yourself, such as the Docker stack, run `prepare`, start the server with the database and secret key it prints, then `run --url http://...`. `check --out <folder>` checks the database again and rewrites the report, as long as no later run has replaced the database. `tests/load/test_load_tool.py`, part of the normal test run, makes sure the checks catch what they should and that the driver still finds its way through the booking pages.

## Where things are

| Path | What |
|---|---|
| `sympraxi/core/policy.py` | Every permission rule. Start here before adding a view |
| `sympraxi/privacy/` | Audit log and each student's record of disclosures, both append-only through database triggers |
| `sympraxi/accounts/` | User model, sign-up rules, the staff two-factor gate |
| `sympraxi/accounts/sso.py` | What a single sign-on login must prove; `sso_config.py` builds the providers from the environment |
| `sympraxi/accounts/encryption.py` | Encrypts the two-factor secrets allauth stores, under a key derived from the secret key; `manage.py reencrypt_mfa_secrets` rewrites them after the key changes |
| `sympraxi/centers/` | Centers, roles, memberships |
| `sympraxi/centers/people.py` | Adding people one at a time or from a CSV file, and turning roles on and off |
| `sympraxi/staff/` | The time clock: entries, clocking in and out, directors' corrections, the midnight clock-out and timesheets |
| `sympraxi/notify/` | Private calendar feeds (`feeds.py`), and announcements and mass email (`news.py`) |
| `sympraxi/records/linkcheck.py` | The weekly link checker: the address guard, robots.txt, delays, and what each answer means |
| `sympraxi/files/` | Files on appointments: content checks, private storage, ClamAV scans, downloads |
| `sympraxi/online/` | Video rooms (signed Jitsi tokens, the join window, a center's switch) and the shared document of a session (`room.py`: tokens, saving, history, drawing) |
| `collab/` | The shared-editor service: one Node file running Hocuspocus, which loads and saves through Sympraxi |
| `frontend/src/room.js` | The shared document's script (Tiptap, Yjs, the chat), bundled by `npm run build` |
| `sympraxi/feedback/` | The written-feedback queue: submissions, claiming and assigning, overdue alerts, turnaround, and the PDF comment viewer (`viewer.py`, `static/sympraxi/pdf-comments.js`) |
| `sympraxi/scheduling/services.py` | Every booking rule: limits, notice, cutoff, groups, locks |
| `sympraxi/scheduling/availability.py` | Open times from weekly hours, exceptions, closures and tutor caps |
| `sympraxi/scheduling/timeutils.py` | Wall-clock times and daylight-saving rules |
| `sympraxi/scheduling/attendance.py` | Marking attendance, possible no-shows, waivers, late-cancellation window |
| `sympraxi/scheduling/blocks.py` | Counting no-shows and late cancellations, and setting and lifting booking blocks |
| `sympraxi/scheduling/reminders.py` | Which reminders are due, and sending each one once |
| `sympraxi/scheduling/scheduler.py` | The minute-by-minute loop behind `manage.py scheduler`; `sympraxi/healthcheck.py` checks its heartbeat |
| `sympraxi/templates/allauth/` | Sign-in and account pages restyled with USWDS |
| `sympraxi/static/sympraxi/sympraxi.js` | Sympraxi's own script, on every page (the CSP allows no inline script). Pages must work without it |
| `sympraxi/records/models.py` | Session records, amendments, writing projects, topics and resources |
| `sympraxi/records/services.py` | Saving, finalizing and amending records, and group copies |
| `sympraxi/records/migrations/0002_final_records_locked.py` | The trigger that refuses changes to final records outside an amendment |
| `sympraxi/records/catalog.py` | Loads `catalog/` into the database (`manage.py load_catalog`) |
| `sympraxi/records/suggestions.py` | Which resources are suggested for a record's topics, and catalog search |
| `sympraxi/records/brief.py` | Record status and the pre-session brief on the appointment page |
| `sympraxi/records/export.py` | One student's complete record, for a FERPA inspection request |
| `sympraxi/forms/models.py` | Forms, versions, questions and options with permanent keys, responses and answers |
| `sympraxi/forms/builder.py` | What directors do to a form: drafts, questions, moving, publishing, and the rules that keep earlier answers meaningful |
| `sympraxi/forms/answering.py` | A form version on a page: follow-up questions, the Continue step, required questions, typed answers |
| `sympraxi/forms/services.py` | Which form applies where (registration once, intake by schedule) and saving answers |
| `sympraxi/forms/display.py` | Showing answers in the wording answered, who sees which registration answers, audited reads |
| `sympraxi/forms/booking.py` | The registration and intake questions on the booking pages |
| `sympraxi/forms/migrations/0002_versions_frozen.py` | The triggers that freeze published versions and keep answers append-only |
| `sympraxi/forms/report.py` | Session-report questions on the record form: draft answers, what finalizing needs, amendments, who sees which answer |
| `sympraxi/forms/surveys.py` | Surveys: who gets one and when, the one-time link, anonymous answers, results by term |
| `sympraxi/forms/tasks.py` | The survey email, sent by the task worker, which makes the link's token |
| `sympraxi/forms/survey_views.py` | The survey page opened from the emailed link, without signing in |
| `sympraxi/forms/migrations/0004_report_answers_locked.py` | The triggers that lock report answers on final records and keep survey responses anonymous |
| `sympraxi/reports/statistics.py` | Statistics by term or dates: visits by outcome, tutor, modality and kind, first visits, topics, all counted in PostgreSQL |
| `sympraxi/reports/utilization.py` | Hours offered, booked and attended, by schedule, tutor and week |
| `sympraxi/reports/lists.py` | The front desk's appointment list and the student list with visit counts |
| `sympraxi/reports/tables.py` | Report tables and their CSV, XLSX and JSON files, with the guard against spreadsheet formulas |
| `sympraxi/reports/center_export.py` | The full-center export ZIP, and deleting it when its link expires |
| `tests/test_policy_matrix.py` | Every role against every action, written out by hand |
| `tests/test_url_inventory.py` | Fails if a new URL isn't assigned a permission |
| `tests/records_world.py` | Builders for session-record tests: topics, resources, appointments, records |
| `tests/attendance_world.py` | A no-show, a possible no-show and a booking block, for attendance page tests |
| `tests/forms_world.py` | Builders for form tests: forms from question specs, answers by label, posted form data, report answers, anonymous survey responses and survey links |
| `tests/reports_world.py` | A term of appointments with every outcome, whose report counts are written out by hand |
| `tests/sso_idp.py` | A fake Entra ID and Google for SSO tests; nothing goes over the network |
| `tests/e2e/` | Browser tests: axe accessibility and Content Security Policy |
| `tests/load/` | The first-Monday load test (`python -m tests.load run`) and its own checks |
| `deploy/` | Production Compose stack, Caddy, install, backup, restore, upgrade |

## Adding a page

1. Add an `Action` to `sympraxi/core/policy.py` if the page needs a new permission, and grant it in `ROLE_GRANTS`.
2. Add the action's row to the matrix in `tests/test_policy_matrix.py`.
3. Fetch objects with `get_for_action()` so outsiders get a 404 and members without permission get a 403.
4. Classify the URL in `tests/test_url_inventory.py`. A page opened by a secret token instead of a sign-in (a survey link) goes in `TOKEN_PAGES`, with a way to make a working token and each kind of dead one.
5. Add the page to `PAGES` in `tests/e2e/test_accessibility.py`. A form with its own kind of error message can also go in `FORMS_WITH_ERRORS`, so axe sees the page after a failed submission. A part of the page that htmx updates goes in `HTMX_SWAPS`, with a step that triggers it, a key student page in `PHONE_PAGES`, a staff page people use on phones in `STAFF_PHONE_PAGES`, and a page with a new kind of table in `REFLOW_PAGES`. Tables with more than a couple of narrow columns use `usa-table--stacked`, with a `data-label` on each cell, so they fit 320 CSS pixels.
6. No inline `style=` or `<script>` blocks. The Content Security Policy blocks them, and a test checks for them.
