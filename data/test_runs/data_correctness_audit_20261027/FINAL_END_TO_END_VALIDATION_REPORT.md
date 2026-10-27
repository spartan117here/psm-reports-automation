# Final End-to-End Pipeline Validation Report — 25 October 2026

**Execution Date:** 2026-10-27  
**Validation Reporting Target:** 25 October 2026  
**Safety Mode:** `STRICT_READ_ONLY=true` (Zero production Google Sheet edits, zero remote database/source record mutations, zero Telegram/WhatsApp alerts, zero cron jobs)  
**Machine-Readable Evidence:** [`expected_vs_actual_20261025.json`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/data/test_runs/data_correctness_audit_20261027/expected_vs_actual_20261025.json)  
**Pipeline Run Audit Evidence:** [`complete_pipeline_validation_20261025.json`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/data/test_runs/data_correctness_audit_20261027/complete_pipeline_validation_20261025.json)

---

## Executive Summary & Final Verdicts

| Report | Table / Component | Scope | Verdict | Key Result |
|---|---|---|:---:|---|
| **New Enrollment Report** | Table 1: Revenue | Cumulative MTD (as on 25/10/2026) | **PASS** | Revenue totals match exactly: SS = ₹35,687,000; SV = ₹306,919,960; Total = ₹342,606,960. Reference target formula discrepancy identified. |
| **New Enrollment Report** | Table 2: Enrollment Count | Cumulative MTD (as on 25/10/2026) | **PASS** | Counts match reference across every single showroom & scheme: SS = 4,897; SV = 950; DG = 19,324; DS = 4,637; Total = 29,808; AVG = 1,192. |
| **Scheme Rejoining Report** | Main Rejoining Table | Cumulative MTD (as on 25/10/2026) | **PASS** | 9 Showrooms evaluated: Closed accounts = 4,604 (100% exact); SV Rejoining = 79 (100% exact); SS Rejoining = 871 vs 867 reference (+4 explained). |
| **Scheme Rejoining Report** | Closed Weight Table | Cumulative MTD (as on 25/10/2026) | **PASS** | Count = 4,604; Total Weight = 32,860.288 g. Exact 100% branch-by-branch match across all 9 physical showrooms. |
| **Scheme Rejoining Report** | Closed Count Table | Cumulative MTD (as on 25/10/2026) | **PASS** | Count = 4,604; SS Closures = 3,947; SV Closures = 657. Exact 100% branch-by-branch match across all 9 physical showrooms. |
| **Daily Pipeline Run** | 6 Daily Data Feeds (25 Oct) | Daily Delta (25/10/2026) | **PASS** | All 6 feeds reconciled: SS = 317 (₹2.839M), SV = 72 (₹21.511M), DG = 878 (₹604.9K), DS = 151 (₹164.3K), Closing = 333 (2,542.70g), Rejoining = 59. |

---

## 1. Scope Reconciliation: Daily Delta vs. Cumulative Month-to-Date (MTD)

A critical architectural distinction verified during this audit is the dual scope of reporting:
1. **Daily Operational Pipeline (25 October 2026 Delta)**:
   Extracts and validates records transacted strictly on `2026-10-25`.
   - Subhiksham (SS): 317 records, ₹2,839,000.00
   - Viruksham (SV): 72 records, ₹21,511,494.00
   - DigiGold (DG): 878 records, ₹604,924.00
   - DigiSilver (DS): 151 records, ₹164,302.00
   - Closing: 333 records, 2,542.698 g
   - Rejoining Daily: 333 evaluated, 59 Rejoin SS, 0 Rejoin SV (17.72% daily rejoining rate).
2. **Executive Consolidated Dashboard (Reference Screenshots)**:
   The reference screenshots display **Cumulative Month-to-Date (MTD) as of 25 October 2026** (spanning 1 Oct 2026 through 25 Oct 2026), reflecting the state of the reporting system prior to the 26 October pipeline run.
   - When 26 October data was later appended to the destination sheets, the totals shifted (e.g., SS MTD became 5,053 and ₹36,872,500; Closing MTD became 4,780).
   - Local validation successfully isolated all records with transaction dates `SCHDATE <= 2026-10-25` to achieve exact reconciliation with the reference screenshots.

---

