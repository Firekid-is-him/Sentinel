# Sentinel

Sentinel is an autonomous agent that watches your GitHub Actions CI/CD pipelines, diagnoses build failures, and takes the correct action on its own: it opens a fix pull request when the fix is safe and well understood, retries the workflow when the failure looks like flakiness rather than a real problem, or escalates to a human with a precise summary and a stated confidence level when the situation genuinely needs a judgment call.

It is built with the Strands Agents SDK and is model agnostic. See [Models configuration](#models-configuration) for the full list of supported providers and how to switch between them.

## Why this exists

Anyone running CI/CD across more than a couple of repositories knows the pattern: a build turns red, and someone has to stop what they are doing, open the logs, scroll through noise, and figure out whether this is a real bug, a dependency conflict, or just a flaky test. Most of that triage does not need a human. It needs someone to read the logs and make the obvious call. Sentinel exists so a human only gets pulled in for the decision that actually needs one.

## What it does

Sentinel is triggered either automatically, when a watched repository's GitHub Actions workflow fails, or manually through a GitHub Actions workflow dispatch. Once triggered, it works through a fixed reasoning process:

1. Identifies the failing run and pulls its logs.
2. Reads the diff of the commit that triggered the failure.
3. Checks whether the same commit has both a passing and a failing run recorded, which is the standard signal for flakiness rather than a genuine regression.
4. If the failure is flaky, it re-triggers the workflow and stops there. It does not open a fix PR or escalate for a flaky failure, since both would be the wrong response.
5. If the failure is a dependency version conflict where the log itself names the exact fix, it opens a pull request that applies that fix directly.
6. If the failure requires judgment, such as an API signature mismatch or a genuine logic change, it escalates with the root cause, a stated confidence level, and the specific decision a human needs to make. It never just forwards raw logs.
7. Separately, it checks whether the same commit made the project's documentation stale, and opens a documentation fix if it can identify the exact outdated text with confidence.

Every run is logged to `runs.json`, which a small static dashboard (`dashboard.html`) reads to show run history, classification breakdown, and links to whatever was opened or reported.

## Architecture

```
GitHub Actions run fails
        |
        v
  Sentinel agent (Strands Agents SDK)
        |
   -----------------
   |               |
Fetch failed     Read failure
run + diff          logs
   |               |
   -----------------
        |
        v
  Check flaky signal
        |
   -------------------------------
   |            |                |
 Flaky      Auto-fixable      Needs judgment
   |            |                |
Retry       Open fix PR      Escalate with
workflow                     confidence level
```

GitHub's REST API, through PyGithub, connects every step to the real repository being watched.

## Models configuration

Sentinel does not depend on a single model provider. `model_config.py` reads `SENTINEL_MODEL_PROVIDER` from the environment and configures the right client. No other file needs to change when switching providers.

Core providers, tested directly against this project:

| Provider | Value | Required environment variables |
|----------|-------|-------------------------------|
| Gemini (default) | `gemini` | `GEMINI_API_KEY` |
| Amazon Bedrock | `bedrock` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` (optional, defaults to `us-east-1`) |
| Anthropic API | `anthropic` | `ANTHROPIC_API_KEY` |

Extended providers, using Strands' first-party OpenAI and Ollama support. Groq is included here because it exposes an OpenAI-compatible API endpoint, so Sentinel reuses the same OpenAI provider pointed at Groq's endpoint rather than a separate integration. These have not been run end to end against this project's own test cases, unlike the three core providers above.

| Provider | Value | Required environment variables |
|----------|-------|-------------------------------|
| Groq | `groq` | `GROQ_API_KEY`, plus optionally `SENTINEL_MODEL_ID` (defaults to `llama-3.3-70b-versatile`) |
| OpenAI | `openai` | `OPENAI_API_KEY`, plus optionally `SENTINEL_MODEL_ID` (defaults to `gpt-4o`) |
| Ollama (local) | `ollama` | None required. Optionally `OLLAMA_HOST` (defaults to `http://localhost:11434`) and `SENTINEL_MODEL_ID` (defaults to `llama3`) |

Switching providers requires no code changes, only a different value for `SENTINEL_MODEL_PROVIDER` and the matching credentials.

## Multi-repository and multi-account support

Sentinel can watch several repositories at once, including repositories that live under different GitHub accounts. Each repository is paired with its own token, so a single Sentinel instance can authenticate correctly across accounts it does not otherwise share credentials between.

## Setup

### Requirements

- Python 3.10 or later
- A GitHub personal access token (fine grained) with Actions read, Contents read and write, and Pull requests read and write permissions, for each account you want Sentinel to act on
- Credentials for whichever model provider you choose, from the table above

### Generating a GitHub token with the right permissions

Sentinel needs a fine grained personal access token for each GitHub account it acts on, scoped to the specific permissions it uses.

1. Go to `github.com/settings/tokens?type=beta` while signed into the account that owns the repository.
2. Click "Generate new token".
3. Under "Repository access", choose "Only select repositories" and pick the specific repository (or repositories) you want Sentinel to watch on this account.
4. Under "Permissions", set the following, leaving everything else at "No access":
   - Actions: Read-only
   - Contents: Read and write
   - Pull requests: Read and write
   - Metadata: Read-only (this is usually selected automatically once the others are set)
5. Click "Generate token" and copy it immediately. It is shown only once.

Repeat this once per GitHub account you want Sentinel to watch, since a token only has access to the account it was generated under. Each token then goes into its own `SENTINEL_TOKEN_N` variable, paired with the matching `SENTINEL_REPO_N` for that account, as shown in the Configure section below.

### Install

```
pip install strands-agents strands-agents-tools PyGithub requests python-dotenv
pip install "strands-agents[gemini]"
```

Swap the last line for the appropriate extra depending on your chosen provider: `pip install boto3` for Bedrock, `pip install anthropic` for the Anthropic API directly, `pip install "strands-agents[openai]"` for OpenAI or Groq (both use the same OpenAI provider), or `pip install "strands-agents[ollama]"` for a local Ollama model.

### Configure

Create a file named `.env` in the project root and paste in the block below, then fill in your real values. `.env` is gitignored and must never be committed, so there is no example file checked into this repository, this block is the template.

```
SENTINEL_MODEL_PROVIDER=gemini
SENTINEL_MODEL_ID=

GEMINI_API_KEY=
ANTHROPIC_API_KEY=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=
GROQ_API_KEY=
OPENAI_API_KEY=
OLLAMA_HOST=

SENTINEL_REPO_1=owner1/repo1
SENTINEL_TOKEN_1=

SENTINEL_REPO_2=owner2/repo2
SENTINEL_TOKEN_2=

GITHUB_TOKEN=
```

Only fill in the variables that apply to your chosen model provider and the repositories you are actually watching. `SENTINEL_MODEL_ID` only needs a value if you want to override the default model for your chosen provider; each provider falls back to a sensible default if left blank. Add as many numbered `SENTINEL_REPO_N` and `SENTINEL_TOKEN_N` pairs as needed, one per repository, each with the token for whichever GitHub account owns it. `GITHUB_TOKEN` at the bottom is an optional fallback token used only for a repository that has no matching numbered pair.

### Run

```
python3 agent_v2.py
```

This checks every configured repository for a current failure and triages whatever it finds. If more than one repository has a failure, it triages all of them in the same run.

### View the dashboard

```
python3 -m http.server 8000
```

Then open the dashboard through that server, since it reads `runs.json` over HTTP rather than the local filesystem.

## Automatic triggering

`sentinel-trigger.yml` is a GitHub Actions workflow you can add to any repository you want Sentinel to watch. It listens for that repository's own workflow runs and, when one fails, checks out Sentinel's code and runs it automatically, with the exact failing run already identified. It also supports manual triggering through the Actions tab, with an optional specific run ID.

To use it, add the file to `.github/workflows/` in the repository you want watched, and add a `GEMINI_API_KEY` secret to that repository, since Gemini is the default provider for the triggered workflow. To use a different provider instead, add a `SENTINEL_MODEL_PROVIDER` repository variable and the matching secret, following the same table in Models configuration above.

## Project structure

```
agent_v2.py           Main agent: system prompt, tool wiring, driver logic
model_config.py        Model provider configuration
token_registry.py       Multi-account token and repository registry
github_tools.py         Fetching failed runs, logs, and diffs
action_tools.py         Opening fix PRs, escalating, retrying workflows
doc_drift_tools.py      Detecting and fixing stale documentation
flaky_tools.py          Flakiness detection
multi_repo_tools.py     Checking multiple repositories, logging run results
dashboard.html          Static run history viewer
runs.json               Run history, written by agent_v2.py
sentinel-trigger.yml    GitHub Actions workflow for automatic triggering
```

## Limitations

Sentinel currently only opens a dependency fix PR when the failing tool's own error message names the exact correct constraint. It does not attempt to guess a fix when the correct change is ambiguous, since a wrong automated fix is worse than an extra escalation. Documentation drift detection is a heuristic based on identifier overlap between a diff and the documentation file, not full static analysis, so it can miss subtler cases of staleness. The automatic trigger workflow checks out and installs Sentinel's dependencies on every run, which adds a short setup delay before triage begins.

## License

MIT

## Author

Ahmed Ramadan Ayomide (Firekid)
