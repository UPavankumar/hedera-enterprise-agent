from modules.ai_extractor import extract_any


def process_pdf(path: str) -> dict:
    """
    Universal PDF invoice reader.
    Uses AI extraction (Groq) — works on any invoice format.
    Returns {invoice_number: canonical_dict}.
    """
    canonical = extract_any(path)
    inv_name  = canonical.get("document_number") or "UNKNOWN"
    return {inv_name: canonical}
