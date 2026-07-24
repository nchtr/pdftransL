from django.apps import AppConfig
from django.db.backends.signals import connection_created


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "PDF Translation API"

    def ready(self) -> None:
        def configure_sqlite(sender, connection, **kwargs):
            if connection.vendor != "sqlite":
                return
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")

        connection_created.connect(
            configure_sqlite, dispatch_uid="pdftransl.sqlite_pragmas", weak=False
        )
