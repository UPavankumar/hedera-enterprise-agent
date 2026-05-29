"""
ai_extractor.py — Universal invoice extractor using Groq LLM.
Works on any invoice format: Malaysian e-Invoice, standard, credit note, etc.
Falls back to rule-based extractor if AI extraction fails.
"""

import os
import json
import logging
import re
import pdfplumber
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logger = logging.getLogger(__name__)

_GROQ_KEY = os.getenv("GROQ_API_KEY", "")

SYSTEM_PROMPT = """You are an expert invoice data extractor specializing in Malaysian e-Invoices and international standards.
Your task is to extract structured data from the provided text and return ONLY a valid JSON object.

Required JSON Structure:
{
  "document_number": "Exact invoice/credit note/debit note number",
  "doc_type": "invoice|credit_note|debit_note|self_billed",
  "document_date": "YYYY-MM-DD",
  "currency": "3-letter ISO code (e.g., MYR, USD)",
  "seller": {
    "name": "Full legal name",
    "tin": "Tax Identification Number",
    "registration_number": "Business registration number",
    "address": "Full physical address",
    "email": "Contact email",
    "phone": "Contact phone"
  },
  "buyer": {
    "name": "Full legal name",
    "tin": "Tax Identification Number",
    "registration_number": "Business registration number",
    "address": "Full physical address",
    "email": "Contact email",
    "phone": "Contact phone"
  },
  "lines": [
    {
      "line_id": "1",
      "description": "Clear item description",
      "quantity": 1.0,
      "rate": 100.0,
      "line_total": 100.0,
      "tax_rate": 0.0,
      "tax_amount": 0.0
    }
  ],
  "totals": {
    "subtotal": 100.0,
    "tax_amount": 0.0,
    "total_amount": 100.0,
    "rounding": 0.0
  },
  "payment": {
    "mode": "Cash|Cheque|Transfer|etc",
    "terms": "e.g., Net 30",
    "due_date": "YYYY-MM-DD",
    "bank_account": "Account number"
  },
  "reference": "Any internal reference or job number"
}

Extraction Rules:
1. Doc Type Detection: 
   - "Self-Billed" in text -> "self_billed"
   - "Credit Note" in text -> "credit_note"
   - "Debit Note" in text -> "debit_note"
   - Default -> "invoice"
2. Cleaning: Remove currency symbols (RM, MYR, $), commas from numbers, and extra whitespace.
3. Missing Data: Use null for missing numbers/dates, empty string for missing text.
4. Line Items: Extract EVERY line item. If quantity or rate is missing but line_total exists, set quantity to 1 and rate to line_total.
5. Dates: Standardize to YYYY-MM-DD.
6. JSON ONLY: No preamble, no markdown code blocks, no trailing text.
"""


def _extract_pdf_text(path: str) -> str:
    """Extract all text from PDF with improved table handling."""
    content = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # Extract text with layout preservation
                text = page.extract_text(x_tolerance=2, y_tolerance=2)
                if text:
                    content.append(text)
                
                # Try to extract tables and append as structured text
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            # Filter out None values and join with |
                            row_text = " | ".join([str(cell).strip() for cell in row if cell is not None])
                            if row_text.strip():
                                content.append(f"| {row_text} |")
    except Exception as e:
        logger.error(f"pdfplumber error: {e}")
    
    return "\n".join(content)


def _post_process_clean(val):
    if isinstance(val, str):
        # Remove common OCR/PDF artifacts
        val = re.sub(r"\(cid:\d+\)", "", val)
        val = re.sub(r"\s+", " ", val).strip()
        # Remove trailing colons/dots/spaces
        val = val.rstrip(":").rstrip(".").strip()
        return val
    return val


def _ai_extract(text: str) -> dict:
    """Use Groq to extract structured invoice data."""
    client = Groq(api_key=_GROQ_KEY)

    # Use the more capable 70b model for cleaner extraction
    model = "llama-3.3-70b-versatile"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"Extract invoice data from this text:\n\n{text[:6000]}"},
        ],
        temperature=0.0, # Most deterministic
    )

    raw = response.choices[0].message.content.strip()

    # Robust JSON extraction
    try:
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        
        data = json.loads(raw.strip())

        # Recursive cleaning
        def clean_recursive(obj):
            if isinstance(obj, dict):
                return {k: clean_recursive(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [clean_recursive(i) for i in obj]
            else:
                return _post_process_clean(obj)

        return clean_recursive(data)
    except Exception as e:
        logger.error(f"JSON parsing error: {e}. Raw output: {raw}")
        raise


def extract_any(path: str) -> dict:
    """Universal extractor with robust AI processing."""
    try:
        text = _extract_pdf_text(path)
        if not text.strip():
            raise ValueError("No text extracted from PDF")

        canonical = _ai_extract(text)
        logger.info(f"AI extraction succeeded: {canonical.get('document_number')}")

        # Post-processing normalization
        if not isinstance(canonical.get("lines"), list):
            canonical["lines"] = []
        
        if not isinstance(canonical.get("totals"), dict):
            canonical["totals"] = {}

        return canonical

    except Exception as e:
        logger.warning(f"AI extraction failed ({e}), falling back to rule-based")
        try:
            from modules.extractor import extract
            return extract(path)
        except Exception as e2:
            logger.error(f"Rule-based fallback also failed: {e2}")
            return {
                "document_number": "UNKNOWN",
                "doc_type": "invoice",
                "document_date": "",
                "currency": "MYR",
                "seller": {"name": "", "tin": ""},
                "buyer": {"name": "", "tin": ""},
                "lines": [],
                "totals": {"subtotal": 0, "tax_amount": 0, "total_amount": 0},
                "payment": {},
                "reference": "",
            }
