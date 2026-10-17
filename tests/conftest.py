import pytest

from app.factory import create_app
from app.printer.mock import MockPrinter
from app.settings import Settings

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "config").mkdir()
    return Settings(mock=True, data_dir=tmp_path / "data", config_dir=tmp_path / "config",
                    host_state_file=tmp_path / "host-state" / "state")


@pytest.fixture
def make_app(settings, clock):
    """make_app(**setting_overrides) -> (flask app, mock printer)"""
    import dataclasses

    def _make(**overrides):
        s = dataclasses.replace(settings, **overrides) if overrides else settings
        printer = MockPrinter(s, clock)
        app = create_app(s, printer=printer, start_background=False, clock=clock)
        return app, printer

    return _make


@pytest.fixture
def client(make_app):
    return make_app()[0].test_client()


def csrf_headers(client):
    token = client.get("/api/csrf").get_json()["token"]
    return {"X-CSRF-Token": token}
