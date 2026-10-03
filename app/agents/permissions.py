from app.agents.exceptions import ToolPermissionDenied


def require_tool_permission(
    tool_name: str, *, allowed_tools: frozenset[str], permitted_tools: frozenset[str]
) -> None:
    """Require a tool to be granted both by the agent and its execution context."""
    if tool_name not in allowed_tools or tool_name not in permitted_tools:
        raise ToolPermissionDenied(f"Agent is not permitted to use tool: {tool_name}")
