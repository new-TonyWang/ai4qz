from __future__ import annotations

import asyncio
import json
import os
import queue
import signal
import threading
from typing import TYPE_CHECKING

import pyte
import urwid

if TYPE_CHECKING:
    import websocket

    from .models import PersistentSession

# pyte color name → urwid color name
_PYTE_FG_MAP = {
    "black": "black",
    "red": "dark red",
    "green": "dark green",
    "brown": "brown",
    "blue": "dark blue",
    "magenta": "dark magenta",
    "cyan": "dark cyan",
    "white": "light gray",
    "default": "default",
    "brightblack": "dark gray",
    "brightred": "light red",
    "brightgreen": "light green",
    "brightbrown": "yellow",
    "brightblue": "light blue",
    "brightmagenta": "light magenta",
    "brightcyan": "light cyan",
    "brightwhite": "white",
}
_PYTE_BG_MAP = {
    "black": "black",
    "red": "dark red",
    "green": "dark green",
    "brown": "brown",
    "blue": "dark blue",
    "magenta": "dark magenta",
    "cyan": "dark cyan",
    "white": "light gray",
    "default": "default",
    "brightblack": "dark gray",
    "brightred": "light red",
    "brightgreen": "light green",
    "brightbrown": "yellow",
    "brightblue": "light blue",
    "brightmagenta": "light magenta",
    "brightcyan": "light cyan",
    "brightwhite": "white",
}

# urwid keypress → terminal escape bytes
_KEY_MAP = {
    "enter": "\r",
    "backspace": "\x7f",
    "tab": "\t",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "delete": "\x1b[3~",
    "insert": "\x1b[2~",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}


def _style_for_char(char: pyte.Char) -> str:
    parts: list[str] = []
    if char.bold:
        parts.append("bold")
    if char.italics:
        parts.append("italics")
    if char.underscore:
        parts.append("underline")
    if char.reverse:
        parts.append("standout")
    fg = _PYTE_FG_MAP.get(char.fg, char.fg)
    if fg and fg != "default":
        parts.append(fg)
    bg = _PYTE_BG_MAP.get(char.bg, char.bg)
    if bg and bg != "default":
        parts.append(bg)
    return " ".join(parts) if parts else ""


def _render_line(row: dict) -> list[tuple[str, str]]:
    spans: list[tuple[str, str]] = []
    current_style: str | None = None
    current_text: list[str] = []
    for col_idx in sorted(row):
        char = row[col_idx]
        style = _style_for_char(char)
        if style != current_style:
            if current_text:
                spans.append((current_style or "", "".join(current_text)))
            current_style = style
            current_text = [char.data]
        else:
            current_text.append(char.data)
    if current_text:
        spans.append((current_style or "", "".join(current_text)))
    if not spans:
        spans.append(("", " "))
    return spans


