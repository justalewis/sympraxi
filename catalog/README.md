# Sympraxi writing resource catalog and topic vocabulary

In Sympraxi, tutors attach writing resources to session records, and topic tags on each session drive resource suggestions and center analytics. This folder holds the shared pieces: the topic vocabulary, the catalog of linked resources, and a script that checks both. Centers add their own handouts and links through the admin screens; those stay in each center's database, not here.

The rules behind the catalog (link, don't copy; never frame a page; respect each source's license and robots rules) are in [docs/03-resource-library.md](../docs/03-resource-library.md).

## License

The topic vocabulary and the catalog metadata are dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). That covers [vocabulary.yaml](vocabulary.yaml), [publishers.yaml](publishers.yaml), [CHANGES.md](CHANGES.md) and everything in [resources/](resources/): slugs, labels, definitions, summaries, topic tags and the other fields. Anyone can reuse them for any purpose without asking.

CC0 doesn't extend to the pages the catalog links to. Each linked page keeps its own license, recorded in its entry, and the catalog stores only a link, the page's title and a summary we wrote. [validate.py](validate.py) is code and falls under Sympraxi's code license, AGPL-3.0-or-later.

Version 1 of the vocabulary was checked against the Writing Center Session Note Data Repository (Modey, Cheatle and Giaimo, University of Michigan Deep Blue Data, 2022, https://doi.org/10.7302/25dd-as06, CC BY-NC 4.0). The vocabulary reproduces no text from it. [CHANGES.md](CHANGES.md) lists what changed from draft 0 and why, quoting a few of the repository's form labels to show where each change came from; those short quotations belong to their authors and aren't part of the CC0 dedication.

## What's in this folder

| File | What it holds |
|---|---|
| `vocabulary.yaml` | Topic vocabulary, version 1: three axes (concern, stage, tutor action), each term with a slug, a label and a one-sentence definition |
| `publishers.yaml` | Every publisher the catalog links to, with the license the docs table gives it and whether its entries are checked automatically or by hand |
| `resources/*.yaml` | Catalog entries, one file per publisher |
| `CHANGES.md` | Changes from draft 0 of the vocabulary, with the reason for each |
| `validate.py` | Checks all of the above |

## The topic vocabulary

A session gets tags on three separate axes, so one catch-all tag can't swallow everything:

- Concern: what the writing needed, such as `argument:thesis`, `evidence:synthesis` or `grammar:articles`. Concerns have a parent and an optional child joined by a colon. Tag the most specific term that fits; tag the parent alone when nothing more specific applies.
- Stage: where the writer was in the work, such as `drafting` or `revising`. Pick every stage that applies.
- Tutor action: what happened in the session, such as `asked-questions` or `modeled`.

Slugs are permanent. Session records and reports from every center use them, so a slug is never renamed or reused. To retire a term, mark it `deprecated: true` with `replaced_by:` naming the term to use instead, and leave it in the file. Labels and definitions can be reworded as long as the meaning stays the same. Centers can add local terms in their own admin screens but can't rename shared ones, so data stays comparable across centers.

## Entry format

Each file in `resources/` is a YAML list. One entry looks like this:

```yaml
- id: purdue-owl-apa-in-text-basics
  title: "In-Text Citations: The Basics (APA)"
  url: https://owl.purdue.edu/owl/research_and_citation/apa_style/apa_formatting_and_style_guide/in_text_citations_the_basics.html
  publisher: Purdue OWL
  license: all-rights-reserved
  can_vendor: false
  link_check: auto
  summary: >-
    How APA's author-and-year citations work inside your sentences: when to add
    a page number, how to handle short quotations and block quotations, what to
    do when a source has no page numbers, and how to cite a paraphrase.
  topics:
    concern: [citation:apa, evidence:quoting]
    stage: [drafting, editing]
  audience: [undergraduate, graduate]
  language: en
  last_checked: 2026-10-14
  last_status: 200
```

| Field | What goes in it |
|---|---|
| `id` | Lowercase words joined by hyphens, starting with the publisher (`unc-`, `purdue-owl-`). Permanent once released, because session records point to it. |
| `title` | The page's own title. Add a short qualifier in parentheses when the bare title is ambiguous, as in "General Format (APA)." |
| `url` | The https address students should open. Never a framed or proxied copy. |
| `publisher` | A `name` from `publishers.yaml`. |
| `license` | The license code `publishers.yaml` gives that publisher, which mirrors the table in docs/03-resource-library.md. Don't record a different license on your own; change the docs table first. |
| `can_vendor` | `true` only when the material, or its `vendor_source`, is CC BY or CC0 and may be copied into the optional content pack. |
| `vendor_source` | Only for publishers that define one (OpenStax): the CC BY copy at a pinned commit, with its license. |
| `link_check` | `auto`, `manual` or `off`. MLA Style Center and Writing Commons ban robots, so their entries are always `manual`. |
| `summary` | 20 to 60 words, written fresh for students. Say what the page helps them do. See "Writing a summary" below. |
| `topics` | `concern` (at least one), `stage`, and optionally `action`, using slugs from `vocabulary.yaml`. |
| `audience` | Any of `undergraduate`, `graduate`, `tutor`. |
| `language` | Two-letter code, such as `en` or `es`. |
| `last_checked`, `last_status` | Date of the last check and the HTTP status it returned. Leave both `null` on a manual entry until a person has opened the page. |
| `license_note`, `notes` | Optional free text. |

### Writing a summary

Read the page, close it, then write the summary from memory. Never paste or lightly reword the page's text or its meta description; `validate.py` compares every summary with the page and fails any that shares a run of eight or more words with it, repeats too many of its five-word sequences, or closely matches its meta description. Address the student as "you," use plain words, and describe what the page covers in the order a student would use it. Skip praise ("a great resource," "essential reading").

## Running the validator

You'll need Python 3.9 or later and PyYAML (`pip install pyyaml`).

```bash
python catalog/validate.py
```

That checks structure, licenses and topic slugs, and compares summaries with any source text already in the local cache. To fetch the source pages and run the full text check:

```bash
python catalog/validate.py --fetch
```

`--fetch` identifies itself, obeys robots.txt and crawl delays (3 seconds for Purdue, 10 for UNC) and never touches `manual` or `off` entries, so a full run takes several minutes. Fetched text lands in `catalog/.cache/`, which is ignored by git; it's third-party text and must never be committed. Add `--checkout PATH` with a local clone of openstax/osbooks-writing-guide to also compare OpenStax summaries with the CC BY snapshot, and `--require-text` to fail when a page couldn't be read. The script exits with status 1 if anything fails, so CI can run it as is.

## Proposing additions

### A new resource

1. Check the publisher. If it's already in `publishers.yaml`, use that license. If it isn't, open an issue first with the publisher's license and terms (a link to the license page and any robots or framing restrictions). New publishers get added to the docs table and `publishers.yaml` before their entries.
2. Skip what can't be linked. Leave out pages behind a login or paywall, and pages whose terms forbid linking.
3. Write the entry in the publisher's file under `resources/`, following the format above. Tag the most specific concern that fits and every stage where a student would reach for the page.
4. Run `python catalog/validate.py --fetch` and fix anything it reports. For a `manual` entry, open the page yourself, confirm the title and summary, and fill in `last_checked` and `last_status`.
5. Open a pull request with a small batch of entries (ten or so) so reviewers can read each summary against its page.

Copies of openly licensed material don't go here. They belong in the optional content pack, with their own licenses and attribution.

### A new vocabulary term

Open an issue with the proposed slug, label and one-sentence definition, two or three examples of sessions it would describe, and why an existing term doesn't cover them. Changes to the shared vocabulary are released in numbered versions with a CHANGES entry, so a director comparing two terms of data knows what moved.

If something here doesn't answer your question, open an issue in the Sympraxi repository and the maintainers will help.
