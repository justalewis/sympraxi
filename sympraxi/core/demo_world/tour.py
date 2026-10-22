"""The demo's guided tour: for each kind of person, what they do here and where to start.

Each link is a page of the demo center, reached after signing in as that person. A test
signs in as each persona and opens every link, so a page that moves or a permission that
changes can't leave the tour pointing nowhere.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Link:
    name: str  # URL name; "sms:settings" has no center, the rest take the center's slug
    label: str
    note: str


@dataclass(frozen=True)
class Tour:
    key: str
    title: str
    persona: str  # email of the sample person to sign in as
    pitch: str
    links: tuple[Link, ...]


TOURS = (
    Tour(
        "student",
        "If you're a student",
        "student@demo.test",
        "Book in under a minute, and leave each visit with a written summary and next steps you can come back to.",
        (
            Link(
                "scheduling:schedules",
                "Book an appointment",
                "Choose a schedule, a day, a tutor and a length. In person or online.",
            ),
            Link(
                "scheduling:my_appointments",
                "See your appointments",
                "Move or cancel, and add them to your own calendar.",
            ),
            Link(
                "records:my_writing",
                "Read your session summaries",
                "What you worked on, your next steps, and the resources your tutor picked.",
            ),
            Link(
                "records:my_timeline",
                "See your writing over time",
                "Every session, by project, in order.",
            ),
            Link(
                "scheduling:waiting_list",
                "Join the waiting list",
                "No time that suits you? Say when you could come and we hold a cancellation for you.",
            ),
            Link(
                "workshops:list",
                "Register for a workshop",
                "Short group sessions on thesis statements, citations and more.",
            ),
            Link(
                "feedback:submit",
                "Send a draft for written feedback",
                "Upload a draft and get comments back from a tutor.",
            ),
            Link(
                "alerts:shares",
                "Share a visit with your instructor",
                "Send a confirmation that you came, and nothing else.",
            ),
            Link(
                "sms:settings",
                "Turn on text reminders",
                "An opt-in you control, with STOP and HELP.",
            ),
            Link(
                "privacy:my_disclosures",
                "See who has looked at your records",
                "Every time staff opened your information, in plain words.",
            ),
        ),
    ),
    Tour(
        "tutor",
        "If you're a tutor",
        "tutor@demo.test",
        "Your day, your records and your training in one place, with the writer's own words and goals in front of you.",
        (
            Link(
                "scheduling:my_tutoring",
                "See your day",
                "Today's sessions and what each student told us they wanted.",
            ),
            Link(
                "records:to_write",
                "Write up a session",
                "Topics, a summary for the writer, next steps and resources, in a few minutes.",
            ),
            Link("records:new", "Record a walk-in", "A meeting that wasn't booked still counts."),
            Link(
                "records:favorites",
                "Keep your favorite resources",
                "Open-license guides, one click to attach.",
            ),
            Link(
                "feedback:queue",
                "Work the written-feedback queue",
                "Claim a draft and send comments back.",
            ),
            Link(
                "alerts:mine",
                "Raise an early alert",
                "A reason is required, and only the right people see it.",
            ),
            Link(
                "training:home",
                "Take your training",
                "Modules with quizzes, tracked for you and your director.",
            ),
            Link("staff:clock", "Clock in and out", "Your hours, ready for approval."),
        ),
    ),
    Tour(
        "frontdesk",
        "If you run the front desk",
        "frontdesk@demo.test",
        "Greet, check in, fix a booking, and see who might not be coming, without a spreadsheet.",
        (
            Link(
                "staff:waiting_room",
                "Open the waiting room",
                "Who is here, who's next, and who's with a tutor.",
            ),
            Link(
                "staff:kiosk", "Run the check-in kiosk", "Students check themselves in at a tablet."
            ),
            Link(
                "scheduling:attendance_list",
                "Check possible no-shows",
                "Confirm or clear each one, with a reason.",
            ),
            Link(
                "reports:appointment_list",
                "Print the day sheet",
                "Every appointment for a day or a week.",
            ),
            Link(
                "scheduling:waiting_list_manage",
                "Manage the waiting list",
                "See who is waiting and for what.",
            ),
            Link(
                "workshops:list",
                "See who registered for workshops",
                "Rosters, seats left, and attendance after the session.",
            ),
        ),
    ),
    Tour(
        "director",
        "If you direct the center",
        "director@demo.test",
        "Numbers you can put in a report, controls you can explain to your dean, and records that stay yours.",
        (
            Link(
                "reports:utilization",
                "Run an hours report",
                "Hours offered and booked, by tutor and schedule.",
            ),
            Link(
                "reports:statistics",
                "See the term's statistics",
                "Appointments, visits and topics, downloadable as CSV, Excel or JSON.",
            ),
            Link(
                "reports:by_course",
                "Break it down by course and class standing",
                "Who comes, from which courses, and what they work on.",
            ),
            Link(
                "reports:student_list",
                "List students and their visits",
                "A master list you can print or export.",
            ),
            Link(
                "scheduling:blocks",
                "Manage booking blocks",
                "Pauses after repeated no-shows, with the count, the reason and a way to lift them.",
            ),
            Link(
                "news:announcements",
                "Post an announcement",
                "Show a notice to students, staff, or everyone.",
            ),
            Link(
                "alerts:list",
                "Handle early alerts",
                "Acknowledge, follow up and close, with what was done.",
            ),
            Link(
                "staff:entries",
                "Approve timesheets",
                "Review hours by person and lock a pay period.",
            ),
            Link(
                "training:onboarding",
                "Onboard new tutors",
                "Cohorts, mentors, checklists and observations.",
            ),
            Link(
                "forms:list",
                "Edit the forms",
                "Registration, intake, session reports and surveys, with versions.",
            ),
            Link("forms:survey_results", "Read the survey results", "Anonymous, by term."),
            Link(
                "privacy:audit_log", "Read the audit log", "Who opened or changed what, and when."
            ),
            Link(
                "reports:research",
                "Make a de-identified research export",
                "Only inside the dates of your IRB approval.",
            ),
            Link(
                "reports:center_export",
                "Take everything with you",
                "A full export of the center's data.",
            ),
            Link("integrations:home", "Connect other systems", "API tokens and signed webhooks."),
            Link(
                "scheduling:schedules",
                "Set up schedules, terms and rooms",
                "Hours, limits, reminders and who works when.",
            ),
        ),
    ),
)

ARGUMENTS = (
    (
        "Your students' records stay with you",
        "Sympraxi runs on a server your college controls. No company holds your students' sessions, and a full export is one click. If you ever leave, you take everything.",
    ),
    (
        "Free, open source, and no per-seat fees",
        "It is licensed under the AGPL. There is no price per appointment or per tutor, and your IT team can read every line it runs on.",
    ),
    (
        "Made for writing center practice",
        "A calendar is the easy part. Sympraxi records what happened in each session, gives the writer a summary and next steps, tags topics, attaches open-license resources, and tracks writing projects across visits.",
    ),
    (
        "Privacy you can explain to your registrar",
        "One set of permission rules decides who sees what. Every read of a student's record is logged. Surveys are anonymous, research exports are de-identified and tied to your IRB dates, and texts need recorded consent.",
    ),
    (
        "Accessible, and tested for it",
        "Built on the U.S. Web Design System and checked against WCAG 2.2 AA in a real browser on every change. Spanish is included for the pages students meet first.",
    ),
    (
        "It fits your campus",
        "Sign in with Microsoft, Google, any OpenID Connect provider, SAML or CAS. Launch from Canvas, Moodle or D2L. Connect other tools with an API and signed webhooks.",
    ),
    (
        "Small enough to run yourself",
        "One modest server, one install script, nightly backups and a tested restore. A demo like this one runs on a $24-a-month machine.",
    ),
    (
        "Built for the work of a whole center",
        "Waiting lists, workshops, a staff time clock and kiosk, tutor training and onboarding, early alerts with required reasons, and written feedback, all in the same place.",
    ),
)

LIMITS = (
    "Sympraxi is new. It has not yet run at a college, and its sign-in, learning-system and text-message connections have been tested against stand-ins, not live services.",
    "It is not a hosted service: your college, or someone you hire, installs and updates it.",
    "Spanish covers the pages students meet first; everything else is in English for now.",
    "The accessibility checks are automated. A review with screen readers and other assistive technology is still ahead.",
)

STEPS = (
    ("Try it", "Sign in as a few people here. Break nothing; it resets every night."),
    (
        "Show your IT office",
        "Hand them the security questionnaire and accessibility notes that come with the source.",
    ),
    ("Install it", "One script on a small Linux server. Plan on an afternoon."),
    (
        "Bring your history",
        "Import past appointments from a spreadsheet so your first term isn't empty.",
    ),
    ("Go live", "Connect your single sign-on, add your people, and open the schedule."),
)

FEATURED_STUDENTS = {
    "student@demo.test": "Start here. Sam is in ENG 101 working on an essay, has more than a dozen sessions behind and the next ones booked.",
    "diego.martinez@demo.test": "A first-year engineer writing in his second language, with a tutor who focuses on patterns, not corrections.",
    "sofia.rossi@demo.test": "A doctoral student on a dissertation timeline, with a standing exemption from the weekly limit.",
    "tyler.brooks@demo.test": "Anxious about writing, and a tutor has raised an early alert with a reason.",
    "student2@demo.test": "Missed three sessions, so online booking is paused. See what a block looks like for the student and the staff.",
    "omar.farouk@demo.test": "A graduate engineer with a journal article in the written-feedback queue.",
}
