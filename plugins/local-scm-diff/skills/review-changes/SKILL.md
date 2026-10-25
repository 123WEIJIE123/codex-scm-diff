---
name: review-changes
description: 在 Codex 内打开 SVN 或 Git 项目的可视化差异，查看全部未提交文件或相对修改前快照的本轮代码变化。适用于用户要显示改了哪些代码、打开 SVN 差异、比较本轮修改；提交描述仍交给相应提交摘要技能。
---

使用本插件的 MCP 工具处理本地 SVN/Git 差异。不会替换 Codex 原生 Git 面板。

## 打开差异

调用 `open_review`，传入当前项目绝对路径 `workspace`。默认 `scm: auto` 选择最近的工作副本；同根同时有 SVN/Git 时优先 SVN。用户明确指定时传 `svn` 或 `git`。

将返回的完整 URL 交给 Codex 的 `open_in_codex`，使用 `target: {type: browser, url: ...}`。这是在 Codex 内打开的独立差异页，不是给原生审查面板增加 SVN 支持。链接包含本地访问凭证，不发给外部服务。

用户选择全部未提交改动时，不需要快照。可先用 `list_changes` 或 `get_diff` 分析再打开。

## 本轮修改

若 UserPromptSubmit Hook 提供了本轮 `snapshot_id`，使用该 ID；若尚未修改且要记录本轮变化，先调用 `capture_baseline`，保存返回 ID，完成修改后传给 `open_review`。完成代码修改后打开页面，方便用户检查。

若改动已完成但没有修改前快照，只能可靠展示全部未提交改动，不能事后捕获并冒充修改前基线。不得把工作副本已有改动都称为本轮改动。快照失效时解释原因，允许查看全部改动。

## 限制

- 只读项目；不提交、不暂存、不还原、不初始化仓库。
- 文本上限 8 MiB，差异最多显示 6000 行。二进制、目录、外部符号链接仅显示状态与原因。
- SVN 使用本机 wc-ng SHA1 pristine 读取 BASE。SVN 属性、关键字展开/特殊编码转换、移动来源不完整的差异请结合 SVN 客户端检查；本轮范围只比较文件内容，不比较属性。
- Git 展示 HEAD 到当前工作区（包含暂存后的最终内容），不分别拆分暂存与未暂存。
- Hook 需按 Codex 正常机制审核信任；不能改信任状态或绕过检查。

若 MCP 尚未出现在当前会话，提示重新打开聊天/重启 Codex。必要时可用本插件 `scripts/scm_diff.py open_review --workspace <绝对路径>` 临时启动同一页面。
