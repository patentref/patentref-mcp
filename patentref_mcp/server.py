"""PatentRef MCP server: the public API as tools, over stdio (default) or streamable HTTP.

  patentref-mcp                       stdio
  patentref-mcp --http --port 8787    streamable HTTP at /mcp

Env: PATENTREF_API_KEY (the X-Api-Key value; never logged), PATENTREF_BASE (default https://patentref.io),
PATENTREF_TIMEOUT (seconds, default 30). The server is a thin client: every tool returns the API's own JSON
and states nothing of its own (PatentRef never-regress item 8).
"""
import argparse
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("PATENTREF_BASE", "https://patentref.io").rstrip("/")
TIMEOUT = float(os.environ.get("PATENTREF_TIMEOUT", "30"))
USER_AGENT = "patentref-mcp/0.4.0"

INSTRUCTIONS = (
    "PatentRef serves the USPTO PatentsView granted-patent corpus with the PatentSearch v1 grammar plus expiry facts. "
    "check_patent_status gives record facts (status, basis, as-of date, data version) and never a legal conclusion; "
    "quote its `plain` sentence rather than paraphrasing it. search_patents takes the v1 q/f/s grammar "
    "(https://patentref.io/llms-full.txt). Every answer carries data_version and as_of; cite them. "
    "get_claim_tree gives one patent's claims as a tree with every span located by offsets in the claim text as published; "
    "quote the claim text at those offsets, and read its flags as notes on the text, never as a conclusion about the claims. "
    "get_patent_brief gives one patent in a fixed order cut to fit max_tokens (identity, status, each independent claim's "
    "elements with their offsets, one line per dependent claim, the claim term keys; truncated and omitted say what the budget "
    "left out); quote its text fields as given and cite a claim or element by its page and anchor (page#anchor). "
    "The rules tools (rep_required, fee_schedule, route_windows, pph_routes, ai_use_statement_required, grace_period, "
    "rules_changelog, rules_index) answer rows of office rules, each with its official source URL and a verbatim quote, plus "
    "rules_version and as_of; quote the row and its citation_url. They take an office, never an application number or a "
    "priority date, and compute no date for an application."
)

mcp = FastMCP("patentref", instructions=INSTRUCTIONS, log_level="WARNING")
logging.getLogger("httpx").setLevel(logging.WARNING)


class ApiError(Exception):
    pass


def _key():
    return os.environ.get("PATENTREF_API_KEY", "").strip()


