from __future__ import annotations

import queue
import unittest

import pyte

from ai4qz.tui import RemoteTerminal


class RemoteTerminalCursorTests(unittest.TestCase):
    def test_render_exposes_cursor_position(self) -> None:
        screen = pyte.HistoryScreen(10, 3, history=10)
        stream = pyte.Stream(screen)
        stream.feed("abc")
        terminal = RemoteTerminal(screen, queue.Queue(), lambda _text: None)

        canvas = terminal.render((10, 3), focus=True)

        self.assertEqual(canvas.cursor, (3, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
