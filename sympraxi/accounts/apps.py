from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "sympraxi.accounts"
    label = "accounts"

    def ready(self) -> None:
        from sympraxi.accounts import signals  # noqa: F401
