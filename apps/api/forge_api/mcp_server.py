"""Local stdio MCP facade over AlgoForge's shared action registry.

Read-only is the default. Pass ``--write`` explicitly to expose actions that
create files, start work, or otherwise mutate the research workspace. The MCP
process writes no diagnostics to stdout because stdout belongs to the protocol.
"""

from __future__ import annotations

import argparse
import inspect
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from forge_api.actions import Actions


def _annotation(field: dict[str, Any], *, optional: bool) -> Any:
    base: Any = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list[Any],
        "object": dict[str, Any],
    }.get(str(field.get("type")), Any)
    if optional:
        base = base | None
    return Annotated[base, Field(description=str(field.get("description", "")))]


def build_server(actions: Actions, *, allow_write: bool = False) -> MCPServer[Any]:
    server = MCPServer(
        "AlgoForge",
        instructions=(
            "Paper-only quantitative research tools. Evidence labels are boundaries: "
            "never describe SYNTHETIC or DEVELOPMENT_IN_SAMPLE results as OOS proof, and "
            "never imply that any tool can place a live order."
        ),
    )

    for schema in actions.schemas():
        if schema["mutating"] and not allow_write:
            continue
        name = str(schema["name"])
        description = str(schema["description"])

        async def invoke(_action: str = name, **arguments: Any) -> dict[str, Any]:
            return actions.call(_action, arguments)

        # FastMCP derives the input schema from the signature. Replace the
        # generic **arguments signature with the action registry's declared
        # fields, preserving required/optional status and primitive types.
        parameters = []
        for key, field in schema["parameters"]["properties"].items():
            default: Any = inspect.Parameter.empty
            optional = key not in schema["parameters"]["required"]
            if optional:
                default = None
            parameters.append(
                inspect.Parameter(
                    key,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=_annotation(field, optional=optional),
                )
            )
        invoke.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            parameters=parameters, return_annotation=dict[str, Any]
        )
        invoke.__name__ = f"algoforge_{name}"
        server.add_tool(
            invoke,
            name=name,
            description=description,
            structured_output=True,
            annotations=ToolAnnotations(
                read_only_hint=not bool(schema["mutating"]),
                # Taken from the action's own risk tier rather than asserted
                # here. A client uses this to decide whether to ask a person
                # first, so it has to mean the same thing the registry means --
                # and the registry will refuse a non-SAFE action anyway unless
                # the operator has confirmed it.
                destructive_hint=schema.get("risk", "safe") != "safe",
                idempotent_hint=not bool(schema["mutating"]),
                open_world_hint=name == "search_papers",
            ),
        )
    return server


def run() -> None:
    parser = argparse.ArgumentParser(description="AlgoForge local MCP server")
    parser.add_argument(
        "--write", action="store_true", help="Expose mutating actions as well as read-only tools"
    )
    args = parser.parse_args()
    # Importing the app constructs exactly the same shared action surface as the
    # desktop API. It does not start HTTP or the continuous research lifecycle.
    from forge_api.main import app

    build_server(app.state.actions, allow_write=args.write).run(transport="stdio")


if __name__ == "__main__":
    run()
