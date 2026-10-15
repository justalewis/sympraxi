# Development setup

## Tools

- Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/) (`pip install uv` works).
- Node.js 24 (only to copy the USWDS and htmx files into place).
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

`seed_demo` creates a demo center with a director, front desk worker, tutor and student, and prints a password for them. Staff accounts are asked to set up two-factor login on first sign-in; any authenticator app works. To skip that while developing, set `SYMPRAXI_REQUIRE_STAFF_MFA=false` in `.env`.

Emails print to the console in development. Set `SYMPRAXI_QUEUE_EMAIL=false` to send them inline, or run `uv run python manage.py db_worker` alongside `runserver` to process the queue.

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

## Where things are

| Path | What |
|---|---|
| `sympraxi/core/policy.py` | Every permission rule. Start here before adding a view |
| `sympraxi/privacy/` | Audit log (append-only, enforced by a database trigger) |
| `sympraxi/accounts/` | User model, sign-up rules, the staff two-factor gate |
| `sympraxi/centers/` | Centers, roles, memberships |
| `sympraxi/scheduling/services.py` | Every booking rule: limits, notice, cutoff, groups, locks |
| `sympraxi/scheduling/availability.py` | Open times from weekly hours, exceptions and closures |
| `sympraxi/scheduling/timeutils.py` | Wall-clock times and daylight-saving rules |
| `sympraxi/templates/allauth/` | Sign-in and account pages restyled with USWDS |
| `tests/test_policy_matrix.py` | Every role against every action, written out by hand |
| `tests/test_url_inventory.py` | Fails if a new URL isn't assigned a permission |
| `tests/e2e/` | Browser tests: axe accessibility and Content Security Policy |
| `deploy/` | Production Compose stack, Caddy, install, backup, restore, upgrade |

## Adding a page

1. Add an `Action` to `sympraxi/core/policy.py` if the page needs a new permission, and grant it in `ROLE_GRANTS`.
2. Add the action's row to the matrix in `tests/test_policy_matrix.py`.
3. Fetch objects with `get_for_action()` so outsiders get a 404 and members without permission get a 403.
4. Classify the URL in `tests/test_url_inventory.py`.
5. Add the page to `PAGES` in `tests/e2e/test_accessibility.py`.
6. No inline `style=` or `<script>` blocks. The Content Security Policy blocks them, and a test checks for them.
