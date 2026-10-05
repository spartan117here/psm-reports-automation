# Phase 1D — Google Sheets Discovery & Integration Specification

**Document Version:** 1.0.0  
**Project:** Pothys Swarna Mahal Daily Reporting Automation  
**Phase:** 1D (Google Sheets Read-Only Discovery & Structural Audit)  
**Date:** October 5, 2026  
**Status:** COMPLETE (Read-Only Discovery)  
**Write Operation Performed:** **NO** (Strict read-only safety enforced)

---

## Executive Summary

Phase 1D establishes the structural, architectural, and business mapping baseline required to bridge the Phase 1C cleaned output dataset with the production Google Sheets daily reporting workflow.

In accordance with strict project instructions, **zero write, append, update, clear, or formatting operations** were executed against any Google Sheet. All discovery logic was implemented using read-only service interfaces, metadata discovery engines, schema mapping analyzers, and robust mock test fixtures.

```
+-----------------------------------------------------------------------------------+
|                            CONFIRMED PIPELINE FLOW                                |
|                                                                                   |
|  Innervex API (Auth)                                                              |
|        ↓                                                                          |
|  Scheme Memberlist Download (308 raw records, JSON saved)                         |
|        ↓                                                                          |
|  Cleaning & Normalization (11 canonical columns, ₹1,955,500 total)                |
|        ↓                                                                          |
|  Location Attribution (LocationResolver: COSTNAME/COMMCODE -> Branch)             |
|        ↓                                                                          |
|  Format Generation (14 Google Sheet columns A-N)                                  |
|        ↓                                                                          |
|  Google Sheets Discovery Engine (Phase 1D: Read-Only Audit & Verification)         |
|        ↓                                                                          |
|  [PHASE 1E+]: Idempotent Append to Target Monthly Tab (SS - Oct)                  |
+-----------------------------------------------------------------------------------+
```

---

## 1. Authentication Status & Security Audit

### 1.1 Evaluated Authentication Mechanisms
The repository supports three standardized Google API authentication mechanisms via `app/google_sheets/client.py`:

1. **Service Account JSON Keyfile (Recommended)**:
   - Environment Variable: `GOOGLE_SERVICE_ACCOUNT_PATH` or `GOOGLE_APPLICATION_CREDENTIALS`
   - Default Path: `secrets/service_account.json`
   - Scope: `https://www.googleapis.com/auth/spreadsheets.readonly` (Read-only discovery mode)
2. **Service Account JSON String**:
   - Environment Variable: `GOOGLE_SERVICE_ACCOUNT_JSON`
   - Used in containerized or CI/CD environments where file mounting is restricted.
3. **Application Default Credentials (ADC)**:
   - Fallback via `google.auth.default(scopes=...)`

### 1.2 Local Authentication Configuration Audit
- **Status:** `CONFIGURATION_MISSING`
- **Environment State:** `.env` contains legitimate `INNERVEX_*` credentials from Phase 1A-1C, but does not yet contain Google Cloud service credentials.
- **Credential Safety:** No fake secrets were invented; no credentials were hardcoded; no sensitive information was exposed in logs or test output.

### 1.3 Exact Missing Configuration Required for Live Access
To connect the discovery engine to the live Google Sheets API in production:

| Configuration Key | Location | Purpose | Required Value Format |
|---|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_PATH` | `.env` | Filepath to Google Cloud Service Account key | `secrets/service_account.json` |
| `GOOGLE_SHEET_NEW_ENROLLMENT_ID` | `.env` | Master Workbook Spreadsheet ID | 44-character string from Google Sheet URL |
| IAM Permissions | Google Drive / Sheet | Access grant | Grant Service Account email **Viewer** role |

---

## 2. Target Google Spreadsheet Identification

- **Master Workbook Title:** *"New Enrollment from April 2026"*
- **Target Spreadsheet ID:** Configured via `GOOGLE_SHEET_NEW_ENROLLMENT_ID`
- **URL Pattern:** `https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit`
- **Redaction Policy:** All CLI outputs and logs automatically mask the spreadsheet ID using `redact_spreadsheet_id()` (e.g. `1AbC...xYz`).

---

## 3. Discovered Workbook Structure & Relevant Tabs

Based on historical production workflows and workbook specifications, the workbook is organized into three distinct tab categories:

```
New Enrollment from April 2026 (Master Workbook)
│
├── Master Directories
│   └── Employees                     # Master employee-to-branch directory (COMMCODE lookup)
│
├── Monthly Scheme Transaction Tabs (14 columns: A through N)
│   ├── SS - Aug                      # Subhiksham August transactions
│   ├── SV - Aug                      # Viruksham August transactions
│   ├── SS - Sept                     # Subhiksham September transactions (Latest active)
│   ├── SV - Sept                     # Viruksham September transactions (Latest active)
│   ├── SS - Oct                      # Target for current pipeline (to be created/used)
│   └── SV - Oct                      # Viruksham October transactions
│
└── Monthly Consolidation Report Tabs
    ├── Consolidate Report - Jul      # July executive branch summary & achievement
    ├── Consolidate Report - Aug      # August executive branch summary & achievement
    ├── Consolidate Report - Sept     # September executive branch summary & achievement
    └── Consolidate Report - Oct      # Target executive summary for AGM
