from __future__ import annotations

import asyncio
import time

from nonebot import logger

from qqbot.chat.config import Config
from qqbot.integrations.llm import ChatClient, CompletionTrace
from qqbot.memory.extraction import MemoryExtractor
from qqbot.memory.v2 import ClaimStore
from qqbot.runtime.audit import AuditLog
from qqbot.storage.group_data import GroupDataStore


class MemoryJobRunner:
    """Schedule the single authoritative memory extractor per group."""

    def __init__(
        self,
        *,
        config: Config,
        audit_log: AuditLog,
        client: ChatClient,
        claim_store: ClaimStore,
        group_data_store: GroupDataStore,
    ) -> None:
        self.config = config
        self.audit_log = audit_log
        self.memory_extractor = MemoryExtractor(
            client,
            claim_store,
            group_data_store,
            batch_size=config.memory_extract_batch_size,
            max_output_tokens=config.memory_extract_max_output_tokens,
            episode_ttl_hours=config.memory_episode_ttl_hours,
            response_observer=self._record_llm_response,
        )
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self.failure_retry_at: dict[int, float] = {}

    def _record_llm_response(
        self,
        group_id: int,
        batch_id: int,
        trace: CompletionTrace,
    ) -> None:
        chunk_size = self.audit_log.text_limit
        max_reasoning_chars = chunk_size * 50
        bounded_reasoning = trace.reasoning_content[:max_reasoning_chars]
        reasoning_chunks = [
            bounded_reasoning[index : index + chunk_size]
            for index in range(0, len(bounded_reasoning), chunk_size)
        ]
        self.audit_log.record(
            "memory_extraction.llm_response",
            group_id=group_id,
            batch_id=batch_id,
            max_output_tokens=self.config.memory_extract_max_output_tokens,
            finish_reason=trace.finish_reason,
            content_chars=trace.content_length,
            reasoning_chars=len(trace.reasoning_content),
            reasoning_truncated=len(trace.reasoning_content) > max_reasoning_chars,
            reasoning_chunks=reasoning_chunks,
            usage=trace.usage,
        )

    def schedule(self, group_id: int) -> None:
        if not self.config.memory_auto_extract_enabled:
            return
        if time.monotonic() < self.failure_retry_at.get(group_id, 0):
            return
        existing = self.tasks.get(group_id)
        if existing is not None and not existing.done():
            return
        self.tasks[group_id] = asyncio.create_task(self._run(group_id))

    async def _run(self, group_id: int) -> None:
        started = time.perf_counter()
        total_processed = 0
        self.audit_log.record(
            "memory_extraction.started",
            group_id=group_id,
            batch_size=self.config.memory_extract_batch_size,
        )
        try:
            for _ in range(3):
                result = await self.memory_extractor.process_available(group_id)
                total_processed += result.processed_messages
                self.audit_log.record(
                    "memory_extraction.batch",
                    group_id=group_id,
                    batch_id=result.batch_id,
                    processed_messages=result.processed_messages,
                    operation_count=result.operation_count,
                    applied_count=result.applied_count,
                    rejection_reasons=result.rejection_reasons,
                )
                if result.processed_messages < self.config.memory_extract_batch_size:
                    break
        except Exception as error:
            backoff_seconds = self.config.memory_extract_failure_backoff_seconds
            self.failure_retry_at[group_id] = time.monotonic() + backoff_seconds
            logger.exception("Background memory extraction failed group={}", group_id)
            self.audit_log.record(
                "memory_extraction.failed",
                group_id=group_id,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                processed_messages=total_processed,
                error_type=type(error).__name__,
                error=str(error),
                retry_after_seconds=backoff_seconds,
            )
        else:
            self.failure_retry_at.pop(group_id, None)
            self.audit_log.record(
                "memory_extraction.completed",
                group_id=group_id,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                processed_messages=total_processed,
            )
