# Changelog

## 0.4.1 (2026-11-11)

- Tool descriptions rewritten. Each one now starts with what the tool does, says when to use it and which tool to use instead, and ends with how it behaves: read only, whether it needs PATENTREF_API_KEY, the allowance of a free key (1,000 requests a month), and the API errors it passes on.
- Every argument has a description in the input schema. office, from_office and to_office list their codes (US, EP, CN, JP, KR, AU, PCT) and accept lower case; a code outside that list is refused before any request is sent, where 0.4.0 sent it and the API answered 400.
- Every tool has a title and the MCP annotations readOnlyHint, idempotentHint and openWorldHint (true) and destructiveHint (false).
- The server reports its own version (0.4.1) in serverInfo, where 0.4.0 reported the MCP SDK's version; `patentref-mcp --version` prints it.
- README: a plain opening, the Glama score badge, and how the server runs: on your own machine, over stdio by default, with `--http` serving streamable HTTP from that machine.
- The repository has tests, a CI workflow (lint, tests on Python 3.10 to 3.14, a build check), CodeQL code scanning and Dependabot updates for pip and GitHub Actions.
- pydantic is listed as a dependency; the server imports it, and mcp already required it.
- No tool or argument was renamed or removed, and each tool sends the same request to the API as in 0.4.0.

## 0.4.0 (2026-11-05)

- Published on PyPI and listed in the official MCP Registry as io.github.patentref/patentref-mcp: 15 tools over stdio or streamable HTTP (check_patent_status, search_patents, similar_patents, get_claim_tree, get_patent_brief, the eight rules tools, build_facts and search_disclosures), MIT license.
