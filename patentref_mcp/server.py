"""PatentRef MCP server: the public API as tools. It runs on the user's own machine, over stdio (default) or
streamable HTTP served from that machine.

  patentref-mcp                       stdio
  patentref-mcp --http --port 8787    streamable HTTP at http://127.0.0.1:8787/mcp, on this machine

Env: PATENTREF_API_KEY (the X-Api-Key value; never logged), PATENTREF_BASE (default https://patentref.io),
PATENTREF_TIMEOUT (seconds, default 30). The server is a thin client: every tool returns the API's own JSON
and states nothing of its own (PatentRef never-regress item 8).
"""
import argparse
import json
import logging
import os
import sys
from typing import Annotated, Any, Literal, get_args

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator, Field

from . import __version__

BASE = os.environ.get("PATENTREF_BASE", "https://patentref.io").rstrip("/")
TIMEOUT = float(os.environ.get("PATENTREF_TIMEOUT", "30"))
USER_AGENT = "patentref-mcp/0.5.0"

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
    "The S1-D tools (experimental: get_patent_units, get_same_part_links, locate_text, search_units) give the problem a "
    "patent states and the parts its claims name, each quoted with a locator, and the patents whose claims name the same "
    "parts; quote the text at the locator. A stated unit is the patent's own sentence or clause found by a named cue rule; a "
    "names_same_part link says only that both patents' claims name these part words; none_found means the rules found no "
    "such sentence, not that the patent has none. "
    "The rules tools (rep_required, fee_schedule, route_windows, pph_routes, ai_use_statement_required, grace_period, "
    "rules_changelog, rules_index) answer rows of office rules, each with its official source URL and a verbatim quote, plus "
    "rules_version and as_of; quote the row and its citation_url. They take an office, never an application number or a "
    "priority date, and compute no date for an application."
)

# Every tool's description is its docstring as one paragraph, then one of these two notes on how it behaves. The key and
# its allowance are documented at https://patentref.io/llms.txt (a free key: 1,000 requests a month; 400 and 404 count).
KEYED = ("Read only. Needs PATENTREF_API_KEY; each call uses one request of the key's allowance (1,000 a month on a "
         "free key). API errors (400 bad argument, 403 missing or unknown key, 404 not found, 429 over the allowance) "
         "come back as the tool error with the HTTP status and reason, with no retry.")
KEYLESS = ("Read only. Sends no API key, so it uses none of a key's allowance. API errors come back as the tool error with "
           "the HTTP status and reason, with no retry.")
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

mcp = FastMCP("patentref", instructions=INSTRUCTIONS, log_level="WARNING")
if hasattr(getattr(mcp, "_mcp_server", None), "version"):
    mcp._mcp_server.version = __version__   # serverInfo.version: this package's version, not the MCP SDK's
logging.getLogger("httpx").setLevel(logging.WARNING)


def summary(fn) -> str:
    """A tool's docstring as one paragraph: the part of its description that says what it does and when to use it."""
    return " ".join((fn.__doc__ or "").split())


def _tool(title: str, keyed: bool = True):
    """Register a read-only tool under its function's name, with a title and its description."""
    def register(fn):
        return mcp.tool(title=title, description=f"{summary(fn)} {KEYED if keyed else KEYLESS}", annotations=READ_ONLY)(fn)
    return register


OfficeCode = Literal["US", "EP", "CN", "JP", "KR", "AU", "PCT"]
OFFICES = get_args(OfficeCode)


def _upper(v: Any) -> Any:
    return v.strip().upper() if isinstance(v, str) else v


PatentId = Annotated[str, Field(description=(
    "A granted US patent number as PatentsView prints it, for example 10000000, D872010, RE31177 or PP36776. "
    "A US prefix and lower case are accepted; no commas or spaces inside the number."))]
Office = Annotated[OfficeCode, BeforeValidator(_upper), Field(description=(
    "The patent office code: US, EP, CN, JP, KR, AU, or PCT for the international phase. Lower case is accepted."))]


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


def _pid(patent_id: str) -> str:
    pid = patent_id.strip().upper()
    return pid[2:] if pid.startswith("US") else pid


