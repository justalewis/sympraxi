# Security questionnaire (draft HECVAT answers)

This is the draft answer set a pilot campus's IT office can review before student data goes into Sympraxi. It follows the Higher Education Community Vendor Assessment Toolkit (HECVAT) and answers each question from the code and docs as they stand, with the file that shows it. Phase 1 of [02-build-plan.md](02-build-plan.md) calls for it. Its companion for accessibility is [accessibility.md](accessibility.md).

Drafted on October 16 and 17, 2026. The answers describe branch `phase-0-foundation` at merge `8490575`, which includes the session-report questions and surveys, statistics and the full-center export, and the load-test fixes to sign-in limits, web workers and booking. Sympraxi has no release yet and runs nowhere in production.

## Which HECVAT

EDUCAUSE publishes HECVAT 4. The current file on its site is version 4.1.6 (`hecvat416.xlsx`, last changed August 27, 2026). HECVAT 4 rolled the old Full, Lite and On-Premise questionnaires into one workbook. A provider answers every section that its answers on the Start Here tab call for. A "Lite" review now means an institution scores only the critical questions, which the workbook marks with an asterisk. This draft marks them the same way (`AAAI-01*`).

The question IDs below come from the 4.1.6 workbook itself. EDUCAUSE's public "HECVAT 4 Issue Tracker/3 to 4 Migration Guide", linked from the HECVAT page, lists the same IDs for the sections carried over from HECVAT 3, but not the On-Premises, AI and Privacy questions HECVAT 4 added. The topics are paraphrased here; EDUCAUSE holds the copyright on the wording and lets institutions and providers use the workbook at no cost.

The finished answers belong in the Excel workbook, which EDUCAUSE says to share as Excel only. This file is the source to copy from. The workbook is not in the repository.

## Who answers for what

In HECVAT's terms, the "solution provider" is the Sympraxi project: it writes and publishes the software. The college runs every server. Nobody in the project operates an installation, receives student data, or holds an account on a college's installation (02-build-plan.md §6.1 item 9). Many HECVAT questions assume a vendor runs a hosted service. Here those become **Campus responsibility**, and the answer says what the campus has to do.

The project is one maintainer, Justin, building with Claude Code, an AI coding assistant (02-build-plan.md §9 and §13). There is no company, no contract and no warranty: the AGPL disclaims both warranty and liability (`LICENSE`, sections 15 and 16).

## Answer key

| Answer | Meaning |
|---|---|
| Yes | In place now. The evidence column says where. |
| Partly | Some of it is in place; the note says what is missing. In the workbook, enter No and put the note in Additional Information. |
| No | Not in place and not planned yet. |
| Not yet (Phase N) | Not in place; planned for the phase named. "Not scheduled" means no phase in the build plan covers it yet. |
| Campus responsibility | The college does this on its own installation. The note says what Sympraxi provides to help. |
| Not applicable | The question is about something Sympraxi doesn't have, such as a mobile app. |

Paths are relative to the repository root.

## Start Here

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| GNRL-01 | Provider name | Sympraxi project | Open source, AGPL-3.0-or-later (`LICENSE`). No legal entity yet |
| GNRL-02 | Solution name | Sympraxi | |
| GNRL-03 | Description | | Scheduling, session records and tutor support for college writing centers, self-hosted by the college. See `README.md` |
| GNRL-04 to GNRL-07 | Provider contact | Not yet (before the repository goes public) | `README.md` says a project contact address comes before the repository goes public. Until then, the pilot campus deals with the maintainer directly |
| GNRL-08 | Country of headquarters | To fill in | The maintainer's country |
| GNRL-09 | Where employees work | Not applicable | No employees. The maintainer and any contributors work remotely, and none of them has access to an installation |
| COMP-01* | Dedicated development team | No | One developer working with an AI coding assistant. The build plan names this as the project's first risk (02-build-plan.md §12) and plans a second maintainer before 1.0 |
| COMP-02 | Background and ownership | | An open-source project with no owner company. Copyright stays with each contributor under the DCO (`CONTRIBUTING.md`), so no one party can relicense it. A consortium or fiscal sponsor is the long-term plan (02-build-plan.md §11) |
| COMP-03 | No unplanned outages in the past 12 months | Not yet (pilot, summer 2027) | Sympraxi isn't in production anywhere yet |
| COMP-04 | Dedicated security staff | No | |
| COMP-05 | Notes for assessors | | Sympraxi is self-hosted: the college's server holds every record, and the project never receives any. This document and 02-build-plan.md §6 describe the design |
| REQU-01 | A product or platform, not only a service | Yes | Software the college installs (`docs/install.md`). So the Product and Infrastructure sections apply |
| REQU-02 | Has a user interface | Yes | Web pages. So the IT Accessibility section applies; see [accessibility.md](accessibility.md) |
| REQU-03 | Consulting services | No | So the Consulting section doesn't apply |
| REQU-04 | AI features now or within 12 months | No | AI features are out of the first release; if they come later they must be opt-in and run on infrastructure the college controls (02-build-plan.md §1). So the AI section doesn't apply |
| REQU-05 | Processes HIPAA data | No | Session notes are education records under FERPA (02-build-plan.md §6.1), which HIPAA's definition of protected health information excludes. Directors write their own intake questions, so a center should not ask for health details |
| REQU-06 | Processes payment cards | No | Payments are out of scope (02-build-plan.md §1). So the PCI DSS section doesn't apply |
| REQU-07 | Runs in the institution's environment, or needs inbound access for the provider | Yes, in the institution's environment; no inbound access | The college runs it on its own VM with Docker Compose (`deploy/compose.yaml`). Nobody outside the college needs access to it. So the On-Premises questions apply (under "Case-specific" below) |
| REQU-08 | Has access to personal or institutional data | Yes | Student names, email addresses, appointments, intake answers and session notes. So the Privacy section applies |

