#!/usr/bin/env python3
"""
End-to-end upload/download directory consistency tests via CLI.

Run with a live notebook target:
    AI4QZ_TEST_TARGET=<target_name> PYTHONPATH=src python -m unittest tests.test_upload_download_cli -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = [sys.executable, "-m", "ai4qz.cli"]
ENV = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        CLI + list(args),
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=ENV,
        timeout=120,
    )
    return result


class UploadDownloadConsistencyTests(unittest.TestCase):
    """Test directory consistency for upload/download via CLI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.target = os.environ.get("AI4QZ_TEST_TARGET")
        if not cls.target:
            raise unittest.SkipTest(
                "Set AI4QZ_TEST_TARGET env var to a notebook target "
                "(e.g. 'h200_ncu').\n"
                "Example: AI4QZ_TEST_TARGET=h200_ncu PYTHONPATH=src python -m unittest "
                "tests.test_upload_download_cli -v"
            )

    # --- upload ---

    def test_upload_trailing_slash_puts_file_inside_directory(self) -> None:
        """upload <local> dir/  should place file as dir/<basename>, not as a file named 'dir'."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("upload consistency test content")
            local_file = f.name

        test_dir = Path(local_file).stem  # unique name from temp file
        remote_dir = f"{test_dir}/"

        try:
            # act
            result = _run("upload", self.target, local_file, remote_dir)
            self.assertEqual(result.returncode, 0, result.stderr)

            # assert — file must be inside the directory, not be the directory
            expected_remote = f"{test_dir}/{Path(local_file).name}"
            result = _run("run", self.target, "--cmd", f"cat '{expected_remote}'")
            self.assertIn("upload consistency test content", result.stdout)
        finally:
            _run("run", self.target, "--cmd", f"rm -rf '{test_dir}'")
            os.unlink(local_file)

    def test_upload_empty_remote_path_uses_local_filename(self) -> None:
        """upload <local> ''  should target the root and use the local filename."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("root upload test")
            local_file = f.name

        fname = Path(local_file).name

        try:
            result = _run("upload", self.target, local_file, "")
            self.assertEqual(result.returncode, 0, result.stderr)

            result = _run("run", self.target, "--cmd", f"cat '{fname}'")
            self.assertIn("root upload test", result.stdout)
        finally:
            _run("run", self.target, "--cmd", f"rm -f '{fname}'")
            os.unlink(local_file)

    # --- download ---

    def test_download_to_existing_directory_appends_remote_filename(self) -> None:
        """download <remote> <existing_local_dir> should write file into the directory."""
        # Use relative path — contents API sees notebook root, not filesystem root.
        remote_path = "ai4qz_dl_test.txt"
        with tempfile.TemporaryDirectory() as tmpdir:
            # setup remote file via terminal in notebook root
            result = _run("run", self.target, "--cmd", f"echo 'download consistency test' > '{remote_path}'")
            self.assertEqual(result.returncode, 0, result.stderr)

            try:
                # act — tmpdir already exists
                result = _run("download", self.target, remote_path, tmpdir)
                self.assertEqual(result.returncode, 0, result.stderr)

                # assert
                expected = Path(tmpdir) / "ai4qz_dl_test.txt"
                self.assertTrue(expected.exists(), f"Expected file not found: {expected}")
                self.assertIn("download consistency test", expected.read_text())
            finally:
                _run("run", self.target, "--cmd", f"rm -f '{remote_path}'")

    def test_download_of_remote_directory_fails(self) -> None:
        """download from a remote directory path must return a non-zero exit code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_dir = f"ai4qz_test_dl_dir_{os.urandom(4).hex()}"
            # create a known remote directory
            result = _run("run", self.target, "--cmd", f"mkdir -p '{remote_dir}'")
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                result = _run("download", self.target, remote_dir, tmpdir)
                self.assertNotEqual(result.returncode, 0)
            finally:
                _run("run", self.target, "--cmd", f"rm -rf '{remote_dir}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
