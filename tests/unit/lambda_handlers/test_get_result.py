"""Tests for the get-result handler (lambda/get_result/handler.py)."""
import json

from get_result import handler as get_result_handler


def _invoke(record_id):
    event = {"pathParameters": {"id": record_id}} if record_id else {}
    return get_result_handler.handler(event, context=None)


def test_missing_id_is_a_400(results_table):
    response = _invoke(None)

    assert response["statusCode"] == 400


def test_unknown_id_is_a_404(results_table):
    response = _invoke("does-not-exist")

    assert response["statusCode"] == 404


def test_known_id_returns_the_full_record(results_table):
    results_table.put_item(
        Item={
            "id": "abc123",
            "record_type": "extraction",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status": "done",
            "extracted_text": "hello world",
        }
    )

    response = _invoke("abc123")

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["extracted_text"] == "hello world"
    assert body["status"] == "done"
