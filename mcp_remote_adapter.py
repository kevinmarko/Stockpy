import asyncio
import sys
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from mcp.server.stdio import stdio_server

# actually, bridging is just reading stdin and forwarding to sse, and reading sse and forwarding to stdout.
