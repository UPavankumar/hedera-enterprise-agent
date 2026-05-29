import asyncio
import os
import tempfile

import pandas as pd
import streamlit as st

from modules.pdf_reader import process_pdf
from modules.mapper import pdf_rows_to_json
from modules.ledger import SubmissionLedger

st.set_page_config(page_title="Enterprise Invoice Agent", layout="wide")

# ── Sidebar Bounty Context ──────────────────────────────────────────────────
with st.sidebar:
    st.title("🏆 Hedera AI Bounty")
    st.info("**Week 2: Enterprise Agent + Plugin**")
    st.markdown("""
    ### Submission by
    [**UPavankumar**](https://github.com/UPavankumar)
    
    ### How it works
    1. **Build** an agent using Hedera Agent Kit.
    2. **Integrate** with real-world tools.
    3. **Submit** to win HBAR.
    
    ### Bounty Schedule
    - Week 1: Fun Basic Agent (Passed)
    - **Week 2: Enterprise Agent (Live)**
    - Week 3: MCP/x402 Agent
    - Week 4: Commerce Agent
    - Week 5: Policy Agent
    """)
    st.divider()
    st.caption("Built for the Hedera AI Agent Bounty program.")

st.title("Enterprise Autonomous Accounts Payable Agent")
st.caption("Powered by Hedera Agent Kit · Groq LLaMA 3.3 · HCS Audit Trail")

# ── Session state init ────────────────────────────────────────────────────────
if "invoices" not in st.session_state:
    st.session_state.invoices = {}
if "json_payloads" not in st.session_state:
    st.session_state.json_payloads = {}
if "approval_results" not in st.session_state:
    st.session_state.approval_results = {}
if "ledger_saved" not in st.session_state:
    st.session_state.ledger_saved = False

@st.cache_resource
def get_agent():
    from agents.invoice_agent import InvoiceAgent
    return InvoiceAgent()

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader("Upload Invoice (PDF)", type=["pdf"])

if uploaded_file:
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    st.success(f"Uploaded: {uploaded_file.name}")

    if st.button("Process Invoice", type="primary"):
        with st.spinner("Extracting invoice data..."):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(uploaded_file.read())
                    temp_path = tmp.name

                invoices = process_pdf(temp_path)
                st.session_state.invoices = invoices
                st.session_state.json_payloads = {}
                st.session_state.approval_results = {}
                st.session_state.ledger_saved = False

                for inv_name, canonical in invoices.items():
                    try:
                        json_obj = pdf_rows_to_json(canonical)
                        json_obj["eInvoiceNumber"] = inv_name
                        st.session_state.json_payloads[inv_name] = json_obj
                    except Exception as e:
                        st.warning(f"Mapper error for {inv_name}: {e}")

                st.success(f"Found {len(invoices)} invoice(s) — scroll down")
            except Exception as e:
                st.error(f"Extraction failed: {e}")

# ── Show results ──────────────────────────────────────────────────────────────
if st.session_state.invoices:
    ledger = SubmissionLedger()

    for inv_name, canonical in st.session_state.invoices.items():
        st.divider()
        st.subheader(f"Invoice: {inv_name}")

        lines = canonical.get("lines", [])
        if lines:
            st.dataframe(pd.DataFrame(lines), width="stretch")

        totals = canonical.get("totals", {})
        col1, col2, col3 = st.columns(3)
        col1.metric("Subtotal", totals.get("subtotal", "—"))
        col2.metric("Tax", totals.get("tax_amount", "0.00"))
        col3.metric("Total", totals.get("total_amount", "—"))

        json_obj = st.session_state.json_payloads.get(inv_name, {})
        if json_obj:
            with st.expander("Structured Invoice JSON"):
                st.json(json_obj)

        # ── Approve button ────────────────────────────────────────────────────
        if inv_name not in st.session_state.approval_results:
            if st.button(f"Approve on Hedera: {inv_name}", key=f"btn_{inv_name}", type="primary"):
                with st.spinner("Agent executing autonomous Hedera workflow..."):
                    try:
                        agent = get_agent()
                        # Refactored agent returns a dict with results
                        result_dict = asyncio.run(agent.approve_invoice(json_obj))
                        
                        # Now add to ledger with REAL results
                        ledger.add_submission(
                            sender_email="demo@hedera.ai",
                            source_file=uploaded_file.name if uploaded_file else "unknown",
                            invoice_number=inv_name,
                            invoice_type=json_obj.get("e-Invoice Type Code", "01"),
                            submission_result={
                                "status_code": 200,
                                "isSuccess": True,
                                "uuid": result_dict.get("uuid", "DEMO-UUID"),
                                "hedera_tx": result_dict.get("hedera_tx", "See Summary"),
                                "error": "",
                            },
                            json_payload=json_obj,
                            request_logs={},
                        )
                        ledger.save()
                        
                        st.session_state.approval_results[inv_name] = result_dict.get("summary", "Approved")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Agent error: {e}")
        else:
            st.success("Invoice Approved on Hedera Testnet")
            st.code(st.session_state.approval_results[inv_name], language="text")

    try:
        # Force Invoice Number to string to prevent Arrow serialization errors with mixed types (e.g., '2637' and 'CN2601')
        df_ledger = pd.read_excel("Submission_Ledger.xlsx", dtype={"Invoice Number": str, "Hedera TX": str, "UUID": str})
        with st.expander("Submission Ledger"):
            st.dataframe(df_ledger, width="stretch")
    except Exception:
        pass
