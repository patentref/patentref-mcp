# patentref-mcp

<!-- mcp-name: io.github.patentref/patentref-mcp -->

The only free API and MCP tool we know of that returns a US patent's claim tree with element segmentation, antecedent links and character offsets, built by deterministic code, 1976 to now.

patentref-mcp is an MCP server for the PatentRef API (https://patentref.io). Each tool sends one HTTPS request to the public API with the key from its environment and returns the API's JSON as the API sent it.

## Run

```
pip install patentref-mcp
export PATENTREF_API_KEY=...        # a key from https://patentref.io/api/v1/meta/signup/
patentref-mcp                       # stdio, what most MCP clients start
patentref-mcp --http --port 8787    # streamable HTTP at http://127.0.0.1:8787/mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"patentref": {"command": "patentref-mcp", "env": {"PATENTREF_API_KEY": "..."}}}}
```

Environment: `PATENTREF_API_KEY` (sent by every tool but `build_facts` and `search_disclosures`), `PATENTREF_BASE` (default `https://patentref.io`), `PATENTREF_TIMEOUT` (seconds, default 30).

Check an install: `python -m patentref_mcp.smoke 10000364` starts the server over stdio, lists its tools, makes two tool calls and prints the transcript.

## Tools

- **check_patent_status**: The one-call verdict for one US patent: status (active, expired_term, expired_fee_nonpayment, withdrawn, unknown), basis entries, reinstatement_possible with its 37 CFR 1.378 basis, as_of, data_version, the versioned permalink, a one-sentence plain record statement and next_steps. Record facts only.

- **search_patents**: Search granted US patents with the PatentSearch v1 grammar: q is one JSON object with one key (for example {"patent_title": "laser"} or {"_and": [{"_gte": {"patent_date": "2015-01-01"}}, {"cpc_current.cpc_group_id": "H01S3/00"}]}), f the fields to return (add "lapse" for the expiry group: expiry_status, expiry_date_estimated, expiry_basis, reinstatement_possible, fee_status, lapse_date), s the sort (for example [{"patent_date": "desc"}]), size up to 1000, after the cursor from the previous page's last sort values.

- **similar_patents**: The n (1 to 20) US patents nearest to a title plus abstract by FTS5 bm25 over title and abstract, with data_version and as_of. A record of what the corpus contained, not a search opinion.

- **get_claim_tree**: The claims of one US patent as a tree, from the claim text the USPTO published: per claim its number, category, dependency, preamble, transition, elements, term mentions with the earlier mention each "the"/"said" phrase was matched to, claim references and flags; the relations between those parts; structure_version, data_version, text_data_version and as_of. Every part carries start and end offsets into its claim's text (text[start:end] is the part). Flags describe the text and the data only.

- **get_patent_brief**: A brief of one US patent that fits max_tokens (300 to 32000, default 2000), in a fixed order: identity and dates, the status record with its basis, each independent claim as its elements with start and end offsets into the claim text, each dependent claim on one line, and the claim term keys; the first item that does not fit ends the brief, and truncated and omitted say what was left out. tokens counts the answer by the stated proxy in tokenizer (UTF-8 bytes / 3, rounded up), never over max_tokens. Every claim and element carries anchor: page + "#" + anchor opens it on the record page. Quotes are the claim text as published.

- **rep_required**: Representation rules of a patent office (office: US, EP, CN, JP, KR, AU, or PCT for the international phase): whether and when an applicant must act through a registered agent, each row with its official source and quote. act keeps the rows of one act of the vocabulary (rules_index lists it).

- **fee_schedule**: Official fees of a patent office in force on as_of (YYYY-MM-DD, default today UTC): filing, search, examination, grant, renewal and other fees with their currency, each row with its official source and quote.

- **route_windows**: Generic windows of a patent office, such as the PCT national phase time limit in months from the priority date. Never a last day for a specific application; it takes no application number or date.

- **pph_routes**: Patent Prosecution Highway rows of to_office whose cited source names from_office as a partner office.

- **ai_use_statement_required**: Rows on whether a patent office requires a statement on the use of AI. An empty answer means the dataset holds no cited rule for that office; nothing follows from its absence.

- **grace_period**: What the cited law of an office says about disclosures before filing (the grace period). Citations only: it does not say whether any disclosure falls inside the period.

- **rules_changelog**: The rules dataset's releases, newest first, after since (a version x.y.z or a date YYYY-MM-DD).

- **rules_index**: The rules dataset: its version and date, the offices with their row counts, the tools and the act vocabulary.

- **build_facts**: The build in service: data_version, as_of, refreshed_at, snapshot hash, superseded versions, table delta.

- **search_disclosures**: Public records of the PatentRef registry (dated technical disclosures) whose title or abstract match q (web search syntax: words, "a phrase", -word, or), newest first, up to 20. Each record carries its permalink (https://patentref.io/r/<id>), server_time, bundle_hash and proof_status; the submitter's text is inside user_content, as entered. A record shows what was published and when, not a patent or an opinion.

An error from the API comes back as the tool's error text: the HTTP status and the API's `X-Status-Reason`. The server does not retry.

## License

MIT, see `LICENSE`. The data the API returns has its own license and attribution lines: https://patentref.io/LICENSES.md

This repository holds the source of the patentref-mcp package published on PyPI (https://pypi.org/project/patentref-mcp/), version 0.4.0. Questions and problems: hello@patentref.io.
