"""Frequently asked questions for the demo site, grouped by who is asking.

Answers say only what Sympraxi does today, including what it does not do yet. An entry
that goes with a how-to video lists the video's slugs, and the page shows that video's steps
(its captions) as a numbered answer, so the FAQ and the videos can't drift apart.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Faq:
    id: str
    group: str
    question: str
    answer: tuple[str, ...]  # paragraphs
    videos: tuple[str, ...] = ()  # slugs from videos.SCENARIOS


GROUPS = (
    ("about", "About Sympraxi"),
    ("student", "For students"),
    ("tutor", "For tutors"),
    ("frontdesk", "For the front desk"),
    ("director", "For directors"),
    ("campus", "For your campus and IT office"),
)

FAQS: tuple[Faq, ...] = (
    # --- About -----------------------------------------------------------------------------
    Faq(
        "what-is-sympraxi",
        "about",
        "What is Sympraxi?",
        (
            "Sympraxi is scheduling and records software for college writing centers. It books appointments and workshops, "
            "records what happens in each session, gives writers a summary and next steps, and gives directors the numbers "
            "they report on.",
            "It is free, open-source software that your college runs on its own server.",
        ),
    ),
    Faq(
        "different-from-a-calendar",
        "about",
        "How is it different from a booking calendar?",
        (
            'A calendar answers "who is coming when." A writing center also needs to know what happened: what the writer worked on, '
            "what they should do next, which resources helped, and how that adds up across a term. Sympraxi keeps session records, "
            "topics, writing projects, goals, surveys, training and staff hours alongside the schedule, with one set of permission rules over all of it.",
        ),
    ),
    Faq(
        "cost",
        "about",
        "What does it cost?",
        (
            "There is no license fee, no price per appointment and no price per tutor. You pay for a server and for someone's time to install and "
            "look after it. A demo like this one runs on a small cloud server for about $24 a month.",
        ),
    ),
    Faq(
        "ready",
        "about",
        "Is it ready to use?",
        (
            "It is complete enough to run a center, and it is new. It has not yet run at a college. Its connections to single sign-on, learning "
            "systems and text messaging have been tested against stand-ins, not live services, and an accessibility review with assistive "
            "technology is still ahead. A pilot center would be the next step.",
        ),
    ),
    Faq(
        "this-demo",
        "about",
        "Is anything on this demo real?",
        (
            "No. The people, sessions, notes and numbers are made up. No email or text message is ever sent from it, and it resets every night, "
            "so feel free to click anything. Please don't type real information.",
        ),
    ),
    # --- Students --------------------------------------------------------------------------
    Faq(
        "how-do-i-book",
        "student",
        "How do I book an appointment?",
        (
            "Choose a schedule, a day and a time, answer a few questions about what you are working on, and book. "
            "You get an email with the details and a calendar file.",
        ),
        ("book-an-appointment",),
    ),
    Faq(
        "cancel-or-move",
        "student",
        "Can I move or cancel an appointment?",
        (
            "Yes, from your appointments page. Each schedule sets how late is too late; canceling inside that window is a late cancellation, "
            "and the center may limit how many a student can have before booking is paused.",
        ),
    ),
    Faq(
        "waiting-list",
        "student",
        "What if there is no time that suits me?",
        (
            "Join the waiting list. You say which day, which hours and how long a visit you need. If someone cancels a time that fits, you are "
            "emailed and the time is held for you for a few minutes.",
        ),
        ("join-the-waiting-list",),
    ),
    Faq(
        "what-do-students-see",
        "student",
        "What do I get after a visit?",
        (
            "Your tutor writes a summary of what you worked on and your next steps, and may attach resources. They appear on your My writing "
            "page, grouped by writing project, with a timeline across terms. Each center chooses whether you also see the tutor's notes for staff.",
        ),
        ("read-your-session-summary",),
    ),
    Faq(
        "workshops",
        "student",
        "Can I join a workshop?",
        (
            "Yes. Workshops are short group sessions with a limited number of seats. Register on the Workshops page and cancel there if you can't come.",
        ),
        ("register-for-a-workshop",),
    ),
    Faq(
        "reminders",
        "student",
        "Will I get reminders?",
        (
            "By email, at times each schedule sets, for example a day and an hour before. Text message reminders are available if your center turns "
            "them on and you opt in. You agree to specific wording, confirm your number with a code, and can reply STOP at any time.",
        ),
    ),
    Faq(
        "share-a-visit",
        "student",
        "Can I show my instructor that I came?",
        (
            "If your center allows it, you can send an instructor a link that confirms the date and that you visited, and nothing else: no tutor "
            "notes, topics or summaries. You can turn the link off.",
        ),
    ),
    # --- Tutors ----------------------------------------------------------------------------
    Faq(
        "prepare",
        "tutor",
        "How do I prepare for a session?",
        (
            "Open the appointment. You see what the writer told the center when they booked, the goals they set, their open writing projects, "
            "and your center's last sessions with them.",
        ),
        ("prepare-for-a-session",),
    ),
    Faq(
        "session-records",
        "tutor",
        "How do I write up a session?",
        (
            "After the visit, open its record from your list of records to write. You tag topics, write a summary and next steps for the writer, "
            "attach resources, and answer your center's report questions. When you finalize it, the writer is emailed a link.",
        ),
        ("write-up-a-session",),
    ),
    Faq(
        "early-alerts",
        "tutor",
        "What is an early alert?",
        (
            "A way to tell the center you are worried about a student: attendance, engagement, well-being. A reason is required. The director and "
            "anyone the center chose can see it, they acknowledge it and close it with a note about what was done, and every step is logged.",
        ),
        ("raise-an-early-alert", "handle-an-early-alert"),
    ),
    Faq(
        "training",
        "tutor",
        "Where is tutor training?",
        (
            "In the same place as the work. A director publishes modules of pages, readings and quizzes, and tracks who has finished them. "
            "New tutors can be placed in a cohort with a mentor, a sign-off checklist and observations.",
        ),
        ("take-a-training-module",),
    ),
    Faq(
        "who-sees-what",
        "tutor",
        "Who can see a student's records?",
        (
            "What a person can see depends on their role and on the appointment, and one set of rules decides it everywhere. Students can ask to see "
            "everything written about them, including notes for staff, so write as if the student will read it. Each time staff open a record, it is "
            "written to the audit log.",
        ),
        ("prepare-for-a-session", "audit-log-and-export"),
    ),
    # --- Front desk ------------------------------------------------------------------------
    Faq(
        "no-shows",
        "frontdesk",
        "How are no-shows handled?",
        (
            "Nothing counts as a no-show until someone confirms it. When nobody marks attendance, the session appears on Attendance to check, and "
            "the front desk says whether the student came. A confirmed no-show tells the student and counts toward the center's limit, after which "
            "online booking can be paused. Staff can waive one with a reason.",
        ),
        ("check-possible-no-shows",),
    ),
    Faq(
        "walk-ins",
        "frontdesk",
        "Can students just walk in?",
        (
            "Yes. Check them in at the waiting room by student ID, or let them use the check-in kiosk. The tutor sees who is waiting, and walk-in "
            "counts feed the center's reports.",
        ),
        ("check-a-student-in",),
    ),
    # --- Directors -------------------------------------------------------------------------
    Faq(
        "reports",
        "director",
        "What reports are there?",
        (
            "Statistics for any term or date range, hours offered and booked by tutor and week, topics by course and class standing, resources tutors "
            "attach, a printable day sheet, student lists and survey results. Each downloads as CSV, Excel or JSON.",
        ),
        ("run-an-hours-report", "read-the-statistics"),
    ),
    Faq(
        "time-clock",
        "director",
        "Can it track staff hours?",
        (
            "Yes. Tutors and the front desk clock in and out, or a director adds hours. Directors approve a pay period, which locks it, and later "
            "changes need a reason and are logged. Payroll IDs and pay codes export for HR.",
        ),
        ("approve-timesheets",),
    ),
    Faq(
        "forms",
        "director",
        "Can we change the questions we ask?",
        (
            "Yes. Registration, intake, session-report and survey forms are all built in the app, with versions, so older answers keep the questions "
            "they were given. You choose which questions the by-course report reads.",
        ),
    ),
    Faq(
        "surveys",
        "director",
        "Are surveys anonymous?",
        (
            "Yes. A student gets a one-time link after a session, and the answer is stored without their name, appointment or record. Results are "
            "counted by term.",
        ),
    ),
    Faq(
        "research",
        "director",
        "Can we use the data for research?",
        (
            "A director can make a de-identified research export, but only inside the dates of the IRB approval recorded for the center, and each export "
            "is logged.",
        ),
    ),
    Faq(
        "your-data",
        "director",
        "What if we want to leave?",
        (
            "Take everything with you. A full export of the center's data is one page away, and nothing is held by a vendor.",
        ),
        ("audit-log-and-export",),
    ),
    # --- Campus ----------------------------------------------------------------------------
    Faq(
        "install",
        "campus",
        "How hard is it to install?",
        (
            "One script on a small Linux server running Docker. Plan on an afternoon, plus time to register your single sign-on. Backups and a tested "
            "restore are included, and an update is one command.",
        ),
    ),
    Faq(
        "sign-in",
        "campus",
        "Can people sign in with their college accounts?",
        (
            "Yes: Microsoft Entra ID, Google Workspace, any OpenID Connect provider, SAML 2.0 and CAS. Staff are required to use two-factor login, "
            "and a college can hide password sign-in once single sign-on works.",
        ),
    ),
    Faq(
        "lms",
        "campus",
        "Does it work with Canvas, Moodle or D2L?",
        (
            "It supports an LTI 1.3 launch, so a student can open it from a course already signed in. It signs people in; it does not yet pass "
            "grades or course rosters back.",
        ),
    ),
    Faq(
        "accessible",
        "campus",
        "Is it accessible?",
        (
            "It is built on the U.S. Web Design System, and every page is checked against WCAG 2.2 AA with automated tests in a real browser on "
            "each change, including phone size, forced colors and reduced motion. A review with screen readers and other assistive technology "
            "has not been done yet, and a conformance report is drafted but not finished.",
        ),
    ),
    Faq(
        "privacy",
        "campus",
        "How does it handle student privacy?",
        (
            "Permissions come from one central rule set tested against every page and role. Every read of a student record is logged in an audit log "
            "that the database itself refuses to alter. Phone numbers are encrypted, surveys are anonymous, and text messages need recorded consent.",
        ),
    ),
    Faq(
        "languages",
        "campus",
        "Is it available in Spanish?",
        (
            "Partly. Sign-in, errors, booking, appointments and account pages are translated, and a person can switch language in the footer. "
            "The rest is in English for now, and the Spanish has not yet been reviewed by a fluent speaker.",
        ),
    ),
    Faq(
        "history",
        "campus",
        "Can we bring our old appointment history?",
        (
            "Yes, from a spreadsheet: a CSV importer reads one past appointment per row, with a preview. A converter for exports from other "
            "scheduling systems is not included yet.",
        ),
    ),
    Faq(
        "integrations",
        "campus",
        "Can other systems connect to it?",
        (
            "Yes. A read and write API with scoped tokens, and signed webhooks that carry ids and never names.",
        ),
    ),
)

BY_ID = {f.id: f for f in FAQS}
