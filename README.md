# Enterprise Autonomous Accounts Payable Agent

> **Author:** [UPavankumar](https://github.com/UPavankumar)  
> **Submission for:** [Hedera AI Agent Bounty — Week 2: Enterprise Agent + Plugin](https://github.com/UPavankumar)

---

## 🏆 Hedera AI Agent Bounty Context

### How it works
Build an agent. Submit. Win HBAR.
- **01 Pick a bounty:** A new bounty opens every Monday.
- **02 Build with the Hedera Agent Kit:** Ship your project as a public GitHub repo using the Hedera Agent Kit (JS or Python).
- **03 Submit before Sunday 23:59 UTC:** Drop your repo, a live demo or social-media post, and a short description.
- **04 Get paid in HBAR:** Winners are announced at the end of the judging period.

### The Bounties
| Week | Bounty | Status | Payout |
|---|---|---|---|
| Week 1 | Fun Basic Hedera Agent | Passed | $500 |
| **Week 2** | **Enterprise Agent + Plugin** | **Live** | **$750** |
| Week 3 | MCP or x402 Agent | Upcoming | $1,000 |
| Week 4 | Hedera Commerce Agent | Upcoming | $1,000 |
| Week 5 | Hedera Policy Agent | Upcoming | $1,500 |

### My Submission: Enterprise Agent + Plugin
**Project Name:** Enterprise Autonomous Accounts Payable Agent  
**Bounty:** Week 2: Enterprise Agent + Plugin  
**Implementation:** Integrates Hedera Agent Kit with a real-world enterprise workflow (PDF extraction, HCS audit, HBAR transfer, and custom plugin notifications).

---

## What It Does

An enterprise-grade Accounts Payable agent that:
- Accepts invoice PDFs (supporting Malaysian e-Invoice and international standards)
- **AI-Powered Extraction:** Uses **LLaMA 3.3 70B** with recursive cleaning to extract and structure 80+ invoice fields with near-perfect accuracy.
- **State & Number Normalization:** Automatically maps addresses to official Malaysian state codes (e.g., Selangor → 10) and cleans currency/OCR artifacts.
- **Autonomous Approval:** A multi-step autonomous agent executes a full workflow:
  - Submits an immutable audit record to **Hedera Consensus Service (HCS)**.
  - Executes a **commercial HBAR transfer** on Hedera testnet.
  - Notifies stakeholders via a custom-built **Hedera Agent Kit plugin**.
- **Immutable Ledger:** Maintains a cumulative Excel submission ledger with real-time transaction verification.

All transactions are publicly verifiable on [HashScan Testnet](https://hashscan.io/testnet).

---

## Live Demo

| Resource | Link |
|---|---|
| HashScan Account | [0.0.9069340](https://hashscan.io/testnet/account/0.0.9069340) |
| Demo Video | *(X post link)* |
| GitHub Repo | *(this repo)* |

---

## Bounty Requirements Checklist

| Requirement | Status | Evidence |
|---|---|---|
| Public GitHub repository | ✅ | This repo |
| Built using Hedera Agent Kit (Python) | ✅ | `agents/invoice_agent.py` — `HederaLangchainToolkit` |
| Two non-query Hedera tools | ✅ | `CREATE_TOPIC_TOOL` + `TRANSFER_HBAR_TOOL` + `SUBMIT_TOPIC_MESSAGE_TOOL` |
| Commercial transaction | ✅ | 0.01 HBAR transfer to `0.0.1001` on testnet |
| Third-party / custom plugin | ✅ | `enterprise_invoice_plugin` — built from scratch using `BaseToolV2` |
| Live demo URL or X video | ✅ | *(X post link)* |
| Enterprise workflow | ✅ | PDF extraction → JSON → HCS audit → HBAR transfer → ledger |
| Feedback submitted | ✅ | *(feedback link)* |

---

## Architecture

```
project/
├── streamlit_app.py                     ← Streamlit UI
├── requirements.txt
├── .env
│
├── agents/
│   └── invoice_agent.py                 ← HederaLangchainToolkit + Groq agent
│
├── modules/
│   ├── extractor.py                     ← PDF → canonical dict
│   ├── transformer.py                   ← canonical dict → e-Invoice JSON
│   ├── pdf_reader.py                    ← wrapper for extractor
│   ├── mapper.py                        ← wrapper for transformer
│   └── ledger.py                        ← Excel submission ledger
│
└── plugins/
    ├── slack_plugin.py                  ← legacy helper (kept for reference)
    └── enterprise_invoice_plugin/       ← custom Hedera Agent Kit plugin
        ├── __init__.py
        ├── plugin.py                    ← Plugin definition
        └── tools/
            └── notify_tool.py           ← BaseToolV2 implementation
```

---

## Hedera Tools Used

| Tool | Plugin | Type | What It Does |
|---|---|---|---|
| `CREATE_TOPIC_TOOL` | `core_consensus_plugin` | **Non-query** | Creates `EnterpriseInvoiceAudit` HCS topic |
| `SUBMIT_TOPIC_MESSAGE_TOOL` | `core_consensus_plugin` | **Non-query** | Writes invoice summary to HCS (immutable audit) |
| `TRANSFER_HBAR_TOOL` | `core_account_plugin` | **Non-query** | Transfers 0.01 HBAR — commercial transaction |
| `NOTIFY_INVOICE_APPROVAL_TOOL` | `enterprise_invoice_plugin` | **Custom** | Sends approval notification + writes audit log |

---

## Custom Plugin: `enterprise_invoice_plugin`

Built from scratch following the [Hedera Agent Kit Plugin Architecture](https://docs.hedera.com/hedera/open-source-solutions/ai-studio-on-hedera/hedera-ai-agent-kit/hedera-agent-kit-py/create-py-plugins).

### Plugin Details

| Field | Value |
|---|---|
| Name | `enterprise-invoice-plugin` |
| Version | `1.0.0` |
| Pattern | `BaseToolV2` (v4 lifecycle) |
| Built for | Enterprise AP workflow on Hedera |

### Tool: `NOTIFY_INVOICE_APPROVAL_TOOL`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `invoice_number` | `string` | ✅ | Invoice number or code |
| `seller_name` | `string` | ✅ | Vendor/seller company name |
| `buyer_name` | `string` | ✅ | Buyer company name |
| `total_amount` | `string` | ✅ | Total payable amount |
| `currency` | `string` | ❌ | Currency code (default: `MYR`) |
| `topic_id` | `string` | ✅ | HCS topic ID used for audit message |
| `tx_id` | `string` | ✅ | HBAR transfer transaction ID |

**What it does:** After the Hedera transactions complete, this tool sends an approval notification to Slack (if webhook configured) and writes an immutable local audit log entry to `invoice_approvals.log`.

---

## Invoice Processing Pipeline

```
PDF Upload
    ↓
extractor.py  →  canonical dict (doc_type, lines, totals, seller, buyer)
    ↓
transformer.py  →  full e-Invoice JSON (Malaysian LHDN standard, 80+ fields)
    ↓
Streamlit UI  →  display line items, totals, structured JSON
    ↓
Groq LLaMA 3.1 Agent  →  executes 4 Hedera steps:
    ├── CREATE_TOPIC_TOOL        (HCS topic: EnterpriseInvoiceAudit)
    ├── SUBMIT_TOPIC_MESSAGE_TOOL (invoice summary → immutable audit)
    ├── TRANSFER_HBAR_TOOL       (0.01 HBAR → 0.0.1001)
    └── NOTIFY_INVOICE_APPROVAL_TOOL (custom plugin → Slack + log)
    ↓
SubmissionLedger  →  Submission_Ledger.xlsx
```

---

## Sample Invoice Processed

| Field | Value |
|---|---|
| Invoice Number | 2637 |
| Date | 2026-02-05 |
| Seller | DLC Engineers Sdn Bhd |
| Buyer | Mtrustee Bhd (IGB REIT) |
| Subtotal | MYR 4,155.00 |
| Tax (8% SST) | MYR 332.40 |
| **Total** | **MYR 4,487.40** |
| Line Items | 3 (Associate × 2, Designer × 1) |

### Verified Transactions on HashScan Testnet

| Transaction ID | Type | Details |
|---|---|---|
| `0.0.9069340@1779914311.372866392` | `CREATE TOPIC` | Topic `0.0.9078008` created |
| `0.0.9069340@1779914345.589034318` | `SUBMIT MESSAGE` | Invoice audit message submitted |
| `0.0.9069340@1779914346.871888160` | `CRYPTO TRANSFER` | 0.01 HBAR → `0.0.1001` ✅ |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
ACCOUNT_ID=0.0.xxxxx
PRIVATE_KEY=302...          # DER encoded private key from Hedera Portal
GROQ_API_KEY=gsk_...
SLACK_WEBHOOK_URL=          # Optional
```

> Get a free testnet account at [portal.hedera.com](https://portal.hedera.com/dashboard)
> Get a free Groq API key at [console.groq.com](https://console.groq.com/keys)

### 3. Run

```bash
streamlit run streamlit_app.py
```

---

## Requirements

```
streamlit
pandas
openpyxl
pdfplumber
python-dotenv
requests
langchain
langchain-groq
langgraph
hedera-agent-kit
```

---

## Agent Execution Mode

| Mode | Value |
|---|---|
| Execution | `AgentMode.AUTONOMOUS` |
| LLM | Groq `llama-3.3-70b-versatile` |
| Plugins | `core_account_plugin`, `core_consensus_plugin`, `enterprise_invoice_plugin` |
| Network | Hedera Testnet |

---

## License

Apache 2.0
