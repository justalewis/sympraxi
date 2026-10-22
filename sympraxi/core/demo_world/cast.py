"""The made-up people of the demo center, and what each one is working on.

Everyone has an address at demo.test. The cast is written to show the range of a real
writing center: first-years and graduate students, multilingual writers, writers
stuck on a first sentence and writers polishing a dissertation, someone who keeps
missing sessions, and tutors with different strengths.
"""

from dataclasses import dataclass, field

from sympraxi.centers.models import Role


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    role: str
    blurb: str = ""


@dataclass(frozen=True)
class StudentProfile:
    email: str
    name: str
    major: str
    year: str
    course: str
    project: str
    instructor: str
    multilingual: bool = False
    first_gen: bool = False
    # Which session templates (library.TEMPLATES keys) this student tends to bring.
    wants: tuple[str, ...] = ()
    # How many sessions this term: 0 = none, higher = a regular.
    visits: int = 3
    # Chance (percent) of missing a booked session.
    misses: int = 5
    student_id: str = ""


STAFF = [
    Person(
        "director@demo.test",
        "Dana Director",
        Role.DIRECTOR,
        "Runs the center: schedules, reports, forms, alerts, hiring and training.",
    ),
    Person(
        "assistant.director@demo.test",
        "Kwame Boateng",
        Role.DIRECTOR,
        "Assistant director for graduate writing and the writing fellows program.",
    ),
    Person(
        "frontdesk@demo.test",
        "Felix Frontdesk",
        Role.FRONT_DESK,
        "Greets writers, books and moves appointments, and confirms attendance.",
    ),
    Person(
        "rosa.delgado@demo.test",
        "Rosa Delgado",
        Role.FRONT_DESK,
        "Evening and weekend front desk; runs the check-in kiosk.",
    ),
    Person(
        "tutor@demo.test",
        "Tomás Tutor",
        Role.TUTOR,
        "Peer tutor: argument, first-year composition, and multilingual writers.",
    ),
    Person(
        "aisha.bello@demo.test",
        "Aisha Bello",
        Role.TUTOR,
        "Graduate tutor: research writing, citations, literature reviews, theses.",
    ),
    Person(
        "marcus.chen@demo.test",
        "Marcus Chen",
        Role.TUTOR,
        "Peer tutor from the sciences: lab reports and technical writing.",
    ),
    Person(
        "lena.fischer@demo.test",
        "Lena Fischer",
        Role.TUTOR,
        "Peer tutor: personal statements, creative writing, and reflection.",
    ),
    Person(
        "jordan.reyes@demo.test",
        "Jordan Reyes",
        Role.TUTOR,
        "Evening online tutor; first-year composition and process coaching.",
    ),
]

