"""
Multi-repo tools for Sentinel, plus run logging that feeds the dashboard.
"""

import os
import json
from datetime import datetime, timezone
from strands import tool
from token_registry import get_client_for_repo


@tool
def find_repos_with_failures(repo_names: list) -> list:
    """
    Check a list of repos and return which ones currently have a failed
    GitHub Actions run, so Sentinel can decide which to triage next.
    Ignores failures in Sentinel's own trigger workflow, and sorts by
    actual recency across all of a repo's workflows, not just the first
    failure encountered in an arbitrary API ordering.

    Args:
        repo_names: List of repos in "owner/repo" format to check

    Returns:
        A list of dicts, one per repo that has a failure, each with
        repo_name, run_id, workflow_name, and html_url, for that repo's
        single most recent failure. Repos with no failures are omitted.
    """
    results = []

    for repo_name in repo_names:
        try:
            gh = get_client_for_repo(repo_name)
            repo = gh.get_repo(repo_name)
            runs = repo.get_workflow_runs(status="completed")

            most_recent_failure = None
            checked = 0
            for run in runs:
                checked += 1
                if checked > 50:
                    # Safety cap: don't scan a repo's entire run history
                    break
                if run.conclusion != "failure":
                    continue
                if run.name == "Sentinel Auto-Triage":
                    # Never treat Sentinel's own trigger workflow as
                    # something for Sentinel itself to triage.
                    continue
                if most_recent_failure is None or run.created_at > most_recent_failure.created_at:
                    most_recent_failure = run

            if most_recent_failure:
                results.append({
                    "repo_name": repo_name,
                    "run_id": most_recent_failure.id,
                    "workflow_name": most_recent_failure.name,
                    "html_url": most_recent_failure.html_url,
                })
        except Exception:
            continue

    return results


def log_run_result(
    repo_name: str,
    classification: str,
    summary: str,
    action_url: str = "",
    watched_repos: list = None,
    log_path: str = "runs.json",
) -> None:
    """
    Append a Sentinel run result to the JSON log the dashboard reads from.
    Not a Strands @tool — called directly by the driver script after the
    agent finishes, so every run is logged exactly once regardless of
    how the agent phrases its response.

    watched_repos, if provided, overwrites the stored list of configured
    repos (distinct from which repos have actual run history) so the
    dashboard can show "watching N repos" accurately even if some of
    them haven't had a failure yet.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_name": repo_name,
        "classification": classification,  # "auto_fixed" or "escalated"
        "summary": summary,
        "action_url": action_url,
    }

    data = {"watched_repos": [], "runs": []}
    if os.path.exists(log_path):
        try:
            with open(log_path, "r") as f:
                loaded = json.load(f)
            # Support migrating from the old flat-array format
            if isinstance(loaded, list):
                data = {"watched_repos": [], "runs": loaded}
            elif isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    data["runs"].append(entry)
    if watched_repos:
        data["watched_repos"] = watched_repos

    with open(log_path, "w") as f:
        json.dump(data, f, indent=2)
