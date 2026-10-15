# Technical decisions

Short records of choices made while building, with the reason, so later contributors don't have to guess. Newest first.

## Phase 0 (October 2026)

**Django 6.1 now, Django 6.2 LTS before the pilot.** The build plan first said "5.2 LTS now." Moving from 5.2 to 6.2 crosses a major version. Moving from 6.1 to 6.2 is a single feature release, and 6.2 LTS ships in April 2027, before the summer pilot. Django 6.x also brings two things Sympraxi would otherwise need packages for: a built-in Content Security Policy with nonces, and the Tasks framework. Renovate is set not to jump to 6.2 automatically.

**Background jobs: Django's Tasks framework with the `django-tasks-db` backend.** Jobs are stored in PostgreSQL and run by `manage.py db_worker`, so a small install needs no Redis or Valkey. The jobs are written against `django.tasks`, so a different backend can be swapped in later without code changes.

**Email through `MAILERS` (new in Django 6.1).** The `default` mailer hands messages to the task worker. The `delivery` mailer talks to the campus SMTP relay. A slow relay never holds up a page.

**Shared cache in the database.** Login rate limiting needs a cache that all Gunicorn workers share. Django's database cache does that without another service. Set `CACHE_URL` to a Valkey URL if a large install needs it.

**Design system: U.S. Web Design System 3.14.** It is built and tested for accessibility, and its code is in the public domain (CC0); its fonts are under the SIL Open Font License and its icons under Apache 2.0. We use its components and styles but not the U.S. government banner or anything that implies a government site. GOV.UK Frontend was the alternative; its typeface and crown are restricted to UK government services.

**Assets are copied from npm packages, not loaded from a CDN.** A self-hosted install makes no third-party requests, which keeps the Content Security Policy at `'self'` and keeps students' browsing private.

**Server-rendered pages with htmx.** Less JavaScript, fewer accessibility traps, a smaller attack surface. htmx is configured with `allowEval` and `allowScriptTags` off and without its inline indicator styles, so it works under the strict CSP.

**Permissions in one module.** `sympraxi/core/policy.py` holds every rule. A hand-written test matrix and a URL inventory test keep it honest. Outsiders get 404 rather than 403, so URLs can't be used to discover which centers or records exist.

**Site administrators are not a back door to records.** Superusers get operational actions only (`SITE_ADMIN_GRANTS`). Actions that read student records will be granted by center role, never to site admins by default.

**The audit log is append-only in the database.** A PostgreSQL trigger rejects `UPDATE` and `DELETE` on `privacy_auditevent`. A future retention purge will set `SET LOCAL sympraxi.audit_purge = 'on'` for its own transaction. The actor and center are stored as IDs without foreign-key constraints, plus a text label, so deleting a person or center never rewrites history.

**Two-factor login required for every staff role.** Tutors, front desk, directors and site admins are sent to the 2FA setup page until they add an authenticator app or a passkey. Text-message codes are not offered. The Django admin uses the same sign-in, so the same rule applies there.

**Python 3.12 in Docker and CI**, matching development machines. Security support for 3.12 runs to October 2028.

**PostgreSQL 18 in production and CI.** The local test run on Windows used a portable PostgreSQL 16 because Docker wasn't available. Nothing in Phase 0 depends on 18.
