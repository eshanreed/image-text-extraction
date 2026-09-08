"""
Basic smoke test that FrontendStack synthesizes and creates the resources
we expect.
"""

import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))

from stacks.data_stack import DataStack  # noqa: E402
from stacks.frontend_stack import FrontendStack  # noqa: E402


def test_frontend_stack_creates_site_bucket_and_distribution():
    app = cdk.App()
    data_stack = DataStack(app, "TestData", env_name="dev")
    frontend_stack = FrontendStack(
        app,
        "TestFrontend",
        env_name="dev",
        api_url="https://example.execute-api.us-east-1.amazonaws.com",
        access_log_bucket=data_stack.access_log_bucket,
    )
    template = Template.from_stack(frontend_stack)

    template.resource_count_is("AWS::S3::Bucket", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
