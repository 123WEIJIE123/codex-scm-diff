"""Read-only local SVN/Git adapters and explicitly captured turn baselines."""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

MAX_FILE = 8 * 1024 * 1024
MAX_SNAPSHOT = 48 * 1024 * 1024
MAX_ROWS = 6000


class ReviewError(Exception):
    pass


def command(name, args, cwd, required=True):
    executable = shutil.which(name)
    if not executable:
        if not required:
            return None
        raise ReviewError(f"未找到 {name}，请安装并加入 PATH。")
    result = subprocess.run(
        [executable, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        if not required:
            return None
        raise ReviewError(result.stderr.decode("utf-8", "replace").strip()[:2000])
    return result.stdout


def state_root():
    root = Path(os.environ.get("SCM_DIFF_DATA") or os.environ.get("PLUGIN_DATA")
                or Path.home() / ".codex" / "scm-diff-data")
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def decode(data):
    if data is None:
        return "", None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), None
        except UnicodeError:
            return "", "无法解码 UTF-16 文件。"
    if b"\0" in data:
        return "", "二进制文件，仅显示状态。"
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), None
        except UnicodeError:
            pass
    return "", "无法识别文本编码，仅显示状态。"


class Repository:
    def __init__(self, workspace, scm="auto"):
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ReviewError("工作区目录不存在。")
        if scm not in {"auto", "svn", "git"}:
            raise ReviewError("未知版本控制类型。")
        candidates = {}
        svn = command("svn", ["info", "--xml", "--non-interactive"], path, False)
        if svn:
            try:
                info = ET.fromstring(svn)
                candidates["svn"] = Path(info.findtext(".//wcroot-abspath")).resolve()
            except (ET.ParseError, TypeError):
                pass
        git = command("git", ["rev-parse", "--show-toplevel"], path, False)
        if git:
            candidates["git"] = Path(os.fsdecode(git).strip()).resolve()
        if scm == "auto":
            # Prefer the nearest owning checkout; SVN wins a same-root hybrid checkout.
            scm = max(candidates, key=lambda k: (len(candidates[k].parts), k == "svn"), default="")
        if scm not in candidates:
            raise ReviewError(f"目录不属于可用的 {scm or 'SVN/Git'} 工作副本。")
        self.root, self.scm, self.available = candidates[scm], scm, list(candidates)

    def local_path(self, relative):
        if not isinstance(relative, str) or not relative or "\0" in relative:
            raise ReviewError("无效文件路径。")
        candidate = self.root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ReviewError("必须使用仓库内的相对路径。")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ReviewError("禁止访问工作区之外的文件或符号链接目标。")
        return candidate

    def relative(self, name):
        path = Path(name)
        if path.is_absolute():
            try:
                return path.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise ReviewError("状态包含工作区以外的路径。") from exc
        return path.as_posix()

    def status(self):
        if self.scm == "svn":
            raw = command("svn", ["status", "--xml", "--ignore-externals", "--non-interactive"], self.root)
            items = []
            for entry in ET.fromstring(raw).iter("entry"):
                wc = entry.find("wc-status")
                status = wc.get("item", "")
                props = wc.get("props") in {"modified", "conflicted"}
                if status in {"normal", "none", "ignored", "external"} and not props:
                    continue
                name = self.relative(entry.get("path", ""))
                if name == ".":
                    continue
                items.append({"path": name, "status": status if status != "normal" else "properties",
                              "properties": props, "copied": wc.get("copied") == "true"})
        else:
            raw = command("git", ["-c", "core.quotepath=false", "status", "--porcelain=v1", "-z",
                                  "--untracked-files=normal"], self.root)
            fields = iter(raw.split(b"\0"))
            items = []
            for field in fields:
                if not field:
                    continue
                xy, name = field[:2].decode("ascii"), field[3:].decode("utf-8", "surrogateescape")
                old = next(fields).decode("utf-8", "surrogateescape") if "R" in xy or "C" in xy else None
                kind = ("conflicted" if xy in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"} else
                        "unversioned" if xy == "??" else "renamed" if "R" in xy else
                        "added" if "A" in xy else "deleted" if "D" in xy else "modified")
                items.append({"path": name, "status": kind, "gitStatus": xy, "oldPath": old})
        result = []
        for item in items:
            try:
                path = self.local_path(item["path"])
                item["directory"] = path.is_dir()
                item["size"] = path.stat().st_size if path.is_file() else 0
                result.append(item)
            except (OSError, ReviewError):
                # Never follow symlinks outside this checkout.
                item.update(directory=False, size=0, unavailable=True)
                result.append(item)
        return sorted(result, key=lambda item: item["path"].casefold())

    def base(self, item):
        name = item.get("oldPath") or item["path"]
        self.local_path(name)
        if self.scm == "git":
            size = command("git", ["cat-file", "-s", "HEAD:" + name], self.root, False)
            if size and int(size) > MAX_FILE:
                raise ReviewError("HEAD 文件超过 8 MiB，未读取内容。")
            return command("git", ["show", "HEAD:" + name], self.root, False)
        # A local BASE read must not contact an SVN server. Reading the pristine store
        # also avoids Windows svn.exe's non-ASCII command-line path conversion issues.
        db = self.root / ".svn" / "wc.db"
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                row = connection.execute("SELECT checksum,kind FROM NODES WHERE local_relpath=? AND op_depth=0",
                                         (name,)).fetchone()
            if not row or row[1] != "file" or not row[0]:
                return None
            checksum = row[0]
            if not re.fullmatch(r"\$sha1\$[0-9a-f]{40}", checksum):
                raise ReviewError("当前 SVN pristine 格式不受支持。")
            sha = checksum[6:]
            pristine = self.root / ".svn" / "pristine" / sha[:2] / (sha + ".svn-base")
            if pristine.stat().st_size > MAX_FILE:
                raise ReviewError("BASE 文件超过 8 MiB，未读取内容。")
            return pristine.read_bytes()
        except (sqlite3.Error, OSError) as exc:
            raise ReviewError("无法读取本地 SVN BASE：" + str(exc)) from exc

    def current(self, item):
        path = self.local_path(item["path"])
        if path.is_dir():
            raise ReviewError("目录项不递归展开；SVN 属性变更请同时使用 SVN 客户端检查。")
        if not path.exists():
            return None
        if path.stat().st_size > MAX_FILE:
            raise ReviewError("文件超过 8 MiB，未读取内容。")
        return path.read_bytes()

    def revision_key(self):
        if self.scm == "git":
            return (command("git", ["rev-parse", "--verify", "HEAD"], self.root, False) or b"unborn").decode().strip()
        digest = hashlib.sha256()
        with sqlite3.connect((self.root / ".svn" / "wc.db").as_uri() + "?mode=ro", uri=True) as connection:
            for row in connection.execute("SELECT local_relpath,revision,checksum,presence FROM NODES WHERE op_depth=0 ORDER BY local_relpath"):
                digest.update(repr(row).encode("utf-8"))
        return digest.hexdigest()

    def info(self):
        return {"root": str(self.root), "scm": self.scm, "available": self.available,
                "readOnly": True, "baseline": "SVN BASE" if self.scm == "svn" else "Git HEAD → 工作区（含暂存修改）"}


