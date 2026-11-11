"""The 15 tools of patentref-mcp: their names and arguments as in 0.4.0, their descriptions and annotations, the request
each one sends (httpx is replaced by a fake: no network), the errors it passes on, and the files that state the version.

    python -m pytest -q
"""
import ast
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

ToolError = pytest.importorskip("mcp.server.fastmcp.exceptions").ToolError   # the MCP SDK (a skip where it is not installed)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from patentref_mcp import __version__, server  # noqa: E402

# The tools and their arguments as patentref-mcp 0.4.0 listed them (tools/list of the published 0.4.0 wheel):
# name -> (argument names in order, required arguments, defaults). Renaming or dropping any of them breaks users.
INTERFACE_0_4_0 = {
    "check_patent_status": (["patent_id"], ["patent_id"], {}),
    "search_patents": (["q", "f", "s", "size", "after"], ["q"], {"f": None, "s": None, "size": 25, "after": None}),
    "similar_patents": (["text", "n"], ["text"], {"n": 20}),
    "get_claim_tree": (["patent_id"], ["patent_id"], {}),
    "get_patent_brief": (["patent_id", "max_tokens"], ["patent_id"], {"max_tokens": 2000}),
    "rep_required": (["office", "act"], ["office"], {"act": None}),
    "fee_schedule": (["office", "as_of"], ["office"], {"as_of": None}),
    "route_windows": (["office"], ["office"], {}),
    "pph_routes": (["from_office", "to_office"], ["from_office", "to_office"], {}),
    "ai_use_statement_required": (["office"], ["office"], {}),
    "grace_period": (["office"], ["office"], {}),
    "rules_changelog": (["since"], [], {"since": None}),
    "rules_index": ([], [], {}),
    "build_facts": ([], [], {}),
    "search_disclosures": (["q"], ["q"], {}),
}
KEYLESS_TOOLS = {"build_facts", "search_disclosures"}
OFFICES = ["US", "EP", "CN", "JP", "KR", "AU", "PCT"]
# The project's wording rules for every word an agent or a reader is given: none of these words (whole words, any case,
# with their endings), no dashes of the em or en kind, plain ASCII.
BANNED = re.compile(
    r"\b(risk\w*|clear\w*|safe\w*|free to use|freedom to operate|invalid\w*|valid\w*|scope\w*|breadth\w*|strength\w*|"
    r"broad\w*|narrow\w*|infring\w*|no other|first\w*|cover\w*|reads? on|enforceab\w*|unenforceab\w*|the only|"
    r"we know of)\b", re.I)
VERBS = ("Get", "Search", "Rank", "List", "Describe")


def tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def wording_problems(text):
    out = [m.group(0) for m in BANNED.finditer(text)]
    if chr(0x2014) in text or chr(0x2013) in text:
        out.append("an em or en dash")
    if re.search(r"[^\x00-\x7f]", text):
        out.append("a character outside ASCII")
    return out


# ------------------------------------------------------------------ names, arguments, descriptions, annotations

def test_the_tool_and_argument_names_are_those_of_0_4_0():
    listed = tools()
    assert set(listed) == set(INTERFACE_0_4_0)
    for name, (args, required, defaults) in INTERFACE_0_4_0.items():
        schema = listed[name].inputSchema
        props = schema.get("properties", {})
        assert list(props) == args, name
        assert sorted(schema.get("required", [])) == sorted(required), name
        assert {k: v["default"] for k, v in props.items() if "default" in v} == defaults, name


def test_every_tool_has_a_description_that_starts_with_a_verb_and_names_its_behavior():
    for name, t in tools().items():
        d = t.description or ""
        assert d.strip() and "\n" not in d, name
        assert d.split()[0] in VERBS, (name, d.split()[0])
        assert "Read only." in d, name
        if name in KEYLESS_TOOLS:
            assert "Sends no API key" in d, name
        else:
            assert "Needs PATENTREF_API_KEY" in d and "1,000 a month" in d and "429" in d, name


def test_every_description_names_a_sibling_to_use_instead():
    names = set(INTERFACE_0_4_0)
    for name, t in tools().items():
        others = {n for n in names - {name} if re.search(rf"\b{n}\b", t.description)}
        assert others, name


