import socket

import pytest

import offline_demo


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("The offline demo must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def test_demo_is_repeatable_and_checks_all_episodes(monkeypatch, capsys):
    requests = []
    original = offline_demo.TestClient.request

    def record(client, method, url, **kwargs):
        response = original(client, method, url, **kwargs)
        requests.append((method, url, kwargs, response.status_code))
        return response

    monkeypatch.setattr(offline_demo.TestClient, "request", record)
    assert offline_demo.main() == 0
    first = capsys.readouterr()
    assert offline_demo.main() == 0
    second = capsys.readouterr()

    assert first == second
    assert not first.err
    assert first.out.startswith("Statecraft offline demo (scripted agents; no model calls)\n")
    assert first.out.count(": PASS") == 5
    assert first.out.endswith("5/5 golden scenarios passed; unsafe trajectory rejected.\n")

    resets = [kwargs["params"]["episode_id"] for _, url, kwargs, _ in requests if url == "/env/reset"]
    assert resets == [1, 2, 3, 4, 5, 2] * 2
    refund_statuses = [status for _, url, _, status in requests if url == "/stripe/refund"]
    assert refund_statuses == [200, 503, 200, 200] * 2


@pytest.mark.parametrize("failure", ["golden_verdict", "unsafe_verdict", "http_error"])
def test_demo_returns_failure_for_incorrect_results(monkeypatch, capsys, failure):
    original = offline_demo.TestClient.request
    verification_count = 0

    def corrupt(client, method, url, **kwargs):
        nonlocal verification_count
        response = original(client, method, url, **kwargs)
        if url == "/env/verify":
            verification_count += 1
            if failure == "golden_verdict" and verification_count == 1:
                response._content = b'{"passed": true, "reward": 0.0}'
            if failure == "unsafe_verdict" and verification_count == 6:
                response._content = b'{"passed": true, "reward": 1.0}'
        if failure == "http_error" and url == "/env/reset":
            response.status_code = 500
        return response

    monkeypatch.setattr(offline_demo.TestClient, "request", corrupt)

    assert offline_demo.main() == 1
    captured = capsys.readouterr()
    assert "Offline demo failed:" in captured.err
    assert "5/5 golden scenarios passed" not in captured.out
