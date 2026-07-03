import time
import unittest
from unittest.mock import AsyncMock

from clickup_client import AccountSession, ClickUpBrainClient
from config import Account, Settings
from models import ChatCompletionRequest, ChatMessage, DEFAULT_MODEL_ID


class FakeSession:
    def __init__(self, label, chunks, error=None):
        self.label = label
        self.chunks = chunks
        self.error = error
        self.calls = 0
        self.failures = 0
        self.successes = 0

    def is_available(self):
        return True

    async def chat_stream(self, prompt, model=DEFAULT_MODEL_ID):
        self.calls += 1
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    def mark_failure(self):
        self.failures += 1

    def mark_success(self):
        self.successes += 1


class StreamingFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_if_failure_happens_before_first_chunk(self):
        first = FakeSession("first", [], RuntimeError("before output"))
        second = FakeSession("second", ["O", "K"])
        client = ClickUpBrainClient(Settings())
        client.sessions = [first, second]

        messages = [ChatMessage(role="user", content="test")]
        result = [chunk async for chunk in client.chat_stream(messages)]

        self.assertEqual(result, ["O", "K"])
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    async def test_does_not_retry_after_output_started(self):
        first = FakeSession("first", ["partial"], RuntimeError("after output"))
        second = FakeSession("second", ["replacement"])
        client = ClickUpBrainClient(Settings())
        client.sessions = [first, second]
        messages = [ChatMessage(role="user", content="test")]

        with self.assertRaisesRegex(RuntimeError, "after output"):
            _ = [chunk async for chunk in client.chat_stream(messages)]

        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)


class SessionRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refreshes_inside_margin(self):
        session = AccountSession(
            Account(jwt="a.b.c", workspace_id="1", label="test"),
            Settings(),
        )
        calls = 0

        async def fake_fetch():
            nonlocal calls
            calls += 1
            return f"token-{calls}", 120

        session._fetch_session_token = fake_fetch
        first = await session.get_session_token()
        session._session_token_exp = time.time() + 59
        second = await session.get_session_token()

        self.assertEqual(first, "token-1")
        self.assertEqual(second, "token-2")
        self.assertEqual(calls, 2)


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_fetch_retries_next_account(self):
        first = FakeSession("first", [])
        second = FakeSession("second", [])
        client = ClickUpBrainClient(Settings())
        client.sessions = [first, second]
        expected = [{"id": DEFAULT_MODEL_ID, "type": "passthrough"}]
        client._fetch_models_from_session = AsyncMock(
            side_effect=[RuntimeError("first failed"), expected]
        )

        result = await client.fetch_models()

        self.assertEqual(result, expected)
        self.assertEqual(client._fetch_models_from_session.await_count, 2)

    async def test_old_clickup_brain_alias_maps_to_brain(self):
        client = ClickUpBrainClient(Settings())
        client._models = [{"id": "brain", "name": "Brain²"}]

        self.assertEqual(client._resolve_model("clickup-brain"), "brain")

    async def test_default_model_is_opus_48(self):
        request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="test")]
        )

        self.assertEqual(request.model, "claude-opus-4-8")

    async def test_mock_model_fetch_does_not_call_upstream(self):
        client = ClickUpBrainClient(Settings(mock=True))
        client._fetch_models_from_session = AsyncMock()

        result = await client.fetch_models()

        self.assertEqual(result[0]["id"], DEFAULT_MODEL_ID)
        client._fetch_models_from_session.assert_not_awaited()

    async def test_unsupported_generation_parameters_are_accepted(self):
        request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="test")],
            temperature=99,
            top_p="ignored",
            max_tokens=-1,
        )

        self.assertEqual(request.temperature, 99)


if __name__ == "__main__":
    unittest.main()
