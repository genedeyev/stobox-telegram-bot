"""Web + GitHub source tests — fully offline via an injected fake fetcher."""

from __future__ import annotations

import pytest

from stobox_ai.knowledge.sources import GitHubSource, WebSource


class FakeFetcher:
    """Maps URLs to canned (status, text) / (status, json) responses.

    ``redirect`` maps a requested URL to the final URL after redirects; the body
    is looked up by that final URL (mirrors httpx follow_redirects)."""

    def __init__(
        self, text: dict | None = None, json: dict | None = None, redirect: dict | None = None
    ) -> None:
        self._text = text or {}
        self._json = json or {}
        self._redirect = redirect or {}
        self.calls: list[str] = []

    async def get_text(self, url, headers=None):
        self.calls.append(url)
        final = self._redirect.get(url, url)
        if final in self._text:
            return 200, self._text[final], final
        return 404, "", final

    async def get_json(self, url, headers=None):
        self.calls.append(url)
        if url in self._json:
            return 200, self._json[url]
        return 404, None

    async def aclose(self):
        pass


# --------------------------------------------------------------------------- #
# Web crawler
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_web_crawl_follows_links_same_domain():
    page1 = """<html><head><title>STBU Overview</title></head>
      <body><main><h1>STBU</h1>
      <p>%s</p>
      <a href="/utility">Utility</a>
      <a href="https://evil.com/x">offsite</a>
      </main></body></html>""" % ("STBU is the native utility token of Stobox. " * 12)
    page2 = """<html><head><title>Utility</title></head><body><main>
      <p>%s</p></main></body></html>""" % ("STBU pays for platform features. " * 12)

    fetcher = FakeFetcher(text={
        "https://www.stobox.io": page1,
        "https://www.stobox.io/utility": page2,
    })
    src = WebSource(seeds=["https://www.stobox.io/"], allow_domains=["www.stobox.io"],
                    max_pages=10, max_depth=2, delay_seconds=0)
    docs = await src.fetch(fetcher)

    titles = {d.meta.title for d in docs}
    assert "STBU Overview" in titles and "Utility" in titles
    # Offsite link was not crawled.
    assert not any("evil.com" in c for c in fetcher.calls)
    # Pages are citable by their live URL.
    assert all(d.meta.source_url.startswith("https://www.stobox.io") for d in docs)


@pytest.mark.asyncio
async def test_web_skips_thin_pages_and_respects_robots():
    robots = "User-agent: *\nDisallow: /secret"
    thin = "<html><body><main><p>hi</p></main></body></html>"
    root = """<html><head><title>Good</title></head><body><main>
      <p>%s</p>
      <a href="/secret">secret</a>
      <a href="/thin">thin</a>
      </main></body></html>""" % ("Real content about Stobox tokenization. " * 12)
    fetcher = FakeFetcher(text={
        "https://stobox.io/robots.txt": robots,
        "https://stobox.io": root,
        "https://stobox.io/secret": root,   # disallowed by robots → must never be fetched
        "https://stobox.io/thin": thin,     # fetched but too thin → no doc
    })
    src = WebSource(seeds=["https://stobox.io/"], allow_domains=["stobox.io"],
                    max_pages=10, max_depth=1, delay_seconds=0)
    docs = await src.fetch(fetcher)

    assert [d.meta.title for d in docs] == ["Good"]     # only the root page produced a doc
    assert "https://stobox.io/secret" not in fetcher.calls   # robots blocked the page GET
    assert "https://stobox.io/thin" in fetcher.calls         # thin page was fetched but skipped


@pytest.mark.asyncio
async def test_web_discovers_urls_from_llms_txt_when_no_sitemap():
    llms = (
        "# Stobox\n> overview\n\n## URLs\n"
        "- https://www.stobox.io/compass\n"
        "- https://www.stobox.io/stbu\n"
        "- https://x.com/StoboxCompany\n"   # offsite → ignored
    )
    page = "<html><head><title>%s</title></head><body><main><p>%s</p></main></body></html>"
    fetcher = FakeFetcher(text={
        # sitemap.xml absent (404); llms-full.txt absent; llms.txt present
        "https://www.stobox.io/llms.txt": llms,
        "https://www.stobox.io/compass": page % ("Compass", "Compass tokenizes assets. " * 12),
        "https://www.stobox.io/stbu": page % ("STBU", "STBU is the utility token. " * 12),
    })
    src = WebSource(seeds=["https://www.stobox.io/"], allow_domains=["www.stobox.io"],
                    max_pages=10, max_depth=1, delay_seconds=0)
    docs = await src.fetch(fetcher)
    titles = {d.meta.title for d in docs}
    assert titles == {"Compass", "STBU"}                 # discovered from llms.txt
    assert not any("x.com" in c for c in fetcher.calls)  # offsite inventory link ignored


