# @regent/schemas

JSON Schema for the contracts every model provider and tool must satisfy, generated from
`regent/schemas.py` (`python scripts/export_schemas.py`). Remote providers are prompted with
`RouteProposal.schema.json`; any other language implementation of a provider or tool can
validate against these files.
