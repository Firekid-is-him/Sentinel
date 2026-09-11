"""
Action tools for Sentinel.
These are the "hands" of the agent — used only after diagnosis and
classification are complete. auto_fix_dependency opens a real PR for
safe, well-understood fixes. escalate_to_human posts a precise triaged
summary instead of raw logs, for anything that needs a judgment call.
"""

from strands import tool
from token_registry import get_client_for_repo


@tool
def rerun_workflow(repo_name: str, run_id: int, reasoning: str) -> dict:
    """
    Re-trigger a specific GitHub Actions workflow run. Use this ONLY when
    check_flaky_signal has confirmed the same commit already has at least
    one passing run — meaning the failure is very likely non-deterministic
    flakiness, not a real regression. This is explicitly NOT a substitute
    for real diagnosis: never call this just because a fix isn't obvious.
    Per industry best practice, a blind retry without confirming the
    flaky signal first just masks real bugs.

    Args:
        repo_name: Repository in "owner/repo" format
        run_id: The workflow run ID to re-trigger
        reasoning: Why this was judged flaky (cite the pass/fail counts
            from check_flaky_signal)

    Returns:
        A dict confirming the rerun was requested, or an error message.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)

    try:
        run = repo.get_workflow_run(run_id)
        run.rerun()
        return {
            "status": "rerun_triggered",
            "run_id": run_id,
            "html_url": run.html_url,
            "reasoning": reasoning,
        }
    except Exception as e:
        return {"error": f"Could not rerun workflow: {e}"}


@tool
def open_dependency_fix_pr(
    repo_name: str,
    base_branch: str,
    pubspec_path: str,
    package_name: str,
    new_constraint: str,
    reasoning: str,
) -> dict:
    """
    Open a pull request that updates a single dependency constraint in
    pubspec.yaml (or a similar manifest) to resolve a version-solving
    failure. Use this ONLY for safe, well-understood dependency conflicts
    where the fix is a straightforward constraint change — not for
    logic bugs or API signature mismatches, which need human review.

    Args:
        repo_name: Repository in "owner/repo" format
        base_branch: The branch to base the fix on (e.g. "main")
        pubspec_path: Path to the manifest file, e.g. "pubspec.yaml"
        package_name: The dependency being updated, e.g. "flutter_quill_extensions"
        new_constraint: The new version constraint, e.g. "^11.0.0"
        reasoning: A short explanation of why this fix is safe, included in the PR body

    Returns:
        A dict with the PR number and html_url, or an error message.
    """
    gh = get_client_for_repo(repo_name)
    repo = gh.get_repo(repo_name)

    try:
        file = repo.get_contents(pubspec_path, ref=base_branch)
        content = file.decoded_content.decode("utf-8")
    except Exception as e:
        return {"error": f"Could not read {pubspec_path}: {e}"}

    updated_lines = []
    changed = False
    for line in content.splitlines():
        if package_name in line and ":" in line and not changed:
            indent = line[: len(line) - len(line.lstrip())]
            updated_lines.append(f"{indent}{package_name}: {new_constraint}")
            changed = True
        else:
            updated_lines.append(line)

    if not changed:
        return {"error": f"Could not find '{package_name}' in {pubspec_path}"}

    updated_content = "\n".join(updated_lines) + "\n"

    branch_name = f"sentinel/fix-{package_name.replace('_', '-')}-{new_constraint.strip('^')}"
    base_ref = repo.get_git_ref(f"heads/{base_branch}")

    # Delete any stale branch from a prior run first, so we always start
    # from a clean, current copy of base_branch rather than silently
    # reusing an old branch that may be out of date or already merged/closed.
    try:
        old_ref = repo.get_git_ref(f"heads/{branch_name}")
        old_ref.delete()
    except Exception:
        pass  # branch didn't exist yet, which is the normal case

    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)

    # Re-read the file from base_branch fresh, since the earlier read's sha
    # could be stale if time has passed since this function started.
    file = repo.get_contents(pubspec_path, ref=base_branch)

    repo.update_file(
        path=pubspec_path,
        message=f"fix: update {package_name} constraint to {new_constraint}",
        content=updated_content,
        sha=file.sha,
        branch=branch_name,
    )

    pr_body = (
        f"## Sentinel auto-fix\n\n"
        f"**What changed:** `{package_name}` constraint updated to `{new_constraint}`\n\n"
        f"**Why:** {reasoning}\n\n"
        f"This is an autonomous fix for a version-solving failure detected in CI. "
        f"The change is limited to a single dependency constraint and was classified "
        f"as safe to apply without human review. Please verify CI passes before merging."
    )

    try:
        pr = repo.create_pull(
            title=f"Sentinel: fix {package_name} version conflict",
            body=pr_body,
            head=branch_name,
            base=base_branch,
        )
    except Exception as e:
        # Don't silently hide this — a failure here means no PR exists
        # despite the branch/commit having been created, which needs to
        # be visible, not swallowed.
        return {
            "error": f"Branch and commit were created, but opening the PR failed: {e}",
            "branch_name": branch_name,
        }

    return {"pr_number": pr.number, "html_url": pr.html_url}


@tool
def escalate_to_human(
    repo_name: str,
    run_html_url: str,
    failure_summary: str,
    root_cause: str,
    suggested_options: str,
    confidence: str = "high",
) -> dict:
    """
    Post a precise, triaged summary of a build failure that requires human
    judgment, instead of leaving raw logs for someone to dig through. Use
    this for logic bugs, API signature mismatches, or anything where the
    correct fix isn't obvious or safe to apply automatically.

    Args:
        repo_name: Repository in "owner/repo" format
        run_html_url: Link to the failed workflow run
        failure_summary: A one or two sentence summary of what broke
        root_cause: The specific root cause identified from the logs/diff
        suggested_options: The judgment call the human needs to make, phrased as options
        confidence: How certain you are in this root cause diagnosis —
            "high" if the log/diff directly names the cause, "medium" if
            it's a strong but not certain inference, "low" if you're
            genuinely unsure and the human should verify from scratch.
            Defaults to "high"; only lower this when genuinely warranted.

    Returns:
        A dict confirming the escalation was recorded (in a full deployment
        this would post to Slack/Discord/GitHub issue; here it returns the
        structured summary for the demo).
    """
    confidence_label = {
        "high": "🟢 High confidence",
        "medium": "🟡 Medium confidence",
        "low": "🟠 Low confidence — please verify",
    }.get(confidence.lower(), "🟢 High confidence")

    summary = (
        f"🔴 **Sentinel escalation — human judgment needed**\n\n"
        f"**Repo:** {repo_name}\n"
        f"**Run:** {run_html_url}\n\n"
        f"**What broke:** {failure_summary}\n\n"
        f"**Root cause** ({confidence_label}): {root_cause}\n\n"
        f"**Your call:** {suggested_options}"
    )

    return {"status": "escalated", "summary": summary, "confidence": confidence}