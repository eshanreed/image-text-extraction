"""
Tests for the shared response helper (lambda/common/responses.py). Pure
function, no AWS involved, so no moto/env-var fixtures needed here --
plain pytest is enough.
"""
import decimal
import json

from common.responses import json_response


def test_basic_response_shape():
    response = json_response(200, {"ok": True})

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    assert json.loads(response["body"]) == {"ok": True}


def test_whole_number_decimal_encodes_as_int():
    # DynamoDB's resource API returns Decimal for numeric attributes.
    # None of our fields are numeric today, but this is exactly the kind
    # of thing that silently breaks the day one is added -- json.dumps
    # can't serialize a Decimal at all without this encoder.
    response = json_response(200, {"count": decimal.Decimal("3")})

    assert json.loads(response["body"])["count"] == 3
    assert isinstance(json.loads(response["body"])["count"], int)


def test_fractional_decimal_encodes_as_float():
    response = json_response(200, {"score": decimal.Decimal("1.5")})

    assert json.loads(response["body"])["score"] == 1.5
