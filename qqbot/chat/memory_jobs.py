from __future__ import annotations

import asyncio
import time

from nonebot import logger

from qqbot.audit import AuditLog
from qqbot.group_data import GroupDataStore
from qqbot.llm import DeepSeekClient
from qqbot.memory import MemoryStore
from qqbot.memory.extraction import MemoryExtractor
from qqbot.memory_shadow import ShadowMemoryExtractor
from qqbot.memory_v2 import ClaimStore

from qqbot.chat.config import Config


class MemoryJobRunner:
    """Schedule the existing V1 and V2 shadow extractors behind one entry point."""

    def __init__(
        self,
        *,
        config: Config,
        audit_log: AuditLog,
        client: DeepSeekClient,
        memory_store: MemoryStore,
        claim_store: ClaimStore,
        group_data_store: GroupDataStore,
    ) -> None:
        self.config = config
        self.audit_log = audit_log
        self.group_data_store = group_data_store
        self.claim_store = claim_store
        self.memory_extractor = MemoryExtractor(
            client,
            memory_store,
            group_data_store,
            batch_size=config.memory_extract_batch_size,
            episode_ttl_hours=config.memory_episode_ttl_hours,
        )
        self.shadow_memory_extractor = ShadowMemoryExtractor(
            client,
            claim_store,
            group_data_store,
            batch_size=config.memory_v2_shadow_batch_size,
            backfill_existing=config.memory_v2_shadow_backfill_existing,
            episode_ttl_hours=config.memory_episode_ttl_hours,
        )
        self.tasks: dict[int, asyncio.Task[None]] = {}

    def initialize_shadow_cursors(self) -> None:
        if not self.config.memory_v2_shadow_enabled:
            return
        for group_id in self.config.llm_allowed_groups:
            self.claim_store.initialize_shadow_cursor(
                group_id,
                self.group_data_store.latest_human_message_id(group_id),
            )

    def schedule(self, group_id: int) -> None:
        if not (
            self.config.memory_auto_extract_enabled
            or self.config.memory_v2_shadow_enabled
        ):
            return
        existing = self.tasks.get(group_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(self._run(group_id))
        self.tasks[group_id] = task

    async def _run(self, group_id: int) -> None:
        started = time.perf_counter()
        total_processed = 0
        if self.config.memory_auto_extract_enabled:
            self.audit_log.record(
                "memory_extraction.started",
                group_id=group_id,
                batch_size=self.config.memory_extract_batch_size,
            )
            try:
                for _ in range(3):
                    processed = await self.memory_extractor.process_available(group_id)
                    total_processed += processed
                    if processed < self.config.memory_extract_batch_size:
                        break
            except Exception as error:
                logger.exception(
                    "Background memory extraction failed group={}", group_id
                )
                self.audit_log.record(
                    "memory_extraction.failed",
                    group_id=group_id,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    processed_messages=total_processed,
                    error_type=type(error).__name__,
                    error=str(error),
                )
            else:
                self.audit_log.record(
                    "memory_extraction.completed",
                    group_id=group_id,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    processed_messages=total_processed,
                )

        if self.config.memory_v2_shadow_enabled:
            shadow_started = time.perf_counter()
            try:
                for _ in range(3):
                    shadow_result = await self.shadow_memory_extractor.process_available(
                        group_id
                    )
                    self.audit_log.record(
                        "memory_v2.shadow_batch",
                        group_id=group_id,
                        batch_id=shadow_result.batch_id,
                        processed_messages=shadow_result.processed_messages,
                        operation_count=shadow_result.operation_count,
                        applied_count=shadow_result.applied_count,
                        rejection_reasons=shadow_result.rejection_reasons,
                    )
                    if (
                        shadow_result.processed_messages
                        < self.config.memory_v2_shadow_batch_size
                    ):
                        break
            except Exception as error:
                logger.exception(
                    "V2 shadow memory extraction failed group={}", group_id
                )
                self.audit_log.record(
                    "memory_v2.shadow_failed",
                    group_id=group_id,
                    duration_ms=round((time.perf_counter() - shadow_started) * 1000, 1),
                    error_type=type(error).__name__,
                    error=str(error),
                )
