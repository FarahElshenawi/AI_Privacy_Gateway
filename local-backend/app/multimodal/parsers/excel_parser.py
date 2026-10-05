"""Excel parser — extracts cells from all sheets using openpyxl."""
from openpyxl import load_workbook


class ExcelParser:
    def parse(self, file_path: str) -> dict:
        """Extract text values from an Excel workbook, sheet by sheet.

        Only string cells are extracted — numbers, formulas, and empty
        cells are skipped. The reconstructor only updates string cells,
        preserving formulas and numeric values.

        Returns: {'sheets': [{'name': str, 'cells': [{'coord': str, 'value': str}, ...]}, ...]}
        """
        wb = load_workbook(file_path, data_only=True)
        sheets = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            cells = []
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is not None and isinstance(cell.value, str):
                        cells.append({
                            "coord": cell.coordinate,
                            "value": cell.value,
                        })
            sheets.append({"name": sheet_name, "cells": cells})
        wb.close()
        return {"sheets": sheets}