@_tool("Patent status record")
def check_patent_status(patent_id: PatentId) -> dict[str, Any]:
    """Get the expiry and maintenance-fee status record of one granted US patent: status (active, expired_term,
    expired_fee_nonpayment, withdrawn or unknown), its basis, reinstatement_possible with its legal basis, as_of,
    data_version, a versioned permalink, a one-sentence `plain` statement and next_steps. Record facts, not a legal
    opinion. Use it for one known patent number; use search_patents with the "lapse" field group for the status of many
    patents at once, and get_patent_brief for the claims together with the status."""
    return _call(f"/api/v1/lapse/status/{_pid(patent_id)}/")


@_tool("Search US patents")
def search_patents(
    q: Annotated[dict[str, Any], Field(description=(
        'The query: one JSON object with exactly one key, for example {"patent_title": "laser"} or '
        '{"_and": [{"_gte": {"patent_date": "2015-01-01"}}, {"cpc_current.cpc_group_id": "H01S3/00"}]}.'))],
    f: Annotated[list[str] | None, Field(description=(
        'The fields to return, for example ["patent_id", "patent_title", "patent_date"]; add "lapse" for the expiry '
        "fields. Omitted: the API's default fields."))] = None,
    s: Annotated[list[dict[str, str]] | None, Field(description=(
        'The sort, a list of {field: "asc" or "desc"}, for example [{"patent_date": "desc"}]. Omitted: the API\'s '
        "default order."))] = None,
    size: Annotated[int, Field(description=(
        "Rows per page, 1 to 1000; a value outside that range is moved to the nearest limit."))] = 25,
    after: Annotated[list[Any] | None, Field(description=(
        "The paging cursor: the sort values of the last row of the previous page. Omitted: the top of the list."))] = None,
) -> dict[str, Any]:
    """Search granted US patents by field values with the PatentSearch v1 query grammar and return the matching rows, a
    page at a time. Use it to find patents by title words, dates, CPC class, assignee, inventor or other fields; use
    similar_patents to rank patents by closeness to a passage of text, check_patent_status for one known number, and
    search_disclosures for PatentRef registry records rather than patents. Query operators: _eq, _neq, _gt, _gte, _lt,
    _lte, _begins, _contains, _text_all, _text_any, _text_phrase, _and, _or, _not; dotted names reach nested fields.
    The "lapse" field group adds expiry_status, expiry_date_estimated, expiry_basis, reinstatement_possible, fee_status
    and lapse_date. For the next page, pass the last row's sort values as after. Field names and the full grammar:
    https://patentref.io/llms-full.txt."""
    params = {"q": json.dumps(q), "o": json.dumps({"size": max(1, min(int(size), 1000)), **({"after": after} if after else {})})}
    if f:
        params["f"] = json.dumps(f)
    if s:
        params["s"] = json.dumps(s)
    return _call("/api/v1/patent/", params)


@_tool("Patents similar to a text")
def similar_patents(
    text: Annotated[str, Field(description=(
        "The passage to match, for example a title and abstract, or a claim; up to 4,000 characters, the rest is cut."))],
    n: Annotated[int, Field(description=(
        "How many patents to return, 1 to 20; a value outside that range is moved to the nearest limit."))] = 20,
) -> dict[str, Any]:
    """Rank granted US patents by how close their title and abstract are to a passage of text, and return the n nearest
    with data_version and as_of. Use it when you have a description, an abstract or a claim and want related patents;
    use search_patents for exact filters such as dates, classes or names. The ranking is full-text bm25 over titles and
    abstracts, searched with the rarest words of the text, so the answer is a record of what the data holds, not a
    search opinion."""
    return _call("/api/v1/lapse/similar", {"text": text[:4000], "n": max(1, min(int(n), 20))})


@_tool("Claim tree of a patent")
def get_claim_tree(patent_id: PatentId) -> dict[str, Any]:
    """Get every claim of one granted US patent as a tree parsed from the claim text the USPTO published: per claim its
    number, category, dependency, preamble, transition, elements, term mentions (each "the" or "said" phrase linked to
    its earlier mention), claim references and flags, with structure_version, data_version, text_data_version and
    as_of. Every part carries start and end offsets into its claim's text (text[start:end] is the part). Use it when
    you need all claims in full detail; use get_patent_brief for a shorter answer that fits a token budget, and
    check_patent_status for the status alone. Flags are notes on the text and the data, not conclusions about the
    claims. A patent without claim text in the data answers 404."""
    return _call(f"/api/v1/structure/claims/{_pid(patent_id)}/")


