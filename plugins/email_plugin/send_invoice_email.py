"""
send_invoice_email.py  –  Hedera Agent Kit compatible email notification tool.
Sends an invoice approval email via Gmail SMTP after Hedera transactions complete.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from hiero_sdk_python import Client
from pydantic import BaseModel, Field

from hedera_agent_kit.shared.configuration import Context
from hedera_agent_kit.shared.models import ToolResponse
from hedera_agent_kit.shared.tool import Tool

SEND_INVOICE_EMAIL_TOOL: str = "send_invoice_approval_email_tool"


class SendInvoiceEmailParameters(BaseModel):
    invoice_number: str = Field(description="The invoice number being approved")
    total_amount: str = Field(description="Total payable amount of the invoice")
    buyer_name: str = Field(description="Name of the invoice buyer/recipient")
    seller_name: str = Field(description="Name of the invoice seller")
    transaction_id: str = Field(description="Hedera transaction ID for the approval")
    recipient_email: Optional[str] = Field(
        default=None,
        description="Email address to notify. Defaults to EMAIL_TO env variable."
    )


class SendInvoiceApprovalEmailTool(Tool):
    """
    Hedera Agent Kit plugin tool that sends an invoice approval email
    after Hedera transactions (HCS audit + HBAR transfer) are complete.
    """

    def __init__(self, context: Context):
        self.method: str = SEND_INVOICE_EMAIL_TOOL
        self.name: str = "Send Invoice Approval Email"
        self.description: str = """
Send an email notification confirming that an enterprise invoice has been
approved and recorded on the Hedera network.

Use this tool AFTER completing the Hedera transactions (topic message + HBAR transfer).

Parameters:
- invoice_number (str, required): Invoice number/code
- total_amount (str, required): Total payable amount with currency
- buyer_name (str, required): Name of the buyer
- seller_name (str, required): Name of the seller
- transaction_id (str, required): Hedera transaction ID from the approval
- recipient_email (str, optional): Override recipient email address
"""
        self.parameters = SendInvoiceEmailParameters
        self.outputParser = None

    async def execute(
        self, client: Client, context: Context, params: Any
    ) -> ToolResponse:
        try:
            smtp_user     = os.getenv("EMAIL_ADDRESS")
            smtp_password = os.getenv("EMAIL_APP_PASSWORD")
            to_address    = params.recipient_email or os.getenv("EMAIL_TO", smtp_user)

            if not smtp_user or not smtp_password:
                return ToolResponse(
                    human_message="Email skipped: EMAIL_ADDRESS or EMAIL_APP_PASSWORD not configured.",
                    error=None,
                )

            subject = f"Invoice {params.invoice_number} Approved on Hedera"

            html_body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;padding:20px;border-radius:8px 8px 0 0">
    <h2 style="color:#00d4aa;margin:0">✅ Invoice Approved</h2>
    <p style="color:#aaa;margin:4px 0 0">Recorded on Hedera Testnet</p>
  </div>
  <div style="border:1px solid #ddd;border-top:none;padding:24px;border-radius:0 0 8px 8px">
    <table width="100%" cellpadding="8" style="border-collapse:collapse">
      <tr style="background:#f9f9f9">
        <td><strong>Invoice Number</strong></td>
        <td>{params.invoice_number}</td>
      </tr>
      <tr>
        <td><strong>Seller</strong></td>
        <td>{params.seller_name}</td>
      </tr>
      <tr style="background:#f9f9f9">
        <td><strong>Buyer</strong></td>
        <td>{params.buyer_name}</td>
      </tr>
      <tr>
        <td><strong>Total Amount</strong></td>
        <td><strong style="color:#00d4aa">{params.total_amount}</strong></td>
      </tr>
      <tr style="background:#f9f9f9">
        <td><strong>Hedera TX ID</strong></td>
        <td style="font-family:monospace;font-size:12px">{params.transaction_id}</td>
      </tr>
    </table>
    <p style="margin-top:20px;font-size:12px;color:#999">
      Verify on HashScan:
      <a href="https://hashscan.io/testnet/account/0.0.9069340">
        hashscan.io/testnet/account/0.0.9069340
      </a>
    </p>
  </div>
</body></html>
"""

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = smtp_user
            msg["To"]      = to_address
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, to_address, msg.as_string())

            return ToolResponse(
                human_message=f"Approval email sent to {to_address} for invoice {params.invoice_number}."
            )

        except Exception as e:
            return ToolResponse(
                human_message=f"Email send failed: {str(e)}",
                error=str(e),
            )
