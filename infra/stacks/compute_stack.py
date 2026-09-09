"""
ComputeStack — the application tier: Lambda functions + API Gateway.

Takes the data-tier resources (bucket, table) as constructor props rather
than creating its own. That's the whole point of the stack split: this
stack should be safe to redeploy constantly (new routes, handler tweaks,
dependency bumps) without any risk to what's stored in DataStack.

Request flow (see docs/decisions.md for why this shape and not a single
synchronous endpoint):
  1. POST /uploads            -> upload_fn creates a DynamoDB record and
                                  returns a presigned S3 URL
  2. (browser PUTs the image directly to S3 using that URL)
  3. S3 ObjectCreated event   -> extract_fn (no API route; not called by
                                  the frontend at all) runs Textract and
                                  updates the record
  4. GET /results             -> list_results_fn, most-recent-first
  5. GET /results/{id}        -> get_result_fn, single record incl. text

IAM: every Lambda gets its own role, and every grant below names the
specific action(s) that function actually calls — not a broad helper
grant — so each role's policy reads as a direct list of what that
function does. The one exception is Textract's DetectDocumentText, which
AWS does not let you scope to a resource ARN at all (it operates on data
passed inline in the request, not on an ARN-addressable object), so that
one statement is necessarily resources=["*"] even though the action
itself is narrow.
"""

from pathlib import Path

import jsii
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigateway,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
)
from cdk_nag import NagSuppressions
from constructs import Construct

LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "lambda"


