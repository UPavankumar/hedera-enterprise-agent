import os
import json
import logging
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class SubmissionLedger:
    COLUMNS = [
        "Timestamp", "Sender Email", "Source File", "Invoice Number",
        "Invoice Type", "Submission Status", "HTTP Status", "UUID",
        "Hedera TX", "Seller Name", "Buyer Name", "Total Amount",
        "Currency", "Error Message",
    ]

    def __init__(self):
        self.ledger_file = "Submission_Ledger.xlsx"
        self.records: list[dict] = []
        self._load_existing()

    def _load_existing(self):
        if os.path.exists(self.ledger_file):
            try:
                # Force Invoice Number and other potentially mixed columns to string
                df = pd.read_excel(self.ledger_file, dtype={"Invoice Number": str, "Hedera TX": str, "UUID": str})
                self.records = df.to_dict("records")
            except Exception as exc:
                logger.warning(f"Could not load existing ledger: {exc}")

    def add_submission(
        self,
        sender_email: str,
        source_file: str,
        invoice_number: str,
        invoice_type: str,
        submission_result: dict,
        json_payload: dict | None = None,
        request_logs: dict | None = None,
    ) -> None:
        body = submission_result.get("response_body", {})
        is_success = submission_result.get("isSuccess", False)
        status_code = submission_result.get("status_code", 200)

        record = {
            "Timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Sender Email":      sender_email,
            "Source File":       os.path.basename(source_file),
            "Invoice Number":    str(invoice_number), # Ensure string
            "Invoice Type":      str(invoice_type),   # Ensure string
            "Submission Status": "SUCCESS" if is_success else "PENDING",
            "HTTP Status":       status_code,
            "UUID":              submission_result.get("uuid", "DEMO-UUID"),
            "Hedera TX":         submission_result.get("hedera_tx", ""),
            "Seller Name":       (json_payload or {}).get("Seller Name", ""),
            "Buyer Name":        (json_payload or {}).get("Buyer Name", ""),
            "Total Amount":      (json_payload or {}).get("Total Payable Amount", ""),
            "Currency":          (json_payload or {}).get("Invoice Currency Code", "MYR"),
            "Error Message":     submission_result.get("error", ""),
        }
        self.records.append(record)

    def save(self) -> str:
        if not self.records:
            return self.ledger_file
        try:
            df = pd.DataFrame(self.records)
            for col in self.COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            df = df[self.COLUMNS]
            df.to_excel(self.ledger_file, index=False)
            logger.info(f"Ledger saved: {self.ledger_file}")
        except Exception as exc:
            logger.error(f"Ledger save failed: {exc}")
        return self.ledger_file
