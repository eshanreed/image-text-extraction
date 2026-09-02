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
  - Lifecycle rule to expire old uploaded images after N days (dev only?)

Table design note: the table's access patterns are (1) get one record by id
and (2) list recent records, most-recent-first. (1) is a direct GetItem on
the base table. (2) is NOT served by a Scan — a Scan reads every item in
the table regardless of how many you actually want, which doesn't hold up
as the table grows. Instead there's a GSI (`ListIndex`) with a constant
partition key (`record_type`, always "extraction" for now, since there's
no per-user partitioning in v1) and `created_at` as the sort key, so
"list recent" is a Query against that index instead of a table Scan.
"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
)
from cdk_nag import NagSuppressions
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

        # Separate bucket purely for S3 server access logs. Not encrypted
        # with the same rigor / lifecycle as real data because it holds no
        # application data, just request records — but still private
        # (BLOCK_ALL) since access patterns are still information about the
        # app. This bucket is disposable in both envs: losing old access
        # logs isn't a data-loss incident the way losing UploadBucket or
        # ResultsTable would be.
        access_log_bucket = s3.Bucket(
            self,
            "AccessLogBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # NOT BUCKET_OWNER_ENFORCED: S3's server-access-logging delivery
            # mechanism (below, via server_access_logs_bucket) grants the
            # logging service an ACL on this bucket, and ACLs are exactly
            # what BUCKET_OWNER_ENFORCED disables. Left at CDK's default
            # (ObjectWriter) so that grant can actually be applied.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # --- S3: uploaded images -------------------------------------------------
        self.upload_bucket = s3.Bucket(
            self,
            "UploadBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
            server_access_logs_bucket=access_log_bucket,
            server_access_logs_prefix="upload-bucket-access-logs/",
            # Publishes all S3 events to the account's default EventBridge
            # bus. Deliberately NOT using bucket.add_event_notification()
            # to wire this directly to a Lambda: that approach adds the
            # notification config (which needs the Lambda's ARN) onto this
            # bucket, i.e. into THIS stack — but the extract Lambda lives in
            # ComputeStack, which already depends on this stack for the
            # bucket/table ARNs it grants permissions on. That's a
            # dependency cycle CDK will refuse to synth. Routing through
            # EventBridge instead means this stack makes zero reference to
            # ComputeStack; ComputeStack defines the EventBridge Rule that
            # matches these events and targets the Lambda, keeping the
            # cross-stack reference pointing one direction only.
            event_bridge_enabled=True,
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

        # GSI for the "list recent results" access pattern — see table design
        # note above. Only projects the attributes the list view actually
        # needs, not the full extracted text, to keep query responses small.
        self.results_table.add_global_secondary_index(
            index_name="ListIndex",
            partition_key=dynamodb.Attribute(
                name="record_type", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.INCLUDE,
            non_key_attributes=["status", "image_key"],
        )

        # --- cdk-nag: accepted findings, documented rather than silently ignored ---
        NagSuppressions.add_resource_suppressions(
            access_log_bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "This bucket IS the access-log destination; AWS does not expect (and doesn't really support) a log bucket to log to itself or another bucket.",
                }
            ],
        )
        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole here belongs to CDK's own auto-generated custom-resource Lambdas (auto-delete-objects for dev buckets, the EventBridge notification toggle) — framework-managed, not application code.",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Wildcard S3 actions (s3:GetBucket*, s3:List*, s3:GetObject*, s3:Abort*) belong to CDK's built-in auto-delete-objects custom resource for dev buckets (auto_delete_objects=True) — it needs to enumerate and remove every object/version on stack deletion; this is generated by the CDK framework, not hand-written here.",
                    "appliesTo": [
                        "Action::s3:GetBucket*",
                        "Action::s3:List*",
                        "Action::s3:GetObject*",
                        "Action::s3:Abort*",
                    ],
                },
                {
                    "id": "AwsSolutions-DDB3",
                    "reason": "Point-in-time recovery is intentionally on in prod only (point_in_time_recovery_specification above, gated on is_prod) — dev tables hold disposable test data, so PITR there is cost without benefit.",
                },
            ],
            apply_to_children=True,
        )
