# Governed Payment Agent

An AI agent that can move money through PayPal, but only through guardrails: a policy engine, a human-approval queue and an audit ledger. Built for the PayPal AI Hackathon (PayPal sandbox only, no real money).

AI agents are starting to pay and get paid on our behalf. The open question is who stops an agent from misspending. This project puts a governance layer between the LLM and the PayPal API, so the model never touches PayPal directly.

It extends the author's earlier [Agent Governance Platform](https://github.com/nandakishore16/agent-governance-platform) (agent governance and cost observability) with a PayPal payments use case.

## How it works

```
User request -> Gemini agent -> governed tool -> policy engine -> PayPal sandbox
                                     |                |
                                     v                v
                              approval queue     audit ledger (SQLite)
```

| Rule | Default | Behavior |
|---|---|---|
| Per-transaction cap | $50 | Above it the request is denied, and PayPal is never called |
| Approval threshold | $25 | Above it the request is held for a human; no PayPal order exists until approved |
| Daily budget | $100 (rolling 24h) | Stops many small charges that each pass the per-transaction check (split-payment attack) |
| Capture | n/a | Only orders created through the governed flow, and only after the buyer approved on PayPal |
| Refund | $50 cap | Same cap as charges |

Every decision (allow, deny, needs_approval, human_approved, human_rejected, blocked) is written to the ledger with its reason and PayPal ID.

The agent has four tools: `create_payment`, `check_payment_status`, `refund_payment`, `capture_payment`. After a payment is allowed, the agent reports `awaiting_buyer_approval` and gives the PayPal approve link; money only moves after the buyer approves and the capture succeeds.

### PayPal capabilities used
PayPal REST API (sandbox): OAuth 2.0 token, Orders v2 (create with `payment_source` experience context, get, capture), Payments v2 (refunds, with `PayPal-Request-Id` idempotency key).

### AI tools used
Google Gemini 2.5 Flash via the `google-genai` SDK, using function calling. Gemini chooses which governed tool to call and writes the user-facing reply. The policy engine is plain Python and does not depend on the model.

## Run it

Requirements: Python 3.10+, a PayPal developer account (free sandbox), a Gemini API key.

```bash
git clone <this repo>
cd governed-payment-agent
pip install -r requirements.txt
cp .env.example .env        # then fill in the three keys, and export them:
export PAYPAL_CLIENT_ID=...   PAYPAL_SECRET=...   GEMINI_API_KEY=...
python dashboard.py         # opens http://127.0.0.1:7860
```

PayPal sandbox setup: at developer.paypal.com create a **Merchant** sandbox app under Apps & Credentials (Sandbox toggle on) and copy its Client ID and Secret. Under Testing Tools -> Sandbox Accounts, note the **Personal** (buyer) account's email and password; you use it to approve payments.

### Try it
1. **Agent tab:** "Charge $12 for the design review." The agent returns an order ID and a PayPal approve link. Open the link in a private window, log in with the sandbox buyer account, click Complete Purchase (the redirect page is expected). Then ask: "Capture order <order id>." The payment completes.
2. "Charge $80 for the annual license." Denied by the per-transaction cap.
3. "Charge $30 for the workshop fee." Held. Open the **Approvals and ledger** tab, click Refresh, enter the approval ID and click Approve.
4. Split-payment attack: several $24 charges in a row are allowed until the $100 daily budget is reached, then denied.
5. The ledger on the second tab shows every decision.

## Testing
Tool selection was checked by hand on four prompts (charge, over-limit charge, refund, status question) and in a partial scripted run where the first three cases (allow, needs_approval, deny) all chose the right tool and got the expected decision. A larger repeated-trial eval is not finished, so reliability across many runs is not claimed here.

## Known limitations
- **PayPal Payouts is not available for accounts in India** (`PAYOUT_NOT_AVAILABLE`), so payments use Orders with buyer approval. For a real deployment, payouts-style flows would need a supported region.
- The ledger is SQLite on local disk and resets when a container restarts. Fine for a demo, not for production.
- Sandbox only. The policy values are demo defaults in `payments_agent.py`.
- Free-tier Gemini quotas are small; a hosted demo would need a paid key.

## License
MIT. See `LICENSE`.
