#!/usr/bin/env python3
# Modified by klovr.co in 2026 for Tag. See NOTICE and repository history.
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

try:
    from .opentag_process_env import backend_environment
    from .slack_mrkdwn import to_mrkdwn
except ImportError:  # Direct script execution does not create a package context.
    from opentag_process_env import backend_environment
    from slack_mrkdwn import to_mrkdwn


MENTION_RE = re.compile(r"<@[^>]+>")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 12_000
MAX_REPLY_CHARS = 3_800
STREAM_START_CHARS = 40
STREAM_APPEND_CHARS = 200
STATUS_REFRESH_SECONDS = 90
LOADING_MESSAGES = [
    "Reading the thread…",
    "Searching connected knowledge…",
    "Working on the request…",
    "Preparing the response…",
]
TEXT_FILE_MIME_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
}
TEXT_FILE_TYPES = {
    "bash",
    "c",
    "cpp",
    "csv",
    "html",
    "java",
    "javascript",
    "json",
    "markdown",
    "md",
    "php",
    "python",
    "ruby",
    "sql",
    "text",
    "txt",
    "typescript",
    "xml",
    "yaml",
}


def attachment_text(value: Any) -> str:
    """Normalize a legacy Slack attachment value without letting it dominate a prompt."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
        return text[:MAX_ATTACHMENT_TEXT_CHARS] + "\n[Attachment text truncated]"
    return text


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def default_workdir() -> Path:
    return Path(os.getenv("OPENTAG_WORKDIR", str(Path.cwd())))


def strip_mention(text: str) -> str:
    stripped = MENTION_RE.sub("", text).strip()
    return stripped or "Find the most relevant context for this thread and summarize it."


def format_message_attachments(message: dict[str, Any]) -> list[str]:
    """Expose integration previews (including Slack Email) to the backend prompt."""
    lines: list[str] = []
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        service = attachment_text(attachment.get("service_name")) or "Slack attachment"
        parts = [f"[{service}]"]
        for label, key in (("Title", "title"), ("From", "author_name"), ("Preview", "pretext"), ("Body", "text")):
            value = attachment_text(attachment.get(key))
            if value:
                parts.append(f"{label}: {value}")
        for field in attachment.get("fields") or []:
            if not isinstance(field, dict):
                continue
            title = attachment_text(field.get("title")) or "Field"
            value = attachment_text(field.get("value"))
            if value:
                parts.append(f"{title}: {value}")
        if len(parts) > 1:
            lines.append("\n".join(parts))
    return lines


def attachment_name(file: dict[str, Any], index: int) -> str:
    raw_name = file.get("name") or f"slack-image-{index}"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip(".-")
    return safe_name or f"slack-image-{index}"


def is_text_file(file: dict[str, Any]) -> bool:
    """Return whether a Slack file is safe to include as bounded prompt text."""
    mime_type = (file.get("mimetype") or "").lower()
    file_type = (file.get("filetype") or "").lower()
    return mime_type.startswith("text/") or mime_type in TEXT_FILE_MIME_TYPES or file_type in TEXT_FILE_TYPES


def download_file_bytes(url: str, token: str, expected_content_prefix: str | None = None) -> bytes:
    """Download one private Slack file while enforcing the attachment size limit."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    chunks: list[bytes] = []
    total = 0
    with urllib.request.urlopen(request, timeout=30) as response:
        if expected_content_prefix:
            content_type = response.headers.get_content_type()
            if content_type and not content_type.startswith(expected_content_prefix):
                raise ValueError(f"Slack returned {content_type}, not {expected_content_prefix.rstrip('/')}")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ATTACHMENT_BYTES:
                raise ValueError("file exceeds the 15 MB safety limit")
            chunks.append(chunk)
    return b"".join(chunks)


