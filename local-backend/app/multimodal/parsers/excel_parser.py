"""Excel parser — extracts every string cell (formulas included) from all sheets."""
from openpyxl import load_workbook


class ExcelParser:
    def parse(self, file_path: str) -> dict:
        """Returns: {'sheets': [{'name': str, 'cells': [{'coord': str, 'value': str}, ...]}, ...]}

        Loaded WITHOUT data_only so formula text is visible: PII can hide in
        string literals inside formulas (e.g. ="john@x.com"), and cached
        formula results are dropped when openpyxl re-saves a workbook, which
        would otherwise blind the residual scan.
        """
        wb = load_workbook(file_path)
        try:
            sheets = []
            for ws in wb.worksheets:
                cells = [
                    {"coord": cell.coordinate, "value": cell.value}
                    for row in ws.iter_rows()
                    for cell in row
                    if isinstance(cell.value, str) and cell.value
                ]
                sheets.append({"name": ws.title, "cells": cells})
            return {"sheets": sheets}
        finally:
            wb.close()
