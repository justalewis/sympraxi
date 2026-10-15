from django.apps import AppConfig


class PrivacyConfig(AppConfig):
    name = "sympraxi.privacy"
    label = "privacy"
    verbose_name = "Privacy and audit"

    def ready(self) -> None:
        from sympraxi.privacy import signals  # noqa: F401