@_tool("Patent brief within a token budget")
def get_patent_brief(
    patent_id: PatentId,
    max_tokens: Annotated[int, Field(description=(
        "The token budget, 300 to 32000, counted as the UTF-8 bytes of the answer / 3, rounded up; a value outside "
        "that range is moved to the nearest limit."))] = 2000,
) -> dict[str, Any]:
    """Get a brief of one granted US patent that fits a token budget, in a fixed order: identity and dates, the status
    record with its basis, each independent claim as its elements with start and end offsets into the claim text, one
    line per dependent claim, and the claim term keys. The brief stops at the item that would go over max_tokens;
    truncated and omitted say what was left out, and tokens gives its size, never over max_tokens. Every claim and
    element carries an anchor: page + "#" + anchor opens it on the record page. Quotes are the claim text as
    published. Use it to read or summarize a patent within a context budget; use get_claim_tree for all claims in full
    structure, and check_patent_status for the status alone."""
    return _call(f"/api/v1/structure/brief/{_pid(patent_id)}/", {"max_tokens": max(300, min(int(max_tokens), 32000))})


UnitType = Literal["Component", "StatedProblem"]
TextField = Literal["claims", "brief_summary", "detail_description"]


@_tool("Problems and parts of a patent (S1-D, experimental)")
def get_patent_units(
    patent_id: PatentId,
    types: Annotated[list[UnitType] | None, Field(description=(
        "The unit types to return: Component (a part the claims name) and StatedProblem (the problem the patent states). "
        "Omitted: both."))] = None,
) -> dict[str, Any]:
    """Get the S1-D units of one granted US patent (experimental): the parts its claims name (unit type Component, the
    claim's own words with the part in normal form) and the problem it states (unit type StatedProblem, the patent's own
    sentence or clause in its brief summary or the background, found by a named cue rule), each with a locator
    {patent_id, field, sequence, start, end}; quote the text at the locator. none_found means the rules found no such
    sentence, not that the patent has none. Every answer says experimental true and names s1d_version. Use it to read the
    problem a patent states and the parts it claims; use get_same_part_links for the patents whose claims name the same
    parts, locate_text to read a locator back, and get_claim_tree for the claims in full."""
    return _call(f"/api/v1/structure/units/{_pid(patent_id)}/", {"types": ",".join(types)} if types else None)


@_tool("Patents naming the same parts (S1-D, experimental)")
def get_same_part_links(
    patent_id: PatentId,
    size: Annotated[int, Field(description=(
        "Links per page, 1 to 100; a value outside that range is moved to the nearest limit."))] = 20,
    after: Annotated[list[Any] | None, Field(description=(
        "The paging cursor: next_after of the previous page, [shared_count, patents_with_key_total, patent_number, rank]. "
        "Omitted: the top of the list."))] = None,
) -> dict[str, Any]:
    """Get the granted US patents whose claims name the same parts as one patent (S1-D names_same_part, experimental),
    ranked by the number of shared parts, then by how many patents name those parts (fewer before more), then by patent
    number, a page at a time; each link lists up to 10 shared parts with their quotes and locators in both patents. A
    link says only that both patents' claims name these part words. For the next page pass next_after as after. Use
    get_patent_units for one patent's own parts and problems, and similar_patents to rank patents by a passage of text."""
    o: dict[str, Any] = {"size": max(1, min(int(size), 100))}
    if after:
        o["after"] = after
    return _call(f"/api/v1/structure/relations/{_pid(patent_id)}/", {"rel": "names_same_part", "o": json.dumps(o)})


@_tool("Text at a locator (S1-D, experimental)")
def locate_text(
    patent_id: PatentId,
    field: Annotated[TextField, Field(description=(
        "The text the locator points into: claims, brief_summary or detail_description."))],
    sequence: Annotated[int, Field(description=(
        "The claim_sequence for claims, the row for brief_summary and detail_description, as the locator gives it."))],
    start: Annotated[int, Field(description="The start offset in that text, as the locator gives it.")],
    end: Annotated[int, Field(description=(
        "The end offset in that text, as the locator gives it; at most 2,000 characters after start."))],
) -> dict[str, Any]:
    """Get the text at one S1-D locator (experimental): the field of one granted US patent at sequence, from start to
    end, up to 2,000 characters. Use it to quote or check a unit or a shared part that get_patent_units,
    get_same_part_links or search_units returned, and quote the text it gives; use get_claim_tree for whole claims."""
    return _call("/api/v1/structure/locate/", {"patent_id": _pid(patent_id), "field": field, "sequence": int(sequence),
                                               "start": int(start), "end": int(end)})


