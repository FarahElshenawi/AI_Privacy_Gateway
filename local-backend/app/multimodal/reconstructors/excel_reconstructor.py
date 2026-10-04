"""Excel reconstructor — in-place cell value replacement.

Opens the ORIGINAL .xlsx and replaces PII text in string cells.
Preserves formulas, formatting, charts, merged cells, conditional formatting.
"""
from openpyxl import load_workbook

from app.pipeline.masking import apply_pairs


class ExcelReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        """Apply replacement pairs to the original Excel workbook."""
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path")

        wb = load_workbook(original_path)

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    if not isinstance(cell.value, str):
                        continue
                    # Formulas aren't skipped wholesale — a formula whose
                    # entire body is a quoted PII literal (e.g.
                    # ="john.doe@example.com") still leaks that PII when
                    # the sheet is opened, same as a plain string cell
                    # would. apply_pairs only replaces an exact substring
                    # match, so a real formula like "=A2*2" (no PII text
                    # inside it) is returned unchanged — nothing to skip
                    # for. This also means a cell reference or function
                    # name could in principle be altered if it happened
                    # to exactly match a detected PII string, which is a
                    # low-probability edge case accepted here in favor
                    # of not leaving real PII in quoted formula literals.
                    cell.value, _ = apply_pairs(cell.value, pairs)

        wb.save(output_path)
        wb.close()
        return output_path