## Organization

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| DOCU-01* | Business continuity plan, tested yearly | No | For the project, the continuity risk is its single maintainer (02-build-plan.md §12). Installed copies keep running without the project: nothing calls home or depends on a project server, and the AGPL source stays with the college. For an installation, continuity planning is Campus responsibility |
| DOCU-02* | Disaster recovery plan, tested yearly | Campus responsibility | Sympraxi provides `deploy/backup.sh` and `deploy/restore.sh`, and `docs/install.md` says to test a restore every term on a spare VM. CI backs up, wipes and restores the real production stack on every push and checks that the data and the audit log's trigger came back (`deploy/smoke-test.sh`, the `docker` job in `.github/workflows/ci.yml`). The campus writes and tests its own plan |
| DOCU-03 | SOC 2 audit | No | The project runs no service to audit. The hosting controls an auditor would look at are the campus's |
| DOCU-04 | Follows a named security framework | No | Controls aren't mapped to NIST CSF, CIS Controls or ISO 27001. Not scheduled |
| DOCU-05 | Architecture and data-flow diagrams | Yes | 02-build-plan.md §4 (services and connections) and §5 (data model); "What's running" in `docs/install.md` |
| DOCU-06 | Data privacy policy | No | The project collects no data, so it has no privacy policy of its own. The FERPA design rules are in 02-build-plan.md §6.1. The privacy notice students see is the college's (Campus responsibility); Sympraxi doesn't show one yet |
| DOCU-07 | Onboarding and offboarding policy | Campus responsibility | No project staff. In the app, removing a membership ends a person's role in a center and is audited (`access.membership_removed` in `sympraxi/privacy/audit.py`). With `SYMPRAXI_SSO_ONLY`, turning off a person's college account ends their access (`docs/install.md`, "Single sign-on only") |
| THRD-01* to THRD-04* | Assessing and contracting with third parties that get data | Not applicable for the project; Campus responsibility for the installation | The project shares no data with anyone, since it holds none. An installation sends data only to services the campus chooses: its SMTP relay, its identity provider and, if set up, an off-site backup store. Pages load nothing from other hosts (decisions.md, Phase 0: assets are copied from npm, not a CDN). The campus vets those services and their contracts (02-build-plan.md §6.1 item 9) |
| THRD-05 | Hardware supply chain | Campus responsibility | No hardware |
| CHNG-01* | Notice of major changes | Not yet (first release, before the pilot) | Planned: release notes that flag schema changes (02-build-plan.md §10) and GitHub security advisories (`SECURITY.md`). No release, announcement list or advisory has been published yet |
| CHNG-02* | Customizations survive releases | Yes | Settings are environment variables in `deploy/.env`. Forms, schedules and center settings are data, carried forward by migrations. A campus that changes the code carries its own changes and must offer its source (`SYMPRAXI_SOURCE_CODE_URL`, `docs/install.md`) |
| CHNG-03* | Configuration management | Yes | Every installation builds the same image from `Dockerfile` with locked dependencies (`uv.lock`, `frontend/package-lock.json`) and reads all settings from `deploy/.env`. Left unset, settings fall back to the strict production values, and a test checks them (`test_production_defaults_are_strict` in `tests/test_security.py`). The host's own configuration is the campus's |
| CHNG-04 | Documented change management | Partly | Every change goes through Git and CI: lint, type checks, a missing-migration check, unit and browser tests, dependency audits, an image scan and a production-stack smoke test (`.github/workflows/ci.yml`). Design decisions are recorded in `docs/decisions.md`. There is no written procedure, and with one maintainer nobody else reviews a change |
| CHNG-05 | Authorization, impact analysis and testing before production | Partly | Testing and validation as in CHNG-04. Authorization is one person. On the campus side, `deploy/upgrade.sh` backs up before it migrates |
| CHNG-06 | Dependencies still supported at each major change | Partly | `renovate.json` proposes updates weekly and security fixes at once; pip-audit and npm audit run in CI. The stack was chosen for long support (decisions.md, Phase 0: Django 6.2 LTS before the pilot, Python 3.12 supported to October 2028, PostgreSQL 18). No per-release checklist |
| CHNG-07 | Critical patch policy | Partly | `SECURITY.md` targets: acknowledgment in 3 business days, assessment in 10, a patch release and advisory for a critical issue as fast as possible. Renovate raises vulnerability fixes at any hour. Applying patch releases is Campus responsibility (`SECURITY.md`, "For colleges running Sympraxi"). No written severity levels or deadlines |
| CHNG-08 | Mitigating risk until a patch lands | No | Not scheduled. Belongs in the incident response plan (HFIH-01) |
| CHNG-09 | Customer can postpone an upgrade | Yes | Nothing updates by itself. The campus runs `deploy/upgrade.sh` when it chooses, and `docs/install.md` advises against schema-changing upgrades during a term |
| CHNG-10 | How many versions are supported | Partly | Before 1.0, only the latest release gets security fixes; from 1.0, each long-term-support release gets them for two years (`SECURITY.md`). No release yet |
| CHNG-11 | Release schedule | Yes | Feature releases in late May and late December, security patches any time (02-build-plan.md §10). None has shipped yet |
| CHNG-12 | Two-year roadmap | Partly | 02-build-plan.md §9 runs through 1.0 in late 2027, about 14 months. Nothing is written past 1.0 |
| CHNG-13 | Updates without the institution | No | By design: the campus decides when to upgrade (CHNG-09) |
| CHNG-14 | Upgrades off-peak | Campus responsibility | The campus picks the time. `upgrade.sh` stops the scheduler while migrations run (decisions.md, Phase 1 attendance) |
| CHNG-15 | Emergency changes documented | No | Not scheduled |
| CHNG-16 | Management of servers and devices | Campus responsibility | |
| PPPR-01* | Documented patch management | Partly | Project side as in CHNG-07. Rebuilding the image picks up Debian security fixes (`apt-get upgrade` in `Dockerfile`), and CI fails if Trivy finds a fixable high or critical vulnerability in it. Patching the host and applying releases is Campus responsibility. No written process |
| PPPR-02* | Can follow the institution's privacy policies | Yes | The college runs it and sets the choices: who can register (`SYMPRAXI_SIGNUP_EMAIL_DOMAINS`), whether students see staff notes (`Center.students_see_staff_notes`), whether summary emails carry text (`Center.summary_email_includes_text`), and how long backups are kept (`deploy/backup.env`). Record retention settings aren't built yet (DRPV-06) |
| PPPR-03* | Subject to the institution's laws | Campus responsibility | The project holds no data and signs no contract. The college, as the holder of the records, answers to FERPA and its state's law; Sympraxi is built to FERPA's rules (02-build-plan.md §6.1) |
| PPPR-04 | Encryption with open standards | Yes | TLS through Caddy (`deploy/Caddyfile`); Argon2 password hashes (`PASSWORD_HASHERS` in `sympraxi/settings.py`); restic's encryption for off-site backups (`deploy/backup.sh`) |
| PPPR-05 | Documented development life cycle | Partly | Phases with exit criteria (02-build-plan.md §9), the rules in `CONTRIBUTING.md`, the checks in `docs/development.md`. Not a formal SDLC document |
| PPPR-06 | Background checks | No | No employees. Contributors aren't screened, and none of them gets access to an installation or its data |
| PPPR-07 | New staff sign agreements | Partly | Contributors certify the DCO on every commit, and CI rejects pull requests without it (`CONTRIBUTING.md`, the `dco` job in `.github/workflows/ci.yml`) |
| PPPR-08 | Information security policy | No | `SECURITY.md` covers reporting vulnerabilities only. Not scheduled |
| PPPR-09 | Security designed into the life cycle | Yes | The permission module came before any feature (decisions.md, Phase 0). A new URL fails the tests until it is assigned a permission, and a new policy action fails them until its row in the hand-written matrix is filled in (`CONTRIBUTING.md`, rules for code that touches student data). Threats and controls: 02-build-plan.md §6.2 |
| PPPR-10 | Follows breach-notification laws | Campus responsibility | The college holds the data and notifies under FERPA and state law. The project can't see any installation, so it can't detect a breach; its part is a patch and an advisory when a vulnerability is reported (`SECURITY.md`) |
| PPPR-11, PPPR-12 | Security awareness training | No | No staff |
| PPPR-13 | Reviews of privileged access | Campus responsibility | Site administrators are listed in the Django admin and directors on each center's members page. Every role change is audited (`access.membership_added`, `_changed`, `_removed`). The build plan's director-visible access reports (02-build-plan.md §6.2) aren't built: Not scheduled |
| PPPR-14 | Internal audit | No | |
| PPPR-15 | Physical security | Campus responsibility | |

