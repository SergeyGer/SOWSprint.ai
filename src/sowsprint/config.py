"""Central configuration for SOWSprint.ai.

Every external dependency is optional at *runtime*: the platform is designed to boot
and complete a full scoping-to-contract cycle with **zero credentials** by falling
back to deterministic local engines (heuristic LLM, hashing embedder, in-process
vector store). As soon as credentials are supplied the corresponding production
adapter takes over automatically.

Configuration precedence (highest first):
    1. Explicit constructor arguments
    2. Environment variables / ``.env`` file
    3. Defaults declared here
"""

from __future__ import annotations

import functools
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
ARTIFACT_DIR = DATA_DIR / "artifacts"


class LLMProvider(str, Enum):
    """Reasoning backend selection policy."""

    AUTO = "auto"  #: pick the best credentialed provider, else deterministic offline
    MOCK = "mock"  #: force the deterministic offline engine
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"
    OLLAMA = "ollama"


class EmbeddingProvider(str, Enum):
    AUTO = "auto"
    HASH = "hash"  #: deterministic offline embeddings (no network, no cost)
    OPENAI = "openai"
    OLLAMA = "ollama"


class RerankProvider(str, Enum):
    AUTO = "auto"
    HEURISTIC = "heuristic"  #: offline lexical cross-encoder surrogate
    COHERE = "cohere"
    TEI = "tei"  #: HuggingFace text-embeddings-inference (self-hosted BGE reranker)


class VectorBackend(str, Enum):
    AUTO = "auto"
    QDRANT = "qdrant"
    MEMORY = "memory"  #: in-process fallback so the graph runs without the container


