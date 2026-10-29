import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from openai import OpenAI, OpenAIError


DEFAULT_ENV_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_TIMEOUT_SECONDS = 30

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ticket",
            "description": "Fetch a Zendesk support ticket by ticket_id",
            "parameters": {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "List Shopify orders and Stripe charge IDs for a customer email",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refund_charge",
            "description": "Issue a full refund on Stripe for a given charge_id",
            "parameters": {
                "type": "object",
                "properties": {"charge_id": {"type": "string"}},
                "required": ["charge_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_ticket",
            "description": "Update Zendesk ticket status and fraud_flag",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "closed", "escalated"],
                    },
                    "fraud_flag": {"type": "boolean"},
                },
                "required": ["ticket_id", "status", "fraud_flag"],
            },
        },
    },
]
TOOL_PARAMETERS = {
    tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOLS
}


def clean_ticket_id(value):
    if value is None:
        return 0
    cleaned = str(value).lstrip("#").strip()
    return int(cleaned) if cleaned.isdigit() else value


def response_body(response):
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        return {"error": "Tool returned a non-JSON response"}


def validate_tool_arguments(name, args):
    """Check the advertised tool contract before making any HTTP request."""
    parameters = TOOL_PARAMETERS[name]
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a JSON object")

    for field in parameters["required"]:
        if field not in args:
            raise ValueError(f"Missing required field: {field}")

    normalized = dict(args)
    field_types = {"integer": int, "string": str, "boolean": bool}
    for field, definition in parameters["properties"].items():
        if field not in normalized:
            continue
        value = normalized[field]
        # Keep compatibility with common model output without coercing arbitrary
        # values (notably truthy integers or unrecognized boolean strings).
        if field == "ticket_id" and isinstance(value, str):
            value = clean_ticket_id(value)
        elif field == "fraud_flag" and isinstance(value, str):
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"

        if type(value) is not field_types[definition["type"]]:
            raise ValueError(f"{field} must be a {definition['type']}")
        if "enum" in definition and value not in definition["enum"]:
            raise ValueError(f"{field} must be one of: {', '.join(definition['enum'])}")
        normalized[field] = value
    return normalized


def execute_tool(name, args, env_base_url, timeout_seconds):
    if name not in TOOL_PARAMETERS:
        return {"status_code": 400, "body": {"error": "Unknown tool"}}
    try:
        args = validate_tool_arguments(name, args)
    except ValueError as exc:
        return {
            "status_code": 400,
            "body": {"error": "Invalid tool arguments", "details": str(exc)},
        }

    try:
        if name == "get_ticket":
            response = requests.get(
                f"{env_base_url}/zendesk/tickets/{args['ticket_id']}",
                timeout=timeout_seconds,
            )
        elif name == "list_orders":
            response = requests.get(
                f"{env_base_url}/shopify/orders",
                params={"email": args["email"]},
                timeout=timeout_seconds,
            )
        elif name == "refund_charge":
            response = requests.post(
                f"{env_base_url}/stripe/refund",
                json={"charge_id": args["charge_id"]},
                timeout=timeout_seconds,
            )
        elif name == "update_ticket":
            response = requests.patch(
                f"{env_base_url}/zendesk/tickets/{args['ticket_id']}",
                json={
                    "status": args["status"],
                    "fraud_flag": args["fraud_flag"],
                },
                timeout=timeout_seconds,
            )
        return {"status_code": response.status_code, "body": response_body(response)}
    except requests.RequestException as exc:
        return {
            "status_code": 503,
            "body": {"error": "Tool transport failure", "type": type(exc).__name__},
        }


def request_environment(method, url, timeout_seconds, **kwargs):
    response = requests.request(method, url, timeout=timeout_seconds, **kwargs)
    response.raise_for_status()
    return response.json()


def assistant_message(message):
    result = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        result["tool_calls"] = [tool_call.model_dump() for tool_call in message.tool_calls]
    return result