## Product

### Authentication, authorization and accounting

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| AAAI-01* | Single sign-on | Yes | OpenID Connect with Microsoft Entra ID and Google Workspace (`sympraxi/accounts/sso.py`, `sympraxi/accounts/sso_config.py`, `sympraxi/accounts/adapter.py`; `tests/test_sso.py`; "Single sign-on" in `docs/install.md`). Every sign-in checks the tenant (`tid`, `iss`) or hosted domain (`hd`) and a verified address (`xms_edov`, `email_verified`). Tested against a fake identity provider only (`tests/sso_idp.py`), not yet a real tenant |
| AAAI-02* | Local accounts | Yes | Email and password with required email verification (allauth; `ACCOUNT_EMAIL_VERIFICATION` in `sympraxi/settings.py`). `SYMPRAXI_SSO_ONLY` turns them off for everyone but site administrators, who keep a break-glass page at `/accounts/login/local/` |
| AAAI-03* | Enforces the institution's password rules | Partly | Defaults: at least 12 characters, not a common password, not too like the account's details, not all digits (`AUTH_PASSWORD_VALIDATORS` in `sympraxi/settings.py`). Changing them means editing that file; there is no environment setting for it. With SSO-only, the identity provider's rules apply |
| AAAI-04* | Limits on password length or characters | No | Sympraxi adds no limit beyond the minimums in AAAI-03 |
| AAAI-05* | Password reset procedure | Yes | allauth's emailed reset link, limited by allauth's default rate limits (5 a minute per address, 20 per IP address). Nobody can see a password. With SSO, the college's own reset applies |
| AAAI-06* | InCommon or eduGAIN member | Campus responsibility | Each installation is its own service provider, so joining a federation would be the college's step. It needs SAML, which is Not yet (Phase 5) |
| AAAI-07* | Hard-coded passwords | No | `deploy/install.sh` generates the secret key and database password for each installation, and Sympraxi won't start without a secret key. The only fixed values are test values and a key used once at build time to collect static files (`Dockerfile`), never when serving |
| AAAI-08* | Passwords stored in plain text | No | Argon2 hashes (`PASSWORD_HASHERS` in `sympraxi/settings.py`; `test_passwords_are_hashed_with_argon2_in_production` in `tests/test_accounts.py`). Service credentials (database password, SMTP password, SSO client secrets) are in `deploy/.env`, which `install.sh` makes readable by its owner only; protecting the server is Campus responsibility. Authenticator-app (TOTP) secrets are stored unencrypted in the database, which is allauth's default (see the gaps list) |
| AAAI-09* | Audit log of sign-in, sign-out, actions and IP address | Yes | `AuditEvent` stores who, what, which object, when, IP address and browser (`sympraxi/privacy/models.py`). Signals record sign-ins, sign-outs, failed sign-ins, password and email changes, and two-factor changes (`sympraxi/privacy/signals.py`). Views record bookings, cancellations, attendance, record reads and edits, exports, form answers read, and role changes (`sympraxi/privacy/audit.py`). Behind Caddy, the address comes from the last `X-Forwarded-For` entry (`client_ip()`; `tests/test_audit.py`) |
| AAAI-10* | What is logged, how, and SIEM use | Partly | Security events as in AAAI-09, including refused SSO sign-ins with the reason (decisions.md, Phase 1 SSO). Directors read their center's log at `/centers/<slug>/audit/`, and viewing it is itself logged; site administrators read every entry in the Django admin, which can't add, change or delete them (`sympraxi/privacy/admin.py`). Gunicorn and Django write to standard output, with survey-link tokens removed (`sympraxi/core/logs.py`, `sympraxi/core/access_log.py`). There is no SIEM export: the campus can ship container logs with a Docker logging driver and query the audit table (Campus responsibility) |
| AAAI-11* | Log retention, protection and access | Partly | Audit entries are kept until a retention purge exists, which isn't built. A database trigger refuses any UPDATE and any DELETE outside a purge transaction (`sympraxi/privacy/migrations/0002_audit_append_only.py`; `test_database_rejects_update_and_delete` in `tests/test_audit.py`). The database owner can still get around the trigger (decisions.md, Phase 0). The campus has full access to its own log. How long container logs are kept is the campus's Docker setting |
| AAAI-12 | Works with other authentication systems | Partly | OIDC only, for Entra ID and Google. SAML (Shibboleth), CAS and other OIDC providers such as Okta: Not yet (Phase 5) |
| AAAI-13 | Customer-defined attribute mappings | Not yet (Phase 5) | Student-ID mapping and account linking (02-build-plan.md §9) |
| AAAI-14 | Directory integration | No | A CSV user import is Not yet (Phase 1). LDAP and SCIM: Not scheduled |
| AAAI-15 | SAML2, OIDC or CAS | Partly | OIDC now; SAML2 and CAS Not yet (Phase 5) |
| AAAI-16 | Tells email address and user ID apart | Yes | Once linked, sign-ins match the provider's subject ID, not the address, and an account linked to one identity is never relinked to another by address (decisions.md, Phase 1 SSO; `tests/test_sso.py`) |
| AAAI-17 | Multi-factor login | Yes | Authenticator apps (TOTP), passkeys and recovery codes; no text-message codes. Required for every staff role and site administrators (`RequireStaffMFAMiddleware` in `sympraxi/accounts/middleware.py`; `tests/test_mfa_gate.py`). Students can turn it on. `SYMPRAXI_TRUST_IDP_MFA` accepts the identity provider's MFA only when the ID token's `amr` claim says `mfa` |
| AAAI-18 | Locks or signs out after inactivity | Partly | A sign-in lasts 8 hours by default (`SESSION_COOKIE_AGE`, set by `SYMPRAXI_SESSION_SECONDS`). Django renews it only when the session changes, so it behaves as a fixed lifetime more than an idle timeout. No separate idle timeout. Not scheduled |

