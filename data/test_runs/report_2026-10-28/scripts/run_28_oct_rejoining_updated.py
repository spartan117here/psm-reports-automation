import os
import sys
from pathlib import Path
from datetime import date
from collections import defaultdict, Counter
import csv

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")
from app.google_sheets.client import GoogleApiSheetsService
from app.processing.rejoining import RejoiningMatchingEngine, parse_date_safe

def run():
    sa_path = str(project_root / "secrets" / "service_account.json")
    sheets = GoogleApiSheetsService(service_account_path=sa_path, read_only=True)
    enr_id = os.getenv("GOOGLE_SHEET_NEW_ENROLLMENT_ID")
    close_id = os.getenv("GOOGLE_SHEET_CLOSED_REJOINING_ID")
    
    target_date = date(2026, 10, 28)
    print(f"Loading data for Cutoff Date: {target_date}...")
    
    raw_ss = sheets.read_range(enr_id, "'SS - Oct'!A2:N")
    raw_sv = sheets.read_range(enr_id, "'SV - Oct'!A2:N")
    raw_close = sheets.read_range(close_id, "'October - 2026'!A2:H")
    
    print(f"Loaded {len(raw_ss)} SS rows, {len(raw_sv)} SV rows, {len(raw_close)} Closing rows.")
    
    # Check date distribution in closing sheet
    close_dates = Counter(parse_date_safe(r[0] if len(r)>0 else "") for r in raw_close)
    print(f"Closing records on 2026-10-28: {close_dates[target_date]} records")
    print(f"Total Closing records <= 2026-10-28: {sum(c for d, c in close_dates.items() if d and d <= target_date)} records")
    
    matcher = RejoiningMatchingEngine(
        cutoff_date=target_date,
        require_enrollment_after_closure=False
    )
    
    res = matcher.process(
        closed_records=raw_close,
        ss_enrollments=raw_ss,
        sv_enrollments=raw_sv
    )
    
    print("\n" + "="*70)
    print(f"SCHEME REJOINING REPORT AS ON {target_date.strftime('%d/%m/%Y')} (WITH 28-OCT CLOSURES)")
    print("="*70)
    
    print("\n--- TABLE 1: MAIN REJOINING TABLE ---")
    print(f"{'SHOWROOM':<10} | {'CLOSED':<8} | {'REJOIN SS':<10} | {'REJOIN SV':<10} | {'TOTAL REJOIN':<12} | {'REJOIN %':<10}")
    print("-" * 75)
    for row in res.table1_rows:
        print(f"{row.showroom:<10} | {row.closed_count:<8} | {row.rejoin_ss:<10} | {row.rejoin_sv:<10} | {row.total_rejoin:<12} | {row.rejoin_percentage:>8.2f}%")
    print("-" * 75)
    print(f"{'TOTAL':<10} | {res.total_closed_count:<8} | {res.total_rejoin_ss:<10} | {res.total_rejoin_sv:<10} | {res.total_rejoin_count:<12} | {res.overall_rejoin_percentage:>8.2f}%")
    
    counts_map = {r.showroom: r.closed_count for r in res.table1_rows}

    print("\n--- TABLE 2: CLOSED WEIGHT TABLE ---")
    print(f"{'SHOWROOM':<10} | {'COUNT':<8} | {'WEIGHT (g)':<12}")
    print("-" * 35)
    for row in res.table2_weight_rows:
        cnt = counts_map.get(row.showroom, 0)
        print(f"{row.showroom:<10} | {cnt:<8} | {row.closed_weight:>10.2f}")
    print("-" * 35)
    print(f"{'TOTAL':<10} | {res.total_closed_count:<8} | {res.total_closed_weight:>10.2f}")
    
    print("\n--- TABLE 3: CLOSED COUNT TABLE ---")
    print(f"{'SHOWROOM':<10} | {'SS':<8} | {'SV':<8} | {'TOTAL':<8}")
    print("-" * 40)
    tot_ss = 0
    tot_sv = 0
    for row in res.table3_scheme_rows:
        ss_cnt = sum(cnt for sch, cnt in row.scheme_counts.items() if "SUBIKSHAM" in sch.upper() or "SS" in sch.upper())
        sv_cnt = sum(cnt for sch, cnt in row.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or "SV" in sch.upper())
        tot = ss_cnt + sv_cnt
        tot_ss += ss_cnt
        tot_sv += sv_cnt
        print(f"{row.showroom:<10} | {ss_cnt:<8} | {sv_cnt:<8} | {tot:<8}")
    print("-" * 40)
    print(f"{'TOTAL':<10} | {tot_ss:<8} | {tot_sv:<8} | {tot_ss + tot_sv:<8}")

    # Save to CSV
    out_dir = project_root / "data" / "test_runs" / "report_2026-10-28"
    csv_path = out_dir / "Scheme_Rejoining_Report_2026-10-28.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([f"SCHEME REJOINING REPORT AS ON {target_date.strftime('%d/%m/%Y')}"])
        w.writerow(["TABLE 1: MAIN REJOINING TABLE"])
        w.writerow(["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"])
        for row in res.table1_rows:
            w.writerow([row.showroom, row.closed_count, row.rejoin_ss, row.rejoin_sv, row.total_rejoin, f"{row.rejoin_percentage:.2f}%"])
        w.writerow(["TOTAL", res.total_closed_count, res.total_rejoin_ss, res.total_rejoin_sv, res.total_rejoin_count, f"{res.overall_rejoin_percentage:.2f}%"])
        w.writerow([])
        w.writerow(["TABLE 2: CLOSED WEIGHT TABLE"])
        w.writerow(["SHOWROOM", "COUNT", "WEIGHT (g)"])
        for row in res.table2_weight_rows:
            cnt = counts_map.get(row.showroom, 0)
            w.writerow([row.showroom, cnt, f"{row.closed_weight:.2f}"])
        w.writerow(["TOTAL", res.total_closed_count, f"{res.total_closed_weight:.2f}"])
        w.writerow([])
        w.writerow(["TABLE 3: CLOSED COUNT TABLE"])
        w.writerow(["SHOWROOM", "SS", "SV"])
        for row in res.table3_scheme_rows:
            ss_cnt = sum(cnt for sch, cnt in row.scheme_counts.items() if "SUBIKSHAM" in sch.upper() or "SS" in sch.upper())
            sv_cnt = sum(cnt for sch, cnt in row.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or "SV" in sch.upper())
            w.writerow([row.showroom, ss_cnt, sv_cnt])
        w.writerow(["TOTAL", tot_ss, tot_sv])
    print(f"\nUpdated Scheme Rejoining CSV saved: {csv_path}")

    # Generate high resolution screenshot using PIL
    from PIL import Image, ImageDraw, ImageFont
    img_w, img_h = 1050, 950
    img = Image.new("RGB", (img_w, img_h), "#ffffff")
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("arialbd.ttf", 20)
        font_bold = ImageFont.truetype("arialbd.ttf", 13)
        font_regular = ImageFont.truetype("arial.ttf", 13)
        font_sub = ImageFont.truetype("arialbd.ttf", 15)
    except:
        font_title = font_bold = font_regular = font_sub = ImageFont.load_default()

    # Draw Title
    draw.rectangle([0, 0, img_w, 45], fill="#1e3a8a")
    draw.text((img_w // 2, 22), f"Scheme Rejoining Report as on {target_date.strftime('%d/%m/%Y')}", fill="#ffffff", font=font_title, anchor="mm")

    y_offset = 65
    draw.text((30, y_offset), "Section A: Closed Member Rejoining", fill="#1e3a8a", font=font_sub)
    y_offset += 25

    # Section A table
    headers_a = ["Showroom", "Closed", "Rejoin SS", "Rejoin SV", "Total Rejoin", "Rejoin %"]
    cols_a = [40, 180, 310, 440, 570, 710, 850]
    draw.rectangle([cols_a[0], y_offset, cols_a[-1], y_offset + 26], fill="#dbeafe")
    for i, h in enumerate(headers_a):
        draw.text((cols_a[i] + 10, y_offset + 6), h, fill="#1e3a8a", font=font_bold)
    y_offset += 26

    for r in res.table1_rows:
        draw.rectangle([cols_a[0], y_offset, cols_a[-1], y_offset + 24], outline="#e5e7eb")
        draw.text((cols_a[0] + 10, y_offset + 5), r.showroom, fill="#111827", font=font_bold)
        draw.text((cols_a[1] + 10, y_offset + 5), f"{r.closed_count:,}", fill="#111827", font=font_regular)
        draw.text((cols_a[2] + 10, y_offset + 5), f"{r.rejoin_ss:,}", fill="#111827", font=font_regular)
        draw.text((cols_a[3] + 10, y_offset + 5), f"{r.rejoin_sv:,}", fill="#111827", font=font_regular)
        draw.text((cols_a[4] + 10, y_offset + 5), f"{r.total_rejoin:,}", fill="#111827", font=font_regular)
        pct_color = "#15803d" if r.rejoin_percentage >= 20.0 else "#b45309" if r.rejoin_percentage >= 15.0 else "#b91c1c"
        draw.text((cols_a[5] + 10, y_offset + 5), f"{r.rejoin_percentage:.2f}%", fill=pct_color, font=font_bold)
        y_offset += 24

    # Total A
    draw.rectangle([cols_a[0], y_offset, cols_a[-1], y_offset + 26], fill="#fef3c7")
    draw.text((cols_a[0] + 10, y_offset + 6), "TOTAL", fill="#92400e", font=font_bold)
    draw.text((cols_a[1] + 10, y_offset + 6), f"{res.total_closed_count:,}", fill="#92400e", font=font_bold)
    draw.text((cols_a[2] + 10, y_offset + 6), f"{res.total_rejoin_ss:,}", fill="#92400e", font=font_bold)
    draw.text((cols_a[3] + 10, y_offset + 6), f"{res.total_rejoin_sv:,}", fill="#92400e", font=font_bold)
    draw.text((cols_a[4] + 10, y_offset + 6), f"{res.total_rejoin_count:,}", fill="#92400e", font=font_bold)
    draw.text((cols_a[5] + 10, y_offset + 6), f"{res.overall_rejoin_percentage:.2f}%", fill="#92400e", font=font_bold)
    y_offset += 45

    # Side-by-side Section B and C
    draw.text((40, y_offset), "Section B: Closed Weight", fill="#1e3a8a", font=font_sub)
    draw.text((500, y_offset), "Section C: Closed Scheme Split", fill="#1e3a8a", font=font_sub)
    y_offset += 25

    cols_b = [40, 180, 290, 420]
    cols_c = [500, 640, 750, 860]

    draw.rectangle([cols_b[0], y_offset, cols_b[-1], y_offset + 26], fill="#dbeafe")
    draw.text((cols_b[0] + 10, y_offset + 6), "Showroom", fill="#1e3a8a", font=font_bold)
    draw.text((cols_b[1] + 10, y_offset + 6), "Count", fill="#1e3a8a", font=font_bold)
    draw.text((cols_b[2] + 10, y_offset + 6), "Weight (g)", fill="#1e3a8a", font=font_bold)

    draw.rectangle([cols_c[0], y_offset, cols_c[-1], y_offset + 26], fill="#dbeafe")
    draw.text((cols_c[0] + 10, y_offset + 6), "Showroom", fill="#1e3a8a", font=font_bold)
    draw.text((cols_c[1] + 10, y_offset + 6), "SS Count", fill="#1e3a8a", font=font_bold)
    draw.text((cols_c[2] + 10, y_offset + 6), "SV Count", fill="#1e3a8a", font=font_bold)
    y_offset += 26

    base_y = y_offset
    for r in res.table2_weight_rows:
        draw.rectangle([cols_b[0], y_offset, cols_b[-1], y_offset + 24], outline="#e5e7eb")
        draw.text((cols_b[0] + 10, y_offset + 5), r.showroom, fill="#111827", font=font_bold)
        cnt = counts_map.get(r.showroom, 0)
        draw.text((cols_b[1] + 10, y_offset + 5), f"{cnt:,}", fill="#111827", font=font_regular)
        draw.text((cols_b[2] + 10, y_offset + 5), f"{r.closed_weight:,.2f}", fill="#111827", font=font_regular)
        y_offset += 24

    draw.rectangle([cols_b[0], y_offset, cols_b[-1], y_offset + 26], fill="#fef3c7")
    draw.text((cols_b[0] + 10, y_offset + 6), "TOTAL", fill="#92400e", font=font_bold)
    draw.text((cols_b[1] + 10, y_offset + 6), f"{res.total_closed_count:,}", fill="#92400e", font=font_bold)
    draw.text((cols_b[2] + 10, y_offset + 6), f"{res.total_closed_weight:,.2f}", fill="#92400e", font=font_bold)

    y_c = base_y
    for r in res.table3_scheme_rows:
        draw.rectangle([cols_c[0], y_c, cols_c[-1], y_c + 24], outline="#e5e7eb")
        draw.text((cols_c[0] + 10, y_c + 5), r.showroom, fill="#111827", font=font_bold)
        sh_ss = sum(cnt for sch, cnt in r.scheme_counts.items() if "SUBIKSHAM" in sch.upper() or "SS" in sch.upper())
        sh_sv = sum(cnt for sch, cnt in r.scheme_counts.items() if "VIRUKSHAM" in sch.upper() or "SV" in sch.upper())
        draw.text((cols_c[1] + 10, y_c + 5), f"{sh_ss:,}", fill="#111827", font=font_regular)
        draw.text((cols_c[2] + 10, y_c + 5), f"{sh_sv:,}", fill="#111827", font=font_regular)
        y_c += 24

    draw.rectangle([cols_c[0], y_c, cols_c[-1], y_c + 26], fill="#fef3c7")
    draw.text((cols_c[0] + 10, y_c + 6), "TOTAL", fill="#92400e", font=font_bold)
    draw.text((cols_c[1] + 10, y_c + 6), f"{tot_ss:,}", fill="#92400e", font=font_bold)
    draw.text((cols_c[2] + 10, y_c + 6), f"{tot_sv:,}", fill="#92400e", font=font_bold)

    png_path = out_dir / "screenshots" / "Scheme_Rejoining_Report_2026-10-28.png"
    img.save(png_path)
    print(f"Updated Scheme Rejoining PNG saved: {png_path}")

if __name__ == "__main__":
    run()
