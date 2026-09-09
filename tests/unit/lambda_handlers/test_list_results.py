"""
Tests for the list-results handler (lambda/list_results/handler.py) --
the one whose whole reason for existing is "Query the ListIndex GSI, don't
Scan the table" (see docs/decisions.md). These tests can't see *how* that
happens from the outside, but they can and do pin down the part that
actually matters to callers: most-recent-first ordering and limit
handling.
"""
import json

from list_results import handler as list_results_handler


def _seed(table, count):
    for i in range(count):
        table.put_item(
            Item={
                "id": f"id-{i}",
                "record_type": "extraction",
                "created_at": f"2026-01-{i + 1:02d}T00:00:00+00:00",
                "status": "done",
            }
        )


def test_returns_most_recent_first(results_table):
    _seed(results_table, count=3)

    response = list_results_handler.handler({}, context=None)

    assert response["statusCode"] == 200
    items = json.loads(response["body"])["items"]
    assert [item["id"] for item in items] == ["id-2", "id-1", "id-0"]


def test_respects_a_custom_limit(results_table):
    _seed(results_table, count=5)

    response = list_results_handler.handler(
        {"queryStringParameters": {"limit": "2"}}, context=None
    )

    items = json.loads(response["body"])["items"]
    assert len(items) == 2


def test_caps_limit_at_max_instead_of_erroring(results_table):
    _seed(results_table, count=3)

    # MAX_LIMIT is 100; asking for far more than that shouldn't 400 or
    # blow up DynamoDB's Limit param, it should just get capped.
    response = list_results_handler.handler(
        {"queryStringParameters": {"limit": "999999"}}, context=None
    )

    assert response["statusCode"] == 200
    assert len(json.loads(response["body"])["items"]) == 3


def test_non_integer_limit_is_a_400(results_table):
    response = list_results_handler.handler(
        {"queryStringParameters": {"limit": "not-a-number"}}, context=None
    )

    assert response["statusCode"] == 400
