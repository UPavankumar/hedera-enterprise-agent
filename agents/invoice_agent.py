"""
invoice_agent.py — Enterprise Invoice Approval Agent
Author: UPavankumar (https://github.com/UPavankumar)
Autonomous Hedera workflow using the Hedera Agent Kit.
"""
import os
import json as _json
import uuid
import asyncio
import logging

from dotenv import load_dotenv

from hedera_agent_kit.langchain.toolkit import HederaLangchainToolkit
from hedera_agent_kit.plugins import core_account_plugin, core_consensus_plugin
from hedera_agent_kit.shared.configuration import Configuration, Context, AgentMode

from hiero_sdk_python import (
    Client, Network, AccountId, PrivateKey,
    TopicCreateTransaction, TopicMessageSubmitTransaction,
    TransferTransaction, Hbar, TopicId,
)

from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from plugins.enterprise_invoice_plugin import (
    enterprise_invoice_plugin,
    enterprise_invoice_plugin_tool_names,
)

load_dotenv()
logger = logging.getLogger(__name__)

# Fetch credentials from .env
_ACCOUNT_ID  = os.getenv("ACCOUNT_ID", "0.0.9069340")
_PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
_GROQ_KEY    = os.getenv("GROQ_API_KEY", "")
_RECEIVER    = os.getenv("RECEIVER_ID", "0.0.1001")
_HBAR_AMOUNT = float(os.getenv("HBAR_AMOUNT", "0.01"))


def _load_private_key(raw: str) -> PrivateKey:
    if not raw:
        raise ValueError("PRIVATE_KEY not found in environment variables.")
    try:
        return PrivateKey.from_string(raw)
    except Exception:
        try:
            return PrivateKey.from_ecdsa(bytes.fromhex(raw[-64:]))
        except Exception:
            return PrivateKey.from_ecdsa(bytes.fromhex(raw))


def _minimal_summary(invoice_json: dict) -> dict:
    return {
        "invoice_number": invoice_json.get("e-Invoice Code or Number") or invoice_json.get("eInvoiceNumber", ""),
        "date":           invoice_json.get("e-Invoice Date", ""),
        "currency":       invoice_json.get("Invoice Currency Code", "MYR"),
        "total":          str(invoice_json.get("Total Payable Amount", "")),
        "seller":         invoice_json.get("Seller Name", ""),
        "buyer":          invoice_json.get("Buyer Name", ""),
    }


class InvoiceAgent:

    def __init__(self):
        self.account_id  = AccountId.from_string(_ACCOUNT_ID)
        self.private_key = _load_private_key(_PRIVATE_KEY)
        self.client      = Client(Network(network="testnet"))
        self.client.set_operator(self.account_id, self.private_key)

        # Keep toolkit for plugin architecture compliance
        # ONLY load the tools we actually need to save tokens
        self.toolkit = HederaLangchainToolkit(
            client=self.client,
            configuration=Configuration(
                tools=[
                    "create_topic_tool",
                    "submit_topic_message_tool",
                    "transfer_hbar_tool",
                    "notify_invoice_approval"
                ],
                plugins=[
                    core_account_plugin,
                    core_consensus_plugin,
                    enterprise_invoice_plugin,
                ],
                context=Context(
                    mode=AgentMode.AUTONOMOUS,
                    account_id=_ACCOUNT_ID,
                ),
            ),
        )
        self.all_tools = self.toolkit.get_tools()

    # ── Direct SDK calls (reliable fallback) ────────────────────────

    def _create_topic(self, memo: str) -> str:
        tx     = TopicCreateTransaction(memo=memo)
        resp   = tx.execute(self.client)
        receipt = resp.get_receipt(self.client)
        return str(receipt.topic_id)

    def _submit_message(self, topic_id: str, message: str) -> str:
        tid = TopicId.from_string(topic_id)
        tx  = TopicMessageSubmitTransaction(topic_id=tid, message=message)
        resp = tx.execute(self.client)
        receipt = resp.get_receipt(self.client)
        return str(resp.transaction_id)

    def _transfer_hbar(self, to_account: str, amount_hbar: float) -> str:
        tinybars = int(amount_hbar * 100_000_000)
        receiver = AccountId.from_string(to_account)
        tx = TransferTransaction(
            hbar_transfers={
                self.account_id: -tinybars,
                receiver:         tinybars,
            }
        )
        resp    = tx.execute(self.client)
        receipt = resp.get_receipt(self.client)
        return str(resp.transaction_id)

    # ── Plugin notify via agent (shows plugin extensibility) ─────────────────

    async def approve_invoice(self, invoice_json: dict) -> dict:
        summary  = _minimal_summary(invoice_json)
        inv_no   = summary["invoice_number"]
        msg_text = _json.dumps(summary)

        llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=_GROQ_KEY)

        agent = create_react_agent(
            model=llm,
            tools=self.all_tools,
            checkpointer=MemorySaver(),
            prompt=(
                "You are an expert Hedera AP Agent. You must complete the AP workflow strictly in order.\n\n"
                "RULES:\n"
                "1. ALWAYS wait for the output of a tool before calling the next tool.\n"
                "2. NEVER use placeholders. You must parse the JSON response from the tool to get the IDs.\n"
                "   Example response: {'topic_id': '0.0.1234', 'transaction_id': '...'}\n"
                "3. If a tool returns a Topic ID, you MUST use that exact string in the next tool call.\n"
                "4. If a tool returns a Transaction ID, you MUST use that exact string in the 'notify' tool call.\n\n"
                "WORKFLOW:\n"
                "Step 1: Create a topic (memo: 'EnterpriseInvoiceAudit').\n"
                "Step 2: Submit the invoice summary to the TOPIC_ID returned in Step 1.\n"
                "Step 3: Transfer 0.01 HBAR to '0.0.1001'.\n"
                "Step 4: Notify using the IDs from Step 2 and Step 3."
            ),
        )

        user_input = (
            f"ACT NOW: Approve this invoice on Hedera.\n"
            f"Data: {msg_text}\n\n"
            "DO NOT plan. Execute the steps one by one. Start by creating the topic."
        )

        logger.info(f"[{inv_no}] Starting autonomous approval workflow...")

        resp = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        )

        final_msg = resp["messages"][-1].content

        return {
            "isSuccess": True,
            "invoice_number": inv_no,
            "summary": final_msg,
            "hedera_tx": "See summary for details",
        }
