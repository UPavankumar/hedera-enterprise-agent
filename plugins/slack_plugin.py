import os
import requests
from dotenv import load_dotenv

load_dotenv()

WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")


def send_notification(invoice_number: str, tx_id: str) -> None:
    if not WEBHOOK:
        return
    payload = {
        "text": (
            f"*Enterprise Invoice Approved* :white_check_mark:\n"
            f"Invoice: `{invoice_number}`\n"
            f"Hedera TX: `{tx_id}`"
        )
    }
    try:
        requests.post(WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass
