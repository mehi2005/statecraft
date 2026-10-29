# Contributing to Statecraft

Statecraft evaluates agents against deterministic simulated workflows. Useful
contributions make the scenarios, evidence requirements, and failure reports
easier to inspect without weakening the verifier.

## Set up and validate

Follow the [README quickstart](README.md#quick-start-tests-and-an-offline-demo)
to create a virtual environment and install `requirements-dev.txt`. The CI
configuration targets Python 3.10–3.13. From the repository root, run:

```bash
python -m pytest -q
python offline_demo.py
```

These commands need no model, listening server, or external service after the
dependencies are installed. The demo uses scripted agents; its results are not
model performance measurements. Do not run concurrent rollouts against one
environment process: the SQLite connection, episode, and action log are shared.

For changes to the HTTP smoke check, also start the localhost server and run
`python sim_test.py` as described in the README. Live Ollama benchmarking is an
optional, separate activity, not a requirement for a contribution.

## Choose a narrow change

- Reproduce a bug with a focused failing test, then make it pass.
- Improve an unclear verifier reason or a documented example.
- Add a regression for an uncovered unsafe trajectory or policy boundary.
- Correct setup instructions using the exact commands you tried.

For larger changes, explain the proposed behavior and tradeoffs in an issue
before writing a broad refactor. Follow [SECURITY.md](SECURITY.md) for suspected
vulnerabilities; do not include sensitive details in a public issue.

## Add or change an episode

Scenarios currently live in Python and embedded SQL, not external fixture files.
Use an existing golden test in [tests/test_verifier.py](tests/test_verifier.py)
as a small working example.

1. Define the policy decision and expected safe behavior. Change `POLICY` in
   [env_server.py](env_server.py) only if the scenario needs a policy change,
   and explain its effect on existing episodes.
2. Add an `EPISODE_RULES` entry: the relevant ticket and email, whether an order
   read is required, the exact ordered mutation attempts, allowed final field
   changes, and the successful verifier reason. Failed attempts such as the
   first episode-5 refund are part of the expected trajectory.
3. Seed deterministic users, orders, charges, and a ticket in `seed_episode`.
   Preserve the baseline snapshot so unrelated changes remain detectable.
   The reset task currently derives the ticket ID as `9000 + episode_id`;
   keep the IDs consistent or explicitly revise and test that assumption.
4. If adding a sixth or later episode, update the reset query's upper bound and
   invalid-ID message, the runner's `range(1, 6)` and progress total, and relevant
   test ranges. In `offline_demo.py`, extend `SCENARIOS` and the scripted actions
   in `run_golden_episode`; listing an episode alone does not implement its
   trajectory. Update the demo's hard-coded `5/5` summary and corresponding test
   totals, plus the README episode table, badge count, and documented totals.
5. Test both a golden trajectory and meaningful failures: missing or irrelevant
   reads, reads after writes, out-of-order mutations, duplicate refunds, and
   unrelated state drift where applicable. Confirm unsuccessful actions cannot
   earn a successful verdict.
6. Run the suite and offline demo sequentially. Show the new behavior and test
   evidence in the pull request.

Keep the mock tools capable of exposing unsafe agent decisions. Enforcing every
company rule inside the mock API would hide mistakes the verifier is meant to
measure. Do not relax evidence ordering, exact mutation checks, or unrelated-state
checks merely to make a trajectory pass.

## Submit a pull request

Describe the concrete problem, resulting behavior, and why the change helps.
Include the exact validation commands and results, plus any untested platforms
or live-model behavior. Preserve existing behavior outside the change and avoid
unnecessary dependencies. Do not describe scripted results as real model scores.

Statecraft is licensed under [Apache 2.0](LICENSE). Keep existing license notices
and identify any third-party material and its license when proposing to add it.

Maintainers should protect `main` from direct pushes before granting external
collaborators write access. The repository's current protection settings have
not been verified by this guide.
