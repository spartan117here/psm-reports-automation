# System Architecture & Technical Specification

## Pothys Swarna Mahal — Daily Reporting Automation System

This document outlines the software architecture, design principles, module responsibilities, security boundaries, and future infrastructure plans for the automated daily reporting system.

---

## 1. Architectural Principles

1. **Idempotency by Design**: Every stage of data processing and sheet manipulation can be run repeatedly without duplicating rows or corrupting aggregated metrics.
2. **Auditability & Traceability**: Raw responses from internal systems are archived in an immutable file hierarchy (`data/raw/YYYY/MM/DD/`) with execution metadata (`RunMetadata`) tracking row counts, processing times, and validation states.
3. **Fail-Closed Validation**: If critical validation checks fail (e.g. missing required headers, corrupt amounts, count mismatches), the system stops immediately and prevents publication or delivery of executive reports.
4. **Decoupled Integrations**: Third-party APIs (Google Sheets, Messaging, Databases) interact solely through abstract service interfaces, allowing painless swapping or testing without altering business logic.
5. **No Guessed Implementations**: Authentication endpoints, employee mappings, formulas, and showroom lists are strictly configuration-driven and explicitly validated against authorized specifications.

---

## 2. High-Level System Architecture

```text
+-----------------------------------------------------------------------------------+
|                                 CLI / SCHEDULER                                   |
|                        (python -m app run --report-date ...)                      |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                                RUN ORCHESTRATOR                                   |
|   1. Creates RunContext (run_id: RUN-YYYYMMDD-HHMMSS)                             |
|   2. Loads Pydantic AppConfigBundle from config/*.yaml                            |
|   3. Checks Idempotency via RunRepository                                         |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                              STAGE 1: INNERVEX CLIENT                             |
|   - Legitimate session establishment via AuthStrategy                             |
|   - POST /schemeNewCustRep with form-encoded filters                              |
|   - Raw JSON response archive -> FileSystemReportRepository                       |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                        STAGE 2: VALIDATION & CLEANING                             |
|   - Check non-empty response, required headers, sanity bounds                     |
|   - SchemeReportCleaner: extracts 11 columns in canonical order                   |
|   - Discards 10 obsolete columns (SNO, DATEOFBIRTH, etc.)                         |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                     STAGE 3: LOCATION & ATTRIBUTION RESOLVER                      |
|   - LocationResolver maps COMMCODE prefixes (TVL, CPT, KPM, etc.) to branches     |
|   - Resolves ECOMM tags (CHROMEPET ECOMM, etc.) and SV suffixes                   |
|   - Flags unresolved employee codes without silent defaults                       |
|   - RecordTransformer generates Column N ("code - name") and row layout A-N       |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                       STAGE 4: RECONCILIATION & GOOGLE SHEETS                     |
|   - DataReconciler verifies: count(raw) == count(cleaned), sum(raw) == sum(clean) |
|   - SheetWriter checks existing MSNOs (Column D) in target month tab              |
|   - Idempotently appends only new unique records                                  |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                      STAGE 5: AGGREGATION & CONSOLIDATION                         |
|   - LocationAggregator aggregates counts and amounts by LOCATION_2                |
|   - Backlog calculations (Target, Yet to Achieve, Achieved %, Daily AVG)          |
|   - ConsolidationEngine merges Subhiksham, Viruksham, and DigiGold metrics        |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                          STAGE 6: DELIVERY & REPORTING                            |
|   - If ValidationReport.has_critical_failures == True -> HALT & BLOCK             |
|   - ReportImageRenderer renders summary cards (Phase 10)                          |
|   - DeliveryChannel distributes report to AGM (WhatsApp / Email) (Phase 11)       |
+-----------------------------------------------------------------------------------+
```

---

## 3. Module Responsibilities

| Module | Responsibility |
| :--- | :--- |
| `app.core.config` | Loads YAML files (`app.yaml`, `innervex.yaml`, `reports.yaml`, `mappings.yaml`, `targets.yaml`), parses `.env`, and validates configuration using Pydantic. |
| `app.core.logging` | Configures structured logging with Run ID and Stage context, generating human-readable console output and machine-parseable JSON Lines log files with credential redaction. |
| `app.core.security` | Masks sensitive values, redacts authorization headers, and filters passwords/tokens from logs and diagnostic dumps. |
| `app.core.models` | Defines domain models (`RunContext`, `RunMetadata`, `RawReportPayload`, `CleanedRecord`, `ValidationResult`, `AggregationMetric`). |
| `app.innervex.client` | Framework-agnostic HTTP client with session pooling, retries with backoff, header management, and response status validation. |
| `app.innervex.auth` | Pluggable `AuthStrategy` interface for legitimate session establishment. Prevents hard-coded cookies or credential bypasses. |
| `app.innervex.reports` | Builds typed POST form payloads for report requests (Subhiksham, Viruksham, Closing). |
| `app.processing.cleaners` | Strips preamble rows and filters raw 21-column datasets down to the required 11 business columns using header names. |
| `app.processing.location` | Maps employee codes to physical branches, adds ECOMM labels, handles special locations (ONLINE, CORPORATE OFFICE), and flags unresolved codes. |
| `app.processing.transformers` | Enriches cleaned records with LOCATION, LOCATION_2, and helper column `COMMCODE - COMMNAME`, and formats them for Google Sheets columns A-N. |
| `app.processing.aggregators` | Calculates store-wise counts, total amounts, Q2/H1 targets, yet-to-achieve balances, achievement percentages, and daily averages. |
| `app.processing.rejoining` | Formulates closed member and rejoining metrics (`TOTAL REJOIN = SS + SV`, `REJOIN % = TOTAL / CLOSED`). |
| `app.processing.reconciliation`| Validates that raw source counts and total amounts match processed records. |
| `app.google_sheets` | Decoupled Google Sheets service interface (`SheetReader`, `SheetWriter`) supporting deduplicated, idempotent appending. |
| `app.validation` | Executes 18-point data health rules and produces structured `ValidationReport` instances. Enforces fail-closed delivery blocks on critical issues. |
| `app.reporting` | Generates consolidated daily data models and abstracts image card generation for executive viewing. |
| `app.delivery` | Pluggable distribution interface (`WhatsApp`, `Email`) kept strictly disabled until approved in Phase 11. |
| `app.repositories` | Abstract data persistence layer (`RunRepository`, `ReportRepository`) with a partitioned filesystem implementation, ready for PostgreSQL migration. |

