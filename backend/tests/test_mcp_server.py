"""MCP 服务器冒烟测试：注册完整性 + 内存内往返调用。"""

import anyio

from mcp_server.server import mcp

from app.ai.tools import TOOL_SPECS


def test_all_tools_registered():
    async def check():
        return await mcp.list_tools()

    tools = anyio.run(check)
    assert len(tools) == len(TOOL_SPECS)

    by_name = {t.name: t for t in tools}
    for spec in TOOL_SPECS:
        tool = by_name[spec["name"]]
        assert tool.description == spec["description"]
        # 权威 spec 中的必填参数必须存在于按签名推断出的
        # schema 中
        for req in spec["input_schema"].get("required", []):
            assert req in tool.inputSchema.get("properties", {}), (
                f"{spec['name']}: missing {req}"
            )


def test_server_name():
    assert mcp.name == "custom-trader"


def test_in_memory_call_tool():
    """通过真实 MCP 客户端会话完成往返调用（带交易门控的工具）。"""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def check():
        async with create_connected_server_and_client_session(
            mcp._mcp_server
        ) as session:
            result = await session.call_tool(
                "place_order",
                {"vt_symbol": "IF2509.CFFEX", "direction": "LONG", "volume": 1},
            )
            return result.content[0].text

    output = anyio.run(check)
    # 交易门控在任何 HTTP 调用之前触发 -> 无需后端即可工作
    assert "AI 交易未启用" in output
