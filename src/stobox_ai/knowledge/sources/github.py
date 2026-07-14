"""GitHub source — auto-discover and ingest the StoboxTechnologies repos.

For each repo (auto-discovered from the org, or an explicit list) it reads the
full file tree via the GitHub API, filters to text files (Markdown/docs and,
optionally, Solidity/TS/… source), and fetches raw contents. Each file becomes a
Document that cites its GitHub blob URL, so the bot can answer "what's in the
STV3 protocol repo?" from the actual source.

Works unauthenticated (low rate limit); set GITHUB_TOKEN for headroom. Raw file
contents are fetched from raw.githubusercontent.com, which doesn't consume the
API rate limit.
"""

from __future__ import annotations

from ...logging import get_logger
from ..models import DocMeta, Document
from .base import Fetcher, Source

log = get_logger(__name__)

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"
_DOC_EXT = {".md", ".markdown", ".rst", ".txt", ".adoc"}


class GitHubSource(Source):
    name = "github"

    def __init__(
        self,
        org: str | None = None,
        repos: list[str] | None = None,
        branch: str | None = None,
        include_ext: list[str] | None = None,
        include_code: bool = True,
        max_files: int = 500,
        token: str | None = None,
    ) -> None:
        self.org = org
        self.repos = repos or []            # "owner/repo" strings; empty = discover org
        self.branch = branch                # None = use each repo's default branch
        self.include_ext = {e.lower() for e in (include_ext or [])} or None
        self.include_code = include_code
        self.max_files = max_files
        self.token = token

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def fetch(self, fetcher: Fetcher) -> list[Document]:
        repos = await self._resolve_repos(fetcher)
        docs: list[Document] = []
        for owner, repo, default_branch in repos:
            if len(docs) >= self.max_files:
                break
            branch = self.branch or default_branch or "main"
            docs += await self._fetch_repo(fetcher, owner, repo, branch)
        log.info("github.fetched", repos=len(repos), files=len(docs))
        return docs[: self.max_files]

    async def _resolve_repos(self, fetcher: Fetcher) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        if self.repos:
            for full in self.repos:
                owner, _, repo = full.partition("/")
                if owner and repo:
                    out.append((owner, repo, self.branch or "main"))
            return out
        if not self.org:
            return out
        status, data = await fetcher.get_json(
            f"{_API}/orgs/{self.org}/repos?per_page=100&type=public", headers=self._headers()
        )
        if status != 200 or not isinstance(data, list):
            log.warning("github.discover_failed", org=self.org, status=status)
            return out
        for r in data:
            if r.get("archived") or r.get("fork"):
                continue
            out.append((self.org, r["name"], r.get("default_branch", "main")))
        log.info("github.discovered", org=self.org, repos=[r for _, r, _ in out])
        return out

    async def _fetch_repo(
        self, fetcher: Fetcher, owner: str, repo: str, branch: str
    ) -> list[Document]:
        status, tree = await fetcher.get_json(
            f"{_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", headers=self._headers()
        )
        if status == 403:
            log.warning("github.rate_limited", hint="set GITHUB_TOKEN for higher limits")
            return []
        if status != 200 or not isinstance(tree, dict):
            log.warning("github.tree_failed", repo=f"{owner}/{repo}", status=status)
            return []

        docs: list[Document] = []
        for node in tree.get("tree", []):
            if node.get("type") != "blob":
                continue
            path = node["path"]
            if not self._want(path):
                continue
            raw_status, content, _ = await fetcher.get_text(f"{_RAW}/{owner}/{repo}/{branch}/{path}")
            if raw_status != 200 or not content.strip():
                continue
            docs.append(self._to_doc(owner, repo, branch, path, content))
            if len(docs) >= self.max_files:
                break
        return docs

    def _want(self, path: str) -> bool:
        low = path.lower()
        ext = low[low.rfind("."):] if "." in low else ""
        if self.include_ext is not None:
            return ext in self.include_ext
        if ext in _DOC_EXT:
            return True
        return self.include_code and self._looks_like_code(ext)

    @staticmethod
    def _looks_like_code(ext: str) -> bool:
        return ext in {
            ".sol", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs",
            ".json", ".yaml", ".yml", ".toml",
        }

    @staticmethod
    def _to_doc(owner: str, repo: str, branch: str, path: str, content: str) -> Document:
        ext = path.lower()[path.rfind("."):] if "." in path else ""
        is_doc = ext in _DOC_EXT
        blob_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
        # For code, prepend a small header so retrieval has context on the file.
        text = content if is_doc else f"File: {path} (repo {repo})\n\n{content}"
        meta = DocMeta(
            title=f"{repo}: {path}",
            source_file=f"github://{owner}/{repo}/{path}",
            source_url=blob_url,
            category="documentation" if is_doc else "code",
            product=repo,
            visibility="public",
            extra={"repo": f"{owner}/{repo}", "path": path, "branch": branch},
        )
        return Document(meta=meta, text=text)