@pytest.mark.asyncio
async def test_web_uses_final_url_after_cross_host_redirect():
    page = "<html><head><title>Docs Home</title></head><body><main><p>%s</p></main></body></html>" % (
        "Real Stobox documentation content. " * 12
    )
    fetcher = FakeFetcher(
        text={"https://www.stobox.io/home": page},
        redirect={"https://docs.stobox.io": "https://www.stobox.io/home"},
    )
    src = WebSource(seeds=["https://docs.stobox.io/"],
                    allow_domains=["docs.stobox.io", "www.stobox.io"],
                    max_pages=5, max_depth=0, delay_seconds=0)
    docs = await src.fetch(fetcher)
    assert len(docs) == 1
    # Cited at the URL it actually resolved to, not the redirecting one.
    assert docs[0].meta.source_url == "https://www.stobox.io/home"


@pytest.mark.asyncio
async def test_llms_txt_source_ingests_full_reference():
    from stobox_ai.knowledge.sources import LlmsTxtSource

    full = "# Stobox — Full Reference for AI Systems\n\n" + ("RWA tokenization infrastructure. " * 40)
    fetcher = FakeFetcher(text={"https://www.stobox.io/llms-full.txt": full})
    src = LlmsTxtSource(hosts=["www.stobox.io"])
    docs = await src.fetch(fetcher)
    assert len(docs) == 1                                  # full supersedes short
    assert docs[0].meta.extra["file"] == "llms-full.txt"
    # Public citation = the website, NEVER the .txt machine file.
    assert docs[0].meta.source_url == "https://www.stobox.io"
    assert docs[0].meta.extra["fetched_from"] == "https://www.stobox.io/llms-full.txt"
    assert ".txt" not in docs[0].meta.source_url
    assert docs[0].meta.confidence == 1.0


@pytest.mark.asyncio
async def test_llms_txt_source_skips_html_soft_404():
    from stobox_ai.knowledge.sources import LlmsTxtSource

    fetcher = FakeFetcher(text={
        "https://www.stobox.io/llms-full.txt": "<!doctype html><html><body>not found</body></html>",
        "https://www.stobox.io/llms.txt": "<html><head></head><body>spa shell</body></html>",
    })
    src = LlmsTxtSource(hosts=["www.stobox.io"])
    docs = await src.fetch(fetcher)
    assert docs == []                                     # HTML shells rejected


# --------------------------------------------------------------------------- #
# GitHub ingester
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_github_discovers_org_and_ingests_docs_and_code():
    org = "StoboxTechnologies"
    repos_json = [
        {"name": "Stobox_STV3_Protocol", "default_branch": "main", "archived": False, "fork": False},
        {"name": "old-fork", "default_branch": "main", "archived": False, "fork": True},  # skipped
    ]
    tree_json = {"tree": [
        {"path": "README.md", "type": "blob"},
        {"path": "contracts/Token.sol", "type": "blob"},
        {"path": "image.png", "type": "blob"},           # filtered out
        {"path": "src", "type": "tree"},                 # not a blob
    ]}
    fetcher = FakeFetcher(
        json={
            f"https://api.github.com/orgs/{org}/repos?per_page=100&type=public": repos_json,
            f"https://api.github.com/repos/{org}/Stobox_STV3_Protocol/git/trees/main?recursive=1": tree_json,
        },
        text={
            f"https://raw.githubusercontent.com/{org}/Stobox_STV3_Protocol/main/README.md":
                "# STV3 Protocol\nProgrammable asset infrastructure.",
            f"https://raw.githubusercontent.com/{org}/Stobox_STV3_Protocol/main/contracts/Token.sol":
                "pragma solidity ^0.8.0;\ncontract Token {}",
        },
    )
    src = GitHubSource(org=org, include_code=True)
    docs = await src.fetch(fetcher)

    paths = {d.meta.extra.get("path") for d in docs}
    assert paths == {"README.md", "contracts/Token.sol"}   # png + tree excluded, fork skipped
    by_cat = {d.meta.extra["path"]: d.meta.category for d in docs}
    assert by_cat["README.md"] == "documentation"
    assert by_cat["contracts/Token.sol"] == "code"
    # Citations point at the real GitHub blob URL.
    assert all(d.meta.source_url.startswith("https://github.com/StoboxTechnologies/") for d in docs)


