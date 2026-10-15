# WCONLINE feature map

This document records what WCONLINE does today, who uses each feature, and what Sympraxi, the open-source replacement, will do about it. It is the parity checklist for the build plan in [02-build-plan.md](02-build-plan.md).

Most of the detail comes from WCONLINE's own help manual (help.mywconline.com, about 55 articles), its marketing, pricing and terms pages, and help pages from 14 colleges that run it. Items marked *unverified* appear only in marketing copy or search snippets.

## How to read the tables

**Who** uses the feature:

| Code | Role |
|---|---|
| S | Student (WCONLINE calls them "clients") |
| T | Tutor or consultant |
| FD | Front desk or receptionist |
| D | Center director or coordinator |
| F | Faculty, advisors and other third parties |

**Plan** says what we do about it:

- **Match**: build it to work the way centers already expect.
- **Better**: build it, and fix a known problem while doing so.
- **New**: WCONLINE doesn't have it.
- **Later**: worth doing, but after the first release.
- **Skip**: not planned.

**Phase** refers to the phases in the build plan: P0 foundation, P1 core release, P2 online and asynchronous sessions, P3 resource library and long-term tracking, P4 staff operations and training, P5 campus integration.

## 1. Structure and roles

WCONLINE has three access levels: Client, Basic Administrator and Full Administrator. Tutors and front-desk staff are both Basic Admins, with per-panel permissions. A tutor isn't an account. A tutor is a "listing" on one schedule, linked to a login only when the email addresses match, so a tutor who works on three schedules needs three listings. One license covers unlimited centers on a campus, but those centers share one registration form, one report form, one survey and one brand, and every admin can see every schedule.

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Roles | Client, Basic Admin, Full Admin; Basic Admin scoped per panel | all | **Better**: real roles (student, tutor, front desk, director, site admin, faculty), each scoped to a center | P0 |
| Tutor identity | One listing per schedule, matched to a login by email | T, D | **Better**: one tutor account, assigned to many schedules | P1 |
| Non-person resources | A listing can be a room, desk or workshop | D | **Match**: rooms and workshops as bookable resources | P1 |
| Multiple centers | Unlimited centers, but shared forms and branding, and no admin scoping | D | **Better**: each center has its own forms, branding, staff and data permissions | P1 (model), P5 (polish) |
| Schedules | Unlimited, each with its own dates, hours, increments, limits and emails | D | **Match** | P1 |
| Schedule visibility | Everyone, admins only, or hidden/archived; QR code and direct link per schedule | D | **Match** | P1 |

## 2. Schedule setup and availability

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Hours and increments | Daily start/end, slot increments with alignment rules | D | **Match** | P1 |
| Terms and closures | Term dates, closed weekdays, holidays | D | **Match** | P1 |
| A/B rotation days | Dates assigned to rotation days | D | **Later** (mostly K-12 and some colleges) | later |
| Slot-only schedules | Numbered slots instead of clock times, used for eTutoring | D | **Better**: asynchronous work gets a real queue (§8), so it doesn't need fake time slots | P2 |
| Alternating time | Odd blocks, such as 45 minutes plus a 15-minute break | D | **Match**: buffer time after each appointment | P1 |
| Weekly availability | "Starting availability" per tutor, then all changes as blackouts | T, D | **Match**, with a simpler editor: a weekly template plus one-off exceptions | P1 |
| Blackouts | Single or repeating, edit one/future/all, can't overlap appointments | T, D | **Match** | P1 |
| Tutor self-service hours | Tutors can be allowed to edit their own hours | T | **Match** | P1 |
| Per-tutor rules | Min/max length, daily/weekly caps, required start times, modalities, group capacity, walk-in only, tentative | D | **Match** | P1 |
| Focuses | Courses or specialties that students can filter by | S, D | **Match**, tied to the topic taxonomy (§15) | P1 |
| Tutor profiles | Photo, bio, hover bio, location, start/end dates | S, D | **Match** | P1 |
| Bulk modality change | Switch every appointment on a day to online (snow days) | D | **Match**, and notify every affected student automatically | P2 |
| Display options | Today-first or Monday-first, day or week view, tutors alphabetical or random | S | **Match** | P1 |

## 3. Booking rules and limits

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Student limits | Max appointments or minutes per day, week, month or schedule; no back-to-back; one future appointment | D sets, S bound | **Match** | P1 |
| Notice and horizon | Minimum notice before booking; how many days ahead are shown | D | **Match** | P1 |
| Change cutoff | No changes or cancellations within a window | D | **Match** | P1 |
| Exemptions | Per-student exemptions, often for accommodations | D | **Match**, with a note field and an expiry date | P1 |
| Appointment credits | Admin-assigned credits, each booking uses one | D | **Later** | later |
| Limits across schedules | Optional | D | **Match** | P1 |