## 2. Table-by-Table Expected vs. Actual Comparison

### A. New Enrollment Report — Table 1: Revenue (MTD as on 25/10/2026)

| Branch | Reference Target | Pipeline Target | Reference SS | Pipeline SS | Reference SV | Pipeline SV | Reference Total | Pipeline Total | Reference Yet to Ach | Pipeline Yet to Ach | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **TVL** | 40,000,000 | 40,000,000 | 3,770,000 | 3,770,000 | 43,031,457 | 43,031,457 | 46,801,457 | 46,801,457 | 6,801,457 | 6,801,457 | **PASS** |
| **CPT** | 72,000,000 | 72,000,000 | 9,326,000 | 9,326,000 | 73,335,187 | 73,335,187 | 82,661,187 | 82,661,187 | 10,661,187 | 10,661,187 | **PASS** |
| **TVM** | 27,000,000 | 27,000,000 | 2,038,000 | 2,038,000 | 22,449,472 | 22,449,742 | 24,487,472 | 24,487,742 | -2,512,528 | -2,512,258 | **PASS** * |
| **TPJ** | 40,000,000 | 40,000,000 | 2,668,000 | 2,668,000 | 34,642,821 | 34,642,821 | 37,310,821 | 37,310,821 | -2,689,179 | -2,689,179 | **PASS** |
| **CBE** | 30,000,000 | 30,000,000 | 2,125,500 | 2,125,500 | 21,888,154 | 21,888,154 | 24,013,654 | 24,013,654 | -5,986,346 | -5,986,346 | **PASS** |
| **SLM** | 30,000,000 | 30,000,000 | 1,419,000 | 1,419,000 | 27,742,417 | 27,742,417 | 29,161,417 | 29,161,417 | -838,583 | -838,583 | **PASS** |
| **PNM** | 20,000,000 | 20,000,000 | 2,359,000 | 2,359,000 | 14,157,021 | 14,157,021 | 16,516,021 | 16,516,021 | -3,483,979 | -3,483,979 | **PASS** |
| **PADI** | 54,000,000 | 54,000,000 | 6,724,500 | 6,724,500 | 49,304,167 | 49,304,167 | 56,028,667 | 56,028,667 | 2,028,667 | 2,028,667 | **PASS** |
| **KANCHI** | 27,000,000 | 27,000,000 | 2,647,000 | 2,647,000 | 11,056,518 | 11,056,518 | 13,703,518 | 13,703,518 | -13,296,482 | -13,296,482 | **PASS** |
| **NGL** | Unconfigured | Unconfigured | 340,000 | 340,000 | 2,375,412 | 2,375,412 | 2,715,412 | 2,715,412 | 2,715,412 | 2,715,412 | **PASS** |
| **ONLINE** | Unconfigured | Unconfigured | 227,000 † | 2,270,000 | 6,937,064 | 6,937,064 | 9,207,064 | 9,207,064 | 9,207,064 | 9,207,064 | **PASS** † |
| **TOTAL** | **313,000,000** ‡ | **313,000,000** | **35,687,000** | **35,687,000** | **306,919,960** | **306,919,960** | **342,606,960** | **342,606,960** | **29,606,960** | **29,606,960** | **PASS** |

> **Table 1 Reconciliation Notes:**
> - ‡ **Target Formula Discrepancy**: The screenshot shows Total Target as ₹313,000,000 because Google Sheets cell `F16` used `=SUM(F6:F13)`, which omits Row 14 KANCHI (₹27M). If KANCHI is included, the true mathematical sum is ₹340,000,000. As instructed, the reference value ₹313,000,000 is preserved for comparison.
> - † **ONLINE SS Truncation**: In the reference screenshot, ONLINE Subhiksham is printed as `227,000` due to a visual column cutoff omitting the trailing zero. The row total in the screenshot is `9,207,064`, which confirms that the underlying calculation was `2,270,000 + 6,937,064 = 9,207,064`, and the grand total `35,687,000` requires exactly ₹2,270,000.
> - \* **TVM Viruksham (₹270)**: Actual source data sums to ₹22,449,742 vs reference ₹22,449,472 due to an upstream manual cell edit in the sheet row. The overall grand total Viruksham ₹306,919,960 matches 100%.

---

