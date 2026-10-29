import pytest
from fastapi.testclient import TestClient

import sim_test
from env_server import app


@pytest.fixture
def smoke_transport(monkeypatch):
    calls = []
    overrides = {}
    with TestClient(app) as client:
        def request(method, url, **kwargs):
            path = url.removeprefix(sim_test.BASE_URL)
            calls.append((method, path, kwargs.pop("timeout", None)))
            response = client.request(method, path, **kwargs)
            replacement = overrides.get((method, path))
            if replacement:
                return replacement(response)
            return response

        for method in ("get", "post", "patch"):
            monkeypatch.setattr(
                sim_test.requests,
                method,
                lambda url, _method=method, **kwargs: request(_method, url, **kwargs),
            )
        yield calls, overrides


def replace_verdict(response, *, passed, reward):
    response._content = (
        f'{{"passed": {str(passed).lower()}, "reward": {reward}, "reason": "fixture"}}'
    ).encode()
    return response


def test_expected_smoke_trajectories_pass_with_finite_timeouts(smoke_transport):
    calls, _ = smoke_transport
    sim_test.run_gullible_agent()
    sim_test.run_robust_agent()

    assert calls
    assert all(timeout is not None and 0 < timeout < 60 for _, _, timeout in calls)


@pytest.mark.parametrize(
    ("run", "passed", "reward"),
    [
        (sim_test.run_gullible_agent, True, 1.0),
        (sim_test.run_gullible_agent, False, 0.0),
        (sim_test.run_robust_agent, False, -1.0),
        (sim_test.run_robust_agent, True, 0.0),
    ],
)
def test_incorrect_verdict_fails_smoke(smoke_transport, run, passed, reward):
    _, overrides = smoke_transport
    overrides[("post", "/env/verify")] = lambda response: replace_verdict(
        response, passed=passed, reward=reward
    )

    with pytest.raises(AssertionError, match="verdict"):
        run()


def test_http_error_stops_smoke_before_more_actions(smoke_transport):
    calls, overrides = smoke_transport

    def fail_reset(response):
        response.status_code = 500
        return response

    overrides[("post", "/env/reset")] = fail_reset

    with pytest.raises(Exception, match="500"):
        sim_test.run_gullible_agent()

    assert len(calls) == 1
