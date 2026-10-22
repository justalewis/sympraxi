"""What happens in a demo session: topics, summaries, next steps and resources.

Each template is one kind of session. A summary is written to the student, in the
second person, the way a tutor would write it after the visit. The pieces are real
topic slugs and resource IDs from the catalog, so the records, topic reports and
resource reports all fill in. Several variants per template keep the records from
reading as copies.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    concerns: tuple[str, ...]
    stage: str
    actions: tuple[str, ...]
    focus: str  # An option of the session-report question "What did you mostly work on?"
    summaries: tuple[str, ...]
    next_steps: tuple[str, ...]
    staff_notes: tuple[str, ...]
    resources: tuple[tuple[str, str], ...] = ()


IDEAS = "Ideas and argument"
ORG = "Organization"
SOURCES = "Using sources"
STYLE = "Sentences and style"

TEMPLATES: dict[str, Template] = {
    "thesis": Template(
        ("argument:thesis",),
        "revising",
        ("read-aloud", "asked-questions"),
        IDEAS,
        (
            "You came in with a draft and a topic you care about. We read the introduction aloud and found that your claim doesn't appear until the last paragraph. You moved it to the top and sharpened it into something a reader could disagree with.",
            "We spent the session on your thesis. You tried three versions and tested each by asking whether a thoughtful reader could argue the opposite. The third one passed, and the rest of your outline now has a clear job to do.",
        ),
        (
            "Rewrite each topic sentence so it points back to the new thesis.",
            "Draft the paragraph that explains why your claim matters, then bring it back.",
        ),
        (
            "Knows the material well. Needed permission to take a position.",
            "Strong ideas, buried. Used the 'could someone disagree' test and it clicked.",
        ),
        (
            ("purdue-owl-thesis-statement-tips", "Look at the argumentative examples."),
            ("unc-thesis-statements", ""),
        ),
    ),
    "evidence": Template(
        ("evidence:integrating-sources", "evidence:quoting"),
        "revising",
        ("modeled", "writer-practiced"),
        SOURCES,
        (
            "We worked on bringing your sources into your paragraphs. You practiced introducing two quotations with signal phrases and then explaining what each one shows, instead of letting it stand alone.",
            "Your paragraphs had good quotations but moved from quote to quote. You practiced the quote sandwich: set it up, give it, then say what it means for your argument.",
        ),
        (
            "Do the same for the quotations in your second section.",
            "Pick your three strongest quotations and write two sentences of your own after each.",
        ),
        (
            "Good sources. Struggles to say why a quote matters.",
            "Picked it up quickly once we modeled one together.",
        ),
        (("purdue-owl-signal-phrases", ""), ("unc-quotations", "For the explaining part.")),
    ),
    "organization": Template(
        ("organization:structure",),
        "revising",
        ("drew-or-mapped", "made-plan"),
        ORG,
        (
            "You felt the draft jumped around. We made a reverse outline, one sentence per paragraph, and saw that two sections were making the same point and one idea showed up in three places. You decided on a new order.",
            "We mapped your argument on paper and found the order that fits it best. Two paragraphs swapped places and one split in two.",
        ),
        (
            "Rearrange the draft to match the new outline, then reread for transitions.",
            "Write a one-sentence purpose for each section before you move any text.",
        ),
        ("Reverse outline was the turning point.", "Visual learner; liked the index-card version."),
        (("purdue-owl-reverse-outlining", ""), ("purdue-owl-how-to-outline", "")),
    ),
    "citations_apa": Template(
        ("citation:apa",),
        "editing",
        ("pointed-to-resources", "writer-practiced"),
        SOURCES,
        (
            "We went through your reference list entry by entry. You fixed the capitalization of titles, added missing DOIs, and made sure each in-text citation matches an entry in the list.",
            "You brought a draft with citations in three different styles. We settled on APA 7 and you practiced reformatting five of them so the pattern sticks.",
        ),
        (
            "Check the rest of the reference list against the examples we used.",
            "Add page numbers to every direct quotation.",
        ),
        (
            "Needs a quick reference sheet next time.",
            "Reference list was close; mostly punctuation.",
        ),
        (
            ("purdue-owl-apa-in-text-basics", "For the page numbers."),
            ("purdue-owl-apa-reference-list-basics", ""),
        ),
    ),
    "citations_mla": Template(
        ("citation:mla",),
        "editing",
        ("pointed-to-resources",),
        SOURCES,
        (
            "We built your Works Cited page together and matched each entry to a parenthetical citation in the text. You learned how to find the container for a web article.",
        ),
        ("Finish formatting the Works Cited page, using the MLA handout as a guide.",),
        ("First time using MLA 9.",),
        (("purdue-owl-mla-in-text-basics", ""), ("purdue-owl-mla-works-cited-basics", "")),
    ),
    "citations_chicago": Template(
        ("citation:chicago",),
        "editing",
        ("pointed-to-resources", "modeled"),
        SOURCES,
        (
            "History uses footnotes, so we worked on Chicago notes and bibliography. You practiced a first-reference note and a short-form note for the same book.",
        ),
        ("Convert your in-text citations to footnotes and build the bibliography.",),
        ("Confused ibid. with short form; sorted it out.",),
        (("purdue-owl-chicago-general-format", ""),),
    ),
    "grammar_boundaries": Template(
        ("grammar:sentence-boundaries",),
        "editing",
        ("read-aloud", "writer-practiced"),
        STYLE,
        (
            "Reading your draft aloud helped you hear where sentences run together. You found and fixed six comma splices and learned three ways to repair each one.",
        ),
        (
            "Read your next draft aloud before you bring it in, and mark any place you run out of breath.",
        ),
        ("Hears the problem when reading aloud. Don't over-correct; ideas were strong.",),
        (("purdue-owl-run-on-sentences", ""),),
    ),
    "articles": Template(
        ("grammar:articles", "multilingual:language-transfer"),
        "editing",
        ("worked-on-text", "used-another-language"),
        STYLE,
        (
            "We looked at a page of your writing for articles. Instead of memorizing rules you sorted nouns by whether the reader already knows which one you mean, and that helped. We also talked about how this works in your first language.",
            "You asked about when to use 'the'. We worked through two paragraphs together and you found most of the places yourself.",
        ),
        (
            "Highlight every noun in one paragraph and decide whether the reader knows which one you mean.",
            "Keep a short list of the patterns we found and add to it.",
        ),
        (
            "Multilingual writer; strong ideas, wants fluency not a rewrite.",
            "Responds well to patterns over rules.",
        ),
        (("purdue-owl-using-articles", ""), ("openstax-wgh-multilingual-writers", "")),
    ),
    "paragraphs": Template(
        ("organization:paragraphs", "organization:transitions"),
        "revising",
        ("read-aloud", "modeled"),
        ORG,
        (
            "Your paragraphs held several ideas each. We found the main point of each one and split two that were doing double duty. Then you added transitions that say how one idea leads to the next.",
        ),
        ("Check that every paragraph begins with its main point.",),
        ("Long paragraphs; each worth keeping, just needed splitting.",),
        (("openstax-wgh-paragraphs-and-transitions", ""), ("purdue-owl-paragraphing", "")),
    ),
    "lab_report": Template(
        ("genre:lab-report",),
        "drafting",
        ("asked-questions", "modeled"),
        ORG,
        (
            "We went through the sections of a lab report and what belongs in each. You were putting interpretation in your Methods; we moved it to the Discussion and tightened the Results to what you observed.",
            "You brought the results section. We worked on describing a figure in words and on keeping the verbs past tense and the interpretation separate.",
        ),
        (
            "Revise the Discussion so every claim points to a result.",
            "Rewrite the abstract last, in five sentences.",
        ),
        ("Science content is strong; genre conventions were the gap.",),
        (("purdue-owl-conciseness", ""), ("purdue-owl-how-to-outline", "")),
    ),
    "personal_statement": Template(
        ("genre:application-essay",),
        "drafting",
        ("asked-questions", "listened"),
        IDEAS,
        (
            "You had a list of achievements and wanted a story. We talked about the moment that made you choose this path and you drafted an opening scene. It sounds like you now, which is what a reader wants.",
            "We read your statement as an admissions reader would and noticed where you told us what you learned but didn't show it. You picked two moments to expand.",
        ),
        (
            "Expand the opening scene and cut one achievement that doesn't serve the story.",
            "Ask someone who knows you whether it sounds like you.",
        ),
        ("Lots of material. Needed help choosing.", "Anxious about the deadline; made a schedule."),
        (("purdue-owl-personal-statement", ""), ("unc-application-essays", "")),
    ),
    "intro_conclusion": Template(
        ("organization:introductions", "organization:conclusions"),
        "revising",
        ("read-aloud", "made-plan"),
        ORG,
        (
            "We focused on how your paper opens and closes. Your introduction started far from your point, so you cut the first two sentences. Your conclusion repeated the introduction; you rewrote it to say what the reader should take away.",
        ),
        ("Reread the conclusion and ask what changed in the reader by the end.",),
        ("Cut the warm-up sentences; much stronger.",),
        (("unc-introductions", ""), ("uw-madison-conclusions", "")),
    ),
    "lit_review": Template(
        ("genre:literature-review", "research:evaluating-sources"),
        "drafting",
        ("drew-or-mapped", "asked-questions"),
        SOURCES,
        (
            "Your literature review read like a list of summaries. We sorted your sources into three conversations and sketched a structure organized by idea instead of by author. You started rewriting the first section that way.",
        ),
        ("Draft a one-paragraph map of the conversations before you rewrite any section.",),
        ("Synthesis is the skill to build; a matrix helped.",),
        (("purdue-owl-literature-review", ""), ("uw-madison-literature-review", "")),
    ),
    "research": Template(
        ("research:questions", "research:finding-sources"),
        "planning",
        ("asked-questions", "pointed-to-resources"),
        SOURCES,
        (
            "You had a broad topic. We worked it into a question you can answer in the space you have and listed search terms to try in the library databases.",
        ),
        ("Try the search terms we listed and bring three sources you might use.",),
        ("Narrowing was the hard part. Suggested a librarian appointment.",),
        (("excelsior-owl-research-questions", ""), ("purdue-owl-evaluating-sources", "")),
    ),
    "synthesis": Template(
        ("evidence:synthesis",),
        "revising",
        ("modeled", "drew-or-mapped"),
        SOURCES,
        (
            "We practiced putting your sources in conversation with each other, not just with your argument. You wrote two sentences that bring two authors together and say what each adds.",
        ),
        ("Find two other places where two of your sources disagree or build on each other.",),
        ("Ready for the next step in synthesis.",),
        (("purdue-owl-synthesizing-sources", ""),),
    ),
    "concision": Template(
        ("style:concision", "style:clarity"),
        "editing",
        ("read-aloud", "writer-practiced"),
        STYLE,
        (
            "We trimmed a page of your writing. You noticed the phrases that carry no meaning and cut a quarter of the words without losing anything. The ideas read more clearly now.",
        ),
        ("Do one pass looking only for 'there is' and 'it is' openings.",),
        ("Wordy but clear thinking; trimming helped.",),
        (("purdue-owl-conciseness", ""),),
    ),
    "getting_started": Template(
        ("process:getting-started",),
        "generating-ideas",
        ("asked-questions", "listened", "made-plan"),
        IDEAS,
        (
            "You came in with a blank page and a deadline. We talked through what you wanted to say and you freewrote for ten minutes. You left with a paragraph of ideas and a plan for the next three days.",
        ),
        ("Freewrite for ten minutes a day, then underline the sentence you like most.",),
        ("Stuck at the first sentence. Talking first, writing second, worked.",),
        (("purdue-owl-writers-block", ""), ("uw-madison-generating-ideas", "")),
    ),
    "anxiety": Template(
        ("process:writing-anxiety", "process:time-management"),
        "planning",
        ("listened", "made-plan"),
        IDEAS,
        (
            "You said writing feels high-stakes. We broke your assignment into small steps with dates and picked the one to start with tonight. Starting small seemed to help.",
        ),
        ("Do only the first step before our next visit.",),
        ("Anxious about grades. Offer a standing appointment.",),
        (("unc-writing-anxiety", ""), ("purdue-owl-long-assignments", "")),
    ),
    "assignment": Template(
        ("assignment:prompt",),
        "understanding-assignment",
        ("asked-questions", "worked-on-text"),
        IDEAS,
        (
            "You weren't sure what the assignment asked for. We read the prompt together and underlined the verbs, which turned out to be 'compare' and 'recommend'. You wrote a plan matching both.",
        ),
        ("Email your instructor the one question we couldn't answer from the prompt.",),
        ("Prompt wording was ambiguous; suggested asking the instructor.",),
        (
            ("purdue-owl-understanding-writing-assignments", ""),
            ("unc-understanding-assignments", ""),
        ),
    ),
    "paraphrasing": Template(
        ("evidence:paraphrasing", "evidence:plagiarism"),
        "revising",
        ("modeled", "writer-practiced"),
        SOURCES,
        (
            "You were close to the source's wording in several sentences. We practiced paraphrasing by covering the original, explaining the idea in your own words, and then checking back. You revised four sentences that way.",
        ),
        ("Paraphrase two more passages with the source covered, then compare.",),
        ("Worried about plagiarism. Reassured; the method works.",),
        (("purdue-owl-paraphrasing", ""), ("purdue-owl-plagiarism-best-practices", "")),
    ),
    "ai_tools": Template(
        ("process:ai-tools",),
        "planning",
        ("asked-questions", "explained"),
        IDEAS,
        (
            "You asked how AI tools fit into your course. We read your instructor's policy together and talked about what is yours to write and what needs disclosure. You decided on a plan you can describe to your instructor.",
        ),
        ("Ask your instructor the one question the policy leaves open.",),
        ("Check the course syllabus. We do not judge; we help them decide.",),
        (("unc-generative-ai", ""), ("excelsior-owl-ai-and-plagiarism", "")),
    ),
    "counterargument": Template(
        ("argument:counterargument",),
        "revising",
        ("asked-questions", "writer-practiced"),
        IDEAS,
        (
            "Your argument was strong but didn't address anyone who'd disagree. You listed the best objection and drafted a paragraph that takes it seriously and answers it.",
        ),
        ("Place the new paragraph where the objection would first occur to a reader.",),
        ("Good instinct once prompted.",),
        (("about-writing-countering-opposing-arguments", ""), ("purdue-owl-rebuttal-sections", "")),
    ),
    "dissertation": Template(
        ("genre:thesis-dissertation",),
        "drafting",
        ("asked-questions", "made-plan"),
        ORG,
        (
            "We looked at a chapter against what your committee asked for. You outlined the revision as a list of moves and agreed on which to do first.",
            "You brought a section that was nearly there. We worked on signposting, so a reader always knows where the argument is going.",
        ),
        ("Make the revision list into dated steps, then start with the shortest.",),
        ("Graduate writer, mid-project. Keeps momentum with deadlines.",),
        (("purdue-owl-thesis-dissertation-overview", ""),),
    ),
    "revision": Template(
        ("process:using-feedback",),
        "revising",
        ("read-aloud", "made-plan"),
        IDEAS,
        (
            "You had instructor comments and weren't sure where to start. We sorted them into ideas, structure and sentences, and you picked the structural ones first. You left with a revision plan in the order that makes the most difference.",
        ),
        ("Revise in order: ideas, then structure, then sentences.",),
        ("Comments were helpful but overwhelming; sorting them helped.",),
        (("uw-madison-revising-argument", ""), ("uw-madison-seeking-feedback", "")),
    ),
    "creative": Template(
        ("genre:creative",),
        "revising",
        ("read-aloud", "asked-questions"),
        STYLE,
        (
            "We read your piece aloud and listened for where the voice changes. You found two sentences that sound like someone else and rewrote them in your own register.",
        ),
        ("Read the whole piece aloud and mark where your attention drifts.",),
        ("Distinct voice. Let the writer lead.",),
        (),
    ),
    "reflection": Template(
        ("genre:reflection",),
        "reflecting",
        ("asked-questions", "listened"),
        IDEAS,
        (
            "Your reflection described what happened but not what you learned. We asked what changed in you, and you rewrote the middle section to answer that, with one specific moment as evidence.",
        ),
        ("Pick one more moment from your experience and say what it taught you.",),
        ("Good material; needed the 'so what' step.",),
        (("openstax-wgh-portfolio-reflection", ""),),
    ),
    "word_choice": Template(
        ("style:word-choice", "style:tone"),
        "editing",
        ("read-aloud", "worked-on-text"),
        STYLE,
        (
            "We looked at word choice and tone in your draft. You swapped several vague words for exact ones and adjusted the tone to match the reader you're writing for.",
        ),
        ("Replace 'good' and 'thing' wherever you find them.",),
        ("Likes a thesaurus too much; steered toward the precise word.",),
        (("excelsior-owl-academic-voice", ""),),
    ),
    "annotated_bib": Template(
        ("genre:annotated-bibliography",),
        "drafting",
        ("modeled", "writer-practiced"),
        SOURCES,
        (
            "We worked on the three parts of an annotation: what the source says, how reliable it is, and how you'll use it. You rewrote two entries to include all three.",
        ),
        ("Revise the remaining entries so each has summary, evaluation and use.",),
        ("Summaries fine; evaluation was missing.",),
        (("purdue-owl-annotated-bibliographies", ""), ("uw-madison-annotated-bibliography", "")),
    ),
}

GOALS = {
    "thesis": "Put my claim in the first paragraph and make it arguable.",
    "evidence": "Explain every quotation I use in a sentence of my own.",
    "organization": "Check that each paragraph does one job, in an order I can explain.",
    "citations_apa": "Format my reference list correctly the first time.",
    "citations_mla": "Match every in-text citation to a Works Cited entry.",
    "citations_chicago": "Use footnotes without confusing short and full forms.",
    "revision": "Revise in order: ideas, then structure, then sentences.",
    "intro_conclusion": "Open with my point and end by saying why it matters.",
    "counterargument": "Answer the strongest objection to my argument.",
    "articles": "Notice where articles are missing before I hand in a draft.",
    "grammar_boundaries": "Catch run-on sentences by reading aloud.",
    "paragraphs": "Split paragraphs that hold more than one idea.",
    "lab_report": "Keep results and interpretation in their own sections.",
    "personal_statement": "Tell one story that sounds like me.",
    "lit_review": "Organize sources by idea, not by author.",
    "research": "Ask a question I can answer in the space I have.",
    "concision": "Cut a quarter of the words without losing anything.",
    "getting_started": "Start writing before I feel ready.",
    "anxiety": "Break a long assignment into steps with dates.",
    "dissertation": "Make a dated plan for each chapter revision.",
    "creative": "Keep my own voice through revision.",
    "reflection": "Say what changed in me, not only what happened.",
    "word_choice": "Replace vague words with exact ones.",
    "annotated_bib": "Write summary, evaluation and use for every source.",
    "paraphrasing": "Paraphrase with the source covered.",
    "synthesis": "Put two sources in conversation with each other.",
    "assignment": "Read the prompt's verbs before I plan.",
    "ai_tools": "Know what my instructor allows and say what I used.",
}

BROUGHT_DRAFT = {"getting_started": "No", "anxiety": "No", "assignment": "No", "research": "No"}

NEXT_TUTOR_NOTES = (
    "Wants to keep working on this next time.",
    "Bring the assignment sheet.",
    "Would benefit from a standing weekly appointment.",
    "Interested in the workshop on thesis statements.",
    "Prefers working at a screen, not on paper.",
    "",
)

SURVEY_COMMENTS = (
    "I finally understood what my instructor meant by 'develop your argument'.",
    "More evening hours would help.",
    "My tutor asked questions instead of just fixing things. That helped me learn.",
    "I needed help with citations, not ideas.",
    "Loved the reverse outline. I use it for every paper now.",
    "Easy to book and the reminder email was helpful.",
    "The wait was a few minutes but worth it.",
    "Please add Saturday hours.",
    "",
    "",
)

ANNOUNCEMENTS = (
    (
        "Welcome back: fall hours are posted",
        "We are open Monday through Friday this term, with evening online sessions Monday, Wednesday and Thursday. Book through the Appointments page.",
    ),
    (
        "New: workshops on thesis statements and citations",
        "Join us for short group workshops. Seats are limited, so register on the Workshops page.",
    ),
)