def download_thread_images(messages: list[dict[str, Any]], attachment_dir: Path) -> list[str]:
    """Download Slack image attachments for the current invocation only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message.get("files") or []:
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            mime_type = file.get("mimetype") or ""
            if not mime_type.startswith("image/"):
                continue
            url = file.get("url_private_download") or file.get("url_private")
            name = attachment_name(file, len(seen_file_ids))
            if not url:
                lines.append(f"[Slack image attachment could not be downloaded: {name}]")
                continue

            target = attachment_dir / name
            try:
                data = download_file_bytes(url, token, expected_content_prefix="image/")
                with target.open("wb") as output:
                    output.write(data)
                detected_type = mimetypes.guess_type(target.name)[0] or mime_type
                lines.append(f"[Slack image attachment: {name} ({detected_type}) at {target}]")
            except (OSError, urllib.error.URLError, ValueError) as exc:
                target.unlink(missing_ok=True)
                lines.append(f"[Could not retrieve Slack image {name}: {exc}]")
    return lines


def download_thread_text_files(messages: list[dict[str, Any]]) -> list[str]:
    """Read Slack snippets and text attachments into the current prompt only."""
    lines: list[str] = []
    seen_file_ids: set[str] = set()
    token = require_env("SLACK_BOT_TOKEN")

    for message in messages:
        for file in message.get("files") or []:
            file_id = file.get("id")
            if not file_id or file_id in seen_file_ids or not is_text_file(file):
                continue
            seen_file_ids.add(file_id)
            name = attachment_name(file, len(seen_file_ids))
            url = file.get("url_private_download") or file.get("url_private")
            if not url:
                lines.append(f"[Slack text attachment could not be downloaded: {name}]")
                continue
            try:
                text = download_file_bytes(url, token).decode("utf-8", errors="replace").strip()
                if len(text) > MAX_ATTACHMENT_TEXT_CHARS:
                    text = text[:MAX_ATTACHMENT_TEXT_CHARS] + "\n[Attachment text truncated]"
                if text:
                    lines.append(f"[Slack text attachment: {name}]\n{text}")
                else:
                    lines.append(f"[Slack text attachment was empty: {name}]")
            except (OSError, UnicodeError, urllib.error.URLError, ValueError) as exc:
                lines.append(f"[Could not retrieve Slack text attachment {name}: {exc}]")
    return lines


def build_thread_text(client: Any, channel: str, thread_ts: str, attachment_dir: Path) -> str:
    response = client.conversations_replies(channel=channel, ts=thread_ts, limit=30)
    messages = response.get("messages", [])
    lines = []
    for message in messages:
        user = message.get("user") or message.get("bot_id") or "unknown"
        text = message.get("text", "")
        lines.append(f"{user}: {text}")
        lines.extend(format_message_attachments(message))
    lines.extend(download_thread_text_files(messages))
    lines.extend(download_thread_images(messages, attachment_dir))
    return "\n".join(lines)


def split_reply(text: str, max_chars: int = MAX_REPLY_CHARS) -> list[str]:
    """Split a Slack reply at readable boundaries without losing generated text."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = max(
            remaining.rfind("\n\n", 0, max_chars + 1),
            remaining.rfind("\n", 0, max_chars + 1),
            remaining.rfind(" ", 0, max_chars + 1),
        )
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    chunks.append(remaining)
    return chunks


