try:
    from config.celery import app as celery_app

    __all__ = ["celery_app"]
except ImportError:  # celery not installed: thread-based dispatch still works
    __all__ = []
