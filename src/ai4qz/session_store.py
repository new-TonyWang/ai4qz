from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import PersistentSession, ProjectConfig, ResolvedTarget


def project_root_from_config(config: ProjectConfig) -> Path:
    config_path = config.path.resolve()
    if config_path.parent.name == "configs":
        return config_path.parent.parent
    return config_path.parent


def session_store_path(config: ProjectConfig) -> Path:
    return project_root_from_config(config) / ".ai4qz" / "sessions.json"


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, PersistentSession]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        sessions: dict[str, PersistentSession] = {}
        for item in raw.get("sessions", []):
            sessions[item["session_id"]] = PersistentSession(
                session_id=item["session_id"],
                target_name=item["target_name"],
                terminal_name=item["terminal_name"],
                base_url=item["base_url"],
                cookies_file=Path(item["cookies_file"]),
                notebook_id=item.get("notebook_id"),
                resolved_from=item["resolved_from"],
                created_at=item["created_at"],
                last_used_at=item["last_used_at"],
                cwd=item.get("cwd", ""),
                use_tmux=bool(item.get("use_tmux", True)),
                tmux_session_name=item.get("tmux_session_name"),
                notes=item.get("notes"),
            )
        return sessions

    def _write_all(self, sessions: dict[str, PersistentSession]) -> None:
        payload = {
            "sessions": [
                {
                    **asdict(session),
                    "cookies_file": str(session.cookies_file),
                }
                for session in sorted(sessions.values(), key=lambda item: item.session_id)
            ]
        }
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)

    def list(self) -> list[PersistentSession]:
        return sorted(self._read_all().values(), key=lambda item: item.session_id)

    def get(self, session_id: str) -> PersistentSession:
        sessions = self._read_all()
        try:
            return sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc

    def upsert(self, session: PersistentSession) -> None:
        sessions = self._read_all()
        sessions[session.session_id] = session
        self._write_all(sessions)

    def delete(self, session_id: str) -> PersistentSession:
        sessions = self._read_all()
        try:
            session = sessions.pop(session_id)
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc
        self._write_all(sessions)
        return session

    def exists(self, session_id: str) -> bool:
        return session_id in self._read_all()


def resolved_target_from_session(session: PersistentSession) -> ResolvedTarget:
    return ResolvedTarget(
        name=session.target_name,
        base_url=session.base_url,
        cookies_file=session.cookies_file,
        notebook_id=session.notebook_id,
        resolved_from=session.resolved_from,
        notes=session.notes,
    )
