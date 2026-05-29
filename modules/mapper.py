"""
mapper.py — Converts AI-extracted canonical dict to display/payload JSON.
Works with both AI extractor output and legacy rule-based extractor output.
"""


import re

# ── Malaysian state code map ─────────────────────────────────────────────────────
_STATE_CODES = {
    "JOHOR":           "01", "JHR": "01",
    "KEDAH":           "02", "KDH": "02",
    "KELANTAN":        "03", "KEL": "03",
    "MELAKA":          "04", "MLK": "04", "MALACCA": "04",
    "NEGERI SEMBILAN": "05", "NSN": "05",
    "PAHANG":          "06", "PHG": "06",
    "PULAU PINANG":    "07", "PNG": "07", "PENANG": "07",
    "PERAK":           "08", "PRK": "08",
    "PERLIS":          "09", "PLS": "09",
    "SELANGOR":        "10", "SGR": "10",
    "TERENGGANU":      "11", "TRG": "11",
    "SABAH":           "12", "SBH": "12",
    "SARAWAK":         "13", "SWK": "13",
    "KUALA LUMPUR":    "14", "WP KUALA LUMPUR": "14",
    "LABUAN":          "15", "WP LABUAN": "15",
    "PUTRAJAYA":       "16", "WP PUTRAJAYA": "16",
    "PETALING JAYA":   "10",
    "SHAH ALAM":       "10",
    "SUBANG JAYA":     "10",
    "KLANG":           "10",
    "PUCHONG":         "10",
}

def _normalize_state(address: str) -> str:
    """Extract and normalize state from address string."""
    if not address:
        return "10" # Default to Selangor
    upper_addr = address.upper()
    for state_name, code in _STATE_CODES.items():
        if state_name in upper_addr:
            return code
    return "10"

def _clean_num(val):
    """Clean numeric string: remove currency symbols and commas, return float or 0.0."""
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    # Remove everything except digits, decimal point, and minus sign
    cleaned = re.sub(r"[^\d.\-]", "", str(val))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def pdf_rows_to_json(canonical: dict) -> dict:
    """
    Takes canonical dict (from AI or rule-based extractor).
    Returns a clean, flat payload dict suitable for display and Hedera agent.
    Falls back to transformer.transform() for legacy format.
    """
    # Detect AI extractor output (has nested seller/buyer dicts or uses AI-specific keys)
    if isinstance(canonical.get("seller"), dict) and "name" in canonical.get("seller"):
        return _ai_canonical_to_json(canonical)

    # Legacy format — use existing transformer
    try:
        from modules.transformer import transform
        result = transform(canonical)
        return result["payload"]
    except Exception:
        return canonical


def _ai_canonical_to_json(c: dict) -> dict:
    seller  = c.get("seller",  {}) or {}
    buyer   = c.get("buyer",   {}) or {}
    totals  = c.get("totals",  {}) or {}
    payment = c.get("payment", {}) or {}
    lines   = c.get("lines",   [])

    payload = {
        # Header
        "e-Invoice Version":             "1.0",
        "e-Invoice Type Code":           _doc_type_code(c.get("doc_type", "invoice")),
        "e-Invoice Code or Number":      c.get("document_number", "UNKNOWN"),
        "e-Invoice Date":                c.get("document_date", ""),
        "Invoice Currency Code":         c.get("currency", "MYR"),
        "Reference":                     c.get("reference", ""),

        # Payment
        "Payment Mode":                  payment.get("mode", ""),
        "Payment Terms":                 payment.get("terms", ""),
        "Payment due date":              payment.get("due_date", ""),
        "Seller Bank Account Number":    payment.get("bank_account", ""),

        # Seller
        "Seller Name":                   seller.get("name", ""),
        "Seller TIN":                    seller.get("tin", ""),
        "Seller Business Registration Number": seller.get("registration_number", ""),
        "Seller Address":                seller.get("address", ""),
        "Seller State":                  _normalize_state(seller.get("address", "")),
        "Seller e-mail":                 seller.get("email", ""),
        "Seller Contact Number":         seller.get("phone", ""),

        # Buyer
        "Buyer Name":                    buyer.get("name", ""),
        "Buyer TIN":                     buyer.get("tin", ""),
        "Buyer Business Registration Number": buyer.get("registration_number", ""),
        "Buyer Address":                 buyer.get("address", ""),
        "Buyer State":                   _normalize_state(buyer.get("address", "")),
        "Buyer e-mail":                  buyer.get("email", ""),
        "Buyer Contact Number":          buyer.get("phone", ""),

        # Totals
        "Total Excluding Tax":           _clean_num(totals.get("subtotal")),
        "Total Tax Amount":              _clean_num(totals.get("tax_amount")),
        "Total Payable Amount":          _clean_num(totals.get("total_amount")),
        "Rounding Amount":               _clean_num(totals.get("rounding")),

        # Lines
        "InvoiceLine": [
            {
                "LineId":       ln.get("line_id", str(i + 1)),
                "Description":  ln.get("description", ""),
                "Quantity":     _clean_num(ln.get("quantity")) or 1.0,
                "Unit Price":   _clean_num(ln.get("rate")) or _clean_num(ln.get("line_total")),
                "Subtotal":     _clean_num(ln.get("line_total")),
                "Tax Rate":     _clean_num(ln.get("tax_rate")),
                "Tax Amount":   _clean_num(ln.get("tax_amount")),
            }
            for i, ln in enumerate(lines)
        ],
    }
    return payload


def _doc_type_code(doc_type: str) -> str:
    mapping = {
        "invoice":      "01",
        "credit_note":  "02",
        "debit_note":   "03",
        "self_billed":  "11",
    }
    return mapping.get(doc_type.lower(), "01")
