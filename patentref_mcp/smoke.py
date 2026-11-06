"""End-to-end smoke test over stdio: starts the server as a child process, speaks MCP to it (initialize,
tools/list, check_patent_status, similar_patents) and prints the transcript. The key comes from the
environment and never appears in the output.

  python3 -m patentref_mcp.smoke 10000364 [--http http://127.0.0.1:8787/mcp]
"""
import asyncio
import json
import os
import sys


def _trim(obj, n=1200):
    s = json.dumps(obj, indent=1, default=str)
    return s if len(s) <= n else s[:n] + f"\n... ({len(s)} chars)"


async def run_stdio(pid):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "patentref_mcp.server"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await _drive(session, pid, "stdio")


async def run_http(pid, url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await _drive(session, pid, "streamable-http " + url)


async def _drive(session, pid, transport):
    init = await session.initialize()
    print(f"[{transport}] initialize -> server {init.serverInfo.name} {init.serverInfo.version}, protocol {init.protocolVersion}")
    tools = await session.list_tools()
    print(f"[{transport}] tools/list -> {[t.name for t in tools.tools]}")
    for name, args in (("check_patent_status", {"patent_id": pid}),
                       ("similar_patents", {"text": "coherent ladar using intra-pixel quadrature detection", "n": 3})):
        res = await session.call_tool(name, args)
        print(f"[{transport}] tools/call {name} {json.dumps(args)} -> isError={res.isError}")
        for c in res.content:
            text = getattr(c, "text", None)
            if text:
                try:
                    print(_trim(json.loads(text)))
                except ValueError:
                    print(text[:1200])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    pid = "10000364"
    url = None
    while argv:
        a = argv.pop(0)
        if a == "--http":
            url = argv.pop(0)
        else:
            pid = a
    if url:
        asyncio.run(run_http(pid, url))
    else:
        asyncio.run(run_stdio(pid))


if __name__ == "__main__":
    main()
