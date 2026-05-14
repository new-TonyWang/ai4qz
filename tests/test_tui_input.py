from __future__ import annotations

import unittest
from pathlib import Path

import pyte
import urwid

from ai4qz.models import PersistentSession
from ai4qz.tui import RemoteTerminal, TUIApp


class DummyWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, payload: str) -> None:
        self.sent.append(payload)


class TUIInputTests(unittest.TestCase):
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

    def test_mouse_wheel_scrolls_history(self) -> None:
        screen = pyte.HistoryScreen(10, 8, history=20)
        stream = pyte.Stream(screen)
        stream.feed("".join(f"line{i}\n" for i in range(20)))
        terminal = RemoteTerminal(screen, None, lambda _text: None)
        terminal._scroll_offset = 0
        terminal.mouse_event((10, 8), "mouse press", 4, 0, 0, True)
        self.assertEqual(terminal._scroll_offset, 1)

        terminal.mouse_event((10, 8), "mouse press", 5, 0, 0, True)
        self.assertEqual(terminal._scroll_offset, 0)

    def test_page_scroll_still_moves_by_full_page(self) -> None:
        screen = pyte.HistoryScreen(10, 8, history=20)
        stream = pyte.Stream(screen)
        stream.feed("".join(f"line{i}\n" for i in range(20)))
        terminal = RemoteTerminal(screen, None, lambda _text: None)
        terminal._scroll_offset = 0
        terminal._scroll_page_up()
        self.assertEqual(terminal._scroll_offset, 8)

    def test_unhandled_ctrl_q_exits(self) -> None:
        app = TUIApp(DummyWS(), self._make_session())
        try:
            with self.assertRaises(urwid.ExitMainLoop):
                app._unhandled_input("ctrl q")
        finally:
            app.close()

    def test_unhandled_ctrl_right_bracket_exits(self) -> None:
        app = TUIApp(DummyWS(), self._make_session())
        try:
            with self.assertRaises(urwid.ExitMainLoop):
                app._unhandled_input("ctrl ]")
        finally:
            app.close()

    def test_unhandled_page_keys_scroll_terminal(self) -> None:
        app = TUIApp(DummyWS(), self._make_session())
        try:
            app.terminal._scroll_offset = 3
            handled = app._unhandled_input("page down")
            self.assertTrue(handled)
            self.assertEqual(app.terminal._scroll_offset, 0)
        finally:
            app.close()

    def test_scroll_offset_changes_visible_content(self) -> None:
        screen = pyte.HistoryScreen(10, 3, history=10)
        stream = pyte.Stream(screen)
        stream.feed("line1\nline2\nline3\nline4\n")
        terminal = RemoteTerminal(screen, None, lambda _text: None)

        base = terminal.render((10, 3), focus=True).decoded_text
        terminal._scroll_offset = 2
        scrolled = terminal.render((10, 3), focus=True).decoded_text

        self.assertNotEqual(base, scrolled)
        self.assertTrue(any("line2" in row for row in scrolled))


if __name__ == "__main__":
    unittest.main(verbosity=2)