def env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class WorkingIndicator:
    """Prefer Slack's native agent status, with the old message as a fallback."""

    def __init__(self, client: Any, channel: str, thread_ts: str, logger: Any) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.logger = logger
        self.native = False
        self.message_ts: str | None = None
        self.refresh_timer: threading.Timer | None = None

    def set_native_status(self) -> None:
        self.client.assistant_threads_setStatus(
            channel_id=self.channel,
            thread_ts=self.thread_ts,
            status="is working on this…",
            loading_messages=LOADING_MESSAGES,
        )

    def schedule_refresh(self) -> None:
        self.refresh_timer = threading.Timer(STATUS_REFRESH_SECONDS, self.refresh)
        self.refresh_timer.daemon = True
        self.refresh_timer.start()

    def refresh(self) -> None:
        if not self.native:
            return
        try:
            self.set_native_status()
        except Exception as exc:  # noqa: BLE001 - a final answer can still be delivered
            self.logger.warning("Could not refresh native Slack loading status: %s", exc)
            self.native = False
            return
        self.schedule_refresh()

    def start(self) -> None:
        try:
            self.set_native_status()
            self.native = True
            self.schedule_refresh()
        except Exception as exc:  # noqa: BLE001 - Slack compatibility fallback
            self.logger.warning("Native Slack loading status is unavailable: %s", exc)
            response = self.client.chat_postMessage(
                channel=self.channel,
                thread_ts=self.thread_ts,
                text="Open Tag is working on this.",
            )
            self.message_ts = response["ts"]

    def clear(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.cancel()
            self.refresh_timer = None
        if not self.native:
            return
        try:
            self.client.assistant_threads_setStatus(
                channel_id=self.channel,
                thread_ts=self.thread_ts,
                status="",
            )
        except Exception as exc:  # noqa: BLE001 - final replies must still be delivered
            self.logger.warning("Could not clear native Slack loading status: %s", exc)
        finally:
            self.native = False


class SlackAnswerStream:
    """Batch answer deltas into Slack's streaming-message APIs."""

    def __init__(self, client: Any, channel: str, thread_ts: str, logger: Any) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.logger = logger
        self.pending = ""
        self.received = ""
        self.ts: str | None = None
        self.failed = False

    def append(self, text: str) -> None:
        if not text:
            return
        self.received += text
        self.pending += text
        if self.failed:
            return
        try:
            if self.ts is None and len(self.pending) >= STREAM_START_CHARS:
                response = self.client.chat_startStream(
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                    markdown_text=self.pending,
                )
                self.ts = response["ts"]
                self.pending = ""
            elif self.ts is not None and len(self.pending) >= STREAM_APPEND_CHARS:
                self.client.chat_appendStream(
                    channel=self.channel,
                    ts=self.ts,
                    markdown_text=self.pending,
                )
                self.pending = ""
        except Exception as exc:  # noqa: BLE001 - preserve the complete final answer
            self.failed = True
            self.logger.warning("Slack answer streaming failed; using a normal reply: %s", exc)

    def finish(self, final_text: str) -> bool:
        """Finalize a real delta stream; return False when a normal reply is safer."""
        if self.failed or not self.received:
            return False

        # The backend final event is authoritative. Most runs exactly match the
        # deltas; append a missing suffix when a backend omitted its last delta.
        if final_text.startswith(self.received):
            self.pending += final_text[len(self.received):]
            self.received = final_text
        elif final_text != self.received:
            self.logger.warning("Backend final text differed from streamed deltas")

        try:
            if self.ts is None:
                response = self.client.chat_startStream(
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                    markdown_text=self.pending,
                )
                self.ts = response["ts"]
                self.pending = ""
            elif self.pending:
                self.client.chat_appendStream(
                    channel=self.channel,
                    ts=self.ts,
                    markdown_text=self.pending,
                )
                self.pending = ""
            self.client.chat_stopStream(channel=self.channel, ts=self.ts)
            return True
        except Exception as exc:  # noqa: BLE001 - caller posts the full fallback reply
            self.failed = True
            self.logger.warning("Could not finalize Slack answer stream: %s", exc)
            return False

    def abort(self) -> None:
        if self.ts is None:
            return
        try:
            self.client.chat_stopStream(channel=self.channel, ts=self.ts)
        except Exception as exc:  # noqa: BLE001 - interruption cleanup is best effort
            self.logger.warning("Could not stop interrupted Slack answer stream: %s", exc)


def run_backend(
    backend: str,
    channel: str,
    caller_id: str,
    question: str,
    thread_text: str,
    attachment_dir: Path,
    timeout: int,
) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        "python3",
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        channel,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
        "--attachments-dir",
        str(attachment_dir),
        "--skill-dir",
        str(skill_dir()),
        "--workdir",
        str(default_workdir()),
        "--timeout",
        str(timeout),
    ]
    try:
        child_env = backend_environment(
            os.environ,
            transport="slack",
            conversation_id=channel,
            caller_id=caller_id,
        )
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout + 10,
            env=child_env,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            return f"Open Tag backend failed with exit code {result.returncode}:\n```text\n{output[-3000:]}\n```"
        return output or "Open Tag finished without output."
    finally:
        try:
            thread_file.unlink()
        except OSError:
            pass


