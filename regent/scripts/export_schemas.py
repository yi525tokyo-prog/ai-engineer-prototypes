"""Export the provider/tool contracts as JSON Schema (packages/schemas)."""

import json
from pathlib import Path

from regent.schemas import (Critique, OperationSpec, RouteEstimates, RouteProposal, Sensitivity, ToolResult,
                            VerificationResult)

OUT = Path(__file__).resolve().parents[1] / "packages" / "schemas"

for model in (RouteProposal, RouteEstimates, Sensitivity, OperationSpec, ToolResult, VerificationResult, Critique):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{model.__name__}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
    print("wrote", model.__name__)
