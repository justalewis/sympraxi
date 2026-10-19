"""What other apps may listen for."""

from django.dispatch import Signal

# Sent inside the transaction that makes a record final, with `record=`.
record_finalized = Signal()