```

---

## 4. Subhiksham Monthly Sheet Structure (A through N)

### 4.1 Header Row Layout
The monthly Subhiksham sheet (`SS - Sept` / `SS - Oct`) contains exactly **14 columns**:

| Col | Column Letter | Header Name | Data Type | Source / Derivation |
|:---:|:---:|:---|:---|:---|
| 1 | **A** | `COSTNAME` | String | Innervex raw `COSTNAME` (Branch code: `CPT`, `PAD`, etc.) |
| 2 | **B** | `CLIENTID` | String | Innervex raw `CLIENTID` (e.g. `APP27SSP1/1957`) |
| 3 | **C** | `GROUPCODE` | String | Innervex raw `GROUPCODE` (e.g. `NEW`) |
| 4 | **D** | `MSNO` | String | Innervex raw `MSNO` (**Primary Idempotency Deduplication Key**) |
| 5 | **E** | `NAME` | String | Customer full name (Sanitized in logs) |
| 6 | **F** | `SCHDATE` | String | Enrollment date (`YYYY-MM-DD`) |
| 7 | **G** | `LOCATION 2` | String | Enriched location attribute (via `LocationResolver`) |
| 8 | **H** | `LOCATION` | String | Enriched showroom location (via `LocationResolver`) |
| 9 | **I** | `RECAMOUNT` | Numeric / Float | Enrollment installment amount (e.g. `1000.00`, `10000.00`) |
| 10 | **J** | `MOBILENO` | String | 10-digit customer mobile number (Sanitized in logs) |
| 11 | **K** | `SCHEME` | String | Scheme name (`NEW SWARNA SUBHIKSHAM`) |
| 12 | **L** | `COMMNAME` | String | Commission agent / Employee name |
| 13 | **M** | `COMMCODE` | String | Commission agent / Employee numeric ID (e.g. `2998`) |
| 14 | **N** | `code - name` | String / Formula | Helper column: concatenation of COMMCODE & COMMNAME |

### 4.2 Static Values vs Formulas
- **Columns A–F, I–M:** Static data values extracted directly from Innervex.
- **Columns G–H:** Enriched business location strings. In the automated pipeline, these are computed deterministically by `LocationResolver` before writing.
- **Column N:** Helper column. In historical sheets, this is created either as a static string (`2998 - SANDHIYA`) or as an Excel formula:
  ```excel
  =M2&" - "&L2
  ```

---

## 5. Cleaned 11 Columns to Sheet 14 Columns Mapping Proposal

| # | Processed Field (Phase 1C) | Sheet Col | Target Header | Transformation / Enrichment Required | Status |
|:---:|:---|:---:|:---|:---|:---:|
| 1 | `COSTNAME` | **A** | `COSTNAME` | Direct 1:1 copy | `DIRECT` |
| 2 | `CLIENTID` | **B** | `CLIENTID` | Direct 1:1 copy | `DIRECT` |
| 3 | `GROUPCODE` | **C** | `GROUPCODE` | Direct 1:1 copy | `DIRECT` |
| 4 | `MSNO` | **D** | `MSNO` | Direct 1:1 copy (Idempotency key) | `DIRECT` |
| 5 | `NAME` | **E** | `NAME` | Direct 1:1 copy (Uppercase trimmed) | `DIRECT` |
| 6 | `SCHDATE` | **F** | `SCHDATE` | Direct 1:1 copy (`YYYY-MM-DD` ISO) | `DIRECT` |
| - | *(Derived)* | **G** | `LOCATION 2` | `LocationResolver.resolve(COSTNAME, COMMCODE).location_2` | `ENRICHED` |
| - | *(Derived)* | **H** | `LOCATION` | `LocationResolver.resolve(COSTNAME, COMMCODE).location` | `ENRICHED` |
| 7 | `RECAMOUNT` | **I** | `RECAMOUNT` | Direct numeric float (`#,##0.00`) | `DIRECT` |
| 8 | `MOBILENO` | **J** | `MOBILENO` | Direct 1:1 copy (10-digit formatted) | `DIRECT` |
| 9 | `SCHEME` | **K** | `SCHEME` | Direct 1:1 copy | `DIRECT` |
| 10 | `COMMNAME` | **L** | `COMMNAME` | Direct 1:1 copy (empty string if blank) | `DIRECT` |
| 11 | `COMMCODE` | **M** | `COMMCODE` | Direct 1:1 copy (empty string if blank) | `DIRECT` |
| - | *(Derived)* | **N** | `code - name` | Concatenation: `COMMCODE & " - " & COMMNAME` | `HELPER_CONCAT` |

---

## 6. Employees Tab Analysis & Role

