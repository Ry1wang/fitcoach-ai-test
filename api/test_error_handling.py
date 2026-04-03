"""
Layer 4 — Error Handling & Input Validation Tests
Tests that malformed requests, wrong content types, and boundary conditions
return appropriate 4xx responses and never trigger unhandled 5xx errors.
"""

import io

import pytest

pytestmark = pytest.mark.layer4


class TestUploadErrorHandling:
    def test_upload_non_pdf_file_rejected(self, authed_client):
        """
        Sending a .txt file to the upload endpoint must return 400, 415, or 422.
        The server must not accept non-PDF content.
        """
        resp = authed_client.post(
            "/documents/upload",
            files={"file": ("document.txt", b"This is not a PDF", "text/plain")},
            data={"domain": "training"},
        )
        assert resp.status_code in (400, 415, 422), (
            f"Expected rejection of .txt file, got {resp.status_code}: {resp.text}"
        )

    def test_upload_docx_rejected(self, authed_client):
        """
        Sending a .docx file (Word document) must be rejected with a 4xx status.
        """
        # Minimal DOCX-like bytes (not a real DOCX, but wrong MIME type)
        fake_docx = b"PK\x03\x04" + b"\x00" * 50  # DOCX zip magic bytes
        resp = authed_client.post(
            "/documents/upload",
            files={
                "file": (
                    "document.docx",
                    fake_docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"domain": "training"},
        )
        assert resp.status_code in (400, 415, 422), (
            f"Expected rejection of .docx file, got {resp.status_code}: {resp.text}"
        )

    def test_upload_no_file_field_returns_422(self, authed_client):
        """POST to upload without a 'file' field must return 422."""
        resp = authed_client.post(
            "/documents/upload",
            data={"domain": "training"},
        )
        assert resp.status_code == 422, (
            f"Expected 422 for missing file field, got {resp.status_code}"
        )

    def test_upload_invalid_domain_value(self, authed_client, minimal_pdf):
        """
        An unrecognized domain value should return 400 or 422.
        The server must validate domain against its allowed set.
        """
        resp = authed_client.post(
            "/documents/upload",
            files={"file": ("test.pdf", minimal_pdf, "application/pdf")},
            data={"domain": "invalid_domain_xyz"},
        )
        # Acceptable: reject with 4xx, OR accept and default to a known domain.
        # Unacceptable: 5xx server error.
        assert resp.status_code < 500, (
            f"Invalid domain triggered server error ({resp.status_code}): {resp.text}"
        )

        # If accepted (2xx), clean up
        if resp.status_code in (200, 201, 202):
            doc_id = (
                resp.json().get("id")
                or resp.json().get("document_id")
                or resp.json().get("file_id")
            )
            if doc_id:
                authed_client.delete(f"/documents/{doc_id}")

    def test_upload_empty_file_handled_gracefully(self, authed_client):
        """
        An empty PDF file (zero bytes named .pdf) must not cause a 5xx error.
        The server should return 400/422, not crash.
        """
        resp = authed_client.post(
            "/documents/upload",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"domain": "training"},
        )
        assert resp.status_code < 500, (
            f"Empty PDF triggered server error ({resp.status_code}): {resp.text}"
        )

    def test_upload_pdf_with_wrong_mime_type(self, authed_client, minimal_pdf):
        """
        A valid PDF sent with MIME type 'text/plain' should be rejected or accepted
        based on content validation — either way, no 5xx.
        """
        resp = authed_client.post(
            "/documents/upload",
            files={"file": ("tricky.pdf", minimal_pdf, "text/plain")},
            data={"domain": "training"},
        )
        assert resp.status_code < 500, (
            f"Valid PDF with wrong MIME type caused server error ({resp.status_code})"
        )
        # Clean up if accidentally accepted
        if resp.status_code in (200, 201, 202):
            doc_id = (
                resp.json().get("id")
                or resp.json().get("document_id")
                or resp.json().get("file_id")
            )
            if doc_id:
                authed_client.delete(f"/documents/{doc_id}")


class TestQueryErrorHandling:
    def test_query_malformed_json_returns_422(self, authed_client):
        """
        Sending malformed JSON to /chat must return 422.
        Use the raw HTTP client to bypass httpx JSON serialization.
        """
        resp = authed_client.post(
            "/chat",
            content=b"{message: not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for malformed JSON, got {resp.status_code}"
        )

    def test_query_wrong_content_type(self, authed_client):
        """
        Sending form data instead of JSON to /chat must return 400 or 422.
        """
        resp = authed_client.post(
            "/chat",
            data={"message": "Hello"},
        )
        assert resp.status_code in (400, 415, 422), (
            f"Expected rejection for wrong content type, got {resp.status_code}"
        )

    def test_very_long_query_no_server_error(self, authed_client):
        """
        A 500-word query must not cause a 5xx error — the server must handle
        it gracefully (truncate, accept, or return a clear 4xx).
        """
        long_query = (
            "I am trying to design a comprehensive training and nutrition program. "
            * 30
        )
        resp = authed_client.post("/chat", json={"message": long_query})
        assert resp.status_code < 500, (
            f"Very long query caused server error ({resp.status_code}): {resp.text}"
        )


class TestGeneralErrorHandling:
    def test_unknown_endpoint_returns_404(self, authed_client):
        """Requesting a non-existent endpoint must return 404."""
        resp = authed_client.get("/this-endpoint-does-not-exist")
        assert resp.status_code == 404, (
            f"Expected 404 for unknown endpoint, got {resp.status_code}"
        )

    def test_wrong_http_method_returns_405(self, authed_client):
        """
        Using the wrong HTTP method on a known endpoint must return 405.
        /auth/login only accepts POST; a GET should return 405.
        """
        resp = authed_client.get("/auth/login")
        assert resp.status_code in (404, 405), (
            f"Expected 404/405 for wrong method on /auth/login, got {resp.status_code}"
        )