def snapshot(repo, snapshot_id=None):
    snapshot_id = snapshot_id or uuid.uuid4().hex[:24]
    snapshot_path(snapshot_id)  # validate externally supplied identifiers
    entries, used = {}, 0
    for item in repo.status():
        record = {"item": item}
        try:
            content = repo.current(item)
            used += len(content or b"")
            if used > MAX_SNAPSHOT:
                raise ReviewError("快照总大小超过 48 MiB，此项未捕获。")
            record["content"] = None if content is None else base64.b64encode(content).decode("ascii")
        except (ReviewError, OSError) as exc:
            record["error"] = str(exc)
        entries[item["path"]] = record
    data = {**repo.info(), "id": snapshot_id, "created": time.time(), "revision": repo.revision_key(), "entries": entries}
    save_json(snapshot_path(snapshot_id), data)
    return {key: data[key] for key in ("id", "created", "root", "scm")}


def snapshot_path(snapshot_id):
    if not re.fullmatch(r"[a-f0-9]{24}", snapshot_id):
        raise ReviewError("无效快照 ID。")
    return state_root() / "snapshots" / (snapshot_id + ".json")


def load_snapshot(repo, snapshot_id):
    if not snapshot_id:
        raise ReviewError("本轮视图需要修改前快照；请先开始记录，或查看全部未提交改动。")
    path = snapshot_path(snapshot_id)
    if not path.exists():
        raise ReviewError("修改前快照不存在。")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["root"] != str(repo.root) or data["scm"] != repo.scm:
        raise ReviewError("此快照属于另一个仓库或版本控制类型。")
    if data["revision"] != repo.revision_key():
        raise ReviewError("仓库基线已更新或提交，本轮快照已失效；请重新捕获。")
    return data


