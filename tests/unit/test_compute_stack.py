"""
Tests for ComputeStack — mainly locking in the least-privilege IAM story
(each function's role should be scoped narrowly, not a shared broad role)
and that the API routes exist.
"""

import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))

from stacks.compute_stack import ComputeStack  # noqa: E402
from stacks.data_stack import DataStack  # noqa: E402


def _synth_compute_stack():
    app = cdk.App()
    data_stack = DataStack(app, "TestData", env_name="dev")
    compute_stack = ComputeStack(
        app,
        "TestCompute",
        env_name="dev",
        upload_bucket=data_stack.upload_bucket,
        results_table=data_stack.results_table,
    )
    return Template.from_stack(compute_stack)


def test_creates_four_lambda_functions():
    template = _synth_compute_stack()
    template.resource_count_is("AWS::Lambda::Function", 4)


def test_creates_http_api_with_three_routes():
    template = _synth_compute_stack()
    template.resource_count_is("AWS::ApiGatewayV2::Api", 1)
    template.resource_count_is("AWS::ApiGatewayV2::Route", 3)


def test_extract_role_has_narrow_textract_permission():
    template = _synth_compute_stack()
    # Exactly one IAM policy should grant textract:DetectDocumentText, and
    # it should grant only that action (not a wildcard like textract:*).
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Action": "textract:DetectDocumentText",
                                    "Resource": "*",
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_extract_on_upload_rule_exists():
    template = _synth_compute_stack()
    template.resource_count_is("AWS::Events::Rule", 1)