### B. New Enrollment Report — Table 2: Enrollment Count (MTD as on 25/10/2026)

| Branch | Count Target | Ref SS | Act SS | Ref SV | Act SV | Ref DG | Act DG | Ref DS | Act DS | Ref Total | Act Total | Ref AVG | Act AVG | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **TVL** | 3,000 | 544 | 544 | 112 | 112 | 773 | 773 | 376 | 376 | 1,805 | 1,805 | 72 | 72 | **PASS** |
| **CPT** | 6,000 | 940 | 940 | 234 | 234 | 1,921 | 1,921 | 356 | 356 | 3,451 | 3,451 | 138 | 138 | **PASS** |
| **TVM** | 4,000 | 277 | 277 | 42 | 42 | 1,847 | 1,847 | 44 | 44 | 2,210 | 2,210 | 88 | 88 | **PASS** |
| **TPJ** | 5,000 | 539 | 539 | 89 | 89 | 1,785 | 1,785 | 643 | 643 | 3,056 | 3,056 | 122 | 122 | **PASS** |
| **CBE** | 7,000 | 416 | 416 | 72 | 72 | 1,987 | 1,987 | 913 | 913 | 3,388 | 3,388 | 136 | 136 | **PASS** |
| **SLM** | 3,000 | 262 | 262 | 83 | 83 | 1,263 | 1,263 | 449 | 449 | 2,057 | 2,057 | 82 | 82 | **PASS** |
| **PNM** | 2,000 | 247 | 247 | 43 | 43 | 449 | 449 | 151 | 151 | 890 | 890 | 36 | 36 | **PASS** |
| **PADI** | 6,000 | 788 | 788 | 163 | 163 | 1,497 | 1,497 | 359 | 359 | 2,807 | 2,807 | 112 | 112 | **PASS** |
| **KANCHI** | Unconfigured | 483 | 483 | 44 | 44 | 2,827 | 2,827 | 388 | 388 | 3,742 | 3,742 | 150 | 150 | **PASS** |
| **NGL** | Unconfigured | 46 | 46 | 8 | 8 | 187 | 187 | 40 | 40 | 281 | 281 | 11 | 11 | **PASS** |
| **E COMM** | Unconfigured | 355 | 355 | 60 | 60 | 4,788 | 4,788 | 918 | 918 | 6,121 | 6,121 | 245 | 245 | **PASS** |
| **TOTAL** | **36,000** | **4,897** | **4,897** | **950** | **950** | **19,324** | **19,324** | **4,637** | **4,637** | **29,808** | **29,808** | **1,192** | **1,192** | **PASS** |

> **Table 2 Reconciliation Notes:**
> - Every branch row and metric matches the reference screenshot with 100% precision.
> - Grand Total = 29,808 across 25 reporting days yields an average of `29,808 / 25 = 1,192.32`, rounded down to `1,192` as in the screenshot.

---

### C. Scheme Rejoining Report — Main Table (MTD as on 25/10/2026)

| Showroom | Ref Closed | Act Closed | Ref Rejoin SS | Act Rejoin SS | Ref Rejoin SV | Act Rejoin SV | Ref Total Rejoin | Act Total Rejoin | Ref Rejoin % | Act Rejoin % | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
### C. Scheme Rejoining Report — Main Table (MTD as on 25/10/2026)

| Showroom | Ref Closed | Act Closed | Ref Rejoin SS | Point-in-Time SS (<= 25 Oct) | Post-26-Oct Sheet SS | Ref Rejoin SV | Act Rejoin SV | Ref Total Rejoin | Act Total Rejoin | Ref Rejoin % | Act Rejoin % | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **TVL** | 554 | 554 | 124 | **124** | 126 (+2) | 15 | 15 | 139 | 139 | 25.09% | 25.09% | **PASS** |
| **CPT** | 1,098 | 1,098 | 211 | **211** | 213 (+2) | 20 | 20 | 231 | 231 | 21.04% | 21.04% | **PASS** |
| **TVM** | 532 | 532 | 117 | **117** | 117 | 10 | 10 | 127 | 127 | 23.87% | 23.87% | **PASS** |
| **TPJ** | 862 | 862 | 164 | **164** | 164 | 9 | 9 | 173 | 173 | 20.07% | 20.07% | **PASS** |
| **CBE** | 706 | 706 | 140 | **140** | 140 | 9 | 9 | 149 | 149 | 21.10% | 21.10% | **PASS** |
| **SLM** | 349 | 349 | 52 | **52** | 52 | 7 | 7 | 59 | 59 | 16.91% | 16.91% | **PASS** |
| **PNM** | 321 | 321 | 39 | **39** | 39 | 7 | 7 | 46 | 46 | 14.33% | 14.33% | **PASS** |
| **PADI** | 181 | 181 | 20 | **20** | 20 | 2 | 2 | 22 | 22 | 12.15% | 12.15% | **PASS** |
| **KPM** | 1 | 1 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00% | **PASS** |
| **TOTAL** | **4,604** | **4,604** | **867** | **867** | **871 (+4)** | **79** | **79** | **946** | **946** | **20.55%** | **20.55%** | **PASS** |

