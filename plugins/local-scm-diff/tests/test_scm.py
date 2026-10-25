import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scm import Repository, ReviewError, decode, file_diff, list_changes, snapshot
from server import ReviewServer


def run(exe, *args, cwd):
    result = subprocess.run([exe, *args], cwd=cwd, capture_output=True, check=True)
    return result.stdout


class Adapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.old_data = os.environ.get("SCM_DIFF_DATA")
        os.environ["SCM_DIFF_DATA"] = str(self.folder / "state")
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.old_data is None:
            os.environ.pop("SCM_DIFF_DATA", None)
        else:
            os.environ["SCM_DIFF_DATA"] = self.old_data

    def git(self):
        root = self.folder / "git"
        root.mkdir()
        run("git", "init", "-q", cwd=root)
        run("git", "config", "user.email", "local@example.invalid", cwd=root)
        run("git", "config", "user.name", "Local test", cwd=root)
        run("git", "config", "core.autocrlf", "false", cwd=root)
        (root / "file.txt").write_text("one\ntwo\n", encoding="utf-8")
        (root / "keep.txt").write_text("keep\n", encoding="utf-8")
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-qm", "baseline", cwd=root)
        return root

    def svn(self):
        store = self.folder / "repository"
        run("svnadmin", "create", str(store), cwd=self.folder)
        root = self.folder / "svn"
        root.mkdir()
        run("svn", "checkout", store.as_uri(), ".", "--non-interactive", cwd=root)
        (root / "file.txt").write_text("one\ntwo\n", encoding="utf-8")
        (root / "中文说明.md").write_text("原来的村庄\n", encoding="utf-8")
        run("svn", "add", ".", "--force", cwd=root)
        run("svn", "commit", "-m", "baseline", "--non-interactive", cwd=root)
        return root

    def assert_lines(self, repo, name, added, removed):
        item = next(x for x in repo.status() if x["path"] == name)
        diff = file_diff(repo, item)
        self.assertEqual((diff["added"], diff["removed"]), (added, removed), diff)
        return diff

    def test_git_staged_unstaged_rename_delete_untracked(self):
        root = self.git()
        (root / "file.txt").write_text("one\nstaged\n", encoding="utf-8")
        run("git", "add", ".", cwd=root)
        (root / "file.txt").write_text("one\nfinal\n", encoding="utf-8")
        run("git", "mv", "keep.txt", "renamed.txt", cwd=root)
        (root / "new.txt").write_text("new\n", encoding="utf-8")
        repo = Repository(root)
        self.assertEqual(repo.scm, "git")
        self.assert_lines(repo, "file.txt", 1, 1)
        self.assert_lines(repo, "renamed.txt", 0, 0)
        self.assert_lines(repo, "new.txt", 1, 0)
        (root / "file.txt").unlink()
        self.assert_lines(repo, "file.txt", 0, 2)

    def test_svn_unicode_hybrid_nested_and_pristine(self):
        root = self.svn()
        (root / "file.txt").write_text("one\nthree\n", encoding="utf-8")
        (root / "中文说明.md").write_text("新的村庄\n", encoding="utf-8")
        run("git", "init", "-q", cwd=root)
        repo = Repository(root)
        self.assertEqual(repo.scm, "svn")
        self.assertEqual(set(repo.available), {"svn", "git"})
        diff = self.assert_lines(repo, "中文说明.md", 1, 1)
        self.assertIn("原来的村庄", [row["text"] for row in diff["rows"]])
        nested = root / "nested"
        nested.mkdir()
        run("git", "init", "-q", cwd=nested)
        self.assertEqual(Repository(nested).scm, "git")

    def test_turn_baseline_excludes_preexisting_and_detects_revert(self):
        root = self.git()
        (root / "file.txt").write_text("user existing\n", encoding="utf-8")
        repo = Repository(root)
        sid = snapshot(repo)["id"]
        self.assertEqual(list_changes(repo, "turn", sid)["files"], [])
        (root / "keep.txt").write_text("assistant edit\n", encoding="utf-8")
        self.assertEqual([x["path"] for x in list_changes(repo, "turn", sid)["files"]], ["keep.txt"])
        run("git", "restore", "file.txt", cwd=root)
        files = list_changes(repo, "turn", sid)["files"]
        self.assertEqual([x["path"] for x in files], ["file.txt", "keep.txt"])
        diff = file_diff(repo, files[0], "turn", sid)
        self.assertIn("user existing", [x["text"] for x in diff["rows"]])
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-qm", "next", cwd=root)
        with self.assertRaises(ReviewError):
            list_changes(repo, "turn", sid)

    def test_svn_turn_and_base_change(self):
        root = self.svn()
        repo = Repository(root)
        (root / "file.txt").write_text("old local\n", encoding="utf-8")
        sid = snapshot(repo)["id"]
        (root / "file.txt").write_text("new local\n", encoding="utf-8")
        item = list_changes(repo, "turn", sid)["files"][0]
        diff = file_diff(repo, item, "turn", sid)
        self.assertEqual((diff["added"], diff["removed"]), (1, 1))
        self.assertIn("old local", [x["text"] for x in diff["rows"]])
        run("svn", "commit", "-m", "next", cwd=root)
        with self.assertRaises(ReviewError):
            list_changes(repo, "turn", sid)

    def test_binary_large_and_path_validation(self):
        root = self.git()
        repo = Repository(root)
        (root / "binary").write_bytes(b"\0\1\2")
        (root / "large").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
        for item in repo.status():
            self.assertTrue(file_diff(repo, item)["unavailable"])
        for name in ["../outside", str(root / "file.txt"), "../git/file.txt", ""]:
            with self.assertRaises(ReviewError):
                repo.local_path(name)
        self.assertEqual(decode("中文".encode("utf-16"))[0], "中文")

    def test_unborn_git_and_deleted_new_file_since_snapshot(self):
        root = self.folder / "empty"
        root.mkdir()
        run("git", "init", "-q", cwd=root)
        (root / "new.txt").write_text("before\n", encoding="utf-8")
        repo = Repository(root)
        self.assert_lines(repo, "new.txt", 1, 0)
        sid = snapshot(repo)["id"]
        (root / "new.txt").unlink()
        items = list_changes(repo, "turn", sid)["files"]
        diff = file_diff(repo, items[0], "turn", sid)
        self.assertEqual((diff["added"], diff["removed"]), (0, 1))

    def test_http_auth_paths_and_html_inert(self):
        root = self.git()
        (root / "file.txt").write_text("<img src=x onerror=alert(1)>\n", encoding="utf-8")
        server = ReviewServer(str(root), "test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        def fetch(path, headers=None):
            req = urllib.request.Request(base + path, headers=headers or {})
            return urllib.request.urlopen(req, timeout=10)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            fetch("/api/status")
        self.assertEqual(denied.exception.code, 401)
        auth = {"Authorization": "Bearer test-token"}
        with fetch("/api/status", auth) as response:
            self.assertEqual(len(json.load(response)["files"]), 1)
        with fetch("/api/diff?path=file.txt", auth) as response:
            self.assertIn("<img", json.load(response)["rows"][-1]["text"])
        for path, headers, expected in [
            ("/api/diff?path=../outside", auth, 400),
            ("/api/status", {**auth, "Origin": "http://evil.invalid"}, 403),
            ("/api/status", {**auth, "Host": "evil.invalid"}, 403)]:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                fetch(path, headers)
            self.assertEqual(denied.exception.code, expected)
        with fetch("/") as response:
            self.assertIn(b"Content-Security-Policy", str(response.headers).encode())

    def test_mcp_protocol_and_hook(self):
        root = self.git()
        script = str(Path(__file__).resolve().parents[1] / "scripts" / "scm_diff.py")
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_changes", "arguments": {"workspace": str(root)}}}]
        result = subprocess.run([sys.executable, script, "mcp"], input="\n".join(map(json.dumps, requests))+"\n", text=True, capture_output=True, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([x["id"] for x in responses], [1, 2, 3])
        self.assertEqual(len(responses[1]["result"]["tools"]), 4)
        self.assertFalse(responses[2]["result"].get("isError"))
        event = {"cwd": str(root), "session_id": "test", "turn_id": "1", "hook_event_name": "UserPromptSubmit"}
        result = subprocess.run([sys.executable, script, "hook"], input=json.dumps(event), text=True, capture_output=True, check=True)
        self.assertIn("snapshot_id", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
        event["hook_event_name"] = "Stop"
        result = subprocess.run([sys.executable, script, "hook"], input=json.dumps(event), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {})


if __name__ == "__main__":
    unittest.main()
