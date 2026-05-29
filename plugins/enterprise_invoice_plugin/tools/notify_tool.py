import os
import requests
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field
from dotenv import load_dotenv
from hiero_sdk_python import Client
from hedera_agent_kit.shared.configuration import Context
from hedera_agent_kit.shared.models import ToolResponse
from hedera_agent_kit.shared.tool_v2 import BaseToolV2

load_dotenv()
logger = logging.getLogger(__name__)

NOTIFY_INVOICE_APPROVAL_TOOL = "notify_invoice_approval"


class NotifyInvoiceApprovalParams(BaseModel):
    invoice_number: str  = Field(...,  description="The invoice number or code being approved")
    seller_name:    str  = Field(...,  description="Name of the seller/vendor")
    buyer_name:     str  = Field(...,  description="Name of the buyer")
    total_amount:   str  = Field(...,  description="Total payable amount including tax")
    currency:       str  = Field("MYR", description="Invoice currency code")
    topic_id:       str  = Field(...,  description="Hedera HCS topic ID where audit message was submitted")
    tx_id:          str  = Field(...,  description="Hedera transaction ID for the HBAR transfer")


def notify_tool_prompt(context: Context = None) -> str:
    return """
    Sends an enterprise invoice approval notification after a Hedera transaction is complete.
    Use this tool AFTER the invoice has been approved on Hedera to notify stakeholders.

    Parameters:
    - invoice_number  (string, required): The invoice number or code
    - seller_name     (string, required): Vendor/seller company name
    - buyer_name      (string, required): Buyer company name
    - total_amount    (string, required): Total payable amount
    - currency        (string, optional): Currency code, default MYR
    - topic_id        (string, required): HCS topic ID used for audit
    - tx_id           (string, required): HBAR transfer transaction ID
    """


class NotifyInvoiceApprovalTool(BaseToolV2):

    def __init__(self, context: Context):
        super().__init__()
        self.method:      str = NOTIFY_INVOICE_APPROVAL_TOOL
        self.name:        str = "Notify Invoice Approval"
        self.description: str = notify_tool_prompt(context)
        self.parameters:  type[NotifyInvoiceApprovalParams] = NotifyInvoiceApprovalParams

    async def normalize_params(
        self, params: Any, context: Context, client: Client
    ) -> NotifyInvoiceApprovalParams:
        if isinstance(params, dict):
            return NotifyInvoiceApprovalParams(**params)
        return params

    async def core_action(
        self,
        normalized_params: NotifyInvoiceApprovalParams,
        context: Context,
        client: Client,
        **kwargs
    ) -> dict:
        # kwargs catch-all handles any unexpected parameters from different kit versions
        return {
            "invoice_number": normalized_params.invoice_number,
            "seller_name":    normalized_params.seller_name,
            "buyer_name":     normalized_params.buyer_name,
            "total_amount":   normalized_params.total_amount,
            "currency":       normalized_params.currency,
            "topic_id":       normalized_params.topic_id,
            "tx_id":          normalized_params.tx_id,
        }

    async def secondary_action(
        self, core_result: dict, client: Client, context: Context
    ) -> ToolResponse:
        webhook = os.getenv("SLACK_WEBHOOK_URL", "")
        notified = False

        if webhook and not webhook.endswith("..."):
            payload = {
                "text": (
                    f"*Enterprise Invoice Approved* :white_check_mark:\n"
                    f"Invoice:   `{core_result['invoice_number']}`\n"
                    f"Seller:    {core_result['seller_name']}\n"
                    f"Buyer:     {core_result['buyer_name']}\n"
                    f"Amount:    {core_result['total_amount']} {core_result['currency']}\n"
                    f"HCS Topic: `{core_result['topic_id']}`\n"
                    f"HBAR TX:   `{core_result['tx_id']}`"
                )
            }
            try:
                requests.post(webhook, json=payload, timeout=10)
                notified = True
            except Exception as e:
                logger.warning(f"Slack notification failed: {e}")

        # Always log to file as audit trail
        log_path = "invoice_approvals.log"
        with open(log_path, "a") as f:
            f.write(
                f"APPROVED | {core_result['invoice_number']} | "
                f"{core_result['seller_name']} -> {core_result['buyer_name']} | "
                f"{core_result['total_amount']} {core_result['currency']} | "
                f"Topic: {core_result['topic_id']} | TX: {core_result['tx_id']}\n"
            )

        msg = (
            f"Invoice {core_result['invoice_number']} approval notification sent. "
            f"Slack: {'sent' if notified else 'skipped (no webhook)'}. "
            f"Audit log written to {log_path}."
        )
        return ToolResponse(human_message=msg)

    async def handle_error(self, error: Exception, context: Context) -> ToolResponse:
        msg = f"Notification failed: {str(error)}"
        logger.error(msg)
        return ToolResponse(human_message=msg, error=msg)


def tool(context: Context) -> BaseToolV2:
    return NotifyInvoiceApprovalTool(context)