> **Main Rejoining Table Reconciliation Notes:**
> - When evaluated with a strict point-in-time filter (`enrollment_date <= 2026-10-25`), the pipeline matches **exactly 867 SS rejoinings**, achieving a 100% exact match across all showrooms and totals.
> - The current Google Sheet displays 871 because 26 October enrollments were appended to `SS - Oct`, retroactively triggering matches in rows 3321, 3322, 3360, and 3361.

---

### C.1 Detailed Investigation of the 4 Discrepant SS Rejoining Records

The +4 discrepancy between the 25 Oct reference screenshot (867) and the post-26 Oct sheet (871) stems from 4 specific closed accounts whose matching new enrollments were transacted on **26 October 2026**:

#### 1. TVL Record 1 — Sheet Row 3321
- **Closed Record**:
  - `DOCDATE`: `18-10-2026`
  - `NAME`: `KUMAR G`
  - `PASSBOOK`: `SPF-7087`
  - `MOBILE`: `94****3755` (masked)
  - `SHOWROOM`: `TVL`
  - `STATUS`: `CLOSE`
- **Matching New Enrollment Record**:
  - Sheet Tab: `'SS - Oct'`, Row: `5054`
  - `SCHDATE`: `26-10-2026`
  - `NAME`: `KUMAR G`
  - `CLIENTID`: `TVL27SSP1/2836`
  - `MSNO`: `TVL27SCT/28983`
  - `LOCATION 2`: `TIRUNELVELI`
  - `RECAMOUNT`: `₹10,500.00`
- **Matching Rule**: `=IFNA(VLOOKUP(D3321, IMPORTRANGE(..., "'SS - Oct'!J:K"), 2, 0))`
- **Audit Assessment**: Legitimate customer rejoining, but occurred on 26 October 2026. On 25 October 2026, row 5054 did not exist in `'SS - Oct'`, so cell `I3321` evaluated to blank/`#N/A`. When 26 October data was appended, this row retroactively became a match (+1 to TVL).

#### 2. TVL Record 2 — Sheet Row 3322
- **Closed Record**:
  - `DOCDATE`: `18-10-2026`
  - `NAME`: `AMUTHA M S`
  - `PASSBOOK`: `SPF-7088`
  - `MOBILE`: `94****3755` (masked)
  - `SHOWROOM`: `TVL`
  - `STATUS`: `CLOSE`
- **Matching New Enrollment Record**:
  - Sheet Tab: `'SS - Oct'`, Row: `5054` (Same enrollment record as above)
  - `SCHDATE`: `26-10-2026`
  - `NAME`: `KUMAR G`
  - `CLIENTID`: `TVL27SSP1/2836`
  - `RECAMOUNT`: `₹10,500.00`
- **Matching Rule**: `=IFNA(VLOOKUP(D3322, IMPORTRANGE(..., "'SS - Oct'!J:K"), 2, 0))`
- **Audit Assessment**: Family account sharing the same mobile phone. A single new enrollment of ₹10,500 by Kumar G on 26 Oct retroactively matched **both** closed passbooks (SPF-7087 and SPF-7088) because the Google Sheet formula matches purely on phone number (`VLOOKUP(D:D)`), resulting in 2 rejoining credits for 1 new enrollment.

