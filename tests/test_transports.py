"""The server as an MCP host starts it: a child process over stdio, and `--http` serving streamable HTTP on this machine.
No request reaches the API: PATENTREF_BASE points at a closed local port and no key is set.

    python -m pytest -q tests/test_transports.py
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.fastmcp")   # the MCP SDK (a skip where it is not installed)
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patentref_mcp import __version__  # noqa: E402

TOOLS = {"check_patent_status", "search_patents", "similar_patents", "get_claim_tree", "get_patent_brief", "rep_required",
         "fee_schedule", "route_windows", "pph_routes", "ai_use_statement_required", "grace_period", "rules_changelog",
         "rules_index", "build_facts", "search_disclosures"}


def child_env():
    env = {k: v for k, v in os.environ.items() if k != "PATENTREF_API_KEY"}
    env.update(PATENTREF_BASE="http://127.0.0.1:9", PYTHONPATH=str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""))
    return env


async def drive(session):
    init = await session.initialize()
    assert init.serverInfo.name == "patentref" and init.serverInfo.version == __version__
    listed = (await session.list_tools()).tools
    assert {t.name for t in listed} == TOOLS
    assert all(t.annotations and t.annotations.readOnlyHint for t in listed)
    res = await session.call_tool("get_patent_brief", {"patent_id": "10034028"})
    text = " ".join(getattr(c, "text", "") or "" for c in res.content)
    assert res.isError and "PATENTREF_API_KEY is not set" in text


def test_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "patentref_mcp.server"], env=child_env())

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await drive(session)

    asyncio.run(run())


def test_http_serves_on_this_machine():
    streamable = pytest.importorskip("mcp.client.streamable_http")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([sys.executable, "-m", "patentref_mcp.server", "--http", "--port", str(port)], env=child_env(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.25)

        # streamable_http_client in newer SDKs, streamablehttp_client in older ones; both yield (read, write, session id)
        client = getattr(streamable, "streamable_http_client", None) or streamable.streamablehttp_client

        async def run():
            async with client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await drive(session)

        asyncio.run(run())
    finally:
        srv.terminate()
        srv.wait(20)


def test_the_command_line():
    out = subprocess.run([sys.executable, "-m", "patentref_mcp.server", "--version"], capture_output=True, text=True,
                         env=child_env(), timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == f"patentref-mcp {__version__}"
    h = subprocess.run([sys.executable, "-m", "patentref_mcp.server", "--help"], capture_output=True, text=True,
                       env=child_env(), timeout=120)
    assert h.returncode == 0 and "usage: patentref-mcp" in h.stdout and "--http" in h.stdout and "this machine" in h.stdout
