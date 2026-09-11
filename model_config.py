"""
Model configuration for Sentinel.
Switch providers by changing SENTINEL_MODEL_PROVIDER in your environment,
or just edit the default below. No other files need to change.

Core providers, tested directly against this project: "gemini", "bedrock",
"anthropic".

Extended providers, using Strands' first-party OpenAI and Ollama support,
not LiteLLM: "openai" and "groq" (Groq exposes an OpenAI-compatible
endpoint, so it reuses the same OpenAIModel with a different base_url),
and "ollama" for local models. These have not been run end to end against
this project's own test cases, unlike the three core providers above.
"""

import os


def get_model():
    """
    Returns a configured Strands model instance based on
    SENTINEL_MODEL_PROVIDER env var (defaults to "gemini",
    since it's the provider proven working without billing friction).
    """
    provider = os.environ.get("SENTINEL_MODEL_PROVIDER", "gemini").lower()

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "SENTINEL_MODEL_PROVIDER is 'gemini' but GEMINI_API_KEY is not "
                "set. Add it to .env locally, or as a repository secret named "
                "GEMINI_API_KEY if running through GitHub Actions."
            )
        from strands.models.gemini import GeminiModel
        return GeminiModel(
            client_args={"api_key": api_key},
            model_id=os.environ.get("SENTINEL_MODEL_ID", "gemini-3.6-flash"),
            params={"temperature": 0.3},
        )

    elif provider == "bedrock":
        if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
            raise ValueError(
                "SENTINEL_MODEL_PROVIDER is 'bedrock' but AWS credentials are "
                "not set. Run `aws configure` locally, or set AWS_ACCESS_KEY_ID "
                "and AWS_SECRET_ACCESS_KEY as repository secrets if running "
                "through GitHub Actions."
            )
        from strands.models import BedrockModel
        return BedrockModel(
            model_id=os.environ.get(
                "SENTINEL_MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0"
            ),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "SENTINEL_MODEL_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY "
                "is not set. Add it to .env locally, or as a repository secret "
                "named ANTHROPIC_API_KEY if running through GitHub Actions."
            )
        from strands.models.anthropic import AnthropicModel
        return AnthropicModel(
            model_id=os.environ.get("SENTINEL_MODEL_ID", "claude-haiku-4-5-20251001"),
            client_args={"api_key": api_key},
        )

    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "SENTINEL_MODEL_PROVIDER is 'openai' but OPENAI_API_KEY is "
                "not set. Add it to .env locally, or as a repository secret "
                "named OPENAI_API_KEY if running through GitHub Actions."
            )
        from strands.models.openai import OpenAIModel
        return OpenAIModel(
            client_args={"api_key": api_key},
            model_id=os.environ.get("SENTINEL_MODEL_ID", "gpt-4o"),
            params={"temperature": 0.3},
        )

    elif provider == "groq":
        # Groq exposes an OpenAI-compatible endpoint, so this reuses
        # Strands' first-party OpenAI provider pointed at Groq's base_url,
        # rather than a separate Groq-specific integration.
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError(
                "SENTINEL_MODEL_PROVIDER is 'groq' but GROQ_API_KEY is not "
                "set. Add it to .env locally, or as a repository secret "
                "named GROQ_API_KEY if running through GitHub Actions."
            )
        from strands.models.openai import OpenAIModel
        return OpenAIModel(
            client_args={
                "api_key": api_key,
                "base_url": "https://api.groq.com/openai/v1",
            },
            model_id=os.environ.get("SENTINEL_MODEL_ID", "llama-3.3-70b-versatile"),
            params={"temperature": 0.3},
        )

    elif provider == "ollama":
        from strands.models.ollama import OllamaModel
        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("SENTINEL_MODEL_ID", "llama3"),
        )

    else:
        raise ValueError(
            f"Unknown SENTINEL_MODEL_PROVIDER: '{provider}'. "
            f"Supported: gemini, bedrock, anthropic, openai, groq, ollama."
        )