"""Tests for building the application.

The built client is not committed, so whether `static/assets` exists depends
on whether anyone has run `npm run build` — true on a developer's machine,
false in the backend CI job, which deliberately installs no Node.

Both states have to work, and both are covered here rather than left to
whatever happens to be on disk. Otherwise coverage is a different number
depending on the machine.
"""

from fastapi.testclient import TestClient

from app import main


def _mount_paths(app) -> list[str]:
    """Only the mounts. Routes added by include_router are nested inside a
    router object rather than flattened onto app.routes, so this cannot be
    used to look for endpoints."""
    return [route.path for route in app.routes if type(route).__name__ == "Mount"]


def test_the_client_is_served_when_it_has_been_built(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr(main.config, "STATIC_DIR", tmp_path)

    assert "/assets" in _mount_paths(main.create_app())


def test_the_api_still_works_when_the_client_has_not_been_built(tmp_path, monkeypatch):
    """A fresh checkout has no client. The API must not refuse to start."""
    monkeypatch.setattr(main.config, "STATIC_DIR", tmp_path)  # empty

    app = main.create_app()
    assert "/assets" not in _mount_paths(app)

    # Routing still works, and the page route explains itself rather than
    # failing in a way that looks like a broken server. No database is
    # touched, so this needs no lifespan.
    response = TestClient(app).get("/")
    assert response.status_code == 503
    assert "npm" in response.json()["detail"]
