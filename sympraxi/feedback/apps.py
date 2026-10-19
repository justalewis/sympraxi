from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    name = "sympraxi.feedback"
    label = "feedback"
    verbose_name = "Written feedback queue"

    def ready(self) -> None:
        from sympraxi.feedback import signals  # noqa: F401
