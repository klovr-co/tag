#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import configparser
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def token_from_env() -> str | None:
    if env("MFS_TOKEN"):
        return env("MFS_TOKEN")
    token_file = Path.home() / ".mfs" / "server.token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None


def print_check(ok: bool, label: str, detail: str = "") -> None:
    status = "ok" if ok else "fail"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def request_json(
    url: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[bool, dict[str, Any]]:
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {"error": str(exc)}
        return False, body
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def slack_api(
    method: str, token: str, params: dict[str, str] | None = None
) -> tuple[bool, dict[str, Any]]:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    ok, data = request_json(f"https://slack.com/api/{method}{query}", token=token)
    return ok and bool(data.get("ok")), data


def selected_transport() -> str:
    transport = env("OPENTAG_TRANSPORT") or "slack"
    if transport in {"slack", "zulip", "both"}:
        return transport
    print_check(False, "OPENTAG_TRANSPORT", "must be slack, zulip, or both")
    return ""


def check_env(transport: str) -> bool:
    required = [
        "MFS_URL",
        "MFS_ALLOWED_SCOPES",
        "OPENTAG_BACKEND",
    ]
    if transport in {"slack", "both"}:
        required.extend(["SLACK_APP_TOKEN", "SLACK_BOT_TOKEN"])
    if transport in {"zulip", "both"}:
        required.append("ZULIP_CONFIG_FILE")
    all_ok = True
    for name in required:
        value = env(name)
        ok = bool(value)
        all_ok = all_ok and ok
        detail = "set" if ok else "missing"
        if name == "SLACK_APP_TOKEN" and value:
            detail = (
                "set, expected xapp-* token"
                if value.startswith("xapp-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xapp-")
        if name == "SLACK_BOT_TOKEN" and value:
            detail = (
                "set, expected xoxb-* token"
                if value.startswith("xoxb-")
                else "set, unexpected prefix"
            )
            ok = value.startswith("xoxb-")
        print_check(ok, name, detail)
        all_ok = all_ok and ok

    mfs_token = token_from_env()
    print_check(
        bool(mfs_token),
        "MFS_TOKEN or ~/.mfs/server.token",
        "available" if mfs_token else "missing",
    )
    return all_ok and bool(mfs_token)


def check_mfs(scopes: list[str]) -> bool:
    base = env("MFS_URL").rstrip("/") or "http://127.0.0.1:13619"
    token = token_from_env()
    ok, data = request_json(f"{base}/healthz")
    print_check(ok, "MFS healthz", data.get("status") or data.get("error", "reachable"))
    if not ok:
        print(f"       hint: MFS server is not reachable at {base}.")
        print("       Install and start it first: uv tool install mfs-server && mfs-server run")
    all_ok = ok

    ok, data = request_json(f"{base}/v1/status", token=token)
    connector_count = len(data.get("connectors") or []) if isinstance(data, dict) else 0
    print_check(
        ok,
        "MFS /v1/status",
        f"{connector_count} connectors" if ok else str(data.get("error")),
    )
    if ok and connector_count == 0:
        print("       hint: no sources indexed yet. Add one with the mfs-ingest skill.")
    all_ok = all_ok and ok

    for scope in scopes:
        params = urllib.parse.urlencode({"path": scope})
        ok, data = request_json(f"{base}/v1/ls?{params}", token=token)
        detail = "listed" if ok else str(data.get("error") or data.get("detail"))
        print_check(ok, f"MFS scope {scope}", detail)
        all_ok = all_ok and ok
    return all_ok


def check_slack(channel_id: str | None) -> bool:
    bot_token = env("SLACK_BOT_TOKEN")
    all_ok = True
    ok, data = slack_api("auth.test", bot_token)
    detail = data.get("team") or data.get("error") or "authenticated"
    print_check(ok, "Slack bot auth.test", detail)
    all_ok = all_ok and ok

    if channel_id:
        ok, data = slack_api("conversations.info", bot_token, {"channel": channel_id})
        if ok:
            channel = data.get("channel") or {}
            detail = f"name={channel.get('name')}, member={channel.get('is_member')}, private={channel.get('is_private')}"
        else:
            detail = data.get("error") or "failed"
        print_check(ok, f"Slack channel {channel_id}", detail)
        all_ok = all_ok and ok

        ok, data = slack_api(
            "conversations.history", bot_token, {"channel": channel_id, "limit": "1"}
        )
        detail = "history readable" if ok else data.get("error") or "failed"
        print_check(ok, f"Slack channel history {channel_id}", detail)
        all_ok = all_ok and ok

    return all_ok


def read_zuliprc(path_value: str) -> tuple[bool, dict[str, str], str]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        return False, {}, f"file not found: {path}"

    parser = configparser.ConfigParser()
    try:
        parser.read(path)
        config = parser["api"]
    except (configparser.Error, KeyError) as exc:
        return False, {}, f"invalid zuliprc: {exc}"

    values = {name: config.get(name, "").strip() for name in ("site", "email", "key")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        return False, {}, f"missing {', '.join(missing)} in [api]"
    return True, values, "loaded"


def check_zulip_config(path_value: str, label: str) -> bool:
    ok, values, detail = read_zuliprc(path_value)
    print_check(ok, f"{label} config", detail)
    if not ok:
        return False

    credentials = f"{values['email']}:{values['key']}".encode()
    authorization = base64.b64encode(credentials).decode()
    url = f"{values['site'].rstrip('/')}/api/v1/users/me"
    ok, data = request_json(url, headers={"Authorization": f"Basic {authorization}"})
    identity = data.get("full_name") or data.get("email") or data.get("msg") or data.get("error")
    print_check(ok and data.get("result") == "success", f"{label} auth", str(identity))
    return ok and data.get("result") == "success"


def check_zulip() -> bool:
    all_ok = check_zulip_config(env("ZULIP_CONFIG_FILE"), "Zulip bot")
    if env("ZULIP_AUTO_GRANT_PRIVATE_HISTORY").lower() == "true":
        admin_path = env("ZULIP_ADMIN_CONFIG_FILE")
        if not admin_path:
            print_check(False, "ZULIP_ADMIN_CONFIG_FILE", "required when auto-grant is enabled")
            return False
        all_ok = check_zulip_config(admin_path, "Zulip admin") and all_ok
    return all_ok


def check_backend() -> bool:
    backend = env("OPENTAG_BACKEND")
    if backend == "claude":
        ok = shutil.which("claude") is not None
        print_check(
            ok,
            "backend claude",
            "claude executable found" if ok else "claude executable missing",
        )
        return ok
    if backend == "codex":
        ok = shutil.which("codex") is not None
        print_check(
            ok,
            "backend codex",
            "codex executable found" if ok else "codex executable missing",
        )
        return ok
    print_check(False, "OPENTAG_BACKEND", "must be claude or codex")
    return False


def check_gws() -> bool:
    if env("OPENTAG_GWS_ENABLED").lower() not in {"1", "true", "yes", "on"}:
        return True

    allowed_callers = [value.strip() for value in env("OPENTAG_GWS_ALLOWED_CALLERS").split(",") if value.strip()]
    callers_ok = bool(allowed_callers)
    print_check(
        callers_ok,
        "OPENTAG_GWS_ALLOWED_CALLERS",
        f"{len(allowed_callers)} permitted caller(s)" if callers_ok else "required when GWS is enabled",
    )

    gws_path = shutil.which("gws")
    print_check(bool(gws_path), "GWS CLI", "found" if gws_path else "missing")
    if not gws_path:
        return False

    result = subprocess.run(
        [gws_path, "auth", "status"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
    )
    try:
        status = json.loads(result.stdout[result.stdout.find("{") :])
    except (json.JSONDecodeError, ValueError):
        status = {}
    token_ok = bool(status.get("token_valid"))
    detail = "authenticated" if token_ok else status.get("token_error", "token invalid or unavailable")
    print_check(token_ok, "GWS Gmail authentication", str(detail))
    if not token_ok:
        print("       hint: run `gws auth login -s gmail` and complete the browser consent flow.")
    return callers_ok and token_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight an Open Tag Slack + MFS setup.")
    parser.add_argument("--channel-id", help="Optional Slack channel ID to verify bot access.")
    args = parser.parse_args()

    transport = selected_transport()
    if not transport:
        return 1
    scopes = [scope.strip() for scope in env("MFS_ALLOWED_SCOPES").split(",") if scope.strip()]
    checks = [
        check_env(transport),
        check_mfs(scopes) if scopes else False,
        check_backend(),
        check_gws(),
    ]
    if transport in {"slack", "both"}:
        checks.append(check_slack(args.channel_id))
    if transport in {"zulip", "both"}:
        checks.append(check_zulip())
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