### Data

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| DATA-01* | Data on publicly routable addresses | Campus responsibility | PostgreSQL is reachable only on the Compose network; the stack publishes only Caddy's ports 80 and 443 (`deploy/compose.yaml`). The host's addressing and firewall are the campus's |
| DATA-02* | Encryption in transit | Yes | HTTPS through Caddy with automatic certificates (`deploy/Caddyfile`); plain HTTP redirects to HTTPS; HSTS for one year on this host only; secure, HTTP-only session cookies (`sympraxi/settings.py`; `tests/test_security.py`; the smoke test checks the redirect). Inside the server, the app reaches PostgreSQL over the unencrypted Docker network. Mail to the relay uses TLS when `EMAIL_URL` asks for it (`smtp+tls://`), which the campus sets |
| DATA-03* | Encryption at rest | Campus responsibility | Sympraxi doesn't encrypt the database or uploads itself. The campus encrypts the VM's disk or volumes. Off-site copies made with restic are encrypted; the backup folders on the server are not (they are readable by their owner only) |
| DATA-04* | FIPS 140-2 or 140-3 modules | No | Caddy, Python and PostgreSQL use their usual cryptographic libraries, which aren't FIPS-validated as shipped. A campus that needs FIPS would need validated builds |
| DATA-05* | Data available after the contract ends | Yes | There is no contract to end. The data is on the college's server and stays there |
| DATA-06* | Rights survive acquisition or bankruptcy | Yes | Nothing the project does can reach an installed copy. The AGPL's grant is irrevocable for the copy the college has (`LICENSE`, section 2) |
| DATA-07* | Backups leave the institution's data zone | Campus responsibility | `deploy/backup.sh` writes to `backups/` on the same server. Restic sends copies only where the campus sets `RESTIC_REPOSITORY` (`deploy/backup.env.example`) |
| DATA-08* | Archive media stored securely | Campus responsibility | |
| DATA-09 | Data returned and deleted at contract end | Not applicable | No contract; the college already holds all of it |
| DATA-10 | Customer can extract a full or partial backup | Yes | `deploy/backup.sh` (database dump, uploads, migration list, checksums). A director can export one student's complete record as JSON or a printable page (`sympraxi/records/export.py`), or the whole center as one ZIP of JSON and CSV files, built by the task worker and downloadable for 24 hours (`sympraxi/reports/center_export.py`). Statistics and lists download as CSV, XLSX or JSON |
| DATA-11 | Backups include software and settings | No | A backup holds the database, uploads and migration list. The software is rebuilt from the repository. `deploy/.env` and `deploy/backup.env` aren't in the backup, and the campus needs its own safe copy of both (see the gaps list) |
| DATA-12 | Off-site backups | Campus responsibility | Optional through restic (`deploy/backup.sh`, `docs/install.md`, "Backups") |
| DATA-13 | Physical backups taken off-site | Campus responsibility | |
| DATA-14 | Backups encrypted | Partly | Restic copies are encrypted. Local backup folders aren't; `backup.sh` writes them with `umask 077` |
| DATA-15, DATA-16 | Media sanitization (NIST SP 800-88) | Campus responsibility | |
| DATA-17 | Provider staff can reach institutional data | No | No one in the project has access to any installation |
| DATA-18 | Securing staff workstations when remote | Not applicable | No staff with access to data |
| DATA-19 | Single tenant, or how tenants are separated | Yes, single tenant | One installation serves one college (decisions.md, Phase 1 SSO). Inside it, centers are separated by the permission module: querysets are scoped by center, and outsiders get a 404 (`sympraxi/core/policy.py`; `test_other_center` and `test_get_for_action_hides_other_centers` in `tests/test_policy_matrix.py`) |
| DATA-20 | Institution owns its data | Yes | The college holds the database on its own server |
| DATA-21 | 90 days to leave at end of service | Yes | Nothing ends. If the project stopped, installed copies would keep running |
| DATA-22 | Scheduled, protected backups that can't be altered | Partly | Scheduling is a cron line the campus adds (`docs/install.md`, "Backups"). Each backup has SHA-256 checksums that `restore.sh` checks before restoring. Making copies immutable (for example, object lock on the restic store) is Campus responsibility |
| DATA-23 | Key management | Campus responsibility | The secret key and database password are generated at install (`deploy/install.sh`) and kept in `deploy/.env`. Replacing them and the SSO client secrets, and keeping the restic password, are the campus's job. Rotation isn't documented yet (see the gaps list) |

## Infrastructure

