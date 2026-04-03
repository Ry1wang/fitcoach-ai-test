"""
Layer 4 — Document Upload Endpoint Tests
Tests POST /documents/upload and GET /documents.
"""

import pytest

pytestmark = pytest.mark.layer4

VALID_DOMAINS = ["training", "rehab", "nutrition"]


class TestUploadHappyPath:
    def test_upload_minimal_pdf_returns_doc_id(self, authed_client, minimal_pdf):
        """Valid PDF upload must return 2xx with a document ID."""
        resp = authed_client.post(
            "/documents/upload",
            files={"file": ("test_minimal.pdf", minimal_pdf, "application/pdf")},
            data={"domain": "training"},
        )
        assert resp.status_code in (200, 201, 202), (
            f"Expected 2xx, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "id" in body or "document_id" in body or "file_id" in body, (
            f"Response missing document ID field: {body}"
        )

        # Clean up
        doc_id = body.get("id") or body.get("document_id") or body.get("file_id")
        if doc_id:
            authed_client.delete(f"/documents/{doc_id}")

    @pytest.mark.parametrize("domain", VALID_DOMAINS)
    def test_upload_with_explicit_domain(self, authed_client, minimal_pdf, domain):
        """Each domain value must be accepted and reflected in the response."""
        resp = authed_client.post(
            "/documents/upload",
            files={"file": (f"test_{domain}.pdf", minimal_pdf, "application/pdf")},
            data={"domain": domain},
        )
        assert resp.status_code in (200, 201, 202), (
            f"Upload with domain='{domain}' failed ({resp.status_code}): {resp.text}"
        )
        body = resp.json()
        # Domain should be echoed back (field name may vary)
        domain_in_response = body.get("domain") or body.get("document_domain", "")
        if domain_in_response:
            assert domain_in_response == domain, (
                f"Expected domain '{domain}', got '{domain_in_response}'"
            )

        doc_id = body.get("id") or body.get("document_id") or body.get("file_id")
        if doc_id:
            authed_client.delete(f"/documents/{doc_id}")

    def test_uploaded_document_appears_in_list(self, authed_client, minimal_pdf):
        """After upload, the document must appear in GET /documents."""
        filename = "test_list_check.pdf"
        resp = authed_client.post(
            "/documents/upload",
            files={"file": (filename, minimal_pdf, "application/pdf")},
            data={"domain": "training"},
        )
        assert resp.status_code in (200, 201, 202)
        doc_id = (
            resp.json().get("id")
            or resp.json().get("document_id")
            or resp.json().get("file_id")
        )

        list_resp = authed_client.get("/documents")
        assert list_resp.status_code == 200
        documents = list_resp.json().get("documents", [])
        filenames = [d.get("filename", "") for d in documents]
        assert filename in filenames, (
            f"Uploaded file '{filename}' not found in document list: {filenames}"
        )

        if doc_id:
            authed_client.delete(f"/documents/{doc_id}")


class TestDocumentList:
    def test_documents_list_returns_list(self, authed_client):
        """GET /documents must return 200 with a 'documents' list."""
        resp = authed_client.get("/documents")
        assert resp.status_code == 200
        body = resp.json()
        assert "documents" in body, f"Response missing 'documents' key: {body}"
        assert isinstance(body["documents"], list)

    def test_document_entries_have_required_fields(self, authed_client, minimal_pdf):
        """Each document entry must contain id, filename, and status fields."""
        # Upload a doc to ensure at least one entry exists
        up = authed_client.post(
            "/documents/upload",
            files={"file": ("test_fields.pdf", minimal_pdf, "application/pdf")},
            data={"domain": "training"},
        )
        assert up.status_code in (200, 201, 202)
        doc_id = (
            up.json().get("id")
            or up.json().get("document_id")
            or up.json().get("file_id")
        )

        resp = authed_client.get("/documents")
        docs = resp.json().get("documents", [])
        assert len(docs) > 0, "Document list is empty after upload"

        for doc in docs:
            assert "id" in doc or "document_id" in doc, f"Entry missing id: {doc}"
            assert "filename" in doc, f"Entry missing filename: {doc}"
            assert "status" in doc, f"Entry missing status: {doc}"

        if doc_id:
            authed_client.delete(f"/documents/{doc_id}")


class TestDocumentDelete:
    def test_delete_existing_document(self, authed_client, minimal_pdf):
        """DELETE /documents/{id} on an existing document must return 200 or 204."""
        up = authed_client.post(
            "/documents/upload",
            files={"file": ("test_delete.pdf", minimal_pdf, "application/pdf")},
            data={"domain": "training"},
        )
        assert up.status_code in (200, 201, 202)
        doc_id = (
            up.json().get("id")
            or up.json().get("document_id")
            or up.json().get("file_id")
        )
        assert doc_id, f"No document ID in upload response: {up.json()}"

        del_resp = authed_client.delete(f"/documents/{doc_id}")
        assert del_resp.status_code in (200, 204), (
            f"Delete returned {del_resp.status_code}: {del_resp.text}"
        )

    def test_delete_nonexistent_document(self, authed_client):
        """DELETE /documents/{id} for a non-existent ID must return 4xx, not 500."""
        # Use a well-formed UUID that does not correspond to any real document
        import uuid
        fake_id = str(uuid.uuid4())
        resp = authed_client.delete(f"/documents/{fake_id}")
        assert resp.status_code in (400, 404, 422), (
            f"Expected 4xx for non-existent doc, got {resp.status_code}"
        )