def before_and_after(repo, item, scope="workspace", snapshot_id=None, captured=None):
    after = repo.current(item)
    if scope == "workspace":
        return repo.base(item), after
    if scope != "turn":
        raise ReviewError("未知差异范围。")
    snap = captured or load_snapshot(repo, snapshot_id)
    previous = snap["entries"].get(item["path"])
    if previous:
        if "error" in previous:
            raise ReviewError(previous["error"])
        before = None if previous["content"] is None else base64.b64decode(previous["content"])
    else:
        before = repo.base(item)
    return before, after


def file_diff(repo, item, scope="workspace", snapshot_id=None):
    result = {"path": item["path"], "status": item["status"], "rows": [], "added": 0, "removed": 0}
    try:
        before, after = before_and_after(repo, item, scope, snapshot_id)
        old, old_error = decode(before)
        new, new_error = decode(after)
        if old_error or new_error:
            raise ReviewError(old_error or new_error)
        result["changed"] = before != after
        old_line, new_line = 0, 0
        total = 0
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="a/" + item["path"], tofile="b/" + item["path"], n=3):
            if line.startswith(("--- ", "+++ ")) and total < 2:
                total += 1
                continue
            if line.startswith("@@"):
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
                old_line, new_line = map(int, match.groups())
                row = {"kind": "hunk", "text": line}
            elif line.startswith("+"):
                result["added"] += 1
                row = {"kind": "add", "new": new_line, "text": line[1:]}
                new_line += 1
            elif line.startswith("-"):
                result["removed"] += 1
                row = {"kind": "del", "old": old_line, "text": line[1:]}
                old_line += 1
            else:
                row = {"kind": "context", "old": old_line, "new": new_line, "text": line[1:]}
                old_line += 1
                new_line += 1
            if len(result["rows"]) < MAX_ROWS:
                result["rows"].append(row)
            total += 1
        result["truncated"] = total - 2 > MAX_ROWS
        if before != after and not result["rows"]:
            result["note"] = "仅换行符、BOM 或文件存在状态发生变化。"
        if item.get("properties"):
            result["note"] = "此项还有 SVN 属性变化；本页展示内容差异，属性请在 SVN 客户端检查。"
    except (ReviewError, OSError) as exc:
        result.update(note=str(exc), unavailable=True)
    return result


def list_changes(repo, scope="workspace", snapshot_id=None):
    items = repo.status()
    if scope == "turn":
        snap = load_snapshot(repo, snapshot_id)
        by_path = {item["path"]: item for item in items}
        for name, previous in snap["entries"].items():
            by_path.setdefault(name, {**previous["item"], "status": "changed-during-turn"})
        items = []
        for item in by_path.values():
            try:
                old, new = before_and_after(repo, item, scope, snapshot_id, snap)
                if old != new:
                    items.append(item)
            except (ReviewError, OSError):
                items.append(item)
    elif scope != "workspace":
        raise ReviewError("未知差异范围。")
    return {**repo.info(), "scope": scope, "snapshotId": snapshot_id,
            "files": sorted(items, key=lambda item: item["path"].casefold()), "updated": time.time()}
