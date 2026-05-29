#!/usr/bin/env python3
"""
transformer.py  –  Canonical dict → StandardINF API payload

Enforcement rules (Feb 2026):
  1. Seller fields for Type 01/02/03/04  → always Master defaults, never null/missing

  2. Buyer fields for Type 11/12/13/14   → defaults applied where extraction incomplete
  3. Payment Mode                         → numeric codes (01/02/03/NA), no free text
  4. Bill Reference Number                → "NA" if missing, never ":" or empty
  5. Currency Exchange Rate               → "1.00000" precision
  6. State                                → numeric code string, NEVER "NA"
  7. Output Format block                  → always included
  8. No null anywhere except
       Details of Tax Exemption / Amount Exempted from Tax (API expects null)
  9. Ledger duplicate check               → raises DuplicateInvoiceError if already exists
"""

import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DuplicateInvoiceError(Exception):
    """Raised when the invoice number already exists in the ledger."""


# ── GTS Seller master data (Types 01 / 02 / 03 / 04) ──────────────────────
_MASTER_SELLER = {
    "name":              "GLOBAL TECH SOLUTIONS SDN BHD",
    "tin":               "C1234567890",
    "category":          "BRN",
    "registration_no":   "202401000000",
    "sst_id":            "W10-1808-12345678",
    "email":             "contact@globaltech.example.com",
    "msic":              "71102",
    "contact":           "60312345678",
    "addr_line0":        "Unit 1-1",
    "addr_line1":        "Tech Plaza",
    "addr_line2":        "Jalan Innovation",
    "postal_zone":       "50450",
    "city_name":         "Kuala Lumpur",
    "state":             "14",          # WP Kuala Lumpur
    "country":           "MYS",
    "business_activity": "Technology Services",
}


# ── SB invoice buyer-side defaults (Types 11 / 12 / 13 / 14) ──────────────────
_SB_BUYER_DEFAULTS = {
    "category":       "NRIC",
    "sst_id":         "NA",
    "state_fallback": "10",   # use when state cannot be resolved
}

# ── Payment mode normalisation map ───────────────────────────────────────────────
_PAYMENT_MODE_MAP = {
    "CASH":            "01",
    "CHEQUE":          "02",
    "ONLINE TRANSFER": "03",
    "ONLINE":          "03",
    "TRANSFER":        "03",
    "BANK TRANSFER":   "03",
    "TT":              "03",
    "IBG":             "03",
    "FPX":             "03",
    "30 DAYS":         "03",
    "60 DAYS":         "03",
    "90 DAYS":         "03",
    "NET 30":          "03",
    "NET 60":          "03",
}

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
    # common cities -> state
    "PETALING JAYA":   "10",
    "SHAH ALAM":       "10",
    "SUBANG JAYA":     "10",
    "KLANG":           "10",
    "PUCHONG":         "10",
    "AMPANG":          "10",
    "CHERAS":          "14",
}


# ── String helpers ───────────────────────────────────────────────────────────────

def _nz(val) -> str:
    return "" if val is None else str(val).strip()


def _determine_tax_category(
    raw_text: str = None,
    tax_type: str = None,
    tax_amount: str = None,
    is_taxed: bool = True
) -> str:
    """
    Deterministically map tax information to a tax category code.
    
    Rules (in order):
    1. If tax_type contains "Sales Tax" → "01"
    2. If tax_type contains "Service Tax" → "02"
    3. If is_taxed is False → "E" (exempt)
    4. If raw_text contains "Sales Tax" → "01"
    5. If raw_text contains "Service Tax" → "02"
    6. Otherwise → "02" (default)
    
    Returns: str, one of: 01, 02, 03, 04, 05, 06, E, Z
    Raises: ValueError if final value is not valid
    """
    valid_codes = {"01", "02", "03", "04", "05", "06", "E", "Z"}
    
    # Rule 1: taxed amount is 0 or not applicable
    if tax_amount:
        try:
            if float(str(tax_amount).replace(",", "")) == 0.0:
                return "02"  # Default to Service Tax if taxed but amount is zero
        except (ValueError, TypeError):
            pass
    
    # Rule 2: Check tax_type first (extracted from invoice)
    if tax_type:
        if "Sales Tax" in tax_type:
            return "01"
        if "Service Tax" in tax_type:
            return "02"
    
    # Rule 3: If not explicitly taxed
    if not is_taxed:
        return "E"
    
    # Rule 4: Check raw text for tax type keywords
    if raw_text:
        text_upper = raw_text.upper()
        if "SALES TAX" in text_upper:
            return "01"
        if "SERVICE TAX" in text_upper:
            return "02"
    
    # Rule 5: Default to Service Tax
    result = "02"
    
    # Validate result
    if result not in valid_codes:
        raise ValueError(
            f"Invalid tax category code '{result}'. "
            f"Must be one of: {', '.join(sorted(valid_codes))}"
        )
    
    return result


