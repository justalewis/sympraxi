"""Compile every locale/*/LC_MESSAGES/django.po to django.mo without GNU gettext.

Handles what Sympraxi's files use: msgid/msgstr pairs, multi-line strings, comments and
the header. Plurals (msgid_plural) and contexts (msgctxt) are not used, and refused if met,
so a file that needs them fails loudly instead of compiling wrong.

    python scripts/compile_messages.py            # write the .mo files
    python scripts/compile_messages.py --check    # exit 1 if a .mo is out of date
"""

import ast
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse(text: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    current: list[str] | None = None

    def flush() -> None:
        if msgid is not None and msgstr is not None:
            key, value = "".join(msgid), "".join(msgstr)
            if value:
                messages[key] = value

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("msgid_plural", "msgctxt")):
            raise ValueError(f"line {number}: plurals and contexts aren't supported")
        if line.startswith("msgid "):
            flush()
            msgid, msgstr = [ast.literal_eval(line[6:])], None
            current = msgid
        elif line.startswith("msgstr "):
            msgstr = [ast.literal_eval(line[7:])]
            current = msgstr
        elif line.startswith('"') and current is not None:
            current.append(ast.literal_eval(line))
        else:
            raise ValueError(f"line {number}: can't read {line[:40]!r}")
    flush()
    return messages


def compile_mo(messages: dict[str, str]) -> bytes:
    keys = sorted(messages)
    ids = b""
    strs = b""
    offsets = []
    for key in keys:
        k, v = key.encode("utf-8"), messages[key].encode("utf-8")
        offsets.append((len(ids), len(k), len(strs), len(v)))
        ids += k + b"\0"
        strs += v + b"\0"
    count = len(keys)
    keystart = 7 * 4 + 16 * count
    valuestart = keystart + len(ids)
    key_table, value_table = [], []
    for o1, l1, o2, l2 in offsets:
        key_table += [l1, o1 + keystart]
        value_table += [l2, o2 + valuestart]
    header = struct.pack("Iiiiiii", 0x950412DE, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)
    return (
        header
        + struct.pack(f"{len(key_table)}i", *key_table)
        + struct.pack(f"{len(value_table)}i", *value_table)
        + ids
        + strs
    )


def main(argv: list[str]) -> int:
    stale = 0
    for po in sorted((ROOT / "locale").glob("*/LC_MESSAGES/django.po")):
        built = compile_mo(parse(po.read_text(encoding="utf-8")))
        mo = po.with_suffix(".mo")
        if "--check" in argv:
            if not mo.exists() or mo.read_bytes() != built:
                print(f"out of date: {mo.relative_to(ROOT)}")
                stale = 1
        else:
            mo.write_bytes(built)
            print(f"wrote {mo.relative_to(ROOT)}")
    return stale


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
