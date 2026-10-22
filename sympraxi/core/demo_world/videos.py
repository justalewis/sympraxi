"""The demo's how-to videos, as scripts.

Each scenario is one task someone does with Sympraxi, written as steps. A step does
one thing on the page (go somewhere, click, type, choose) and carries the caption
shown on screen. `scripts/record_demo_videos.py` plays the steps in a browser and
records the result, so a video is always a true picture of the current software:
change a screen, record again.

The captions double as the transcript on each video's page and as the numbered
answers in the FAQ, so they are written to be read, not only watched.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    do: str  # goto, click, fill, select, check, highlight, scroll, wait or caption
    target: str = ""  # goto: a path; others: "link:Text", "button:Text", "label:Text", "css:..."
    value: str = ""  # fill and select: what to enter or choose
    caption: str = ""  # shown on screen for this step; empty means no caption
    hold: float = 1.0  # seconds to stay after the step, at least; captions add reading time
    optional: bool = False  # skip quietly if the thing isn't on the page (data-dependent steps)


@dataclass(frozen=True)
class Scenario:
    slug: str
    title: str
    audience: str  # student, tutor, frontdesk or director
    persona: str  # email of the sample person
    summary: str
    steps: tuple[Step, ...]
    try_url: str = ""  # where a visitor can do this themselves, after signing in as the persona
    faq: tuple[str, ...] = ()  # ids of FAQ entries that go with the video


PERSONA_NAMES = {
    "student@demo.test": "Sam Student",
    "tutor@demo.test": "Tomás Tutor",
    "frontdesk@demo.test": "Felix Frontdesk",
    "director@demo.test": "Dana Director",
}

AUDIENCES = (
    ("student", "For students"),
    ("tutor", "For tutors"),
    ("frontdesk", "For the front desk"),
    ("director", "For directors"),
)

C = "/centers/demo"


def go(path: str, caption: str = "", hold: float = 1.0) -> Step:
    return Step("goto", path, caption=caption, hold=hold)


def click(target: str, caption: str = "", hold: float = 1.0) -> Step:
    return Step("click", target, caption=caption, hold=hold)


def fill(target: str, value: str, caption: str = "", hold: float = 0.8) -> Step:
    return Step("fill", target, value, caption, hold)


def choose(target: str, value: str, caption: str = "", hold: float = 0.8) -> Step:
    return Step("select", target, value, caption, hold)


def check(target: str, caption: str = "", hold: float = 0.8) -> Step:
    return Step("check", target, caption=caption, hold=hold)


def look(target: str, caption: str = "", hold: float = 1.5) -> Step:
    return Step("highlight", target, caption=caption, hold=hold)


def scroll(target: str, caption: str = "", hold: float = 1.5, optional: bool = False) -> Step:
    return Step("scroll", target, caption=caption, hold=hold, optional=optional)


def note(caption: str, hold: float = 1.0) -> Step:
    return Step("caption", caption=caption, hold=hold)


# {next_weekday} in a path or a typed value becomes the date of the next weekday, so a
# recording made on a Friday evening still books a time that exists.
SAM, TOMAS, FELIX, DANA = (
    "student@demo.test",
    "tutor@demo.test",
    "frontdesk@demo.test",
    "director@demo.test",
)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "book-an-appointment",
        "Book an appointment",
        "student",
        SAM,
        "Choose a schedule, a day and a tutor, tell the center what you need, and you are booked.",
        (
            go(
                C + "/",
                "Sam is a student. This is his page for the center: what is coming up, and what he worked on last.",
                2,
            ),
            click(
                "link:Book an appointment",
                "He wants a tutor this week, so he chooses Book an appointment.",
            ),
            click(
                "link:See open times on Drop-in",
                "Each schedule is a kind of help. Drop-in tutoring takes half hours and hours with peer tutors.",
            ),
            fill("label:Date", "{next_weekday}", "He picks a day."),
            click(
                "button:Show open times",
                "Open times list every tutor's free slots. He can also narrow by length, tutor, or in person versus online.",
            ),
            click("link:with Tomás", "He takes a time with Tomás, who helped him before."),
            fill(
                "label:What course is this for?",
                "ENG 101",
                "The center's intake form asks what he needs, so his tutor can prepare.",
            ),
            check("radio:An essay or paper"),
            check("checkbox:Using sources"),
            fill("label:Anything else", "I want to work on my second section."),
            look("button:Book it", "One click books it."),
            click("button:Book it"),
            note(
                "He gets an email with the details and a calendar file, and a reminder before the visit. He can move or cancel from this page.",
                3,
            ),
        ),
        try_url="/centers/demo/schedules/",
        faq=("how-do-i-book",),
    ),
    Scenario(
        "read-your-session-summary",
        "Read your session summaries and next steps",
        "student",
        SAM,
        "After every visit, your tutor writes you a summary, next steps and resources. They are all in one place.",
        (
            go(
                C + "/",
                "After each visit, Sam's tutor writes him a short summary. This card shows the latest.",
                2,
            ),
            click(
                "link:All your session summaries",
                "My writing keeps every summary, grouped by project, newest first.",
            ),
            look(
                "heading:Argument essay on campus food policy",
                "Sessions are grouped by writing project, so he can see one paper's whole story.",
            ),
            click("link:Your writing across terms", "The timeline shows his writing across terms."),
            note(
                "Sam can also set writing goals, and see who outside the center has looked at his records.",
                3,
            ),
        ),
        try_url="/centers/demo/my-writing/",
        faq=("what-do-students-see",),
    ),
    Scenario(
        "join-the-waiting-list",
        "Join the waiting list",
        "student",
        SAM,
        "No time that suits you? Say when you could come, and the center holds a canceled time for you.",
        (
            go(
                C + "/appointments/",
                "Sam looked at the open times and none of them suit his schedule.",
                2,
            ),
            click(
                "link:Join the waiting list",
                "Instead of checking back all week, he asks for a time.",
            ),
            choose("label:Schedule", "Drop-in tutoring"),
            fill("label:Day", "{next_weekday}", "He says which day he could come."),
            fill("label:No earlier than", "11:00"),
            fill("label:Finishing by", "15:00", "And the window that works for him."),
            choose("label:How long", "30 minutes", "He says how long a visit he needs."),
            click(
                "button:Join the waiting list",
                "If anyone cancels a time that fits, he is emailed and the time is held for him for a few minutes.",
            ),
            note("He can leave the list at any time, and the center can see who is waiting.", 3),
        ),
        try_url="/centers/demo/waiting-list/",
        faq=("waiting-list",),
    ),
    Scenario(
        "register-for-a-workshop",
        "Register for a workshop",
        "student",
        SAM,
        "Workshops are short group sessions with seats. Register in a click.",
        (
            go(
                C + "/workshops/",
                "The center runs short group workshops, on thesis statements, citations and more.",
                2,
            ),
            click(
                "link:Reading like a writer",
                "Sam opens one to see when and where it meets, who leads it, and how many seats are left.",
            ),
            click("button:Register", "One click registers him."),
            note(
                "He gets a confirmation email. If he cannot come, he can cancel and free the seat.",
                3,
            ),
        ),
        try_url="/centers/demo/workshops/",
        faq=("workshops",),
    ),
    Scenario(
        "prepare-for-a-session",
        "Prepare for a session",
        "tutor",
        TOMAS,
        "Before the student arrives, see what they told the center, their goals, and what happened last time.",
        (
            go(
                C + "/tutoring/",
                "Tomás is a peer tutor. This is his schedule: every appointment, by day.",
                2,
            ),
            click("css:main a[href*='/appointments/']", "He opens his next appointment."),
            scroll(
                "heading:Before the session",
                "Before the student arrives, he can see everything they told the center.",
            ),
            scroll(
                "heading:Intake answers",
                "What course the work is for, and what they want to focus on.",
                optional=True,
            ),
            scroll(
                "heading:Writing goals",
                "The goals the student set with the center.",
                optional=True,
            ),
            scroll(
                "heading:Last sessions",
                "And what happened in their last sessions, so he does not start from zero.",
                optional=True,
            ),
            note(
                "All of it is limited to what a tutor may see, and each look is written to the audit log.",
                3,
            ),
        ),
        try_url="/centers/demo/tutoring/",
        faq=("who-sees-what",),
    ),
    Scenario(
        "write-up-a-session",
        "Write up a session",
        "tutor",
        TOMAS,
        "In a few minutes, record what happened: topics, a summary for the writer, next steps and resources.",
        (
            go(
                C + "/records/to-write/",
                "After a visit, the tutor writes it up. This list shows sessions still waiting for a record.",
                2,
            ),
            click("link:Write it for Sam", "Tomás opens the record for Sam's last visit."),
            fill(
                "label:Summary for the student",
                "We worked on bringing your sources into your paragraphs. You practiced introducing two quotations and explaining why each one matters.",
                "The summary is written to the student, who reads it on their My writing page.",
            ),
            fill(
                "label:Next steps",
                "Do the same for the quotations in your second section.",
                "Next steps tell the student what to do before the next visit.",
            ),
            click(
                "text:Ideas and argument",
                "Topics describe what the writing needed, where the writer was in the work, and what happened. They feed the center's reports.",
            ),
            check("label:Thesis, claim or focus"),
            check("label:Revising"),
            check("label:Read aloud"),
            fill("label:Length in minutes", "45"),
            check("label:Yes"),
            note(
                "The center's own report questions come last. The director can change them any time.",
                2,
            ),
            click(
                "button:Save and finalize",
                "Finalizing shows the summary to the student and emails them a link. Later changes are kept as dated amendments.",
            ),
            note(
                "The record is final. The statistics, the topic report and the student's timeline all update.",
                3,
            ),
        ),
        try_url="/centers/demo/records/to-write/",
        faq=("session-records",),
    ),
    Scenario(
        "raise-an-early-alert",
        "Raise an early alert",
        "tutor",
        TOMAS,
        "When you are worried about a student, say why. The right people see it, and it is tracked to a close.",
        (
            go(C + "/records/to-write/", "A tutor has a concern about a student's attendance.", 2),
            click(
                "css:main a[href*='/appointments/']", "Tomás opens a recent visit with the student."
            ),
            click("link:All of", "From the student's page..."),
            click("link:Raise an early alert", "...he can raise an early alert."),
            choose(
                "label:What it's about",
                "Stopped coming or missed sessions",
                "He says what it is about.",
            ),
            fill(
                "label:Why you're raising it",
                "They have not booked for two weeks and said they are thinking of dropping the course.",
                "A reason is required. It is what the director reads, and what the student can ask to see.",
            ),
            check(
                "label:The student knows I'm raising this", "He records whether the student knows."
            ),
            note(
                "Only the director and the people the center chose see the alert, and every step is written to the audit log.",
                3,
            ),
        ),
        try_url="/centers/demo/alerts/mine/",
        faq=("early-alerts",),
    ),
    Scenario(
        "take-a-training-module",
        "Take a training module",
        "tutor",
        TOMAS,
        "Tutor training lives in the same place as the work: modules, readings and quizzes, tracked for you.",
        (
            go(
                C + "/training/",
                "Training sits beside the work. Tomás sees every module his center has published.",
                2,
            ),
            click(
                "link:When a writer is struggling",
                "He opens a module on supporting a struggling writer.",
            ),
            click(
                "link:Listening first",
                "Each module is a few short items: pages, readings and quizzes.",
            ),
            click("link:Next: Check yourself", "He reads the page, then moves to the quiz."),
            note(
                "Quiz results and completions are tracked for the director, who can sign off a new tutor's module.",
                3,
            ),
        ),
        try_url="/centers/demo/training/",
        faq=("training",),
    ),
    Scenario(
        "check-possible-no-shows",
        "Check possible no-shows",
        "frontdesk",
        FELIX,
        "Confirm who came, who did not, and clear any flag, with a note on the record.",
        (
            go(
                C + "/attendance/",
                "Felix runs the front desk. Sessions where nobody marked attendance show up here.",
                2,
            ),
            look(
                "button:Came",
                "Each one has three choices: they came, they did not, or the flag was wrong.",
            ),
            click("button:Came", "He marks the student as having come."),
            note(
                "A confirmed no-show counts toward the center's booking limit, and the student is told. Staff can also waive one with a reason.",
                3,
            ),
            scroll(
                "heading:No-shows and late cancellations",
                "Below is the last 30 days, where a no-show can be waived.",
            ),
        ),
        try_url="/centers/demo/attendance/",
        faq=("no-shows",),
    ),
    Scenario(
        "check-a-student-in",
        "Check a student in at the front desk",
        "frontdesk",
        FELIX,
        "See who is waiting and who is with a tutor, and check a walk-in student in with their ID.",
        (
            go(
                C + "/waiting-room/",
                "The waiting room shows who has checked in, and who is next.",
                2,
            ),
            fill(
                "label:Student ID",
                "S4400114",
                "A student gives their ID, or checks in at the kiosk themselves.",
            ),
            fill(
                "label:What are you working on?",
                "Annotated bibliography",
                "Optionally, what they want help with.",
            ),
            click("button:Check in", "One click checks them in."),
            note("The tutor sees them waiting, and walk-in counts feed the center's reports.", 3),
        ),
        try_url="/centers/demo/waiting-room/",
        faq=("walk-ins",),
    ),
    Scenario(
        "run-an-hours-report",
        "Run an hours report",
        "director",
        DANA,
        "See how many hours you offered and how many were booked, by tutor, schedule and week.",
        (
            go(
                C + "/",
                "Dana directs the center. Her page opens with the numbers, and the things that need her.",
                3,
            ),
            click("link:Hours offered and booked", "She opens the hours report."),
            choose(
                "label:Term",
                "Fall term",
                "Hours are offered by schedule, tutor and week. She picks the term.",
            ),
            click("button:Show"),
            scroll(
                "heading:By tutor",
                "By tutor: hours offered, hours booked, and how full each tutor's time was.",
            ),
            scroll("heading:By week", "And by week, so she can see where demand peaks."),
            look("link:Excel", "Every report downloads as CSV, Excel or JSON."),
            note("The same numbers feed budget requests and the end-of-year report.", 3),
        ),
        try_url="/centers/demo/reports/hours/",
        faq=("reports",),
    ),
    Scenario(
        "read-the-statistics",
        "Read the statistics and break them down by course",
        "director",
        DANA,
        "Appointments, visits, no-shows and topics for a term, and who comes from which courses and years.",
        (
            go(
                C + "/reports/statistics/",
                "The statistics page counts appointments, visits and students for any term.",
                2,
            ),
            scroll("heading:By tutor", "Broken down by tutor..."),
            scroll("heading:By how they met", "...and by how they met: in person or online."),
            go(
                C + "/reports/by-course/",
                "The by-course report shows which courses and class years send students, and what they work on.",
            ),
            note(
                "The center chooses which registration and intake questions the report reads, so it fits its own forms.",
                3,
            ),
        ),
        try_url="/centers/demo/reports/statistics/",
        faq=("reports",),
    ),
    Scenario(
        "handle-an-early-alert",
        "Handle an early alert",
        "director",
        DANA,
        "Read the reason, acknowledge it, and close it with a note about what was done.",
        (
            go(C + "/alerts/", "Early alerts raised by tutors and staff come to the director.", 2),
            click(
                "link:Liam",
                "Dana opens one about a student who has missed two booked sessions and not replied to the reminder.",
            ),
            note("She reads who raised it, why, and whether the student knows.", 2),
            click(
                "button:looking into this",
                "She acknowledges it, so the person who raised it knows it was seen.",
            ),
            note(
                "When it is resolved, she closes it with a note about what was done. Nothing is deleted, and the audit log keeps every step.",
                3,
            ),
        ),
        try_url="/centers/demo/alerts/",
        faq=("early-alerts",),
    ),
    Scenario(
        "approve-timesheets",
        "Approve timesheets",
        "director",
        DANA,
        "Tutors clock in and out; you review hours by person and approve a pay period.",
        (
            go(
                C + "/time-clock/approvals/",
                "Tutors and the front desk clock in and out in the app. The director approves their hours here.",
                2,
            ),
            look("button:Approve Aisha", "Each person's hours for the period are one line."),
            click(
                "button:Approve Aisha",
                "Approving locks the period, so nobody changes hours afterward by accident.",
            ),
            note(
                "Changes to approved hours need a director's reason, and every change is in the audit log. Payroll IDs and pay codes export for HR.",
                3,
            ),
        ),
        try_url="/centers/demo/time-clock/approvals/",
        faq=("time-clock",),
    ),
    Scenario(
        "audit-log-and-export",
        "See who opened what, and take your data with you",
        "director",
        DANA,
        "The audit log records every look at a student's record. A full export gives you everything.",
        (
            go(
                C + "/audit/",
                "Every sign-in, booking, record opened, and export is written to the audit log.",
                2,
            ),
            note("It cannot be edited or deleted by the center. The database itself refuses.", 3),
            go(C + "/reports/export/", "A full export of the center's data is one page away."),
            note("If you ever leave Sympraxi, you take everything with you.", 3),
        ),
        try_url="/centers/demo/audit/",
        faq=("your-data",),
    ),
)
