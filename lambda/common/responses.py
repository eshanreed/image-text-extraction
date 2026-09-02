"""Shared helper for building API Gateway (HTTP API) proxy responses."""
import decimal
import json


class _DecimalSafeEncoder(json.JSONEncoder):
    """DynamoDB's boto3 resource API returns Decimal for numbers; none of
    our current item fields are numeric, but this keeps json.dumps from
    blowing up the day one is added."""

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


def json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=_DecimalSafeEncoder),
    }