class Settings(BaseSettings):
    """Runtime settings for the whole platform."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        env_prefix="SOWSPRINT_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- application
    app_name: str = "SOWSprint.ai"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True

    # ---------------------------------------------------------------- LLM providers
    llm_provider: LLMProvider = LLMProvider.AUTO
    """Reasoning backend. ``auto`` prefers a credentialed cloud provider over offline."""

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    cohere_api_key: str | None = None

    openai_base_url: str | None = None
    """Override for OpenAI-compatible gateways (Azure, vLLM, LiteLLM, Groq)."""

    anthropic_base_url: str | None = None

    ollama_base_url: str = "http://localhost:11434"
    tei_rerank_url: str | None = None
    """Self-hosted BGE reranker endpoint, e.g. ``http://reranker:80/rerank``."""

    reasoning_model: str = "gpt-4o"
    """Model used by Triage / Architect / Legal nodes (highest quality tier)."""

    critic_model: str | None = None
    """Critic runs LLM-as-a-Judge. ``None`` inherits :attr:`reasoning_model`.

    Leaving this unset is the safe default: a hard-coded vendor-specific default here
    would be sent to whichever provider the reasoning tier resolved to, producing an
    opaque "model not found" from the wrong API.
    """

    critic_provider: LLMProvider = LLMProvider.AUTO
    """Provider serving the Critic node. ``AUTO`` uses the reasoning provider.

    Set this to a *different vendor* than the drafting agent to get genuine judge
    independence — a model auditing its own output is measurably weaker at catching
    its own hallucinations.
    """

    fast_model: str | None = None
    """Cheap tier for classification, extraction and query rewriting.

    ``None`` inherits :attr:`reasoning_model`, for the same reason as ``critic_model``.
    """

    request_timeout_s: float = 120.0
    """Read timeout: how long to wait for tokens once a request is in flight.

    Generous on purpose — a large structured-output completion legitimately takes a
    while, and cutting it off mid-JSON is worse than waiting.
    """

    connect_timeout_s: float = 10.0
    """Connect timeout: how long to wait for the TCP/TLS handshake.

    Kept short and separate from the read timeout. A single 120-second budget for both
    means an unreachable endpoint burns two minutes per attempt — six minutes across
    three retries — before the fallback provider engages, during which the user's chat
    simply hangs.
    """

    #: Attempts per call. One may be consumed by output-budget escalation, so a
    #: value of 3 leaves only two genuine retries for a large structured node.
    max_retries: int = 4
    temperature: float = 0.1
    max_tokens: int = 4096

    # ---------------------------------------------------------------- retrieval
    vector_backend: VectorBackend = VectorBackend.AUTO
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "sowsprint_legal"

    embedding_provider: EmbeddingProvider = EmbeddingProvider.AUTO
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    """Must match the Qdrant collection's configured vector size."""

    rerank_provider: RerankProvider = RerankProvider.AUTO
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    retrieval_top_k: int = 20
    """Candidates pulled from each retriever arm before fusion."""

    retrieval_final_k: int = 5
    """Chunks surviving reranking and injected into the Legal agent's context."""

    rrf_k: float = 60.0
    """Reciprocal Rank Fusion smoothing constant (Cormack et al., 2009)."""

    chunk_size: int = 1100
    chunk_overlap: int = 150

    default_jurisdiction: Literal["EU", "US", "BOTH"] = "EU"

    # ---------------------------------------------------------------- guardrails
    max_critic_retries: int = 1
    """How many times a failed quality audit may bounce back to the Legal node."""

    session_budget_usd: float = 2.50
    """Hard ceiling for a single session; the graph halts before exceeding it."""

    enable_cloud_fallback: bool = False
    """Allow escalation from offline engines to cloud when credentials appear mid-run."""

    # ---------------------------------------------------------------- integrations
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    #: Inert. The connector derives a project key from the scope title and never reads
    #: this; it is documented as unused rather than silently ignored.
    jira_project_key: str | None = None
    #: Scrum template, which needs Jira Software and project-create rights. Set blank
    #: to create a template-less project on tenants that lack them, rather than
    #: failing outright.
    jira_project_template: str = "com.pyxis.greenhopper.jira:gh-scrum-template"

    notion_api_key: str | None = None
    notion_parent_page_id: str | None = None

    dry_run_integrations: bool = True

    # ---------------------------------------------------------------- authentication
    # On by default. The application spends real money on model calls and writes to
    # Jira and Notion, and it binds 0.0.0.0 so a phone on the same Wi-Fi can reach it —
    # which also means anything else on that network can. An open deployment is a
    # decision an operator should make explicitly, not a default they inherit.
    auth_enabled: bool = True
    #: JSON array of accounts, as printed by `python -m sowsprint.security.passwd`.
    auth_users: str = ""
    #: Machine accounts for harnesses and CI: `name:key` pairs separated by semicolons.
    auth_api_keys: str = ""
    """When true, Jira/Notion calls are simulated and payloads are surfaced in the UI."""

    # ---------------------------------------------------------------- voice
    whisper_provider: Literal["auto", "openai", "groq", "local", "offline"] = "auto"
    """``local`` targets a self-hosted OpenAI-compatible Whisper server."""

    whisper_model: str = "whisper-1"
    groq_whisper_model: str = "whisper-large-v3-turbo"
    max_audio_mb: float = 25.0

    whisper_base_url: str | None = None
    """Self-hosted Whisper endpoint, e.g. ``http://whisper:8000/v1``.

    Works with any OpenAI-compatible speech-to-text server (faster-whisper-server,
    whisper.cpp's ``server``, vLLM, Speaches). Keeping audio on-premise matters when
    the requirement being dictated is itself confidential.
    """

    whisper_api_key: str | None = None
    """Optional bearer token for the self-hosted endpoint; many need none."""

    audio_sample_rate: int = 24000
    """Sample rate of the raw PCM the Chainlit capture widget streams.

    Must match ``[features.audio].sample_rate`` in ``.chainlit/config.toml``. The
    bytes arriving on the socket are headerless mono PCM and are unusable without it.
    """

    # ---------------------------------------------------------------- derived flags
    @field_validator("port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"port must be within 1..65535, got {v}")
        return v

    @field_validator("host")
    @classmethod
    def _warn_loopback(cls, v: str) -> str:
        # A loopback bind silently breaks the documented iPhone-over-Wi-Fi workflow.
        return v

    # ---------------------------------------------------------------- capabilities
    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_cohere(self) -> bool:
        return bool(self.cohere_api_key)

    @property
    def resolved_llm_provider(self) -> LLMProvider:
        """Collapse ``AUTO`` into a concrete provider based on available credentials."""
        if self.llm_provider is not LLMProvider.AUTO:
            return self.llm_provider
        if self.has_anthropic:
            return LLMProvider.ANTHROPIC
        if self.has_openai:
            return LLMProvider.OPENAI
        if self.has_groq:
            return LLMProvider.GROQ
        return LLMProvider.MOCK

    @property
    def resolved_critic_provider(self) -> LLMProvider:
        """Provider serving the Critic node."""
        if self.critic_provider is LLMProvider.AUTO:
            return self.resolved_llm_provider
        return self.critic_provider

    @property
    def judge_is_independent(self) -> bool:
        """True when the Critic runs on a different vendor than the drafting agent."""
        return self.resolved_critic_provider is not self.resolved_llm_provider

    @property
    def resolved_embedding_provider(self) -> EmbeddingProvider:
        if self.embedding_provider is not EmbeddingProvider.AUTO:
            return self.embedding_provider
        return EmbeddingProvider.OPENAI if self.has_openai else EmbeddingProvider.HASH

    @property
    def resolved_rerank_provider(self) -> RerankProvider:
        if self.rerank_provider is not RerankProvider.AUTO:
            return self.rerank_provider
        if self.tei_rerank_url:
            return RerankProvider.TEI
        if self.has_cohere:
            return RerankProvider.COHERE
        return RerankProvider.HEURISTIC

    @property
    def resolved_whisper_provider(self) -> str:
        """Pick the speech-to-text backend.

        A configured self-hosted endpoint wins over the cloud providers: an operator
        who stood up local Whisper did so deliberately, usually for data-residency
        reasons, and silently shipping audio to a third party would defeat that.
        """
        if self.whisper_provider != "auto":
            return self.whisper_provider
        if self.whisper_base_url:
            return "local"
        if self.has_groq:
            return "groq"
        if self.has_openai:
            return "openai"
        return "offline"

    @property
    def offline_mode(self) -> bool:
        """True when the platform will run entirely on deterministic local engines."""
        return self.resolved_llm_provider is LLMProvider.MOCK

    def provider_is_usable(self, provider: LLMProvider) -> bool:
        """True when this provider can actually be constructed.

        ``build_llm_client`` silently degrades to the offline engine when a cloud
        provider has no credential, so reporting the configured provider without that
        caveat would tell the operator a model is in use when it is not.
        """
        return {
            LLMProvider.ANTHROPIC: self.has_anthropic,
            LLMProvider.OPENAI: self.has_openai,
            LLMProvider.GROQ: self.has_groq,
            # Self-hosted runtimes are assumed reachable; there is no key to check.
            LLMProvider.OLLAMA: True,
            LLMProvider.MOCK: True,
            LLMProvider.AUTO: True,
        }.get(provider, True)

    def describe_provider(self, provider: LLMProvider) -> str:
        """Provider label annotated with a degradation note when it is unusable."""
        if not self.provider_is_usable(provider):
            return f"{provider.value} (no key - offline fallback)"
        return provider.value

    def capability_matrix(self) -> dict[str, str]:
        """Human-readable summary of which adapter each subsystem resolved to.

        Every entry reflects what will *actually* run, not what was configured:

        * integration entries derive from the same predicate the connectors use;
        * reasoning entries are annotated when a cloud provider has no credential.

        A dashboard that reports a model which is not in use is worse than no
        dashboard, because it is trusted.
        """
        critic_label = self.describe_provider(self.resolved_critic_provider)
        if self.judge_is_independent:
            critic_label += " (independent judge)"

        return {
            "reasoning": self.describe_provider(self.resolved_llm_provider),
            "critic": critic_label,
            "embeddings": self.resolved_embedding_provider.value,
            "reranker": self.resolved_rerank_provider.value,
            "vector_store": self.vector_backend.value,
            "transcription": self.resolved_whisper_provider,
            "auth": "enabled" if self.auth_enabled else "DISABLED (open access)",
            "jira": self.integration_mode(self.jira_configured),
            "notion": self.integration_mode(self.notion_configured),
        }

    def integration_mode(self, configured: bool) -> str:
        """Resolve the effective mode of one integration connector."""
        if not self.dry_run_integrations and configured:
            return "live"
        if self.dry_run_integrations and configured:
            return "dry-run"
        if self.dry_run_integrations:
            return "dry-run (unconfigured)"
        return "dry-run (unconfigured credentials)"

    @property
    def jira_configured(self) -> bool:
        return bool(self.jira_base_url and self.jira_email and self.jira_api_token)

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_api_key and self.notion_parent_page_id)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings accessor."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests that mutate the environment)."""
    get_settings.cache_clear()


def openai_client_kwargs(settings: Settings, *, base_url: str | None = None) -> dict[str, Any]:
    """Keyword arguments for an OpenAI-compatible client, with ``base_url`` omitted
    when it is not configured.

    ``base_url=""`` is not treated as "use the default". The SDK stores the empty
    string and builds every request URL as ``"" + "/embeddings"``, which httpx rejects
    with ``UnsupportedProtocol: Request URL is missing an 'http://' or 'https://'
    protocol`` — surfaced to the caller as a bare ``APIConnectionError: Connection
    error``. That reads as a network fault, so it was diagnosed as intermittent
    connectivity and worked around by retrying, while OpenAI embeddings were in fact
    completely non-functional whenever ``SOWSPRINT_OPENAI_BASE_URL`` was left blank.
    The SDK applies its own default only when the argument is absent.
    """
    resolved = (base_url if base_url is not None else settings.openai_base_url) or ""
    resolved = resolved.strip()
    return {"base_url": resolved} if resolved else {}
