import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["investyo_mcp_server.py", "--transport", "stdio"],
    )

    print(f"Connecting to MCP server using command: {server_params.command} {' '.join(server_params.args)}")
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Successfully initialized session with MCP server.")
                
                tools_response = await session.list_tools()
                tools = tools_response.tools
                print(f"Found {len(tools)} tools.")
                
                target_tool = "get_regime_status"
                if any(tool.name == target_tool for tool in tools):
                    print(f"Found target tool: {target_tool}")
                else:
                    print(f"Tool '{target_tool}' not found!", file=sys.stderr)
                    sys.exit(1)

                print(f"Calling tool '{target_tool}'...")
                try:
                    result = await session.call_tool(target_tool, arguments={})
                except Exception as e:
                    print(f"Failed to call tool: {e}", file=sys.stderr)
                    sys.exit(1)
                
                if getattr(result, "isError", False):
                    print("Tool returned an error status:", file=sys.stderr)
                else:
                    print("Tool call result:")
                    
                for content in result.content:
                    if content.type == "text":
                        print(content.text)
                    else:
                        print(content)
                        
    except Exception as e:
        print(f"MCP server connection or initialization failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nProcess interrupted by user.", file=sys.stderr)
        sys.exit(0)
