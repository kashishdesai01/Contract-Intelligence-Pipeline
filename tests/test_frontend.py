"""The packaged interface is public; contract data remains tenant protected."""

from fastapi.testclient import TestClient

from cip.api import app
from cip.domain import verify_snapshot
from cip.models import Snapshot


def test_frontend_assets_and_security_boundary():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert (
            client.get("/index.html").headers["content-security-policy"]
            == page.headers["content-security-policy"]
        )
        assert "Accord" in page.text
        assert "script-src 'self'" in page.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["referrer-policy"] == "no-referrer"
        for name in ("styles.css", "app.js", "logic.mjs", "favicon.svg", "sample.json"):
            assert client.get("/" + name).status_code == 200
        assert client.get("/v1/snapshots/current").status_code == 401
        assert client.get("/../schema.sql").status_code == 404
        sample = client.get("/sample.json").json()
        snapshot = Snapshot.model_validate(sample["content"])
        assert not verify_snapshot(snapshot)
        assert len(snapshot.families) == 4
        assert any(f.versions[-1].conflicts for f in snapshot.families)
        assert "cip_" not in page.text
