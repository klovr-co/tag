from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

try:
    import slack_bolt  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("slack_bolt is installed by the Slack bridge runtime")

from scripts import slack_socket_agent


class SlackBotNameTests(unittest.TestCase):
    def test_openmax_is_the_backend_independent_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "OpenMax")
            self.assertEqual(slack_socket_agent.suggested_bot_name("claude"), "OpenMax")

    def test_operator_can_override_the_display_name(self) -> None:
        with patch.dict(os.environ, {"OPENTAG_BOT_NAME": "TeamBot"}, clear=True):
            self.assertEqual(slack_socket_agent.suggested_bot_name("codex"), "TeamBot")


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, _size: int = -1) -> bytes:
        body, self.body = self.body, b""
        return body


class SlackTextAttachmentTests(unittest.TestCase):
    @patch(
        "scripts.slack_socket_agent.urllib.request.urlopen",
        return_value=FakeResponse(b"opening line\nclosing line"),
    )
    def test_includes_slack_text_snippet_in_thread_context(self, mock_urlopen: object) -> None:
        messages = [
            {
                "files": [
                    {
                        "id": "F123",
                        "name": "lecture-transcript.txt",
                        "mimetype": "text/plain",
                        "url_private_download": "https://files.slack.com/F123",
                    }
                ]
            }
        ]
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            text = slack_socket_agent.download_thread_text_files(messages)

        self.assertEqual(["[Slack text attachment: lecture-transcript.txt]\nopening line\nclosing line"], text)
        request = mock_urlopen.call_args.args[0]  # type: ignore[union-attr]
        self.assertEqual("Bearer xoxb-test", request.get_header("Authorization"))

    def test_skips_binary_attachments(self) -> None:
        messages = [{"files": [{"id": "F123", "name": "slides.pdf", "mimetype": "application/pdf"}]}]
        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            self.assertEqual([], slack_socket_agent.download_thread_text_files(messages))

    def test_truncates_large_text_attachment(self) -> None:
        with patch(
            "scripts.slack_socket_agent.download_file_bytes",
            return_value=b"a" * (slack_socket_agent.MAX_ATTACHMENT_TEXT_CHARS + 1),
        ), patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test"}, clear=False):
            text = slack_socket_agent.download_thread_text_files(
                [{"files": [{"id": "F123", "name": "transcript.txt", "mimetype": "text/plain", "url_private": "https://example.test/F123"}]}]
            )

        self.assertTrue(text[0].endswith("[Attachment text truncated]"))


class SlackReplyChunkingTests(unittest.TestCase):
    def test_splits_at_paragraph_boundaries(self) -> None:
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."

        self.assertEqual(
            ["First paragraph.", "Second paragraph.", "Third paragraph."],
            slack_socket_agent.split_reply(text, max_chars=20),
        )

    def test_preserves_long_unbroken_text(self) -> None:
        text = "a" * 25

        self.assertEqual(["a" * 10, "a" * 10, "a" * 5], slack_socket_agent.split_reply(text, max_chars=10))
class SlackChannelAllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("SLACK_CHANNEL_ID")

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("SLACK_CHANNEL_ID", None)
        else:
            os.environ["SLACK_CHANNEL_ID"] = self.previous

    def test_configured_channel_is_allowed(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = "C123"
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C123"))
        self.assertFalse(slack_socket_agent.slack_channel_allowed("C999"))

    def test_empty_configuration_preserves_existing_behavior(self) -> None:
        os.environ["SLACK_CHANNEL_ID"] = ""
        self.assertTrue(slack_socket_agent.slack_channel_allowed("C999"))


class SlackWorkingIndicatorTests(unittest.TestCase):
    def test_uses_native_slack_loading_status(self) -> None:
        client = MagicMock()
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        with patch("scripts.slack_socket_agent.threading.Timer") as timer:
            indicator.start()
            indicator.clear()

        first_call = client.assistant_threads_setStatus.call_args_list[0].kwargs
        self.assertEqual("is working on this…", first_call["status"])
        self.assertEqual(slack_socket_agent.LOADING_MESSAGES, first_call["loading_messages"])
        self.assertEqual("", client.assistant_threads_setStatus.call_args_list[1].kwargs["status"])
        timer.assert_called_once_with(
            slack_socket_agent.STATUS_REFRESH_SECONDS,
            indicator.refresh,
        )
        timer.return_value.start.assert_called_once()
        timer.return_value.cancel.assert_called_once()
        client.chat_postMessage.assert_not_called()

    def test_falls_back_to_temporary_message_when_native_status_fails(self) -> None:
        client = MagicMock()
        client.assistant_threads_setStatus.side_effect = RuntimeError("unsupported")
        client.chat_postMessage.return_value = {"ts": "2.34"}
        indicator = slack_socket_agent.WorkingIndicator(client, "C123", "1.23", MagicMock())

        indicator.start()
        indicator.clear()

        self.assertFalse(indicator.native)
        self.assertEqual("2.34", indicator.message_ts)
        client.chat_postMessage.assert_called_once()
        client.assistant_threads_setStatus.assert_called_once()


class SlackAnswerStreamTests(unittest.TestCase):
    def test_batches_deltas_and_finishes_stream(self) -> None:
        client = MagicMock()
        client.chat_startStream.return_value = {"ts": "3.45"}
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)
        stream.append("b" * slack_socket_agent.STREAM_APPEND_CHARS)
        finished = stream.finish(stream.received)

        self.assertTrue(finished)
        client.chat_startStream.assert_called_once()
        client.chat_appendStream.assert_called_once()
        client.chat_stopStream.assert_called_once_with(channel="C123", ts="3.45")

    def test_no_deltas_uses_normal_message_fallback(self) -> None:
        client = MagicMock()
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        self.assertFalse(stream.finish("Complete Codex answer"))
        client.chat_startStream.assert_not_called()

    def test_start_failure_preserves_complete_answer_fallback(self) -> None:
        client = MagicMock()
        client.chat_startStream.side_effect = RuntimeError("not supported")
        stream = slack_socket_agent.SlackAnswerStream(client, "C123", "1.23", MagicMock())

        stream.append("a" * slack_socket_agent.STREAM_START_CHARS)

        self.assertTrue(stream.failed)
        self.assertFalse(stream.finish(stream.received))


class SlackStreamingConfigurationTests(unittest.TestCase):
    def test_streaming_defaults_on_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))
        with patch.dict(os.environ, {"OPENTAG_SLACK_STREAMING": "0"}, clear=True):
            self.assertFalse(slack_socket_agent.env_enabled("OPENTAG_SLACK_STREAMING", default=True))
