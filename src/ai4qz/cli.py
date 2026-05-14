from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .config import load_config, resolve_target
from .jupyter import JupyterNotebookClient
from .models import CommandResult, NotebookTarget, PersistentSession
from .session_store import SessionStore, project_root_from_config, resolved_target_from_session, session_store_path


def _default_config_path() -> str:
    env_value = os.environ.get("AI4QZ_CONFIG")
    if env_value:
        return env_value
    return str(Path(__file__).resolve().parents[2] / "configs" / "notebooks.yaml")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), ensure_ascii=False, indent=2))


def _resolve_config(args: argparse.Namespace):
    return load_config(args.config)


def _session_store(config) -> SessionStore:
    return SessionStore(session_store_path(config))


def _build_client(config, target_name: str, *, timeout: int | None = None) -> JupyterNotebookClient:
    target = config.get_target(target_name)
    resolved = resolve_target(target, config.defaults)
    defaults = config.defaults
    if timeout is not None:
        from dataclasses import replace
        defaults = replace(defaults, command_timeout_sec=timeout)
    return JupyterNotebookClient(resolved, defaults)


def _build_client_from_session(config, session: PersistentSession) -> JupyterNotebookClient:
    return JupyterNotebookClient(resolved_target_from_session(session), config.defaults)


def _extract_command(args: argparse.Namespace) -> str:
    if getattr(args, "cmd", None):
        return args.cmd
    remainder = list(getattr(args, "command", []) or [])
    if len(remainder) >= 2 and remainder[0] == "--cmd":
        return " ".join(remainder[1:]).strip()
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    command = " ".join(remainder).strip()
    if not command:
        raise SystemExit("missing command, use --cmd '<command>'")
    return command


