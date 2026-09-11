"""
Multi-account token + repo registry for Sentinel.

Reads paired secrets in this shape (works identically from a local .env
file or from GitHub Actions Secrets injected as env vars):

    SENTINEL_REPO_1=owner1/repo1
    SENTINEL_TOKEN_1=ghp_xxx...

    SENTINEL_REPO_2=owner2/repo2
    SENTINEL_TOKEN_2=ghp_yyy...

Add as many numbered pairs as you need. Each repo gets matched to its
own token by index, so repos across different GitHub accounts each
authenticate correctly.

A plain GITHUB_TOKEN is still supported as a fallback for any repo not
found in the numbered list.
"""

import os
from github import Github

try:
    from dotenv import load_dotenv
    load_dotenv()  # no-op if no .env file present (e.g. in CI/Actions)
except ImportError:
    pass  # dotenv is optional; env vars may already be set (e.g. by Actions)


def load_repo_token_pairs() -> dict:
    """
    Scans SENTINEL_REPO_N / SENTINEL_TOKEN_N pairs from the environment
    and returns a dict mapping repo_name -> token.
    """
    pairs = {}
    i = 1
    while True:
        repo = os.environ.get(f"SENTINEL_REPO_{i}")
        token = os.environ.get(f"SENTINEL_TOKEN_{i}")
        if not repo and not token:
            break
        if repo and token:
            pairs[repo] = token
        i += 1
    return pairs


_REPO_TOKEN_MAP = load_repo_token_pairs()


def get_watched_repos() -> list:
    """Returns the list of repos configured via SENTINEL_REPO_N pairs."""
    return list(_REPO_TOKEN_MAP.keys())


def get_token_for_repo(repo_name: str) -> str:
    """
    Resolve the correct token for a given "owner/repo" string.
    Checks the numbered SENTINEL_REPO_N/TOKEN_N pairs first,
    falls back to a plain GITHUB_TOKEN.
    """
    if repo_name in _REPO_TOKEN_MAP:
        return _REPO_TOKEN_MAP[repo_name]

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    raise ValueError(
        f"No token found for repo '{repo_name}'. "
        f"Add SENTINEL_REPO_N='{repo_name}' and SENTINEL_TOKEN_N='<token>' "
        f"as a pair, or set GITHUB_TOKEN as a fallback."
    )


def get_client_for_repo(repo_name: str) -> Github:
    """Returns a PyGithub client authenticated with the right token for this repo."""
    return Github(get_token_for_repo(repo_name))