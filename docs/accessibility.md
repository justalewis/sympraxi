# Accessibility conformance notes (draft)

A draft conformance report for a pilot campus's accessibility and IT review, laid out like the WCAG edition of the ITI VPAT 2.5. It reports what has actually been tested and says plainly what hasn't. Phase 1 of [02-build-plan.md](02-build-plan.md) calls for it, and [security-questionnaire.md](security-questionnaire.md) points here for HECVAT's IT Accessibility section.

| | |
|---|---|
| Product | Sympraxi, scheduling and session records for college writing centers |
| Version | No release yet. Branch `phase-0-foundation` at commit `1dd462c`, with the fixes made on October 17 for issues this draft found (decisions.md, "Fixes from the security and accessibility drafts") and the checks added later that day (decisions.md, "Accessibility checks on phones, after htmx updates and with emulated settings"). The statistics and export pages being built that day aren't covered |
| Report date | October 17, 2026 (draft) |
| Contact | To fill in before the repository goes public |
| Standard | WCAG 2.2, Levels A and AA. Level AAA isn't reported |

## Summary

Sympraxi aims at WCAG 2.2 AA (02-build-plan.md §1 and §7), which includes the WCAG 2.1 AA level that the ADA Title II rule requires of public colleges. Its pages are server-rendered Django templates built from U.S. Web Design System 3.14 components. Sympraxi ships no JavaScript of its own: htmx and USWDS's script are the only scripts (`frontend/package.json`), and the forms that use htmx also work without it (decisions.md, Phase 1 forms and session records).

What is known today:

- Every page in the browser tests' lists passes axe's automated WCAG 2.2 A and AA rules (results below). Since October 17 the lists include the rest of allauth's account pages, the error pages, five forms sent back with errors, eight student pages at phone size with the menu open, the content htmx swaps into seven pages, and eight pages with forced colors and with reduced motion emulated.
- Ten scripted browser tests drive key flows by keyboard or check the text of a status line.
- No one has tested Sympraxi with a screen reader, by keyboard alone across every page, at 400% zoom, or with a real contrast theme or reduce-motion setting (the browser tests only emulate those two). So almost every criterion below is "Not Evaluated." The manual review planned at the end decides the real levels.

## Terms

| Level | Meaning |
|---|---|
| Supports | The functionality meets the criterion without known defects |
| Partially Supports | Some functionality doesn't meet the criterion |
| Does Not Support | Most functionality doesn't meet the criterion |
| Not Applicable | The criterion doesn't apply to the product |
| Not Evaluated | Nobody has checked it by hand yet |

A finished VPAT 2.5 allows "Not Evaluated" only for Level AAA criteria. This draft uses it for A and AA too, because the manual review hasn't happened, and claims no "Supports" without it. "Not Applicable" is used only where the code shows the feature doesn't exist, such as audio or video. Each "Remarks" cell gives the evidence there is, including any known issue, so the reviewer knows where to look first.

## How it has been checked

### Automated: axe in a real browser