def _call(path: str, params: dict[str, Any] | None = None, keyed: bool = True) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if keyed:
        key = _key()
        if not key:
            raise ApiError("PATENTREF_API_KEY is not set; get a Free key at https://patentref.io/api/v1/meta/signup/")
        headers["X-Api-Key"] = key
    try:
        r = httpx.get(BASE + path, params=params, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise ApiError(f"request failed: {e.__class__.__name__}") from None
    if r.status_code >= 400:
        reason = r.headers.get("X-Status-Reason") or ""
        code = r.headers.get("X-Status-Reason-Code") or ""
        retry = r.headers.get("Retry-After")
        text = f"HTTP {r.status_code}"
        if code:
            text += f" {code}"
        if reason:
            text += f": {reason}"
        if retry:
            text += f" (retry after {retry} s)"
        if not code and not reason:
            try:
                text += ": " + json.dumps(r.json())[:300]
            except ValueError:
                pass
        raise ApiError(text)
    try:
        return r.json()
    except ValueError:
        raise ApiError("the API answered with a non-JSON body") from None


@mcp.tool()
def check_patent_status(patent_id: str) -> dict[str, Any]:
    """The one-call verdict for one US patent: status (active, expired_term, expired_fee_nonpayment, withdrawn,
    unknown), basis entries, reinstatement_possible with its 37 CFR 1.378 basis, as_of, data_version, the versioned
    permalink, a one-sentence `plain` record statement and next_steps. Record facts only."""
    pid = patent_id.strip().upper()
    pid = pid[2:] if pid.startswith("US") else pid
    return _call(f"/api/v1/lapse/status/{pid}/")


@mcp.tool()
def search_patents(q: dict[str, Any], f: list[str] | None = None, s: list[dict[str, str]] | None = None,
                   size: int = 25, after: list[Any] | None = None) -> dict[str, Any]:
    """Search granted US patents with the PatentSearch v1 grammar: q is one JSON object with one key
    (for example {"patent_title": "laser"} or {"_and": [{"_gte": {"patent_date": "2015-01-01"}}, {"cpc_current.cpc_group_id": "H01S3/00"}]}),
    f the fields to return (add "lapse" for the expiry group: expiry_status, expiry_date_estimated, expiry_basis,
    reinstatement_possible, fee_status, lapse_date), s the sort (for example [{"patent_date": "desc"}]), size up to 1000,
    after the cursor from the previous page's last sort values."""
    params = {"q": json.dumps(q), "o": json.dumps({"size": max(1, min(int(size), 1000)), **({"after": after} if after else {})})}
    if f:
        params["f"] = json.dumps(f)
    if s:
        params["s"] = json.dumps(s)
    return _call("/api/v1/patent/", params)


@mcp.tool()
def similar_patents(text: str, n: int = 20) -> dict[str, Any]:
    """The n (1 to 20) US patents nearest to a title plus abstract by FTS5 bm25 over title and abstract, with
    data_version and as_of. A record of what the corpus contained, not a search opinion."""
    return _call("/api/v1/lapse/similar", {"text": text[:4000], "n": max(1, min(int(n), 20))})


@mcp.tool()
def get_claim_tree(patent_id: str) -> dict[str, Any]:
    """The claims of one US patent as a tree, from the claim text the USPTO published: per claim its number, category,
    dependency, preamble, transition, elements, term mentions with the earlier mention each "the"/"said" phrase was matched
    to, claim references and flags; the relations between those parts; structure_version, data_version, text_data_version
    and as_of. Every part carries start and end offsets into its claim's text (text[start:end] is the part). Flags describe
    the text and the data only. GET /api/v1/structure/claims/<patent_id>/."""
    pid = patent_id.strip().upper()
    pid = pid[2:] if pid.startswith("US") else pid
    return _call(f"/api/v1/structure/claims/{pid}/")


@mcp.tool()
def get_patent_brief(patent_id: str, max_tokens: int = 2000) -> dict[str, Any]:
    """A brief of one US patent that fits max_tokens (300 to 32000, default 2000), in a fixed order: identity and dates,
    the status record with its basis, each independent claim as its elements with start and end offsets into the claim
    text, each dependent claim on one line, and the claim term keys; the first item that does not fit ends the brief,
    and truncated and omitted say what was left out. tokens counts the answer by the stated proxy in tokenizer (UTF-8
    bytes / 3, rounded up), never over max_tokens. Every claim and element carries anchor: page + "#" + anchor opens it
    on the record page. Quotes are the claim text as published. GET /api/v1/structure/brief/<patent_id>/?max_tokens=."""
    pid = patent_id.strip().upper()
    pid = pid[2:] if pid.startswith("US") else pid
    return _call(f"/api/v1/structure/brief/{pid}/", {"max_tokens": max(300, min(int(max_tokens), 32000))})


def _rules(tool_path: str, **params: str | None) -> dict[str, Any]:
    return _call("/api/v1/rules/" + tool_path, {k: v for k, v in params.items() if v})


@mcp.tool()
def rep_required(office: str, act: str | None = None) -> dict[str, Any]:
    """Representation rules of a patent office (office: US, EP, CN, JP, KR, AU, or PCT for the international phase):
    whether and when an applicant must act through a registered agent, each row with its official source and quote.
    act keeps the rows of one act of the vocabulary (rules_index lists it)."""
    return _rules("rep_required/", office=office, act=act)


@mcp.tool()
def fee_schedule(office: str, as_of: str | None = None) -> dict[str, Any]:
    """Official fees of a patent office in force on as_of (YYYY-MM-DD, default today UTC): filing, search, examination,
    grant, renewal and other fees with their currency, each row with its official source and quote."""
    return _rules("fee_schedule/", office=office, as_of=as_of)


@mcp.tool()
def route_windows(office: str) -> dict[str, Any]:
    """Generic windows of a patent office, such as the PCT national phase time limit in months from the priority date.
    Never a last day for a specific application; it takes no application number or date."""
    return _rules("route_windows/", office=office)


@mcp.tool()
def pph_routes(from_office: str, to_office: str) -> dict[str, Any]:
    """Patent Prosecution Highway rows of to_office whose cited source names from_office as a partner office."""
    return _rules("pph_routes/", from_office=from_office, to_office=to_office)


@mcp.tool()
def ai_use_statement_required(office: str) -> dict[str, Any]:
    """Rows on whether a patent office requires a statement on the use of AI. An empty answer means the dataset holds
    no cited rule for that office; nothing follows from its absence."""
    return _rules("ai_use_statement_required/", office=office)


@mcp.tool()
def grace_period(office: str) -> dict[str, Any]:
    """What the cited law of an office says about disclosures before filing (the grace period). Citations only: it does
    not say whether any disclosure falls inside the period."""
    return _rules("grace_period/", office=office)


@mcp.tool()
def rules_changelog(since: str | None = None) -> dict[str, Any]:
    """The rules dataset's releases, newest first, after since (a version x.y.z or a date YYYY-MM-DD)."""
    return _rules("changelog/", since=since)


@mcp.tool()
def rules_index() -> dict[str, Any]:
    """The rules dataset: its version and date, the offices with their row counts, the tools and the act vocabulary."""
    return _rules("")


@mcp.tool()
def build_facts() -> dict[str, Any]:
    """The build in service: data_version, as_of, refreshed_at, snapshot hash, superseded versions, table delta."""
    return _call("/api/v1/meta/", keyed=False)


@mcp.tool()
def search_disclosures(q: str) -> dict[str, Any]:
    """Public records of the PatentRef registry (dated technical disclosures) whose title or abstract match q (web
    search syntax: words, "a phrase", -word, or), newest first, up to 20. Each record carries its permalink
    (https://patentref.io/r/<id>), server_time, bundle_hash and proof_status; the submitter's text is inside
    user_content, as entered. A record shows what was published and when, not a patent or an opinion."""
    return _call("/registry/search", {"q": q.strip()[:200]}, keyed=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="patentref-mcp", description="PatentRef MCP server (stdio by default)")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args(argv)
    if not _key():
        print("patentref-mcp: PATENTREF_API_KEY is not set; keyed tools will answer an error", file=sys.stderr)
    if a.http:
        mcp.settings.host, mcp.settings.port = a.host, a.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