@pytest.mark.asyncio
async def test_github_docs_only_when_include_code_false():
    fetcher = FakeFetcher(
        json={"https://api.github.com/repos/o/r/git/trees/main?recursive=1": {"tree": [
            {"path": "docs/guide.md", "type": "blob"},
            {"path": "src/a.ts", "type": "blob"},
        ]}},
        text={
            "https://raw.githubusercontent.com/o/r/main/docs/guide.md": "# Guide\n" + "text " * 5,
            "https://raw.githubusercontent.com/o/r/main/src/a.ts": "export const x = 1",
        },
    )
    src = GitHubSource(repos=["o/r"], branch="main", include_code=False)
    docs = await src.fetch(fetcher)
    assert {d.meta.extra["path"] for d in docs} == {"docs/guide.md"}


# --------------------------------------------------------------------------- #
# Pagination walk (full blog archive) + GitHub budget/priority rules
# --------------------------------------------------------------------------- #
def _listing(*hrefs, title="Blog"):
    links = "".join(f'<a href="{h}">post</a>' for h in hrefs)
    return (f"<html><head><title>{title}</title></head><body><main>"
            f"<p>{'Stobox blog archive listing page with posts. ' * 10}</p>"
            f"{links}</main></body></html>")


def _post(title):
    return (f"<html><head><title>{title}</title></head><body><main>"
            f"<p>{'Deep tokenization insight from the Stobox team. ' * 12}</p>"
            "</main></body></html>")


@pytest.mark.asyncio
async def test_web_paginate_walks_archive_until_dry():
    fetcher = FakeFetcher(text={
        "https://s.io/blog": _listing("/blog/a"),
        "https://s.io/blog/page/2": _listing("/blog/b"),
        "https://s.io/blog/page/3": _listing("/blog/c"),
        # page/4 404s → walk stops
        "https://s.io/blog/a": _post("Post A"),
        "https://s.io/blog/b": _post("Post B"),
        "https://s.io/blog/c": _post("Post C"),
    })
    src = WebSource(seeds=["https://s.io/blog"], allow_domains=["s.io"],
                    max_pages=50, max_depth=2, delay_seconds=0,
                    paginate=["https://s.io/blog/page/{n}"])
    docs = await src.fetch(fetcher)
    titles = {d.meta.title for d in docs}
    # Every archived post is captured, including ones only reachable via pagination.
    assert {"Post A", "Post B", "Post C"} <= titles
    # The walk stopped at the 404 (no runaway enumeration).
    assert "https://s.io/blog/page/5" not in fetcher.calls


def test_github_want_includes_licenses_excludes_junk():
    src = GitHubSource(org="X")
    assert src._want("LICENSE")
    assert src._want("NOTICE")
    assert src._want("docs/CHANGELOG")
    assert src._want("contracts/Token.sol")
    assert not src._want("node_modules/lodash/index.js")
    assert not src._want("frontend/dist/app.min.js")
    assert not src._want("package-lock.json")
    assert not src._want("build/output.js")


def test_github_priority_orders_docs_then_contracts_then_code():
    p = GitHubSource._priority
    assert p("LICENSE") == 0 and p("README.md") == 0 and p("CHANGELOG") == 0
    assert p("contracts/STV3.sol") == 1
    assert p("src/app.ts") == 2
    ordered = sorted(["src/app.ts", "contracts/STV3.sol", "LICENSE"], key=p)
    assert ordered == ["LICENSE", "contracts/STV3.sol", "src/app.ts"]