def run_episode(
    client,
    model_name,
    episode,
    temperature,
    seed,
    max_steps,
    max_completion_tokens,
    env_base_url,
    timeout_seconds,
):
    environment = request_environment(
        "POST",
        f"{env_base_url}/env/reset",
        timeout_seconds,
        params={"episode_id": episode},
    )
    messages = [
        {"role": "system", "content": environment["policy"]},
        {"role": "user", "content": environment["task"]},
    ]
    transcript = []

    for step in range(1, max_steps + 1):
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            temperature=temperature,
            seed=seed,
            max_completion_tokens=max_completion_tokens,
        )
        message = response.choices[0].message
        messages.append(assistant_message(message))
        if not message.tool_calls:
            break

        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments)
            except (TypeError, json.JSONDecodeError):
                arguments = {}
                tool_result = {
                    "status_code": 400,
                    "body": {"error": "Invalid tool arguments"},
                }
            else:
                tool_result = execute_tool(
                    tool_call.function.name,
                    arguments,
                    env_base_url,
                    timeout_seconds,
                )

            transcript.append(
                {
                    "step": step,
                    "tool": tool_call.function.name,
                    "arguments": arguments,
                    "result": tool_result,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result, sort_keys=True),
                }
            )

    verdict = request_environment(
        "POST",
        f"{env_base_url}/env/verify",
        timeout_seconds,
    )
    return {
        "episode": episode,
        "seed": seed,
        "reward": verdict["reward"],
        "passed": verdict["passed"],
        "reason": verdict["reason"],
        "tool_calls": transcript,
    }


def ollama_root_url(llm_base_url):
    parts = urlsplit(llm_base_url)
    return f"{parts.scheme}://{parts.netloc}"


