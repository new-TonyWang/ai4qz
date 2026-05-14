from __future__ import annotations

import unittest
from pathlib import Path

import pyte

from ai4qz.models import PersistentSession
from ai4qz.tui import TUIApp


class DummyWS:
    def send(self, _payload: str) -> None:
        pass


class TUIStatusTests(unittest.TestCase):
    def _make_session(self) -> PersistentSession:
        return PersistentSession(
            session_id="0123456789ab",
            target_name="qz_dev",
            terminal_name="1",
            base_url="https://example.invalid",
            cookies_file=Path("/tmp/cookies.txt"),
            notebook_id="nb",
            resolved_from="config",
            created_at="2026-05-11T00:00:00+0800",
            last_used_at="2026-05-11T00:00:00+0800",
            use_tmux=False,
        )

    def test_status_text_is_clipped_to_terminal_width(self) -> None:
        app = TUIApp(DummyWS(), self._make_session())
        try:
            app.screen = pyte.HistoryScreen(20, 5, history=10)
            text = app._status_text()
            self.assertEqual(len(text), 20)
        finally:
            app.close()

    def test_status_bar_uses_clip_wrap_mode(self) -> None:
        app = TUIApp(DummyWS(), self._make_session())
        try:
            self.assertEqual(app.status_bar.wrap, "clip")
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
