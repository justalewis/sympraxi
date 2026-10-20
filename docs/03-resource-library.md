# Writing resource library

Tutors attach writing resources (Purdue OWL pages, open textbook chapters, the center's own handouts) to a session record. The student gets them in their session summary and on their "My writing" page, and the center can see which topics and resources come up over time.

This document covers what we're allowed to do with each source, how the catalog is stored, and a first draft of the topic vocabulary. Licenses and site headers were checked on October 14, 2026. They change, so the link checker and a yearly review should recheck them.

## Rules

1. **Every entry is a link plus our own description.** The catalog stores a URL, the title, a summary written by a contributor, topic tags, the license and the date it was last checked. Every source below permits this.
2. **Never iframe a source.** Purdue's policy is explicit: "Purdue OWL pages may not be presented in frames of any kind." MLA and APA terms also forbid framing, and many writing-center sites send headers that block it anyway. Framing would also load third-party trackers inside our app.
3. **Never copy text from a source that isn't openly licensed,** including its meta descriptions. Write a fresh summary.
4. **Copy openly licensed material only into the separate content pack,** with title, author, source and license recorded for each item, and only within that license's terms (§3).
5. **The automated link checker obeys robots.txt and crawl delays,** and skips sites whose terms ban robots (Writing Commons, MLA). Those entries are checked by hand. The checker's user agent starts with `Mozilla/5.0 (compatible; `, then names the checker (`SympraxiLinkChecker`, in `sympraxi/records/linkcheck.py`; it is off unless an installation turns it on). Pressbooks (openoregon.pressbooks.pub, which hosts *About Writing*) returns 403 to user agents without that prefix.

## 1. Sources and what we may do

"Our summary" means the catalog stores a description we wrote, not the source's own text.

| Source | License | Link | Store | Iframe | Copy or adapt | Notes |
|---|---|---|---|---|---|---|
| **Purdue OWL** (owl.purdue.edu) | All rights reserved, under its own fair-use policy; no commercial use | Yes, deep links allowed without asking | Title and our summary | No | No | Asks, as a courtesy, for an email when you link. Sitemap with about 1,240 URLs and last-modified dates; crawl delay of 3 seconds; no API or RSS |
| **Excelsior OWL** (owl.excelsior.edu) | **CC BY-NC 4.0.** Excelsior confirmed it by email on October 16, 2026, and corrected the footer, which had said CC BY 4.0 | Yes | Yes; WordPress API, sitemap, RSS | Not blocked, but don't | NonCommercial, with attribution | Has a Spanish section |
| **UNC Writing Center handouts** | CC BY-NC-ND 4.0; reproduction must use the whole handout and credit UNC | Yes | Yes; page sitemap only | Not blocked, but don't | Whole and unmodified only, NonCommercial | About 94 handouts and 14 videos. robots.txt blocks AI crawlers and agents by name (ClaudeBot, anthropic-ai, ChatGPT-User, GPTBot and others), disallows `/wp-json/` for every bot it doesn't name, and sets a 10-second crawl delay |
| **UW–Madison Writer's Handbook** | CC BY-NC-SA 4.0 | Yes | Yes; WordPress API, sitemap | Not blocked, but don't | NonCommercial; adaptations must keep the same license | |
| **Writing Commons** | Site all rights reserved; some articles CC BY-NC-ND 4.0 | Yes; terms allow linking | **Manual entry only**; terms ban robots and copying | No | Only the individually licensed articles, verbatim, NonCommercial | Leave out of automated link checks |
| **Writing Spaces**, vols. 1–6 | CC BY-NC-ND 4.0 (older printings 3.0), subject to its own terms of use | Yes; essay PDFs available | Yes | No (blocked) | Verbatim, NonCommercial | Terms-of-use page returned an error; *unverified* |
| **OpenStax *Writing Guide with Handbook*** | **CC BY-NC-SA 4.0 since March 2026.** The previous version in the openstax/osbooks-writing-guide repository (commit a6c9552) is **CC BY 4.0**, and CC licenses can't be revoked | Yes | Yes | Not blocked, but don't | From the a6c9552 snapshot: freely, with attribution. From the current version: NonCommercial, share-alike | OpenStax now also bars using the book to train AI models without permission. Don't use OpenStax or Rice names or logos |
| ***About Writing: A Guide*** (Jeffrey, Open Oregon) | CC BY 4.0 | Yes | Yes | n/a | **Freely, with attribution** | Has a section for multilingual writers; reviewers call it dated |
| ***The Word on College Reading and Writing*** | CC BY-NC (version not confirmed) | Yes | Yes | Unknown | NonCommercial | |
| ***A Guide to Rhetoric, Genre, and Success in First-Year Writing*** | CC BY-NC-SA 4.0, except where noted | Yes | Yes | Not blocked | NonCommercial, share-alike | No longer updated |
| ***Write What Matters*** | CC BY-NC-SA 4.0 (*unverified*; publisher page blocked) | Yes | Yes | Unknown | NonCommercial, share-alike | Some readings licensed chapter by chapter |
| ***Successful College Composition*** | CC BY-NC-SA 4.0 (Galileo); the LibreTexts copy says 3.0 | Yes | Yes | Not blocked | NonCommercial, share-alike | |
| **LibreTexts** (Humanities) | Licensed page by page | Yes | Yes | Not blocked | Check each book's licensing page | |
| ***College ESL Writers*** (Hall and Wallace) | CC BY-NC-SA 4.0 | Yes | Yes | n/a | NonCommercial, share-alike | For multilingual writers |
| ***College Success and Advanced ESL Writing*** (LibreTexts) | CC BY-NC 4.0 | Yes | Yes | n/a | NonCommercial | For multilingual writers |
| **George Mason Writing Center guides** | All rights reserved | Yes | Title and our summary | No (blocked) | No | |
| **Indiana Writing Tutorial Services guides** | All rights reserved | Yes | Title and our summary | No (blocked) | No | |
| **Dartmouth writing materials** | No license stated | Yes | Title and our summary | No (blocked) | No | |
| **Harvard College Writing Center** | All rights reserved (*unverified*) | Yes | Title and our summary | Unknown | No | Fetches were refused |
| **Walden Writing Center** | Unknown | Uncertain | n/a | No | No | Every URL tried redirected to a login page; it may no longer be public |
| **APA Style** (apastyle.apa.org) | Terms bar framing and reproduction (*unverified*; the site requires a bot check) | Yes | Title and our summary | No | No | Automated link checks will be challenged; check by hand |
| **MLA Style Center** (style.mla.org) | MLA Network terms: personal, noncommercial use | Yes | **Manual entry only**; terms ban indexing robots | No | No | |
| **Chicago Manual of Style** | Subscriber terms; no redistribution | Yes | Title and our summary | Unknown | No | Much of the site needs a subscription; prefer the free Citation Quick Guide |

### What the NonCommercial licenses mean for us

Creative Commons says NonCommercial depends on how the material is used, not on who uses it. A nonprofit college running its own copy is fine. A company selling hosted service with NC content inside it may not be. That's why NC, ND and SA material goes in a separate, optional content pack, never in the core repository, and why the hosting guide warns paid hosts about it. Purdue's policy also bans commercial use outright, so a paid host should contact Purdue before including OWL links.

## 2. Catalog format

The catalog is YAML in the repository's `catalog/` folder, so anyone can propose additions or fixes through a pull request. Centers add their own links and uploaded handouts through the admin screens; those stay in their database.

Illustrative entry:

```yaml
- id: purdue-owl-apa-in-text-basics
  title: "In-Text Citations: The Basics"
  url: https://owl.purdue.edu/owl/research_and_citation/apa_style/apa_formatting_and_style_guide/in_text_citations_the_basics.html
  publisher: Purdue OWL
  license: all-rights-reserved     # SPDX-style code or "all-rights-reserved"
  can_vendor: false                # true only for CC BY / CC0 material
  link_check: auto                 # auto | manual | off
  summary: >-
    How to cite a source inside your own sentences in APA 7: author and year,
    page numbers for quotations, and when to name the author in the sentence.
  topics:
    concern: [citation:apa]
    stage: [revising, editing]
  audience: [undergraduate, graduate]
  language: en
  last_checked: 2026-10-14
  last_status: 200
```

Validation in CI rejects entries with no license, no summary, or a summary that matches the source page's own text.

### Finding pages to catalog

| Source | Discovery feed |
|---|---|
| Purdue OWL | `owl.purdue.edu/sitemap.xml` (use last-modified dates to spot changed pages) |
| Excelsior, UW–Madison | WordPress REST API (`/wp-json/wp/v2/pages`), sitemaps, RSS. The oEmbed responses include staff author names; don't display them |
| UNC | `writingcenter.unc.edu/page-sitemap.xml`. robots.txt disallows `/wp-json/`, so don't use the WordPress API |
| Open Textbook Library | `open.umn.edu/opentextbooks/textbooks/<slug>.json`, which includes a license field |
| OpenStax | The book's GitHub repository |
| Writing Commons, MLA | None allowed; enter by hand |

A script can pull candidate titles and URLs from these feeds into a review queue. A person still writes the summary and assigns topics before an entry goes live.

## 3. Content pack

The optional content pack holds full copies of openly licensed material so a center can attach a handout that lives on its own server. It lives in this repository, in `content-pack/`, in two folders so the line between them is plain: `cc-by/` (CC BY and CC0, loaded by every installation) and `noncommercial/` (anything with a NonCommercial term, loaded only with `--include-noncommercial` and left out of the Docker image). The code is AGPL; the text in the pack keeps each item's own license. Each item carries its author, source, license and a note of what changed, and its page shows that credit above the text. See `content-pack/README.md`.

- **CC BY** material (*About Writing*, the CC BY OpenStax snapshot) can be adapted: shortened, re-sequenced, or rewritten for a center's students. Each item keeps its attribution.
- **NoDerivatives** material (UNC, Writing Spaces, some Writing Commons articles) can only be included whole and unchanged.
- **ShareAlike** material can be adapted, but the adaptation must carry the same license.
- **NonCommercial** material is fine for colleges running their own copy. The pack's README says so plainly.

The first release doesn't need the content pack. Links cover the quick-win version of this feature. The mechanism is built (October 2026); the pack ships with twenty sections of the OpenStax *Writing Guide with Handbook* (the CC BY snapshot) and grows only from sources whose license has been checked at the item.

Checked on October 20, 2026: the license file of the OpenStax *Writing Guide with Handbook* at commit a6c9552 is CC BY 4.0 (the repository's current README says CC BY-NC-SA, so only that commit may be used as CC BY). Writing Spaces essays default to CC BY-NC-ND (3.0 on the volume 1 and 2 front matter, 4.0 on volumes 3 and 4) under the Writing Spaces Terms of Use, and an author can choose a different license, so each essay's own license page decides; volumes 5 and 6 and the Terms of Use itself were not confirmed. Because of NoDerivatives, a Writing Spaces essay could only be included whole and unchanged, and that tier is not in the first pack.

## 4. Topic vocabulary, draft 0

No shared writing-center topic vocabulary exists. Researchers have criticized the checkbox lists centers use now. An Iowa State study found about 4,000 of 6,000 session notes marked simply "revising." So this vocabulary splits topics into three separate axes, and a tutor tags each one.

**Concern** (what the writing needed)

- assignment: understanding the prompt, audience, genre conventions
- argument: thesis or claim, counterargument, analysis
- organization: overall structure, paragraphing, transitions, introductions, conclusions
- evidence: finding sources, integrating sources, quoting and paraphrasing, avoiding plagiarism
- citation: apa, mla, chicago, other
- style: clarity, concision, tone and register, word choice
- grammar: sentence boundaries, verb tense, subject-verb agreement, articles, prepositions, punctuation
- multilingual: language transfer, idioms, academic vocabulary
- genre: literature review, lab report, personal statement, application essay, resume or cover letter, creative writing, presentation or multimodal
- research: narrowing a topic, research questions, database searching
- process: time management, getting started, writer's block, using feedback

**Stage** (where the student is): understanding the assignment, generating ideas, planning, drafting, revising, editing and proofreading, reflecting after feedback or a grade.

**Tutor action** (what happened in the session): asked questions, listened while the student talked through ideas, read aloud, explained a concept, modeled a revision, student practiced, made a revision plan, pointed to resources, worked directly on the text.

Next steps for the vocabulary:

1. Check it against the session-note question bank in the **Writing Center Session Note Data Repository** (Modey, Cheatle and Giaimo, University of Michigan Deep Blue Data, 2022, DOI 10.7302/25dd-as06, CC BY-NC 4.0). The repository holds notes from 63 institutions and was funded by IWCA. Deep Blue's robots.txt blocks ClaudeBot, anthropic-ai and Claude-Web, so the files were downloaded by hand on October 14, 2026.
2. Circulate it to a few writing-center directors for comments.
3. Release version 1 under CC0, citing the repository.

Possible outside frameworks for the top level, such as the WPA Outcomes Statement or CWPA's "Five Knowledge Domains," have licenses that haven't been checked.

## 5. Open questions

- Writing Spaces terms of use (the page returned an error).
- APA terms of use (behind a bot check).
- License version for *The Word on College Reading and Writing*.
- Whether Walden's writing center is still public.
- Purdue's courtesy email about linking, now that the project has a name.

## Sources

Purdue OWL fair use policy, FAQ, sitemap and robots.txt; Excelsior OWL; UNC Writing Center tips and tools; UW–Madison Writer's Handbook; Writing Commons and its terms of service; Writing Spaces and the WAC Clearinghouse volume pages; OpenStax *Writing Guide with Handbook* and the openstax/osbooks-writing-guide repository; Cleveland State Pressbooks; Open Textbook Library listings and JSON; Galileo Open Learning Materials; LibreTexts Humanities; George Mason, Indiana and Dartmouth writing pages; MLA Network terms of use; Chicago Manual of Style terms of use; Creative Commons FAQ; University of Michigan Deep Blue Data (DOI 10.7302/25dd-as06); DeKruif and Smith, Iowa State (NSF PAR manuscript); Writing Centers Research Project survey. HTTP headers were checked on October 14, 2026 for every site in the table.
