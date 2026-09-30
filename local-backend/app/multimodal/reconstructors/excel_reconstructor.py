"""Excel reconstructor — in-place replacement in string cells.

Numbers, dates, booleans and formatting are untouched. Formula cells are
also processed (PII can sit in string literals inside a formula).

Known limit: openpyxl drops some embedded objects on save (charts, images,
pivot caches). Check output if the source workbook relies on them.
"""
from openpyxl import load_workbook

from app.pipeline.masking import apply_pairs


class ExcelReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original .xlsx path)")

        wb = load_workbook(original_path)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value:
                            masked, count = apply_pairs(cell.value, pairs)
                            if count:
                                cell.value = masked
            props = wb.properties
            props.creator = ""
            props.lastModifiedBy = ""
            wb.save(output_path)
        finally:
            wb.close()
        return output_path
