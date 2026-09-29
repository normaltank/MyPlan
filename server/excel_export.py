"""Standard dual-sheet Excel export (有邮箱 / 无邮箱) shared by college and university deliveries.

Moved out of the retired ``autonomous_agent`` module so the delivery path no
longer imports the old script harvester (and PyMuPDF / Playwright with it).
"""
from __future__ import annotations

import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def _excel_safe_text(value):
    """Drop characters an .xlsx file cannot hold; keep every visible character."""
    if isinstance(value, str):
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def _store_formulas_as_text(workbook) -> None:
    """Harvested text is data: a value like ``=HYPERLINK(...)`` stays literal text."""
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"


def export_dual_sheet_excel(records: list, output_filepath: str) -> dict:
    """Export styled dual-sheet standard Excel file adhering to strict invariants.

    Every value is harvested website text and is written as text: a value
    starting with ``=`` is never stored as a formula, and XML control
    characters (which openpyxl cannot store) are removed instead of failing
    the whole university workbook.
    """
    df = pd.DataFrame(records).map(_excel_safe_text) if records else pd.DataFrame(records)
    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    if df.empty:
        empty_df = pd.DataFrame(columns=columns)
        with pd.ExcelWriter(output_filepath, engine='openpyxl') as writer:
            empty_df.to_excel(writer, sheet_name='教师列表', index=False)
            empty_df.to_excel(writer, sheet_name='无邮箱教师列表', index=False)
        try:
            wb = openpyxl.load_workbook(output_filepath)
            header_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
            header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="1E293B")
            for ws in wb.worksheets:
                ws.views.sheetView[0].showGridLines = True
                for cell in ws[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                for col in ws.columns:
                    col_letter = get_column_letter(col[0].column)
                    ws.column_dimensions[col_letter].width = 16
            wb.save(output_filepath)
        except Exception:
            pass
        return {"total": 0, "with_email": 0, "no_email": 0}

    # Sort consistently without collapsing homonyms
    df = df.sort_values(by=['学校', '学院', '姓名']).reset_index(drop=True)

    df_with_email = df[df['邮箱'].fillna('').astype(str).str.strip() != ""].copy().reset_index(drop=True)
    df_no_email = df[df['邮箱'].fillna('').astype(str).str.strip() == ""].copy().reset_index(drop=True)

    df_with_email['序号'] = range(1, len(df_with_email) + 1)
    df_no_email['序号'] = range(1, len(df_no_email) + 1)

    for c in columns:
        if c not in df_with_email.columns:
            df_with_email[c] = ""
        if c not in df_no_email.columns:
            df_no_email[c] = ""

    df_with_email = df_with_email[columns]
    df_no_email = df_no_email[columns]

    with pd.ExcelWriter(output_filepath, engine='openpyxl') as writer:
        df_with_email.to_excel(writer, sheet_name='教师列表', index=False)
        df_no_email.to_excel(writer, sheet_name='无邮箱教师列表', index=False)

    wb = openpyxl.load_workbook(output_filepath)
    _store_formulas_as_text(wb)  # must persist even if styling below fails

    # Style Excel Sheets with openpyxl
    try:
        header_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="1E293B")
        data_font = Font(name="Microsoft YaHei", size=10, color="334155")
        thin_border = Border(
            left=Side(style='thin', color='E2E8F0'),
            right=Side(style='thin', color='E2E8F0'),
            top=Side(style='thin', color='E2E8F0'),
            bottom=Side(style='thin', color='E2E8F0')
        )

        for ws in wb.worksheets:
            ws.views.sheetView[0].showGridLines = True
            for row_idx, row in enumerate(ws.iter_rows(), start=1):
                for cell in row:
                    cell.border = thin_border
                    if row_idx == 1:
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.alignment = Alignment(horizontal='center', vertical='center')
                    else:
                        cell.font = data_font
                        if cell.column in [1, 2, 3, 5, 6, 7, 8]:
                            cell.alignment = Alignment(horizontal='center', vertical='center')
                        else:
                            cell.alignment = Alignment(horizontal='left', vertical='center')

            for col in ws.columns:
                col_letter = get_column_letter(col[0].column)
                max_len = 0
                for cell in col[:15]:
                    val_str = str(cell.value or '')
                    max_len = max(max_len, len(val_str.encode('gbk', 'ignore')))
                ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 48)
    except Exception:
        pass
    wb.save(output_filepath)

    return {
        "total": len(df),
        "with_email": len(df_with_email),
        "no_email": len(df_no_email)
    }
