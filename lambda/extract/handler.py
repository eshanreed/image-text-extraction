"""
Extract Lambda — triggered by S3 ObjectCreated events on the upload
bucket (not called via API Gateway). Runs Amazon Textract's synchronous
DetectDocumentText against the uploaded image and updates the DynamoDB
record with the result.

Scope note: synchronous Textract only (single image / single-page doc,
<10MB), per the project's v1 scope decision. No async job + SNS/Step
Functions handling here.
"""
import urllib.parse

import boto3

from common.dynamo import results_table

_textract = boto3.client("textract")


def handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        _process_object(bucket, key)


def _process_object(bucket: str, key: str) -> None:
    record_id = _record_id_from_key(key)
    table = results_table()

    try:
        response = _textract.detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}}
        )
        extracted_text = "\n".join(
            block["Text"]
            for block in response.get("Blocks", [])
            if block["BlockType"] == "LINE"
        )
        table.update_item(
            Key={"id": record_id},
            UpdateExpression="SET #s = :status, extracted_text = :text",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": "done", ":text": extracted_text},
        )
    except Exception as exc:
        # Best effort: record the failure on the item so the frontend can
        # show something other than an infinite "pending" spinner, then
        # re-raise so this still shows up as a Lambda error/CloudWatch
        # alarm target.
        table.update_item(
            Key={"id": record_id},
            UpdateExpression="SET #s = :status, #e = :error",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":status": "failed", ":error": str(exc)},
        )
        raise


def _record_id_from_key(key: str) -> str:
    # Keys look like "uploads/{id}.{ext}" — see upload/handler.py.
    filename = key.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0]
