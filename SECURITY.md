# Security policy

Sympraxi stores student education records, so we treat security reports as urgent.

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's private vulnerability reporting ("Report a vulnerability" on the repository's Security tab). Until the repository is public, contact the maintainer directly.

Include what you found, how to reproduce it, and what an attacker could do with it. Don't access, change, or keep any real student data while testing; use a local install with demo data (`manage.py seed_demo`).

## What to expect

| Step | Target |
|---|---|
| Acknowledgment | 3 business days |
| Initial assessment | 10 business days |
| Fix for a critical issue | As fast as possible, with a patch release and an advisory |

We credit reporters in the advisory unless you ask us not to.

## Supported versions

Before 1.0, only the latest release gets security fixes. From 1.0 on, each long-term-support release gets security fixes for two years (see `docs/02-build-plan.md`, section 10).

## For colleges running Sympraxi

- Apply patch releases promptly; they never contain schema-breaking changes.
- Require multi-factor login for staff (on by default: `SYMPRAXI_REQUIRE_STAFF_MFA`).
- Back up nightly and test a restore every term (`deploy/backup.sh`, `deploy/restore.sh`).
