#!/usr/bin/env python3
"""
extractor.py  –  PDF → canonical dict
Supports Type 01 (Invoice), 02 (Credit Note), 11 (Self-Billed).
"""

import re
import logging
import unicodedata
import pdfplumber

logger = logging.getLogger(__name__)


def _clean(val):
    if val is None:
        return None
    v = str(val).strip().rstrip(".")
    if v.upper() == "N/A":
        return "NA"
    return v if v else None


def _extract_amount(token: str):
    t = token.strip().rstrip(".")
    if re.fullmatch(r"[\d,]+\.\d{2}", t):
        return t
    return None


CURRENCY_REGEX = r"\d{1,3}(?:,\d{3})*\.\d{2}"

# Centralized stop words – used by description extraction for type 01/02/03
STOP_WORDS = [
    # financial totals
    "Sub-Total",
    "Service Tax",
    "TOTAL",
    "E & O.E",
    "Ringgit Malaysia",
    # breakdown markers
    "Manhour",
    "Charge",
    "Charges",
    "Rate",
    "Amount (",
    "Code",
    "Tax",
    # transaction markers
    "Professional Fees",
    "Re-issue",
    "Less Previous Invoice",
    "Progress Claim",
    "Adjustment",
]

# Known OCR ligature / encoding corrections applied after cid substitution
_OCR_CORRECTIONS = [
    # pattern              replacement
    (r"TNB\s+U[^\s]*lies\b",    "TNB Utilities"),   # "TNB Ulies", "TNB Ulties", etc.
    (r"\bUlies\b",              "Utilities"),
    (r"\bUlies\b",              "Utilities"),
    (r"\bfi\s*ca\s*tion\b",     "fication"),         # "Iden fi ca tion" artifacts
    (r"\(cid:415\)",            "ti"),
    (r"\(cid:414\)",            "fi"),
    (r"\(cid:407\)",            "fl"),
    (r"\(cid:\d+\)",            ""),
]


def _get_raw_text(pdf_path: str) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t:
                pages.append(t)
    text = "\n".join(pages)
    # Apply all OCR corrections in order
    for pattern, replacement in _OCR_CORRECTIONS:
        text = re.sub(pattern, replacement, text)
    # Normalize N/A → NA everywhere before any field parsing
    text = re.sub(r"\bN/A\b", "NA", text)
    return text


