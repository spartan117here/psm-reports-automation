"""
Excel-Style Report Image Renderer.

Generates high-resolution, presentation-ready spreadsheet report cards directly from
independently calculated pipeline results with authentic Excel/Google Sheets visual styling:
- Clear spreadsheet-style rows and columns with crisp cell gridlines.
- Professional typography (Arial / Calibri).
- Exact header banners, background shading, and conditional highlight colors.
- Precise alignment (left-aligned showroom/branch names, right-aligned monetary values and counts).
- Zero dependency on pre-existing consolidated Google Sheets report tabs.
"""

import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont


def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Resolve standard Windows sans-serif fonts with fallback."""
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_enrollment_report_image(
    target_date: date,
    table1_rows: List[Dict[str, Any]],
    table1_total: Dict[str, Any],
    table2_rows: List[Dict[str, Any]],
    table2_total: Dict[str, Any],
    output_path: Path,
) -> Path:
    """
    Render Photo 1: New Enrollment Report (Revenue Table 1 + Enrollment Count Table 2).
    """
    target_date_str = target_date.strftime("%d/%m/%Y")
    img_w = 1460
    img_h = 1240
    im = Image.new("RGB", (img_w, img_h), "#ffffff")
    d = ImageDraw.Draw(im)

    font_title = _get_font(22, bold=True)
    font_header = _get_font(18, bold=True)
    font_body = _get_font(18, bold=False)
    font_body_bold = _get_font(18, bold=True)
    font_total = _get_font(19, bold=True)

    BORDER_COLOR = "#000000"
    BANNER_BLUE = "#6d9eeb"
    HEADER_BLUE = "#b4c6e7"
    TOTAL_YELLOW = "#fbbc04"
    GREEN_ACHIEVED = "#a9d18e"
    YELLOW_ACHIEVED = "#fff2cc"

    margin_x = 20
    content_w = img_w - (margin_x * 2)

    # -------------------------------------------------------------
    # TABLE 1: REVENUE
    # -------------------------------------------------------------
    y = 20

    # Title Banner
    banner_h = 42
    d.rectangle([margin_x, y, margin_x + content_w, y + banner_h], fill=BANNER_BLUE, outline=BORDER_COLOR, width=1)
    title_text = f"New Enrollment as on {target_date_str}"
    bbox = d.textbbox((0, 0), title_text, font=font_title)
    tx = margin_x + (content_w - (bbox[2] - bbox[0])) // 2
    ty = y + (banner_h - (bbox[3] - bbox[1])) // 2 - 2
    d.text((tx, ty), title_text, fill="#000000", font=font_title)
    y += banner_h

    cols_t1_widths = [160, 210, 210, 220, 220, 210, 190]
    cols_t1_x = [margin_x]
    for w in cols_t1_widths:
        cols_t1_x.append(cols_t1_x[-1] + w)

    headers_t1 = ["Branch", "Target Value", "Subhiksham", "Viruksham", "Total", "Yet to Achieve", "Achieved %"]
    row_h_t1 = 36

    # Header Row
    d.rectangle([margin_x, y, margin_x + content_w, y + row_h_t1], fill=HEADER_BLUE, outline=BORDER_COLOR, width=1)
    for i, h in enumerate(headers_t1):
        cx1 = cols_t1_x[i]
        cx2 = cols_t1_x[i + 1]
        d.line([(cx2, y), (cx2, y + row_h_t1)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), h, font=font_header)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (row_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), h, fill="#000000", font=font_header)
    y += row_h_t1

    # Data Rows Table 1
    for r in table1_rows:
        d.rectangle([margin_x, y, margin_x + content_w, y + row_h_t1], fill="#ffffff", outline=BORDER_COLOR, width=1)
        for i in range(len(cols_t1_widths)):
            cx1 = cols_t1_x[i]
            cx2 = cols_t1_x[i + 1]
            d.line([(cx2, y), (cx2, y + row_h_t1)], fill=BORDER_COLOR, width=1)

            fnt = font_body
            if i == 0:  # Branch
                val_str = str(r["branch"])
                fnt = font_body_bold
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            elif i == 1:  # Target
                val_str = str(int(r["target"])) if r.get("target") else ""
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx2 - (bbox[2] - bbox[0]) - 16
            elif i in (2, 3, 4, 5):  # Amounts
                key = ["ss", "sv", "total", "yet"][i - 2]
                val = r[key]
                val_str = str(int(round(val)))
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx2 - (bbox[2] - bbox[0]) - 16
            elif i == 6:  # Achieved %
                val_str = f"{r['ach_pct']}%" if r.get("ach_pct") is not None else ""
                if r.get("ach_pct") is not None:
                    ach_color = GREEN_ACHIEVED if r["ach_pct"] >= 100 else (YELLOW_ACHIEVED if r["ach_pct"] < 80 else "#ffffff")
                    d.rectangle([cx1 + 1, y + 1, cx2 - 1, y + row_h_t1 - 1], fill=ach_color)
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2

            ty = y + (row_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
            d.text((tx, ty), val_str, fill="#000000", font=fnt)
        y += row_h_t1

    # Total Row Table 1
    total_h_t1 = 38
    d.rectangle([margin_x, y, margin_x + content_w, y + total_h_t1], fill=TOTAL_YELLOW, outline=BORDER_COLOR, width=2)
    tot_vals_t1 = [
        "Total",
        str(int(table1_total["target"])),
        str(int(round(table1_total["ss"]))),
        str(int(round(table1_total["sv"]))),
        str(int(round(table1_total["total"]))),
        str(int(round(table1_total["yet"]))),
        f"{table1_total['ach_pct']}%",
    ]
    for i, val_str in enumerate(tot_vals_t1):
        cx1 = cols_t1_x[i]
        cx2 = cols_t1_x[i + 1]
        d.line([(cx2, y), (cx2, y + total_h_t1)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), val_str, font=font_total)
        if i == 0 or i == 6:
            tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        else:
            tx = cx2 - (bbox[2] - bbox[0]) - 16
        ty = y + (total_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), val_str, fill="#000000", font=font_total)
    y += total_h_t1

    # -------------------------------------------------------------
    # TABLE 2: ENROLLMENT COUNTS
    # -------------------------------------------------------------
    y += 45

    cols_t2_widths = [160, 180, 175, 175, 175, 175, 220, 160]
    cols_t2_x = [margin_x]
    for w in cols_t2_widths:
        cols_t2_x.append(cols_t2_x[-1] + w)

    h_row1 = 34
    h_row2 = 32
    total_hdr_h = h_row1 + h_row2

    # Top Header Row (Table 2)
    d.rectangle([margin_x, y, margin_x + content_w, y + total_hdr_h], fill=HEADER_BLUE, outline=BORDER_COLOR, width=1)

    # Col 0: Branch (rowspan 2)
    d.line([(cols_t2_x[1], y), (cols_t2_x[1], y + total_hdr_h)], fill=BORDER_COLOR, width=1)
    b_txt = "Branch"
    bbox = d.textbbox((0, 0), b_txt, font=font_header)
    d.text((cols_t2_x[0] + (cols_t2_widths[0] - (bbox[2] - bbox[0])) // 2, y + (total_hdr_h - (bbox[3] - bbox[1])) // 2 - 1), b_txt, fill="#000000", font=font_header)

    # Col 1: Count Target (rowspan 2)
    d.line([(cols_t2_x[2], y), (cols_t2_x[2], y + total_hdr_h)], fill=BORDER_COLOR, width=1)
    ct_lines = ["Count Target", "SS + SV + DG", "+ DS"]
    font_hdr_small = _get_font(15, bold=True)
    ct_y = y + 8
    for line in ct_lines:
        bbox = d.textbbox((0, 0), line, font=font_hdr_small)
        d.text((cols_t2_x[1] + (cols_t2_widths[1] - (bbox[2] - bbox[0])) // 2, ct_y), line, fill="#000000", font=font_hdr_small)
        ct_y += 18

    # Col 2-5: Achieved Count (colspan 4 on row 1)
    d.line([(cols_t2_x[6], y), (cols_t2_x[6], y + total_hdr_h)], fill=BORDER_COLOR, width=1)
    ach_txt = "Achieved Count"
    bbox = d.textbbox((0, 0), ach_txt, font=font_header)
    ach_w = sum(cols_t2_widths[2:6])
    d.text((cols_t2_x[2] + (ach_w - (bbox[2] - bbox[0])) // 2, y + (h_row1 - (bbox[3] - bbox[1])) // 2 - 1), ach_txt, fill="#000000", font=font_header)

    # Subheaders for row 2 (Subhiksham, Viruksham, DigiGold, DigiSilver)
    d.line([(cols_t2_x[2], y + h_row1), (cols_t2_x[6], y + h_row1)], fill=BORDER_COLOR, width=1)
    sub_hdrs = ["Subhiksham", "Viruksham", "DigiGold", "DigiSilver"]
    for idx, sh in enumerate(sub_hdrs):
        cx1 = cols_t2_x[2 + idx]
        cx2 = cols_t2_x[3 + idx]
        if idx > 0:
            d.line([(cx1, y + h_row1), (cx1, y + total_hdr_h)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), sh, font=_get_font(17, bold=True))
        d.text((cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2, y + h_row1 + (h_row2 - (bbox[3] - bbox[1])) // 2 - 1), sh, fill="#000000", font=_get_font(17, bold=True))

    # Col 6: SS + SV + DG + DS (rowspan 2)
    d.line([(cols_t2_x[7], y), (cols_t2_x[7], y + total_hdr_h)], fill=BORDER_COLOR, width=1)
    comb_lines = ["SS + SV + DG +", "DS"]
    comb_y = y + 16
    for line in comb_lines:
        bbox = d.textbbox((0, 0), line, font=font_header)
        d.text((cols_t2_x[6] + (cols_t2_widths[6] - (bbox[2] - bbox[0])) // 2, comb_y), line, fill="#000000", font=font_header)
        comb_y += 20

    # Col 7: AVG (rowspan 2)
    avg_txt = "AVG"
    bbox = d.textbbox((0, 0), avg_txt, font=font_header)
    d.text((cols_t2_x[7] + (cols_t2_widths[7] - (bbox[2] - bbox[0])) // 2, y + (total_hdr_h - (bbox[3] - bbox[1])) // 2 - 1), avg_txt, fill="#000000", font=font_header)

    y += total_hdr_h

    # Data Rows Table 2
    row_h_t2 = 35
    for r in table2_rows:
        d.rectangle([margin_x, y, margin_x + content_w, y + row_h_t2], fill="#ffffff", outline=BORDER_COLOR, width=1)
        for i in range(len(cols_t2_widths)):
            cx1 = cols_t2_x[i]
            cx2 = cols_t2_x[i + 1]
            d.line([(cx2, y), (cx2, y + row_h_t2)], fill=BORDER_COLOR, width=1)

            fnt = font_body
            if i == 0:  # Branch
                val_str = str(r["branch"])
                fnt = font_body_bold
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            elif i == 1:  # Count Target
                val_str = str(int(r["target"])) if r.get("target") else ""
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            elif i in (2, 3, 4, 5, 6, 7):
                key = ["ss", "sv", "dg", "ds", "total", "avg"][i - 2]
                val = r[key]
                if key == "dg" and val >= 1000:
                    val_str = f"{val:,}" if r["branch"] == "E COMM" else str(val)
                else:
                    val_str = str(val)
                bbox = d.textbbox((0, 0), val_str, font=fnt)
                tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2

            ty = y + (row_h_t2 - (bbox[3] - bbox[1])) // 2 - 1
            d.text((tx, ty), val_str, fill="#000000", font=fnt)
        y += row_h_t2

    # Total Row Table 2
    total_h_t2 = 38
    d.rectangle([margin_x, y, margin_x + content_w, y + total_h_t2], fill=TOTAL_YELLOW, outline=BORDER_COLOR, width=2)
    tot_vals_t2 = [
        "Total",
        str(int(table2_total["target"])),
        str(table2_total["ss"]),
        str(table2_total["sv"]),
        str(table2_total["dg"]),
        str(table2_total["ds"]),
        str(table2_total["total"]),
        str(table2_total["avg"]),
    ]
    for i, val_str in enumerate(tot_vals_t2):
        cx1 = cols_t2_x[i]
        cx2 = cols_t2_x[i + 1]
        d.line([(cx2, y), (cx2, y + total_h_t2)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), val_str, font=font_total)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (total_h_t2 - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), val_str, fill="#000000", font=font_total)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(str(output_path), "PNG", dpi=(200, 200))
    return output_path


def render_closing_report_image(
    target_date: date,
    table1_rows: List[Dict[str, Any]],
    table1_total: Dict[str, Any],
    weight_rows: List[Dict[str, Any]],
    weight_total: Dict[str, Any],
    scheme_rows: List[Dict[str, Any]],
    scheme_total: Dict[str, Any],
    output_path: Path,
) -> Path:
    """
    Render Photo 2: Scheme Rejoining Report (Main Rejoining, Closed Weight, and Closed Count split).
    """
    target_date_str = target_date.strftime("%d/%m/%Y")
    img_w = 1240
    img_h = 1150
    im = Image.new("RGB", (img_w, img_h), "#ffffff")
    d = ImageDraw.Draw(im)

    font_title = _get_font(21, bold=True)
    font_header = _get_font(18, bold=True)
    font_body = _get_font(18, bold=False)
    font_body_bold = _get_font(18, bold=True)
    font_total = _get_font(19, bold=True)

    BORDER_COLOR = "#000000"
    NAVY_BANNER = "#004b87"
    HEADER_BLUE = "#b4c6e7"
    TOTAL_YELLOW = "#ffff00"

    margin_x = 20
    content_w = img_w - (margin_x * 2)

    # -------------------------------------------------------------
    # TOP TITLE BAR
    # -------------------------------------------------------------
    y = 20
    banner_h = 42
    d.rectangle([margin_x, y, margin_x + content_w, y + banner_h], fill=NAVY_BANNER, outline=BORDER_COLOR, width=1)

    t_left = "SCHEME REJOINING REPORT AS ON"
    bbox_l = d.textbbox((0, 0), t_left, font=font_title)
    d.text((margin_x + 12, y + (banner_h - (bbox_l[3] - bbox_l[1])) // 2 - 2), t_left, fill="#ffffff", font=font_title)

    bbox_r = d.textbbox((0, 0), target_date_str, font=font_title)
    d.text((margin_x + content_w - (bbox_r[2] - bbox_r[0]) - 16, y + (banner_h - (bbox_r[3] - bbox_r[1])) // 2 - 2), target_date_str, fill="#ffffff", font=font_title)
    y += banner_h

    # -------------------------------------------------------------
    # TABLE 1: MAIN REJOINING TABLE
    # -------------------------------------------------------------
    cols_t1_widths = [190, 200, 200, 200, 200, 210]
    cols_t1_x = [margin_x]
    for w in cols_t1_widths:
        cols_t1_x.append(cols_t1_x[-1] + w)

    headers_t1 = ["SHOWROOM", "CLOSED", "REJOIN SS", "REJOIN SV", "TOTAL REJOIN", "REJOIN %"]
    row_h_t1 = 37

    # Header Row
    d.rectangle([margin_x, y, margin_x + content_w, y + row_h_t1], fill=HEADER_BLUE, outline=BORDER_COLOR, width=1)
    for i, h in enumerate(headers_t1):
        cx1 = cols_t1_x[i]
        cx2 = cols_t1_x[i + 1]
        d.line([(cx2, y), (cx2, y + row_h_t1)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), h, font=font_header)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (row_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), h, fill="#000000", font=font_header)
    y += row_h_t1

    # Data Rows
    for r in table1_rows:
        d.rectangle([margin_x, y, margin_x + content_w, y + row_h_t1], fill="#ffffff", outline=BORDER_COLOR, width=1)
        vals = [
            r["showroom"],
            str(r["closed"]),
            str(r["rejoin_ss"]),
            str(r["rejoin_sv"]),
            str(r["total_rejoin"]),
            f"{r['rejoin_pct']:.2f}%",
        ]
        for i, val_str in enumerate(vals):
            cx1 = cols_t1_x[i]
            cx2 = cols_t1_x[i + 1]
            d.line([(cx2, y), (cx2, y + row_h_t1)], fill=BORDER_COLOR, width=1)
            fnt = font_body_bold if i == 0 else font_body
            bbox = d.textbbox((0, 0), val_str, font=fnt)
            tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            ty = y + (row_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
            d.text((tx, ty), val_str, fill="#000000", font=fnt)
        y += row_h_t1

    # Total Row Table 1
    total_h_t1 = 38
    d.rectangle([margin_x, y, margin_x + content_w, y + total_h_t1], fill=TOTAL_YELLOW, outline=BORDER_COLOR, width=2)
    tot_vals_t1 = [
        "TOTAL",
        str(table1_total["closed"]),
        str(table1_total["rejoin_ss"]),
        str(table1_total["rejoin_sv"]),
        str(table1_total["total_rejoin"]),
        f"{table1_total['rejoin_pct']:.2f}%",
    ]
    for i, val_str in enumerate(tot_vals_t1):
        cx1 = cols_t1_x[i]
        cx2 = cols_t1_x[i + 1]
        d.line([(cx2, y), (cx2, y + total_h_t1)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), val_str, font=font_total)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (total_h_t1 - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), val_str, fill="#000000", font=font_total)
    y += total_h_t1

    # -------------------------------------------------------------
    # BOTTOM SPLIT: SECTION 2 (CLOSED WEIGHT) & SECTION 3 (CLOSED COUNT)
    # -------------------------------------------------------------
    split_w_left = 595
    split_w_right = 605
    x_left = margin_x
    x_right = margin_x + split_w_left

    banner_sub_h = 36
    # Left Banner
    d.rectangle([x_left, y, x_left + split_w_left, y + banner_sub_h], fill=NAVY_BANNER, outline=BORDER_COLOR, width=1)
    bw_txt = "CLOSED WEIGHT"
    bbox = d.textbbox((0, 0), bw_txt, font=font_header)
    d.text((x_left + (split_w_left - (bbox[2] - bbox[0])) // 2, y + (banner_sub_h - (bbox[3] - bbox[1])) // 2 - 1), bw_txt, fill="#ffffff", font=font_header)

    # Right Banner
    d.rectangle([x_right, y, x_right + split_w_right, y + banner_sub_h], fill=NAVY_BANNER, outline=BORDER_COLOR, width=1)
    bc_txt = "CLOSED COUNT"
    bbox = d.textbbox((0, 0), bc_txt, font=font_header)
    d.text((x_right + (split_w_right - (bbox[2] - bbox[0])) // 2, y + (banner_sub_h - (bbox[3] - bbox[1])) // 2 - 1), bc_txt, fill="#ffffff", font=font_header)
    y += banner_sub_h

    # Subheaders
    row_sub_hdr = 34
    cols_left_x = [x_left, x_left + 190, x_left + 190 + 175, x_left + split_w_left]
    cols_right_x = [x_right, x_right + 205, x_right + 205 + 200, x_right + split_w_right]

    d.rectangle([x_left, y, x_left + split_w_left, y + row_sub_hdr], fill=HEADER_BLUE, outline=BORDER_COLOR, width=1)
    left_hdrs = ["SHOWROOM", "COUNT", "WEIGHT"]
    for i, h in enumerate(left_hdrs):
        cx1, cx2 = cols_left_x[i], cols_left_x[i + 1]
        d.line([(cx2, y), (cx2, y + row_sub_hdr)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), h, font=font_header)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (row_sub_hdr - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), h, fill="#000000", font=font_header)

    d.rectangle([x_right, y, x_right + split_w_right, y + row_sub_hdr], fill=HEADER_BLUE, outline=BORDER_COLOR, width=1)
    right_hdrs = ["SHOWROOM", "SS", "SV"]
    for i, h in enumerate(right_hdrs):
        cx1, cx2 = cols_right_x[i], cols_right_x[i + 1]
        d.line([(cx2, y), (cx2, y + row_sub_hdr)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), h, font=font_header)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (row_sub_hdr - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), h, fill="#000000", font=font_header)
    y += row_sub_hdr

    row_split_h = 36
    num_rows = len(weight_rows)
    for r_idx in range(num_rows):
        rw = weight_rows[r_idx]
        rc = scheme_rows[r_idx]

        # Left Row
        d.rectangle([x_left, y, x_left + split_w_left, y + row_split_h], fill="#ffffff", outline=BORDER_COLOR, width=1)
        left_vals = [rw["showroom"], str(rw["count"]), f"{rw['weight']:.2f}" if rw["weight"] > 1 else str(rw["weight"])]
        for i, val_str in enumerate(left_vals):
            cx1, cx2 = cols_left_x[i], cols_left_x[i + 1]
            d.line([(cx2, y), (cx2, y + row_split_h)], fill=BORDER_COLOR, width=1)
            fnt = font_body_bold if i == 0 else font_body
            bbox = d.textbbox((0, 0), val_str, font=fnt)
            tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            ty = y + (row_split_h - (bbox[3] - bbox[1])) // 2 - 1
            d.text((tx, ty), val_str, fill="#000000", font=fnt)

        # Right Row
        d.rectangle([x_right, y, x_right + split_w_right, y + row_split_h], fill="#ffffff", outline=BORDER_COLOR, width=1)
        right_vals = [rc["showroom"], str(rc["ss"]), str(rc["sv"]) if rc["sv"] > 0 else ""]
        for i, val_str in enumerate(right_vals):
            cx1, cx2 = cols_right_x[i], cols_right_x[i + 1]
            d.line([(cx2, y), (cx2, y + row_split_h)], fill=BORDER_COLOR, width=1)
            fnt = font_body_bold if i == 0 else font_body
            bbox = d.textbbox((0, 0), val_str, font=fnt)
            tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
            ty = y + (row_split_h - (bbox[3] - bbox[1])) // 2 - 1
            d.text((tx, ty), val_str, fill="#000000", font=fnt)

        y += row_split_h

    # Total Row for Bottom Split
    total_split_h = 38
    d.rectangle([x_left, y, x_left + split_w_left, y + total_split_h], fill=TOTAL_YELLOW, outline=BORDER_COLOR, width=2)
    left_tot_vals = ["TOTAL", str(weight_total["count"]), f"{weight_total['weight']:.2f}"]
    for i, val_str in enumerate(left_tot_vals):
        cx1, cx2 = cols_left_x[i], cols_left_x[i + 1]
        d.line([(cx2, y), (cx2, y + total_split_h)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), val_str, font=font_total)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (total_split_h - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), val_str, fill="#000000", font=font_total)

    d.rectangle([x_right, y, x_right + split_w_right, y + total_split_h], fill=TOTAL_YELLOW, outline=BORDER_COLOR, width=2)
    right_tot_vals = ["TOTAL", str(scheme_total["ss"]), str(scheme_total["sv"])]
    for i, val_str in enumerate(right_tot_vals):
        cx1, cx2 = cols_right_x[i], cols_right_x[i + 1]
        d.line([(cx2, y), (cx2, y + total_split_h)], fill=BORDER_COLOR, width=1)
        bbox = d.textbbox((0, 0), val_str, font=font_total)
        tx = cx1 + (cx2 - cx1 - (bbox[2] - bbox[0])) // 2
        ty = y + (total_split_h - (bbox[3] - bbox[1])) // 2 - 1
        d.text((tx, ty), val_str, fill="#000000", font=font_total)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(str(output_path), "PNG", dpi=(200, 200))
    return output_path

