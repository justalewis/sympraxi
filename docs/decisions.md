# Technical decisions

Short records of choices made while building, with the reason, so later contributors don't have to guess. Newest first.

## Phase 1: single sign-on (October 2026)

**One Entra tenant and one Google domain per installation, set in the environment.** The build plan had each center entering its own tenant's settings. An installation serves one college, though, and a college has one tenant or one Workspace domain. Reading providers from `SYMPRAXI_ENTRA_*` and `SYMPRAXI_GOOGLE_*` variables keeps client secrets out of the database and its backups. allauth's admin screens for apps and tokens are removed, and an app saved in the database is ignored. A provider with only some of its variables set stops the site from starting, rather than quietly leaving SSO off.

**allauth's generic OpenID Connect provider for both.** allauth's Microsoft provider reads the Graph profile, whose `mail` field isn't verified and which carries no tenant or `amr` claim. The generic provider reads the ID token, which has everything the checks need. Userinfo isn't fetched, PKCE is on, and tokens aren't stored. The sign-in URLs are `/accounts/oidc/entra/...` and `/accounts/oidc/google/...`.

**The tenant is pinned three ways.** The discovery document is the tenant's own (`/{tenant}/v2.0`), so allauth rejects ID tokens from any other issuer. `common`, `organizations`, `consumers` and anything that isn't a GUID are refused at startup. `sympraxi/accounts/sso.py` also checks `tid` and `iss` on every sign-in.

**Only addresses the provider vouches for.** Entra's `email` claim is unverified and editable, the hole behind the 2023 "nOAuth" account takeovers. Sympraxi requires Entra's `xms_edov` claim (the address is in a domain the tenant verified), and Google's `email_verified` plus a matching `hd`. When `SYMPRAXI_SIGNUP_EMAIL_DOMAINS` is set, the address must be in it. The checks run on every sign-in, so an account that stops passing them can't keep using SSO.

**Linking by address, with two limits.** A sign-in that passes the checks is linked to an existing account with the same address. After that, sign-ins match the provider's subject ID, so a later change of address doesn't break the link. An account already linked to a different identity at the same provider is never linked again by address: colleges reuse addresses, and a new holder must not inherit the old holder's records. allauth removes the password of a matched account whose address was never verified, so whoever registered it first loses access. Links, unlinks and refused sign-ins are audited (`auth.sso_linked`, `auth.sso_unlinked`, `auth.login_failed` with a reason).

**SSO doesn't skip the staff 2FA rule unless the token proves MFA.** By default allauth's two-factor step runs after SSO like after a password, and staff without a second factor are sent to set one up. `SYMPRAXI_TRUST_IDP_MFA` accepts the provider's MFA only when the ID token's `amr` claim contains `mfa`. Nothing else counts, including Google's `hwk` and `swk`, whose meaning as multi-factor Google doesn't define. Entra sends `amr` once it is added as an optional claim. Google began sending `amr` in June 2026, on request, and only to apps it has verified, so an Internal Workspace app may never receive it; those installations keep Sympraxi's own 2FA.

**SSO-only is enforced at sign-in, not in the template.** `SYMPRAXI_SSO_ONLY` hides the password form, passkey button and sign-up link. `AccountAdapter.pre_login()` refuses every non-SSO sign-in except a site administrator's, from any page. Site administrators keep a break-glass password page at `/accounts/login/local/`, with two-factor login as usual. Without a provider, the setting is refused at startup and ignored at runtime, so it can't lock everyone out.

**SSO buttons are forms, and form-action allows the provider.** Starting sign-in takes a POST, so a link on another site can't start one. Browsers apply the Content Security Policy's `form-action` to the redirect that follows a form, so the policy adds `https://login.microsoftonline.com` or `https://accounts.google.com`, only for providers that are set up. A browser test presses each button from the keyboard and checks that the browser then requests the provider's page, which it does only if the policy allows the redirect. The test browser resolves no host but localhost, so that request, like any other a page might make, never leaves the machine. Playwright can't intercept a request made while following a redirect, so blocking name lookups is the only dependable guard.

**Plain text buttons, no logos.** "Sign in with Microsoft" and "Sign in with Google" use the standard USWDS button. Vendor logos would put trademarked artwork in the repository for no gain in clarity.

## Phase 1: scheduling core (October 2026)

**No double-booking, enforced by PostgreSQL.** An exclusion constraint (`scheduling_appointment_no_tutor_overlap`) rejects two active appointments for the same tutor whose times overlap, on any schedule in any center. The `=` on the tutor column needs the `btree_gist` extension, which the first scheduling migration creates. It is a trusted extension, so the database owner can create it without superuser rights. The booking service checks first so people get a readable message; the constraint catches the race that check can't.

**The break after an appointment is stored on the row.** Each appointment keeps `blocked_until`, its end plus the schedule's buffer at booking time, and the constraint covers `[starts_at, blocked_until)`. The constraint can't add the buffer itself: adding an interval to a `timestamptz` isn't an immutable expression, so PostgreSQL won't index it. Changing a schedule's buffer later leaves existing appointments as they are.

