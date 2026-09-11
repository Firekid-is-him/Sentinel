"""
GitHub Actions tools for Sentinel.
Fetches failed workflow runs, downloads and extracts failure logs,
and retrieves the diff of the commit that triggered a run.

Multi-account aware: resolves the correct token per repo via
token_registry, so Sentinel can watch repos across different
GitHub accounts you control.
"""

import io
import zipfile
import requests
from strands import tool

from token_registry import get_token_for_repo, get_client_for_repo


@tool
def get_latest_failed_run(repo_name: str) -> dict:
    """
    Find the most recent failed GitHub Actions workflow run for a repo.

    Args:
        repo_name: Repository in "owner/repo" format, e.g. "idc-what-u-think/notebook"

    Returns:
        A dict with run_id, workflow_name, branch, commit_sha, and html_url
        for the most recent failed run. Empty dict if no failed runs found.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)
    runs = repo.get_workflow_runs(status="completed")

    for run in runs:
        if run.conclusion == "failure":
            return {
                "run_id": run.id,
                "workflow_name": run.name,
                "branch": run.head_branch,
                "commit_sha": run.head_sha,
                "html_url": run.html_url,
                "created_at": str(run.created_at),
            }
    return {}


@tool
def get_failure_logs(repo_name: str, run_id: int, max_chars: int = 8000) -> str:
    """
    Download and extract the log output for a failed GitHub Actions run,
    returning only the tail of the logs (where failures usually appear)
    trimmed to a manageable size for reasoning.

    Args:
        repo_name: Repository in "owner/repo" format
        run_id: The workflow run ID to fetch logs for
        max_chars: Max characters to return per failed job's log (default 8000)

    Returns:
        Concatenated failure-relevant log text across all failed jobs in the run.
    """
    token = get_token_for_repo(repo_name)
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)
    run = repo.get_workflow_run(run_id)

    logs_url = f"https://api.github.com/repos/{repo_name}/actions/runs/{run_id}/logs"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(logs_url, headers=headers, timeout=30)
    resp.raise_for_status()

    zip_bytes = io.BytesIO(resp.content)
    output = []

    with zipfile.ZipFile(zip_bytes) as zf:
        failed_job_names = set()
        for job in run.jobs():
            if job.conclusion == "failure":
                failed_job_names.add(job.name)

        for name in zf.namelist():
            job_part = name.split("/")[0].replace(".txt", "")
            if any(fj in job_part or job_part in fj for fj in failed_job_names):
                with zf.open(name) as f:
                    content = f.read().decode("utf-8", errors="replace")
                    tail = content[-max_chars:] if len(content) > max_chars else content
                    output.append(f"=== {name} ===\n{tail}")

    if not output:
        return "No failure logs found for this run."

    return "\n\n".join(output)


@tool
def get_run_diff(repo_name: str, commit_sha: str) -> str:
    """
    Get the code diff for the commit that triggered a failed run.

    Args:
        repo_name: Repository in "owner/repo" format
        commit_sha: The commit SHA that triggered the run

    Returns:
        A unified diff string of the changes in that commit.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)
    commit = repo.get_commit(commit_sha)

    diff_parts = []
    for f in commit.files:
        diff_parts.append(f"File: {f.filename} (+{f.additions}/-{f.deletions})")
        if f.patch:
            diff_parts.append(f.patch)

    return "\n".join(diff_parts) if diff_parts else "No diff data available."