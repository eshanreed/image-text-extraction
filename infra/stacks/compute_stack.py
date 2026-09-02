"""
ComputeStack — the application tier: Lambda functions + API Gateway.

Takes the data-tier resources (bucket, table) as constructor props rather
than creating its own. That's the whole point of the stack split: this
stack should be safe to redeploy constantly (new routes, handler tweaks,
dependency bumps) without any risk to what's stored in DataStack.

Each Lambda function gets its own IAM role, scoped to exactly what that
function needs — not a shared "app role" with broad permissions. For
example: the extract function needs `textract:DetectDocumentText` (which
takes no resource ARN — it's not resource-scopable) plus read/write on its
own DynamoDB items and read on the upload bucket; it should not be able to
delete the table or touch unrelated resources.

TODO (tracked as we build this out):
  - Lambda functions: upload, extract, list_results, get_result
  - API Gateway REST API (or HTTP API) wiring routes to each function
  - Least-privilege grants:
      upload_bucket.grant_read(extract_fn)
      results_table.grant_read_write_data(...)
      explicit textract:DetectDocumentText statement on the extract role
  - CORS config once the frontend origin (CloudFront domain) is known
  - Structured logging / log retention policy
"""

from aws_cdk import (
    Stack,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        upload_bucket: s3.Bucket,
        results_table: dynamodb.Table,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        self.upload_bucket = upload_bucket
        self.results_table = results_table

        # Placeholder until Lambda + API Gateway are wired up (see TODOs above).
        self.api = None
        self.api_url = None
