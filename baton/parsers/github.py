"""GitHub parser and read-only connector.

The parser accepts a JSON export ({"repo":..., "items":[...]}). `fetch_repo` builds the same shape from the
public REST API, with an optional read-only token. Baton never asks for admin scopes.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from .base import ParseError, ParseResult, Skipped, Unit

_REPO_URL = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def parse_github(data: str | dict, code: str = "GH") -> ParseResult:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ParseError(f"This file is not valid JSON ({e.msg}, line {e.lineno}).") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ParseError('Expected GitHub JSON with an "items" list of issues and pull requests.')

    result = ParseResult(title=data.get("repo", "GitHub repository"))
    logins: set[str] = set()
    for pos, item in enumerate(data["items"], 1):
        if not isinstance(item, dict) or "number" not in item:
            result.skipped.append(Skipped(f"item {pos}", "no issue or PR number", str(item)[:80]))
            continue
        num = item["number"]
        is_pr = item.get("type") == "pull_request"
        noun = "PR" if is_pr else "Issue"
        author = (item.get("user") or {}).get("login") or "unknown"
        logins.add(author)
        url = item.get("html_url", "")
        state = "merged" if item.get("merged") else item.get("state", "unknown")
        labels = item.get("labels") or []
        label_text = f" [labels: {', '.join(labels)}]" if labels else ""
        body = (item.get("body") or "").strip()
        head = f"{noun} #{num} '{item.get('title', '')}' (state: {state}){label_text}:"
        result.units.append(
            Unit(f"{code}-{num}", author, _ts(item.get("created_at")), f"{head} {body}".strip(), url, "issue")
        )
        for c_pos, c in enumerate(item.get("comments_data") or [], 1):
            c_author = (c.get("user") or {}).get("login") or "unknown"
            logins.add(c_author)
            result.units.append(
                Unit(
                    f"{code}-{num}c{c_pos}",
                    c_author,
                    _ts(c.get("created_at")),
                    f"Comment on {noun} #{num}: {(c.get('body') or '').strip()}",
                    c.get("html_url") or url,
                    "comment",
                )
            )
        if item.get("merged_at"):
            result.units.append(
                Unit(f"{code}-{num}m", author, _ts(item["merged_at"]),
                     f"PR #{num} '{item.get('title', '')}' was merged.", url, "event")
            )
        elif item.get("closed_at"):
            result.units.append(
                Unit(f"{code}-{num}x", author, _ts(item["closed_at"]),
                     f"{noun} #{num} '{item.get('title', '')}' was closed.", url, "event")
            )
    if not result.units:
        raise ParseError("No issues or pull requests were found in this GitHub data.")
    result.roster = [{"name": x, "aliases": [x]} for x in sorted(logins)]
    return result


def parse_repo_url(url: str) -> tuple[str, str]:
    m = _REPO_URL.match(url.strip())
    if not m:
        raise ParseError("Please enter a GitHub repository as https://github.com/owner/name or owner/name.")
    return m.group(1), m.group(2)


def fetch_repo(url: str, token: str | None = None, max_items: int = 60, max_comment_fetches: int = 25,
               client: httpx.Client | None = None) -> dict:
    """Read issues, PRs and comments over the public REST API. No write scope is ever used."""
    owner, name = parse_repo_url(url)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    own = client is None
    client = client or httpx.Client(timeout=20, headers=headers, follow_redirects=True)
    base = f"https://api.github.com/repos/{owner}/{name}"
    try:
        items: list[dict] = []
        comment_fetches = 0
        page = 1
        while len(items) < max_items:
            r = client.get(f"{base}/issues", params={"state": "all", "per_page": 50, "page": page,
                                                     "sort": "updated"}, headers=headers)
            _raise_for_github(r, f"{owner}/{name}")
            batch = r.json()
            if not batch:
                break
            for it in batch:
                if len(items) >= max_items:
                    break
                is_pr = "pull_request" in it
                entry = {
                    "number": it["number"], "type": "pull_request" if is_pr else "issue", "title": it.get("title", ""),
                    "user": it.get("user"), "state": it.get("state"), "created_at": it.get("created_at"),
                    "closed_at": it.get("closed_at"), "html_url": it.get("html_url"), "body": it.get("body") or "",
                    "labels": [lb["name"] for lb in it.get("labels", []) if isinstance(lb, dict)],
                    "comments_data": [],
                }
                if is_pr and (it["pull_request"] or {}).get("merged_at"):
                    entry["merged"] = True
                    entry["merged_at"] = it["pull_request"]["merged_at"]
                    entry["closed_at"] = None
                if it.get("comments") and comment_fetches < max_comment_fetches:
                    comment_fetches += 1
                    cr = client.get(it["comments_url"], params={"per_page": 30}, headers=headers)
                    _raise_for_github(cr, f"{owner}/{name}")
                    entry["comments_data"] = [
                        {"user": c.get("user"), "created_at": c.get("created_at"), "body": c.get("body"),
                         "html_url": c.get("html_url")}
                        for c in cr.json()
                    ]
                items.append(entry)
            page += 1
        return {"repo": f"{owner}/{name}", "items": items}
    except httpx.HTTPError as e:
        raise ParseError(f"Could not reach GitHub ({type(e).__name__}). Check your connection and try again.") from e
    finally:
        if own:
            client.close()


def _raise_for_github(r: httpx.Response, repo: str) -> None:
    if r.status_code == 404:
        raise ParseError(f"Repository {repo} was not found, or it is private and the token cannot read it.")
    if r.status_code in (401, 403, 429):
        raise ParseError(
            f"GitHub refused the request (HTTP {r.status_code}). The anonymous rate limit may be used up, "
            "or the token may lack read access. Add a read-only token, or upload a JSON export instead."
        )
    if r.status_code >= 400:
        raise ParseError(f"GitHub returned HTTP {r.status_code} for {repo}.")
