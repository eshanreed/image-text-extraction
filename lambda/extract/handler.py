"""
Extract Lambda — triggered by S3 ObjectCreated events *via EventBridge*
(not a direct S3-to-Lambda notification, and not called via API Gateway
either). Runs Amazon Textract's synchronous DetectDocumentText against the
uploaded image and updates the DynamoDB record with the result.

Event shape note (this is the thing that actually broke on the first real
deploy): EventBridge's "Object Created" event does NOT look like a classic
S3 event notification. There's no top-level "Records" array -- S3 hands
EventBridge one event per object, and the bucket/object info lives directly
under "detail":

  {
    "version": "0",
    "detail-type": "Object Created",
    "source": "aws.s3",
    "detail": {
      "bucket": {"name": "..."},
      "object": {"key": "...", "size": ..., "eTag": "...", "sequencer": "..."},
      "reason": "PutObject"
    },
    ...
  }

The handler used to read event["Records"][0]["s3"][...], which is the
DIRECT-notification shape -- a leftover from writing this before double
checking what EventBridge itself actually delivers. That KeyError'd on
every real invocation, before ever reaching the try/except below, so the
DynamoDB record never even got marked "failed" -- it just sat in
"pending" forever, with no visible error except in this function's own
CloudWatch Logs. The original tests didn't catch it because they built a
synthetic event using the same (wrong) shape the handler expected, so code
and test agreed with each other while disagreeing with what AWS actually
sends.

Scope note: synchronous Textract only (single image / single-page doc,
<10MB), per the project's v1 scope decision. No async job + SNS/Step
Functions handling here.
"""
import urllib.parse

import boto3

from common.dynamo import results_table

_textract = boto3.client("textract")


def handler(event, context):
    # One EventBridge event = one S3 object, no batching/Records array
    # (see module docstring above).
    detail = event["detail"]
    bucket = detail["bucket"]["name"]
    key = urllib.parse.unquote_plus(detail["object"]["key"])
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
