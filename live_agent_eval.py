import json
import requests
from openai import OpenAI

BASE_URL = "http://127.0.0.1:8000"

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_ticket",
            "description": "Fetch a Zendesk support ticket by ticket_id",
            "parameters": {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "List Shopify orders and Stripe charge IDs for a customer email",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "refund_charge",
            "description": "Issue a full refund on Stripe for a given charge_id",
            "parameters": {
                "type": "object",
                "properties": {"charge_id": {"type": "string"}},
                "required": ["charge_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_ticket",
            "description": "Update Zendesk ticket status ('open', 'closed', or 'escalated') and fraud_flag",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["open", "closed", "escalated"]},
                    "fraud_flag": {"type": "boolean"}
                },
                "required": ["ticket_id", "status", "fraud_flag"]
            }
        }
    }
]

def clean_ticket_id(val):
    if val is None:
        return 0
    # Strip any leading '#' or whitespace if model passed '#9001'
    cleaned = str(val).lstrip('#').strip()
    return int(cleaned) if cleaned.isdigit() else val

def execute_tool(name: str, args: dict):
    try:
        if name == "get_ticket":
            tid = clean_ticket_id(args.get("ticket_id"))
            resp = requests.get(f"{BASE_URL}/zendesk/tickets/{tid}")
        elif name == "list_orders":
            resp = requests.get(f"{BASE_URL}/shopify/orders", params={"email": args.get("email")})
        elif name == "refund_charge":
            resp = requests.post(f"{BASE_URL}/stripe/refund", json={"charge_id": args.get("charge_id")})
        elif name == "update_ticket":
            tid = clean_ticket_id(args.get("ticket_id"))
            fraud_val = args.get("fraud_flag", False)
            if isinstance(fraud_val, str):
                fraud_val = fraud_val.lower() == "true"
            resp = requests.patch(
                f"{BASE_URL}/zendesk/tickets/{tid}",
                json={"status": args.get("status"), "fraud_flag": bool(fraud_val)}
            )
        else:
            return {"status_code": 400, "body": {"error": f"Unknown tool: {name}"}}
        return {"status_code": resp.status_code, "body": resp.json()}
    except Exception as e:
        return {"status_code": 400, "body": {"error": f"Tool execution error: {str(e)}"}}

def run_benchmark(model_name="qwen2.5:7b"):
    results = []
    for ep in range(1, 6):
        env = requests.post(f"{BASE_URL}/env/reset", params={"episode_id": ep}).json()
        messages = [
            {"role": "system", "content": env["policy"]},
            {"role": "user", "content": env["task"]}
        ]
        print(f"\n--- Running Episode {ep}: {env['task']} ---")
        for step in range(6):
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=TOOLS
            )
            msg = response.choices[0].message
            messages.append(msg)
            if not msg.tool_calls:
                break
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                print(f"  Step {step+1}: {tc.function.name}({args})")
                res = execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(res)
                })
        verdict = requests.post(f"{BASE_URL}/env/verify").json()
        print(f"  Result -> Reward: {verdict['reward']} | {verdict['reason']}")
        results.append((ep, verdict["reward"], verdict["passed"], verdict["reason"]))

    print("\n================ BENCHMARK SUMMARY ================")
    print("| Episode | Reward | Passed | Verifier Verdict |")
    print("|---|---|---|---|")
    for ep, rew, passed, reason in results:
        print(f"| Episode {ep} | `{rew}` | {passed} | {reason} |")

if __name__ == "__main__":
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "llama3.2:3b"
    run_benchmark(model_name=model)