### Application

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| APPL-01* | Role-based access control | Yes | One module decides every permission: roles (student, tutor, front desk, director) are scoped to a center, and grants that reach only a person's own records are object rules (`sympraxi/core/policy.py`). `tests/test_policy_matrix.py` checks every role against every action, written out by hand. `tests/test_url_inventory.py` fails if a URL isn't assigned a permission, and requests pages as each role |
| APPL-02* | Web application firewall | No | Campus responsibility if the college wants one in front of Caddy |
| APPL-03* | Only supported software | Yes | Python 3.12, Django 6.1 (moving to 6.2 LTS before the pilot), PostgreSQL 18, Debian trixie base images, Caddy 2.11 (`Dockerfile`, `deploy/compose.yaml`, decisions.md Phase 0). Renovate proposes updates (`renovate.json`). The host OS and Docker are the campus's |
| APPL-04 | Needs location data | No | |
| APPL-05* | Separates security, system and user administration | Yes | Site administrators run the installation and appoint directors, but get no access to records (`SITE_ADMIN_GRANTS` in `sympraxi/core/policy.py`; decisions.md Phase 0). Records aren't registered in the Django admin (decisions.md, Phase 1 session records). Directors manage their own center only |
| APPL-06* | Static analysis before release | Yes | CodeQL with the `security-extended` queries for Python and GitHub Actions on pull requests and weekly (`.github/workflows/codeql.yml`). Ruff with the flake8-bandit rules (`S`) and mypy run in CI (`pyproject.toml`, `.github/workflows/ci.yml`) |
| APPL-07* | Software testing process | Yes | Unit and integration tests, property-based tests of the booking engine (`tests/test_booking_properties.py`), browser tests with axe and CSP checks (`tests/e2e/`), a production-stack smoke test (`deploy/smoke-test.sh`) and a load test of the first-Monday rush (`tests/load/`; results in decisions.md). A run for this draft on October 16, 2026 passed 1,820 unit and integration tests, with one skipped because Gunicorn doesn't run on Windows; the browser tests are reported in accessibility.md. The checkout also held another slice's uncommitted statistics work |
| APPL-08 | Provider staff access is role-based | Not applicable | No provider staff have access |
| APPL-09 | Input validation and error messages | Yes | Django forms validate every input; templates autoescape output; a strict Content Security Policy allows scripts and styles only from the site itself, so injected inline code doesn't run (`SECURE_CSP` in `sympraxi/settings.py`; `test_templates_have_no_inline_styles_or_scripts` in `tests/test_security.py`; `test_page_has_no_csp_violations` in `tests/e2e/test_accessibility.py`). Refused bookings show a message, and the load test fails if one doesn't (decisions.md, Phase 1 load test) |
| APPL-10 | Software supply chain | Partly | Locked dependencies with hashes (`uv.lock`, `frontend/package-lock.json`), pip-audit and npm audit, a Trivy image scan, Renovate. Not yet: an SBOM and signed images for each release (02-build-plan.md §6.2; first release). Base images are pulled by tag, not digest, and GitHub Actions are referenced by tag until Renovate pins them |
| APPL-11 | Developers trained in secure coding | No | No formal training |
| APPL-12 | Built with secure coding practices | Yes | The rules in `CONTRIBUTING.md` (every permission through the policy module, scoped querysets, audited reads, no student data in logs or URLs), Django's protections (CSRF, autoescaping, parameterized queries), public IDs as UUIDs (decisions.md, Phase 1 session records) |
| APPL-13 | Mobile app from a trusted store | Not applicable | No mobile app; the web pages work on phones |
| APPL-14 | How provider staff get admin access to the institution's instance | Not applicable | They don't. The campus creates its own site administrators (`docs/install.md`) |

### Data center

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| DCTR-01 | Hosting option | On premises, or the college's own cloud account | Campus responsibility |
| DCTR-02 to DCTR-13 | Hosting provider's SOC 2, data region, staffing, physical barriers, redundancy, power, cooling, network | Campus responsibility | Sympraxi needs one Linux VM; sizes are in `docs/install.md` |
| DCTR-14 | MFA for cloud administrators | Campus responsibility | |
| DCTR-15 | Hardened images | Campus responsibility for the host | The app container runs as an unprivileged user (`USER sympraxi` in `Dockerfile`) |
| DCTR-16 | Cloud provider holds the encryption keys | Campus responsibility | |

### Firewalls, intrusion detection and prevention

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| FIDP-01* to FIDP-11 | Firewalls, IDS, IPS, monitoring | Campus responsibility | The stack needs ports 80 and 443 open to users (443 over UDP too, for HTTP/3) and nothing else inbound (`deploy/compose.yaml`). Outbound it needs the SMTP relay, the identity provider, and Caddy's certificate authorities (Let's Encrypt, with ZeroSSL as a fallback); when building or upgrading, also GitHub, Docker Hub, PyPI, npm and Debian's package mirrors |

### Incident handling

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| HFIH-01 | Formal incident response plan | No | `SECURITY.md` covers receiving vulnerability reports. Not scheduled; it should exist before the pilot (see the gaps list). An incident on an installation is the campus's to handle |
| HFIH-02 | Incident response team | No | One maintainer |
| HFIH-03 | 24 x 7 response | No | |
| HFIH-04 | Cyber-risk insurance | No | |

### Vulnerability management

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| VULN-01* | Authenticated vulnerability scans before releases | No | CI scans dependencies and the image, and CodeQL analyzes the code, but no authenticated scanner (DAST) runs against a signed-in session. Not scheduled |
| VULN-02* | Shares scan results | Yes, once public | CI results will be public with the repository. The campus can run the same scans on its own copy |
| VULN-03* | Institution may test it | Yes | The campus runs its own copy and can test it any time. `SECURITY.md` asks testers to use demo data (`manage.py seed_demo`), never real student records |
| VULN-04 | Third-party assessment in the past year | Not yet (Phase 5) | An external security review before 1.0 (02-build-plan.md §6.3). No penetration test has been done |
| VULN-05 | Scans for SQL injection, XSS, CSRF and similar | Partly | CodeQL's security queries cover these classes statically; the CSP tests catch inline script. No dynamic scan |
| VULN-06 | External vulnerability scans | Campus responsibility | Of the campus's own installation |

## IT accessibility