def test_every_argument_has_a_description_and_the_office_codes_are_listed():
    for name, t in tools().items():
        for arg, prop in t.inputSchema.get("properties", {}).items():
            assert prop.get("description"), (name, arg)
            if arg in ("office", "from_office", "to_office"):
                assert prop.get("enum") == OFFICES, (name, arg)
                assert all(code in prop["description"] for code in OFFICES), (name, arg)


def test_no_description_title_or_argument_text_uses_a_barred_word():
    for name, t in tools().items():
        texts = [t.description, t.title or ""] + [p.get("description", "") for p in t.inputSchema.get("properties", {}).values()]
        for text in texts:
            assert not wording_problems(text), (name, wording_problems(text), text[:80])
    assert not wording_problems(server.INSTRUCTIONS)


def test_every_tool_is_annotated_read_only_idempotent_and_open_world_with_a_title():
    for name, t in tools().items():
        a = t.annotations
        assert a is not None, name
        assert a.readOnlyHint is True and a.idempotentHint is True and a.openWorldHint is True, name
        assert a.destructiveHint is False, name
        assert t.title, name


# ------------------------------------------------------------------ the request each tool sends

class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = {"error": False} if body is None else body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def calls(monkeypatch):
    seen = []
    answers = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return answers.pop(0) if answers else FakeResponse()

    monkeypatch.setenv("PATENTREF_API_KEY", "test-key-not-real")
    monkeypatch.setattr(server.httpx, "get", fake_get)
    return seen, answers


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("PATENTREF_API_KEY", raising=False)


def call(name, args):
    """A tool call as an MCP host makes it: the arguments go through the input schema first."""
    return asyncio.run(server.mcp.call_tool(name, args))


REQUESTS = {
    "check_patent_status": ({"patent_id": "us10034028"}, "/api/v1/lapse/status/10034028/", None),
    "get_claim_tree": ({"patent_id": "US10034028"}, "/api/v1/structure/claims/10034028/", None),
    "get_patent_brief": ({"patent_id": "D872010"}, "/api/v1/structure/brief/D872010/", {"max_tokens": 2000}),
    "similar_patents": ({"text": "a laser"}, "/api/v1/lapse/similar", {"text": "a laser", "n": 20}),
    "rep_required": ({"office": "ep", "act": "prosecute"}, "/api/v1/rules/rep_required/", {"office": "EP", "act": "prosecute"}),
    "fee_schedule": ({"office": "JP"}, "/api/v1/rules/fee_schedule/", {"office": "JP"}),
    "route_windows": ({"office": " kr "}, "/api/v1/rules/route_windows/", {"office": "KR"}),
    "pph_routes": ({"from_office": "ep", "to_office": "us"}, "/api/v1/rules/pph_routes/",
                   {"from_office": "EP", "to_office": "US"}),
    "ai_use_statement_required": ({"office": "US"}, "/api/v1/rules/ai_use_statement_required/", {"office": "US"}),
    "grace_period": ({"office": "pct"}, "/api/v1/rules/grace_period/", {"office": "PCT"}),
    "rules_changelog": ({"since": "0.1.0"}, "/api/v1/rules/changelog/", {"since": "0.1.0"}),
    "rules_index": ({}, "/api/v1/rules/", {}),
    "build_facts": ({}, "/api/v1/meta/", None),
    "search_disclosures": ({"q": "  thermal fuse  "}, "/registry/search", {"q": "thermal fuse"}),
}


def test_every_tool_is_tested_for_its_request():
    assert set(REQUESTS) | {"search_patents"} == set(INTERFACE_0_4_0)


@pytest.mark.parametrize("name", sorted(REQUESTS))
def test_each_tool_sends_one_get_to_its_path(calls, name):
    seen, _ = calls
    args, path, params = REQUESTS[name]
    call(name, args)
    assert len(seen) == 1
    assert seen[0]["url"] == server.BASE + path and seen[0]["params"] == params, seen[0]
    assert seen[0]["headers"]["User-Agent"] == f"patentref-mcp/{__version__}"
    assert ("X-Api-Key" in seen[0]["headers"]) == (name not in KEYLESS_TOOLS)
    if name not in KEYLESS_TOOLS:
        assert seen[0]["headers"]["X-Api-Key"] == "test-key-not-real"


