import unittest
from unittest.mock import AsyncMock, Mock, patch

from qqbot.chat.config import Config
from qqbot.chat.memory_jobs import MemoryJobRunner
from qqbot.integrations.llm import CompletionTrace
from qqbot.memory.extraction import ExtractionResult


class MemoryJobRunnerTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self) -> MemoryJobRunner:
        config = Config(
            llm_api_key="test",
            llm_base_url="https://example.com",
            llm_model="test-model",
            memory_extract_failure_backoff_seconds=300,
        )
        audit_log = Mock()
        audit_log.text_limit = 100
        runner = MemoryJobRunner(
            config=config,
            audit_log=audit_log,
            client=Mock(),
            claim_store=Mock(),
            group_data_store=Mock(),
        )
        return runner

    async def test_failed_extraction_suppresses_scheduling_during_backoff(self) -> None:
        runner = self.make_runner()
        runner.memory_extractor.process_available = AsyncMock(
            side_effect=ValueError("empty")
        )

        with (
            patch("qqbot.chat.memory_jobs.time.monotonic", return_value=100),
            patch("qqbot.chat.memory_jobs.logger.exception"),
        ):
            await runner._run(1)

        self.assertEqual(runner.failure_retry_at[1], 400)
        with (
            patch("qqbot.chat.memory_jobs.time.monotonic", return_value=200),
            patch("qqbot.chat.memory_jobs.asyncio.create_task") as create_task,
        ):
            runner.schedule(1)
            runner.schedule(2)
        create_task.assert_called_once()
        create_task.call_args.args[0].close()
        self.assertNotIn(1, runner.tasks)
        self.assertIn(2, runner.tasks)

    async def test_successful_extraction_clears_failure_backoff(self) -> None:
        runner = self.make_runner()
        runner.failure_retry_at[1] = 400
        runner.memory_extractor.process_available = AsyncMock(
            return_value=ExtractionResult(0, 0, 0, None)
        )

        await runner._run(1)

        self.assertNotIn(1, runner.failure_retry_at)

    def test_records_bounded_reviewable_reasoning_trace(self) -> None:
        runner = self.make_runner()
        trace = CompletionTrace(
            finish_reason="stop",
            content_length=2,
            reasoning_content="思" * 250,
            usage={"completion_tokens_details": {"reasoning_tokens": 100}},
        )

        runner._record_llm_response(1, 7, trace)

        fields = runner.audit_log.record.call_args.kwargs
        self.assertEqual(
            runner.audit_log.record.call_args.args[0],
            "memory_extraction.llm_response",
        )
        self.assertEqual(fields["max_output_tokens"], 8192)
        self.assertEqual(fields["reasoning_chars"], 250)
        self.assertEqual(
            [len(chunk) for chunk in fields["reasoning_chunks"]],
            [100, 100, 50],
        )
        self.assertFalse(fields["reasoning_truncated"])


if __name__ == "__main__":
    unittest.main()
