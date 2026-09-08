"""
FrontendStack — the presentation tier: a static site (plain HTML/CSS/JS)
served from S3 behind CloudFront.

Isolated from ComputeStack so a copy change or a CSS tweak never has to
touch (or wait on) backend logic, and vice versa. It DOES take ComputeStack's
api_url as a prop — that's a one-directional reference (Frontend reads from
Compute), not the two-way kind that caused the S3-notification dependency
cycle documented in data_stack.py, so it's safe as a plain constructor arg.

How the frontend learns the API's URL: BucketDeployment writes a small
generated config.js alongside the static frontend/ files, with
`window.API_BASE_URL` set to the real API URL. The frontend JS reads that
global instead of having the URL hand-typed anywhere.

Access pattern: private bucket + CloudFront Origin Access Control (OAC) —
the modern replacement for the older OAI pattern. CloudFront is the only
thing allowed to read the bucket directly; there's no public bucket policy.

Cache invalidation: BucketDeployment's `distribution` + `distribution_paths`
args below make every deploy automatically invalidate the whole CloudFront
cache, so there's no stale-content problem during active development —
each deploy's changes show up immediately, no manual invalidation needed.

Not doing for v1 (documented, not overlooked):
  - CORS on the API is still allow_origins=["*"] (see compute_stack.py) —
    tightening it to this stack's CloudFront domain would need a hard
    cross-stack reference the other direction (Compute -> Frontend), which
    combined with this stack's existing Frontend -> Compute reference would
    recreate the same kind of cycle documented in data_stack.py.
  - No custom domain — no domain purchased for this project, and Route 53 +
    ACM adds real complexity for no benefit here. CloudFront's default
    *.cloudfront.net domain is used as-is.
  - No SPA-style error-page rewiring (e.g. 404 -> index.html) — this is a
    handful of static pages, not a client-side-routed app, so the default
    S3/CloudFront error behavior is fine.
  - CloudFront TLS on the default *.cloudfront.net certificate can't be
    pinned to TLSv1.2+ (that's only configurable with a custom domain +
    ACM certificate) — a direct consequence of skipping a custom domain
    above, not a separate gap.
  - No geo restrictions or AWS WAF on the distribution — no requirement to
    block specific countries, and WAF is a real ongoing cost for a static
    demo site with no auth or sensitive data behind it. Would reconsider
    WAF for a production workload.
"""

from pathlib import Path

from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
)
from cdk_nag import NagSuppressions
from constructs import Construct

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        api_url: str | None = None,
        access_log_bucket: s3.Bucket | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        self.api_url = api_url
        is_prod = env_name == "prod"

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
            server_access_logs_bucket=access_log_bucket,
            server_access_logs_prefix="site-bucket-access-logs/",
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                # AWS-managed policy: caches based on the request path only,
                # no cookies/query strings to vary on for a static site like
                # this. Paired with the deploy-time invalidation above.
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            # CloudFront's own request-level access logs (distinct from the
            # S3 bucket's server access logs above) — same shared log
            # bucket from DataStack, different prefix.
            log_bucket=access_log_bucket,
            log_file_prefix="cloudfront-access-logs/",
        )

        self.site_url = f"https://{self.distribution.distribution_domain_name}"

        config_js = f'window.API_BASE_URL = "{api_url}";\n' if api_url else 'window.API_BASE_URL = "";\n'

        s3_deployment.BucketDeployment(
            self,
            "DeploySite",
            sources=[
                s3_deployment.Source.asset(str(FRONTEND_ROOT)),
                s3_deployment.Source.data("config.js", config_js),
            ],
            destination_bucket=site_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

        # --- cdk-nag: accepted findings, documented rather than silently ignored ---
        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "AWSLambdaBasicExecutionRole belongs to CDK's own built-in BucketDeployment custom resource (the Lambda that copies frontend/ into the bucket and triggers a CloudFront invalidation) — framework-managed, not application code.",
                    "appliesTo": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "All from CDK's built-in BucketDeployment custom resource: it needs to read the CDK bootstrap assets bucket (where the zipped frontend/ source is staged), and read/write/delete/list on this stack's own SiteBucket to sync its contents, plus cloudfront:CreateInvalidation (not resource-scopable, like Textract's DetectDocumentText) to bust the cache after each deploy. Framework-generated, not hand-written here.",
                    "appliesTo": [
                        "Action::s3:GetObject*",
                        "Action::s3:GetBucket*",
                        "Action::s3:List*",
                        "Action::s3:DeleteObject*",
                        "Action::s3:Abort*",
                        "Resource::*",
                        {"regex": "/^Resource::.*cdk-hnb659fds-assets.*$/"},
                        {"regex": "/^Resource::.*SiteBucket.*$/"},
                    ],
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": "Runtime belongs to CDK's own built-in BucketDeployment custom resource Lambda — its runtime version is managed by the CDK framework release, not something application code pins.",
                },
                {
                    "id": "AwsSolutions-CFR1",
                    "reason": "No geo-restriction requirement for this project — it's a public portfolio demo, not something restricted by region.",
                },
                {
                    "id": "AwsSolutions-CFR2",
                    "reason": "No AWS WAF for v1 — real ongoing cost for a static site with no auth or sensitive data behind it (see docs/decisions.md). Would reconsider for a production workload.",
                },
                {
                    "id": "AwsSolutions-CFR4",
                    "reason": "Pinning a minimum TLS version requires a custom domain + ACM certificate; this project deliberately has no custom domain for v1 (see module docstring / docs/decisions.md), so it's stuck on CloudFront's default certificate behavior.",
                },
            ],
            apply_to_children=True,
        )