## 4. Appointment types

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| In-person | Standard appointment | S, T | **Match** | P1 |
| Online (live) | Built-in module or a Zoom/Meet link | S, T | **Better** (see §7) | P2 |
| eTutoring (asynchronous) | Upload a paper, tutor returns a file | S, T | **Better** (see §8) | P2 |
| Group and workshop | Resource with capacity above 1 | S, D | **Match**, with a proper roster and attendance | P1 (basic), P4 (workshops) |
| Walk-in | A checkbox, used for reporting | T, FD | **Match**, plus a quick walk-in form at the front desk | P1 |
| Placeholder | Admin-only hold, left out of statistics | FD, D | **Match** | P1 |
| Tentative | Student booking that needs admin confirmation | D | **Match** | P1 |
| Repeating | Weekly series; admin-only unless "client repeating" is on; files attach only to the first one | S, T, D | **Better**: each appointment in a series keeps its own files and notes | P1 |

## 5. Waiting list, cancellations and no-shows

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Waiting list | Per day, optional tutor/time/focus filter. When a slot opens, everyone is notified at once and the first to book wins | S, D | **Better**: offer the slot to one student at a time with a short hold (for example, 30 minutes), then move to the next | P4 |
| Cancellation | Student cancels before the cutoff; optional late-cancel policy with its own email and threshold | S, D | **Match** | P1 |
| No-show marking | Manual, from the appointment menu | T, FD | **Better**: auto-flag as "possible no-show" at a set time (MTSU and American University count 15 minutes late as missed) for staff to confirm | P1 |
| Blocking | At a threshold, deactivate (or deactivate and hide) the account; blocks never expire; "reactivate all" each term | D | **Better**: blocks can expire on a date or at term end, the student sees why and how to restore access, and every block is logged | P1 |
| No-show email | Sent with a running count | S | **Match**, with a gentler default template | P1 |

## 6. Forms

WCONLINE has four forms: registration, appointment intake, client report (session notes) and survey. Each holds up to 20 questions, answers are stored by question number, and there is no branching logic. Reordering questions or changing a question's type scrambles past data.

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Registration form | Name, email, password, plus up to 20 custom questions; admins-only questions; forced profile updates; optional GDPR statement | S, D | **Better**: no question cap, questions keep stable IDs so reordering is safe, and demographic questions are optional by default | P1 |
| Intake form | Up to 20 questions per schedule (course, instructor, assignment, goals, file upload); answers can color an appointment or go to the tutor's email | S | **Better**: a form builder with versioning and simple conditional questions | P1 |
| Instructor verification | Napa Valley emails the instructor to confirm extra-credit requests | F | **Later** | P5 |
| Session report (notes) | Up to 20 custom questions; confidential by default; students can't see their reports in the system; can be emailed to the student, tutor, center or others; one file attachment; "orphan" alerts for missing reports | T writes, D reads | **Better**: a structured note with topic tags and attached resources, plus a separate student-facing summary that shows in the student's history. Missing-report reminders stay | P1 |
| Off-schedule reports | Notes for meetings that weren't booked | T | **Match** | P1 |
| Group reports | Copy a report to every group member | T | **Match** | P1 |
| Form versioning | None; changes rewrite history | D | **New**: each submission records the form version it used | P1 |

## 7. Live online sessions

WCONLINE's built-in module offers a shared rich-text "whiteboard" with author colors, text chat, WebRTC audio and video, screen sharing, a drawing layer, a math tool and a timeslider that replays every edit. Its hosts are named `etherpad-us.mywco.com`, which suggests it is built on Etherpad. It doesn't record, doesn't export chat, and has no captions. Tutors in an IWCA *Peer Review* study preferred Google Docs plus Zoom, because the whiteboard mangles tables and imported PDFs and has no anchored comments.

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Video and audio | Peer-to-peer WebRTC, up to about 10 people; fails on locked-down networks | S, T | **Better**: a self-hosted open-source video server with TURN relay, embedded in the session page | P2 |
| External meeting links | Zoom (OAuth per tutor) or a pasted link; Meet confirmed; Teams/Webex *unverified* | T, D | **Match**: a per-tutor or per-appointment meeting link works from day one, so centers can use the campus Zoom or Teams before the built-in video exists | P1 |
| Shared document | Rich-text whiteboard with author colors and replay | S, T | **Better**: a collaborative editor that keeps headings, lists and tables, with anchored comments | P2 |
| Text chat | Live chat; can't be exported | S, T | **Better**: chat saved to the session record (with a retention setting) | P2 |
| Screen share, drawing, math | Present | S, T | Screen share **Match** (P2); drawing and math **Later** | P2 / later |
| Replay | Timeslider and starred snapshots | S, T | **Match**: a version history of the shared document | P2 |
| Recording | Not available | n/a | **Skip** by default. Recording student sessions raises consent and FERPA questions; a center could switch it on later with explicit consent | later |

