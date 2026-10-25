"""Local plugin CLI, MCP stdio entry point and non-blocking Codex hooks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from scm import Repository, ReviewError, file_diff, list_changes, snapshot

TOOLS = [
    ("open_review", "打开当前 SVN/Git 工作区的只读差异页；返回 URL 后使用 Codex open_in_codex 浏览器打开。", {}),
    ("list_changes", "列出全部未提交改动，或相对明确快照的本轮改动。", {}),
    ("get_diff", "读取当前改动列表中一个文件的文本差异。", {"path": {"type": "string"}}),
    ("capture_baseline", "修改前捕获本轮基线，写入插件私有缓存，不写项目。返回 snapshot_id。", {}),
]


def invoke(name, args):
    workspace = args.get("workspace")
    if not workspace:
        raise ReviewError("必须明确传入当前工作区路径。")
    repo = Repository(workspace, args.get("scm", "auto"))
    sid = args.get("snapshot_id")
    scope = args.get("scope", "workspace")
    if name == "capture_baseline":
        return snapshot(repo)
    if name == "open_review":
        if sid:
            from scm import load_snapshot
            load_snapshot(repo, sid)
        from server import launch
        return {**repo.info(), **launch(workspace, sid)}
    changes = list_changes(repo, scope, sid)
    if name == "list_changes":
        return changes
    if name == "get_diff":
        item = next((item for item in changes["files"] if item["path"] == args.get("path")), None)
        if not item:
            raise ReviewError("该文件不在改动列表中。")
        return file_diff(repo, item, scope, sid)
    raise ReviewError("未知工具。")


def definitions():
    common = {"workspace": {"type": "string", "description": "当前项目的绝对目录"},
              "scm": {"type": "string", "enum": ["auto", "svn", "git"], "default": "auto"},
              "scope": {"type": "string", "enum": ["workspace", "turn"], "default": "workspace"},
              "snapshot_id": {"type": "string", "description": "修改前捕获的快照 ID；不能用事后快照冒充本轮基线"}}
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "properties": {**common, **extra},
                             "required": ["workspace", *extra], "additionalProperties": False},
             "annotations": {"readOnlyHint": name != "capture_baseline", "destructiveHint": False,
                             "openWorldHint": False}}
            for name, description, extra in TOOLS]


def mcp():
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method, params = request.get("method"), request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "local-scm-diff", "version": "0.2.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": definitions()}
            elif method == "tools/call":
                try:
                    value = invoke(params["name"], params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True)}]}
                except Exception as exc:
                    result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                  "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                              "error": {"code": -32700, "message": str(exc)}}), flush=True)


def hook():
    try:
        event = json.load(sys.stdin)
        if event.get("hook_event_name") != "UserPromptSubmit":
            return {}
        if not event.get("cwd") or not event.get("session_id") or not event.get("turn_id"):
            return {}  # No reliable per-turn identity: never silently replace an old baseline.
        repo = Repository(event["cwd"])
        identity = "|".join((event["session_id"], event["turn_id"], str(repo.root)))
        sid = hashlib.sha256(identity.encode()).hexdigest()[:24]
        from scm import snapshot_path
        if not snapshot_path(sid).exists():
            snapshot(repo, sid)
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
            f"Local SCM Diff 已记录本轮修改前快照 {sid}，工作区 {repo.root}。本轮完成修改后，"
            "可调用 local-scm-diff 的 open_review，传入 workspace 和 snapshot_id，再用 "
            "Codex open_in_codex 的 browser 目标打开返回 URL；用户可在界面切换全部改动/本轮改动。"
            "不要把本来已有的改动说成本轮新增。Git 原生差异面板保留。"}}
    except Exception as exc:
        print("Local SCM Diff hook: " + str(exc), file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["mcp", "hook", "open_review", "list_changes", "get_diff", "capture_baseline"])
    parser.add_argument("--workspace")
    parser.add_argument("--scm", default="auto")
    parser.add_argument("--scope", default="workspace")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--path")
    args = vars(parser.parse_args())
    action = args.pop("action")
    if action == "mcp":
        mcp()
    elif action == "hook":
        print(json.dumps(hook(), ensure_ascii=True))
    else:
        try:
            print(json.dumps(invoke(action, args), ensure_ascii=True))
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=True))
            sys.exit(1)


if __name__ == "__main__":
    main()
