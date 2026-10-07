"""Central settings. This is the only module allowed to read from the environment.

Every other module — auth, the DB layer, the encryption helper, health checks —
must obtain configuration through `get_settings()`, never via `os.environ` directly.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/ is two levels up. .env lives
# alongside pyproject.toml in backend/, not at the repo root.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Core
    app_env: str = Field("development", alias="APP_ENV")
    app_secret_key: str = Field(..., alias="APP_SECRET_KEY")
    credential_encryption_key: str = Field(..., alias="CREDENTIAL_ENCRYPTION_KEY")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # MySQL
    mysql_host: str = Field("localhost", alias="MYSQL_HOST")
    mysql_port: int = Field(3306, alias="MYSQL_PORT")
    mysql_database: str = Field("architectos", alias="MYSQL_DATABASE")
    mysql_user: str = Field("architectos", alias="MYSQL_USER")
    mysql_password: str = Field("", alias="MYSQL_PASSWORD")

    # Redis
    redis_host: str = Field("localhost", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_db: int = Field(0, alias="REDIS_DB")
    job_queue_key: str = Field("architectos:jobs", alias="JOB_QUEUE_KEY")

    # Resource manager — full build in Phase 13; this is the thin hook (§6.2)
    # that lets the worker refuse a second heavy job while one is running.
    max_concurrent_heavy_jobs: int = Field(1, alias="MAX_CONCURRENT_HEAVY_JOBS")

    # Seeded user (consumed from step 1.B.4 onward)
    seed_user_email: str | None = Field(None, alias="SEED_USER_EMAIL")
    seed_user_password: str | None = Field(None, alias="SEED_USER_PASSWORD")

    # Frontend (consumed from step 1.D.1 onward, for CORS)
    frontend_origin: str = Field("http://localhost:2004", alias="FRONTEND_ORIGIN")

    # LLM (consumed from step 2.A.1 onward). Model IDs come from env [C5].
    # FreeLLMAPI (OpenAI-compatible, self-hosted) is primary; Groq is the fallback.
    # FreeLLMAPI routes across its own providers, so one model id (`auto`)
    # serves both tiers. Its API lives under `/v1`; the adapter appends that
    # when the configured base URL omits it.
    freellm_base_url: str = Field("http://localhost:3333/v1", alias="FREELLM_BASE_URL")
    freellm_api_key: str = Field("", alias="FREELLM_API_KEY")
    freellm_model: str = Field("auto", alias="FREELLM_MODEL")
    groq_api_key: str = Field("", alias="GROQ_API_KEY")
    groq_model_strong: str = Field("", alias="GROQ_MODEL_STRONG")
    groq_model_small: str = Field("", alias="GROQ_MODEL_SMALL")
    llm_request_timeout_seconds: int = Field(60, alias="LLM_REQUEST_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(2, alias="LLM_MAX_RETRIES")
    # GLM-5.3 (and other reasoning models) emit zero completion tokens when
    # max_tokens is omitted; they then spend the whole budget on hidden
    # reasoning if the cap is too small. 4096 leaves room for the visible JSON.
    llm_max_tokens: int = Field(4096, alias="LLM_MAX_TOKENS", ge=1)
    # SMALL structured planners (intent, research, optimization, validation).
    # Interventions / plans are short JSON. Reserving the global 4096
    # completion budget against Groq TPM/OTPM is what turned a compact
    # batch into "Requested 12788 / Limit 6000" and a Layer 5 call into
    # "OTPM Limit 1000, Requested 1088". 800 stays under the Groq
    # on-demand SMALL OTPM of 1000. Code and review calls keep LLM_MAX_TOKENS.
    llm_planner_max_tokens: int = Field(800, alias="LLM_PLANNER_MAX_TOKENS", ge=1)
    # Groq on-demand OTPM for GROQ_MODEL_SMALL (observed for
    # qwen/qwen3.8-27b: Limit 1000). The Groq adapter never sends
    # max_tokens above this for that model. STRONG is not capped here —
    # its OTPM is not the limit we have evidence for.
    groq_small_otpm_limit: int = Field(1000, alias="GROQ_SMALL_OTPM_LIMIT", ge=1)
    # Groq on-demand tokens-per-minute per request. Groq counts input tokens
    # plus the `max_tokens` reservation against this, and refuses a single
    # call above it (observed on openai/gpt-oss-120b: "Limit 8000, Requested
    # 8456"). The Groq adapter shrinks `max_tokens` to fit, or skips Groq when
    # the prompt alone leaves no room. Raise these after upgrading the tier.
    groq_strong_tpm_limit: int = Field(8000, alias="GROQ_STRONG_TPM_LIMIT", ge=1)
    groq_small_tpm_limit: int = Field(6000, alias="GROQ_SMALL_TPM_LIMIT", ge=1)
    # Total characters of target-file text the Code Agent puts in its prompt.
    # 0 derives it from GROQ_STRONG_TPM_LIMIT so the prompt always fits the
    # tightest fallback provider; files over their share are shown as windows
    # around the evidence, and the model answers with edits, not whole files.
    code_agent_context_chars: int = Field(0, alias="CODE_AGENT_CONTEXT_CHARS", ge=0)
    # Extra Code Agent rounds after the sandbox build fails: the patch is rolled
    # back, the build error is shown to the model, and the corrected patch is
    # validated again. 0 disables repair.
    code_agent_repair_rounds: int = Field(1, alias="CODE_AGENT_REPAIR_ROUNDS", ge=0, le=3)

    # Workspaces (consumed from step 2.B.2 onward). Relative paths resolve
    # against the repository root so `./workspaces` matches the gitignored
    # `workspaces/` directory, not `backend/workspaces/`.
    workspace_root: str = Field("./workspaces", alias="WORKSPACE_ROOT")
    # Upper bound for any single git subprocess (clone, push, ...). Clones of
    # repositories with large committed binaries are bandwidth-bound and can
    # take minutes.
    git_timeout_seconds: int = Field(900, alias="GIT_TIMEOUT_SECONDS")

    # Neo4j (consumed from step 2.C.2 onward)
    neo4j_uri: str = Field("bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field("neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field("", alias="NEO4J_PASSWORD")

    # Qdrant (consumed from step 2.D.3 onward)
    qdrant_host: str = Field("localhost", alias="QDRANT_HOST")
    qdrant_port: int = Field(6333, alias="QDRANT_PORT")
    qdrant_collection_code: str = Field("code_chunks", alias="QDRANT_COLLECTION_CODE")
    qdrant_collection_pages: str = Field("page_content", alias="QDRANT_COLLECTION_PAGES")
    qdrant_collection_knowledge: str = Field(
        "optimization_knowledge", alias="QDRANT_COLLECTION_KNOWLEDGE"
    )

    # Local embedding model (consumed from step 2.D.2 onward)
    embedding_model: str = Field("BAAI/bge-small-en-v1.5", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(384, alias="EMBEDDING_DIM")
    model_idle_unload_seconds: int = Field(600, alias="MODEL_IDLE_UNLOAD_SECONDS")

    # Reranker (consumed from step 5.A.2 onward)
    reranker_model: str = Field("BAAI/bge-reranker-base", alias="RERANKER_MODEL")
    ram_pressure_threshold_mb: int = Field(2048, alias="RAM_PRESSURE_THRESHOLD_MB")
    # Audit-time hybrid retrieval (embed + Qdrant + Neo4j + cross-encoder rerank
    # per fired rule). It only appends "derived" evidence rows to findings and
    # never changes hits, severity, or scores, but it dominates audit runtime.
    # Agents keep their own retrieval tools regardless of this flag.
    audit_retrieval_enabled: bool = Field(False, alias="AUDIT_RETRIEVAL_ENABLED")

    # Crawling (consumed from step 3.B.1 onward)
    crawl_user_agent: str = Field("ArchitectOSBot/1.0", alias="CRAWL_USER_AGENT")
    crawl_max_urls: int = Field(200, alias="CRAWL_MAX_URLS")
    crawl_max_depth: int = Field(4, alias="CRAWL_MAX_DEPTH")
    crawl_fetch_timeout_seconds: int = Field(20, alias="CRAWL_FETCH_TIMEOUT_SECONDS")
    crawl_max_html_bytes: int = Field(1_572_864, alias="CRAWL_MAX_HTML_BYTES")
    playwright_max_contexts: int = Field(2, alias="PLAYWRIGHT_MAX_CONTEXTS")
    playwright_timeout_seconds: int = Field(25, alias="PLAYWRIGHT_TIMEOUT_SECONDS")
    lighthouse_enabled: bool = Field(True, alias="LIGHTHOUSE_ENABLED")
    crawl_render_max_pages: int = Field(5, alias="CRAWL_RENDER_MAX_PAGES")
    crawl_lighthouse_max_pages: int = Field(1, alias="CRAWL_LIGHTHOUSE_MAX_PAGES")

    # Agent loop limits (consumed from step 6.2 onward). Every agent gets
    # all five budgets `[SPEC AGENTS.md §51]`; these are the defaults
    # `app.agents.base.default_budget` reads — budgets stay constructor
    # arguments, never hard-coded per agent.
    agent_max_iterations: int = Field(8, alias="AGENT_MAX_ITERATIONS")
    agent_max_tool_calls: int = Field(20, alias="AGENT_MAX_TOOL_CALLS")
    agent_max_execution_seconds: float = Field(120, alias="AGENT_MAX_EXECUTION_SECONDS")
    agent_max_token_budget: int = Field(20_000, alias="AGENT_MAX_TOKEN_BUDGET")
    agent_max_files_modified: int = Field(0, alias="AGENT_MAX_FILES_MODIFIED")

    # Sandbox (consumed from step 7.5 onward). SANDBOX_IMAGE blank means the
    # sandbox is not configured — `run_in_sandbox` fails explicitly rather
    # than silently running anything on the host.
    sandbox_image: str = Field("", alias="SANDBOX_IMAGE")
    sandbox_cpu_limit: str = Field("2", alias="SANDBOX_CPU_LIMIT")
    sandbox_memory_limit: str = Field("2g", alias="SANDBOX_MEMORY_LIMIT")
    sandbox_timeout_seconds: int = Field(600, alias="SANDBOX_TIMEOUT_SECONDS")
    # [PROPOSED] not in the master env list (§4): `--user` is only forced when
    # set, since "non-root where possible" (AGENTS.md §34) depends on the
    # configured SANDBOX_IMAGE actually having that uid available.
    sandbox_run_as_uid: str = Field("", alias="SANDBOX_RUN_AS_UID")
    sandbox_preview_port: int = Field(3000, alias="SANDBOX_PREVIEW_PORT")
    sandbox_preview_ready_seconds: int = Field(90, alias="SANDBOX_PREVIEW_READY_SECONDS")

    # Scope envelope (step 7.4, `[SPEC AGENTS.md §33]`). [PROPOSED] defaults —
    # not in the master env list, added when the scope enforcer needed
    # concrete numbers. Forbidden directories always apply, on top of
    # whatever the Change Plan itself allows.
    scope_max_files_changed: int = Field(10, alias="SCOPE_MAX_FILES_CHANGED")
    scope_max_lines_changed: int = Field(200, alias="SCOPE_MAX_LINES_CHANGED")
    scope_max_diff_bytes: int = Field(20_000, alias="SCOPE_MAX_DIFF_BYTES")
    scope_forbidden_directories: str = Field(
        "auth,database,migrations,alembic,.git,node_modules,secrets,.env",
        alias="SCOPE_FORBIDDEN_DIRECTORIES",
    )

    # Targeted validation (step 7.6, `[SPEC AGENTS.md §36]`). [PROPOSED]: caps
    # how many crawled URLs one code change validates in the browser, so a
    # popular dynamic route can never balloon into a full site recrawl.
    targeted_validation_max_urls: int = Field(5, alias="TARGETED_VALIDATION_MAX_URLS")
    targeted_validation_sibling_urls: int = Field(5, alias="TARGETED_VALIDATION_SIBLING_URLS")

    # GitHub. OAuth 2.0 Web flow is primary (same shape as GSC). Encrypted
    # access/refresh tokens live on platform_connections. GITHUB_TOKEN is a
    # documented reminder only — the connector decrypts the DB ciphertext,
    # never logs it, and never sends it to an LLM. A PAT can still be stored
    # as a fallback via PUT /github/connection.
    github_token: str = Field("", alias="GITHUB_TOKEN")
    github_api_base_url: str = Field(
        "https://api.github.com", alias="GITHUB_API_BASE_URL"
    )
    github_oauth_client_id: str = Field("", alias="GITHUB_OAUTH_CLIENT_ID")
    github_oauth_client_secret: str = Field("", alias="GITHUB_OAUTH_CLIENT_SECRET")
    github_oauth_redirect_uri: str = Field("", alias="GITHUB_OAUTH_REDIRECT_URI")

    # Google Search Console (consumed from step 11.1 onward). OAuth 2.0
    # Web client — the user consents with their Google account. Refresh
    # tokens are stored encrypted on platform_connections. Client id/secret
    # are never logged or sent to an LLM. CSV/JSON import is not implemented
    # (Q8). Service-account JSON keys are not used.
    gsc_oauth_client_id: str = Field("", alias="GSC_OAUTH_CLIENT_ID")
    gsc_oauth_client_secret: str = Field("", alias="GSC_OAUTH_CLIENT_SECRET")
    gsc_oauth_redirect_uri: str = Field(
        "",
        alias="GSC_OAUTH_REDIRECT_URI",
    )

    # SMTP — site health report delivery. Empty host/user/password means
    # the report is stored and email_status stays unavailable.
    smtp_host: str = Field("", alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_user: str = Field("", alias="SMTP_USER")
    smtp_password: str = Field("", alias="SMTP_PASS")
    smtp_from: str = Field("", alias="SMTP_FROM")
    smtp_timeout_seconds: int = Field(30, alias="SMTP_TIMEOUT_SECONDS")
    # Comma-separated To: list. The schedule file can override per project.
    site_report_email_to: str = Field("", alias="SITE_REPORT_EMAIL_TO")
    site_report_schedule_path: str = Field(
        "site_report_schedule.yaml",
        alias="SITE_REPORT_SCHEDULE_PATH",
    )

    # Rollback confidence (step 8.6, `[SPEC AGENTS.md §38]`). [PROPOSED]: not
    # in the master env list -- a rollback plan below this confidence must
    # STOP and require explicit user confirmation rather than proceeding.
    rollback_confidence_threshold: float = Field(0.75, alias="ROLLBACK_CONFIDENCE_THRESHOLD")

    @field_validator(
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "smtp_from",
        "site_report_email_to",
        "site_report_schedule_path",
        mode="before",
    )
    @classmethod
    def _strip_smtp_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'")
        return value

    @property
    def smtp_from_address(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def smtp_is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def resolved_site_report_schedule_path(self) -> Path:
        path = Path(self.site_report_schedule_path)
        if path.is_absolute():
            return path
        return (BACKEND_ROOT / path).resolve()

    @property
    def scope_forbidden_directory_list(self) -> tuple[str, ...]:
        return tuple(
            part.strip().strip("/")
            for part in self.scope_forbidden_directories.split(",")
            if part.strip()
        )

    @property
    def mysql_dsn(self) -> str:
        # connect_timeout=10 matches pymysql's own default (belt-and-braces,
        # not the fix for a multi-minute stall — a dead port fails in ~10s
        # either way). lock_wait_timeout caps how long a DDL statement (e.g.
        # Alembic's CREATE/ALTER TABLE) will sit blocked behind a metadata
        # lock held by a stale/leaked connection from an earlier crashed run
        # — MySQL's server-side default is 31536000s (1 year), which is what
        # actually produces an apparently-infinite hang on startup.
        init_command = quote("SET SESSION lock_wait_timeout=15")
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?connect_timeout=10&init_command={init_command}"
        )

    @property
    def resolved_workspace_root(self) -> Path:
        path = Path(self.workspace_root)
        if path.is_absolute():
            return path
        repo_root = BACKEND_ROOT.parent
        return (repo_root / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
