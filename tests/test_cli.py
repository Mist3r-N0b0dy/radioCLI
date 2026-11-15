"""Tests de la ligne de commande (sans interface ni réseau)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "radiocli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class CliTests(unittest.TestCase):
    def test_list_stations(self) -> None:
        result = run_cli("--list-stations")
        self.assertEqual(result.returncode, 0)
        self.assertIn("fip", result.stdout)
        self.assertIn("radiofrance", result.stdout)

    def test_version(self) -> None:
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("radiocli", result.stdout)

    def test_unknown_station(self) -> None:
        result = run_cli("--station", "polka", "--stats", "--duration", "0.1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown station", result.stderr)

    def test_stats_requires_ffmpeg_path(self) -> None:
        # --stats sur une URL invalide doit rendre la main sans planter.
        result = run_cli(
            "--url",
            "http://127.0.0.1:9/invalid.mp3",
            "--stats",
            "--no-audio",
            "--duration",
            "1",
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