def cmd_list(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    rows = []
    for notebook in config.notebooks:
        rows.append(
            {
                "name": notebook.name,
                "tags": notebook.tags,
                "base_url": notebook.base_url,
                "entry_url": notebook.entry_url,
                "cookies_file": str(notebook.cookies_file or config.defaults.cookies_file or ""),
            }
        )
    if args.json:
        _print_json(rows)
    else:
        for row in rows:
            tags = ",".join(row["tags"]) if row["tags"] else "-"
            base_state = row["base_url"] or "<discover at runtime>"
            print(f"{row['name']}\ttags={tags}\tbase={base_state}")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    client = _build_client(config, args.target)
    payload = {
        "name": client.target.name,
        "base_url": client.base_url,
        "resolved_from": client.target.resolved_from,
        "cookies_file": client.target.cookies_file,
        "notebook_id": client.target.notebook_id,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"name: {payload['name']}")
        print(f"base_url: {payload['base_url']}")
        print(f"resolved_from: {payload['resolved_from']}")
        print(f"cookies_file: {payload['cookies_file']}")
        print(f"notebook_id: {payload['notebook_id']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    client = _build_client(config, args.target)
    result = client.check(deep=args.deep)
    if args.json:
        _print_json(result)
    else:
        print(f"name: {result.name}")
        print(f"ok: {result.ok}")
        print(f"base_url: {result.base_url}")
        print(f"resolved_from: {result.resolved_from}")
        print(f"cookies_file: {result.cookies_file}")
        print(f"xsrf_found: {result.xsrf_found}")
        print(f"contents_api_ok: {result.contents_api_ok}")
        print(f"terminals: {result.terminal_count} {result.terminal_names}")
        if args.deep:
            print(f"probe_exit_code: {result.probe_exit_code}")
            print("probe_output:")
            print(result.probe_output.rstrip())
        if result.error:
            print(f"error: {result.error}")
    return 0 if result.ok else 1


def _print_command_result(result: CommandResult) -> None:
    status = "ok" if result.ok else "failed"
    rc = result.exit_code if result.exit_code is not None else "-"
    print(f"[{result.name}] status={status} rc={rc} seconds={result.seconds:.2f}")
    if result.output:
        print(result.output.rstrip())
    if result.error:
        print(f"[{result.name}] error: {result.error}")


def _timeout_from_args(args: argparse.Namespace) -> int | None:
    val = getattr(args, "timeout", None)
    return int(val) if val is not None else None


def cmd_run(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    client = _build_client(config, args.target, timeout=_timeout_from_args(args))
    result = client.run_command(_extract_command(args))
    if args.json:
        _print_json(result)
    else:
        _print_command_result(result)
    if result.exit_code is not None:
        return result.exit_code
    return 1


def _fanout_targets(config, args: argparse.Namespace) -> list[NotebookTarget]:
    names = None
    if args.targets:
        names = [item.strip() for item in args.targets.split(",") if item.strip()]
    tags = list(args.tag or [])
    return config.select_targets(names=names, tags=tags)


def cmd_fanout(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    command = _extract_command(args)
    selected = _fanout_targets(config, args)
    if not selected:
        raise SystemExit("no notebook matched the fanout selector")

    timeout = _timeout_from_args(args)
    defaults = config.defaults
    if timeout is not None:
        from dataclasses import replace
        defaults = replace(defaults, command_timeout_sec=timeout)

    results: list[CommandResult] = []
    concurrency = args.concurrency or config.defaults.concurrency
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {}
        for target in selected:
            future = pool.submit(
                lambda notebook=target: JupyterNotebookClient(
                    resolve_target(notebook, defaults),
                    defaults,
                ).run_command(command)
            )
            future_map[future] = target.name

        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda item: item.name)
    if args.json:
        _print_json(results)
    else:
        for result in results:
            _print_command_result(result)

    return 0 if all(item.ok for item in results) else 1


def cmd_upload(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    client = _build_client(config, args.target)
    metadata = client.upload_file(Path(args.local_path), args.remote_path)
    if args.json:
        _print_json(metadata)
    else:
        print(f"uploaded: {args.local_path} -> {args.remote_path}")
        print(f"size: {metadata.get('size')}")
        print(f"last_modified: {metadata.get('last_modified')}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    client = _build_client(config, args.target)
    metadata = client.download_file(args.remote_path, Path(args.local_path))
    if args.json:
        _print_json(metadata)
    else:
        print(f"downloaded: {args.remote_path} -> {args.local_path}")
        print(f"size: {metadata.get('size')}")
        print(f"last_modified: {metadata.get('last_modified')}")
    return 0


def cmd_session_open(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    store = _session_store(config)
    client = _build_client(config, args.target)
    session = client.open_persistent_session(
        cwd=args.cwd or "",
        use_tmux=args.tmux and not args.no_tmux,
        tmux_session_name=args.tmux_name,
    )
    store.upsert(session)
    if args.tui:
        session = client.ensure_tmux_session(session)
        store.upsert(session)
        client.attach_tui(session)
        return 0
    if args.json:
        _print_json(session)
    else:
        print(f"session_id: {session.session_id}")
        print(f"target: {session.target_name}")
        print(f"terminal_name: {session.terminal_name}")
        print(f"base_url: {session.base_url}")
        print(f"use_tmux: {session.use_tmux}")
        if session.tmux_session_name:
            print(f"tmux_session_name: {session.tmux_session_name}")
    return 0


def cmd_session_list(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    store = _session_store(config)
    sessions = store.list()
    if args.json:
        _print_json(sessions)
    else:
        if not sessions:
            print("no persistent sessions")
            return 0
        for session in sessions:
            print(
                f"{session.session_id}\ttarget={session.target_name}\tterminal={session.terminal_name}\t"
                f"tmux={session.tmux_session_name or '-'}\tlast_used_at={session.last_used_at}"
            )
    return 0


def cmd_session_run(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    store = _session_store(config)
    session = store.get(args.session_id)
    timeout = _timeout_from_args(args)
    defaults = config.defaults
    if timeout is not None:
        from dataclasses import replace
        defaults = replace(defaults, command_timeout_sec=timeout)
    client = JupyterNotebookClient(resolved_target_from_session(session), defaults)
    if not client.ensure_terminal_exists(session.terminal_name):
        print(f"session {session.session_id} terminal no longer exists", file=sys.stderr)
        return 1
    session = client.ensure_tmux_session(session)
    result = client.run_command_in_terminal(session.terminal_name, _extract_command(args))
    session.last_used_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    store.upsert(session)
    if args.json:
        _print_json(result)
    else:
        _print_command_result(result)
    if result.exit_code is not None:
        return result.exit_code
    return 1


def cmd_session_attach(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    store = _session_store(config)
    session = store.get(args.session_id)
    client = _build_client_from_session(config, session)
    if not client.ensure_terminal_exists(session.terminal_name):
        print(f"session {session.session_id} terminal no longer exists", file=sys.stderr)
        return 1
    session = client.ensure_tmux_session(session)
    print("attach started, press Ctrl-] to detach")
    session.last_used_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    store.upsert(session)
    client.attach_session(session)
    return 0


def cmd_session_close(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    store = _session_store(config)
    session = store.get(args.session_id)
    client = _build_client_from_session(config, session)
    if client.ensure_terminal_exists(session.terminal_name):
        client.close_persistent_session(session)
    store.delete(session.session_id)
    if args.json:
        _print_json({"closed": session.session_id})
    else:
        print(f"closed session: {session.session_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control qz notebooks from the local machine")
    parser.add_argument(
        "--config",
        default=_default_config_path(),
        help="path to notebooks yaml config",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable json")
    parser.add_argument("--timeout", type=int, help="command timeout in seconds (overrides config)")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    subparsers.add_parser("list", help="list configured notebook targets")

    discover = subparsers.add_parser("discover", help="resolve and print the target base_url")
    discover.add_argument("target")

    check = subparsers.add_parser("check", help="verify cookies, xsrf, terminals and contents")
    check.add_argument("target")
    check.add_argument("--deep", action="store_true", help="run a real pwd probe")

    run = subparsers.add_parser("run", help="execute one command on one notebook")
    run.add_argument("target")
    run.add_argument("--cmd", help="command string to run")
    run.add_argument("command", nargs=argparse.REMAINDER)

    fanout = subparsers.add_parser("fanout", help="execute one command on multiple notebooks")
    fanout.add_argument("--targets", help="comma separated notebook names")
    fanout.add_argument("--tag", action="append", help="repeatable tag filter")
    fanout.add_argument("--concurrency", type=int, help="override default fanout concurrency")
    fanout.add_argument("--cmd", help="command string to run")
    fanout.add_argument("command", nargs=argparse.REMAINDER)

    upload = subparsers.add_parser("upload", help="upload a local file to a notebook")
    upload.add_argument("target")
    upload.add_argument("local_path")
    upload.add_argument("remote_path")

    download = subparsers.add_parser("download", help="download a notebook file to local disk")
    download.add_argument("target")
    download.add_argument("remote_path")
    download.add_argument("local_path")

    session_open = subparsers.add_parser("session-open", help="create a persistent notebook shell session")
    session_open.add_argument("target")
    session_open.add_argument("--cwd", help="optional working directory for terminal creation")
    session_open.add_argument("--tmux", action="store_true", help="initialize and attach to a remote tmux session")
    session_open.add_argument("--no-tmux", action="store_true", help="do not initialize tmux")
    session_open.add_argument("--tmux-name", default="ai4qz", help="remote tmux session name")
    session_open.add_argument("--tui", action="store_true", help="open session and launch interactive TUI")

    subparsers.add_parser("session-list", help="list locally tracked persistent sessions")

    session_run = subparsers.add_parser("session-run", help="run a command inside an existing persistent session")
    session_run.add_argument("session_id")
    session_run.add_argument("--cmd", help="command string to run")
    session_run.add_argument("command", nargs=argparse.REMAINDER)

    session_attach = subparsers.add_parser("session-attach", help="attach local terminal to a persistent session")
    session_attach.add_argument("session_id")

    session_close = subparsers.add_parser("session-close", help="close a persistent session and delete its terminal")
    session_close.add_argument("session_id")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "discover": cmd_discover,
        "check": cmd_check,
        "run": cmd_run,
        "fanout": cmd_fanout,
        "upload": cmd_upload,
        "download": cmd_download,
        "session-open": cmd_session_open,
        "session-list": cmd_session_list,
        "session-run": cmd_session_run,
        "session-attach": cmd_session_attach,
        "session-close": cmd_session_close,
    }
    return handlers[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
