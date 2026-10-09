"""ASGI entrypoint for the Regent API: ``uvicorn apps.api.main:app``."""

from regent.api.app import app  # noqa: F401