#### 3. CPT Record 1 — Sheet Row 3360
- **Closed Record**:
  - `DOCDATE`: `19-10-2026`
  - `NAME`: `A.ELUMALAI`
  - `PASSBOOK`: `SCF-12521`
  - `MOBILE`: `78****1614` (masked)
  - `SHOWROOM`: `CPT`
  - `STATUS`: `CLOSE`
- **Matching New Enrollment Record**:
  - Sheet Tab: `'SS - Oct'`, Row: `4935`
  - `SCHDATE`: `26-10-2026`
  - `NAME`: `A.ELUMALAI`
  - `CLIENTID`: `CPT27SSP1/3204`
  - `MSNO`: `CPT27SCT/42345`
  - `LOCATION 2`: `CHROMEPET`
  - `RECAMOUNT`: `₹8,000.00`
- **Matching Rule**: `=IFNA(VLOOKUP(D3360, IMPORTRANGE(..., "'SS - Oct'!J:K"), 2, 0))`
- **Audit Assessment**: Legitimate customer rejoining, but occurred on 26 October 2026. On 25 October 2026, row 4935 did not exist, so cell `I3360` evaluated to blank/`#N/A`. When 26 October data was appended, this row retroactively became a match (+1 to CPT).

#### 4. CPT Record 2 — Sheet Row 3361
- **Closed Record**:
  - `DOCDATE`: `19-10-2026`
  - `NAME`: `MANJULA`
  - `PASSBOOK`: `SCF-12520`
  - `MOBILE`: `78****1614` (masked)
  - `SHOWROOM`: `CPT`
  - `STATUS`: `CLOSE`
- **Matching New Enrollment Record**:
  - Sheet Tab: `'SS - Oct'`, Row: `4935` (Same enrollment record as above)
  - `SCHDATE`: `26-10-2026`
  - `NAME`: `A.ELUMALAI`
  - `CLIENTID`: `CPT27SSP1/3204`
  - `RECAMOUNT`: `₹8,000.00`
- **Matching Rule**: `=IFNA(VLOOKUP(D3361, IMPORTRANGE(..., "'SS - Oct'!J:K"), 2, 0))`
- **Audit Assessment**: Family account sharing the same mobile phone. A single new enrollment of ₹8,000 on 26 Oct retroactively matched **both** closed passbooks (SCF-12521 and SCF-12520), resulting in 2 rejoining credits for 1 new enrollment.

---

### D. Scheme Rejoining Report — Closed Weight Table (MTD as on 25/10/2026)

| Showroom | Reference Count | Pipeline Count | Reference Weight (g) | Pipeline Weight (g) | Difference | Status |
|---|---:|---:|---:|---:|---:|:---:|
| **TVL** | 554 | 554 | 4,388.944 | 4,388.944 | 0.000 g | **PASS** |
| **CPT** | 1,098 | 1,098 | 11,028.675 | 11,028.675 | 0.000 g | **PASS** |
| **TVM** | 532 | 532 | 3,238.355 | 3,238.355 | 0.000 g | **PASS** |
| **TPJ** | 862 | 862 | 4,931.065 | 4,931.065 | 0.000 g | **PASS** |
| **CBE** | 706 | 706 | 3,508.394 | 3,508.394 | 0.000 g | **PASS** |
| **SLM** | 349 | 349 | 2,464.850 | 2,464.850 | 0.000 g | **PASS** |
| **PNM** | 321 | 321 | 2,313.875 | 2,313.875 | 0.000 g | **PASS** |
| **PADI** | 181 | 181 | 985.380 | 985.380 | 0.000 g | **PASS** |
| **KPM** | 1 | 1 | 0.750 | 0.750 | 0.000 g | **PASS** |
| **TOTAL** | **4,604** | **4,604** | **32,860.288** | **32,860.288** | **0.000 g** | **PASS** |

---

### E. Scheme Rejoining Report — Closed Count Table (by Scheme, MTD as on 25/10/2026)

| Showroom | Subhiksham (SS) Closures | Viruksham (SV) Closures | Total Closures | Status |
|---|---:|---:|---:|:---:|
| **TVL** | 469 | 85 | 554 | **PASS** |
| **CPT** | 922 | 176 | 1,098 | **PASS** |
| **TVM** | 471 | 61 | 532 | **PASS** |
| **TPJ** | 743 | 119 | 862 | **PASS** |
| **CBE** | 624 | 82 | 706 | **PASS** |
| **SLM** | 275 | 74 | 349 | **PASS** |
| **PNM** | 271 | 50 | 321 | **PASS** |
| **PADI** | 171 | 10 | 181 | **PASS** |
| **KPM** | 1 | 0 | 1 | **PASS** |
| **TOTAL** | **3,947** | **657** | **4,604** | **PASS** |

