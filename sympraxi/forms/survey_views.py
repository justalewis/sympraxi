"""The survey page a student opens from the emailed link, without signing in.

The link's token is the only thing that opens it. A link that is unknown, used
or expired, or whose survey was stopped, gets the same page with the same
status, so links can't be probed. These views never read who is signed in and
write nothing but the anonymous answers (see surveys.py): no audit event, no
session, no message. Follow-up questions work as on every other answering
page.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy

from sympraxi.forms import surveys
from sympraxi.forms.answering import CONTINUE, Questions, follow_up_message

SUBMIT = gettext_lazy("Send your answers")


def _dead(request: HttpRequest) -> HttpResponse:
    context = {"days": surveys.LINK_DAYS}
    return render(request, "forms/survey_dead.html", context, status=404)


def answer(request: HttpRequest, token: str) -> HttpResponse:
    survey = surveys.open_survey(token)
    if survey is None:
        return _dead(request)
    posted = request.POST if request.method == "POST" else None
    questions = Questions(survey.version, prefix="survey", data=posted)
    status = ""
    if posted is not None:
        if CONTINUE in posted or questions.needs_another_look:
            if CONTINUE not in posted:
                status = follow_up_message(str(SUBMIT))
            questions = questions.again()
        elif questions.is_valid():
            if not surveys.submit(survey, token, questions.values()):
                return _dead(request)
            return redirect("survey:thanks")
    context = {
        "center": survey.center,
        "version": survey.version,
        "questions": questions,
        "form_status": status or questions.status,
        "submit": SUBMIT,
    }
    return render(request, "forms/survey.html", context)


def thanks(request: HttpRequest) -> HttpResponse:
    return render(request, "forms/survey_thanks.html")
