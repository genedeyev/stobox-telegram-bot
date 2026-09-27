"""Stoby, stage 1: a thin client over the live site, SIG and the chain.

Stoby holds no facts of its own. Every fact in an answer comes from one of three
sources read at answer time: https://www.stobox.io/llms-full.txt (the site), the
SIG MCP server (https://mcp.stobox.io) and Base mainnet. See wiki page "Stoby" in
the Stobox vault for the programme and acceptance criteria A1-1…A1-7.
"""

__version__ = "1.0.0"