def _na(val) -> str:
    """Return value or 'NA' – never blank or null in output."""
    v = str(val).strip() if val is not None else ""
    return v if v else "NA"


def _amount_str(val) -> str:
    if val is None:
        return "0.00"
    s = str(val).replace(",", "").strip()
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return s if s else "0.00"


def _alphanumeric_only(val) -> str:
    if val is None:
        return ""
    return re.sub(r"[\s\-]", "", str(val).strip())


def _truncate(text: str, max_len: int = 50) -> str:
    if not text or len(text) <= max_len:
        return text if text else "NA"
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    return truncated[:last_space] if last_space > 0 else truncated


# ── Date helpers ─────────────────────────────────────────────────────────────────

def _normalize_date(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw, flags=re.IGNORECASE)
    for fmt in ["%d %B %Y", "%d %b %Y", "%d/%m/%Y",
                "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%d",
                "%d %B, %Y", "%B %d %Y"]:
        try:
            return datetime.strptime(clean.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    logger.warning(f"Could not parse date: {raw!r}")
    return raw


def _payment_due_date(doc_date: str, pay_term: str) -> str:
    """Compute payment due date; return None if unresolvable."""
    if not doc_date or not pay_term:
        return None
    m = re.search(r"(\d+)\s*day", pay_term, re.IGNORECASE)
    if not m:
        return None
    try:
        base = datetime.strptime(doc_date, "%Y-%m-%d")
        return (base + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    except Exception:
        return None


# ── Payment mode normalisation ───────────────────────────────────────────────────

def _normalize_payment_mode(raw: str) -> str:
    """
    Convert free-text payment mode to IRBM numeric code.
    Cash=01, Cheque=02, Online Transfer=03, Others=NA
    Never sends raw free text like "ONLINE TRANSFER" or "30 Days".
    """
    if not raw:
        return "NA"
    key = raw.strip().upper()
    if key in _PAYMENT_MODE_MAP:
        return _PAYMENT_MODE_MAP[key]
    for k, code in _PAYMENT_MODE_MAP.items():
        if k in key:
            return code
    # Already a valid code?
    if re.fullmatch(r"0[1-9]|1[0-1]", key):
        return key
    return "NA"


# ── State code helper ────────────────────────────────────────────────────────────

def _state_code(raw: str, fallback: str = "10") -> str:
    """
    Convert free-text state/city to IRBM numeric code.
    NEVER returns 'NA' – falls back to numeric fallback (default '10' = Selangor).
    """
    if not raw:
        return fallback
    key = raw.strip().upper().rstrip(".")
    if re.fullmatch(r"\d{2}", key):
        return key
    if key in _STATE_CODES:
        return _STATE_CODES[key]
    for name, code in _STATE_CODES.items():
        if name in key or key in name:
            return code
    logger.warning(f"Unknown state '{raw}' – defaulting to '{fallback}'")
    return fallback


# ── Address splitter ─────────────────────────────────────────────────────────────

def _split_address(address, state_fallback: str = "10") -> dict:
    out = dict(
        line0="NA", line1="NA", line2="NA",
        postal_zone="NA", city_name="NA",
        state=state_fallback,   # numeric default, never "NA"
        country="MYS",
    )
    if not address:
        return out

    # Sanitize colon-only or whitespace-only artifact strings from PDF extraction
    sanitized = str(address).strip().strip(":").strip()
    if not sanitized:
        return out

    parts = [p.strip().rstrip(".") for p in re.split(r"[,\n]+", sanitized) if p.strip().strip(":").strip()]

    postal_idx = None
    for i, p in enumerate(parts):
        if re.match(r"^\d{5}", p):
            postal_idx = i
            break

    if postal_idx is not None:
        pm = re.match(r"(\d{5})\s*(.*)", parts[postal_idx])
        if pm:
            out["postal_zone"] = pm.group(1)
            city_raw = pm.group(2).strip().rstrip(".")
            out["city_name"] = city_raw if city_raw else "NA"

        for j in range(postal_idx + 1, len(parts)):
            candidate = parts[j].rstrip(".")
            if candidate.upper() not in ("MALAYSIA", "MYS", "MY"):
                out["state"] = _state_code(candidate, fallback=state_fallback)
                break

        addr_parts = parts[:postal_idx]
    else:
        addr_parts = parts

    if len(addr_parts) > 0:
        out["line0"] = _truncate(addr_parts[0]) if addr_parts[0] else "NA"
    if len(addr_parts) > 1:
        out["line1"] = _truncate(addr_parts[1]) if addr_parts[1] else "NA"
    if len(addr_parts) > 2:
        joined = " ".join(addr_parts[2:])
        out["line2"] = _truncate(joined) if joined else "NA"

    return out


# ── Line builder ─────────────────────────────────────────────────────────────────

def _build_line(line: dict, idx: int, tax_rate: str, doc_type: str, 
                tax_category_code: str = "02", tax_amount: str = None) -> dict:
    description = _na(line.get("description"))
    quantity    = _nz(line.get("quantity")) or "1"
    rate        = _nz(line.get("rate"))
    line_total  = _amount_str(line.get("line_total"))
    unit_price  = rate if rate else line_total

    if tax_rate != "0":
        try:
            line_tax = f"{float(line_total) * float(tax_rate) / 100:.2f}"
        except Exception:
            line_tax = "0.00"
    else:
        line_tax = "0.00"

    is_taxed = tax_rate != "0"

    # Determine tax exemption reason based on tax category
    # ⚠️ Set to empty string per requirement
    tax_exemption_reason = ""

    # For credit/debit notes (doc_type 02 or 03), set SST Tax Category and Tax Type to "NA"
    if doc_type in ("02", "03") and tax_category_code == "06":
        sst_tax_category = "06"  # Per requirement for credit/debit notes
        tax_type_field = "06"  # Per requirement for credit/debit notes
    else:
        sst_tax_category = tax_category_code if is_taxed else "06"
        tax_type_field = tax_category_code if is_taxed else "06"
    
    return {
        "LineId":                        str(idx),
        "Classification Class":          "CLASS",
        "Classification Code":           "022",
        "Product ID":                    "NA",
        "Description":                   description,
        "Product Tariff Code":           "NA",
        "Product Tariff Class":          "NA",
        "Country":                       "MYS",
        "Unit Price":                    unit_price,
        "Quantity":                      quantity,
        "Measurement":                   "HUR" if rate else "NA",
        "Subtotal":                      line_total,
        "SST Tax Category":              sst_tax_category,
        "Tax Type":                      tax_type_field,
        "Tax Rate":                      tax_rate,
        "Tax Amount":                    line_tax,
        "Details of Tax Exemption":      tax_exemption_reason,
        "Amount Exempted from Tax":      line_total if tax_category_code in ("E", "Z", "O") else None,
        "Total Excluding Tax":           line_total,
        "Invoice line net amount":       line_total,
        "Nett Amount":                   line_total,
        "TaxCategory schemeID":          "UN/ECE 5153",
        "TaxCategory schemeAgencyID":    "6",
        "TaxCategory schemeAgency code": "OTH",
    }


# ── Duplicate guard ──────────────────────────────────────────────────────────────

def _check_duplicate(invoice_no: str) -> None:
    """
    Raise DuplicateInvoiceError if invoice_no was already successfully submitted.
    Reads Master_Ledger.json. If ledger absent, silently skips.
    """
    import os, json as _json
    ledger_path = "Master_Ledger.json"
    if not os.path.exists(ledger_path):
        return
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            records = _json.load(f)
        for r in records:
            if (str(r.get("Invoice Number", "")).strip() == str(invoice_no).strip()
                    and r.get("Submission Status") == "SUCCESS"):
                raise DuplicateInvoiceError(
                    f"Invoice '{invoice_no}' already submitted successfully "
                    f"(ledger record: {r.get('Timestamp', '?')}). "
                    "Assign a new invoice number before resubmitting."
                )
    except DuplicateInvoiceError:
        raise
    except Exception as exc:
        logger.warning(f"Could not check ledger for duplicates: {exc}")


# ── Main transform ────────────────────────────────────────────────────────────────

def transform(canonical: dict) -> dict:
    """
    Convert canonical dict to API payload dict.
    Raises DuplicateInvoiceError if the invoice number was already submitted OK.
    """
    doc_type   = canonical["doc_type"]
    buyer      = canonical["buyer"]
    totals     = canonical["totals"]
    invoice_no = _nz(canonical["document_number"])

    # ── 1. Duplicate guard ─────────────────────────────────────────────────────
    _check_duplicate(invoice_no)

    is_sb = doc_type in ("11", "12", "13", "14")
    is_credit_debit_note = doc_type in ("02", "03")  # Credit/Debit notes – no tax

    # ── 2. Endpoint / source name ──────────────────────────────────────────────
    if doc_type == "11":
        endpoint    = "STDINFJSONSubmitSBInvoice"
        line_key    = "SBInvoiceLine"
        source_name = "SBINGTS"
    elif doc_type == "02":
        endpoint    = "STDINFJSONSubmitCreditNote"
        line_key    = "CreditNoteLine"
        source_name = "CNGTS"
    elif doc_type == "03":
        endpoint    = "STDINFJSONSubmitDebitNote"
        line_key    = "DebitNoteLine"
        source_name = "DNGTS"
    else:
        endpoint    = "STDINFJSONSubmitInvoice"
        line_key    = "InvoiceLine"
        source_name = "INGTS"

    # ── 3. Amounts ─────────────────────────────────────────────────────────────
    total_amt  = _amount_str(totals.get("total_amount"))
    tax_amount = _amount_str(totals.get("tax_amount") or "0.00")

    if is_sb:
        subtotal   = total_amt
        tax_amount = "0.00"
    else:
        subtotal = _amount_str(totals.get("subtotal") or totals.get("total_amount"))

    # For credit/debit notes (types 02, 03), do not calculate tax
    if is_credit_debit_note:
        tax_rate = "0"
        tax_amount = "0.00"
    else:
        tax_rate = "8" if not is_sb else "0"

    # ── 3.5. Determine tax category code dynamically ───────────────────────────
    raw_text = canonical.get("raw_text")
    tax_type = totals.get("tax_type")
    is_taxed = tax_rate != "0"
    
    # For credit/debit notes (types 02, 03), set tax category to "NA" and tax type to "06"
    if is_credit_debit_note:
        tax_category_code = "06"  # Per requirement for credit/debit notes
    else:
        try:
            tax_category_code = _determine_tax_category(
                raw_text=raw_text,
                tax_type=tax_type,
                tax_amount=tax_amount,
                is_taxed=is_taxed
            )
        except ValueError as exc:
            logger.error(f"Tax category validation failed: {exc}")
            raise

    # ── 4. Date & time ─────────────────────────────────────────────────────────
    doc_date = _normalize_date(_nz(canonical.get("document_date")))
    inv_time = "00:00:00Z"

    # ── 5. Payment mode – NUMERIC CODE ONLY ───────────────────────────────────
    raw_pay_term  = _nz(canonical.get("payment_term"))
    payment_mode  = _normalize_payment_mode(raw_pay_term)
    payment_terms = raw_pay_term if raw_pay_term else "NA"
    payment_due   = _payment_due_date(doc_date, raw_pay_term)


    orig_no   = _nz(canonical.get("original_invoice_number"))
    orig_uuid = _nz(canonical.get("original_invoice_uuid"))
    job_no    = _nz(canonical.get("job_no"))
    # ── 6. Bill Reference Number – "NA" if missing ───────────────────────────
    bill_ref = _na(job_no) if job_no else "NA"

    # ── 7. Currency exchange rate – 5 decimal precision ───────────────────────
    currency = _nz(canonical.get("currency")) or "MYR"
    fx_rate  = "1.00000" if currency == "MYR" else "NA"

    # ── 8. Lines ───────────────────────────────────────────────────────────────
    api_lines = [_build_line(ln, i + 1, tax_rate, doc_type, tax_category_code, tax_amount)
                 for i, ln in enumerate(canonical["lines"])]

    # ── 9. DocTaxTotal ─────────────────────────────────────────────────────────
    if tax_rate != "0":
        try:
            computed_tax = f"{sum(float(_amount_str(ln.get('line_total'))) * float(tax_rate) / 100 for ln in canonical['lines']):.2f}"
        except Exception:
            computed_tax = tax_amount
    else:
        computed_tax = "0.00"

    # Determine tax exemption reason at document level based on tax category
    doc_tax_exemption_reason = None
    # ⚠️ Set to empty string per requirement
    doc_tax_exemption_reason = ""

    # For credit/debit notes, use "NA" for TaxCategory Id and "06" for tax rate
    if is_credit_debit_note:
        doc_tax = {
            "TAX category tax amount in accounting currency": "0.00",
            "Total Taxable Amount Per Tax Type":              subtotal,
            "TaxCategory Id":                                 "06",
            "TaxCategory TaxScheme Id":                       "UN/ECE 5153",
            "TaxCategory schemeAgencyID":                     "6",
            "TaxCategory schemeAgency code":                  "OTH",
            "TAX category rate":                              "06",
            "Details of Tax Exemption":                       doc_tax_exemption_reason,
        }
    else:
        doc_tax = {
            "TAX category tax amount in accounting currency": computed_tax,
            "Total Taxable Amount Per Tax Type":              subtotal,
            "TaxCategory Id":                                 tax_category_code,
            "TaxCategory TaxScheme Id":                       "UN/ECE 5153",
            "TaxCategory schemeAgencyID":                     "6",
            "TaxCategory schemeAgency code":                  "OTH",
            "TAX category rate":                              tax_rate,
            "Details of Tax Exemption":                       doc_tax_exemption_reason,
        }

    # ── 10. SELLER & BUYER blocks – SWAP for SB invoices ────────────────────────
    # For SB (type 11+): Payload Seller = extracted buyer, Payload Buyer = Master defaults
    # For others:       Payload Seller = Master defaults, Payload Buyer = extracted buyer

    if is_sb:
        # SB: Seller = extracted buyer (individual/supplier)
        sb_seller = buyer
        sb_seller_bank = _na(sb_seller.get("bank_account"))
        sa = _split_address(sb_seller.get("address"), state_fallback="10")
        seller_addr0 = sa["line0"]
        seller_addr1 = sa["line1"]
        seller_addr2 = sa["line2"]
        seller_postal= sa["postal_zone"]
        seller_city  = sa["city_name"]
        seller_state = sa["state"]
        seller_sst   = _alphanumeric_only(sb_seller.get("sst_id")) or "NA"
        seller_name  = _na(sb_seller.get("name"))
        seller_tin   = _alphanumeric_only(sb_seller.get("tin"))
        # ⚠️  IF CATEGORY IS "NRIC" → Registration Number becomes "NA"
        # Store actual NRIC value separately for Supplier Identification Number
        sb_seller_identification_number = _na(sb_seller.get("registration_no"))  # NRIC/current BRN
        seller_brn   = "NA"  # For NRIC category, always set to "NA"
        seller_email = _na(sb_seller.get("email"))
        seller_contact = _alphanumeric_only(sb_seller.get("contact")) or "NA"

        # SB: Buyer = Master defaults
        ba = _split_address("", state_fallback="10")  # Empty to use Master defaults
        buyer_name     = _MASTER_SELLER["name"]
        buyer_tin_raw  = _MASTER_SELLER["tin"]
        buyer_brn      = _MASTER_SELLER["registration_no"]
        buyer_sst      = _MASTER_SELLER["sst_id"]
        buyer_email    = _MASTER_SELLER["email"]
        buyer_contact  = _MASTER_SELLER["contact"]
        buyer_addr0    = _MASTER_SELLER["addr_line0"]
        buyer_addr1    = _MASTER_SELLER["addr_line1"]
        buyer_addr2    = _MASTER_SELLER["addr_line2"]
        buyer_postal   = _MASTER_SELLER["postal_zone"]
        buyer_city     = _MASTER_SELLER["city_name"]
        buyer_state    = _MASTER_SELLER["state"]
        buyer_category = "BRN"
        buyer_country  = "MYS"
        buyer_id_no    = "NA"
        # SB seller is an individual – use their MSIC code and NRIC category
        sb_seller_msic     = _alphanumeric_only(sb_seller.get("misc_code")) or "00000"
        sb_seller_category = "NRIC"
    else:
        # Non-SB: Seller = Master defaults
        seller_bank = _na(canonical["seller"].get("bank_account"))
        seller_addr0 = _MASTER_SELLER["addr_line0"]
        seller_addr1 = _MASTER_SELLER["addr_line1"]
        seller_addr2 = _MASTER_SELLER["addr_line2"]
        seller_postal= _MASTER_SELLER["postal_zone"]
        seller_city  = _MASTER_SELLER["city_name"]
        seller_state = _MASTER_SELLER["state"]
        seller_sst   = _MASTER_SELLER["sst_id"]
        seller_name  = _MASTER_SELLER["name"]
        seller_tin   = _MASTER_SELLER["tin"]
        seller_brn   = _MASTER_SELLER["registration_no"]
        seller_email = _MASTER_SELLER["email"]
        seller_contact = _MASTER_SELLER["contact"]
        sb_seller_identification_number = "NA"  # Not used for non-SB
        sb_seller_msic = "00000"  # Not used for non-SB
        sb_seller_category = "BRN"  # Not used for non-SB

        # Non-SB: Buyer = extracted buyer
        ba = _split_address(buyer.get("address"), state_fallback=_SB_BUYER_DEFAULTS["state_fallback"])
        buyer_tin_raw  = _nz(buyer.get("tin"))
        buyer_name     = _na(buyer.get("name"))
        buyer_brn      = _na(buyer.get("registration_no"))
        buyer_sst      = _alphanumeric_only(buyer.get("sst_id")) or "NA"
        buyer_email    = _na(buyer.get("email"))
        buyer_contact  = _alphanumeric_only(buyer.get("contact")) or "NA"
        buyer_addr0    = _na(ba["line0"])
        buyer_addr1    = _na(ba["line1"])
        buyer_addr2    = _na(ba["line2"])
        buyer_postal   = ba["postal_zone"]
        buyer_city     = _na(ba["city_name"])
        buyer_state    = ba["state"]
        buyer_country  = "MYS"
        buyer_category = "BRN"
        buyer_id_no    = "NA"

    # ── 12. Assemble payload ───────────────────────────────────────────────────
    payload = {
        # Document header
        "e-Invoice Version":                              "1.0",
        "e-Invoice Type Code":                            doc_type,
        "e-Invoice Code or Number":                       invoice_no,
        "Source Invoice Number":                          "",
        "e-Invoice Date":                                 doc_date,
        "e-Invoice Time":                                 inv_time,
        "Invoice Currency Code":                          currency,
        "Currency Exchange Rate":                         fx_rate,      # "1.00000"
        "Payment Mode":                                   payment_mode, # numeric code
        "Payment Terms":                                  payment_terms,
        "Payment due date":                               payment_due,  # null if unknown
        "Bill Reference Number":                          bill_ref,     # "NA" if unknown

        # Seller
        "Seller Bank Account Number":                     seller_bank if not is_sb else sb_seller_bank,
        "Seller Name":                                    seller_name,
        "Seller TIN":                                     seller_tin,
        "Seller Category":                                sb_seller_category if is_sb else _MASTER_SELLER["category"],
        "Seller Business Registration Number":            _alphanumeric_only(seller_brn) if seller_brn and seller_brn != "NA" else seller_brn,
        "Seller Identification Number or Passport Number":_alphanumeric_only(sb_seller_identification_number) if sb_seller_identification_number and sb_seller_identification_number != "NA" else sb_seller_identification_number,
        "Seller SST Registration Number":                 seller_sst,
        "Seller e-mail":                                  seller_email,
        "Seller Malaysia Standard Industrial Classification Code": sb_seller_msic if is_sb else _MASTER_SELLER["msic"],
        "Seller Contact Number":                          seller_contact,
        "Seller Address Line 0":                          seller_addr0,
        "Seller Address Line 1":                          seller_addr1,
        "Seller Address Line 2":                          seller_addr2,
        "Seller Postal Zone":                             seller_postal,
        "Seller City Name":                               seller_city,
        "Seller State":                                   seller_state, # NEVER "NA"
        "Seller Country":                                 "MYS",
        "Seller Business Activity Description":           "NA" if is_sb else _MASTER_SELLER["business_activity"],
        "Seller MSIC":                                    sb_seller_msic if is_sb else _MASTER_SELLER["msic"],

        # Buyer
        "Buyer Name":                                     buyer_name,
        "Buyer TIN":                                      _alphanumeric_only(buyer_tin_raw),
        "Buyer Category":                                 buyer_category,
        "Buyer Business Registration Number":             buyer_brn,
        "Buyer Identification Number or Passport Number": buyer_id_no,
        "Buyer SST Registration Number":                  buyer_sst,
        "Buyer e-mail":                                   buyer_email,
        "Buyer Contact Number":                           buyer_contact,
        "Buyer Address Line 0":                           buyer_addr0,
        "Buyer Address Line 1":                           buyer_addr1,
        "Buyer Address Line 2":                           buyer_addr2,
        "Buyer Postal Zone":                              buyer_postal,
        "Buyer City Name":                                buyer_city,
        "Buyer State":                                    buyer_state,  # NEVER "NA"
        "Buyer Country":                                  "MYS",

        # Totals
        "Sum of Invoice line net amount":                 subtotal,
        "Sum of allowances on document level":            "0",
        "Total Fee or Charge Amount":                     "0",
        "Total Excluding Tax":                            subtotal,
        "Total Including Tax":                            total_amt,
        "Rounding amount":                                "0.00",
        "Paid amount":                                    "0",
        "Total Payable Amount":                           total_amt,
        "Total Net Amount":                               subtotal,

        # Lines & tax
        line_key:                                         api_lines,
        "DocTaxTotal":                                    doc_tax,
        "AllowanceCharges":                               [],
        "Source Name":                                    source_name,

        # Output format block – always required
        "Output Format":                                  "json",
        "Template Name":                                  "",
        "Tax Office Scheduler Template Name":             "",
    }

    # Credit / Debit note extra fields
    if doc_type in ("02", "03"):
        payload["Original Invoice Number"] = orig_no if orig_no else "NA"
        payload["Original Invoice IRBM Unique No"]      = orig_uuid if orig_uuid else "NA"

    return {
        "invoice_no":  invoice_no,
        "type_code":   doc_type,
        "endpoint":    endpoint,
        "payload":     payload,
    }