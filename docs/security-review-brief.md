# Security review brief

For an outside reviewer of Sympraxi before a 1.0 release. It says what the software does, where the sensitive parts are, what has been checked, and what hasn't. It describes the branch as of October 21, 2026. Nobody outside the project has reviewed the code yet.

## What it is

A web application for college writing centers: scheduling, session records, forms, files, an online-session shared editor, a staff time clock, workshops, training, early alerts, text reminders, an API and webhooks. Django 6.1 and PostgreSQL 18, htmx and USWDS in the browser, a worker process, a small collaboration sidecar and Caddy. The college runs it on its own server; the project runs no service and holds no data. AGPL-3.0-or-later.

## What to look at first

| Area | Why it matters | Where |
|---|---|---|
| Central permission rules | Every page is checked against one table of actions and roles; a bug here exposes student records | `sympraxi/core/policy.py`; `tests/test_policy*.py`, `tests/test_url_inventory.py` (every URL must be classified) |
| Sign-in paths | Six ways in: password, passkey, Entra/Google/generic OIDC, SAML, CAS, LTI | `sympraxi/accounts/`, `sympraxi/lti/` |
| The LTI launch | Trusts a signed token from another site to sign a person in; CSRF is exempt on purpose | `sympraxi/lti/launch.py`, `sympraxi/lti/views.py` |
| The API and webhooks | Bearer tokens act as a director; webhooks make server-side requests to addresses a director enters | `sympraxi/integrations/`; address vetting in `sympraxi/records/linkcheck.py` |
| Text messages | A provider webhook open to the internet, checked only by signature | `sympraxi/sms/` |
| Shared visit links | Unauthenticated pages reached by a random token | `sympraxi/alerts/public_urls.py` |
| Uploaded files | Type checks, optional virus scan, download headers | `sympraxi/files/`, `sympraxi/feedback/` |
| Anonymous surveys | Anonymity depends on what is and isn't stored | `sympraxi/forms/` |
| The audit log | Append-only by database trigger | `sympraxi/privacy/` |
| Encryption of secrets | Two-factor secrets, webhook secrets and phone numbers are encrypted under a key derived from `SECRET_KEY` | `sympraxi/accounts/encryption.py` |
| Content security policy | Strict; no inline script | `sympraxi/settings.py` (CSP) |

## What has been checked

- Unit and integration tests (about 1,600 in the last targeted runs; the full unit suite takes about an hour), including a role-by-page permission matrix and a check that every URL is classified.
- A browser suite of about 570 tests with axe accessibility and CSP checks.
- Static checks in CI: ruff, mypy, and dependency review through Renovate.
- Two earlier self-reviews, recorded in `docs/decisions.md` ("Fixes from the security and accessibility drafts").
- Written by one maintainer with an AI coding assistant. No second human has reviewed the changes.

## What has not been checked

- No penetration test, no DAST scan, no fuzzing.
- No real identity provider, CAS server, learning system, SMS provider or webhook receiver has been used; each is tested against a stand-in written in this repository, so a mismatch with the real service wouldn't show.
- The Docker images, Caddy routes, backups and restore scripts haven't been run in this environment.
- Load testing was done once, before the later features.
- SAML (`sympraxi/accounts/saml.py`) is new and has only been tried against a stand-in provider that signs real responses; single logout isn't implemented.

## Known gaps

The numbered gaps at the end of `docs/security-questionnaire.md`, plus: no idle session timeout; the audit trigger can be bypassed by the database owner; the API's per-token limit (600 a minute) is the only API throttle; webhook and SMS delivery depend on a worker being up.

## What would help most

1. A review of the permission policy and the URL inventory test for anything classified too loosely.
2. An attempt to forge or replay an LTI launch, a CAS ticket, an SMS webhook, an API token and a shared visit link.
3. A look at server-side request forgery through webhooks and the link checker, including DNS rebinding.
4. A look at file upload and download handling.
5. Whether the survey anonymity claims in `docs/install.md` hold.

Report findings as `SECURITY.md` describes.
