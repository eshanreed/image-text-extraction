"""
FrontendStack — the presentation tier: a static site (plain HTML/CSS/JS)
served from S3 behind CloudFront.

Isolated from ComputeStack so a copy change or a CSS tweak never has to
touch (or wait on) backend logic, and vice versa.

TODO (tracked as we build this out):
  - S3 bucket for the static site (private; origin access via CloudFront
    OAC, not a public bucket policy)
  - CloudFront distribution in front of it
  - Deploy the frontend/ directory contents via BucketDeployment
  - Inject the API Gateway URL into the frontend at deploy time (e.g. a
    generated config.js) once ComputeStack's api_url exists
"""

from aws_cdk import Stack
from constructs import Construct


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        api_url: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        self.api_url = api_url

        # Placeholder until S3 + CloudFront are wired up (see TODOs above).
        self.distribution = None