def test_search_patents_sends_the_v1_grammar_as_json(calls):
    seen, _ = calls
    q = {"_and": [{"_gte": {"patent_date": "2015-01-01"}}, {"cpc_current.cpc_group_id": "H01S3/00"}]}
    call("search_patents", {"q": q, "f": ["patent_id", "lapse"], "s": [{"patent_date": "desc"}], "size": 5000,
                            "after": ["2020-01-01", "10000000"]})
    p = seen[0]["params"]
    assert seen[0]["url"] == server.BASE + "/api/v1/patent/"
    assert json.loads(p["q"]) == q and json.loads(p["f"]) == ["patent_id", "lapse"]
    assert json.loads(p["s"]) == [{"patent_date": "desc"}]
    assert json.loads(p["o"]) == {"size": 1000, "after": ["2020-01-01", "10000000"]}
    call("search_patents", {"q": {"patent_title": "laser"}})
    assert seen[1]["params"] == {"q": '{"patent_title": "laser"}', "o": '{"size": 25}'}


def test_values_outside_their_range_are_moved_to_the_nearest_limit(calls):
    seen, _ = calls
    call("search_patents", {"q": {"patent_id": "1"}, "size": 0})
    call("similar_patents", {"text": "x" * 5000, "n": 99})
    call("similar_patents", {"text": "x", "n": 0})
    call("get_patent_brief", {"patent_id": "10034028", "max_tokens": 5})
    call("get_patent_brief", {"patent_id": "10034028", "max_tokens": 999999})
    call("search_disclosures", {"q": "y" * 300})
    assert json.loads(seen[0]["params"]["o"]) == {"size": 1}
    assert len(seen[1]["params"]["text"]) == 4000 and seen[1]["params"]["n"] == 20
    assert seen[2]["params"]["n"] == 1
    assert seen[3]["params"] == {"max_tokens": 300} and seen[4]["params"] == {"max_tokens": 32000}
    assert len(seen[5]["params"]["q"]) == 200


def test_unset_optional_arguments_are_not_sent(calls):
    seen, _ = calls
    call("fee_schedule", {"office": "EP"})
    call("rep_required", {"office": "US"})
    call("rules_changelog", {})
    assert [c["params"] for c in seen] == [{"office": "EP"}, {"office": "US"}, {}]


def test_an_office_outside_the_list_is_refused_before_any_request(calls):
    seen, _ = calls
    for name, args in (("route_windows", {"office": "EPO"}), ("pph_routes", {"from_office": "EP", "to_office": "XX"})):
        with pytest.raises(ToolError):
            call(name, args)
    assert seen == []


def test_the_answer_is_the_api_json_unchanged(calls):
    seen, answers = calls
    body = {"error": False, "rows": [{"office": "EP", "citation_url": "https://example.org/x"}], "rules_version": "0.2.0"}
    answers.append(FakeResponse(200, body))
    out = server.route_windows("EP")
    assert out == body


# ------------------------------------------------------------------ errors

def test_a_keyed_tool_without_a_key_sends_nothing_and_names_the_variable(monkeypatch, no_key):
    seen = []
    monkeypatch.setattr(server.httpx, "get", lambda *a, **k: seen.append(1))
    with pytest.raises(ToolError, match="PATENTREF_API_KEY is not set"):
        call("get_claim_tree", {"patent_id": "10034028"})
    assert seen == []


def test_the_keyless_tools_work_without_a_key(monkeypatch, no_key):
    seen = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.append(headers)
        return FakeResponse(200, {"data_version": "20261027.2"})

    monkeypatch.setattr(server.httpx, "get", fake_get)
    assert server.build_facts()["data_version"] == "20261027.2"
    server.search_disclosures("fuse")
    assert all("X-Api-Key" not in h for h in seen) and len(seen) == 2