`tests/e2e/test_accessibility.py` opens each page in Chromium through Playwright, signed in as the right role, and runs axe-core 4.12.1 (bundled with `axe-playwright-python`) with the rule tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` and `best-practice`. Any violation fails the test. CI runs it on every push (the `test` job in `.github/workflows/ci.yml`). A rule in `CONTRIBUTING.md` and step 5 of "Adding a page" in [development.md](development.md) put every new page on the list.

Run for this draft on October 16 and 17, 2026: all 240 browser tests passed, in 52 minutes, in a checkout that also held another slice's uncommitted statistics work. After the October 17 fixes, all 295 passed, in 47 minutes, without that work. That is axe and the CSP check on the 115 page views of the main list and on the 21 account pages; axe and the CSP check on 5 error pages; axe on the expired-form page and on 5 forms sent back with errors; and the keyboard, status and booking tests described later (12 in all, one of them axe on a dead survey link). With the checks added later that day, all 329 passed, in 43 minutes: those 295; axe on 8 student pages at phone size, each as it loads and with the menu open; axe on 7 pages after htmx updates; axe on 8 pages and on the open phone menu with forced colors and, separately, with reduced motion (18 tests); and the keyboard test of the phone menu. All three runs used Chromium on Windows 11 with PostgreSQL 18.6.

The main list holds 115 page views: 108 pages and 7 variants that change once single sign-on is set up. Six more lists, added on October 17, are after the table.

| Signed in as | Pages |
|---|---|
| Nobody (6) | Home; sign in; create an account; reset password; a survey opened from its emailed link; the survey's thank-you page |
| Nobody, with SSO set up (6) | Sign in and create an account with Microsoft and Google buttons; sign in when SSO is the only way; the site administrators' break-glass sign-in; the Microsoft start page; a canceled SSO sign-in |
| Student (21) | My centers; the center page; name and pronouns; email addresses; change password; two-factor settings; schedules; open times for a day; booking a time; my appointments; one appointment; canceling; moving (choosing a day, then a time); My writing; one session on My writing; a past appointment; email reminders; registration answers; intake answers for an appointment; a session whose report answers include one shown to the student |
| Student, other cases (5) | Open times, my appointments and a missed appointment for a student whose booking is paused; the booking page for a student who hasn't answered the registration questions yet; linked accounts with SSO set up |
| Tutor (18) | My tutoring; an appointment; records to write; starting a record; a past appointment; editing a draft; a final record; amending it; an off-schedule record; a student's writing history; editing a writing project; attendance for a session with no record and for one with a record; possible no-shows; email reminders; a student's registration answers; a record with report answers, and amending it |
| Front desk (11) | Open times; booking for a student; the day sheet; an appointment; confirming a tentative appointment; records to write; a past appointment; possible no-shows; an appointment with no record; canceling; waiving a no-show |
| Director (48) | The center page; members; the audit log; schedules (list, new, manage, edit); tutor assignments (new, edit, weekly hours, new hours change, deleting one); terms (list, new, edit, new closure, deleting one); rooms (list, new, edit); exemptions (list, new, ending one); a student's export; the center's resources (list, new, edit); record settings; booking blocks (list, settings, lifting one); forms (list, new, the builder, new question, editing and removing a question, preview, publish); an appointment; a student's registration answers; the session-report questions and one of them; the survey, when it goes out, and its results; a record with report answers; and, for a director without two-factor login yet, the page that asks them to set it up |

| Added list | Pages |
|---|---|
| allauth's account pages (21) | Not signed in: the password-reset steps after the email (the notice, the form the link opens, and the last page); the email-confirmation notice and a confirmation link; an inactive account; a failed SSO sign-in. Signed in as a student: signing out; reauthenticating with a password; setting a password on an account made by SSO; the two-factor code at sign-in; setting up an authenticator app; adding a passkey. Signed in as a student with an authenticator app, recovery codes and a passkey: two-factor settings; reauthenticating with a code; turning the app off; recovery codes, and making new ones; the passkey list; editing and removing a passkey |
| Error pages (7) | Page not found, signed out and for a student opening an appointment that doesn't exist; not part of your role (403); something went wrong (500), signed out and signed in; a form that expired (a failed CSRF check); a dead survey link |
| Forms sent back with errors (5) | Sign-up with a short password; sign-in with a wrong password; booking without the required intake answers; a demographic question marked required (a checkbox's error); name and pronouns with a blank full name |
| On a phone, 375 by 812 CSS pixels (8) | Not signed in: sign in. As a student: My centers; the center page; open times for a day; booking a time; my appointments; one appointment; one session on My writing. Each is checked as it loads and again with the menu open, and the test fails if the page scrolls sideways |
| After htmx updates part of a page (7 pages, 8 states) | Open times after changing the date, on a schedule's page and on the page for moving an appointment; the booking page after choosing an answer that brings up a follow-up question; the record form after tagging a topic brings up suggestions, and again after attaching one; the record form's resource search; the members search; the form builder after moving a question |
| With emulated settings (8 pages, plus the open phone menu, each twice) | Sign in; the center page; open times for a day; booking a time; one appointment; a tutor starting a record; the front desk's day sheet; the form builder. Each with forced colors on and, separately, with reduced motion on. axe's two color rules are off under forced colors (see below) |

The pages on the main list, the account pages and the first five error pages also load under the real Content Security Policy, and a test fails on any policy error (`test_page_has_no_csp_violations`, `test_an_account_page_has_no_csp_violations`, `test_an_error_page_passes_axe_and_the_csp`). Another test fails if any template has a `style=` attribute, an `on...=` event attribute or an inline `<script>` (`test_templates_have_no_inline_styles_or_scripts` in `tests/test_security.py`). For accessibility this means all behavior comes from htmx and the published USWDS components, with no custom widgets of Sympraxi's own to break, and no inline styles for a person's own style sheet to work around.

### What axe doesn't cover

Automated rules find only some kinds of failure. The tests see most pages only as they first load, at Playwright's default desktop size, in light mode; the lists above add a failed submission on five forms, htmx updates on seven pages, a phone-sized screen on eight student pages, and emulated forced colors and reduced motion on eight pages. They never see:

- error messages on forms other than the five above, or htmx updates other than the eight above;
- staff pages, and student pages not listed above, on a phone-sized screen, or any page at 320 CSS pixels, the width WCAG's reflow criterion uses;
- hover and focus states, zoom or changed text spacing;
- a real Windows contrast theme. Chromium's emulation switches the forced-colors styles on, but axe can't judge contrast under it: it compares the page's own text colors with the forced background, not the colors the browser draws, so its `color-contrast` and `link-in-text-block` rules are off in those runs. Screenshots taken under emulation showed field borders, radio buttons, checkboxes and the menu's close button drawn in the theme's colors, but no test checks that;
- whether names, headings, titles and messages make sense.

With the menu open, axe marks its `aria-hidden-focus` rule as needing review: USWDS hides the page behind the menu from screen readers with `aria-hidden`, and its links can still take focus. USWDS's focus trap keeps Tab inside the menu, and a scripted test checks that (below).

These pages aren't on the list at all:

- Two allauth steps: the SSO sign-up page, which appears only when a provider's sign-in lacks something allauth needs, and signing in with a passkey, which happens in the browser's own dialog.
- The Django admin, used only by site administrators to create centers and appoint directors. It is Django's own interface, not USWDS.
- Emails (plain text) and calendar files.

### Scripted keyboard and status tests

These tests in `tests/e2e/test_accessibility.py` pass with the axe run above. Some move focus with Playwright's `focus()` rather than the Tab key, so they show that the controls work from the keyboard, not the whole Tab order.

| Test | What it shows |
|---|---|
| `test_sso_button_works_from_the_keyboard_under_the_csp` | Tab reaches "Sign in with Microsoft" and "Sign in with Google" and Enter starts the sign-in |
| `test_a_tutor_writes_and_finalizes_a_record_by_keyboard` | A tutor answers a report question and checks a topic with the space bar, Tabs to a suggested resource and attaches it with Enter, types a note and finalizes, with no reload. The status line reports the suggestions and the attachment |
| `test_front_desk_confirms_a_possible_no_show_by_keyboard` | Tab reaches "No-show" on the list of possible no-shows and Enter records it |
| `test_follow_up_questions_appear_in_place_by_keyboard` | Choosing an answer with the space bar shows its follow-up question in place; focus stays on the choice, what was typed is kept, and the status line says a question appeared |
| `test_a_director_reorders_questions_by_keyboard` | Tab reaches "Move down" and Enter moves the question; the list changes in place, focus stays on the button, and the status line says what moved |
| `test_a_student_answers_a_survey_by_keyboard_without_signing_in` | A student chooses with the space bar, types, Tabs to "Send your answers" and sends with Enter |
| `test_open_times_update_without_reloading` | Changing the date shows that day's times in place, and the live region outside the list announces the new count |
| `test_resource_search_updates_without_reloading` | Typing in the resource search updates the results, and the status line gives the count |
| `test_member_search_updates_results_without_reloading` | The members search updates its results in place |
| `test_the_phone_menu_keeps_focus_inside_until_closed` | On a phone-sized screen, Tab reaches "Menu" and Enter opens the menu with focus on its close button; Tab and Shift+Tab stay inside the menu; Escape closes it, focus returns to "Menu", and nothing on the page is left hidden from screen readers |

Two more browser tests (`test_a_student_books_a_time` and `test_a_dead_survey_link_passes_axe`) check the booking flow with the mouse and the dead-link page with axe.

### Design and code review

For this draft the templates were read for the things a page's structure decides. These are observations from the code, not test results:

- One base layout for every page (`sympraxi/templates/base.html`): `lang` on `<html>`, a skip link to `main`, header, a navigation landmark named "Main", main and footer, and a title of the form "page | site name". The viewport tag doesn't block zooming.
- Every field's label is tied to it. Hints and errors are tied with `aria-describedby`, and a field with an error gets `aria-invalid` (`sympraxi/core/templatetags/sympraxi_forms.py`, `sympraxi/templates/includes/field.html`). Since October 17 that includes checkboxes, and a test checks that `aria-describedby` never names an element missing from the page (`tests/test_field_rendering.py`). Form questions with choices are fieldsets with legends (`sympraxi/templates/forms/_question.html`).
- Booking is a list of times, not a grid (02-build-plan.md §7). The week grid the plan describes isn't built, so neither are its sticky headers.
- Nothing uses dragging. Questions move with Move up and Move down buttons, and options with a Place menu (decisions.md, Phase 1 forms).
- No audio, video, iframes or canvas, and one image: the menu's close icon, which is decorative. The close button's name is hidden text, because USWDS hides the image under forced colors (fixed October 17).
- Sympraxi's own stylesheet (`sympraxi/static/sympraxi/sympraxi.css`) uses only USWDS colors, removes no focus outlines, and has no sticky or fixed positioning, animation, or rules for reduced motion or forced colors. The USWDS stylesheet it builds on has 5 media queries for `prefers-reduced-motion` and about 100 for `forced-colors`. Three of the reduced-motion ones apply to Sympraxi's pages: they stop the mobile menu sliding in, the overlay behind it fading in, and the skip link sliding down when it gets focus.

### USWDS components

Pages use these USWDS 3.14 components as published: header with basic navigation and the mobile menu, skip link, side navigation, breadcrumbs, buttons, form controls (text input, select, textarea, checkbox, radio, fieldset, label, hint, error message), alerts, tags, cards, tables, pagination, search and footer. The USWDS team tests its components for accessibility, but a component used correctly still has to be checked in place. Sympraxi doesn't use the government banner.

## Table 1: Success Criteria, Level A

| Criterion | Level | Remarks |
|---|---|---|
| 1.1.1 Non-text Content | Not Evaluated | The only image, the menu's close icon, is decorative, and its button is named by hidden text; USWDS's other icons come from CSS. axe's image and name rules pass, including with the menu open under forced colors. A reviewer should confirm no icon or color carries meaning without text |
| 1.2.1 Audio-only and Video-only (Prerecorded) | Not Applicable | No audio or video on any page. Phase 2 adds video sessions through Jitsi; evaluate them then |
| 1.2.2 Captions (Prerecorded) | Not Applicable | As 1.2.1 |
| 1.2.3 Audio Description or Media Alternative (Prerecorded) | Not Applicable | As 1.2.1 |
| 1.3.1 Info and Relationships | Not Evaluated | Labels, hints and errors are tied to fields, checkboxes included (fixed October 17; `tests/test_field_rendering.py`); choice questions use fieldsets; data tables use `th` with `scope` (for example the day sheet); axe's structure rules pass |
| 1.3.2 Meaningful Sequence | Not Evaluated | Single-column layouts whose reading order follows the source; on center pages the side navigation comes before the content |
| 1.3.3 Sensory Characteristics | Not Evaluated | No instructions found that depend on shape, position or sound; not checked page by page |
| 1.4.1 Use of Color | Not Evaluated | Design rule: color is never the only signal (02-build-plan.md §7). Statuses are words (alerts, tags). axe's rule for links in text passes in each page's first state |
| 1.4.2 Audio Control | Not Applicable | No audio |
| 2.1.1 Keyboard | Not Evaluated | Every control is a native link, button or field; there are no custom widgets. The keyboard tests above pass. No keyboard-only pass of every page yet |
| 2.1.2 No Keyboard Trap | Not Evaluated | No dialogs of Sympraxi's own; the USWDS mobile menu is the only overlay. It keeps focus inside while open, and Escape closes it and puts focus back on "Menu" (tested by keyboard on a phone-sized screen) |
| 2.1.4 Character Key Shortcuts | Not Applicable | Sympraxi defines no keyboard shortcuts, and no template handles key events |
| 2.2.1 Timing Adjustable | Not Evaluated | **Known issue:** a sign-in lasts 8 hours by default (`SYMPRAXI_SESSION_SECONDS`) with no warning before it ends. A form posted after that goes to the sign-in page, and what was typed, such as a tutor's notes, is lost. A campus can set 72,000 seconds (20 hours) or more to meet the criterion's 20-hour exception. The sign-in steps themselves expire after 15 minutes (allauth's `ACCOUNT_LOGIN_TIMEOUT`), so someone slow to enter a two-factor code starts again. Survey links last 14 days |
| 2.2.2 Pause, Stop, Hide | Not Applicable | Nothing moves, blinks, scrolls or updates by itself. Pages change only when the person acts; no htmx request runs on a timer |
| 2.3.1 Three Flashes or Below Threshold | Not Applicable | Nothing flashes |
| 2.4.1 Bypass Blocks | Not Evaluated | "Skip to main content" is the first link on every page, and pages have landmarks. axe's `bypass` and `region` rules pass |
| 2.4.2 Page Titled | Not Evaluated | Every title is "page \| site name"; axe's `document-title` passes. Whether each describes its page needs a person |
| 2.4.3 Focus Order | Not Evaluated | Source order matches the layout. Where htmx updates part of a page, focus stays where it was (open times, follow-up questions and reordering; the last two are tested). Opening the mobile menu moves focus to its close button, and closing it moves focus back to "Menu" (tested) |
| 2.4.4 Link Purpose (In Context) | Not Evaluated | Repeated "Book" links carry the time and tutor as hidden text (`sympraxi/templates/scheduling/_open_times.html`); links to outside resources say they open in a new tab. axe's `link-name` passes |
| 2.5.1 Pointer Gestures | Not Applicable | Everything works with a single click or tap; there are no multi-finger or path gestures |
| 2.5.2 Pointer Cancellation | Not Evaluated | Native links and buttons, which act when released; Sympraxi adds no pointer handlers |
| 2.5.3 Label in Name | Not Evaluated | Accessible names start with the visible text: "Book" links read "Book 3:00 p.m. with" a tutor's name |
| 2.5.4 Motion Actuation | Not Applicable | Nothing responds to device motion |
| 3.1.1 Language of Page | Not Evaluated | `lang` comes from Django's language setting in `sympraxi/templates/base.html`; axe's `html-has-lang` and `html-lang-valid` pass on every page tested |
| 3.2.1 On Focus | Not Evaluated | No focus handlers |
| 3.2.2 On Input | Not Evaluated | Changing an open-times filter, a choice with follow-ups or a topic, or typing in a search box, updates part of the page and a status line; none of them books, saves or leaves the page. Without JavaScript, a button does the same |
| 3.2.6 Consistent Help | Not Evaluated | Pages have no help link or contact details of their own. A blocked student's notice says whom to write to on the booking and appointments pages; a reviewer should check it sits in the same place on each |
| 3.3.1 Error Identification | Not Evaluated | Field errors appear as text by the field, with `role="alert"`, `aria-invalid` and `aria-describedby`; form-wide errors appear in an alert (`sympraxi/templates/includes/form_fields.html`). A checkbox's error showed nowhere until October 17. axe checks five forms sent back with errors, one of them a checkbox's; other forms' error states aren't checked |
| 3.3.2 Labels or Instructions | Not Evaluated | Every field has a visible label; optional fields say "(optional)", and record fields needed to finalize say so. A checkbox's hint shows under its label (fixed October 17) |
| 3.3.7 Redundant Entry | Not Evaluated | Registration questions are asked once, at the first booking (decisions.md, Phase 1 forms). A booking page that comes back for a follow-up keeps what was typed (tested) |
| 4.1.1 Parsing | Not Applicable | Removed from WCAG 2.2. For a WCAG 2.1 report, W3C's errata treat it as always met for HTML |
| 4.1.2 Name, Role, Value | Not Evaluated | Native controls and USWDS components. At the ends of a list, Move up and Move down stay focusable and are marked `aria-disabled` (decisions.md, Phase 1 forms). axe's name and ARIA rules pass, also after htmx updates, with the mobile menu open, and under emulated forced colors. Under forced colors the menu's close button had no name until October 17 (Known issues). Needs screen-reader checks, especially the mobile menu and the parts htmx updates |

## Table 2: Success Criteria, Level AA

| Criterion | Level | Remarks |
|---|---|---|
| 1.2.4 Captions (Live) | Not Applicable | No live audio or video. Phase 2's video sessions will need this |
| 1.2.5 Audio Description (Prerecorded) | Not Applicable | No video |
| 1.3.4 Orientation | Not Evaluated | Nothing locks orientation; USWDS layouts are responsive |
| 1.3.5 Identify Input Purpose | Not Evaluated | Name fields have `autocomplete="name"` and `"nickname"` (`sympraxi/accounts/forms.py`); allauth's email and password fields have their own. axe's `autocomplete-valid` passes. Questions directors write, for example asking for a phone number, get no `autocomplete` value |
| 1.4.3 Contrast (Minimum) | Not Evaluated | USWDS colors throughout. axe's `color-contrast` passes for text as each page loads, on eight student pages at phone size, after htmx updates on seven pages, and on five forms with errors; hover, focus and selected states aren't checked. axe can't check contrast under forced colors (see "What axe doesn't cover") |
| 1.4.4 Resize Text | Not Evaluated | USWDS sizes in rem; zoom isn't blocked. Needs a 200% check |
| 1.4.5 Images of Text | Not Applicable | No images of text |
| 1.4.10 Reflow | Not Evaluated | USWDS's responsive grid. Eight student pages don't scroll sideways at 375 CSS pixels (tested); nothing is checked at 320. Wide tables, such as the day sheet and the export, may need horizontal scrolling at 320 CSS pixels, which the criterion allows for data tables. Needs a 400% check |
| 1.4.11 Non-text Contrast | Not Evaluated | USWDS field borders and focus ring. axe has no rule for this |
| 1.4.12 Text Spacing | Not Evaluated | No inline styles anywhere (tested), so nothing sets spacing inline with `!important`, which is what axe checks. Whether text clips with wider spacing needs a manual check |
| 1.4.13 Content on Hover or Focus | Not Evaluated | No tooltips or hover menus in the templates; a reviewer should confirm nothing appears on hover or focus |
| 2.4.5 Multiple Ways | Not Evaluated | Center pages are reached from the center page, the side navigation and breadcrumbs. There is no search across the site |
| 2.4.6 Headings and Labels | Not Evaluated | Each page has an `h1`; axe's best-practice heading rules pass. Whether headings and labels describe their content needs a person |
| 2.4.7 Focus Visible | Not Evaluated | USWDS's focus outline; Sympraxi's stylesheet doesn't remove it. axe has no rule for this |
| 2.4.11 Focus Not Obscured (Minimum) | Not Evaluated | No sticky or fixed elements in Sympraxi's stylesheet; the USWDS basic header scrolls with the page. Check with the mobile menu and at high zoom |
| 2.5.7 Dragging Movements | Not Applicable | Nothing uses dragging; reordering uses buttons and a menu (tested by keyboard) |
| 2.5.8 Target Size (Minimum) | Not Evaluated | USWDS buttons and controls; axe's `target-size` rule passes on every page tested, including eight student pages at phone size. Check small links such as "Previous day" and "Next day" on a real phone |
| 3.1.2 Language of Parts | Not Evaluated | The interface is in English. Text people type (notes, questions, center names) may be in another language and isn't marked. A Spanish translation is planned for Phase 5 |
| 3.2.3 Consistent Navigation | Not Evaluated | One base layout for every page; center pages share the side navigation |
| 3.2.4 Consistent Identification | Not Evaluated | The same components and words for the same actions across pages |
| 3.3.3 Error Suggestion | Not Evaluated | Messages say what to fix; every refused booking shows a reason (the load test fails otherwise; decisions.md, Phase 1 load test). Wording needs review |
| 3.3.4 Error Prevention (Legal, Financial, Data) | Not Evaluated | No legal or financial transactions. Booking asks "Book this appointment?" before saving. Canceling, ending an exemption, deleting a closure or hours change, removing a question and lifting a block each go through a confirmation page. A final record can be amended, and the earlier text is kept |
| 3.3.8 Accessible Authentication (Minimum) | Not Evaluated | Nothing on the sign-in page blocks paste or password managers, and its fields carry allauth's `autocomplete` values; passkeys are offered; SSO hands off to the college's own page. A two-factor code has to be read from an authenticator app and typed or pasted; that page passes axe |
| 4.1.3 Status Messages | Not Evaluated | Messages after an action are alerts with `role="status"` (`sympraxi/templates/base.html`). Live regions announce new open times, follow-up questions, reordering, and suggestions and attachments on the record form; the browser tests check their text. Whether NVDA and VoiceOver announce them needs a person |

## Known issues

Found while writing this draft, from the code. None has been confirmed with assistive technology.

1. The 8-hour sign-in ends without a warning, and work in a form posted after it is lost (2.2.1). The two-factor step at sign-in expires after 15 minutes.
2. axe sees error states on five forms, htmx updates on seven pages and a phone-sized screen on eight student pages, and on no others. It can't judge contrast under forced colors at all.

Fixed on October 17, 2026, after the first draft: checkboxes showed no hint or error (1.3.1, 3.3.1, 3.3.2); the two-factor pages and the rest of allauth's pages weren't axe-tested, and adding them showed that the passkey list failed with a server error; error pages were Django's plain ones, without the site's layout or a way back. Later that day, axe on the open phone menu under forced colors found that the menu's close button had no accessible name (4.1.2): its name was the alt text of its X image, which USWDS hides in that mode. The button is now named by hidden text. The other new runs found nothing.

## Plan for the manual review

The Phase 1 exit criteria require a WCAG 2.2 AA review of every student-facing flow that passes (02-build-plan.md §9). The review should cover:

**Flows.** Each one start to finish, as a student:

1. Create an account and confirm the email address; sign in with a password, with a passkey, and through SSO; reset a password.
2. Turn on two-factor login (optional for students), and sign in with a code.
3. Find a time: choose a date, length, tutor and kind of session, and move between days.
4. Book it, answering the registration questions, the intake questions and a follow-up, including a failed submission with errors.
5. My appointments: open one, add it to a calendar, move it, cancel it, cancel late and see the warning.
6. A paused booking: the notice, the reason and whom to write to.
7. My writing: the sessions, their summaries, next steps and resources, and a link out to a resource.
8. Change registration and intake answers.
9. Email reminders and survey settings.
10. Answer a survey from its emailed link, and open a used link.
11. The emails themselves and the calendar file, in a common mail client and calendar.
12. The 404 page a student reaches by an old or wrong link.

**Checks, on every flow:**

| Check | Setup |
|---|---|
| Screen reader on Windows | NVDA, current version, with Firefox and Chrome |
| Screen reader on Apple devices | VoiceOver with Safari on macOS and on iOS |
| Keyboard only | No mouse or touchpad, in Chrome and Firefox: Tab order, visible focus, nothing trapped or hidden behind other content |
| 400% zoom | A 1280-pixel-wide window at 400% (320 CSS pixels): no two-way scrolling except data tables, nothing cut off. Also 200% text-only zoom in Firefox, and WCAG's text-spacing values |
| Reduced motion | The operating system's reduce-motion setting on; confirm nothing animates that matters |
| Forced colors | Windows contrast themes (dark and light) in Edge: every control, focus ring, checkbox, radio button and status still visible |

**Recording.** For each flow and setup: the date, browser, assistive technology and versions, what failed, and which criterion it maps to. Fix what fails, retest, and then replace "Not Evaluated" in the tables above with real levels. After the student flows, the same review should cover the tutor, front desk and director flows, since staff are users too. Each release then ships a finished Accessibility Conformance Report in the VPAT 2.5 format (02-build-plan.md §7).

**Automated checks to add.** On October 17 the axe run gained the remaining allauth pages, five forms after a failed submission, eight student pages at phone size with the menu open, eight states that htmx swaps in, and runs with Playwright's `forced_colors="active"` and `reduced_motion="reduce"` emulation. Still to add, from "What axe doesn't cover": a check for sideways scrolling at 320 CSS pixels on the student pages; the tutor's record form and the front desk's pages at phone size; and a check that each control shows a focus outline when it has focus. These catch regressions between manual reviews; they don't replace them.
