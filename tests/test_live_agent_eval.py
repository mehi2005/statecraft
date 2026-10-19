import pytest

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
