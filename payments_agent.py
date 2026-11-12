"""Governed payment agent: an LLM agent that can move money through PayPal,
but only through a policy engine, a human-approval queue and an audit ledger.

Sandbox only. Keys come from environment variables (see .env.example).
"""
import os, time, uuid, sqlite3, requests

# ---------- Config ----------
BASE = "https://api-m.sandbox.paypal.com"
DB_PATH = os.environ.get("LEDGER_DB", "ledger.db")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
RETURN_URL = os.environ.get("RETURN_URL", "https://example.com/return")
CANCEL_URL = os.environ.get("CANCEL_URL", "https://example.com/cancel")
POLICY = {"per_txn_cap": 50.0, "approval_threshold": 25.0, "daily_budget": 100.0}


def _secret(name):
    val = os.environ.get(name)
    if val:
        return val
    try:  # Colab fallback
        from google.colab import userdata
        return userdata.get(name)
    except Exception:
        raise RuntimeError(f"Missing {name}. Set it as an environment variable.")


# ---------- PayPal API (sandbox) ----------
def get_token():
    r = requests.post(f"{BASE}/v1/oauth2/token",
                      auth=(_secret("PAYPAL_CLIENT_ID"), _secret("PAYPAL_SECRET")),
                      data={"grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _headers(extra=None):
    h = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def create_order(amount, currency="USD"):
    r = requests.post(
        f"{BASE}/v2/checkout/orders", headers=_headers(), timeout=30,
        json={"intent": "CAPTURE",
              "purchase_units": [{"amount": {"currency_code": currency, "value": f"{amount:.2f}"}}],
              "payment_source": {"paypal": {"experience_context": {
                  "return_url": RETURN_URL, "cancel_url": CANCEL_URL,
                  "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING"}}}})
    r.raise_for_status()
    return r.json()


def capture_order(order_id):
    r = requests.post(f"{BASE}/v2/checkout/orders/{order_id}/capture", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def refund_capture(capture_id, amount=None, currency="USD"):
    body = {} if amount is None else {"amount": {"value": f"{amount:.2f}", "currency_code": currency}}
    r = requests.post(f"{BASE}/v2/payments/captures/{capture_id}/refund",
                      headers=_headers({"PayPal-Request-Id": str(uuid.uuid4())}),
                      json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _approve_link(order):
    for l in order.get("links", []):
        if l["rel"] in ("payer-action", "approve"):
            return l["href"]


# ---------- Ledger ----------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS actions(
    id INTEGER PRIMARY KEY, ts REAL, tool TEXT, amount REAL,
    decision TEXT, reason TEXT, paypal_id TEXT)""")
db.execute("""CREATE TABLE IF NOT EXISTS approvals(
    id INTEGER PRIMARY KEY, ts REAL, amount REAL, description TEXT,
    status TEXT, order_id TEXT, approve_url TEXT)""")
db.commit()


def record(tool, amount, decision, reason, paypal_id=None):
    db.execute("INSERT INTO actions(ts,tool,amount,decision,reason,paypal_id) VALUES(?,?,?,?,?,?)",
               (time.time(), tool, amount, decision, reason, paypal_id))
    db.commit()


def reset_demo_data():
    db.execute("DELETE FROM actions")
    db.execute("DELETE FROM approvals")
    db.commit()


# ---------- Policy engine ----------
def spent_last_24h():
    return db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM actions WHERE tool='create_order' "
        "AND decision IN ('allow','human_approved') AND ts > ?", (time.time() - 86400,)).fetchone()[0]


def check_policy(amount):
    if amount > POLICY["per_txn_cap"]:
        return "deny", f"exceeds per-transaction cap of ${POLICY['per_txn_cap']:.2f}"
    if spent_last_24h() + amount > POLICY["daily_budget"]:
        return "deny", f"would exceed daily budget of ${POLICY['daily_budget']:.2f}"
    if amount > POLICY["approval_threshold"]:
        return "needs_approval", f"above approval threshold of ${POLICY['approval_threshold']:.2f}"
    return "allow", "within policy"


# ---------- Governed actions and approval queue ----------
def governed_create_order(amount, description=""):
    decision, reason = check_policy(amount)
    if decision == "deny":
        record("create_order", amount, "deny", reason)
        return {"decision": "deny", "reason": reason}
    if decision == "needs_approval":
        cur = db.execute("INSERT INTO approvals(ts,amount,description,status) VALUES(?,?,?,?)",
                         (time.time(), amount, description, "pending"))
        db.commit()
        record("create_order", amount, "needs_approval", reason)
        return {"decision": "needs_approval", "reason": reason, "approval_id": cur.lastrowid,
                "message": "Held for human approval. No PayPal order has been created."}
    order = create_order(amount)
    record("create_order", amount, "allow", reason, order["id"])
    return {"decision": "allow", "order_id": order["id"], "status": "awaiting_buyer_approval",
            "approve_url": _approve_link(order),
            "message": "Order created. No money moves until the buyer approves at approve_url."}


def approve_request(approval_id):
    row = db.execute("SELECT amount,status FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row or row[1] != "pending":
        return {"error": "request not found or not pending"}
    amount = row[0]
    if spent_last_24h() + amount > POLICY["daily_budget"]:
        return {"error": "would exceed daily budget"}
    order = create_order(amount)
    url = _approve_link(order)
    db.execute("UPDATE approvals SET status='approved', order_id=?, approve_url=? WHERE id=?",
               (order["id"], url, approval_id))
    db.commit()
    record("create_order", amount, "human_approved", f"approval #{approval_id}", order["id"])
    return {"order_id": order["id"], "status": "awaiting_buyer_approval", "approve_url": url}


def reject_request(approval_id):
    row = db.execute("SELECT amount FROM approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
    if not row:
        return {"error": "request not found or not pending"}
    db.execute("UPDATE approvals SET status='rejected' WHERE id=?", (approval_id,))
    db.commit()
    record("create_order", row[0], "human_rejected", f"approval #{approval_id}")
    return {"status": "rejected"}


# ---------- Agent tools: the only functions the LLM can see ----------
def create_payment(amount: float, description: str) -> dict:
    """Create a PayPal payment order for a given USD amount. Use when the user wants to charge or collect money. Returns the policy decision; an allowed order still needs buyer approval before any money moves."""
    return governed_create_order(amount, description)


def check_payment_status(order_id: str) -> dict:
    """Look up the current status of an existing PayPal order by its order_id."""
    r = requests.get(f"{BASE}/v2/checkout/orders/{order_id}", headers=_headers(), timeout=30)
    record("check_status", None, "allow", "read-only", order_id)
    return {"order_id": order_id, "status": r.json().get("status", "UNKNOWN")}


def refund_payment(capture_id: str, amount: float) -> dict:
    """Refund a USD amount from an already captured PayPal payment, identified by its capture_id. Use only when the user asks to refund or return money."""
    if amount > POLICY["per_txn_cap"]:
        reason = f"refund exceeds cap of ${POLICY['per_txn_cap']:.2f}"
        record("refund_payment", amount, "deny", reason, capture_id)
        return {"decision": "deny", "reason": reason}
    res = refund_capture(capture_id, amount)
    record("refund_payment", amount, "allow", "within policy", res["id"])
    return {"decision": "allow", "refund_id": res["id"], "status": res["status"]}


def capture_payment(order_id: str) -> dict:
    """Capture (finalize) a PayPal order after the buyer has approved it, so the money actually moves. Use only when the user asks to complete or capture a specific order_id."""
    known = db.execute(
        "SELECT 1 FROM actions WHERE paypal_id=? AND tool='create_order' "
        "AND decision IN ('allow','human_approved')", (order_id,)).fetchone()
    if not known:
        record("capture_payment", None, "deny", "order not created by this agent", order_id)
        return {"decision": "deny", "reason": "Unknown order: not created through the governed flow."}
    r = requests.get(f"{BASE}/v2/checkout/orders/{order_id}", headers=_headers(), timeout=30)
    status = r.json().get("status")
    if status != "APPROVED":
        record("capture_payment", None, "blocked", f"order status is {status}", order_id)
        return {"decision": "blocked", "status": status,
                "message": "The buyer has not approved this order yet. No money has moved."}
    cap = capture_order(order_id)
    c = cap["purchase_units"][0]["payments"]["captures"][0]
    amount = float(c["amount"]["value"])
    record("capture_payment", amount, "allow", "captured after buyer approval", c["id"])
    return {"decision": "allow", "status": cap["status"], "capture_id": c["id"], "amount": amount}


TOOLS = [create_payment, check_payment_status, refund_payment, capture_payment]
TOOL_MAP = {t.__name__: t for t in TOOLS}

SYSTEM = ("You are a payments agent. Use the provided tools for every payment action. "
          "Never claim a payment happened unless a tool result confirms it. "
          "An order with status awaiting_buyer_approval means NO money has moved yet; "
          "say the order is awaiting buyer approval and give the approve_url. "
          "If a tool returns deny or needs_approval, explain that to the user and do not retry.")

# ---------- Agent runner (Gemini function calling) ----------
_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=_secret("GEMINI_API_KEY"))
    return _client


def _retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                time.sleep(10 * (i + 1))
                continue
            raise
    raise RuntimeError("model unavailable after retries")


def run_agent(prompt):
    """Model picks tools, tools execute, model writes the reply. Returns (reply, tool_calls)."""
    from google.genai import types
    resp = _retry(lambda: _get_client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM, tools=TOOLS)))
    calls = [(p.function_call.name, dict(p.function_call.args))
             for turn in (resp.automatic_function_calling_history or [])
             for p in (turn.parts or []) if p.function_call]
    return resp.text, calls


def pick_tool(prompt):
    """Eval helper: tool choice only; the caller executes the tool."""
    from google.genai import types
    resp = _retry(lambda: _get_client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM, tools=TOOLS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))))
    return [(p.function_call.name, dict(p.function_call.args))
            for p in (resp.candidates[0].content.parts or []) if p.function_call]
