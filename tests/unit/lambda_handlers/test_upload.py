"""
Tests for the upload handler (lambda/upload/handler.py) -- it creates the
DynamoDB record and hands back a presigned S3 PUT URL, without ever
touching image bytes itself (see docs/decisions.md for why).
"""
import json

from upload import handler as upload_handler


def _invoke(body: dict):
    return upload_handler.handler({"body": json.dumps(body)}, context=None)


def test_rejects_unsupported_content_type(results_table):
    response = _invoke({"contentType": "image/gif"})

    assert response["statusCode"] == 400
    assert "contentType" in json.loads(response["body"])["error"]


def test_rejects_malformed_json_body(results_table):
    response = upload_handler.handler({"body": "not json"}, context=None)

    assert response["statusCode"] == 400


def test_accepts_supported_image_and_creates_pending_record(results_table):
    response = _invoke({"contentType": "image/png"})

    assert response["statusCode"] == 201
    payload = json.loads(response["body"])
    record_id = payload["id"]
    assert payload["uploadUrl"].startswith("https://")

    # The real behavior worth locking in: a pending record actually landed
    # in the table, keyed so the extract handler can find it again by the
    # same id once S3 triggers it, and so list/get results see it as
    # "pending" until Textract finishes.
    item = results_table.get_item(Key={"id": record_id})["Item"]
    assert item["status"] == "pending"
    assert item["record_type"] == "extraction"
    assert item["image_key"] == f"uploads/{record_id}.png"


def test_upload_url_targets_the_configured_bucket_and_key(results_table, monkeypatch):
    response = _invoke({"contentType": "image/jpeg"})
    payload = json.loads(response["body"])

    # generate_presigned_url signs locally rather than calling AWS, so this
    # doesn't need moto's S3 mock -- it's really asserting the Params we
    # pass in, surfaced back out through the URL boto3 builds.
    assert "test-upload-bucket" in payload["uploadUrl"]
    assert payload["id"] in payload["uploadUrl"]
