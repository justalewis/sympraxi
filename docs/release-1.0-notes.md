# Sympraxi 1.0 release notes (draft)

Not released. This is the draft to finish when the open items below are closed. Version numbers and dates are filled in at release.

## What 1.0 contains

**Scheduling.** Schedules, tutors, terms, closures, rooms, booking limits and exemptions, group and tentative appointments, online sessions with video and a shared document, a waiting list that holds a canceled time for the next person, reminders by email and, if the center turns it on, text message.

**Records.** Session records with reports, forms and registration and intake answers, writing projects, a student's writing history, files and written feedback with comments, early alerts with required reasons, visit confirmations a student chooses to share with an instructor, and exports for a student and for a whole center.

**Staff.** A time clock with approvals and locking, a check-in kiosk, payroll detail, onboarding and training, cohorts, mentors and observations, workshops with registration.

**Sign-in.** Email and password, passkeys, two-factor login (required for staff), single sign-on with Microsoft Entra ID, Google or any OpenID Connect provider, SAML 2.0, CAS, and a launch from Canvas, Moodle or D2L (LTI 1.3).

**Integration.** A read and write API with scoped tokens, and signed webhooks.

**Content.** An optional pack of open-license writing guides, with Writing Spaces essays as links with credit.

**Language.** English, and a first part of Spanish.

**Importing.** A history importer for appointment data exported from another system.

## Before you install

- Read `docs/install.md`. A college runs its own server; nothing is hosted for you.
- Send your IT office `docs/security-questionnaire.md`, `docs/accessibility.md` and `docs/security-review-brief.md`.
- Plan to register single sign-on and, if wanted, text messaging and a learning-system tool before students arrive.

## What isn't in 1.0

- SAML single logout, and SAML requests signed or encrypted by Sympraxi.
- A converter for WCONLINE exports (the importer takes a documented CSV layout, not a WCONLINE file).
- Translations beyond the first Spanish pages.
- Deep linking and grade passback in the LTI launch.
- A retention purge and legal hold.

## Open items before release

1. An outside security review (`docs/security-review-brief.md`).
2. A manual accessibility review with real assistive technology and a finished conformance report.
3. A Spanish speaker's review of the translation.
4. A second maintainer or reviewer.
5. A real-system test of single sign-on, CAS, LTI and text messages at a pilot campus.
6. The full unit and browser suites passing on the release commit.
