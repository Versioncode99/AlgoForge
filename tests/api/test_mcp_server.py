from __future__ import annotations

from forge_api.main import create_app
from forge_api.mcp_server import build_server


def test_mcp_is_read_only_by_default(tmp_path):
    app = create_app(tmp_path / "mcp.db")
    server = build_server(app.state.actions)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == {
        "engine_status",
        "list_families",
        "list_strategies",
        "list_templates",
        "read_research",
    }
    assert all(tool.annotations.read_only_hint for tool in tools.values())


def test_mcp_write_mode_exposes_the_same_action_registry(tmp_path):
    app = create_app(tmp_path / "mcp-write.db")
    server = build_server(app.state.actions, allow_write=True)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == set(app.state.actions.names())
    assert tools["create_family"].annotations.read_only_hint is False
    assert tools["create_family"].annotations.destructive_hint is False
