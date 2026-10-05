# Registry of Unconfirmed Business Rules & Technical Dependencies

This document tracks all external dependencies, unverified formulas, and pending configurations required before future phases can be implemented.

---

## 1. Phase 1 — Innervex Authentication & Connectivity Dependencies

- [ ] **Exact Login Endpoint & Protocol**:
  - The login URL, HTTP method (GET/POST), and form fields (e.g., `username`, `password`, or session token) are not yet confirmed.
  - Verification needed: Inspect network traffic during an authorized browser login at `http://192.168.5.213:4499`.
- [ ] **Session Expiry & Refresh Behavior**:
  - How long does the authenticated session (JSESSIONID) remain valid?
  - Does the server issue a new cookie on each request or require re-login after inactivity?
- [ ] **LAN Firewall & IP Access Restrictions**:
  - Confirm whether the server only accepts requests from specific subnet IP addresses or requires VPN access.

---

## 2. Phase 2 — Scheme Filter Specifications

- [ ] **Full 31 Showroom Code List**:
  - Currently observed 10 branch prefixes: `TVC`, `TVL`, `CPT`, `KPM`, `PAD`, `SLM`, `PNM`, `CBE`, `TRI`, `TPJ`.
  - The remaining 21 showroom codes used by the Innervex selector need to be documented in `config/mappings.yaml`.
- [ ] **Subhiksham Sub-Scheme Names**:
  - The exact 4 sub-scheme values in the filter selector for "NEW SWARNA SUBHIKSHAM" need to be verified.
- [ ] **Viruksham Sub-Scheme Names**:
  - The exact 2 sub-scheme values in the filter selector for "SWARNA VIRUKSHAM" need to be verified.

---

## 3. Phase 3 & 4 — Cleaning & Location Attribution

- [ ] **Deduplication Key Verification**:
  - Validate against historical production data whether `MSNO` is 100% unique per enrollment, or if a composite key (`CLIENTID + SCHDATE` or `MSNO + SCHDATE`) is required.
- [ ] **Comprehensive Employee Code Mapping Table**:
  - Obtain the complete master employee-code-to-branch directory (or confirm if it can be dynamically synced from the "Employees" Google Sheet tab).
- [ ] **Special Corporate & Online Attribution Rules**:
  - Formalize the exact business attribution rules for records with `COSTNAME` or `COMMCODE` containing `ONLINE`, `APP`, `CORPORATE OFFICE`, or `HEAD OFFICE`.

---

## 4. Phase 5 & 6 — Google Sheets & Consolidation

- [ ] **Google Sheets Identifiers & Service Account**:
  - Provide Google Cloud Service Account JSON file with editor permissions.
  - Provide Workbook ID for *"New Enrollment from April 2026"*.
- [ ] **Monthly Sheet Naming Convention**:
  - Confirm whether tabs follow `"SS - <Mon>"` (e.g. `SS - Oct` or `SS - Oct 2026`) across all fiscal years.
- [ ] **Target Allocation Updates**:
  - Confirm if branch targets (Q2/H1) change quarterly and establish the authorized process to update `config/targets.yaml`.

---

## 5. Phase 7 — Closing & Rejoining Reports

- [ ] **Scheme Closing Report Cleaned Columns**:
  - Innervex provides ~15 columns.
  - Need confirmed list of the exact 7 columns kept in the manual cleaned process.
- [ ] **Closed Member & Rejoining Matching Engine**:
  - Confirm the exact business rule/matching logic used to identify whether a closed member has rejoined under Subhiksham (`REJOIN SS`) or Viruksham (`REJOIN SV`).
  - Is matching performed on `MOBILENO`, `CLIENTID`, or `PASSBOOK`?

---

## 6. Phase 8 — DigiGold / DigiSilver Integration

- [ ] **Digi Gold Storewise Workbook Details**:
  - Provide Workbook ID for *"DiGi Gold New Member - Storewise (FY 26-27)"*.
  - Confirm tab layout and formulas used to feed DigiGold and DigiSilver values into the monthly Consolidate Report.

---

## 7. Phase 10 & 11 — Report Images & Executive Delivery

- [ ] **Report Image Card Specifications**:
  - Obtain visual templates/layouts preferred by the AGM for:
    1. New Enrollment Summary Card
    2. Backlog / Target Achievement Card
    3. Rejoining Summary Card
- [ ] **Approved Delivery Channel**:
  - Formalize the authorized distribution channel (e.g. official WhatsApp Business API, corporate SMTP email, or Telegram bot).
  - Provide recipient phone numbers or email distribution lists.

---

## 8. Phase 12 — Production Server & Scheduling

- [ ] **Target Windows Server Environment**:
  - Identify host machine, service account, and network access permissions for Windows Task Scheduler configuration.
- [ ] **Execution Schedule**:
  - Confirm daily trigger time (e.g., daily at 06:00 AM IST to process previous day's numbers).
