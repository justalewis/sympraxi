"""Print what a person sees on a demo page: headings, links, buttons and form fields.

    python scripts/explore_demo_page.py student@demo.test /centers/demo/schedules/

Arguments after the person are steps: a path to visit, `click:Text`, `fill:Label=value` or
`select:Label=Option`. A helper for writing video scenarios.
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sympraxi.core.demo_world.videos import PERSONA_NAMES  # noqa: E402

BASE = "http://localhost:8010"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(BASE + "/demo/")
    page.get_by_role(
        "button", name=re.compile("Sign in as " + re.escape(PERSONA_NAMES[sys.argv[1]]))
    ).first.click()
    page.wait_for_load_state("domcontentloaded")
    for path in sys.argv[2:]:
        if path.startswith("click:"):
            page.get_by_text(re.compile(re.escape(path[6:]), re.I)).first.click()
            page.wait_for_load_state("domcontentloaded")
        elif path.startswith("fill:"):
            label, _, value = path[5:].partition("=")
            page.get_by_label(re.compile(re.escape(label), re.I)).first.fill(value)
            continue
        elif path.startswith("select:"):
            label, _, value = path[7:].partition("=")
            page.get_by_label(re.compile(re.escape(label), re.I)).first.select_option(label=value)
            continue
        else:
            page.goto(BASE + path)
        print("==", page.url, "|", page.title())
        data = page.evaluate(
            """() => {
              const main = document.querySelector('main') || document.body;
              const t = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
              return {
                h: [...main.querySelectorAll('h1,h2,h3')].map(t),
                a: [...main.querySelectorAll('a[href]')].map(e => t(e) + '  -> ' + e.getAttribute('href')),
                b: [...main.querySelectorAll('button,input[type=submit]')].map(t),
                f: [...main.querySelectorAll('input,select,textarea')].filter(e => e.type !== 'hidden').map(e => {
                  const l = e.labels && e.labels[0] ? t(e.labels[0]) : (e.getAttribute('aria-label') || e.placeholder || '');
                  return e.tagName.toLowerCase() + '[' + (e.type || '') + '] name=' + e.name + ' label=' + l;
                }),
              };
            }"""
        )
        for key, label in (("h", "headings"), ("f", "fields"), ("b", "buttons"), ("a", "links")):
            print(f"-- {label}")
            for item in data[key][:60]:
                print("  ", item.encode("ascii", "replace").decode())
    browser.close()
