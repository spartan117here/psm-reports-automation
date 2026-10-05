# Pothys Swarna Mahal — Daily Reporting Automation System

An enterprise-grade, idempotent, and auditable automation system designed to eliminate manual data extraction, spreadsheet editing, consolidation, and report distribution for Pothys Swarna Mahal.

---

## 1. Overview & Business Objective

Currently, daily reporting involves manually logging into the company's internal system (**Innervex**), downloading multiple reports, manually cleaning and sorting columns in Excel, updating monthly Google Sheets, calculating business metrics (targets, daily averages, yet-to-achieve amounts), consolidating figures across schemes, generating report images, and delivering them to executive management (AGM).

This project automates the entire daily reporting lifecycle while ensuring:
- **Auditability**: Complete preservation of raw Innervex payloads and run metadata.
- **Idempotency**: Safe re-execution without duplicate rows or distorted totals.
- **Data Integrity**: An 18-point validation suite that blocks distribution on critical errors.
- **Modularity**: Framework-agnostic, adapter-based design ready to migrate from local development to a Windows Server environment.

---

## 2. System Architecture & High-Level Flow

```text
    ┌────────────────────────┐
    │   Innervex (LAN App)   │
    │  http://192.168.5.213  │
    └───────────┬────────────┘
                │ Authenticated Session (Phase 1)
                ▼
    ┌────────────────────────┐
    │    Raw Data Archive    │ ──> data/raw/YYYY/MM/DD/<report_id>_<time>.json
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │  18-Point Validation   │ ──> Verifies headers, non-empty, sanity bounds
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │  Cleaning & Filtering  │ ──> Reduces 21 raw columns to 11 standard columns
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │  Location Attribution  │ ──> Maps employee codes to branches & ECOMM tags
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │  Google Sheets Update  │ ──> Appends unique rows to monthly tabs (A-N)
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │     Consolidation      │ ──> Store-wise targets, Q2/H1 backlog, DigiGold
    └───────────┬────────────┘
                │
                ▼
    ┌────────────────────────┐
    │   Executive Delivery   │ ──> Generates cards & delivers (WhatsApp/Email)
    └────────────────────────┘
```

---

## 3. Directory Layout

```text
pothys-reporting-automation/
├── .env.example              # Environment variables template (no secrets)
├── .gitignore                # Git ignore configuration
├── pyproject.toml            # Python packaging and dependencies metadata
├── requirements.txt          # Production and development dependencies
├── README.md                 # System overview and quickstart guide
├── ARCHITECTURE.md           # Comprehensive architectural specification
├── TODO.md                   # Registry of unconfirmed business rules & TODOs
├── config/                   # Configuration files (YAML)
│   ├── app.yaml              # Global application and storage settings
│   ├── innervex.yaml         # Innervex base URL, timeouts, and auth specs
│   ├── reports.yaml          # Generic report definitions (Subhiksham, Viruksham, Closing)
│   ├── mappings.yaml         # Employee code to branch & showroom mappings
│   └── targets.yaml          # Branch targets (Q2/H1) and calculation formulas
├── data/
│   ├── raw/                  # Partitioned immutable raw JSON payloads
│   └── processed/            # Partitioned cleaned datasets
├── logs/                     # JSON Lines and console audit log files
├── app/
│   ├── cli.py                # Command-line interface entry points
│   ├── core/                 # Config loader, logging, exceptions, domain models, security
│   ├── innervex/             # Framework-agnostic HTTP client, auth, and report fetchers
│   ├── processing/           # 11-column cleaners, location resolver, aggregators, rejoining
│   ├── google_sheets/        # Decoupled Google Sheets service interface & idempotent writer
│   ├── validation/           # 18-point validation engine and rule catalog
│   ├── reporting/            # Consolidation builder and report image renderer
│   ├── delivery/             # Delivery channel abstraction (WhatsApp/Email)
│   └── repositories/         # Storage repository interfaces (Filesystem / PostgreSQL ready)
└── tests/
    ├── conftest.py           # Shared pytest fixtures
    ├── fixtures/             # Mock Innervex raw responses
    ├── unit/                 # Unit tests (config, cleaners, location, validation, CLI, models)
    └── integration/          # Full offline pipeline dry-run and idempotency tests
```

---

## 4. Getting Started Locally

### Prerequisites
- Python 3.10+ (tested on Python 3.14.7)
- Access to company LAN (for Phase 1+ Innervex extraction)

### Installation
1. Navigate to the project directory:
   ```powershell
   cd C:\Users\Pothys\.gemini\antigravity-ide\scratch\pothys-reporting-automation
   ```
2. Create and activate a virtual environment (optional if using system Python):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
4. Setup environment variables:
   ```powershell
   copy .env.example .env
   # Edit .env with authorized credentials when instructed
   ```

---

## 5. Running the Pipeline & CLI Commands

The CLI provides commands to inspect, audit, and execute the reporting pipeline:

- **Audit System Health & Mappings**:
  ```powershell
  python -m app validate
  ```
- **Execute Pipeline Dry Run (No External Network Calls)**:
  ```powershell
  python -m app run --dry-run
  ```
- **Run for Specific Report Date**:
  ```powershell
  python -m app run --report-date 2026-10-04
  ```
- **Force Re-run (Bypass Idempotency)**:
  ```powershell
  python -m app run --report-date 2026-10-04 --force
  ```
- **Run All Automated Tests**:
  ```powershell
  python -m pytest
  ```

---

## 6. Development Phases

- [x] **Phase 0**: Architecture, Project Scaffold, Test Suite, Models, Configuration & Documentation *(CURRENT)*
- [ ] **Phase 1**: Innervex Legitimate Authentication & Single Report Download
- [ ] **Phase 2**: Subhiksham & Viruksham Automated Report Download
- [ ] **Phase 3**: Raw Report Validation & 11-Column Cleaning Engine
- [ ] **Phase 4**: Employee/Branch Mapping & ECOMM Attribution Resolution
- [ ] **Phase 5**: Google Sheets Integration & Idempotent Row Appending
- [ ] **Phase 6**: Consolidate Report Tab & Backlog Calculations Automation
- [ ] **Phase 7**: Closing & Rejoining Reports Automation
- [ ] **Phase 8**: DigiGold / DigiSilver Pipeline Integration
- [ ] **Phase 9**: Full Reconciliation & Automated Pipeline Integrity Suite
- [ ] **Phase 10**: Report Image Card Rendering
- [ ] **Phase 11**: Approved Delivery Channel Integration
- [ ] **Phase 12**: Windows Server Deployment & Scheduled Production Runs
