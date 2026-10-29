import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import live_agent_eval as evaluation
from live_agent_eval import clean_ticket_id, compute_summary


def make_result(episode, trial, passed):
    return {
        "model": "test-model:1",
        "temperature": 0.2,
        "episode": episode,
        "trial": trial,
        "passed": passed,
    }


def test_compute_summary_reports_pass_at_3_and_pass_power_3():
    results = []
    outcomes = {
        1: [True, False, False],
        2: [True, True, True],
        3: [False, False, False],
        4: [True, True, False],
        5: [False, True, False],
    }
    for episode, passed_values in outcomes.items():
        for trial, passed in enumerate(passed_values, start=1):
            results.append(make_result(episode, trial, passed))

    summary = compute_summary(results)
    aggregate = summary["aggregates"][0]

    assert aggregate["successes"] == 7
    assert aggregate["attempts"] == 15
    assert aggregate["pass_rate"] == pytest.approx(7 / 15)
    assert aggregate["episode_pass_at_3"] == pytest.approx(4 / 5)
    assert aggregate["episode_pass_power_3"] == pytest.approx(1 / 5)


def test_compute_summary_does_not_invent_three_trial_metrics():
    summary = compute_summary([make_result(1, 1, True)])
    aggregate = summary["aggregates"][0]

    assert aggregate["episode_pass_at_3"] is None
    assert aggregate["episode_pass_power_3"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(9001, 9001), ("#9001", 9001), (" 9001 ", 9001), ("ticket", "ticket")],
)
def test_clean_ticket_id(value, expected):
    assert clean_ticket_id(value) == expected


@pytest.fixture
def http(monkeypatch):
    """Fail closed: these runner tests must never reach a live service."""
    methods = {}
    for name in ("get", "post", "patch", "request"):
        methods[name] = Mock(side_effect=AssertionError("Unexpected HTTP request"))
        monkeypatch.setattr(evaluation.requests, name, methods[name])
    return methods


def stub_response(body):
    return SimpleNamespace(
        status_code=200,
        json=lambda: body,
        raise_for_status=lambda: None,
    )


@pytest.mark.parametrize("arguments", [[], None, "ticket", 9001, 1.5, True])
def test_non_object_tool_arguments_are_rejected_without_http(http, arguments):
    result = evaluation.execute_tool("get_ticket", arguments, "http://unused", 1)

    assert result["status_code"] == 400
    assert result["body"]["error"]
    for method in http.values():
        method.assert_not_called()


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("get_ticket", {}, "ticket_id"),
        ("list_orders", {}, "email"),
        ("refund_charge", {}, "charge_id"),
        ("update_ticket", {"ticket_id": 9001, "status": "closed"}, "fraud_flag"),
        ("get_ticket", {"ticket_id": True}, "ticket_id"),
        ("get_ticket", {"ticket_id": 9001.5}, "ticket_id"),
        ("get_ticket", {"ticket_id": "../env/reset"}, "ticket_id"),
        ("list_orders", {"email": ["customer@example.test"]}, "email"),
        ("refund_charge", {"charge_id": None}, "charge_id"),
        (
            "update_ticket",
            {"ticket_id": 9001, "status": [], "fraud_flag": False},
            "status",
        ),
        (
            "update_ticket",
            {"ticket_id": 9001, "status": "deleted", "fraud_flag": False},
            "status",
        ),
        (
            "update_ticket",
            {"ticket_id": 9001, "status": "closed", "fraud_flag": 1},
            "fraud_flag",
        ),
        (
            "update_ticket",
            {"ticket_id": 9001, "status": "closed", "fraud_flag": "maybe"},
            "fraud_flag",
        ),
    ],
)
def test_invalid_fields_are_rejected_without_http(http, name, arguments, field):
    result = evaluation.execute_tool(name, arguments, "http://unused", 1)

    assert result["status_code"] == 400
    assert field in result["body"]["details"]
    for method in http.values():
        method.assert_not_called()