The full answer is [accessibility.md](accessibility.md).

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| ITAC-01 to ITAC-04 | Accessibility contact | Not yet (before the repository goes public) | Same as GNRL-04 |
| ITAC-05 | Link to accessibility statement or VPAT | Yes, as a draft | `docs/accessibility.md` |
| ITAC-06* | VPAT or ACR made in the past 12 months | Partly | A draft shaped like VPAT 2.5, October 2026, with most criteria not yet evaluated by hand. A full Accessibility Conformance Report with each release: Not yet (first release) |
| ITAC-07* | Will commit to a standard in a contract | Campus responsibility | No contract and no warranty. The project's target is WCAG 2.2 AA (02-build-plan.md §1 and §7), which includes 2.1 AA |
| ITAC-08* | Substantially conforms to WCAG 2.1 AA | Not yet (Phase 1 exit) | Every page on the browser tests' list passed axe's WCAG 2.2 A and AA rules on October 16 and 17, 2026 (accessibility.md), but the manual review the Phase 1 exit requires hasn't happened |
| ITAC-09* | Process for reporting and tracking accessibility issues | Not yet (when the repository goes public) | GitHub issues are the plan. Nothing is published yet |
| ITAC-10 | Documentation of accessibility features | Partly | `docs/accessibility.md` |
| ITAC-11 | Third-party audit | No | |
| ITAC-12 | Process for verifying conformance | Partly | axe on every page listed in `tests/e2e/test_accessibility.py`, keyboard tests of the main flows, and a rule that every new page joins that list (`CONTRIBUTING.md`; `docs/development.md`, "Adding a page"). The manual review is planned (accessibility.md) |
| ITAC-13 | Adopted a conformance standard | Yes | WCAG 2.2 AA |
| ITAC-14 | Accessibility roadmap with dates | Partly | 02-build-plan.md §7; the review plan in accessibility.md |
| ITAC-15 | Staff keep accessibility skills current | No | One maintainer |
| ITAC-16 | Accessibility in the development life cycle | Yes | `CONTRIBUTING.md` rule 6; "Adding a page" in `docs/development.md` |
| ITAC-17 | Everything works by keyboard | Not yet (Phase 1 exit) | Designed for it, and browser tests drive the main flows by keyboard, but no one has done a keyboard-only pass of every page |
| ITAC-18 | Relies on an accessibility mode, lite version or overlay | No | One interface for everyone, with no overlay (02-build-plan.md §1 item 4) |

## Case-specific

Consulting (CONS), HIPAA (HIPA) and PCI DSS (PCID) don't apply, because of the answers to REQU-03, REQU-05 and REQU-06. The On-Premises questions apply, because of REQU-07.

### On-premises

The campus runs one Linux VM with Docker Engine and the Compose plugin, and five containers: Caddy, the web app, the task worker, the scheduler and PostgreSQL 18 (`deploy/compose.yaml`; `docs/install.md`). Install, upgrade, backup and restore are scripts in `deploy/`. The app containers run as an unprivileged user (`Dockerfile`). Running Docker itself needs root on the host, or membership in the `docker` group, which amounts to the same thing; who holds that is the campus's decision.

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| OPEM-01 | Role-based access for system administrators | Partly | In the app, site administrators are one role with operational actions only and no access to records (`SITE_ADMIN_GRANTS` in `sympraxi/core/policy.py`). It can't be split into narrower administrator roles. Access to the host and database is the campus's to divide |
| OPEM-02 | Provider staff reach customer systems remotely | No | The project needs no account, VPN, inbound firewall rule or remote session, and Sympraxi doesn't call home |
| OPEM-03 | Architecture and data-communication diagrams | Yes | As DOCU-05. The connections each service makes are listed under FIDP above |
| OPEM-04 | Provider requires remote management | No | The campus runs `deploy/upgrade.sh`, which backs up, rebuilds, migrates, reloads the catalog and restarts |
| OPEM-05 | Remote actions logged and visible | Not applicable | No remote management (OPEM-04) |
| OPEM-06 | FERPA-compliant handling under remote access | Not applicable | No remote access (OPEM-02) |
| OPEM-07 | Status monitoring the campus can use (SNMPv3 or other) | Yes, without SNMP | `/healthz` answers over HTTP (`sympraxi/core/views.py`), and the web, worker, scheduler and database containers each have a Docker health check; the scheduler's turns unhealthy when it stops finishing its sweeps (`deploy/compose.yaml`, `sympraxi/healthcheck.py`). No SNMP |
| OPEM-08 | Other safeguards against malicious activity | | The audit log records sign-ins, failed sign-ins, refused SSO sign-ins with the reason, two-factor changes and role changes (AAAI-09, AAAI-10); allauth rate-limits sign-ins and password resets, counted per visitor address behind Caddy, with a page that says when to try again (AAAI-05; decisions.md, Phase 1 sign-in limits); container logs go to standard output for the campus's own monitoring |
| OPEM-09 | How long in this business | | Since October 2026, when the build plan was written |
| OPEM-10 | Existing higher education customers | No | The pilot center is still being recruited (02-build-plan.md §13) |

## AI

The AI section (AIQU, AIGN, AIPL, AISC, AIML and AILM questions) doesn't apply, because of REQU-04. The AI questions in the Privacy section (DPAI) are answered below.

## Privacy

What Sympraxi keeps, for the questions below. Accounts: email address, full name, and optionally a preferred name, pronouns and student ID (`sympraxi/accounts/models.py`). Center roles. Appointments, attendance, late cancellations, no-shows and booking blocks, with staff notes on exemptions and waivers, which may record accommodations (`sympraxi/scheduling/models.py`). Registration and intake answers to questions each director writes (`sympraxi/forms/models.py`). Session records: staff notes, the summary written for the student, next steps, topics, attached resources and amendments (`sympraxi/records/models.py`). Survey answers, stored without the student, appointment, record or time of day (decisions.md, Phase 1 surveys). Audit events with IP address and browser (`sympraxi/privacy/models.py`).

