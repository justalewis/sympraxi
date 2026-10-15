# Sympraxi

An open-source system for college writing centers: tutor scheduling, session notes, online and asynchronous sessions, tutor hours and training, and a writing-resource library tutors can attach to sessions. It is meant as a free, self-hostable alternative to WCONLINE.

Status: Phase 0 (foundation) is scaffolded: accounts with required two-factor login for staff, centers and roles, a single permission module, an append-only audit log, accessible base pages, CI, and a Docker Compose deployment with backup and restore. Phase 1 has started with the scheduling core: terms, schedules, tutors' weekly hours, list-first booking with limits, group and repeating appointments, and confirmation emails with calendar files. Session records are built too. Tutors write a summary for the student and notes for staff, tag topics and attach resources from the starter catalog. Once a record is final, changes are kept as amendments, and students see their sessions on a My writing page.

## Try it

See [docs/development.md](docs/development.md) to run it locally and [docs/install.md](docs/install.md) to install it on a server.

## Planning documents

| Document | What it covers |
|---|---|
| [docs/01-feature-map.md](docs/01-feature-map.md) | Everything WCONLINE does, who uses it, and whether we match it, improve it, or leave it out |
| [docs/02-build-plan.md](docs/02-build-plan.md) | Goals, stack, architecture, data model, security and FERPA design, accessibility, phases, risks, and open decisions |
| [docs/decisions.md](docs/decisions.md) | Technical decisions made while building, with reasons |
| [docs/03-resource-library.md](docs/03-resource-library.md) | What we may do with Purdue OWL and openly licensed writing resources, the catalog format, and a draft topic vocabulary |

## In short

- **Stack:** Django and PostgreSQL, server-rendered pages with HTMX, a small Yjs collaboration sidecar, and optional self-hosted Jitsi for video. Runs with Docker Compose on one VM.
- **Phases:** foundation, then a release a center can run for a whole term (with Entra ID or Google sign-in), then online and asynchronous sessions, the resource library and long-term tracking, staff operations and training, and finally campus integration (SSO, LTI, API) for 1.0.
- **Records:** session notes are FERPA education records. Access is scoped by role and center, reads are logged, and students can see their own history.
- **License:** AGPL-3.0-or-later for the code; CC0 for the topic vocabulary and catalog metadata.
- **Pilot:** summer 2027, with single sign-on through Microsoft Entra ID or Google. Pilot center still to be found.

## Contributing and contact

See [CONTRIBUTING.md](CONTRIBUTING.md) (commits need a DCO sign-off) and [SECURITY.md](SECURITY.md) for reporting vulnerabilities. A contact address for the project will be added before the repository goes public.