## 8. Asynchronous feedback (eTutoring)

Today a student books a numbered slot and uploads a paper (up to 10 files, often 5 MB each). The tutor downloads it, comments in Word, and uploads the file back. There's no in-browser annotation and no turnaround tracking, so centers enforce page limits and deadlines by policy.

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Submission | Book a slot and upload files | S | **Better**: a submission queue with no fake time slots; the student says what kind of feedback they want and when they need it | P2 |
| Assignment | Tied to the slot's tutor | D | **Better**: directors or tutors claim submissions from a queue, or the system assigns by focus and load | P2 |
| Feedback | Download, comment in Word, upload back | T | **Better**: comment in the browser on PDFs and converted .docx files; uploading a commented Word file still works | P2 |
| Turnaround | Not tracked | D | **New**: due dates, overdue alerts, and turnaround statistics | P2 |
| File limits | Small caps (1 to 5 MB seen) | D | **Better**: configurable, with a 25 MB default | P2 |

## 9. Tutor and front-desk tools

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| My appointments | Today's list, intake answers, the student's past reports | T | **Match**, with the student's history and topic trends on one screen | P1 |
| Booking for others | Front desk books for any student, adds students, marks walk-ins | FD | **Match** | P1 |
| Time clock | Clock in/out, IP restriction, 2 questions at clock-in, auto clock-out at midnight, linked to the day's appointment | T, D | **Match**, plus hour categories (tutoring, training, admin) and a timesheet export for payroll | P1 (basic), P4 (full) |
| Kiosk and card swipe | Keyboard-emulating card reader; first swipe links the card; auto logout | S, T, FD | **Match** | P4 |
| Waiting room | Arrivals listed in order for staff | FD | **Match** | P4 |
| Tutor training | Not offered | T, D | **New**: training modules, readings, observation forms and sign-offs (see §15) | P4 |
| Day-only name change | Rename a staff listing for one day | FD | **Skip**; reassigning the appointment covers it | n/a |

## 10. Communications

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Appointment emails | Confirmation, change, cancellation; per-schedule templates with merge codes; iCal attachment | S, T | **Match** | P1 |
| Reminders | Up to 3, from 30 minutes to 48 hours ahead | S | **Match** | P1 |
| Text messages | Student enters number and carrier, which points to email-to-SMS gateways (AT&T closed its gateway in June 2025) | S, T | **Better**: a pluggable SMS provider with recorded opt-in consent; email-to-SMS is not used | P5 |
| Mass email | Filtered sends with a log | D | **Match** | P1 |
| Announcements | On the login page or on schedules | D | **Match** | P1 |
| Calendar feeds | iCal attachment per appointment | S, T | **Better**: a private subscribable calendar feed per user | P1 |

## 11. Reports and data

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Statistics | Counts by schedule, tutor, day and time, and every countable form answer | D | **Match** | P1 |
| Utilization | Booked time as a share of available time | D | **Match** | P1 |
| Master listings | Filterable lists of appointments, reports, time clock and waiting list; restore canceled appointments | D | **Match** | P1 |
| Data export | Excel only, about 14 datasets | D | **Better**: CSV, XLSX and JSON, plus a full-center export that a director can run at any time | P1 |
| API | Read-only JSON, 300 requests an hour, no webhooks | D | **Better**: a documented read/write API and webhooks | P5 |
| Long-term student view | None in-app; notes are scattered in emails | T, D, S | **New**: a student timeline across sessions, with topics, goals and resources (see §15) | P3 |
| Topic analytics | Only as counts of form answers | D | **New**: topic trends by term, course and student population | P3 |

## 12. Surveys, alerts and faculty

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Post-session survey | One survey per site, up to 20 questions; sent after the report or the appointment, or as a public link; anonymous | S | **Better**: a survey per center or schedule, built with the same form builder | P1 |
| Early alerts | Email an advisor, instructor, coach or parent when a student books, cancels, misses, or has a report filed | D sets, F receives | **Match**, but every alert rule needs a recorded reason, and the student can see that it exists (a FERPA safeguard) | P5 |
| Faculty access | Email only; no login | F | **Later**: an optional faculty view showing attendance confirmations the student chose to share | P5 |

