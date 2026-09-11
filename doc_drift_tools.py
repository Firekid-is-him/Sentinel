"""
Doc/API drift tools for Sentinel.
Detects when a merged commit changes a public function/method signature
that's referenced in the project's README or docs, and opens a PR to
update the documentation to match.
"""

import re
from strands import tool
from token_registry import get_client_for_repo


@tool
def check_doc_drift(repo_name: str, commit_sha: str, doc_path: str = "README.md") -> dict:
    """
    Check whether a commit's code changes reference function or method
    names that also appear in the project's documentation, which may
    now be stale. This is a lightweight heuristic check, not a full
    static analysis — it looks for overlapping identifiers between the
    diff and the doc file.

    Args:
        repo_name: Repository in "owner/repo" format
        commit_sha: The commit SHA to check
        doc_path: Path to the documentation file to check against (default README.md)

    Returns:
        A dict with 'drift_detected' (bool), 'matched_identifiers' (list),
        and 'doc_excerpt' (the relevant doc section, if found).
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)
    commit = repo.get_commit(commit_sha)

    changed_identifiers = set()
    for f in commit.files:
        if not f.patch:
            continue
        for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]{3,})\s*\(", f.patch):
            changed_identifiers.add(match.group(1))

    try:
        doc_file = repo.get_contents(doc_path)
        doc_content = doc_file.decoded_content.decode("utf-8")
    except Exception:
        return {"drift_detected": False, "matched_identifiers": [], "doc_excerpt": ""}

    matched = [ident for ident in changed_identifiers if ident in doc_content]

    excerpt = ""
    if matched:
        idx = doc_content.find(matched[0])
        start = max(0, idx - 100)
        end = min(len(doc_content), idx + 200)
        excerpt = doc_content[start:end]

    return {
        "drift_detected": len(matched) > 0,
        "matched_identifiers": matched,
        "doc_excerpt": excerpt,
    }


@tool
def open_doc_update_pr(
    repo_name: str,
    base_branch: str,
    doc_path: str,
    old_text: str,
    new_text: str,
    reasoning: str,
) -> dict:
    """
    Open a pull request that updates a specific section of a documentation
    file to match a code change. Use this only when check_doc_drift has
    confirmed a specific stale reference, and you know the exact old and
    new text to swap.

    Args:
        repo_name: Repository in "owner/repo" format
        base_branch: The branch to base the fix on (e.g. "main")
        doc_path: Path to the documentation file, e.g. "README.md"
        old_text: The exact stale text to replace
        new_text: The corrected text
        reasoning: A short explanation of why this update is needed

    Returns:
        A dict with the PR number and html_url, or an error message.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)

    try:
        file = repo.get_contents(doc_path, ref=base_branch)
        content = file.decoded_content.decode("utf-8")
    except Exception as e:
        return {"error": f"Could not read {doc_path}: {e}"}

    if old_text not in content:
        return {"error": f"Could not find the specified text in {doc_path}"}

    updated_content = content.replace(old_text, new_text, 1)

    branch_name = f"sentinel/docs-update-{doc_path.replace('/', '-').replace('.', '-')}"
    base_ref = repo.get_git_ref(f"heads/{base_branch}")

    try:
        old_ref = repo.get_git_ref(f"heads/{branch_name}")
        old_ref.delete()
    except Exception:
        pass  # branch didn't exist yet, which is the normal case

    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)

    file = repo.get_contents(doc_path, ref=base_branch)

    repo.update_file(
        path=doc_path,
        message=f"docs: update {doc_path} to match code changes",
        content=updated_content,
        sha=file.sha,
        branch=branch_name,
    )

    pr_body = (
        f"## Sentinel doc-drift fix\n\n"
        f"**File:** `{doc_path}`\n\n"
        f"**Why:** {reasoning}\n\n"
        f"Detected that a recent code change made this documentation stale. "
        f"Please verify the update reads correctly before merging."
    )

    try:
        pr = repo.create_pull(
            title=f"Sentinel: update {doc_path} for code changes",
            body=pr_body,
            head=branch_name,
            base=base_branch,
        )
    except Exception as e:
        return {
            "error": f"Branch and commit were created, but opening the PR failed: {e}",
            "branch_name": branch_name,
        }

    return {"pr_number": pr.number, "html_url": pr.html_url}