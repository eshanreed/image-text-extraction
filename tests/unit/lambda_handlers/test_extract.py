"""
Tests for the extract handler (lambda/extract/handler.py) -- triggered by
S3 via EventBridge, not API Gateway (see docs/decisions.md for why it's
wired that way), so these tests build a synthetic S3 "Object Created"
event rather than an API Gateway one.

Textract itself is patched directly rather than relying on moto's
Textract mock: moto's coverage of the synchronous DetectDocumentText
response shape is thin, and the actual thing worth testing here is "does
the handler correctly turn *some* Textract response into the right
DynamoDB update," not "does moto correctly emulate Textract." DynamoDB, by
contrast, moto emulates well, so that part uses a real (mocked) table --
asserting the persisted item state, not just the call arguments, so a bug
in the actual update expression would still be caught.
"""
from unittest.mock import MagicMock

import pytest

import extract.handler as extract_handler

BUCKET = "test-upload-bucket"


def _s3_event(key: str) -> dict:
    return {
        "Records": [
            {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
        ]
    }


def test_record_id_from_key_strips_prefix_and_extension():
    assert extract_handler._record_id_from_key("uploads/abc-123.jpg") == "abc-123"


def test_successful_extraction_updates_record_to_done(results_table, monkeypatch):
    results_table.put_item(
        Item={
            "id": "rec-1",
            "record_type": "extraction",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "pending",
            "image_key": "uploads/rec-1.png",
        }
    )
    fake_textract = MagicMock()
    fake_textract.detect_document_text.return_value = {
        "Blocks": [
            {"BlockType": "LINE", "Text": "Hello"},
            {"BlockType": "WORD", "Text": "ignored -- not a LINE block"},
            {"BlockType": "LINE", "Text": "World"},
        ]
    }
    monkeypatch.setattr(extract_handler, "_textract", fake_textract)

    extract_handler.handler(_s3_event("uploads/rec-1.png"), context=None)

    item = results_table.get_item(Key={"id": "rec-1"})["Item"]
    assert item["status"] == "done"
    assert item["extracted_text"] == "Hello\nWorld"
    fake_textract.detect_document_text.assert_called_once_with(
        Document={"S3Object": {"Bucket": BUCKET, "Name": "uploads/rec-1.png"}}
    )


def test_textract_failure_marks_record_failed_and_reraises(results_table, monkeypatch):
    results_table.put_item(
        Item={
            "id": "rec-2",
            "record_type": "extraction",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "pending",
            "image_key": "uploads/rec-2.jpg",
        }
    )
    fake_textract = MagicMock()
    fake_textract.detect_document_text.side_effect = RuntimeError("textract exploded")
    monkeypatch.setattr(extract_handler, "_textract", fake_textract)

    # The handler deliberately re-raises after recording the failure, so
    # it still surfaces as a Lambda error (and a CloudWatch alarm target)
    # instead of silently swallowing it -- see the comment in handler.py.
    with pytest.raises(RuntimeError, match="textract exploded"):
        extract_handler.handler(_s3_event("uploads/rec-2.jpg"), context=None)

    item = results_table.get_item(Key={"id": "rec-2"})["Item"]
    assert item["status"] == "failed"
    assert item["error"] == "textract exploded"


def test_key_with_url_encoded_characters_is_unquoted(results_table, monkeypatch):
    # S3 event keys are URL-encoded (e.g. spaces become "+"); handler.py
    # unquotes before deriving the record id, so this has to actually
    # round-trip through handler(), not _process_object() directly.
    results_table.put_item(
        Item={
            "id": "rec with space",
            "record_type": "extraction",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "pending",
        }
    )
    fake_textract = MagicMock()
    fake_textract.detect_document_text.return_value = {"Blocks": []}
    monkeypatch.setattr(extract_handler, "_textract", fake_textract)

    extract_handler.handler(
        _s3_event("uploads/rec+with+space.jpg"), context=None
    )

    item = results_table.get_item(Key={"id": "rec with space"})["Item"]
    assert item["status"] == "done"
