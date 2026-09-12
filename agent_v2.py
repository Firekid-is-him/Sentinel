"""
Sentinel v2 — autonomous CI/CD + docs triage agent.

Watches GitHub Actions across multiple repos, diagnoses build failures,
detects doc/API drift, and either fixes safely or escalates with a
precise summary. Model-agnostic (Gemini / Bedrock / Anthropic via
model_config.py). Logs every run to runs.json for the dashboard.

Built with the Strands Agents SDK for the Agents for Humans Hackathon
(Professional Agents track).
"""

import os
import json
import time
from strands import Agent

from model_config import get_model
from github_tools import get_latest_failed_run, get_failure_logs, get_run_diff
from action_tools import open_dependency_fix_pr, escalate_to_human, rerun_workflow
from doc_drift_tools import check_doc_drift, open_doc_update_pr
from multi_repo_tools import find_repos_with_failures, log_run_result
from token_registry import get_watched_repos
from flaky_tools import check_flaky_signal


SYSTEM_PROMPT = """You are Sentinel, an autonomous CI/CD and documentation
triage agent.

Your job: when a GitHub Actions build fails, diagnose the root cause from
the logs and the triggering commit's diff, classify the failure, and take
the correct action — without making a human dig through raw logs unless
they genuinely need to make a judgment call. You also check whether the
triggering commit made the project's documentation stale.

## Classification rules for build failures

Before classifying as auto-fixable or needing judgment, ALWAYS first call
check_flaky_signal on the triggering commit. This is a real, checkable
fact (does this exact commit have both a pass and a fail?), not a guess,
and it must be checked before the other two paths below because it
changes which path is correct.

**FLAKY — use rerun_workflow:**
- check_flaky_signal returns is_flaky=true (the same commit has both a
  passing and a failing run). This means the code is very likely fine
  and the failure was non-deterministic. Per industry best practice, do
  NOT open a fix PR and do NOT escalate to a human for this case — both
  would be wrong and wasteful. Just call rerun_workflow, citing the
  pass/fail counts as your reasoning.

**AUTO-FIXABLE — use open_dependency_fix_pr:**
- check_flaky_signal returned is_flaky=false, AND the failure is a
  dependency version-solving failure where the log itself names the exact
  fix (e.g. "Because X depends on Y ^A which doesn't match any versions...
  Consider downgrading your constraint on Y: flutter pub add Y:^B").
  This is safe because the tool that failed already told you the precise,
  correct constraint change — there's no ambiguity or design decision involved.

**NEEDS HUMAN JUDGMENT — use escalate_to_human:**
- check_flaky_signal returned is_flaky=false, AND it's an API signature
  mismatch (a function call uses a named parameter that no longer exists
  on the target class/method). This requires a human decision: update the
  calling code to the new API, or pin the dependency back to a version
  that has the old API.
- Logic bugs, test failures that reflect an actual behavior change, or
  anything where the "fix" would require choosing between multiple
  reasonable interpretations of intent.
- Anything you are not highly confident about. When in doubt, escalate —
  a wrong auto-fix is worse than an extra ping to a human.

When calling escalate_to_human, set the confidence parameter honestly —
"high" only when the log or diff directly names the cause, "medium" for
a strong inference, "low" if you're genuinely unsure. Don't default to
"high" out of habit; an honest "medium" is more useful to the human than
false certainty.

## Documentation drift

After resolving (or alongside triaging) a build failure, if the triggering
commit changed code, call check_doc_drift on that commit. If drift_detected
is true, review the doc_excerpt and, if you can identify the exact stale
text and its correction with high confidence, call open_doc_update_pr. If
you are not confident about the exact replacement text, do not guess —
mention the possible drift in your final summary instead of taking action.

## Process

Follow these steps in order, calling each tool exactly once per repo being
triaged. Do not repeat a tool call you have already made for the same repo.

1. Identify the failing run(s):
   - If the user's message names ONE specific repo, call get_latest_failed_run
     on it directly. Do NOT call find_repos_with_failures in this case.
   - If the user's message provides a LIST of multiple repos, call
     find_repos_with_failures ONCE with that full list. It returns EVERY
     repo that currently has a failure — not just one. If it returns
     multiple repos, you must triage ALL of them in this same response,
     one at a time, repeating steps 2-6 for each. Do not stop after the
     first one. Do NOT call get_latest_failed_run for any repo already
     covered by find_repos_with_failures's results — you already have
     its run_id and commit info.
2. For each failing repo, call get_failure_logs once, using its run_id.
3. For each failing repo, call get_run_diff once, using its commit_sha.
4. For each failing repo, call check_flaky_signal once, using its
   commit_sha AND its workflow_name (from the same result that gave you
   the run_id and commit_sha in step 1). Never omit workflow_name and
   never guess it — using the wrong or no workflow name will count an
   unrelated workflow's runs toward this commit's pass or fail count,
   which can wrongly call a real, repeatable failure flaky.
5. For each failing repo, classify the build failure using the rules above
   (flaky check first) and take exactly one action: rerun_workflow,
   open_dependency_fix_pr, or escalate_to_human. After calling
   open_dependency_fix_pr, open_doc_update_pr, or rerun_workflow, check
   the tool's return value before reporting success. If it contains an
   "error" key, your summary MUST say the action failed and state the
   exact error returned. Never report a pull request or rerun as
   completed unless the tool's return value actually contains a real
   pr_number, html_url, or success confirmation. Reporting success when
   the tool returned an error is worse than reporting a failure clearly.
6. For each failing repo, only if its classification was NOT flaky, call
   check_doc_drift once on its commit. Act on it only if confident. Skip
   this step for any repo whose failure was flaky.
7. After triaging every failing repo, end your response with one
   classification line PER REPO, each on its own line, in this exact
   format so they can be parsed:
   CLASSIFICATION[owner/repo]: auto_fixed | escalated | flaky_retried

You have a strict tool-call budget. Never call the same tool twice with
the same arguments for the same repo. If you are unsure what to do next
for a given repo, proceed to classification for that repo with whatever
information you already have rather than re-checking.

Be precise and concise. Your value is turning a wall of log noise into
either a done fix or a five-second decision for a human.
"""


