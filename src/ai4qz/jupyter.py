from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import select
import re
import sys
import termios
import time
import tty
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import websocket

from .cookies import build_cookie_header, build_requests_session, find_xsrf_token, load_cookiejar
from .models import CheckResult, CommandResult, Defaults, PersistentSession, ResolvedTarget


class TerminalExecutionError(RuntimeError):
    def __init__(self, message: str, partial_output: str = "") -> None:
        super().__init__(message)
        self.partial_output = partial_output


class JupyterNotebookClient:
    ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    DONE_MARKER_RE = re.compile(r"__AI4QZ_DONE__.*__RC=\d+__")
    READY_MARKER_RE = re.compile(r"__AI4QZ_READY__.*__")

    def __init__(self, target: ResolvedTarget, defaults: Defaults) -> None:
        self.target = target
        self.defaults = defaults
        self.cookiejar = load_cookiejar(target.cookies_file)
        self.session = build_requests_session(self.cookiejar)
        self.base_url = target.base_url.rstrip("/")
        self.origin = self._origin_from_base_url(self.base_url)
        self.base_path = urlparse(self.base_url).path
        self.xsrf_token = find_xsrf_token(self.cookiejar, self.base_url)

    @staticmethod
    def _origin_from_base_url(base_url: str) -> str:
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _headers(
        self,
        *,
        include_xsrf: bool = False,
        content_type: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Origin": self.origin,
            "Referer": f"{self.base_url}/lab",
        }
        if include_xsrf:
            if not self.xsrf_token:
                raise RuntimeError(f"{self.target.name}: missing _xsrf cookie for {self.base_url}")
            headers["X-XSRFToken"] = self.xsrf_token
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        include_xsrf: bool = False,
        timeout: int = 30,
        retries: int = 0,
        **kwargs: object,
    ) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = dict(kwargs.pop("headers", {}))
        headers = {**self._headers(include_xsrf=include_xsrf), **headers}
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    **kwargs,
                )
                if response.status_code >= 400:
                    body = response.text[:400].strip()
                    raise RuntimeError(
                        f"{self.target.name}: {method} {url} failed with {response.status_code}: {body}"
                    )
                return response
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as exc:
                last_error = exc
                if attempt >= retries:
                    raise
                time.sleep(0.6 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{self.target.name}: request failed unexpectedly for {method} {url}")

    @staticmethod
    def _contents_path(remote_path: str) -> str:
        cleaned = remote_path.strip("/")
        if not cleaned:
            return "api/contents"
        return f"api/contents/{quote(cleaned, safe='/')}"

    def list_terminals(self) -> list[dict]:
        response = self._request("GET", "api/terminals", retries=2)
        return response.json()

    def get_contents_metadata(self, remote_path: str = "") -> dict:
        response = self._request(
            "GET",
            self._contents_path(remote_path),
            params={"content": 0},
            retries=2,
        )
        return response.json()

    def create_terminal(self, cwd: str = "") -> str:
        payload = json.dumps({"cwd": cwd})
        response = self._request(
            "POST",
            "api/terminals",
            include_xsrf=True,
            data=payload,
            headers={"Content-Type": "text/plain;charset=UTF-8"},
            retries=2,
        )
        data = response.json()
        return str(data["name"])

    def delete_terminal(self, name: str) -> None:
        self._request("DELETE", f"api/terminals/{name}", include_xsrf=True)

    def _ensure_remote_directory(self, remote_dir: str) -> None:
        cleaned = remote_dir.strip("/")
        if not cleaned:
            return
        current = []
        for part in cleaned.split("/"):
            current.append(part)
            path = "/".join(current)
            payload = json.dumps({"type": "directory"})
            try:
                self._request(
                    "PUT",
                    self._contents_path(path),
                    include_xsrf=True,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
            except RuntimeError as exc:
                message = str(exc)
                if "409" in message or "file already exists" in message:
                    continue
                if "400" in message and "directory already exists" in message:
                    continue
                raise

    def upload_file(self, local_path: Path, remote_path: str) -> dict:
        data = local_path.read_bytes()
        parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path.strip("/") else ""
        self._ensure_remote_directory(parent)
        payload = {
            "type": "file",
            "format": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
        response = self._request(
            "PUT",
            self._contents_path(remote_path),
            include_xsrf=True,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def download_file(self, remote_path: str, local_path: Path) -> dict:
        response = self._request(
            "GET",
            self._contents_path(remote_path),
            params={"content": 1, "format": "base64"},
        )
        data = response.json()
        content = data.get("content", "")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(base64.b64decode(content))
        return data

    def check(self, *, deep: bool = False) -> CheckResult:
        try:
            terminals = self.list_terminals()
            contents = self.get_contents_metadata("")
            probe = self.run_command("pwd") if deep else None
            return CheckResult(
                name=self.target.name,
                ok=probe.ok if probe else True,
                base_url=self.base_url,
                cookies_file=self.target.cookies_file,
                resolved_from=self.target.resolved_from,
                xsrf_found=bool(self.xsrf_token),
                terminal_count=len(terminals),
                terminal_names=[str(item["name"]) for item in terminals],
                contents_api_ok=contents.get("type") == "directory",
                probe_exit_code=probe.exit_code if probe else None,
                probe_output=probe.output if probe else "",
            )
        except Exception as exc:
            return CheckResult(
                name=self.target.name,
                ok=False,
                base_url=self.base_url,
                cookies_file=self.target.cookies_file,
                resolved_from=self.target.resolved_from,
                xsrf_found=bool(self.xsrf_token),
                error=str(exc),
            )

    def _websocket_url(self, terminal_name: str) -> str:
        return self.base_url.replace("https://", "wss://").replace(
            "http://", "ws://"
        ) + f"/terminals/websocket/{terminal_name}"

    def _open_terminal_socket(self, terminal_name: str) -> websocket.WebSocket:
        ws_url = self._websocket_url(terminal_name)
        parsed = urlparse(ws_url)
        cookie_header = build_cookie_header(self.cookiejar, ws_url)
        return websocket.create_connection(
            ws_url,
            timeout=self.defaults.connect_timeout_sec,
            cookie=cookie_header,
            origin=self.origin,
            host=parsed.netloc,
            header=[
                "Pragma: no-cache",
                "Cache-Control: no-cache",
                "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits",
            ],
        )

    @staticmethod
    def _recv_stdout(ws: websocket.WebSocket, timeout_sec: float) -> str:
        ws.settimeout(timeout_sec)
        raw = ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        message = json.loads(raw)
        if not isinstance(message, list) or not message:
            return ""
        kind = message[0]
        if kind == "stdout":
            return str(message[1])
        if kind == "disconnect":
            raise TerminalExecutionError("websocket disconnected")
        return ""

    def _drain_socket(self, ws: websocket.WebSocket, idle_timeout_sec: float = 0.35) -> str:
        chunks: list[str] = []
        while True:
            try:
                chunk = self._recv_stdout(ws, idle_timeout_sec)
            except websocket.WebSocketTimeoutException:
                break
            if chunk:
                chunks.append(chunk)
        return "".join(chunks)

    def _send_stdin(self, ws: websocket.WebSocket, text: str) -> None:
        ws.send(json.dumps(["stdin", text]))

    def _send_resize(self, ws: websocket.WebSocket) -> None:
        ws.send(json.dumps(["set_size", self.defaults.rows, self.defaults.cols, 0, 0]))

    def _prepare_terminal(self, ws: websocket.WebSocket) -> str:
        self._drain_socket(ws)
        self._send_resize(ws)
        self._drain_socket(ws)
        return self._sync_terminal(ws)

    def _sync_terminal(self, ws: websocket.WebSocket) -> str:
        token = uuid.uuid4().hex
        marker = f"__AI4QZ_READY__{token}__"
        self._send_stdin(ws, "stty -echo\r")
        self._drain_socket(ws)
        self._send_stdin(ws, f"printf '\\n{marker}\\n'\r")
        started = time.monotonic()
        buffer = ""
        while time.monotonic() - started < self.defaults.connect_timeout_sec:
            try:
                chunk = self._recv_stdout(ws, 0.5)
            except websocket.WebSocketTimeoutException:
                continue
            buffer += chunk
            if marker in buffer:
                return marker
        raise TerminalExecutionError("terminal did not reach ready state", buffer)

    def _sanitize_output(self, text: str, *, ready_marker: str, command: str) -> str:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = self.ANSI_ESCAPE_RE.sub("", cleaned)
        if ready_marker in cleaned:
            cleaned = cleaned.split(ready_marker, 1)[1]
        lines = [line.rstrip() for line in cleaned.split("\n")]
        filtered: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if filtered and filtered[-1] != "":
                    filtered.append("")
                continue
            if stripped == "stty -echo":
                continue
            if stripped.startswith("Agent pid "):
                continue
            if stripped.startswith("Identity added: "):
                continue
            if "__AI4QZ_RC=$?" in stripped:
                continue
            if self.DONE_MARKER_RE.search(stripped):
                continue
            if self.READY_MARKER_RE.search(stripped):
                continue
            if re.match(r"^\[.*\]\$\s*$", stripped):
                continue
            if re.match(r"^\[.*\]\$\s+stty -echo$", stripped):
                continue
            filtered.append(line)

        while filtered and filtered[0] == "":
            filtered.pop(0)
        while filtered and filtered[-1] == "":
            filtered.pop()
        return "\n".join(filtered)

    def _run_over_websocket(
        self,
        ws: websocket.WebSocket,
        command: str,
        timeout_sec: int,
    ) -> tuple[str, int]:
        ready_marker = self._prepare_terminal(ws)

        token = uuid.uuid4().hex
        marker_prefix = f"__AI4QZ_DONE__{token}__RC="
        wrapped = (
            f"{command}; __AI4QZ_RC=$?; "
            f"printf '\\n{marker_prefix}%s__\\n' \"$__AI4QZ_RC\"\r"
        )
        self._send_stdin(ws, wrapped)

        started = time.monotonic()
        buffer = ""
        exit_code: int | None = None
        marker_pattern = re.compile(
            rf"{re.escape(marker_prefix)}(?P<code>\d+)__",
            re.MULTILINE,
        )
        while time.monotonic() - started < timeout_sec:
            try:
                chunk = self._recv_stdout(ws, 0.5)
            except websocket.WebSocketTimeoutException:
                continue
            buffer += chunk
            match = marker_pattern.search(buffer)
            if match:
                exit_code = int(match.group("code"))
                buffer = buffer[: match.start()]
                break

        try:
            self._send_stdin(ws, "stty echo\r")
            self._drain_socket(ws)
        except Exception:
            pass

        if exit_code is None:
            raise TerminalExecutionError("command timed out before sentinel arrived", buffer)

        cleaned = self._sanitize_output(buffer, ready_marker=ready_marker, command=command)
        return cleaned, exit_code

    def run_command_in_terminal(
        self,
        terminal_name: str,
        command: str,
    ) -> CommandResult:
        started = time.monotonic()
        partial_output = ""
        try:
            ws = self._open_terminal_socket(terminal_name)
            try:
                output, exit_code = self._run_over_websocket(
                    ws,
                    command,
                    timeout_sec=self.defaults.command_timeout_sec,
                )
            finally:
                ws.close()
            seconds = time.monotonic() - started
            return CommandResult(
                name=self.target.name,
                ok=exit_code == 0,
                exit_code=exit_code,
                output=output,
                terminal_name=terminal_name,
                seconds=seconds,
            )
        except TerminalExecutionError as exc:
            partial_output = exc.partial_output.replace("\r\n", "\n").replace("\r", "\n")
            seconds = time.monotonic() - started
            return CommandResult(
                name=self.target.name,
                ok=False,
                exit_code=None,
                output=partial_output.strip(),
                terminal_name=terminal_name,
                seconds=seconds,
                error=str(exc),
            )
        except Exception as exc:
            seconds = time.monotonic() - started
            return CommandResult(
                name=self.target.name,
                ok=False,
                exit_code=None,
                output=partial_output,
                terminal_name=terminal_name,
                seconds=seconds,
                error=str(exc),
            )

    def run_command(self, command: str, *, cwd: str = "") -> CommandResult:
        started = time.monotonic()
        terminal_name: str | None = None
        try:
            terminal_name = self.create_terminal(cwd=cwd)
            result = self.run_command_in_terminal(terminal_name, command)
            result.seconds = time.monotonic() - started
            return result
        finally:
            if terminal_name:
                try:
                    self.delete_terminal(terminal_name)
                except Exception:
                    pass

    def open_persistent_session(
        self,
        *,
        cwd: str = "",
        use_tmux: bool = True,
        tmux_session_name: str = "ai4qz",
    ) -> PersistentSession:
        terminal_name = self.create_terminal(cwd=cwd)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
        session = PersistentSession(
            session_id=uuid.uuid4().hex[:12],
            target_name=self.target.name,
            terminal_name=terminal_name,
            base_url=self.base_url,
            cookies_file=self.target.cookies_file,
            notebook_id=self.target.notebook_id,
            resolved_from=self.target.resolved_from,
            created_at=now,
            last_used_at=now,
            cwd=cwd,
            use_tmux=use_tmux,
            tmux_session_name=tmux_session_name if use_tmux else None,
            notes=self.target.notes,
        )
        return session

    def ensure_terminal_exists(self, terminal_name: str) -> bool:
        terminals = self.list_terminals()
        return any(str(item["name"]) == str(terminal_name) for item in terminals)

    def close_persistent_session(self, session: PersistentSession) -> None:
        self.delete_terminal(session.terminal_name)

    def ensure_tmux_session(self, session: PersistentSession) -> PersistentSession:
        if not session.use_tmux or not session.tmux_session_name:
            return session
        result = self.run_command_in_terminal(
            session.terminal_name,
            (
                "if [ -n \"$TMUX\" ]; then "
                "printf 'tmux_nested\\n'; "
                "elif command -v tmux >/dev/null 2>&1; then "
                f"TMUX= tmux new-session -Ad -s {session.tmux_session_name} && printf 'tmux_ready\\n'; "
                "else printf 'tmux_missing\\n'; "
                "fi"
            ),
        )
        if (
            "tmux_missing" in result.output
            or "tmux_nested" in result.output
            or "sessions should be nested with care" in result.output
        ):
            session.use_tmux = False
            session.tmux_session_name = None
            return session
        if not result.ok or "tmux_ready" not in result.output:
            raise RuntimeError(
                f"{self.target.name}: failed to initialize tmux session {session.tmux_session_name}"
            )
        return session

    def attach_tui(self, session: PersistentSession) -> None:
        from .tui import run_tui

        ws = self._open_terminal_socket(session.terminal_name)
        self._drain_socket(ws)
        self._send_resize(ws)
        self._drain_socket(ws)
        if session.use_tmux and session.tmux_session_name:
            self._send_stdin(ws, f"TMUX= tmux attach -t {session.tmux_session_name}\r")
            time.sleep(0.5)
            self._drain_socket(ws)
        try:
            run_tui(ws, session)
        finally:
            ws.close()

    def attach_session(self, session: PersistentSession) -> None:
        ws = self._open_terminal_socket(session.terminal_name)
        self._drain_socket(ws)
        self._send_resize(ws)
        self._drain_socket(ws)
        if session.use_tmux and session.tmux_session_name:
            self._send_stdin(ws, f"TMUX= tmux attach -t {session.tmux_session_name}\r")
            time.sleep(0.5)
            self._drain_socket(ws)

        old_tty = None
        stdin_fd = None
        try:
            if sys.stdin.isatty():
                stdin_fd = sys.stdin.fileno()
                old_tty = termios.tcgetattr(stdin_fd)
                tty.setraw(stdin_fd)

            ws.settimeout(0.1)
            while True:
                with contextlib.suppress(websocket.WebSocketTimeoutException):
                    raw = ws.recv()
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    message = json.loads(raw)
                    if isinstance(message, list) and message:
                        if message[0] == "stdout":
                            sys.stdout.write(str(message[1]))
                            sys.stdout.flush()
                        elif message[0] == "disconnect":
                            raise RuntimeError("remote terminal disconnected")

                if stdin_fd is not None:
                    ready, _, _ = select.select([stdin_fd], [], [], 0.05)
                    if ready:
                        data = os.read(stdin_fd, 1024)
                        if not data:
                            break
                        if b"\x1d" in data:
                            break
                        text = data.decode("utf-8", errors="ignore")
                        if text:
                            self._send_stdin(ws, text)
                else:
                    break
        finally:
            if stdin_fd is not None and old_tty is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
            ws.close()