def test_unknown_tool_is_rejected_without_http(http):
    result = evaluation.execute_tool("reset_environment", {}, "http://unused", 1)

    assert result["status_code"] == 400
    assert result["body"]["error"] == "Unknown tool"
    for method in http.values():
        method.assert_not_called()


@pytest.mark.parametrize(
    ("name", "arguments", "method", "path", "kwargs"),
    [
        ("get_ticket", {"ticket_id": "#9001"}, "get", "/zendesk/tickets/9001", {}),
        (
            "list_orders",
            {"email": "customer@example.test"},
            "get",
            "/shopify/orders",
            {"params": {"email": "customer@example.test"}},
        ),
        (
            "refund_charge",
            {"charge_id": "ch_201"},
            "post",
            "/stripe/refund",
            {"json": {"charge_id": "ch_201"}},
        ),
        (
            "update_ticket",
            {"ticket_id": " 9001 ", "status": "closed", "fraud_flag": "FALSE"},
            "patch",
            "/zendesk/tickets/9001",
            {"json": {"status": "closed", "fraud_flag": False}},
        ),
        (
            "update_ticket",
            {"ticket_id": 9001, "status": "closed", "fraud_flag": True},
            "patch",
            "/zendesk/tickets/9001",
            {"json": {"status": "closed", "fraud_flag": True}},
        ),
    ],
)
def test_valid_tool_calls_preserve_requests(
    http, name, arguments, method, path, kwargs
):
    http[method].side_effect = None
    http[method].return_value = stub_response({"ok": True})

    result = evaluation.execute_tool(name, arguments, "http://unused", 7)

    assert result == {"status_code": 200, "body": {"ok": True}}
    http[method].assert_called_once_with(f"http://unused{path}", timeout=7, **kwargs)
    for other_name, other_method in http.items():
        if other_name != method:
            other_method.assert_not_called()


def tool_message(arguments, call_id):
    function = SimpleNamespace(name="get_ticket", arguments=arguments)
    tool_call = SimpleNamespace(
        id=call_id,
        function=function,
        model_dump=lambda: {
            "id": call_id,
            "type": "function",
            "function": {"name": function.name, "arguments": arguments},
        },
    )
    return SimpleNamespace(content=None, tool_calls=[tool_call])


@pytest.mark.parametrize(
    "bad_arguments", ["{", "[]", "null", '"ticket"', "42", "true"]
)
def test_episode_recovers_after_invalid_arguments(http, bad_arguments):
    http["request"].side_effect = [
        stub_response({"policy": "fixture", "task": "fixture"}),
        stub_response(
            {"reward": 0.0, "passed": False, "reason": "read-only fixture"}
        ),
    ]
    http["get"].side_effect = None
    http["get"].return_value = stub_response({"ticket_id": 9001})
    messages = [
        tool_message(bad_arguments, "bad-call"),
        tool_message('{"ticket_id": 9001}', "valid-call"),
        SimpleNamespace(content="Finished reading.", tool_calls=[]),
    ]
    request_messages = []

    def complete(**kwargs):
        request_messages.append(list(kwargs["messages"]))
        return SimpleNamespace(choices=[SimpleNamespace(message=messages.pop(0))])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
    )
    result = evaluation.run_episode(
        client, "scripted", 1, 0.0, 1, 3, 32, "http://unused", 7
    )

    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["result"]["status_code"] == 400
    assert result["tool_calls"][1]["result"]["status_code"] == 200
    assert result["reason"] == "read-only fixture"
    correction = request_messages[1][-1]
    assert correction["role"] == "tool"
    assert correction["tool_call_id"] == "bad-call"
    assert json.loads(correction["content"])["status_code"] == 400
    http["get"].assert_called_once_with("http://unused/zendesk/tickets/9001", timeout=7)
    assert http["request"].call_count == 2
    http["post"].assert_not_called()
    http["patch"].assert_not_called()
