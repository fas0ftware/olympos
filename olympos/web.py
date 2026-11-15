"""The browser interface: python -m olympos serve (127.0.0.1 only)."""

from __future__ import annotations

from pathlib import Path

from .state import State
from .web_base import App

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8746


def create_app(state: State) -> App:
    app = App(STATIC)

    @app.get("/api/state")
    def get_state(query):
        return state.snapshot()

    @app.post("/api/refresh")
    def refresh(body):
        # Re-read everything now and start a GitHub / website check in the background.
        return state.snapshot(force=True)

    return app


def serve(state: State, port: int = DEFAULT_PORT) -> None:
    state.snapshot()   # warm up: starts the GitHub fetch before the first page load
    create_app(state).serve(port, "Olympos")
