"""
Upload Lambda — creates the initial DynamoDB record for a new extraction
and hands back a presigned S3 URL for the browser to upload directly to.

This function never touches the image bytes themselves: it only signs a
URL that lets the browser PUT the file straight to S3. That's deliberate —
see docs/decisions.md for why (API Gateway's payload limit plus base64
overhead makes proxying the file through here unreliable at the project's
stated size scope).
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

from common.dynamo import results_table
from common.responses import json_response

ALLOWED_CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
PRESIGNED_URL_EXPIRY_SECONDS = 300

_s3 = boto3.client("s3")


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return json_response(400, {"error": "body must be valid JSON"})

    content_type = body.get("contentType")
    if content_type not in ALLOWED_CONTENT_TYPES:
        return json_response(
            400,
            {"error": f"contentType must be one of {sorted(ALLOWED_CONTENT_TYPES)}"},
        )

    record_id = str(uuid.uuid4())
    extension = ALLOWED_CONTENT_TYPES[content_type]
    image_key = f"uploads/{record_id}.{extension}"
    now = datetime.now(timezone.utc).isoformat()

    results_table().put_item(
        Item={
            "id": record_id,
            "record_type": "extraction",
            "created_at": now,
            "status": "pending",
            "image_key": image_key,
        }
    )

    upload_url = _s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["UPLOAD_BUCKET_NAME"],
            "Key": image_key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
    )

    return json_response(201, {"id": record_id, "uploadUrl": upload_url})