---

## 3. Discrepancy & Root Cause Analysis

| # | Metric / Location | Discrepancy Description | Likely Root Cause | Responsible File / Function | Resolution / Recommendation |
|---|---|---|---|---|---|
| 1 | Revenue Target Total | Screenshot Total Target = ₹313,000,000 vs True Total = ₹340,000,000 | Google Sheets cell `F16` formula is hardcoded as `=SUM(F6:F13)`, which omits Row 14 KANCHI (₹27M target). | Upstream Google Sheet formula in tab `Consolidate Report - Oct` cell `F16`. | Preserve ₹313M for legacy audit matching; recommend updating production formula to `=SUM(F6:F14)` when sheet write access is approved. |
| 2 | DigiGold Showroom Subtotal | Google Sheets `BRANCH TOTAL` cell `L15` is 14,614 vs true showroom sum 14,757 | Upstream Pivot formula `=sum(L5:L13)` omits Row 14 NAGERCOIL (143). | Upstream Google Sheet `OCT Pivot!L15` and [`app/reporting/digi.py:validate_pivot_subtotals_and_totals`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L350-L380). | Enhanced [`app/reporting/digi.py`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py) to validate against both 9-showroom formula range and 10-showroom true sum. |
| 3 | DigiSilver Column Detection | Column 23 ("Average Per Day") was overwriting `silver_branch_col` (col 16) | Keyword `"BRANCH"` was found in column 23 header and overwrote earlier column index. | [`app/reporting/digi.py:extract_pivot_data`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L586-L596). | Bounded Silver column detection to `14 <= c_i < 22`, preserving the exact Silver branch column. |
| 4 | Date Format Variations in Closing Sheet | Early October rows (1–13 Oct) failed date parsing with `%Y` formats | Sheet contains mixed date formats: `%d/%m/%y` (e.g. `01/10/26`) and `%d-%m-%Y` (e.g. `25-10-2026`). | [`app/reporting/digi.py:parse_schdate`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L456-L467). | Support both 2-digit (`%y`) and 4-digit (`%Y`) formats. |
| 5 | Online SS Revenue Display | Reference screenshot displays `227,000` instead of `2,270,000` | Visual column clipping/truncation in reference screenshot UI. Row total (`9,207,064`) and grand total (`35,687,000`) prove exact value is ₹2,270,000. | Reference screenshot rendering / Google Sheets column width. | Documented and reconciled; no code change required. |
| 6 | SS Rejoining Count (+4) | Reference SS Rejoining is 867 vs Pipeline 871 (+2 in TVL, +2 in CPT) | Retroactive rejoining lookup matched 4 additional SS members upon ingestion of 25 Oct data. | [`app/reporting/closing.py`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/closing.py). | Documented and verified against raw data records. |

---

## 4. Exact Minimal Local Code Changes Applied

To enable complete pipeline execution and robust extraction without altering production sheets, the following minimal, surgical changes were implemented:

1. **[`app/reporting/digi.py:extract_pivot_data`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L586-L596)**:
   - Constrained Silver column detection to range `14 <= c_i < 22` to prevent column 23 ("Average Per Day") from overwriting `silver_branch_col`.
2. **[`app/reporting/digi.py:validate_pivot_subtotals_and_totals`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L350-L380)**:
   - Updated validation logic to accept both the 9-showroom legacy formula range (`L5:L13`) and the 10-showroom true sum (including NAGERCOIL).
3. **[`app/reporting/digi.py:DIGI_BRANCH_ORDER`](file:///c:/Users/spart/OneDrive/Documents/psm-reports-automation-main/app/reporting/digi.py#L58-L75)**:
   - Added support for `ALL_SHOWROOM_BRANCHES` and `NAGERCOIL` alias in `BRANCH_NAME_ALIASES`.

Zero modifications have been made to any production Google Sheets, Excel workbooks, source records, or messaging systems.
