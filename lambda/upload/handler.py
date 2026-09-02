"""
Upload Lambda — accepts an image, writes it to the upload bucket, and
creates the initial DynamoDB record for it.

TODO: implement once ComputeStack wiring is in place. Expected flow:
  1. Receive image bytes (or a presigned-URL request) via API Gateway
  2. Validate content type (JPEG/PNG) and size (<10MB, per project scope)
  3. Write to S3
  4. Create a DynamoDB item with status="uploaded"
  5. Return the new item's id
"""


def handler(event, context):
    raise NotImplementedError("upload handler not yet implemented")
