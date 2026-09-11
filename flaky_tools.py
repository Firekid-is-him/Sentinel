"""
Flaky test detection for Sentinel.

Uses the industry-standard detection signal (the same one used by
production tools like Harness CI): a test/workflow is flaky if the
SAME commit produced BOTH a passing run and a failing run within a
recent observation window. That's a checkable fact from GitHub's own
run history — not a guess, and not something that needs a fabricated
test case to validate.

Reference pattern: https://developer.harness.io/docs/continuous-integration/
use-ci/run-tests/test-management/ci-flaky-tests
"""

from strands import tool
from token_registry import get_client_for_repo


@tool
def check_flaky_signal(repo_name: str, commit_sha: str, workflow_name: str = None) -> dict:
    """
    Check whether the given commit has BOTH a passing and a failing run
    for the same workflow, which is the standard signal for flakiness
    rather than a genuine regression. Looks at the most recent runs for
    this commit only — not older commits, so this never confuses a real
    fix (different commit) with a flaky flip (same commit).

    Args:
        repo_name: Repository in "owner/repo" format
        commit_sha: The commit SHA to check across multiple runs
        workflow_name: Optional workflow name to narrow the check to one
            workflow (recommended if the repo has multiple workflows)

    Returns:
        A dict with 'is_flaky' (bool), 'pass_count' and 'fail_count' for
        this commit, and 'run_urls' for the runs found, so a human can
        verify if they want to.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)

    all_runs = repo.get_workflow_runs(status="completed")

    matching_runs = []
    for run in all_runs:
        if run.head_sha != commit_sha:
            continue
        if workflow_name and run.name != workflow_name:
            continue
        matching_runs.append(run)
        if len(matching_runs) >= 10:
            break

    pass_count = sum(1 for r in matching_runs if r.conclusion == "success")
    fail_count = sum(1 for r in matching_runs if r.conclusion == "failure")

    is_flaky = pass_count > 0 and fail_count > 0

    return {
        "is_flaky": is_flaky,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "run_urls": [r.html_url for r in matching_runs],
    }