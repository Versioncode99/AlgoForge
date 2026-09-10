from __future__ import annotations

from forge_api.main import create_app
from forge_api.mcp_server import build_server


def test_mcp_is_read_only_by_default(tmp_path, monkeypatch):
    # Without this the workspace resolves to the repository's own data/ dir and
    # the test writes into a real one.
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "mcp.db")
    server = build_server(app.state.actions)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == {
        "analysis_trades",
        "audit_log",
        "calculate_risk",
        "current_mode",
        "describe_workspace",
        "engine_status",
        "experiment_lineage",
        "export_strategy",
        "export_workspace",
        "fund_config",
        "fund_operations",
        "fund_performance",
        "fund_state",
        "list_analyses",
        "list_analysis_artifacts",
        "list_blueprints",
        "list_experiments",
        "list_families",
        "list_modes",
        "list_prop_accounts",
        "list_strategies",
        "list_templates",
        "list_workspace_templates",
        "list_workspaces",
        "pending_approvals",
        "prop_account_status",
        "read_research",
        "research_memory",
        "strategy_definition",
        "strategy_dossier",
        "strategy_regimes",
        "strategy_trades",
        "workspace_history",
    }
    assert all(tool.annotations.read_only_hint for tool in tools.values())
    # Reading a layout is not destructive, so nothing here should be flagged as
    # needing a prompt. The workspace verbs that *do* change one are mutating
    # and never reach this list.
    assert not any(tool.annotations.destructive_hint for tool in tools.values())


def test_mcp_write_mode_exposes_the_same_action_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "mcp-write.db")
    server = build_server(app.state.actions, allow_write=True)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == set(app.state.actions.names())
    assert tools["create_family"].annotations.read_only_hint is False
    assert tools["create_family"].annotations.destructive_hint is False
