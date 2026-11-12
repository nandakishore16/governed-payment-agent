"""Gradio UI: talk to the payments agent, approve held requests, watch spend and the audit ledger."""
import pandas as pd
import gradio as gr
import payments_agent as pa


def pending_df():
    return pd.read_sql_query(
        "SELECT id, amount, description FROM approvals WHERE status='pending' ORDER BY id", pa.db)


def ledger_df():
    return pd.read_sql_query(
        "SELECT id, datetime(ts,'unixepoch') AS time, tool, amount, decision, reason, paypal_id "
        "FROM actions ORDER BY id DESC LIMIT 50", pa.db)


def spend_text():
    spent, budget = pa.spent_last_24h(), pa.POLICY["daily_budget"]
    return f"### Spend (last 24h): ${spent:.2f} of ${budget:.2f} ({spent / budget:.0%})"


def refresh():
    return spend_text(), pending_df(), ledger_df()


def act(approval_id, action):
    if approval_id is None:
        return ("Enter an approval ID first.",) + refresh()
    fn = pa.approve_request if action == "approve" else pa.reject_request
    return (str(fn(int(approval_id))),) + refresh()


def reset():
    pa.reset_demo_data()
    return ("Demo data cleared.",) + refresh()


def chat(message, history):
    try:
        text, calls = pa.run_agent(message)
    except Exception as e:
        return f"Agent error: {str(e)[:300]}"
    tools = ", ".join(c[0] for c in calls) or "none"
    return f"{text}\n\n_Tools called: {tools}_"


with gr.Blocks(title="Governed Payment Agent") as demo:
    gr.Markdown("# Governed Payment Agent (PayPal sandbox)")
    with gr.Tab("Agent"):
        gr.ChatInterface(chat, examples=[
            "Charge $12 for the design review.",
            "Charge $80 for the annual license.",
            "Charge $30 for the workshop fee.",
        ])
    with gr.Tab("Approvals and ledger"):
        spend = gr.Markdown(spend_text())
        pend = gr.Dataframe(pending_df(), label="Pending requests")
        with gr.Row():
            aid = gr.Number(label="Approval ID", precision=0)
            b_ok, b_no, b_rf = gr.Button("Approve"), gr.Button("Reject"), gr.Button("Refresh")
            b_reset = gr.Button("Reset demo data")
        result = gr.Textbox(label="Result")
        ledger = gr.Dataframe(ledger_df(), label="Action ledger (latest 50)")
        outs = [result, spend, pend, ledger]
        b_ok.click(lambda i: act(i, "approve"), aid, outs)
        b_no.click(lambda i: act(i, "reject"), aid, outs)
        b_rf.click(lambda: ("",) + refresh(), None, outs)
        b_reset.click(reset, None, outs)

if __name__ == "__main__":
    demo.launch()