| ID | Asks about | Answer | Evidence and notes |
|---|---|---|---|
| PRGN-01 | Processes FERPA data | Yes | Session records, appointments and form answers are education records (02-build-plan.md §6.1). Access is limited to staff with a legitimate educational interest (the module docstring in `sympraxi/core/policy.py`) |
| PRGN-02 | Processes GDPR or PIPL data | Campus responsibility | Depends on whom the college serves and where it hosts. Sympraxi has no retention or deletion tools yet (DRPV-06), which GDPR would need |
| PRGN-03 | Processes data under state privacy laws | Campus responsibility | As PRGN-02 |
| PRGN-04 | Users can enter regulated information | Yes | Tutors' notes, students' answers and the questions directors write are free text and could hold anything, including health or disability details |
| PRGN-05 | Link to a privacy notice | Campus responsibility | The college's own notice. Sympraxi doesn't show or link one yet (see the gaps list) |
| PCOM-01 | Personal data breach in the past three years | No | Never deployed |
| PCOM-02* | Notes on privacy practices | | The design rules are in 02-build-plan.md §6.1. Every staff read of a record, history, brief or answer set is audited, without the content (`tests/test_records_privacy.py`). Emails say as little as possible: a tutor's booking email has no student name, and the summary email links to the summary, carrying its text only if a center turns that on (decisions.md, Phase 1 scheduling core and session records). Students never see other students: open times carry no names |
| PCOM-03 | Privacy violations in the past 36 months | No | |
| PCOM-04 | Dedicated privacy staff | No | |
| PDOC-01 | SOC 2 includes the Privacy principle | Not applicable | No SOC 2 (DOCU-03) |
| PDOC-02 | Follows a privacy framework | No | Built to FERPA's rules; not mapped to the NIST Privacy Framework or ISO 27701. Not scheduled |
| PDOC-03 | Staff onboarding covers security and privacy training | Campus responsibility | No project staff. Training the center's staff on FERPA is the college's. The record form tells tutors that students can ask to see everything written there (decisions.md, Phase 1 session records) |
| PTHP-01* | Contracts make third parties meet regulatory requirements | Campus responsibility | As THRD-01 to THRD-04 |
| PTHP-02 | Privacy assessments of third parties | Campus responsibility | As THRD-01 to THRD-04 |
| PCHG-01 | Change management includes privacy review | Partly | Each design choice that touches student data is written up with its privacy reasoning in `docs/decisions.md` (for example, why survey answers can't be tied to a student), and `CONTRIBUTING.md` sets rules for code that touches student data. No separate review step; one maintainer |
| PCHG-02 | Mitigating privacy risks until resolved | No | Not scheduled |
| PDAT-01* | Collects demographic information | Yes, if a center asks | Directors mark questions as demographic, and a database check constraint keeps them optional (decisions.md, Phase 1 forms). Pronouns on the account are optional |
| PDAT-02* | Genetic, biometric or behavioral data | No | A passkey may be unlocked with a fingerprint or face, but that happens on the person's own device; Sympraxi receives only a public key |
| PDAT-03* | Combines institutional data with outside sources | No | |
| PDAT-04 | Data crosses the US border | Campus responsibility | Wherever the campus puts its server and backups. The project moves no data anywhere |
| PDAT-05 | Captures device information | Yes | Audit events store the IP address and browser string (`sympraxi/privacy/models.py`). No MAC addresses |
| PDAT-06 | Web tracking (pixels, cookies) | No | No analytics, tracking pixels or third-party requests (decisions.md, Phase 0). The only cookies are first-party ones that keep a person signed in, protect forms (CSRF) and carry one-time messages |
| PDAT-07 | Provider staff can reach institutional data | No | As DATA-17 |
| PDAT-08 | Handles personal data lawfully | Campus responsibility | The college runs it and holds the data; Sympraxi is built to FERPA's rules |
| PRPO-01 | Documented privacy management process | No | |
| PRPO-02 | Privacy by design | Yes | Survey anonymity is enforced in the database: it refuses a survey response tied to a student, appointment, record or time (forms migration 0004; decisions.md, Phase 1 surveys). Survey links are kept out of the logs (`sympraxi/core/logs.py`). Site administrators can't read records. No student names or student IDs in URLs (decisions.md, Phase 1 session records). Minimal emails (PCOM-02) |
| PRPO-03 | Follows breach-notification laws | Campus responsibility | As PPPR-10 |
| PRPO-04 | Follows the institution's privacy policies | Yes | As PPPR-02 |
| PRPO-05 | Subject to the institution's laws | Campus responsibility | As PPPR-03 |
| PRPO-06* | Privacy training program | No | No staff |
| PRPO-07 | Privacy training mandatory | No | |
| PRPO-08 | AI privacy and ethics training | Not applicable | No AI features. The code is written with an AI coding assistant, which sees no student data because the project has none |
| PRPO-09 | Fully automated decisions | Yes | Booking limits refuse a booking automatically. A booking block starts automatically when a student's no-shows or late cancellations reach the center's limit, but staff confirm every no-show first (possible no-shows are only flagged), and a late cancellation counts only when the student cancels inside the window or staff record that the student asked (decisions.md, Phase 1 attendance) |
| PRPO-10 | Process for automated decisions, including review requests | Partly | The rules are written down (decisions.md, Phase 1 attendance). Blocks are audited when set and lifted. The student sees why, when the block ends and whom to write to; staff can waive a no-show or late cancellation, and a director can lift a block. No formal process document |
| PRPO-11 | Policy on sharing with law enforcement | Campus responsibility | The project holds no data to share |
| PRPO-12* | Shares data with law enforcement without a warrant or subpoena | No | The project holds no institutional data. What the college discloses, and under which FERPA exception, is its own decision |
| PRPO-13 | Privacy officer on the incident response team | No | No incident response team (HFIH-02) |
| INTL-01 | Data in the European Economic Area | Campus responsibility | As PDAT-04 |
| INTL-02 | Data protection officer | No | |
| INTL-03 | Signs GDPR Standard Contractual Clauses | Not applicable | The project transfers no data and signs no contracts |
| INTL-04 | Data in China | Campus responsibility | As PDAT-04 |
| INTL-05 | Meets PIPL requirements | Not applicable | As INTL-03 |
| DRPV-01 | Data privacy impact assessment done | No | This document and 02-build-plan.md §6 cover parts of one. Not scheduled |
| DRPV-02 | Privacy notice for end users | No | Sympraxi shows none; the college publishes its own (PRGN-05) |
| DRPV-03 | Choices and consent | Partly | Students turn reminder and survey emails on or off (Account, Email reminders); demographic questions are always optional; surveys are anonymous. Students choosing what instructors receive, and its consent records, are Not yet (Phase 5). FERPA, not consent, governs the records a center keeps |
| DRPV-04 | Collects only for the stated purposes | Yes | Sympraxi uses what it holds only to run the center, and the project receives none of it. What a center asks in its forms is the director's choice (Campus responsibility) |
| DRPV-05 | Documented list of personal data | Yes | 02-build-plan.md §5.1, the list at the top of this section, and the FERPA export, which lists everything held about one student (`sympraxi/records/export.py`) |
| DRPV-06 | Keeps data only as long as needed | Not yet (not scheduled) | The retention purge isn't built, though the feature map lists retention settings under Phase 0 (`docs/01-feature-map.md`). Each center will set a retention period, and a scheduled purge will remove expired content, with legal holds (02-build-plan.md §6.1 item 8). The triggers already reserve settings for it (`sympraxi.audit_purge`, `sympraxi.records_purge`, `sympraxi.forms_purge`). Until then, final records, amendments, form answers and audit events can't be deleted through Sympraxi at all, and backups keep what they hold for as long as the campus keeps them (`docs/install.md`, "Backups") |
| DRPV-07 | People can review and update their data | Partly | Students see their summaries, next steps and resources on My writing, their appointments and their own form answers. They can change their email address, their registration answers, and their intake answers until the appointment starts; there is no page yet for changing their own name or pronouns. Staff notes appear only if a center turns that on. A director can export a student's complete record in one step to answer an inspection request under 34 CFR 99.10 (`sympraxi/records/export.py`). A final record changes only by amendment, with the reason and the earlier text kept |
| DRPV-08 | Discloses only for stated purposes or with consent | Yes | Sympraxi sends nothing outside the college except email through its relay (THRD-01). The disclosure log for FERPA 99.32 is Not yet (Phase 1) |
| DRPV-09 | Protects against unauthorized access | Yes | Logical access as in APPL-01, AAAI-17 and AAAI-09. Physical security is Campus responsibility |
| DRPV-10 | Keeps data accurate | Partly | Students update their email address and answers, but not their name or pronouns; final records change only by amendment (DRPV-07) |
| DRPV-11 | Handles privacy complaints | Campus responsibility | |
| DRPV-12 | De-identifies data | Yes | Survey responses are stored without the student, and results show only for a term with at least five responses (decisions.md, Phase 1 surveys). A de-identified research export is Not yet (Phase 3) |
| DRPV-13 | Uses de-identified data for other purposes | No | The project receives none |
| DRPV-14 | Honors requests to stop processing | Campus responsibility | The campus can deactivate an account. Sympraxi can't yet delete a student's records (DRPV-06) |
| DRPV-15 | Reviews code for ethical considerations | Partly | Design choices with effects on students are argued in `docs/decisions.md` (for example, why only directors see and lift booking blocks, and why survey responses don't record the tutor). No formal review |
| DPAI-01 | Uses AI on institutional data | No | No AI features (REQU-04) |
| DPAI-02* to DPAI-08 | Retention, contracts, subprocessors and opt-out for AI | Not applicable | No AI features |

## What the campus has to do

Collected from the answers above:

1. Run and patch the host: OS updates, Docker updates, disk or volume encryption, firewall (only 80 and 443 inbound), monitoring and log shipping.
2. Apply Sympraxi patch releases promptly (`SECURITY.md`).
3. Schedule `deploy/backup.sh` nightly, keep encrypted copies off the server, test a restore every term, and match backup retention to the records-retention policy (`docs/install.md`).
4. Keep safe copies of `deploy/.env` and `deploy/backup.env`, including the restic password, which no one can recover.
5. Register the app with Entra ID or Google, add the optional claims, and replace the client secret before it expires (`docs/install.md`, "Single sign-on").
6. Keep at least one site administrator with a password and two-factor login for the break-glass page, and try it once a term.
7. Review site administrators and directors each term.
8. Keep direct database access to the people who run the server; the audit log's and records' triggers don't stop the database owner, and survey anonymity depends on it (`docs/install.md`, "Session-report questions and surveys").
9. Leave `/survey/` paths out of any access log added to Caddy.
10. Handle incidents and breach notification under FERPA and state law.
11. Vet and contract with the SMTP relay, identity provider and backup store.
12. Publish the college's own privacy notice for students.

## Gaps

The open items a pilot campus will notice, in rough order of how much they'll matter to its review:

1. **One maintainer.** No second reviewer of changes, no team, no on-call (COMP-01, CHNG-04, HFIH-02). The build plan's mitigation is a second maintainer before 1.0.
2. **No penetration test or external review.** Planned before 1.0 (Phase 5). No DAST scan in CI (VULN-01, VULN-04).
3. **No SOC 2 or framework mapping** (DOCU-03, DOCU-04). SOC 2 doesn't fit a project that runs no service; a mapping to NIST CSF or CIS Controls could come with the external review.
4. **No formal incident response plan, information security policy, or patch deadlines by severity** (HFIH-01, PPPR-08, CHNG-07, CHNG-08). A short incident response plan covering how the project tells installations about a vulnerability, and what a campus should do, is worth writing before the pilot.
5. **No release, release notes, advisory channel or contact address yet** (CHNG-01, GNRL-04, ITAC-09). The repository is private.
6. **Retention purge, legal hold and disclosure log not built** (DRPV-06, DRPV-08). Records can't be deleted through the app at all until the purge exists, so requests to delete or stop processing (DRPV-14) have no tool yet.
7. **No encryption at rest in the app** (DATA-03). TOTP secrets for two-factor login are stored unencrypted in the database, because allauth's default MFA adapter doesn't encrypt them. A database dump or backup therefore holds the seed of every authenticator app set up in Sympraxi. allauth provides `encrypt()` and `decrypt()` hooks for this.
8. **SSO tested only against a fake provider** (AAAI-01). A real-tenant test needs the pilot's app registration.
9. **No idle timeout and no login-alert emails** (AAAI-18). 02-build-plan.md §6.2 lists login alerts; allauth's `ACCOUNT_EMAIL_NOTIFICATIONS` covers password, email and two-factor changes, not new sign-ins.
10. **No SBOM or signed images; images and actions pinned by tag, not digest** (APPL-10). Whether the Renovate app is switched on for the repository isn't recorded; the workflows still reference actions by tag, which Renovate's `pinGitHubActionDigests` would change.
11. **No SIEM export** (AAAI-10). Container logs and the audit table are there for the campus to collect.
12. **Backups leave out `deploy/.env` and `deploy/backup.env`, and the docs don't say to keep copies of them** (DATA-11). Losing the restic password loses every off-site copy. Key rotation isn't documented (DATA-23).
13. **No CSV user import yet** (AAAI-14). Phase 1.
14. **Password rules can't be changed without editing code** (AAAI-03).
15. **No privacy notice, privacy framework or impact assessment** (PRGN-05, DRPV-01, DRPV-02, PDOC-02). The college publishes its own notice, but Sympraxi has no setting to link it from its pages.
16. **Students can't change their own name or pronouns** (DRPV-07, DRPV-10). Only a site administrator can, in the Django admin.
