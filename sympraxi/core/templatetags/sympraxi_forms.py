"""Render Django form fields with U.S. Web Design System classes and ARIA wiring."""

from django import forms, template
from django.forms.boundfield import BoundField
from django.utils.safestring import SafeString

register = template.Library()

WIDGET_CLASSES = {
    "checkbox": "usa-checkbox__input",
    "select": "usa-select",
    "textarea": "usa-textarea",
    "input": "usa-input",
    "file": "usa-file-input",
}


@register.filter
def widget_kind(field: BoundField) -> str:
    widget = field.field.widget
    if isinstance(widget, forms.HiddenInput):
        return "hidden"
    if isinstance(widget, forms.CheckboxInput):
        return "checkbox"
    if isinstance(widget, forms.FileInput):
        return "file"
    if isinstance(widget, forms.Select):
        return "select"
    if isinstance(widget, forms.Textarea):
        return "textarea"
    return "input"


@register.filter
def usa_widget(field: BoundField) -> SafeString:
    kind = widget_kind(field)
    if kind == "hidden":
        return field.as_widget()
    classes = [field.field.widget.attrs.get("class", ""), WIDGET_CLASSES[kind]]
    attrs: dict[str, str | bool] = {}
    described_by = []
    if field.help_text:
        described_by.append(f"{field.auto_id}_helptext")
    if field.errors:
        classes.append("usa-input--error")
        attrs["aria-invalid"] = "true"
        described_by.append(f"{field.auto_id}_error")
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    attrs["class"] = " ".join(c for c in classes if c)
    return field.as_widget(attrs=attrs)