# Students, most active first. Sam leads: the demo opens on a student with real history.
STUDENTS = [
    StudentProfile(
        "student@demo.test",
        "Sam Student",
        "Environmental science",
        "Second year",
        "ENG 101",
        "Argument essay on campus food policy",
        "Prof. Okafor",
        wants=(
            "thesis",
            "evidence",
            "organization",
            "citations_apa",
            "revision",
            "intro_conclusion",
            "counterargument",
        ),
        visits=9,
        misses=0,
        student_id="S4400101",
    ),
    StudentProfile(
        "student2@demo.test",
        "Robin Student",
        "Business administration",
        "First year",
        "BUS 110",
        "Memo on a marketing plan",
        "Prof. Lindqvist",
        wants=("assignment", "concision"),
        visits=0,
        misses=100,
        student_id="S4400102",
    ),
    StudentProfile(
        "amara.okafor@demo.test",
        "Amara Okafor",
        "Biology",
        "Third year",
        "BIO 301",
        "Lab report: enzyme kinetics",
        "Dr. Ferreira",
        wants=("lab_report", "citations_apa", "concision", "evidence"),
        visits=7,
        student_id="S4400103",
    ),
    StudentProfile(
        "diego.martinez@demo.test",
        "Diego Martínez",
        "Mechanical engineering",
        "First year",
        "ENG 101",
        "Narrative about a robotics build",
        "Prof. Okafor",
        multilingual=True,
        first_gen=True,
        wants=("articles", "grammar_boundaries", "paragraphs", "thesis"),
        visits=8,
        student_id="S4400104",
    ),
    StudentProfile(
        "mei.lin@demo.test",
        "Mei Lin",
        "Nursing",
        "Second year",
        "NURS 210",
        "Case study reflection (APA)",
        "Prof. Abara",
        multilingual=True,
        wants=("citations_apa", "paraphrasing", "articles", "reflection"),
        visits=6,
        student_id="S4400105",
    ),
    StudentProfile(
        "hassan.ali@demo.test",
        "Hassan Ali",
        "Computer science",
        "Fourth year",
        "CS 490",
        "Capstone design document",
        "Dr. Nguyen",
        wants=("concision", "organization", "revision"),
        visits=4,
        student_id="S4400106",
    ),
    StudentProfile(
        "priya.raman@demo.test",
        "Priya Raman",
        "Psychology",
        "Third year",
        "PSY 330",
        "Literature review on sleep and memory",
        "Dr. Whitfield",
        wants=("lit_review", "research", "citations_apa", "synthesis"),
        visits=6,
        student_id="S4400107",
    ),
    StudentProfile(
        "jamal.washington@demo.test",
        "Jamal Washington",
        "History",
        "Second year",
        "HIST 220",
        "Argument paper on the Civil Rights Act",
        "Prof. Duarte",
        first_gen=True,
        wants=("thesis", "evidence", "counterargument", "citations_chicago"),
        visits=5,
        student_id="S4400108",
    ),
    StudentProfile(
        "sofia.rossi@demo.test",
        "Sofia Rossi",
        "Education (EdD)",
        "Graduate",
        "EDU 810",
        "Dissertation proposal, chapter 2",
        "Dr. Marsh",
        wants=("dissertation", "lit_review", "citations_apa", "concision"),
        visits=8,
        student_id="S4400109",
    ),
    StudentProfile(
        "kofi.mensah@demo.test",
        "Kofi Mensah",
        "Economics",
        "Fourth year",
        "ECON 499",
        "Graduate school statement of purpose",
        "Advisor: Dr. Ibarra",
        wants=("personal_statement", "revision", "intro_conclusion"),
        visits=5,
        student_id="S4400110",
    ),
    StudentProfile(
        "yuki.tanaka@demo.test",
        "Yuki Tanaka",
        "Graphic design",
        "First year",
        "ART 105",
        "Artist statement and process reflection",
        "Prof. Silva",
        multilingual=True,
        wants=("reflection", "articles", "word_choice"),
        visits=4,
        student_id="S4400111",
    ),
    StudentProfile(
        "elena.petrova@demo.test",
        "Elena Petrova",
        "Political science",
        "Third year",
        "POLS 340",
        "Research paper on migration policy",
        "Dr. Haddad",
        wants=("research", "thesis", "citations_apa", "counterargument"),
        visits=5,
        student_id="S4400112",
    ),
    StudentProfile(
        "tyler.brooks@demo.test",
        "Tyler Brooks",
        "Undeclared",
        "First year",
        "ENG 101",
        "Personal essay on a turning point",
        "Prof. Okafor",
        wants=("getting_started", "anxiety", "paragraphs"),
        visits=4,
        misses=25,
        student_id="S4400113",
    ),
    StudentProfile(
        "fatima.zahra@demo.test",
        "Fatima Zahra",
        "Public health",
        "Second year",
        "PH 201",
        "Annotated bibliography on vaccine access",
        "Dr. Reyes-Oduya",
        wants=("annotated_bib", "research", "citations_apa"),
        visits=3,
        student_id="S4400114",
    ),
    StudentProfile(
        "noah.kim@demo.test",
        "Noah Kim",
        "Chemistry",
        "Third year",
        "CHEM 310",
        "Lab report: spectroscopy",
        "Dr. Tran",
        wants=("lab_report", "concision", "evidence"),
        visits=4,
        student_id="S4400115",
    ),
    StudentProfile(
        "isabella.cruz@demo.test",
        "Isabella Cruz",
        "Sociology",
        "Fourth year",
        "SOC 495",
        "Senior thesis, methods section",
        "Dr. Gallo",
        first_gen=True,
        wants=("dissertation", "lit_review", "revision"),
        visits=6,
        student_id="S4400116",
    ),
    StudentProfile(
        "liam.oconnor@demo.test",
        "Liam O'Connor",
        "Criminal justice",
        "Second year",
        "CJ 220",
        "Policy brief on bail reform",
        "Prof. Mbeki",
        wants=("assignment", "organization", "evidence"),
        visits=3,
        misses=15,
        student_id="S4400117",
    ),
    StudentProfile(
        "zainab.hussein@demo.test",
        "Zainab Hussein",
        "Pre-medicine",
        "First year",
        "Pre-health advising",
        "Personal statement for a summer program",
        "Advisor: Dr. Pell",
        multilingual=True,
        first_gen=True,
        wants=("personal_statement", "articles", "intro_conclusion"),
        visits=5,
        student_id="S4400118",
    ),
    StudentProfile(
        "ethan.park@demo.test",
        "Ethan Park",
        "Music",
        "Third year",
        "MUS 301",
        "Program notes for a recital",
        "Prof. Laurent",
        wants=("creative", "concision", "word_choice"),
        visits=3,
        student_id="S4400119",
    ),
    StudentProfile(
        "grace.adeyemi@demo.test",
        "Grace Adeyemi",
        "English",
        "Fourth year",
        "ENGL 480",
        "Creative writing portfolio",
        "Prof. Castellanos",
        wants=("creative", "revision", "word_choice"),
        visits=4,
        student_id="S4400120",
    ),
    StudentProfile(
        "carlos.mendes@demo.test",
        "Carlos Mendes",
        "Accounting",
        "Second year",
        "ACCT 210",
        "Audit memo",
        "Prof. Weber",
        multilingual=True,
        wants=("concision", "grammar_boundaries", "assignment"),
        visits=3,
        student_id="S4400121",
    ),
    StudentProfile(
        "hannah.berg@demo.test",
        "Hannah Berg",
        "Education",
        "Third year",
        "EDU 305",
        "Teaching philosophy statement",
        "Dr. Osei",
        wants=("reflection", "intro_conclusion", "paragraphs"),
        visits=3,
        student_id="S4400122",
    ),
    StudentProfile(
        "omar.farouk@demo.test",
        "Omar Farouk",
        "Mechanical engineering",
        "Graduate",
        "ME 780",
        "Journal article draft",
        "Dr. Lindgren",
        multilingual=True,
        wants=("articles", "concision", "citations_apa", "revision"),
        visits=7,
        student_id="S4400123",
    ),
    StudentProfile(
        "lucia.gomez@demo.test",
        "Lucía Gómez",
        "Anthropology",
        "Second year",
        "ANTH 101",
        "Ethnographic field note essay",
        "Dr. Varga",
        wants=("evidence", "paragraphs", "ai_tools"),
        visits=2,
        misses=20,
        student_id="S4400124",
    ),
]