---

## 4. Data Flow & Cleaning Rules

### 4.1 Subhiksham & Viruksham Cleaning
- **Raw Input**: 21 columns from Innervex (JSON `data` array or CSV export with 3 preamble rows).
- **Target Output**: Exactly 11 columns selected by header name in the following order:
  1. `COSTNAME`
  2. `CLIENTID`
  3. `GROUPCODE`
  4. `MSNO`
  5. `NAME`
  6. `SCHDATE`
  7. `RECAMOUNT`
  8. `MOBILENO`
  9. `SCHEME`
  10. `COMMNAME`
  11. `COMMCODE`
- **Discarded Columns** (tracked for audit): `SNO`, `DATEOFBIRTH`, `ADDRESS`, `PHONE`, `EMPNAME`, `EMPCODE`, `IS TELECALLER`, `IS HM`, `PROMOCODE`, `BRANCHNAME`.

### 4.2 Monthly Sheet Layout (Columns A through N)
- **A**: `COSTNAME`
- **B**: `CLIENTID`
- **C**: `GROUPCODE`
- **D**: `MSNO` (Unique Deduplication Key)
- **E**: `NAME`
- **F**: `SCHDATE`
- **G**: `LOCATION 2` (Generated by `LocationResolver`)
- **H**: `LOCATION` (Generated by `LocationResolver`)
- **I**: `RECAMOUNT`
- **J**: `MOBILENO`
- **K**: `SCHEME`
- **L**: `COMMNAME`
- **M**: `COMMCODE`
- **N**: `CODE_NAME` (Helper column: `COMMCODE - COMMNAME`)

---

## 5. Authentication Strategy

Innervex is an internal web application hosted at `http://192.168.5.213:4499` running on the company LAN behind a Jetty application server.
- The UI exposes `.aspx` pages, but the backend is treated as framework-agnostic.
- **Strict Guidelines**:
  - Never hard-code a temporary browser `JSESSIONID`.
  - Never guess or speculate on login endpoints.
  - The Python automation client will authenticate legitimately using configured user credentials and maintain its own session cookie jar.
  - In Phase 0, `PlaceholderAuthStrategy` is active. During Phase 1, the legitimate login endpoint and form parameters will be verified and plugged in.

---

## 6. Security & Credential Protection

- **No Secrets in Code**: All sensitive credentials (`INNERVEX_USERNAME`, `INNERVEX_PASSWORD`, OAuth tokens, Google credentials) are loaded from environment variables or secure local files specified in `.gitignore`.
- **Automatic Log Redaction**: The custom logging infrastructure uses `app.core.security` to inspect all log records and redact any fields matching sensitive keywords (`password`, `token`, `secret`, `cookie`, `session_id`, `jsessionid`, `authorization`).
- **No Bypasses**: The automation operates under the exact authority and permissions of the designated service user. No CAPTCHA bypasses, scraping evasions, or vulnerability exploits are implemented.

---

## 7. Configuration Strategy

Configuration is separated into 5 clear YAML files under `config/`:
1. `app.yaml`: Global runtime settings, storage paths, validation policies, and execution timeouts.
2. `innervex.yaml`: Network parameters, default HTTP headers, retry limits, and endpoint routes.
3. `reports.yaml`: Generic report configurations (Subhiksham, Viruksham, Closing) defining preamble rules, expected column counts, and column ordering.
4. `mappings.yaml`: Employee code prefix to branch mappings, showroom codes, and ECOMM naming rules.
5. `targets.yaml`: Branch sales targets (Q2/H1) and formula expressions.

---

## 8. Future Deployment Strategy (Windows Server & PostgreSQL)

The system is engineered to easily scale from a local developer laptop to a centralized Windows Server environment:

### Windows Server Deployment:
- **Execution Mechanism**: Scheduled task via **Windows Task Scheduler** configured to run daily (e.g., at 06:00 AM IST) targeting the previous day's data:
  ```powershell
  python.exe -m app run
  ```
- **Error Alerts**: Any failed run produces structured failure logs and can trigger alert webhooks or administrative emails.

### Database Migration (PostgreSQL):
- The `app.repositories.base` abstractions (`RunRepository` and `ReportRepository`) decouple data storage from the filesystem.
- When ready, a `PostgresRunRepository` and `PostgresReportRepository` using SQLAlchemy / psycopg2 can be enabled with zero modifications to cleaners, resolvers, or validation logic.
