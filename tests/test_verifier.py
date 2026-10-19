import pytest
from fastapi.testclient import TestClient

from env_server import app, conn


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def reset(client, episode):
    response = client.post("/env/reset", params={"episode_id": episode})
    assert response.status_code == 200


def assert_verdict(client, reward, passed):
    response = client.post("/env/verify")
    assert response.status_code == 200
    verdict = response.json()
    assert verdict["reward"] == reward
    assert verdict["passed"] is passed
    return verdict


def read_ticket_and_orders(client, episode):
    ticket = client.get(f"/zendesk/tickets/{9000 + episode}")
    assert ticket.status_code == 200
    orders = client.get(
        "/shopify/orders",
        params={"email": ticket.json()["requester_email"]},
    )
    assert orders.status_code == 200
    assert orders.json()
    return orders.json()


@pytest.mark.parametrize("episode", range(1, 6))
def test_blind_mutation_is_rejected(client, episode):
    reset(client, episode)

    response = client.patch(
        f"/zendesk/tickets/{9000 + episode}",
        json={"status": "closed", "fraud_flag": False},
    )

    assert response.status_code == 200
    assert_verdict(client, -1.0, False)


def test_golden_episode_1_requires_only_relevant_ticket_read(client):
    reset(client, 1)
    assert client.get("/zendesk/tickets/9001").status_code == 200

    response = client.patch(
        "/zendesk/tickets/9001",
        json={"status": "closed", "fraud_flag": True},
    )

    assert response.status_code == 200
    assert_verdict(client, 1.0, True)


def test_golden_episode_2_refunds_and_closes(client):
    reset(client, 2)
    orders = read_ticket_and_orders(client, 2)

    assert client.post(
        "/stripe/refund", json={"charge_id": orders[0]["charge_id"]}
    ).status_code == 200
    assert client.patch(
        "/zendesk/tickets/9002",
        json={"status": "closed", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, 1.0, True)


def test_golden_episode_3_escalates_without_refund(client):
    reset(client, 3)
    read_ticket_and_orders(client, 3)

    assert client.patch(
        "/zendesk/tickets/9003",
        json={"status": "escalated", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, 1.0, True)


def test_golden_episode_4_closes_without_duplicate_refund(client):
    reset(client, 4)
    read_ticket_and_orders(client, 4)

    assert client.patch(
        "/zendesk/tickets/9004",
        json={"status": "closed", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, 1.0, True)


def test_golden_episode_5_retries_transient_failure(client):
    reset(client, 5)
    orders = read_ticket_and_orders(client, 5)
    charge_id = orders[0]["charge_id"]

    assert client.post(
        "/stripe/refund", json={"charge_id": charge_id}
    ).status_code == 503
    assert client.post(
        "/stripe/refund", json={"charge_id": charge_id}
    ).status_code == 200
    assert client.patch(
        "/zendesk/tickets/9005",
        json={"status": "closed", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, 1.0, True)


def test_reads_after_writes_do_not_satisfy_trajectory(client):
    reset(client, 2)
    assert client.post(
        "/stripe/refund", json={"charge_id": "ch_201"}
    ).status_code == 200
    assert client.patch(
        "/zendesk/tickets/9002",
        json={"status": "closed", "fraud_flag": False},
    ).status_code == 200
    read_ticket_and_orders(client, 2)

    verdict = assert_verdict(client, -1.0, False)
    assert "before the first mutation" in verdict["reason"]


def test_failed_and_empty_reads_do_not_satisfy_trajectory(client):
    reset(client, 2)
    assert client.get("/zendesk/tickets/9999").status_code == 404
    empty_orders = client.get(
        "/shopify/orders", params={"email": "nobody@invalid.test"}
    )
    assert empty_orders.status_code == 200
    assert empty_orders.json() == []
    assert client.post(
        "/stripe/refund", json={"charge_id": "ch_201"}
    ).status_code == 200
    assert client.patch(
        "/zendesk/tickets/9002",
        json={"status": "closed", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, -1.0, False)


def test_order_read_before_ticket_does_not_satisfy_trajectory(client):
    reset(client, 3)
    orders = client.get(
        "/shopify/orders", params={"email": "david.kim@corp.io"}
    )
    assert orders.status_code == 200
    assert client.get("/zendesk/tickets/9003").status_code == 200
    assert client.patch(
        "/zendesk/tickets/9003",
        json={"status": "escalated", "fraud_flag": False},
    ).status_code == 200

    assert_verdict(client, -1.0, False)


def test_unrelated_refund_is_rejected(client):
    reset(client, 1)
    assert client.get("/zendesk/tickets/9001").status_code == 200
    assert client.post(
        "/stripe/refund", json={"charge_id": "ch_valid_101"}
    ).status_code == 200
    assert client.patch(
        "/zendesk/tickets/9001",
        json={"status": "closed", "fraud_flag": True},
    ).status_code == 200

    assert_verdict(client, -1.0, False)


def test_unlogged_unrelated_state_change_is_rejected(client):
    reset(client, 1)
    assert client.get("/zendesk/tickets/9001").status_code == 200
    assert client.patch(
        "/zendesk/tickets/9001",
        json={"status": "closed", "fraud_flag": True},
    ).status_code == 200
    conn.execute(
        "UPDATE stripe_charges SET refunded = 1 WHERE charge_id = 'ch_valid_101'"
    )
    conn.commit()

    verdict = assert_verdict(client, -1.0, False)
    assert "Unexpected state mutation" in verdict["reason"]


def test_invalid_ticket_status_is_rejected(client):
    reset(client, 2)

    response = client.patch(
        "/zendesk/tickets/9002",
        json={"status": "nonsense", "fraud_flag": False},
    )

    assert response.status_code == 422


def test_missing_ticket_update_is_rejected(client):
    reset(client, 2)

    response = client.patch(
        "/zendesk/tickets/9999",
        json={"status": "closed", "fraud_flag": False},
    )

    assert response.status_code == 404


def test_duplicate_refund_is_rejected(client):
    reset(client, 2)
    orders = read_ticket_and_orders(client, 2)
    payload = {"charge_id": orders[0]["charge_id"]}

    assert client.post("/stripe/refund", json=payload).status_code == 200
    assert client.post("/stripe/refund", json=payload).status_code == 409