@pytest.mark.parametrize("answer, text", [
    (FakeResponse(404, {"error": True}, {"X-Status-Reason-Code": "NOT_FOUND", "X-Status-Reason": "No such patent."}),
     "HTTP 404 NOT_FOUND: No such patent."),
    (FakeResponse(400, {"error": True}, {"X-Status-Reason-Code": "ERR_Q", "X-Status-Reason": "'office' must be one of"}),
     "HTTP 400 ERR_Q: 'office' must be one of"),
    (FakeResponse(429, {"detail": "Request was throttled."}, {"Retry-After": "30"}),
     'HTTP 429 (retry after 30 s): {"detail": "Request was throttled."}'),
    (FakeResponse(403, {"detail": "You do not have permission to perform this action."}),
     'HTTP 403: {"detail": "You do not have permission to perform this action."}'),
    (FakeResponse(200, ValueError("not json")), "the API answered with a non-JSON body"),
])
def test_an_api_error_comes_back_as_the_tool_error(calls, answer, text):
    seen, answers = calls
    answers.append(answer)
    with pytest.raises(ToolError) as e:
        call("get_claim_tree", {"patent_id": "10034028"})
    assert text in str(e.value)
    assert len(seen) == 1   # no retry


def test_a_network_failure_names_the_error_class_only(monkeypatch):
    def down(*a, **k):
        raise server.httpx.ConnectError("connection refused to a host")

    monkeypatch.setenv("PATENTREF_API_KEY", "test-key-not-real")
    monkeypatch.setattr(server.httpx, "get", down)
    with pytest.raises(server.ApiError, match="^request failed: ConnectError$"):
        server.check_patent_status("10034028")


# ------------------------------------------------------------------ versions and the files that state them

def test_the_version_is_the_same_everywhere():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"$', pyproject, re.M).group(1)
    assert version == __version__ == server.USER_AGENT.split("/", 1)[1]
    sj = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert sj["version"] == version and [p["version"] for p in sj["packages"]] == [version]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## ([0-9.]+) \(\d{4}-\d{2}-\d{2}\)$", changelog, re.M).group(1) == version
    init = server.mcp._mcp_server.create_initialization_options()
    assert init.server_name == "patentref" and init.server_version == version


def test_the_registry_entry_lists_the_package_over_stdio():
    sj = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert sj["name"] == "io.github.patentref/patentref-mcp" and sj["title"] == "PatentRef"
    assert len(sj["description"]) <= 100 and not wording_problems(sj["description"])
    assert sj["repository"] == {"url": "https://github.com/patentref/patentref-mcp", "source": "github"}
    (pkg,) = sj["packages"]
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == "patentref-mcp" and pkg["transport"] == {"type": "stdio"}
    env = {e["name"]: e for e in pkg["environmentVariables"]}
    assert env["PATENTREF_API_KEY"]["isRequired"] is True and env["PATENTREF_API_KEY"]["isSecret"] is True


def test_the_readme_lists_every_tool_as_the_server_describes_it():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- mcp-name: io.github.patentref/patentref-mcp -->" in readme
    assert "https://glama.ai/mcp/servers/patentref/patentref-mcp/badges/score.svg" in readme
    lines = [ln for ln in readme.splitlines() if ln.startswith("- **")]
    listed = {re.match(r"- \*\*(\w+)\*\*: ", ln).group(1): ln for ln in lines}
    assert set(listed) == set(INTERFACE_0_4_0) and len(lines) == 15
    for name, ln in listed.items():
        assert ln == f"- **{name}**: {server.summary(getattr(server, name))}", name


def test_the_readme_says_where_the_server_runs():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "The server runs on your own machine." in readme
    assert "over stdio, the default" in readme and "patentref-mcp --http" in readme
    assert "There is no hosted MCP endpoint for this server" in readme
    for cmd in ("pip install patentref-mcp", "patentref-mcp                       # stdio",
                "patentref-mcp --http --port 8787    # streamable HTTP at http://127.0.0.1:8787/mcp"):
        assert cmd in readme


@pytest.mark.parametrize("name", ["README.md", "CHANGELOG.md"])
def test_the_readme_and_the_changelog_use_no_barred_word(name):
    for n, ln in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
        assert not wording_problems(ln), (name, n, wording_problems(ln))


def test_the_package_imports_only_the_standard_library_the_mcp_sdk_httpx_and_pydantic():
    allowed = {"argparse", "asyncio", "json", "logging", "os", "sys", "typing", "httpx", "mcp", "pydantic", "__future__"}
    for p in sorted((ROOT / "patentref_mcp").glob("*.py")):
        names = set()
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        assert names <= allowed, (p.name, sorted(names - allowed))
