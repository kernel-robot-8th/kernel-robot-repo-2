"""Contracts for HarborLogi CI layer caching and self-review gate rules."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION_PIN = re.compile(r"^\s*(?:-\s+)?uses:\s*[\w.-]+/[\w.-]+@[0-9a-f]{40}\s+#\s+v\d+\.\d+\.\d+")


class TestCiAndReviewGate(unittest.TestCase):
    def test_dockerfile_caches_web_deps_before_source_copy(self):
        text = (REPO_ROOT / "harborlogi" / "deploy" / "compose" / "Dockerfile").read_text(encoding="utf-8")
        copy_toml = text.index("COPY pyproject.toml /app/pyproject.toml")
        copy_src = text.index("COPY . /app")
        self.assertLess(copy_toml, copy_src)
        self.assertIn("pip install --no-cache-dir --no-deps .", text[copy_src:])

    def test_workflows_keep_pinned_actions_and_cache_settings(self):
        workflows = REPO_ROOT / ".github" / "workflows"
        for wf in sorted(workflows.glob("*.yml")):
            for line in wf.read_text(encoding="utf-8").splitlines():
                if re.match(r"^\s*(?:-\s+)?uses:\s*", line):
                    self.assertRegex(line, ACTION_PIN, msg=f"unpinned action in {wf.name}: {line}")
        test_yml = (workflows / "test.yml").read_text(encoding="utf-8")
        self.assertEqual(test_yml.count('cache: "pip"'), 2)
        self.assertIn("cache-dependency-path: harborlogi/pyproject.toml", test_yml)
        pub_yml = (workflows / "publish-image.yml").read_text(encoding="utf-8")
        self.assertIn('--cache-from "${REPOSITORY}:main"', pub_yml)
        gate_yml = (workflows / "review-gate.yml").read_text(encoding="utf-8")
        self.assertIn("issue_comment:", gate_yml)
        self.assertIn("Overlap-Review:", gate_yml)

    def test_review_gate_script_defaults_open_pr_only_and_allows_self_overlap(self):
        gate_sh = (REPO_ROOT / ".github" / "review-gate.sh").read_text(encoding="utf-8")
        self.assertIn('WINDOW_DAYS:-0', gate_sh)
        self.assertIn("Overlap-Review:[[:space:]]*self", gate_sh)
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
        if not bash or subprocess.run([bash, "-c", "exit 0"], capture_output=True).returncode != 0:
            return
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            fake_bin = tdir / "bin"
            fake_bin.mkdir()
            state_file = tdir / "body.txt"
            state_file.write_text("", encoding="utf-8")
            (fake_bin / "gh").write_text(
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *\"pulls/99/files\"*) printf 'harborlogi/a.py\\n' ;;\n"
                "  *\"pulls?state=open\"*) printf 'alice 101\\n' ;;\n"
                "  *\"pulls/101/files\"*) printf 'alice\\tharborlogi/a.py\\n' ;;\n"
                "  *\"pulls/99 \"*|*\"pulls/99\"*) cat \"$BODY_FILE\" ;;\n"
                "  *\"reviews\"*) printf '' ;;\n"
                "  *\"comments\"*) printf '' ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
                newline="\n",
            )
            os.chmod(fake_bin / "gh", 0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "GH_TOKEN": "t", "REPO": "org/repo", "PR": "99", "AUTHOR": "bob",
                "BODY_FILE": str(state_file),
            }
            gate_script = (REPO_ROOT / ".github" / "review-gate.sh").as_posix()
            fail = subprocess.run([bash, gate_script], env=env, capture_output=True, text=True)
            self.assertIn("must approve", fail.stdout + fail.stderr)
            self.assertNotEqual(fail.returncode, 0)
            state_file.write_text("Overlap-Review: self (coordinated solo change)\n", encoding="utf-8")
            ok = subprocess.run([bash, gate_script], env=env, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)


if __name__ == "__main__":
    unittest.main()
