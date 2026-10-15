# Topic vocabulary: changes from draft 0 to version 1

Draft 0 is the list in [docs/03-resource-library.md](../docs/03-resource-library.md), section 4. Version 1 is [vocabulary.yaml](vocabulary.yaml), released 2026-10-14.

Version 1 was checked against the Session Note Question Bank (35 session-note forms) and the categorical topic and stage fields of the three session-note datasets in Modey, C. A., Cheatle, J., & Giaimo, G. N. (2022), *Writing Center Session Note Data Repository* [Data set], University of Michigan Deep Blue Data, https://doi.org/10.7302/25dd-as06 (CC BY-NC 4.0). Only those fields were counted; the free-text notes were not read, and no text from the repository appears in the vocabulary. "Form" numbers below refer to the question bank.

## What the data says about the three axes

The datasets show the same catch-all problem the Iowa State study found:

- In Dataset 3, "Proofreading/Editing" is the primary issue in 415 of 1,228 notes (34%).
- In Dataset 1, 242 of the 439 notes with a focus recorded checked "Writing process (planning, drafting, revising, editing)," one box covering four stages.
- In Dataset 2, 290 of the 600 notes with a stage recorded (48%) marked more than one stage or "Mixed." Version 1 lets tutors pick several stages and says so in the axis definition.

Tagging concern, stage and action on separate axes is meant to keep any one box from absorbing a third of all sessions.

## Concern

| Change | Why |
|---|---|
| Every term now has a permanent slug. Child terms use `parent:child`, as in `citation:apa`. | Session records, resource suggestions and analytics all key on slugs, so slugs can't change once released. |
| Added `assignment:format` (length, headings, layout, templates). | Forms 8 ("manuscript format") and 11 ("following assignment format") track it, and draft 0 had nowhere to put it. |
| Relabeled `argument` as "Ideas and argument" and `argument:thesis` as "Thesis, claim or focus." | Form 10 uses "Thesis or Focus." Narratives and reflections have a focus but no thesis. |
| Added `argument:development` (explanation, examples, detail). | Forms 8 and 11 list developing ideas. "Development of Argument or Analysis" is the second most common primary issue in Dataset 3 (197 of 1,228). |
| Moved "finding sources" from evidence to research and merged it with draft 0's "database searching" as `research:finding-sources`. | Two tags for the same activity would split the counts. |
| Added `evidence:summarizing`. | Form 11 pairs summarizing with paraphrasing, and the sources teach quoting, paraphrasing and summarizing as a set. |
| Added `evidence:synthesis`. | Form 8 lists synthesis, and Form 11 lists "incorporating texts." It's the main move in a literature review. |
| Broadened "verb tense" to `grammar:verbs`, "Verb tense and form." | Form 11 lists verb endings and Form 33 lists verbs. Modals and verb forms needed a home. |
| Added `grammar:sentence-structure`, `grammar:pronouns` and `grammar:word-forms`. | Form 11 ("syntax/sentence structure," "parts of speech") and Form 33 ("sentence logic," "noun-pronoun agreement," "plural/singular forms"). |
| Relabeled `multilingual` as "Writing across languages" and defined it as a need in the text, not a description of the writer. Grammar points stay under `grammar`. | If tutors tag every session with a multilingual student, the tag records who came in instead of what the writing needed, and the concern counts mix demographics with writing needs. |
| Merged "personal statement" and "application essay" into `genre:application-essay`, labeled "Personal statement or application essay." | Tutors can't reliably tell them apart, the resources cover them together, and Form 27 adds scholarship applications to the same group. |
| Added `genre:annotated-bibliography`. | Students bring it in often, and every major source in the catalog has a page on it. |
| Added `genre:thesis-dissertation`. | Form 10 lists senior theses (115 of 1,228 sessions in Dataset 3), and graduate centers need it. |
| Added `genre:reflection` (reflection or response papers, portfolio letters). | Form 10 lists reflective and response papers (130 sessions in Dataset 3). |
| Widened `genre:creative` to include personal narrative. | Form 10 lists personal narratives (24 sessions in Dataset 3). |
| Renamed "presentation or multimodal" to `genre:multimodal` and moved spoken delivery to the new `speaking` area. | Designing slides or a video is a different need from rehearsing a talk. |
| Added `research:evaluating-sources`. | Form 31 asks about "finding and/or evaluating resources." |
| Added `reading`, with `reading:comprehension` and `reading:annotation`. | Forms 11, 31 and 33 all have reading sections (comprehension, annotation, note-taking). |
| Added `speaking`, with `speaking:presentation` and `speaking:conversation`. | Forms 27 and 33 track oral presentation, conversation, pronunciation and fluency. |
| Merged "getting started" and "writer's block" into `process:getting-started`. | They're the same need seen from two sides: starting a draft and restarting one. |
| Added `process:writing-anxiety`. | Form 33 lists anxiety as its own learning concern. |
| Added `process:ai-tools` (whether and how to use generative AI within a course's rules, and disclosing it). | It isn't in the question bank, which was collected in 2020. It's added because sessions now often turn on what a course allows, and MLA, UNC and Excelsior all publish guidance on it. |

## Stage

No stages were added or removed. Slugs are `understanding-assignment`, `generating-ideas`, `planning`, `drafting`, `revising`, `editing` (labeled "Editing and proofreading") and `reflecting`. Draft 0's wording "generating ideas" was kept over the build plan's "brainstorming" because it also covers freewriting and choosing a topic. The axis definition now tells tutors to pick every stage that applies (see the Dataset 2 figures above).

## Tutor action

| Change | Why |
|---|---|
| "Student practiced" became `writer-practiced`. | Matches "writer" in the other definitions. |
| "Made a revision plan" became `made-plan`, covering any next steps or schedule. | Forms 5, 24 and 28 ask about next steps or an action plan, not only revision. |
| Added `drew-or-mapped` (outline, reverse outline, idea map, diagram). | Form 25 lists visual strategies, and Form 27 lists outlining and planning. |
| Added `referred-elsewhere` (library, disability office, counseling). | Form 16 asks whether a referral was made and to where. |
| Added `used-another-language`. | Form 33 asks whether a session used more than one language and for what purpose. |

## Considered and left out

- **A higher-order/lower-order flag** (Form 8). It ranks concerns instead of describing them, and Form 8's own wording (lower-order concerns "should only be addressed after" higher-order ones) builds a teaching position into the data. A center that wants the split can map concern parents to the two groups in a report.
- **Study skills** such as note-taking for class and managing a homework load (Forms 11 and 33). These belong to learning centers more than writing centers; a center can add them as local terms.
- **Language test preparation** (Form 27). Same reason.
- **Assignment types** such as research paper, compare/contrast and text analysis (Form 10). They would become catch-alls the way "revising" did. Leave `genre` empty for an ordinary course essay.
- **A "researching" stage.** The research concerns cover it, and a student gathering sources is usually generating ideas or planning.
