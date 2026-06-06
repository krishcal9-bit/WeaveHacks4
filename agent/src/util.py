from langchain_core.messages import ToolCall


def should_route_to_tool_node(tool_calls: list[ToolCall], fe_tools: list) -> bool:
    """
    Returns True if every tool call targets a backend tool (so we should run the
    ToolNode). Returns False if any tool call is a CopilotKit *frontend* action —
    those are handled by the browser, not the backend graph.
    """
    if not tool_calls:
        return False

    fe_tool_names = {tool.get("name") for tool in fe_tools}

    for tool_call in tool_calls:
        tool_name = (
            tool_call.get("name")
            if isinstance(tool_call, dict)
            else getattr(tool_call, "name", None)
        )
        if tool_name in fe_tool_names:
            return False

    return True
