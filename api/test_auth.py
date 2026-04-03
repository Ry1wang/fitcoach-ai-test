"""
Layer 4 — Authentication & Authorization Tests
Tests /auth/login, /auth/register, and that protected endpoints enforce auth.
"""

import uuid

import pytest

pytestmark = pytest.mark.layer4


class TestLogin:
    def test_login_valid_credentials(self, anon_client, auth_token):
        """Sanity-check: a fresh login with correct credentials returns a token."""
        from conftest import TEST_USER

        resp = anon_client.post(
            "/auth/login",
            data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert isinstance(body["access_token"], str)
        assert len(body["access_token"]) > 0

    def test_login_wrong_password(self, anon_client):
        """Wrong password must return 401."""
        from conftest import TEST_USER

        resp = anon_client.post(
            "/auth/login",
            data={"username": TEST_USER["email"], "password": "WrongPassword!"},
        )
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, anon_client):
        """Login for a user that was never registered must return 401."""
        resp = anon_client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "SomePassword1!"},
        )
        assert resp.status_code == 401

    def test_login_missing_password(self, anon_client):
        """Omitting the password field must return 422 (validation error)."""
        from conftest import TEST_USER

        resp = anon_client.post(
            "/auth/login",
            data={"username": TEST_USER["email"]},
        )
        assert resp.status_code == 422

    def test_login_empty_body(self, anon_client):
        """Empty form data must return 422."""
        resp = anon_client.post("/auth/login", data={})
        assert resp.status_code == 422


class TestRegister:
    def test_register_new_user(self, anon_client):
        """Registering a fresh unique user must succeed (200 or 201)."""
        unique_id = uuid.uuid4().hex[:8]
        new_user = {
            "username": f"pytest_{unique_id}",
            "email": f"pytest_{unique_id}@example.com",
            "password": "PytestPass123!",
        }
        resp = anon_client.post("/auth/register", json=new_user)
        assert resp.status_code in (200, 201), (
            f"Registration failed ({resp.status_code}): {resp.text}"
        )

    def test_register_duplicate_email(self, anon_client):
        """Registering with an already-used e-mail must return 400, 409, or 422."""
        from conftest import TEST_USER

        resp = anon_client.post("/auth/register", json=TEST_USER)
        assert resp.status_code in (400, 409, 422), (
            f"Expected a conflict/validation error, got {resp.status_code}: {resp.text}"
        )

    def test_register_missing_email(self, anon_client):
        """Missing e-mail field must return 422."""
        resp = anon_client.post(
            "/auth/register",
            json={"username": "no_email_user", "password": "SomePass123!"},
        )
        assert resp.status_code == 422

    def test_register_missing_password(self, anon_client):
        """Missing password field must return 422."""
        unique_id = uuid.uuid4().hex[:8]
        resp = anon_client.post(
            "/auth/register",
            json={"username": f"u_{unique_id}", "email": f"u_{unique_id}@example.com"},
        )
        assert resp.status_code == 422


class TestProtectedEndpoints:
    def test_documents_list_requires_auth(self, anon_client):
        """GET /documents without a token must return 401."""
        resp = anon_client.get("/documents")
        assert resp.status_code == 401

    def test_documents_list_with_invalid_token(self, anon_client):
        """GET /documents with a bogus token must return 401."""
        resp = anon_client.get(
            "/documents",
            headers={"Authorization": "Bearer this.is.not.a.valid.token"},
        )
        assert resp.status_code == 401

    def test_chat_requires_auth(self, anon_client):
        """POST /chat without a token must return 401."""
        resp = anon_client.post("/chat", json={"message": "Hello"})
        assert resp.status_code == 401

    def test_upload_requires_auth(self, anon_client, minimal_pdf):
        """POST /documents/upload without a token must return 401."""
        resp = anon_client.post(
            "/documents/upload",
            files={"file": ("test.pdf", minimal_pdf, "application/pdf")},
            data={"domain": "training"},
        )
        assert resp.status_code == 401