@_tool("Search problems and parts (S1-D, experimental)")
def search_units(
    q: Annotated[dict[str, Any], Field(description=(
        'The query: one JSON object, for example {"patent_id": ["10034028", "7861317"]} or {"part": "drive motor"} or '
        '{"_and": [{"type": "StatedProblem"}, {"patent_id": "10034028"}]}; type, cue and section filter it.'))],
    f: Annotated[list[str] | None, Field(description=(
        "The unit fields to return. Omitted: the API's default fields."))] = None,
    s: Annotated[list[dict[str, str]] | None, Field(description=(
        'The sort, a list of {field: "asc" or "desc"}, patent_id before the others. Omitted: the API\'s default order.'))] = None,
    size: Annotated[int, Field(description=(
        "Rows per page, 1 to 100; a value outside that range is moved to the nearest limit."))] = 25,
    after: Annotated[list[Any] | None, Field(description=(
        "The paging cursor: next_after of the previous page. Omitted: the top of the list."))] = None,
) -> dict[str, Any]:
    """Search S1-D units across granted US patents (experimental) with the PatentsView grammar: q names patent_id (a
    value or a list of up to 100) or part (a part in normal form), and type, cue and section filter it; plain values,
    _eq, and _and or _or on one field. Each row is a unit with its text and locator; total_hits_exact and budget_reached
    say how far the count goes. For the next page pass next_after as after. Use get_patent_units for one patent, and
    get_same_part_links for ranked links between patents."""
    params = {"q": json.dumps(q), "o": json.dumps({"size": max(1, min(int(size), 100)), **({"after": after} if after else {})})}
    if f:
        params["f"] = json.dumps(f)
    if s:
        params["s"] = json.dumps(s)
    return _call("/api/v1/structure/units/", params)


def _rules(tool_path: str, **params: str | None) -> dict[str, Any]:
    return _call("/api/v1/rules/" + tool_path, {k: v for k, v in params.items() if v})


@_tool("Office representation rules")
def rep_required(
    office: Office,
    act: Annotated[str | None, Field(description=(
        "One act of the representation vocabulary that rules_index lists. Omitted: every act."))] = None,
) -> dict[str, Any]:
    """List the representation rules of a patent office: whether and when an applicant must act through a registered
    agent, each row with its official source URL and a verbatim quote. Use it for questions about needing a local
    agent; use fee_schedule for fees, route_windows for time limits, and rules_index for the offices and the act
    vocabulary. office takes US, EP, CN, JP, KR, AU or PCT."""
    return _rules("rep_required/", office=office, act=act)


@_tool("Office fee schedule")
def fee_schedule(
    office: Office,
    as_of: Annotated[str | None, Field(description=(
        "The date the fees apply on, YYYY-MM-DD. Omitted: today (UTC)."))] = None,
) -> dict[str, Any]:
    """List the official fees of a patent office in force on a date: filing, search, examination, grant, renewal and
    other fees with their amounts, currency and entity tiers as the cited schedule prints them, each row with its
    official source URL and a verbatim quote. A row is in force when effective_from <= as_of < effective_to. Use it for
    what an office charges; use rep_required for agent rules and route_windows for time limits. office takes US, EP,
    CN, JP, KR, AU or PCT."""
    return _rules("fee_schedule/", office=office, as_of=as_of)


@_tool("Office time windows")
def route_windows(office: Office) -> dict[str, Any]:
    """List the generic time windows of a patent office, such as the PCT national phase time limit in months from the
    priority date, each row with its official source URL and a verbatim quote. Use it to learn how long a period an
    office allows; it never computes a last day for a specific application and takes no application number or date.
    Use fee_schedule for fees and pph_routes for Patent Prosecution Highway routes. office takes US, EP, CN, JP, KR,
    AU or PCT."""
    return _rules("route_windows/", office=office)