def _normalize_text(text: str) -> str:
    """NFKD-normalize, strip cid artifacts, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"\(cid:\d+\)", "", text)
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_first_paragraph_description(text: str) -> str | None:
    if not text:
        return None

    lines = text.splitlines()

    # Locate Description header
    header_pattern = re.compile(
        r"(item\s+description)|(description.*amount)",
        re.IGNORECASE,
    )

    start_idx = None
    for i, line in enumerate(lines):
        if header_pattern.search(line):
            start_idx = i
            break

    if start_idx is None:
        return None

    # Collect first paragraph only (until first blank line)
    paragraph_lines = []
    for line in lines[start_idx + 1:]:
        if not line.strip():
            break
        paragraph_lines.append(line.strip())

    if not paragraph_lines:
        return None

    paragraph_text = " ".join(paragraph_lines)

    # Strict: extract only text between first '-' and next '-'
    match = re.search(r"-\s*(.*?)\s*-\s*", paragraph_text, re.DOTALL)
    if not match:
        return None

    return _normalize_text(match.group(1))

def _detect_type(text: str) -> str:
    upper = text.upper()
    if "SELF-BILLED" in upper or "SELF BILLED" in upper:
        return "11"
    if "CREDIT NOTE" in upper:
        return "02"
    if "DEBIT NOTE" in upper:
        return "03"
    return "01"


def _extract_header(text: str) -> dict:
    """Extract company header block (name, address, tel, email) from top of PDF."""
    lines = text.splitlines()
    name_lines, address_parts = [], []
    tel = fax = email = None

    for ln in lines[:16]:
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^(GLOBAL|TECH|SOLUTIONS|SDN|BHD)", s, re.IGNORECASE):
            if re.search(r"Supplier|Buyer|Invoice|Credit|Self", s, re.IGNORECASE):
                break
            name_lines.append(s)
        elif re.search(r"\(\d{6,}-?[A-Z]\)", s) and name_lines:
            name_lines.append(s)
            break
        elif re.match(r"^B-\d|^Jalan|^Dataran|\d{5}|^No\.\d|^No\s+\d", s):
            address_parts.append(s)
        elif name_lines and re.match(r"^(Supplier|Buyer|Invoice|INVOICE|CREDIT|SELF)", s, re.IGNORECASE):
            break

    for ln in lines:
        if re.search(r"Tel[:\s]", ln, re.IGNORECASE) and not tel:
            m = re.search(r"Tel[:\s]+([0-9\-\s]+)", ln, re.IGNORECASE)
            if m:
                tel = _clean(m.group(1))
        if re.search(r"Fax[:\s]", ln, re.IGNORECASE) and not fax:
            m = re.search(r"Fax[:\s]+([0-9\-\s]+)", ln, re.IGNORECASE)
            if m:
                fax = _clean(m.group(1))
        if re.search(r"Email[:\s]", ln, re.IGNORECASE) and not email:
            m = re.search(r"Email[:\s]+([\w.\-]+@[\w.\-]+)", ln, re.IGNORECASE)
            if m:
                email = _clean(m.group(1))

    # Build a full address string from header address parts if available
    company_address = None
    if address_parts:
        # Try to reconstruct a proper address string that _split_address can parse
        company_address = ", ".join(address_parts)

    return {
        "company_name":    _clean(" ".join(name_lines)) if name_lines else None,
        "company_address": company_address,
        "tel":   tel,
        "fax":   fax,
        "email": email,
    }


def _extract_supplier_block(text: str) -> dict:
    """Extract Supplier-labelled fields from the PDF body."""
    out = dict(name=None, tin=None, registration_no=None, sst_id=None,
               misc_code=None, email=None, contact=None, address=None)

    for ln in text.splitlines():
        l = ln.strip()

        if re.search(r"Supplier\s+TIN", l, re.IGNORECASE) and not out["tin"]:
            m = re.search(r"Supplier\s+TIN\s*[:\.]?\s*([A-Z0-9]+)", l, re.IGNORECASE)
            if m:
                out["tin"] = m.group(1).strip()

        if re.search(r"Supplier\s+(?:Registration|Reg|New\s+Reg)", l, re.IGNORECASE) and not out["registration_no"]:
            m = re.search(r"(?:Registration|Reg)[^\d]*(\d{6,}[\d\-]*)", l, re.IGNORECASE)
            if m:
                out["registration_no"] = m.group(1).strip()

        if re.search(r"Supplier\s+SST", l, re.IGNORECASE) and not out["sst_id"]:
            m = re.search(r"SST\s+ID\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            if m:
                out["sst_id"] = m.group(1).strip()

        # MSIC / MISC code
        if re.search(r"Supplier\s+M[IS]+C\s*[Cc]ode", l, re.IGNORECASE) and not out["misc_code"]:
            m = re.search(r"M[IS]+C\s*[Cc]ode\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            if m:
                out["misc_code"] = m.group(1).strip()

        if re.search(r"Supplier\s+Name", l, re.IGNORECASE) and not out["name"]:
            m = re.search(r"Supplier\s+Name\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                raw = re.sub(r"\s+E-Invoice.*$", "", m.group(1), flags=re.IGNORECASE)
                raw = re.split(r"\s{2,}|SB Invoice|Invoice Date", raw)[0]
                out["name"] = _clean(raw)

        if re.search(r"Supplier\s+Email", l, re.IGNORECASE) and not out["email"]:
            m = re.search(r"Supplier\s+Email\s*[:\.]?\s*([\w.\-]+@[\w.\-]+)", l, re.IGNORECASE)
            if m:
                out["email"] = m.group(1).strip()

        if re.search(r"Supplier\s+Contact", l, re.IGNORECASE) and not out["contact"]:
            m = re.search(r"Supplier\s+Contact\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                out["contact"] = _clean(m.group(1))

        if re.search(r"Supplier\s+Address", l, re.IGNORECASE) and not out["address"]:
            # Variation 1: content on the same line after the colon
            m = re.search(r"Supplier\s+Address\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                val = _clean(m.group(1))
                # Discard colon-only or whitespace-only artifacts
                if val and val.strip().rstrip(":").strip():
                    out["address"] = val
            # Variation 2: blank / colon-only → address stays None (handled downstream)

        # Supplier Identification Number (SB invoices)
        if re.search(r"Supplier\s+Iden", l, re.IGNORECASE) and not out["registration_no"]:
            m = re.search(r"Number\s*[:\.]?\s*([\d\-]+)", l, re.IGNORECASE)
            if m:
                out["registration_no"] = m.group(1).strip()

    return out


def _extract_buyer_block(text: str) -> dict:
    """Extract Buyer-labelled fields from the PDF body."""
    out = dict(name=None, tin=None, registration_no=None, sst_id=None,
               email=None, contact=None, address=None,
               payment_term=None, bank_name=None, bank_account=None)

    lines         = text.splitlines()
    address_lines = []
    collect_addr  = False

    for ln in lines:
        l = ln.strip()
        if not l:
            collect_addr = False
            continue

        if re.search(r"Buyer\s+TIN", l, re.IGNORECASE) and not out["tin"]:
            m = re.search(r"Buyer\s+TIN\s*[:\.]?\s*([A-Z0-9]+)", l, re.IGNORECASE)
            if m:
                out["tin"] = m.group(1).strip()

        if re.search(r"Buyer\s+Name", l, re.IGNORECASE) and not out["name"]:
            m = re.search(r"Buyer\s+Name\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                raw = re.split(r"\s{2,}(?:Bank\s*[:\.]|Payment|Buyer Email)", m.group(1))[0]
                raw = re.sub(r"\s+Bank\s*[:\.].*$", "", raw, flags=re.IGNORECASE)
                out["name"] = _clean(raw)

        if re.search(r"Buyer\s+(?:Registration|Reg|New\s+Reg)", l, re.IGNORECASE) and not out["registration_no"]:
            m = re.search(r"(?:Registration|Reg)[^\d]*(\d{6,}[\d\-]*)", l, re.IGNORECASE)
            if m:
                out["registration_no"] = m.group(1).strip()

        if re.search(r"Buyer\s+SST", l, re.IGNORECASE) and not out["sst_id"]:
            m = re.search(r"SST\s+ID\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            if m:
                out["sst_id"] = m.group(1).strip()

        if re.search(r"Buyer\s*Email", l, re.IGNORECASE) and not out["email"]:
            m = re.search(r"Buyer\s*Email\s*[:\.]?\s*([\w.\-]+@[\w.\-]+)", l, re.IGNORECASE)
            if m:
                out["email"] = m.group(1).strip()

        if re.search(r"Buyer\s+Contact", l, re.IGNORECASE) and not out["contact"]:
            m = re.search(r"Buyer\s+Contact(?:\s+Number)?\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                out["contact"] = _clean(m.group(1))

        if re.search(r"Buyer\s+Address", l, re.IGNORECASE):
            m = re.search(r"Buyer\s+Address\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                address_lines = [_clean(m.group(1))]
                collect_addr  = True
            continue

        if collect_addr:
            if re.match(
                r"^(Attn|Item|No\.|Sub-?Total|TOTAL|E\s*&|Supplier|Buyer\s+[A-Z]|Payment|Bank\s*[:\.])",
                l, re.IGNORECASE,
            ):
                collect_addr = False
            else:
                address_lines.append(l)

        if re.search(r"Payment\s+[Tt]erm", l, re.IGNORECASE) and not out["payment_term"]:
            m = re.search(r"Payment\s+[Tt]erm\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                out["payment_term"] = _clean(re.split(r"\s{3,}", m.group(1))[0])

        if re.search(r"Bank\s+Account", l, re.IGNORECASE) and not out["bank_account"]:
            m = re.search(r"Bank\s+Account\s*[:\.]?\s*([\d\-]+)", l, re.IGNORECASE)
            if m:
                out["bank_account"] = m.group(1).strip()

        if re.search(r"^\s*Bank\s*[:\.]", l, re.IGNORECASE) and not out["bank_name"]:
            m = re.search(r"Bank\s*[:\.]?\s*(.+)", l, re.IGNORECASE)
            if m:
                raw = m.group(1).strip()
                if re.match(r"[\d\-]+\s", raw):
                    acct, _, name = raw.partition(" ")
                    if not out["bank_account"]:
                        out["bank_account"] = acct.strip()
                    out["bank_name"] = _clean(name)
                elif not re.match(r"Account", raw, re.IGNORECASE):
                    out["bank_name"] = _clean(raw)

        if (out["bank_account"] and not out["bank_name"]
                and re.search(r"\b(Bank|Berhad|Bhd|PBB)\b", l, re.IGNORECASE)
                and not re.search(r"Buyer|Supplier|Account", l, re.IGNORECASE)):
            out["bank_name"] = _clean(l)

    if address_lines:
        out["address"] = ", ".join(filter(None, address_lines))

    return out


def _extract_doc_ids(text: str, doc_type: str) -> dict:
    out = dict(document_number=None, document_date=None, currency=None,
               original_invoice_number=None, original_invoice_uuid=None,
               job_no=None)

    for ln in text.splitlines():
        l = ln.strip()

        if not out["document_number"]:
            if doc_type == "11":
                m = re.search(r"SB\s+Invoice\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            elif doc_type == "02":
                m = re.search(r"Credit\s+Note\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            elif doc_type == "03":
                m = re.search(r"Debit\s+Note\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            else:
                m = re.search(r"Invoice\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            if m:
                out["document_number"] = m.group(1).strip()

        if not out["document_date"]:
            m = re.search(r"(?:Invoice\s+Date|Date)\s*[:\.]?\s*(.+?)(?:\s{3,}|$)", l, re.IGNORECASE)
            if m:
                raw = _clean(m.group(1))
                if raw and not re.search(r"Invoice|Credit|SB", raw, re.IGNORECASE):
                    out["document_date"] = raw

        if not out["currency"]:
            m = re.search(r"[Ii]nvoice\s+currency\s+code\s*[:\.]?\s*([A-Z]{3})", l, re.IGNORECASE)
            if m:
                out["currency"] = m.group(1)

        # Job No. → Bill Reference Number
        if not out["job_no"]:
            m = re.search(r"Job\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
            if m:
                out["job_no"] = m.group(1).strip()

        if doc_type in ("02", "03"):
            if not out["original_invoice_number"]:
                m = re.search(r"Original\s+Invoice\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
                if m:
                    out["original_invoice_number"] = m.group(1).strip()
            if not out["original_invoice_uuid"]:
                m = re.search(r"UUID\s+No\s*[:\.]?\s*(\S+)", l, re.IGNORECASE)
                if m:
                    out["original_invoice_uuid"] = m.group(1).strip()

    return out


def _lines_type11(text: str) -> list:
    result = []
    for raw in text.splitlines():
        l  = raw.strip()
        m  = re.match(r"^(\d+)\.\s+(.+)", l)
        if not m:
            continue
        tokens = m.group(2).split()
        tail   = []
        for tok in reversed(tokens):
            if _extract_amount(tok) or tok == "-":
                tail.insert(0, tok)
            else:
                break
        line_total = None
        for tok in reversed(tail):
            if _extract_amount(tok):
                line_total = tok
                break
        desc_tokens = tokens[: len(tokens) - len(tail)]

        # Strip trailing classification code (3-digit number, e.g. "036")
        if desc_tokens and re.fullmatch(r"\d{3}", desc_tokens[-1]):
            desc_tokens.pop()

        # Additional SB variation: strip trailing zero/integer-only tokens
        # that represent tax-rate and tax-amount columns bled into the row
        # e.g. "Borang G&H Endorsement 0 0" → "Borang G&H Endorsement"
        while desc_tokens and re.fullmatch(r"0+\.?0*|\d+", desc_tokens[-1]):
            desc_tokens.pop()

        # Strip any remaining lone 3-digit code that may appear after the above
        if desc_tokens and re.fullmatch(r"\d{3}", desc_tokens[-1]):
            desc_tokens.pop()

        description = " ".join(desc_tokens).strip()
        if description and line_total:
            result.append(dict(description=description, quantity=None, rate=None, line_total=line_total))
    return result


def _lines_type01(text: str) -> list:
    """
    For Invoice (01): extract the first descriptive paragraph under the item
    table header as a single line item, using the subtotal as line_total.
    Falls back to the old numbered-row extraction if no paragraph is found.
    """
    description = _extract_first_paragraph_description(text)
    if description:
        # Pair with subtotal (the pre-tax amount for a single-block invoice)
        m = re.search(r"Sub-?Total\s+(" + CURRENCY_REGEX + ")", text, re.IGNORECASE)
        line_total = m.group(1) if m else None
        if description and line_total:
            return [dict(description=description, quantity=None, rate=None, line_total=line_total)]

    # Fallback: parse individual numbered rows (e.g. "1) Associate  2.5  220.00  550.00")
    result = []
    for raw in text.splitlines():
        l = raw.strip()
        m = re.match(r"^\d+\)\s+(.+)", l)
        if not m:
            continue
        tokens = m.group(1).split()
        nums   = []
        for tok in reversed(tokens):
            if _extract_amount(tok) or re.fullmatch(r"\d+\.?\d*", tok):
                nums.insert(0, tok)
            else:
                break
        desc  = " ".join(tokens[: len(tokens) - len(nums)]).strip()
        qty = rate = total = None
        if len(nums) >= 3:
            qty, rate, total = nums[0], nums[1], nums[2]
        elif len(nums) == 2:
            qty, total = nums[0], nums[1]
        elif len(nums) == 1:
            total = nums[0]
        if desc and total:
            result.append(dict(description=desc, quantity=qty, rate=rate, line_total=total))
    return result


def _lines_type02_03(text: str) -> list:
    """
    For Credit Note (02) and Debit Note (03): extract the first descriptive
    paragraph under the item table header as a single line item, using the
    subtotal as line_total.
    Falls back to the old trailing-amount row extraction if no paragraph is found.
    """
    description = _extract_first_paragraph_description(text)
    if description:
        m = re.search(r"Sub-?Total\s+(" + CURRENCY_REGEX + ")", text, re.IGNORECASE)
        line_total = m.group(1) if m else None
        if description and line_total:
            return [dict(description=description, quantity=None, rate=None, line_total=line_total)]

    # Fallback: trailing-amount row scan (original type02 logic)
    EXCLUDE = re.compile(
        r"^(Sub-?Total|Service\s+Tax|TOTAL|E\s*&\s*O|Less\s+Previous"
        r"|Ringgit|For\s+GTS|Authorised|Attn)",
        re.IGNORECASE,
    )
    result = []
    for raw in text.splitlines():
        l = raw.strip()
        if not l or EXCLUDE.match(l):
            continue
        m = re.search(r"([\d,]+\.\d{2})\s*$", l)
        if not m:
            continue
        desc = l[: m.start()].strip()
        if len(desc) < 5:
            continue
        result.append(dict(description=desc, quantity=None, rate=None, line_total=m.group(1)))
    return result


def _extract_totals(text: str, doc_type: str) -> dict:
    out = dict(subtotal=None, tax_amount=None, total_amount=None, amount_in_words=None, tax_type=None)
    for ln in text.splitlines():
        l = ln.strip()
        if doc_type == "11":
            if not out["total_amount"]:
                m = re.search(r"TOTAL\s+AMOUNT\s+([\d,]+\.\d{2})", l, re.IGNORECASE)
                if m:
                    out["total_amount"] = m.group(1)
        else:
            if not out["subtotal"]:
                m = re.match(r"Sub-?Total\s+([\d,]+\.\d{2})", l, re.IGNORECASE)
                if m:
                    out["subtotal"] = m.group(1)
            if not out["tax_amount"]:
                m = re.search(r"Service\s+Tax\s*\([^)]*\)\s*([\d,]+\.\d{2})", l, re.IGNORECASE)
                if m:
                    out["tax_amount"] = m.group(1)
                    out["tax_type"] = "Service Tax"
            # Check for Sales Tax as alternative
            if not out["tax_amount"]:
                m = re.search(r"Sales\s+Tax\s*\([^)]*\)\s*([\d,]+\.\d{2})", l, re.IGNORECASE)
                if m:
                    out["tax_amount"] = m.group(1)
                    out["tax_type"] = "Sales Tax"
            if not out["total_amount"]:
                m = re.search(r"(?<!\-)\bTOTAL\b\s+([\d,]+\.\d{2})", l, re.IGNORECASE)
                if m and not re.match(r"Sub", l, re.IGNORECASE):
                    out["total_amount"] = m.group(1)
            if not out["amount_in_words"]:
                m = re.search(
                    r"Ringgit\s+Malaysia\s*[:\.]?\s*:?\s*(.+?)(?:\s{3,}TOTAL|$)",
                    l, re.IGNORECASE,
                )
                if m:
                    out["amount_in_words"] = _clean(m.group(1))
    return out


def _build_canonical(doc_type, header, supplier, buyer, doc_ids, lines, totals, raw_text: str = None) -> dict:
    """
    Build canonical dict.

    For Type 11 (Self-Billed):
      PDF labels individual as 'Supplier', Generic Seller as 'Buyer'.
      But for SB invoices, Generic Seller is the seller (self-biller) and individual is the buyer.
      So we SWAP them: canonical seller = Generic Seller, canonical buyer = individual.
    """
    if doc_type == "11":
        # SWAP: seller = Generic Seller (from PDF buyer), buyer = individual (from PDF supplier)
        seller = dict(
            name            = buyer["name"] or header.get("company_name"),
            tin             = buyer["tin"],
            registration_no = buyer["registration_no"],
            sst_id          = buyer["sst_id"],
            misc_code       = buyer.get("misc_code"),
            address         = header.get("company_address") or buyer["address"],
            email           = header.get("email") or buyer["email"],
            contact         = header.get("tel") or buyer["contact"],
            bank_name       = buyer["bank_name"],
            bank_account    = buyer["bank_account"],
        )
        canon_buyer = dict(
            name            = supplier["name"],
            tin             = supplier["tin"],
            registration_no = supplier["registration_no"],
            sst_id          = supplier["sst_id"],
            misc_code       = supplier.get("misc_code"),
            address         = supplier["address"],
            email           = supplier["email"],
            contact         = supplier["contact"],
        )
    else:
        seller = dict(
            name            = supplier["name"] or header.get("company_name"),
            tin             = supplier["tin"],
            registration_no = supplier["registration_no"],
            sst_id          = supplier["sst_id"],
            misc_code       = supplier.get("misc_code"),
            address         = supplier["address"] or header.get("company_address"),
            email           = supplier["email"]   or header.get("email"),
            contact         = supplier["contact"] or header.get("tel"),
            bank_name       = buyer["bank_name"],
            bank_account    = buyer["bank_account"],
        )
        canon_buyer = dict(
            name            = buyer["name"],
            tin             = buyer["tin"],
            registration_no = buyer["registration_no"],
            sst_id          = buyer["sst_id"],
            address         = buyer["address"],
            email           = buyer["email"],
            contact         = buyer["contact"],
        )

    # ⚠️  CRITICAL: These fields affect ALL invoice types (INV/01, CN/02, DN/03, SB/11)
    # If you modify 'document_number' or 'document_date' here, changes will be reflected
    # across every single invoice regardless of type (SB, CN, DN, INV)
    return dict(
        doc_type                = doc_type,
        document_number         = doc_ids["document_number"],
        document_date           = doc_ids["document_date"],
        currency                = doc_ids.get("currency") or "MYR",
        job_no                  = doc_ids.get("job_no"),
        payment_term            = buyer.get("payment_term"),
        source_name             = None,
        seller                  = seller,
        buyer                   = canon_buyer,
        original_invoice_number = doc_ids.get("original_invoice_number") or "NA",
        original_invoice_uuid   = doc_ids.get("original_invoice_uuid") or "NA",
        lines                   = lines,
        totals                  = totals,
        raw_text                = raw_text,
    )


def extract(pdf_path: str) -> dict:
    """Extract and return canonical document dict from a PDF."""
    logger.info(f"Extracting: {pdf_path}")
    raw      = _get_raw_text(pdf_path)
    doc_type = _detect_type(raw)
    logger.info(f"  Detected type: {doc_type}")

    header   = _extract_header(raw)
    supplier = _extract_supplier_block(raw)
    buyer    = _extract_buyer_block(raw)
    doc_ids  = _extract_doc_ids(raw, doc_type)

    if doc_type == "11":
        lines = _lines_type11(raw)
    elif doc_type == "01":
        lines = _lines_type01(raw)
    else:  # "02" (Credit Note) and "03" (Debit Note)
        lines = _lines_type02_03(raw)

    totals = _extract_totals(raw, doc_type)
    return _build_canonical(doc_type, header, supplier, buyer, doc_ids, lines, totals, raw)