**Row locks in a fixed order.** Booking, changing and canceling each run in one transaction and lock rows in the same order: the students' memberships (their limits and their own calendars), then the tutor assignments by primary key (caps and group seats), then the appointment. Two students racing for the last seat in a group, or one student booking twice at once, can't both get through. The locks use `select_for_update(of=("self",))`, so joined rows such as the center stay unlocked and bookings across a center don't queue behind each other.

**Wall-clock times on daylight-saving nights.** Weekly hours and opening hours are wall-clock times in the center's zone. A wall-clock time stands for the first instant at which the clock reads that time or later. On the night clocks go back, 1:30 means the first 1:30; on the night they go forward, 2:30 means 3:00. Slots step in real time from the opening instant, so the repeated hour gets its own slots and every appointment lasts as long as it says. A weekly series keeps its wall-clock time each week. Notice, horizon and cutoff are measured in real time.

**Limits count by the appointment's start, in local time.** A day is a local calendar day (23 or 25 hours on switch days), a week runs Monday to Sunday, and a term is the schedule's term dates. Canceled appointments and placeholders don't count. When "count appointments on every schedule" is checked, a schedule's limits count the student's appointments on all of the center's schedules; each schedule's own limits apply when booking on it.

**Exemptions cover appointments, not booking days.** An exemption lifts a student's count and minute limits for appointments on or before its last date, so the last day of an exemption can't be used to book a month ahead. Notice, horizon and cutoff still apply. Every exemption has an end date. Adding or ending one is audited, without the note.

**Staff book under different rules.** Front desk and directors aren't held to minimum notice, the horizon or the change cutoff, so they can enter walk-ins and fix mistakes. They are held to student limits and tutor caps unless they check "book even if it goes over," which the audit log records. Nobody can book outside a tutor's hours or over another appointment.

**Students move their own one-to-one appointments; staff move groups.** Until the change cutoff, a student who is the only person in an appointment can move it to another open time on the same schedule. The move keeps the appointment's row and calendar UID, raises its `SEQUENCE`, and re-checks every booking rule while leaving the appointment itself out of the counts. Moving to another schedule means canceling and booking again. Only staff move group appointments, because a move changes the time for everyone in the group. Moving has its own policy action, `scheduling.change_appointment`, granted like canceling.

**Grants that reach only a person's own records.** Students can view, cancel and move only the appointments they are in, and tutors can view only the appointments they teach. These limits are `OBJECT_RULES` in `policy.py`, beside the role grants, and `tests/test_policy_matrix.py` lists them by hand. Someone else's appointment returns 404, as it does for an outsider, so appointment IDs can't be probed. Students never see other students: open times carry no names, and only staff and the tutor see who is in a group.

**Site administrators get no scheduling actions.** Appointments are student records, so the Phase 0 rule holds: they are reached through a center role, not through `is_superuser`.

**Two statuses and three flags.** An appointment is scheduled or canceled. Walk-in, placeholder and tentative are flags on a scheduled appointment, and a tentative booking holds its time until staff confirm or cancel it. The constraint ignores only canceled appointments, so a status added later that still holds the time (completed, missed) is covered without a migration.

**Extra hours stay inside opening hours.** A tutor's extra hours can add time on a day they don't usually work, but never past the schedule's opening hours. A center that stays open late for finals changes the schedule's hours.

**Appointment emails say as little as possible.** A student's email names their tutor, the time and the place. A tutor's email says a student booked and links to the appointment, without the student's name. Mail goes out after the transaction commits, so a booking that is rolled back sends nothing. `SYMPRAXI_BASE_URL` sets the links in mail sent outside a page request, such as the reminders still to come.

**Calendar files are written by hand.** An appointment needs about a dozen iCalendar properties, and the rules that matter (escaping, CRLF line endings, folding at 75 octets) fit in one short module, so there is no `icalendar` dependency. Each appointment keeps one UID. Bookings and changes go out as `METHOD:PUBLISH` with a rising `SEQUENCE`, cancellations as `METHOD:CANCEL`. Calendar apps vary in how they handle these, so the email text is the record. The queued mailer now carries attachments, as text or as base64 for binary files.

**Property-based tests with Hypothesis.** A state machine books, cancels and moves appointments at random on two schedules that share a tutor, across a daylight-saving week, and checks the tables after every step: no tutor or student is double-booked and no limit, cap or group size is exceeded. The checks are computed independently of the booking code. The default run plays the same sequences every time, so CI is repeatable, and it fails if those sequences never book, cancel, move or reach a limit, so it can't pass by being refused everything. `HYPOTHESIS_PROFILE=thorough` plays about twelve times as many, chosen at random. A separate test races real threads in committed transactions.

**PostgreSQL 18 for local tests on Windows.** The pgserver package's PostgreSQL has no contrib extensions, so it can't create `btree_gist`. The local runs for this phase used the EDB PostgreSQL 18.6 binaries zip, started from a scratch folder on its own port. The `postgres` Docker images and the Debian and Ubuntu packages include `btree_gist`.

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
