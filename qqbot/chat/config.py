from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from qqbot.chat.prompts import DEFAULT_SYSTEM_PROMPT


class Config(BaseModel):
    llm_api_key: SecretStr
    llm_base_url: str = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    llm_thinking_mode: Literal["provider_default", "disabled"] = "provider_default"
    allowed_groups: frozenset[int] = frozenset()
    llm_context_turns: int = Field(default=50, ge=5, le=200)
    llm_assistant_context_turns: int = Field(default=3, ge=0, le=20)
    llm_context_ttl_hours: float = Field(default=12, ge=0, le=168)
    llm_max_input_chars: int = Field(default=2000, ge=100, le=10000)
    llm_max_output_tokens: int = Field(default=4096, ge=100, le=8192)
    llm_timeout_seconds: float = Field(default=45, ge=5, le=120)
    llm_cooldown_seconds: float = Field(default=3, ge=0, le=60)
    llm_max_concurrency: int = Field(default=2, ge=1, le=10)
    tavily_api_key: SecretStr = SecretStr("")
    media_max_images: int = Field(default=4, ge=1, le=8)
    media_max_image_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=50 * 1024 * 1024,
    )
    media_download_timeout_seconds: float = Field(default=45, ge=5, le=120)
    bilibili_auto_parse_enabled: bool = False
    history_today_enabled: bool = False
    memory_auto_extract_enabled: bool = True
    memory_extract_batch_size: int = Field(default=20, ge=5, le=100)
    memory_extract_max_output_tokens: int = Field(default=8192, ge=100, le=8192)
    memory_episode_ttl_hours: float = Field(default=72, ge=1, le=720)
    memory_extract_failure_backoff_seconds: float = Field(
        default=300,
        ge=0,
        le=86400,
    )
    audit_log_enabled: bool = True
    audit_log_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    audit_log_backup_count: int = Field(default=3, ge=1, le=20)
    audit_log_text_limit: int = Field(default=4000, ge=100, le=20000)
    llm_system_prompt: str = DEFAULT_SYSTEM_PROMPT
