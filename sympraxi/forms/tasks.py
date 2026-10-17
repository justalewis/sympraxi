"""The survey email, sent by the task worker.

The token in a survey link is made here, at the moment the email is sent, and
only its hash is stored on the invitation. The email goes straight to the
delivery mailer rather than through the queued one: the queue keeps each
message's text in the database, and the link must not be kept anywhere.

Setting the hash and sending happen in one transaction. If the relay refuses
the email, the hash is rolled back and running the task again sends a new link;
if the hash is already set, the email went out and nothing is sent twice.
"""

from django.core.mail import mailers
from django.db import transaction
from django.tasks import task

from sympraxi.forms import surveys
from sympraxi.forms.models import SurveyInvitation, token_hash


@task()
def send_survey_invitation(invitation_id: int, base_url: str) -> None:
    token = surveys.new_token()
    with transaction.atomic():
        updated = SurveyInvitation.objects.filter(pk=invitation_id, token_hash="").update(
            token_hash=token_hash(token)
        )
        if not updated:
            return
        invitation = SurveyInvitation.objects.select_related(
            "student", "appointment__schedule__center", "appointment__tutor"
        ).get(pk=invitation_id)
        message = surveys.invitation_message(invitation, token, url_base=base_url)
        mailers["delivery"].send_messages([message])
