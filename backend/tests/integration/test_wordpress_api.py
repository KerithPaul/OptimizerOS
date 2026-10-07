"""WordPress Application Password API (step 10.1 / 10.7). Password never appears in responses."""

import uuid
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from app.connectors.capabilities import ModificationLevel, wordpress_capabilities
from app.connectors.wordpress.adapters.base import SeoPlugin
from app.connectors.wordpress.detect import WordPressDetection
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.project import Project


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    settings = get_settings()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": settings.seed_user_email, "password": settings.seed_user_password},
    )
    assert resp.status_code == 200
    return client


@pytest.fixture
def project(auth_client):
    resp = auth_client.post(
        "/api/v1/projects", json={"name": f"wp-api-{uuid.uuid4()}"}
    )
    assert resp.status_code == 201
    body = resp.json()
    yield body
    db = SessionLocal()
    try:
        row = db.get(Project, body["id"])
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def test_attach_wordpress_via_website_endpoint_is_rejected(auth_client, project) -> None:
    created = auth_client.post(
        f"/api/v1/projects/{project['id']}/website",
        json={"url": "https://golden-c.example/", "platform": "wordpress"},
    )
    assert created.status_code == 400
    assert "Application Password" in created.json()["detail"]


def test_put_application_password_never_returns_the_secret(auth_client, project) -> None:
    secret = "aaaa bbbb cccc dddd"
    detection = WordPressDetection(
        is_wordpress=True,
        namespaces=["wp/v2", "yoast/v1"],
        seo_plugin=SeoPlugin.YOAST,
        site_name="Golden C",
        revisions_available=True,
    )
    report = wordpress_capabilities(seo_plugin="yoast", revisions_available=True)

    class FakeConnector:
        seo_plugin = SeoPlugin.YOAST

        def authenticate(self) -> None:
            return None

        def get_capabilities(self):
            return report

    with patch(
        "app.api.v1.wordpress.WordPressConnector.from_credentials",
        return_value=FakeConnector(),
    ):
        created = auth_client.put(
            f"/api/v1/projects/{project['id']}/wordpress/connection",
            json={
                "url": "https://golden-c.example/",
                "username": "admin",
                "application_password": secret,
            },
        )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["connected"] is True
    assert body["has_credentials"] is True
    assert body["auth_type"] == "application_password"
    assert body["capabilities"]["source_access"] is False
    assert body["capabilities"]["theme_modification"] == ModificationLevel.LOW.value
    assert body["capabilities"]["seo_modification"] == ModificationLevel.HIGH.value
    assert body["capabilities"]["pull_request"] == ModificationLevel.NONE.value
    assert secret not in created.text
    assert "aaaa" not in created.text
    assert "application_password" not in body
    assert "credentials_encrypted" not in created.text

    fetched = auth_client.get(f"/api/v1/projects/{project['id']}/wordpress")
    assert fetched.status_code == 200
    assert secret not in fetched.text
    del detection

    caps = auth_client.get(f"/api/v1/projects/{project['id']}/capabilities")
    assert caps.status_code == 200
    assert "CREATE_PR" not in caps.json()["allowed_modes"]
    assert "COMMIT" in caps.json()["allowed_modes"]


def test_delete_clears_connection(auth_client, project) -> None:
    report = wordpress_capabilities()

    class FakeConnector:
        seo_plugin = SeoPlugin.YOAST

        def authenticate(self) -> None:
            return None

        def get_capabilities(self):
            return report

    with patch(
        "app.api.v1.wordpress.WordPressConnector.from_credentials",
        return_value=FakeConnector(),
    ):
        auth_client.put(
            f"/api/v1/projects/{project['id']}/wordpress/connection",
            json={
                "url": "https://golden-c.example/",
                "username": "admin",
                "application_password": "xxxx yyyy zzzz aaaa",
            },
        )
    deleted = auth_client.delete(
        f"/api/v1/projects/{project['id']}/wordpress/connection"
    )
    assert deleted.status_code == 200
    assert deleted.json()["has_credentials"] is False
    assert "xxxx" not in deleted.text
