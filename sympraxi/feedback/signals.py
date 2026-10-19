"""Return a submission when its feedback is final."""

from typing import Any

from django.dispatch import receiver

from sympraxi.feedback import services
from sympraxi.records.signals import record_finalized


@receiver(record_finalized)
def return_submission(sender: Any, record: Any, **kwargs: Any) -> None:
    services.returned_by_record(record)
