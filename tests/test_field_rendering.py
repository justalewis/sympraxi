"""Every field drawn by includes/field.html shows its hint and error, tied to the input.

WCAG 1.3.1, 3.3.1 and 3.3.2: a hint or error that is on the page but not tied to
its field, or tied to an element that isn't there, doesn't reach a screen reader.
"""

from html.parser import HTMLParser

import pytest
from django import forms
from django.template.loader import render_to_string

HINT = "Once a week, on Mondays."


class Elements(HTMLParser):
    """The rendered elements, by id, and every form control's attributes."""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self.ids: dict[str, dict[str, str | None]] = {}
        self.controls: list[dict[str, str | None]] = []
        self.classes: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids[str(found["id"])] = found
        if tag in ("input", "select", "textarea"):
            self.controls.append(found)
        self.classes.extend(str(found.get("class") or "").split())


class Sample(forms.Form):
    email_me = forms.BooleanField(label="Email me", help_text=HINT)
    plain_box = forms.BooleanField(label="Plain", required=False)
    name = forms.CharField(label="Name", help_text=HINT)
    kind = forms.ChoiceField(label="Kind", choices=[("a", "A")], help_text=HINT)
    notes = forms.CharField(label="Notes", widget=forms.Textarea, help_text=HINT)


def render(form: forms.Form, name: str) -> tuple[str, Elements]:
    markup = render_to_string("includes/field.html", {"field": form[name]})
    return markup, Elements(markup)


def test_a_checkbox_shows_its_hint_tied_to_the_box() -> None:
    markup, page = render(Sample(), "email_me")
    (box,) = page.controls
    assert box["aria-describedby"] == "id_email_me_helptext"
    assert HINT in markup
    assert "id_email_me_helptext" in page.ids
    assert "aria-invalid" not in box


def test_a_checkbox_shows_its_error_tied_to_the_box() -> None:
    form = Sample(data={})
    markup, page = render(form, "email_me")
    (box,) = page.controls
    assert box["aria-invalid"] == "true"
    assert box["aria-describedby"] == "id_email_me_helptext id_email_me_error"
    assert page.ids["id_email_me_error"]["role"] == "alert"
    assert "This field is required." in markup
    assert "usa-form-group--error" in page.classes


def test_a_plain_checkbox_has_nothing_extra() -> None:
    _, page = render(Sample(), "plain_box")
    (box,) = page.controls
    assert "aria-describedby" not in box
    assert "usa-form-group" not in page.classes


@pytest.mark.parametrize("name", list(Sample.base_fields))
@pytest.mark.parametrize("bound", [False, True])
def test_described_by_never_points_at_something_missing(name: str, bound: bool) -> None:
    form = Sample(data={} if bound else None)
    _, page = render(form, name)
    for control in page.controls:
        for target in str(control.get("aria-describedby") or "").split():
            assert target in page.ids, f"{name} points at {target}, which isn't on the page"
