"""Validate saved outputs without claiming browser visual inspection."""
import json
from pathlib import Path

import nbformat

records = {}
for path in [Path('notebooks/spx_forecast.ipynb'), Path('notebooks/spx_forecast.executed.ipynb')]:
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    code = [cell for cell in notebook.cells if cell.cell_type == 'code']
    errors = [out for cell in code for out in cell.get('outputs', []) if out.output_type == 'error']
    assert not errors, errors
    figures = sum('image/png' in out.get('data', {}) for cell in code for out in cell.get('outputs', []))
    if 'executed' in path.name:
        assert all(cell.execution_count is not None for cell in code)
        assert figures == 7, figures
    records[str(path)] = dict(cells=len(notebook.cells), code_cells=len(code),
                              executed_code_cells=sum(cell.execution_count is not None for cell in code),
                              embedded_png_outputs=figures, error_outputs=len(errors))
Path('artifacts/notebook_validation.json').write_text(json.dumps(records, indent=2))
print(json.dumps(records, indent=2))