## 13. Accounts, authentication and integration

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Local accounts | Email and password (10+ characters); domain-restricted registration with activation link | all | **Match**, plus passkeys and required multi-factor login for staff | P0 |
| OIDC single sign-on | Not offered | all | **New**: Microsoft Entra ID and Google Workspace | P1 |
| SAML single sign-on | Recommended; email is the identifier | all | **Match** (SAML/Shibboleth), plus CAS and other OIDC providers | P5 |
| LDAP and "Universal SSO" | Being deprecated | all | **Skip** | n/a |
| Staff two-factor login | Text-message codes for admins | D | **Better**: authenticator apps and passkeys instead of text codes | P0 |
| LMS integration | None documented | S, F | **New**: LTI 1.3 launch from Canvas, Moodle and D2L | P5 |
| CRM integration | EAB Navigate and Salesforce, *unverified* | D | **Later**, through the API and webhooks | P5 |
| Bulk account tools | Mass import, mass deactivation, mass email-domain change | D | **Match** | P1 |

## 14. Accessibility, security and privacy

| Feature | WCONLINE today | Who | Plan | Phase |
|---|---|---|---|---|
| Accessibility | VPAT 2.5 posted, no stated WCAG level; the calendar is a color grid, and screen-reader users are sent to a separate text-only view | all | **Better**: one interface built to WCAG 2.2 AA (which covers the 2.1 AA legal requirement), tested with screen readers, where color is never the only signal | P0 onward |
| Hosting | Vendor-hosted SaaS in US or Canadian datacenters; no self-hosting | n/a | **Better**: self-hosted by the college, or hosted by a consortium | P0 |
| FERPA | Not mentioned in the terms of service | n/a | **New**: FERPA-aware defaults (see the build plan's security section) | P0 onward |
| Data ownership | Customer owns the data but must export before canceling; deleted within 24 hours | D | **Better**: the college holds the database; documented retention and deletion settings | P0 |
| Admin session timeout | 30 to 45 minutes | D | **Match**, configurable | P0 |

## 15. Things WCONLINE doesn't do

These are the reasons to build a replacement instead of only copying one.

1. **A student's writing history, visible to the student.** Each session leaves a student-facing summary: what they worked on, what they plan to do next, and the resources the tutor attached. Students can review it all in one place, and tutors see it before the next session.
2. **A topic taxonomy across all records.** The same tags appear on intake forms, session reports, asynchronous feedback and resources. That makes questions like "how often do first-year students come in about citation?" or "what did this student work on across three terms?" answerable without hand-coding exports.
3. **A writing resource library.** Tutors can attach Purdue OWL pages and openly licensed handouts to a session in a couple of clicks. See the resource section of the build plan for the licensing rules.
4. **Tutor training.** Modules, readings, quizzes, observation forms and hour requirements, tracked per tutor.
5. **A real asynchronous queue** with in-browser comments and turnaround tracking.
6. **Openness:** no license fee, full exports, a write API, webhooks, LTI, and code that a center can inspect.

## 16. Pain points and the design response

| Pain point | Response |
|---|---|
| Cost ($1,999 a year campus license, metered pricing for online schools) and lock-in after the 2023 ownership change | Open source, self-hostable, full export at any time |
| Forms capped at 20 questions, keyed to position, no branching | Form builder with stable question IDs, versions and simple conditions |
| Multiple centers can't be separated | Center-scoped roles, forms and branding |
| Students can't see their own notes | Student-facing session summaries and a history page |
| Waiting list is a race | One offer at a time, with a hold |
| No-shows are manual, blocks never expire | Auto-flag for staff to confirm; blocks expire and are logged |
| Whiteboard mangles formatting, no recording, peer-to-peer video fails on campus networks | Collaborative editor that keeps formatting; self-hosted video with TURN; recording left off by default |
| eTutoring is a file round-trip | Submission queue with in-browser comments |
| Text messages depend on carrier gateways | Pluggable SMS provider with consent records |
| Accessibility handled by a separate text view | One accessible interface |
| No LMS integration or webhooks; read-only API | LTI 1.3, write API, webhooks |
| No faculty portal or tutor training | Optional faculty view; training module |

## Sources

**Vendor:** mywconline.com (home, features, pricing, terms of service and supplement); 26llc.com; help.mywconline.com (schedule management, forms, access levels, online meetings, global settings, staff and resources, availability, blackouts, early alerts, client records, survey, time clock, statistics, utilization, master listings, data export, mass email, announcements, FAQ).

**Institutions:** Trinity College (CT) tutor and eTutoring guides; Northeastern State University; San Diego State; University of Toronto Mississauga; Middle Tennessee State; University of Arizona; Napa Valley College; American University; Vanderbilt; University of Michigan Sweetland Center; Ohio State; Georgetown SCS; Purdue Northwest; University of West Florida.

**Scholarship:** "From Online Writing Centers to Centering Writing Online," *The Peer Review* 10.1 (IWCA).

**Not verified (search snippets only):** workshop sign-ups at Naval Postgraduate School and UT Arlington; the 2023 *Praxis* critique of WCONLINE (abstract only); EAB, Salesforce, Teams and Webex integrations; historical prices.