def run_backend_events(
    backend: str,
    channel: str,
    caller_id: str,
    question: str,
    thread_text: str,
    attachment_dir: Path,
    timeout: int,
    on_delta: Callable[[str], None],
) -> tuple[str, bool]:
    """Consume normalized backend events and forward only answer deltas."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
        f.write(thread_text)
        thread_file = Path(f.name)

    cmd = [
        "python3",
        str(skill_dir() / "scripts" / "opentag_agent.py"),
        "--backend",
        backend,
        "--channel-id",
        channel,
        "--question",
        question,
        "--thread-file",
        str(thread_file),
        "--attachments-dir",
        str(attachment_dir),
        "--skill-dir",
        str(skill_dir()),
        "--workdir",
        str(default_workdir()),
        "--timeout",
        str(timeout),
        "--event-stream",
    ]
    child_env = backend_environment(
        os.environ,
        transport="slack",
        conversation_id=channel,
        caller_id=caller_id,
    )
    process = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env=child_env,
    )
    timed_out = threading.Event()

    def stop_process() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout + 10, stop_process)
    timer.start()
    final_text = ""
    delta_text: list[str] = []
    error_text = ""
    diagnostics: list[str] = []
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                diagnostics.append(line)
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            text = event.get("text")
            if not isinstance(text, str):
                continue
            if event_type == "delta":
                delta_text.append(text)
                on_delta(text)
            elif event_type == "final":
                final_text = text
            elif event_type == "error":
                error_text = text
        return_code = process.wait()
    finally:
        timer.cancel()
        thread_file.unlink(missing_ok=True)

    if timed_out.is_set():
        return f"Open Tag backend timed out after {timeout}s", False
    if return_code != 0:
        details = error_text or "\n".join(diagnostics)[-3000:].strip()
        return details or f"Open Tag backend failed with exit code {return_code}.", False
    answer = final_text or "".join(delta_text)
    return (answer or "Open Tag finished without output."), True


def post_final_reply(
    client: Any,
    channel: str,
    thread_ts: str,
    answer: str,
    placeholder_ts: str | None = None,
) -> None:
    chunks = split_reply(to_mrkdwn(answer))
    if placeholder_ts is not None:
        client.chat_update(channel=channel, ts=placeholder_ts, text=chunks[0], mrkdwn=True)
    else:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunks[0], mrkdwn=True)
    for chunk in chunks[1:]:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk, mrkdwn=True)


def suggested_bot_name(backend: str) -> str:
    if os.getenv("OPENTAG_BOT_NAME"):
        return os.environ["OPENTAG_BOT_NAME"]
    return "OpenMax"


def slack_channel_allowed(channel: str) -> bool:
    """Restrict Slack execution to the configured channel when one is set."""
    allowed_channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    return not allowed_channel or channel == allowed_channel


def print_live_summary(backend: str) -> None:
    bot = suggested_bot_name(backend)
    scopes = [s.strip() for s in os.getenv("MFS_ALLOWED_SCOPES", "").split(",") if s.strip()]
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip() or "(any joined channel)"
    invoke = {
        "claude": "claude -p --dangerously-skip-permissions",
        "codex": "codex exec --approve-for-me",
    }[backend]

    print("=" * 64)
    print(f"Open Tag is live as @{bot}")
    print(f"  Brain   : {backend}  ->  {invoke}")
    print(f"  Memory  : {len(scopes)} permitted MFS scope(s):")
    for scope in scopes or ["(none — set MFS_ALLOWED_SCOPES)"]:
        print(f"            - {scope}")
    print(f"  Slack   : listening for @mentions in channel {channel}")
    print("")
    print("  Anyone who can @mention the bot in that channel can drive the")
    print("  backend, which runs with your shell and inherited environment.")
    print("  Slack text flows into the prompt, so treat every mention as")
    print("  untrusted input: use an isolated channel on a non-production host.")
    print(f"  Invite the bot only where it should respond: /invite @{bot}")
    print("=" * 64)


def create_app(backend: str, timeout: int) -> App:
    app = App(token=require_env("SLACK_BOT_TOKEN"))

    @app.event("app_mention")
    def handle_mention(event: dict[str, Any], client: Any, logger: Any) -> None:
        channel = event["channel"]
        if not slack_channel_allowed(channel):
            logger.warning("Ignoring Open Tag mention from unapproved Slack channel %s", channel)
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        question = strip_mention(event.get("text", ""))

        indicator = WorkingIndicator(client, channel, thread_ts, logger)
        indicator.start()
        answer_stream: SlackAnswerStream | None = None

        try:
            with tempfile.TemporaryDirectory(prefix="opentag-slack-") as raw_attachment_dir:
                attachment_dir = Path(raw_attachment_dir)
                thread_text = build_thread_text(client, channel, thread_ts, attachment_dir)
                if env_enabled("OPENTAG_SLACK_STREAMING", default=True) and indicator.native:
                    answer_stream = SlackAnswerStream(client, channel, thread_ts, logger)
                    answer, succeeded = run_backend_events(
                        backend,
                        channel,
                        event.get("user", ""),
                        question,
                        thread_text,
                        attachment_dir,
                        timeout,
                        answer_stream.append,
                    )
                else:
                    answer = run_backend(
                        backend,
                        channel,
                        event.get("user", ""),
                        question,
                        thread_text,
                        attachment_dir,
                        timeout,
                    )
                    succeeded = True
            indicator.clear()
            if answer_stream is not None and succeeded and answer_stream.finish(answer):
                return
            if answer_stream is not None:
                answer_stream.abort()
            post_final_reply(client, channel, thread_ts, answer, indicator.message_ts)
        except Exception as exc:
            logger.exception("Open Tag failed")
            indicator.clear()
            if answer_stream is not None:
                answer_stream.abort()
            answer = f"Open Tag failed: `{type(exc).__name__}: {exc}`"
            post_final_reply(client, channel, thread_ts, answer, indicator.message_ts)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Open Tag Slack Socket Mode bridge.")
    parser.add_argument(
        "--backend",
        choices=["claude", "codex"],
        default=os.getenv("OPENTAG_BACKEND"),
    )
    parser.add_argument(
        "--timeout", type=int, default=int(os.getenv("OPENTAG_TIMEOUT_SECONDS", "420"))
    )
    args = parser.parse_args()
    if not args.backend:
        parser.error("--backend or OPENTAG_BACKEND is required")

    app = create_app(args.backend, args.timeout)
    print_live_summary(args.backend)
    SocketModeHandler(app, require_env("SLACK_APP_TOKEN")).start()


if __name__ == "__main__":
    main()