def build_agent() -> Agent:
    return Agent(
        model=get_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_latest_failed_run,
            get_failure_logs,
            get_run_diff,
            check_flaky_signal,
            rerun_workflow,
            open_dependency_fix_pr,
            escalate_to_human,
            check_doc_drift,
            open_doc_update_pr,
            find_repos_with_failures,
        ],
    )


def _extract_classifications(result_text: str) -> dict:
    """
    Parses one or more CLASSIFICATION[owner/repo]: value lines from the
    agent's response. Returns a dict of repo_name -> classification.
    """
    import re
    results = {}
    pattern = r"CLASSIFICATION\[([^\]]+)\]:\s*(\S+)"
    for match in re.finditer(pattern, result_text):
        repo_name = match.group(1).strip()
        raw_value = match.group(2).strip().lower()
        if "flaky" in raw_value:
            results[repo_name] = "flaky_retried"
        elif "auto" in raw_value:
            results[repo_name] = "auto_fixed"
        elif "escalat" in raw_value:
            results[repo_name] = "escalated"
        else:
            results[repo_name] = "unknown"
    return results


def _extract_action_urls(result_text: str) -> list:
    import re
    return re.findall(r"https://github\.com/\S+/pull/\d+", result_text)


if __name__ == "__main__":
    # Repos are now sourced from SENTINEL_REPO_N / SENTINEL_TOKEN_N pairs
    # (via .env locally, or GitHub Actions Secrets in CI). Falls back to
    # SENTINEL_REPOS (comma-separated) + GITHUB_TOKEN for simple single-account setups.
    repo_list = get_watched_repos()
    if not repo_list:
        repos_env = os.environ.get("SENTINEL_REPOS", "idc-what-u-think/notebook")
        repo_list = [r.strip() for r in repos_env.split(",")]

    agent = build_agent()

    if len(repo_list) > 1:
        prompt = (
            f"Check these repos for failures: {repo_list}. "
            f"Use find_repos_with_failures first. If it returns MORE THAN "
            f"ONE repo with a failure, triage every single one in this "
            f"response, not just the first — do not stop after one."
        )
    else:
        triggered_run_id = os.environ.get("SENTINEL_TRIGGERED_RUN_ID")
        if triggered_run_id:
            # Triggered by a GitHub Actions workflow_run event — the exact
            # failing run is already known, so skip searching for it.
            prompt = (
                f"Triage GitHub Actions run {triggered_run_id} in repo "
                f"{repo_list[0]}. Call get_failure_logs directly with this "
                f"run_id (skip get_latest_failed_run since we already know "
                f"the run_id). Follow your process from step 2 onward."
            )
        else:
            prompt = (
                f"Check {repo_list[0]} for the latest failed GitHub Actions "
                f"run and triage it following your process."
            )

    max_retries = 3
    result_text = ""
    for attempt in range(max_retries):
        try:
            result = agent(prompt)
            result_text = str(result)
            break
        except Exception as e:
            error_str = str(e)
            print(f"Attempt {attempt + 1} failed with: {type(e).__name__}: {error_str}")
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait = 35 * (attempt + 1)
                print(f"Rate limited, waiting {wait}s before retry "
                      f"({attempt + 1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise
    else:
        print("Failed after retries due to rate limiting. Try again shortly, "
              "or switch SENTINEL_MODEL_PROVIDER to reduce load.")
        raise SystemExit(1)

    print(result_text)

    per_repo_classifications = _extract_classifications(result_text)
    action_urls = _extract_action_urls(result_text)

    if per_repo_classifications:
        # Multi-repo case (or single repo, if the agent used the tagged
        # format anyway): log one entry per repo actually triaged.
        for i, (repo_name, classification) in enumerate(per_repo_classifications.items()):
            action_url = action_urls[i] if i < len(action_urls) else ""
            log_run_result(
                repo_name=repo_name,
                classification=classification,
                summary=result_text[:500],
                action_url=action_url,
                watched_repos=repo_list,
            )
    else:
        # Fallback: no tagged classification lines found at all. Log
        # against the first repo with an honest "unknown" rather than
        # silently mislabeling which repo was actually triaged.
        log_run_result(
            repo_name=repo_list[0],
            classification="unknown",
            summary=result_text[:500],
            action_url=action_urls[0] if action_urls else "",
            watched_repos=repo_list,
        )