# Where each tutor works. weekday: 0 = Monday. (start hour, end hour) in the center's time.
# schedule: "dropin" (30 to 60 minutes), "consult" (60 minutes, confirmed by the center),
# or "online" (evenings, video).
TUTOR_HOURS = {
    "tutor@demo.test": (
        "dropin",
        {0: (10, 16), 1: (10, 16), 2: (10, 16), 3: (10, 16), 4: (10, 14)},
    ),
    "aisha.bello@demo.test": ("consult", {0: (12, 18), 2: (12, 18), 3: (12, 18)}),
    "marcus.chen@demo.test": ("dropin", {1: (10, 15), 3: (10, 15)}),
    "lena.fischer@demo.test": ("dropin", {0: (13, 17), 1: (13, 17), 2: (13, 17)}),
    "jordan.reyes@demo.test": ("online", {0: (18, 21), 2: (18, 21), 3: (18, 21)}),
}

STUDENT_BY_EMAIL = {s.email: s for s in STUDENTS}
ALL_PEOPLE_EMAILS = [p.email for p in STAFF] + [s.email for s in STUDENTS]

__all__ = ["STAFF", "STUDENTS", "STUDENT_BY_EMAIL", "TUTOR_HOURS", "Person", "StudentProfile"]
_unused = field  # keep the dataclasses import used for type checkers
