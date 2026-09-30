"""Excel reconstructor — in-place cell value replacement.

Opens the ORIGINAL .xlsx and replaces PII text in string cells.
Preserves formulas, formatting, charts, merged cells, conditional formatting.
"""
from openpyxl import load_workbook


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
                    if cell.value.startswith("="):
                        continue  # Don't touch formulas
                    for original, replacement in pairs:
                        if original in cell.value:
                            cell.value = cell.value.replace(original, replacement)

        wb.save(output_path)
        wb.close()
        return output_path