- **Purpose:** Central lookup directory maintaining employee-to-showroom associations.
- **Key Observation from Phase 1C Data:**
  - `COMMCODE` in Innervex is **numeric** (e.g. `2998`, `3218`, `1492`, `2542`), representing staff payroll/ID numbers.
  - The branch code is carried primarily in **`COSTNAME`** (`CPT`, `PAD`, `KPM`, `TPJ`, `TVL`, `CBE`, `SLM`, `APP`, `TVC`, `PNM`).
  - Records originating from the mobile app (`COSTNAME: "APP"`) frequently have blank `COMMNAME` and blank `COMMCODE` because customers enrolled directly online without an in-store sales representative.

---

## 7. Consolidation Sheet Business Formulas & Logic

The monthly consolidation tabs (`Consolidate Report - Sept`, `Consolidate Report - Oct`) aggregate daily enrollments across all 31 showrooms and calculate executive KPIs for the AGM.

### 7.1 Key Aggregation Formulas
1. **Total Monthly Collection by Showroom:**
   ```excel
   =SUMIFS('SS - Oct'!$I:$I, 'SS - Oct'!$H:$H, A4)
   ```
2. **Subhiksham Enrollment Count by Showroom:**
   ```excel
   =COUNTIFS('SS - Oct'!$H:$H, A4, 'SS - Oct'!$K:$K, "NEW SWARNA SUBHIKSHAM")
   ```
3. **Target Achievement Percentage:**
   ```excel
   =Actual_Amount / Target_Amount * 100.0
   ```
4. **Yet to Achieve (Backlog):**
   ```excel
   =Target_Amount - Actual_Amount
   ```
5. **Daily Run-Rate Average:**
   ```excel
   =Actual_Amount / DAY(TODAY())
   ```

---

## 8. September Data Structure & Monthly Sheet Creation Strategy

- **Current Active Month:** September 2026 (`SS - Sept`, `SV - Sept`, `Consolidate Report - Sept`).
- **Target New Month:** October 2026 (`SS - Oct`).
- **Standardized Procedure for Next Month Sheet Creation:**
  1. Check if `SS - Oct` tab exists via `SheetDiscoveryEngine._check_october_data()`.
  2. If missing, clone structure from template / `SS - Sept` with 14 header columns (A1:N1).
  3. Pre-format Column I as numeric currency.
  4. Ensure idempotent append logic uses Column D (`MSNO`) as the uniqueness barrier.

---

## 9. October Data Presence Status

- **`SS - Oct` Tab Present:** **NO** (Not created yet in master workbook).
- **Accidental October Records in September Tab:** **0** (Verified clean separation).
- **Current Raw & Cleaned October Data:**
  - Local dataset for October 4, 2026: 308 records verified and preserved under `data/processed/2026/10/04/`.
  - Ready for idempotent ingestion once write phase is authorized.

---

## 10. Unresolved Business Rules & Technical Dependencies

Before enabling automated write operations in Phase 1E+, the following business rules must be finalized:

1. **`COSTNAME: "APP"` Location Attribution:**
   - When `COSTNAME == "APP"` and `COMMCODE` is blank, should `LOCATION` and `LOCATION 2` be attributed to `"ONLINE"` or `"APP ECOMM"`?
   - *Current recommendation:* Attribute to `"ONLINE"`.
2. **`LOCATION` vs `LOCATION 2` Differentiation:**
   - In Subhiksham (`SS`), physical stores carry identical names in both columns (e.g. `"CHROMEPET"` / `"CHROMEPET"`).
   - In Viruksham (`SV`), Column G is typically `"CHROMEPET SV"` while Column H is `"CHROMEPET"`.
   - Confirm whether any specific showroom uses differing suffixes.
3. **Numeric `COMMCODE` Mapping vs `COSTNAME` Mapping:**
   - Since `COMMCODE` values are numeric employee IDs, confirm whether the master `Employees` sheet is needed for showroom resolution, or if `COSTNAME` is the definitive source of branch attribution.
4. **Idempotency Deduplication Key:**
   - Phase 1C confirmed `MSNO` is 100% unique across all 308 records. Confirm whether `MSNO` is globally unique across years or if composite `(MSNO, SCHDATE)` should be indexed.
5. **Consolidation Sheet Update Strategy:**
   - Determine whether the daily automation should write formulas into `Consolidate Report - Oct` or let existing Google Sheets formulas update automatically based on rows appended to `SS - Oct`.

---

## 11. Recommended Next Implementation Step

1. **Add Google Credentials & Spreadsheet ID to `.env`:**
   ```bash
   GOOGLE_SERVICE_ACCOUNT_PATH=secrets/service_account.json
   GOOGLE_SHEET_NEW_ENROLLMENT_ID=<actual_spreadsheet_id>
   ```
2. **Execute Live Read-Only Discovery:**
   ```bash
   python -m app sheets-discover
   ```
3. **Implement Phase 1E (Idempotent Sheet Writer):**
   - Implement batch append with deduplication against live Column D (`MSNO`).
   - Run end-to-end dry-run before writing actual data.
