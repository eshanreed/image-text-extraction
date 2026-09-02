"""
Extract Lambda — runs Amazon Textract's synchronous DetectDocumentText
against an uploaded image and stores the extracted text.

TODO: implement once ComputeStack wiring is in place. Expected flow:
  1. Look up the DynamoDB item and its S3 object
  2. Call textract.detect_document_text against the S3 object
  3. Update the DynamoDB item with extracted text and status="done"
     (or status="failed" with an error message)

Scope note: synchronous Textract only (single image / single-page doc,
<10MB) per the project's v1 scope decision. No async job + SNS/Step
Functions handling here.
"""


def handler(event, context):
    raise NotImplementedError("extract handler not yet implemented")
