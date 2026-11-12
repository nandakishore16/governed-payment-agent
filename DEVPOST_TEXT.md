# Devpost submission text (paste into the form)

## Project name
Governed Payment Agent

## Tagline
Let AI agents spend money safely: policy limits, human approval, and an audit trail in front of PayPal.

## Description
AI agents are starting to pay and get paid for us. The unsolved problem is control: what stops an agent from overspending, being talked into a bad payment, or acting on an order nobody approved?

Governed Payment Agent puts a governance layer between a Gemini agent and the PayPal API. The model can only call four governed tools (create payment, check status, refund, capture). Every call passes through:
- a per-transaction cap ($50) that denies the request before PayPal is ever called,
- an approval threshold ($25): larger requests are held in a queue and no PayPal order exists until a human approves,
- a rolling daily budget ($100) that blocks split-payment attacks (many small charges that each look fine),
- capture checks: only orders the agent created, and only after the buyer approved on PayPal,
- an audit ledger recording every decision with its reason and PayPal ID.

The Gradio app has a chat tab for the agent and an approvals tab showing pending requests, spend against budget, and the ledger. Payments use PayPal Orders v2 (create, get, capture) and Payments v2 refunds with idempotency keys, all in the sandbox.

Built on the author's earlier Agent Governance Platform; the PayPal integration, approval queue, spend policy and payment tools were built during the hackathon.

## Tools used
- PayPal REST APIs (sandbox): OAuth, Orders v2, Payments v2 refunds
- Google Gemini 2.5 Flash (google-genai, function calling): chooses the tool and writes replies
- Python, SQLite (ledger), Gradio (UI)

## Challenges
PayPal Payouts is blocked for India accounts, so I built around Orders with buyer approval, which kept a real human step in the loop.

## What's next
Persistent ledger storage, per-agent budgets, and a larger repeated-trial evaluation of tool selection.

---

# Demo video script (target 2:30, hard limit 3:00)

0:00 Problem (15s, on screen: the Agent tab). "AI agents can now spend money. Who stops them? This is a governance layer in front of PayPal."
0:15 Happy path (40s). Ask: "Charge $12 for the design review." Show the approve link. Open it in the sandbox buyer window, Complete Purchase. Back in chat: "Capture order <id>." Show COMPLETED.
0:55 Hard limit (20s). "Charge $80 for the annual license." Denied; point out PayPal was never called.
1:15 Human approval (40s). "Charge $30 for the workshop fee." Held. Switch to the Approvals tab, Refresh, Approve, show the new order.
1:55 Split-payment attack (25s). Run the 24-dollar charges (see below). Budget blocks them.
2:20 Ledger and close (25s). Show the ledger with every decision. "Policy, approval and audit sit between the model and the money."

Recording tips: click "Reset demo data" first. Each agent run costs about 2 Gemini requests; the free tier allows 20 per day, so rehearse the split-attack with direct calls and record the agent runs once. Do not show your keys or the .env file. No music, or royalty-free only.
