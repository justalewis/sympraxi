"""Mark students with a final session record as having come to their appointment.

A final record means the session happened. From now on `services.finalize`
marks the student as here; this does the same for records finalized before
attendance existed. It changes appointments' participants only, never the
records themselves, so the final-record trigger isn't involved.
"""

from django.db import migrations
from django.db.models import Exists, OuterRef, Subquery


def mark_attended(apps, schema_editor):
    Participant = apps.get_model("scheduling", "Participant")
    SessionRecord = apps.get_model("records", "SessionRecord")
    final = SessionRecord.objects.filter(
        appointment=OuterRef("appointment"), student=OuterRef("student"), status="final"
    )
    Participant.objects.filter(Exists(final), canceled_at__isnull=True, attendance="").update(
        attendance="attended",
        attendance_marked_at=Subquery(final.values("finalized_at")[:1]),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0002_final_records_locked"),
        ("scheduling", "0002_attendance_blocks_reminders"),
    ]

    operations = [
        migrations.RunPython(mark_attended, migrations.RunPython.noop),
    ]