class RemoteTerminal(urwid.Widget):
    _sizing = frozenset([urwid.BOX])
    _selectable = True
    ignore_focus = False

    def __init__(self, screen: pyte.HistoryScreen, ws_queue: queue.Queue, ws_send_fn) -> None:
        super().__init__()
        self.screen = screen
        self.stream = pyte.Stream(screen)
        self.ws_queue = ws_queue
        self.ws_send_fn = ws_send_fn
        self._scroll_offset = 0

    def render(self, size: tuple[int, ...], focus: bool = False) -> urwid.Canvas:
        cols = size[0] if size else self.screen.columns
        max_rows = size[1] if len(size) > 1 else self.screen.lines

        history_lines = [_render_line(row) for row in self.screen.history.top]
        screen_lines = [_render_line(self.screen.buffer[row_idx]) for row_idx in range(self.screen.lines)]
        all_lines = history_lines + screen_lines
        if not all_lines:
            all_lines = [[("", " ")]]

        total_lines = len(all_lines)
        scroll_offset = min(self._scroll_offset, max(0, total_lines - 1))
        window_end = max(0, total_lines - scroll_offset)
        window_start = max(0, window_end - max_rows)
        lines = all_lines[window_start:window_end]
        while len(lines) < max_rows:
            lines.insert(0, [("", " ")])

        text_widgets = [urwid.Text(line_spans, wrap="clip") for line_spans in lines]
        canvases = [w.render((cols,)) for w in text_widgets]
        canvas = urwid.CompositeCanvas(urwid.CanvasCombine([(c, None, False) for c in canvases]))

        if focus and not self.screen.cursor.hidden:
            cursor_x = max(0, min(self.screen.cursor.x, cols - 1)) if cols > 0 else 0
            cursor_line_index = len(history_lines) + self.screen.cursor.y
            cursor_y = cursor_line_index - window_start
            if 0 <= cursor_y < max_rows:
                canvas.cursor = (cursor_x, cursor_y)

        return canvas

    def _scroll_page_up(self) -> None:
        self._scroll_offset = min(
            self._scroll_offset + self.screen.lines,
            len(self.screen.history.top),
        )
        self._invalidate()

    def _scroll_page_down(self) -> None:
        self._scroll_offset = max(self._scroll_offset - self.screen.lines, 0)
        self._invalidate()

    def _wheel_scroll_step(self) -> int:
        return 1

    def _scroll_wheel_up(self) -> None:
        self._scroll_offset = min(
            self._scroll_offset + self._wheel_scroll_step(),
            len(self.screen.history.top),
        )
        self._invalidate()

    def _scroll_wheel_down(self) -> None:
        self._scroll_offset = max(self._scroll_offset - self._wheel_scroll_step(), 0)
        self._invalidate()

    def keypress(self, size: tuple[int, ...], key: str) -> str | None:
        if key in {"ctrl q", "ctrl ]"}:
            raise urwid.ExitMainLoop()

        if key == "page up":
            self._scroll_page_up()
            return None
        if key == "page down":
            self._scroll_page_down()
            return None

        if self._scroll_offset > 0:
            self._scroll_offset = 0
            self._invalidate()

        if key in _KEY_MAP:
            self.ws_send_fn(_KEY_MAP[key])
            return None

        if key.startswith("ctrl ") and len(key) == 6:
            ch = key[5]
            code = ord(ch) - ord("a") + 1
            if 1 <= code <= 26:
                self.ws_send_fn(chr(code))
                return None

        if key.startswith("meta ") and len(key) == 6:
            self.ws_send_fn(f"\x1b{key[5]}")
            return None

        if len(key) == 1:
            self.ws_send_fn(key)
            return None

        return key

    def mouse_event(
        self,
        size: tuple[int, ...],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        if event == "mouse press":
            if button == 4:
                self._scroll_wheel_up()
                return True
            if button == 5:
                self._scroll_wheel_down()
                return True
        return False


class TUIApp:
    def __init__(self, ws: websocket.WebSocket, session: PersistentSession) -> None:
        self.ws = ws
        self.session = session
        self._running = True
        self._ws_queue: queue.Queue[str | None] = queue.Queue()
        self._original_sigwinch: object = None

        try:
            cols, rows = os.get_terminal_size()
        except OSError:
            cols, rows = 80, 24

        self.screen = pyte.HistoryScreen(cols, rows, history=10000)
        self.stream = pyte.Stream(self.screen)

        self._send_resize(rows, cols)

        self.terminal = RemoteTerminal(self.screen, self._ws_queue, self._send_stdin)
        self.status_bar = urwid.Text(self._status_text(), wrap="clip")
        self.pile = urwid.Pile([
            ("weight", 1, self.terminal),
            ("pack", self.status_bar),
        ])

        self._asyncio_loop = asyncio.new_event_loop()
        self._screen_widget = urwid.raw_display.Screen()
        self.loop = urwid.MainLoop(
            self.pile,
            palette=self._build_palette(),
            screen=self._screen_widget,
            handle_mouse=True,
            event_loop=urwid.AsyncioEventLoop(loop=self._asyncio_loop),
            unhandled_input=self._unhandled_input,
        )

    def _build_palette(self) -> list[tuple[str, str, str, ...]]:
        entries = [
            ("bold", "default,bold", ""),
            ("italics", "default,italics", ""),
            ("underline", "default,underline", ""),
            ("standout", "black", "white"),
        ]
        for name, color in [
            ("black", "black"), ("dark gray", "dark gray"),
            ("dark red", "dark red"), ("light red", "light red"),
            ("dark green", "dark green"), ("light green", "light green"),
            ("brown", "brown"), ("yellow", "yellow"),
            ("dark blue", "dark blue"), ("light blue", "light blue"),
            ("dark magenta", "dark magenta"), ("light magenta", "light magenta"),
            ("dark cyan", "dark cyan"), ("light cyan", "light cyan"),
            ("light gray", "light gray"), ("white", "white"),
        ]:
            entries.append((name, color, ""))
            entries.append((f"bold {name}", f"{color},bold", ""))
        for name, color in [
            ("black", "black"), ("dark gray", "dark gray"),
            ("dark red", "dark red"), ("light red", "light red"),
            ("dark green", "dark green"), ("light green", "light green"),
            ("brown", "brown"), ("yellow", "yellow"),
            ("dark blue", "dark blue"), ("light blue", "light blue"),
            ("dark magenta", "dark magenta"), ("light magenta", "light magenta"),
            ("dark cyan", "dark cyan"), ("light cyan", "light cyan"),
            ("light gray", "light gray"), ("white", "white"),
        ]:
            entries.append((f"default {name}", "default", color))
            entries.append((f"bold default {name}", "default,bold", color))
        return entries

    def _status_text(self) -> str:
        parts = [
            f" {self.session.target_name}",
            f" session={self.session.session_id[:8]}",
        ]
        if self.session.tmux_session_name:
            parts.append(f" tmux={self.session.tmux_session_name}")
        parts.append(f" {self.screen.columns}x{self.screen.lines}")
        scrollback = len(self.screen.history.top)
        if scrollback:
            parts.append(f" scrollback={scrollback}")
        parts.append(" Ctrl+]:detach PgUp/PgDn/wheel:scroll")
        text = " ".join(parts)
        if self.screen.columns > 0:
            return text[: self.screen.columns].ljust(self.screen.columns)
        return text

    def _send_stdin(self, text: str) -> None:
        try:
            self.ws.send(json.dumps(["stdin", text]))
        except Exception:
            self._running = False
            raise urwid.ExitMainLoop()

    def _send_resize(self, rows: int, cols: int) -> None:
        try:
            self.ws.send(json.dumps(["set_size", rows, cols, 0, 0]))
        except Exception:
            pass

    def _ws_reader(self) -> None:
        import websocket as ws_mod

        while self._running:
            try:
                self.ws.settimeout(0.5)
                raw = self.ws.recv()
            except ws_mod.WebSocketTimeoutException:
                continue
            except Exception:
                if self._running:
                    self._ws_queue.put(None)
                return
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(message, list) and message:
                if message[0] == "stdout":
                    self._ws_queue.put(str(message[1]))
                elif message[0] == "disconnect":
                    self._ws_queue.put(None)
                    return

    def _drain_ws_queue(self) -> bool:
        dirty = False
        while True:
            try:
                data = self._ws_queue.get_nowait()
            except queue.Empty:
                break
            if data is None:
                self._running = False
                return True
            self.stream.feed(data)
            dirty = True
        if dirty:
            self.terminal._scroll_offset = 0
            self.status_bar.set_text(self._status_text())
            self.terminal._invalidate()
        return False

    def _on_sigwinch(self, signum: int, frame: object) -> None:
        try:
            cols, rows = os.get_terminal_size()
        except OSError:
            return
        self.screen.resize(rows, cols)
        self._send_resize(rows, cols)
        self.status_bar.set_text(self._status_text())
        self.terminal._invalidate()

    def _unhandled_input(self, key: str | tuple[str, int, int, int]) -> bool | None:
        if key in {"ctrl q", "ctrl ]"}:
            raise urwid.ExitMainLoop()
        if key == "page up":
            self.terminal._scroll_page_up()
            return True
        if key == "page down":
            self.terminal._scroll_page_down()
            return True
        return None

    def close(self) -> None:
        if not self._asyncio_loop.is_closed():
            self._asyncio_loop.close()

    def run(self) -> None:
        reader = threading.Thread(target=self._ws_reader, daemon=True)
        reader.start()

        self._original_sigwinch = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, self._on_sigwinch)
        self._screen_widget.set_mouse_tracking()

        self.loop.set_alarm_in(0.03, self._alarm_drain)

        try:
            self.loop.run()
        finally:
            self._running = False
            signal.signal(signal.SIGWINCH, self._original_sigwinch)
            if self.session.use_tmux and self.session.tmux_session_name:
                try:
                    self.ws.send(json.dumps(["stdin", "\x02d"]))
                except Exception:
                    pass

    def _alarm_drain(self, loop: urwid.MainLoop, data: object) -> None:
        should_exit = self._drain_ws_queue()
        if should_exit:
            raise urwid.ExitMainLoop()
        if self._running:
            loop.set_alarm_in(0.03, self._alarm_drain)


def run_tui(ws: websocket.WebSocket, session: PersistentSession) -> None:
    app = TUIApp(ws, session)
    app.run()
