"""
DataStack — the stateful resources: the image upload bucket and the
extraction-results table.

Kept isolated from ComputeStack and FrontendStack on purpose:

  - This stack should change rarely. ComputeStack will be redeployed
    constantly as Lambda/API logic iterates; DataStack should not be caught
    up in that churn.
  - Removal policy is stricter here than elsewhere. A mistaken `cdk destroy`
    of the compute or frontend stack should never be able to take stored
    data with it. In prod, resources are RETAINed by default.

TODO (tracked as we build this out):
  - Bucket encryption (SSE-S3 is fine here; no need for a customer KMS key
    for a portfolio project, but worth being explicit about that choice)
  - Block public access on the upload bucket (Textract reads objects via
    IAM, never via public URL)
  - Lifecycle rule to expire old uploaded images after N days (dev only?)
  - DynamoDB point-in-time recovery for prod
"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        is_prod = env_name == "prod"

        # --- S3: uploaded images -------------------------------------------------
        self.upload_bucket = s3.Bucket(
            self,
            "UploadBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
        )

        # --- DynamoDB: extraction results -----------------------------------------
        self.results_table = dynamodb.Table(
            self,
            "ResultsTable",
            partition_key=dynamodb.Attribute(
                name="id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_prod
            ),
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )
