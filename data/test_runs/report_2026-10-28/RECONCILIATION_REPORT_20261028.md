# Independent Source-to-Report Reconciliation Audit — 28 October 2026

- **Report Date**: 28 October 2026
- **Audit Execution Timestamp**: 2026-10-29T12:50:00 IST
- **Pipeline Mode**: `STRICT_READ_ONLY=true` (Zero production modifications, zero live transmissions)
- **Investigation Scope**: Reconcile Viruksham Revenue (₹109,400 difference) and DigiGold Count (4,000 difference) between Pipeline Output and Manual Report.

---

## Executive Verdict & Summary of Findings

| Metric | Pipeline Generated | Manual Reference Report | Variance (Manual − Pipeline) | Correct Figure | Defect Origin |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **TVM Viruksham (SV)** | ₹2,24,49,742 | ₹2,25,59,142 | **+₹1,09,400** | **₹2,24,49,742** | **Manual Sheet Formula Bug (`=C44+C45`)** |
| **Total Viruksham (SV)** | ₹34,53,15,879 | ₹34,54,25,279 | **+₹1,09,400** | **₹34,53,15,879** | **Manual Sheet Formula Bug (Double-counts C44)** |
| **Grand Total Revenue** | ₹38,42,20,379 | ₹38,43,29,779 | **+₹1,09,400** | **₹38,42,20,379** | **Manual Sheet Formula Bug** |
| **E COMM DigiGold (DG)**| 5,444 | 1,444 | **-4,000** | **5,444** | **Manual Sheet Typo (Cell I32 typed `1444`)** |
| **E COMM Total Count** | 6,978 | 2,978 | **-4,000** | **6,978** | **Manual Sheet Typo** |
| **DigiGold Grand Total** | 21,168 | 17,168 | **-4,000** | **21,168** | **Manual Sheet Typo (Cascaded to SUM)** |
| **Overall Grand Count** | 32,655 | 28,655 | **-4,000** | **32,655** | **Manual Sheet Typo (Cascaded to SUM)** |
| **Daily Average Count** | 1,166 | 1,023 | **-143** | **1,166** | **Manual Sheet Typo ($28,655 / 28$ vs $32,655 / 28$)** |

### Definitive Conclusion
1. **The Pipeline Output is 100% mathematically and factually CORRECT.**
2. **The Manual Reference Report contains two distinct upstream clerical defects in Google Sheets:**
   - **Formula Defect in Table 1 Cell H8**: Accidental double-counting of Row 44 (`TRICHY SV ECOMM`, ₹109,400) into Trivandrum.
   - **Typographical Defect in Table 2 Cell I32**: Accidental miskeying of `1444` instead of `5444` for E COMM DigiGold.
3. **Delivery Safety Recommendation**: The pipeline figures are **SAFE TO DELIVER** as the true representation of business performance.

---

## Issue 1 Investigation: Viruksham Revenue Discrepancy (₹109,400)

### 1. Branch-by-Branch SV Comparison

Every single branch's Viruksham revenue was recalculated independently from raw records in `SV - Oct` (1,085 rows) dated on or before 28 October 2026:

| Branch | Raw SV Transactions | Raw Amount Sum (Pipeline) | Manual Report (Consolidate Table 1) | Discrepancy | Underlying Pivot Source Cell |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **TVL** | 133 | ₹4,93,00,281.00 | ₹4,93,00,281.00 | ₹0 | `=C41+C42` (TIRUNELVELI + ECOMM) |
| **CPT** | 264 | ₹8,20,36,981.00 | ₹8,20,36,981.00 | ₹0 | `=C28+C29` (CHROMEPET + ECOMM) |
| **TVM** | **42** | **₹2,24,49,742.00** | **₹2,25,59,142.00** | **+₹1,09,400** | **`=C44+C45` (TRICHY ECOMM + TRIVANDRUM)** |
| **TPJ** | 95 | ₹3,85,25,356.00 | ₹3,85,25,356.00 | ₹0 | `=C43+C44` (TRICHY + TRICHY ECOMM) |
| **CBE** | 79 | ₹2,30,32,929.00 | ₹2,30,32,929.00 | ₹0 | `=C30+C31` (COIMBATORE + ECOMM) |
| **SLM** | 92 | ₹3,01,06,441.00 | ₹3,01,06,441.00 | ₹0 | `=C39+C40` (SALEM + ECOMM) |
| **PNM** | 51 | ₹1,56,49,225.00 | ₹1,56,49,225.00 | ₹0 | `=C38` (POONAMALLEE) |
| **PADI** | 190 | ₹5,75,01,201.00 | ₹5,75,01,201.00 | ₹0 | `=C36+C37` (PADI + ECOMM) |
| **KANCHI**| 48 | ₹1,30,99,106.00 | ₹1,30,99,106.00 | ₹0 | `=C33` (KANCHEEPURAM) |
| **NGL** | 11 | ₹44,86,533.00 | ₹44,86,533.00 | ₹0 | `=C34` (NAGARKOVIL) |
| **ONLINE**| 80 | ₹91,28,084.00 | ₹91,28,084.00 | ₹0 | `=C32+C35` (CORP OFFICE + ONLINE) |
| **Total** | **1,085** | **₹34,53,15,879.00** | **₹34,54,25,279.00** | **+₹1,09,400** | **Pivot Grand Total is ₹34,53,15,879** |

### 2. Exact Formula Bug Identification in Google Sheets
In spreadsheet `1MijDDFq5g0j4yR4buI_q04yGLst-cK8HlQgW_X06zCc`, tab `Consolidate Report - Oct`:
1. The Viruksham Pivot Table occupies rows 26 to 46:
   - **Row 43**: `TRICHY SV` | Count: 94 | Amount (Cell C43): **₹3,84,15,956**
   - **Row 44**: `TRICHY SV ECOMM` | Count: 1 | Amount (Cell C44): **₹1,09,400**
   - **Row 45**: `TRIVANDRUM SV` | Count: 42 | Amount (Cell C45): **₹2,24,49,742**
   - **Row 46**: `Grand Total` | Count: 1,085 | Amount (Cell C46): **₹34,53,15,879**
2. In the summary table:
   - **Cell H9 (TPJ / Trichy)** formula: `=C43+C44` $\rightarrow 38,415,956 + 109,400 = \mathbf{38,525,356}$. (Correct)
   - **Cell H8 (TVM / Trivandrum)** formula: `=C44+C45` $\rightarrow 109,400 + 22,449,742 = \mathbf{22,559,142}$. (**DEFECT**)
3. Why did this happen?
   - In Subhiksham (SS), Trivandrum has two rows in the pivot: `Row 21: TRIVANDRUM` and `Row 22: TRIVANDRUM ECOMM`, so cell G8 is `=C21+C22`.
   - The creator assumed Trivandrum had two rows in Viruksham as well and wrote `=C44+C45`.
   - However, in Viruksham, Trivandrum has only **one** row (Row 45). Row 44 is `TRICHY SV ECOMM`.
4. As a result:
   - Cell C44 (`TRICHY SV ECOMM`, ₹109,400) was double-counted into both TPJ and TVM.
   - Cell H17 (`=SUM(H6:H16)`) summed the column, adding ₹109,400 twice:
     $$\text{Actual Pivot Grand Total (C46)} + \text{Double-counted C44} = ₹34,53,15,879 + ₹1,09,400 = \mathbf{₹34,54,25,279}$$
5. Underlying Transaction Proof:
   - Row 34 in `SV - Oct`: `Client ID: APP27SVP1/503 | Name: SENTHIL KUMAR | Amount: 109,400 | Location 2: TRICHY SV ECOMM`.
   - There are zero Trivandrum transactions of ₹109,400. Trivandrum SV has 42 transactions totaling exactly ₹22,449,742.00, unchanged from 25 October.

---

## Issue 2 Investigation: DigiGold Count Discrepancy (4,000)

### 1. Comparison of Pipeline Output vs Manual Report

| Field | Pipeline Output | Manual Report | Variance | Cause |
| :--- | :---: | :---: | :---: | :--- |
| **E COMM DigiGold** | **5,444** | **1,444** | **-4,000** | Human Typo in Cell I32 |
| **E COMM Total Count** | **6,978** | **2,978** | **-4,000** | Formula `=G32+H32+I32+J32` |
| **DigiGold Grand Total**| **21,168** | **17,168** | **-4,000** | Formula `=SUM(I22:I32)` |
| **Grand Total Count** | **32,655** | **28,655** | **-4,000** | Formula `=SUM(K22:K32)` |
| **Daily Average** | **1,166** | **1,023** | **-143** | $32,655 / 28$ vs $28,655 / 28$ |

### 2. Proof of Upstream Typo in Google Sheets
In spreadsheet `1MijDDFq5g0j4yR4buI_q04yGLst-cK8HlQgW_X06zCc`, tab `Consolidate Report - Oct`:
1. In Table 2, all DigiGold and DigiSilver figures in columns I and J are **manually typed literal numbers**, not formulas linked to the Digi sheet.
2. Cell I32 contains the literal typed string: `1444`.
3. In the official Digi spreadsheet (`1N-4NRCNGHs702W0TlpQpGqgpPsJV9xH5QwXFfR18j2U`), tab `OCT Pivot`:
   - **Row 17, Columns J to M**:
     `ECOMMERCE | Target: 5000 | Achieved: 5,444 | 💯`
   - The achieved count is explicitly **5,444**, accompanied by the `💯` achievement badge.
4. Independent Raw Transaction Audit (`OCT` tab):
   - Total `OCT` rows: 26,811 (21,753 DigiGold + 5,058 DigiSilver).
   - DigiGold branch attribution:
     - `#N/A` (Direct E-Commerce / Online): **5,318**
     - `CORPORATE OFFICE`: **126**
     - E-COMM Total: $5,318 + 126 = \mathbf{5,444}$ records.
5. Historical Month-to-Date Progression:
   - **26 October** Reference Screenshot: E COMM DG = **5,179** (Total DG = 19,936)
   - **27 October** Reference Screenshot: E COMM DG = **5,283** (Total DG = 20,546) [New: +104]
   - **28 October** Raw Transactions: E COMM DG = **5,444** (Total DG = 21,168) [New: +161]
   - If the manual report's `1,444` were accepted, cumulative month-to-date E COMM enrollments would have crashed by **-3,839** overnight, and Grand Total DG would have crashed by **-3,378**, which is mathematically and operationally impossible for cumulative month-to-date tracking.

---

## Final Validation & Delivery Statement

### Validation Checklist
- [x] All raw transactions in `SS - Oct` (5,371 rows) reconciled.
- [x] All raw transactions in `SV - Oct` (1,085 rows) reconciled.
- [x] All raw transactions in `OCT` (26,811 rows) reconciled.
- [x] Date cutoff strictly enforced at 2026-10-28 23:59:59.
- [x] Every branch arithmetic total verified across all columns and rows.
- [x] Upstream formula bug in Table 1 Cell H8 pinpointed with row/cell references.
- [x] Upstream clerical keystroke typo in Table 2 Cell I32 pinpointed with row/cell references.

### Final Classification
**`PASS WITH WARNINGS — REVIEW REQUIRED`**
- **Safety to Deliver**: **YES** — The pipeline-calculated figures are the true, auditable reflection of company performance. The manual report figures for SV revenue and E COMM DigiGold are demonstrably compromised by spreadsheet formula and typing defects.