@_tool("Patent Prosecution Highway routes")
def pph_routes(
    from_office: Annotated[OfficeCode, BeforeValidator(_upper), Field(description=(
        "The office whose work product the PPH request relies on: US, EP, CN, JP, KR, AU or PCT. Lower case is "
        "accepted."))],
    to_office: Annotated[OfficeCode, BeforeValidator(_upper), Field(description=(
        "The office the PPH request is filed with: US, EP, CN, JP, KR, AU or PCT. Lower case is accepted."))],
) -> dict[str, Any]:
    """List the Patent Prosecution Highway (PPH) rows of to_office whose cited source names from_office as a partner
    office, each with its official source URL and a verbatim quote, and to_office_pph_rows, the number of PPH rows held
    for to_office. Use it to see whether work done at one office can support a PPH request at another; use
    route_windows for time limits. An empty list means no PPH row of to_office names from_office."""
    return _rules("pph_routes/", from_office=from_office, to_office=to_office)


@_tool("Office AI-use statement rules")
def ai_use_statement_required(office: Office) -> dict[str, Any]:
    """List the rows on whether a patent office requires a statement on the use of AI, each with its official source
    URL and a verbatim quote. An empty answer means the data holds no cited rule for that office; nothing follows from
    its absence. Use it for AI-use statement rules; use rep_required, fee_schedule or grace_period for the other rules
    of an office. office takes US, EP, CN, JP, KR, AU or PCT."""
    return _rules("ai_use_statement_required/", office=office)


@_tool("Office grace period law")
def grace_period(office: Office) -> dict[str, Any]:
    """List what the cited law of a patent office says about disclosures made before filing (the grace period), each
    row with its official source URL and a verbatim quote. Citations only: it does not say whether any disclosure falls
    inside the period. Use it to read an office's grace period text; use search_disclosures to find dated public
    disclosures in the PatentRef registry, and route_windows for other time limits. office takes US, EP, CN, JP, KR,
    AU or PCT."""
    return _rules("grace_period/", office=office)


@_tool("Rules data changelog")
def rules_changelog(
    since: Annotated[str | None, Field(description=(
        "A version x.y.z or a date YYYY-MM-DD; only releases after it are listed. Omitted: every release."))] = None,
) -> dict[str, Any]:
    """List the releases of the patent office rules data from newest to oldest, each with its version, date and the
    rows it added, closed, changed or removed. Use it to see what changed since a version or date you used before; use
    rules_index for the current version, the offices and the vocabulary."""
    return _rules("changelog/", since=since)


@_tool("Rules data index")
def rules_index() -> dict[str, Any]:
    """Describe the patent office rules data: its version and as_of date, the offices it holds with their row counts by
    rule kind, each rules tool with its parameters, and the act vocabulary. Call it before the other rules tools to
    learn which offices and act values exist; use rules_changelog for the history of changes. Takes no arguments, so
    nothing can be filtered here."""
    return _rules("")


@_tool("Data build facts", keyed=False)
def build_facts() -> dict[str, Any]:
    """Get the facts of the patent data build the API serves now: data_version, as_of, refreshed_at, the snapshot hash,
    superseded versions and the table delta. Use it to cite or check which data an answer came from, or to see when
    the data was last refreshed; it holds no patent data (use search_patents or check_patent_status) and no rules data
    (use rules_index). Takes no arguments."""
    return _call("/api/v1/meta/", keyed=False)


@_tool("Search registry disclosures", keyed=False)
def search_disclosures(
    q: Annotated[str, Field(description=(
        'Web search syntax: words, "a phrase", -word to leave a word out, or between alternatives; up to 200 '
        "characters, the rest is cut."))],
) -> dict[str, Any]:
    """Search the public records of the PatentRef registry (dated technical disclosures) whose title or abstract match
    q, from newest to oldest, up to 20. Each record carries its permalink (https://patentref.io/r/ and the record's
    id), server_time, bundle_hash and proof_status; the submitter's text is inside user_content, as entered. A record
    shows what was published and when, not a patent or an opinion. Use it to find disclosures; use search_patents or
    similar_patents to find patents."""
    return _call("/registry/search", {"q": q.strip()[:200]}, keyed=False)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="patentref-mcp",
        description="PatentRef MCP server. It runs on this machine and sends each tool call to the PatentRef API as one "
                    "HTTPS request. stdio by default; --http serves streamable HTTP from this machine instead.")
    ap.add_argument("--http", action="store_true",
                    help="serve streamable HTTP at http://HOST:PORT/mcp from this machine instead of stdio")
    ap.add_argument("--host", default="127.0.0.1", help="the address to listen on with --http (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8787, help="the port to listen on with --http (default 8787)")
    ap.add_argument("--version", action="version", version=f"patentref-mcp {__version__}")
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