@jsii.implements(apigwv2.IAccessLogSettings)
class _AccessLogSettings:
    """
    apigwv2.HttpStageProps.access_log_settings takes an IAccessLogSettings
    — a real interface, not a plain data struct, so (unlike most CDK props)
    a Python dict literal doesn't satisfy it; jsii needs an object that
    actually implements the interface. Hence this tiny wrapper.
    """

    def __init__(self, destination: apigateway.IAccessLogDestination, format: apigateway.AccessLogFormat):
        self._destination = destination
        self._format = format

    @property
    def destination(self) -> apigateway.IAccessLogDestination:
        return self._destination

    @property
    def format(self) -> apigateway.AccessLogFormat:
        return self._format


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

        is_prod = env_name == "prod"
        log_retention = logs.RetentionDays.ONE_MONTH if is_prod else logs.RetentionDays.ONE_WEEK
        common_env = {
            "RESULTS_TABLE_NAME": results_table.table_name,
            "UPLOAD_BUCKET_NAME": upload_bucket.bucket_name,
        }

        # --- upload: creates the DynamoDB record, hands back a presigned URL -------
        self.upload_fn = self._make_function("UploadFunction", "upload", common_env, log_retention)
        results_table.grant(self.upload_fn, "dynamodb:PutItem")
        upload_bucket.grant_put(self.upload_fn, "uploads/*")

        # --- extract: triggered by S3 upload, runs Textract, updates the record ----
        self.extract_fn = self._make_function(
            "ExtractFunction",
            "extract",
            common_env,
            log_retention,
            timeout=Duration.seconds(30),
        )
        results_table.grant(self.extract_fn, "dynamodb:UpdateItem")
        upload_bucket.grant_read(self.extract_fn, "uploads/*")
        self.extract_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["textract:DetectDocumentText"],
                resources=["*"],  # not resource-scopable — see module docstring
            )
        )
        # Matches S3's "Object Created" events (published to EventBridge
        # because DataStack set event_bridge_enabled=True on the bucket) for
        # this specific bucket and the uploads/ prefix, and invokes
        # extract_fn. This — not a direct S3-to-Lambda notification — is
        # what keeps this stack's dependency on DataStack one-directional;
        # see the comment on upload_bucket in data_stack.py.
        events.Rule(
            self,
            "ExtractOnUploadRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [upload_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "uploads/"}]},
                },
            ),
            targets=[events_targets.LambdaFunction(self.extract_fn)],
        )

        # --- list_results: queries the ListIndex GSI, never scans the table --------
        self.list_results_fn = self._make_function(
            "ListResultsFunction", "list_results", common_env, log_retention
        )
        results_table.grant(self.list_results_fn, "dynamodb:Query")

        # --- get_result: single-item lookup by id -----------------------------------
        self.get_result_fn = self._make_function(
            "GetResultFunction", "get_result", common_env, log_retention
        )
        results_table.grant(self.get_result_fn, "dynamodb:GetItem")

        # --- API Gateway (HTTP API) --------------------------------------------------
        # HTTP API rather than REST API: cheaper and simpler, and this
        # project doesn't need REST API's extra machinery (usage plans, API
        # keys, request-validation models) — every request is validated
        # inside its Lambda handler instead.
        self.api = apigwv2.HttpApi(
            self,
            "Api",
            create_default_stage=False,  # stage created explicitly below, with access logging
            cors_preflight=apigwv2.CorsPreflightOptions(
                # TODO: scope allow_origins to the CloudFront domain once
                # FrontendStack exists. It can't be a hard CDK cross-stack
                # reference without ComputeStack and FrontendStack depending
                # on each other (Frontend needs Compute's API URL; Compute
                # would need Frontend's domain for CORS) — see
                # docs/decisions.md.
                allow_origins=["*"],
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
                allow_headers=["Content-Type"],
            ),
        )

        # Access logging: who called the API, what route, what status — not
        # to be confused with the Lambda functions' own execution logs.
        # Requires a resource policy on the log group granting API Gateway
        # permission to write to it (this is how access-log delivery is
        # authorized for both REST and HTTP APIs; there's no L2 helper for
        # this on HttpApi the way apigateway.LogGroupLogDestination provides
        # for REST APIs, so it's done explicitly here).
        api_access_log_group = logs.LogGroup(
            self,
            "ApiAccessLogGroup",
            retention=log_retention,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )
        api_access_log_group.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                principals=[iam.ServicePrincipal("apigateway.amazonaws.com")],
                resources=[api_access_log_group.log_group_arn],
            )
        )
        self.api_stage = apigwv2.HttpStage(
            self,
            "DefaultStage",
            http_api=self.api,
            stage_name="$default",
            auto_deploy=True,
            access_log_settings=_AccessLogSettings(
                destination=apigateway.LogGroupLogDestination(api_access_log_group),
                format=apigateway.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=False,
                ),
            ),
        )

        self.api.add_routes(
            path="/uploads",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "UploadIntegration", self.upload_fn
            ),
        )
        self.api.add_routes(
            path="/results",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "ListResultsIntegration", self.list_results_fn
            ),
        )
        self.api.add_routes(
            path="/results/{id}",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "GetResultIntegration", self.get_result_fn
            ),
        )

        self.api_url = self.api_stage.url

        CfnOutput(self, "ApiUrl", value=self.api_url)

        # --- cdk-nag: accepted findings, documented rather than silently ignored ---
        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole grants only CloudWatch Logs create-log-group/stream/put-log-events for the function's own log group — that's the same scope a hand-written policy would need, so replacing it would be cosmetic.",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Four accepted wildcards, each explained where it's granted: "
                        "(1) textract:DetectDocumentText resources=['*'] — Textract's sync API isn't resource-scopable at all, it operates on data passed inline in the request; "
                        "(2) dynamodb:Query on '<table>/index/*' — CDK's Table.grant() doesn't support scoping to one named index, and this table has exactly one GSI (ListIndex) today; "
                        "(3) s3 grant_put/grant_read RESOURCE scoping on 'uploads/*' — this IS the intended scoping (the uploads/ prefix), the wildcard character is the prefix match working as designed, not an unbounded grant; "
                        "(4) s3 grant_put/grant_read ACTION scoping (s3:Abort*, s3:GetBucket*, s3:GetObject*, s3:List*) — these are CDK's own predefined 'write' and 'read' action bundles (grant_put -> the write bundle, which includes multipart-upload abort; grant_read -> the read bundle, which includes bucket-level Get/List variants needed to actually fetch an object); both bundles are still scoped to the uploads/ prefix only, per (3)."
                    ),
                    "appliesTo": [
                        "Resource::*",
                        {"regex": "/^Resource::.*ResultsTable.*\\/index\\/\\*$/"},
                        {"regex": "/^Resource::.*UploadBucket.*\\/uploads\\/\\*$/"},
                        "Action::s3:Abort*",
                        "Action::s3:GetBucket*",
                        "Action::s3:GetObject*",
                        "Action::s3:List*",
                    ],
                },
                {
                    "id": "AwsSolutions-APIG4",
                    "reason": "v1 deliberately ships with no authentication — a single shared history, no per-user data (see docs/decisions.md). Cognito-based auth is a scoped stretch feature, not part of v1.",
                },
            ],
            apply_to_children=True,
        )

    def _make_function(
        self,
        construct_id: str,
        lambda_dir_name: str,
        environment: dict,
        log_retention: logs.RetentionDays,
        *,
        timeout: Duration = Duration.seconds(10),
    ) -> _lambda.Function:
        # Bundles the whole lambda/ directory for every function (rather than
        # just e.g. lambda/upload/) so that lambda/common/ — shared code used
        # by all four handlers — is always included. Simpler than a Lambda
        # Layer for a project this size; a Layer would be the next step if
        # the shared code grew or needed independent versioning.
        log_group = logs.LogGroup(
            self,
            f"{construct_id}LogGroup",
            log_group_name=f"/aws/lambda/{self.stack_name}-{construct_id}",
            retention=log_retention,
            removal_policy=RemovalPolicy.RETAIN if self.env_name == "prod" else RemovalPolicy.DESTROY,
        )
        return _lambda.Function(
            self,
            construct_id,
            runtime=_lambda.Runtime.PYTHON_3_14,
            handler=f"{lambda_dir_name}.handler.handler",
            code=_lambda.Code.from_asset(str(LAMBDA_ROOT)),
            environment=environment,
            timeout=timeout,
            log_group=log_group,
        )
