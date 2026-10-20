# The open-content pack

Full text of openly licensed writing guidance that Sympraxi can show a student without
sending them to another site. Tutors attach an item like any other resource. The rules are
in [docs/03-resource-library.md](../docs/03-resource-library.md), section 3, and the loader is
`sympraxi/records/contentpack.py`.

## Licenses

**The code is AGPL-3.0-or-later. The text in this folder is not.** Each item keeps the license
its author gave it, written in the item's file and shown above its text on the page. Nothing
here is released under Sympraxi's code license.

| Folder | What goes in it | Loaded |
|---|---|---|
| `cc-by/` | Material under CC BY or CC0, which may be copied and adapted with credit | Always, by `manage.py load_content_pack` |
| `noncommercial/` | Material with a NonCommercial term, and usually NoDerivatives or ShareAlike as well | Only with `--include-noncommercial`; kept out of the Docker image |

A college running its own copy of Sympraxi may use the noncommercial folder. A company that sells
hosting of Sympraxi should not load it, or should ask each author first. NoDerivatives items
are shown exactly as published and are never shortened, reworded or restyled.

## Layout

```
content-pack/
  cc-by/
    about-writing/          one folder for each source book
      pack.yaml             what the items share: author, source, license
      items/
        thesis-statements.yaml
```

`pack.yaml` holds `id`, `source_title`, `author`, `license`, `license_url`, `retrieved` and
`notes`. An item file holds its own `id` (lowercase letters, digits and hyphens, permanent),
`title`, `source_url` (the page it came from), `summary` (in our own words), `topics` (the
catalog's vocabulary, with at least one concern), `changes` (what was altered, or `none`) and
`body` (HTML). It can override anything from `pack.yaml`. The loader rebuilds `body` from a
short list of elements (paragraphs, headings, lists, emphasis, quotations, tables, code and
http or https links) and drops everything else, so images, scripts and styles never get in.
Images are left out on purpose: they often carry a different license from the text.
