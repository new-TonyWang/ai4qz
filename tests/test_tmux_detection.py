from __future__ import annotations

import unittest
from pathlib import Path
from ai4qz.jupyter import JupyterNotebookClient
from ai4qz.models import CommandResult, Defaults, PersistentSession, ResolvedTarget


class EnsureTmuxSessionTests(unittest.TestCase):
    def _make_client(self) -> JupyterNotebookClient:
        target = ResolvedTarget(
            name='qz_dev',
            base_url='https://example.invalid',
            cookies_file=Path(__file__).resolve().parent.parent / 'cookies' / 'qz_cookies.txt',
            notebook_id='nb',
            resolved_from='config',
        )
        return JupyterNotebookClient(target, Defaults())

    def _make_session(self) -> PersistentSession:
        return PersistentSession(
            session_id='sess12345678',
            target_name='qz_dev',
            terminal_name='42',
            base_url='https://example.invalid',
            cookies_file=Path(__file__).resolve().parent.parent / 'cookies' / 'qz_cookies.txt',
            notebook_id='nb',
            resolved_from='config',
            created_at='2026-05-10T00:00:00+0800',
            last_used_at='2026-05-10T00:00:00+0800',
            use_tmux=True,
            tmux_session_name='ai4qz',
        )

    def test_nested_tmux_disables_tmux_mode(self) -> None:
        client = self._make_client()
        session = self._make_session()

        def fake_run(terminal_name: str, command: str) -> CommandResult:
            self.assertIn('tmux_nested', command)
            self.assertEqual(terminal_name, '42')
            return CommandResult(
                name='qz_dev',
                ok=True,
                exit_code=0,
                output='tmux_nested\n',
                terminal_name='42',
                seconds=0.01,
            )

        client.run_command_in_terminal = fake_run  # type: ignore[method-assign]
        updated = client.ensure_tmux_session(session)
        self.assertFalse(updated.use_tmux)
        self.assertIsNone(updated.tmux_session_name)

    def test_missing_tmux_disables_tmux_mode(self) -> None:
        client = self._make_client()
        session = self._make_session()
        client.run_command_in_terminal = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
            name='qz_dev',
            ok=True,
            exit_code=0,
            output='tmux_missing\n',
            terminal_name='42',
            seconds=0.01,
        )
        updated = client.ensure_tmux_session(session)
        self.assertFalse(updated.use_tmux)
        self.assertIsNone(updated.tmux_session_name)

    def test_nested_tmux_warning_output_disables_tmux_mode(self) -> None:
        client = self._make_client()
        session = self._make_session()
        client.run_command_in_terminal = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
            name='qz_dev',
            ok=False,
            exit_code=None,
            output='sessions should be nested with care, unset $TMUX to force\n',
            terminal_name='42',
            seconds=0.01,
            error='command timed out before sentinel arrived',
        )
        updated = client.ensure_tmux_session(session)
        self.assertFalse(updated.use_tmux)
        self.assertIsNone(updated.tmux_session_name)

    def test_tmux_ready_keeps_tmux_mode(self) -> None:
        client = self._make_client()
        session = self._make_session()
        client.run_command_in_terminal = lambda *_args, **_kwargs: CommandResult(  # type: ignore[method-assign]
            name='qz_dev',
            ok=True,
            exit_code=0,
            output='tmux_ready\n',
            terminal_name='42',
            seconds=0.01,
        )
        updated = client.ensure_tmux_session(session)
        self.assertTrue(updated.use_tmux)
        self.assertEqual(updated.tmux_session_name, 'ai4qz')


if __name__ == '__main__':
    unittest.main(verbosity=2)