def resolve_ollama_models(model_names, llm_base_url, timeout_seconds):
    response = requests.get(
        f"{ollama_root_url(llm_base_url)}/api/tags",
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    available = {model["name"]: model for model in response.json().get("models", [])}
    missing = [name for name in model_names if name not in available]
    if missing:
        raise RuntimeError(f"Ollama models are not installed: {', '.join(missing)}")

    return {
        name: {
            "name": name,
            "digest": available[name]["digest"],
            "size": available[name].get("size"),
            "modified_at": available[name].get("modified_at"),
        }
        for name in model_names
    }


def source_fingerprint():
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        paths = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return {"revision": None, "dirty": None, "source_sha256": None}

    digest = hashlib.sha256()
    for relative_path in sorted(paths):
        path = Path(relative_path)
        if path.is_file():
            digest.update(relative_path.replace("\\", "/").encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return {
        "revision": revision,
        "dirty": bool(status.strip()),
        "source_sha256": digest.hexdigest(),
    }


def runtime_versions():
    packages = {}
    for package in ("fastapi", "openai", "pydantic", "requests", "uvicorn"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
    }


def compute_summary(results):
    grouped = defaultdict(list)
    for result in results:
        grouped[(result["model"], result["temperature"], result["episode"])].append(
            result
        )

    episode_summaries = []
    for (model, temperature, episode), episode_results in sorted(grouped.items()):
        ordered = sorted(episode_results, key=lambda item: item["trial"])
        passed = [bool(item["passed"]) for item in ordered]
        first_three = passed[:3]
        episode_summaries.append(
            {
                "model": model,
                "temperature": temperature,
                "episode": episode,
                "successes": sum(passed),
                "trials": len(passed),
                "pass_rate": sum(passed) / len(passed),
                "pass_at_3": any(first_three) if len(first_three) == 3 else None,
                "pass_power_3": all(first_three) if len(first_three) == 3 else None,
            }
        )

    aggregate_groups = defaultdict(list)
    for episode_summary in episode_summaries:
        aggregate_groups[
            (episode_summary["model"], episode_summary["temperature"])
        ].append(episode_summary)

    aggregates = []
    for (model, temperature), episodes in sorted(aggregate_groups.items()):
        successes = sum(episode["successes"] for episode in episodes)
        attempts = sum(episode["trials"] for episode in episodes)
        pass_at_3 = [episode["pass_at_3"] for episode in episodes]
        pass_power_3 = [episode["pass_power_3"] for episode in episodes]
        aggregates.append(
            {
                "model": model,
                "temperature": temperature,
                "successes": successes,
                "attempts": attempts,
                "pass_rate": successes / attempts,
                "episode_pass_at_3": (
                    sum(pass_at_3) / len(pass_at_3)
                    if all(value is not None for value in pass_at_3)
                    else None
                ),
                "episode_pass_power_3": (
                    sum(pass_power_3) / len(pass_power_3)
                    if all(value is not None for value in pass_power_3)
                    else None
                ),
            }
        )

    return {"episodes": episode_summaries, "aggregates": aggregates}


def print_summary(summary):
    print("\n================ MULTI-TRIAL SUMMARY ================")
    print("| Model | Temperature | Passed | Pass rate | Episode pass@3 | Episode pass^3 |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in summary["aggregates"]:
        pass_at_3 = (
            "n/a" if row["episode_pass_at_3"] is None else f"{row['episode_pass_at_3']:.0%}"
        )
        pass_power_3 = (
            "n/a"
            if row["episode_pass_power_3"] is None
            else f"{row['episode_pass_power_3']:.0%}"
        )
        print(
            f"| {row['model']} | {row['temperature']} | "
            f"{row['successes']}/{row['attempts']} | {row['pass_rate']:.0%} | "
            f"{pass_at_3} | {pass_power_3} |"
        )


def write_manifest(path, manifest):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def build_manifest(args, model_versions, results, repository, runtime, status):
    return {
        "schema_version": 1,
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "runtime": runtime,
        "configuration": {
            "models": args.models,
            "temperatures": args.temperatures,
            "trials_per_episode": args.trials,
            "base_seed": args.base_seed,
            "max_steps": args.max_steps,
            "max_completion_tokens": args.max_completion_tokens,
            "env_base_url": args.env_base_url,
            "llm_base_url": args.llm_base_url,
            "timeout_seconds": args.timeout,
        },
        "metric_definitions": {
            "pass_at_3": "Episode has at least one success in its first three recorded trials.",
            "pass_power_3": "Episode succeeds in all of its first three recorded trials.",
        },
        "model_versions": model_versions,
        "results": results,
        "summary": compute_summary(results),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run reproducible multi-trial Statecraft agent benchmarks."
    )
    parser.add_argument("models", nargs="*", default=["qwen2.5:7b"])
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--temperature",
        type=float,
        action="append",
        dest="temperatures",
        help="Repeat to benchmark multiple temperatures. Default: 0.0",
    )
    parser.add_argument("--base-seed", type=int, default=20261019)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--max-completion-tokens", type=int, default=512)
    parser.add_argument("--env-base-url", default=DEFAULT_ENV_BASE_URL)
    parser.add_argument("--llm-base-url", default=DEFAULT_LLM_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--output",
        default="benchmark-results/latest.json",
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    if args.max_steps < 1:
        parser.error("--max-steps must be at least 1")
    if args.max_completion_tokens < 1:
        parser.error("--max-completion-tokens must be at least 1")
    args.temperatures = args.temperatures or [0.0]
    return args


def main():
    args = parse_args()
    initial_models = resolve_ollama_models(args.models, args.llm_base_url, args.timeout)
    client = OpenAI(
        base_url=args.llm_base_url,
        api_key="ollama",
        timeout=args.timeout,
        max_retries=0,
    )
    results = []
    repository = source_fingerprint()
    runtime = runtime_versions()
    write_manifest(
        args.output,
        build_manifest(
            args,
            initial_models,
            results,
            repository,
            runtime,
            status="running",
        ),
    )

    for model_name in args.models:
        for temperature in args.temperatures:
            for trial in range(1, args.trials + 1):
                seed = args.base_seed + trial - 1
                for episode in range(1, 6):
                    print(
                        f"Model={model_name} temperature={temperature} "
                        f"trial={trial}/{args.trials} episode={episode}/5 seed={seed}"
                    )
                    result = run_episode(
                        client=client,
                        model_name=model_name,
                        episode=episode,
                        temperature=temperature,
                        seed=seed,
                        max_steps=args.max_steps,
                        max_completion_tokens=args.max_completion_tokens,
                        env_base_url=args.env_base_url,
                        timeout_seconds=args.timeout,
                    )
                    result.update(
                        {
                            "model": model_name,
                            "model_digest": initial_models[model_name]["digest"],
                            "temperature": temperature,
                            "trial": trial,
                        }
                    )
                    results.append(result)
                    write_manifest(
                        args.output,
                        build_manifest(
                            args,
                            initial_models,
                            results,
                            repository,
                            runtime,
                            status="running",
                        ),
                    )
                    print(
                        f"  reward={result['reward']} passed={result['passed']} "
                        f"reason={result['reason']}"
                    )

    final_models = resolve_ollama_models(args.models, args.llm_base_url, args.timeout)
    for model_name in args.models:
        if final_models[model_name]["digest"] != initial_models[model_name]["digest"]:
            raise RuntimeError(f"Model digest changed during benchmark: {model_name}")

    summary = compute_summary(results)
    manifest = build_manifest(
        args,
        initial_models,
        results,
        repository,
        runtime,
        status="complete",
    )
    write_manifest(args.output, manifest)
    print_summary(summary)
    print(f"\nManifest: {Path(args.output).resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OpenAIError, requests.RequestException, RuntimeError) as exc:
        print(f"Benchmark infrastructure error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
