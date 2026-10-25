# Local SCM Diff

在 Codex 内查看 **SVN / Git 的本地文件差异**。适合 SVN 工程、Unity 项目，以及同时存在 SVN 和 Git 的工作区。

这是独立开发的社区插件，通过本 GitHub 插件市场分发，不属于 OpenAI 官方收录插件。

## 功能

- 独立差异页：文件列表、红绿行差异、行号、新增/删除数量。
- 自动识别最近的 SVN/Git 仓库；同根同时存在两者时优先 SVN，也可手动切换。
- 支持筛选文件、隐藏 `.meta`、刷新。
- 支持全部未提交改动，以及相对修改前快照的本轮内容变化。
- 保留 Codex 原生 Git 面板。只读项目，不提交、不暂存、不还原。
- 代码和快照留在本机；无远端后端、无账号、无插件订阅。

## 环境要求

- 支持插件、stdio MCP 和内置浏览器面板的 Codex 桌面客户端。CLI 可安装插件，但可视化页面需要浏览器。
- **Python 3.10+**，启动 Codex 前确认 `python --version` 可用，且不是 Microsoft Store 安装占位程序。
- 查看 SVN 项目需安装 SVN 命令行工具，确认 `svn --version --quiet` 可用。Windows 使用 TortoiseSVN 时需勾选 Command line client tools 并加入 PATH。
- 查看 Git 项目需安装 Git，确认 `git --version` 可用。

已在 Windows 上验证 SVN/Git 和 Codex 页面。其他桌面系统尚未实机验证；运行命令使用 `python`，仅提供 `python3` 的环境需先配置 `python` 为 Python 3。仅依赖 Python 标准库。

## 安装

在终端执行：

```text
codex plugin marketplace add https://github.com/123WEIJIE123/codex-scm-diff.git
codex plugin add local-scm-diff@weijie-dev-tools
```

然后新开聊天；若没有发现工具，重启 Codex。使用界面管理市场时，添加本仓库 GitHub 地址，再在 **WEIJIE Developer Tools** 中选择 **Local SCM Diff**。

该插件需要在本地聊天运行，不适用于无法访问本机项目的云端聊天。原生审查面板仍由 Codex 管理；SVN 差异显示在另外打开的浏览器面板内。

## 使用

在项目聊天中输入：

> 打开当前项目差异

或显式调用 `review-changes` 技能。助手会在 Codex 内打开差异页面，左侧选择文件，右侧查看行差异。

**全部未提交改动**：比较 SVN BASE / Git HEAD 与当前工作区。Git 包括已暂存后的最终内容，不分别拆分暂存区和未暂存。

**本轮修改**：必须在修改前捕获快照。可让助手“先记录修改前快照，完成后显示本轮差异”。没有提前快照时，只展示全部改动，不会把已有改动归因到本轮。

### 可选：自动快照 Hook

插件带有 UserPromptSubmit Hook。按 Codex 的正常 Hook 管理流程审核并信任后，它会尝试在每轮开始前记录基线，并提示助手完成修改后打开差异页。插件不会自行修改 Hook 信任状态。

自动快照要求宿主在事件中提供 `cwd`、`session_id`、`turn_id`；缺失时不记录。Hook 未启用或宿主不兼容时，可用 `capture_baseline` 工具手动记录，其余差异功能不受影响。

### 不通过 MCP 运行

克隆仓库后，在仓库根目录运行下面命令，把示例目录替换成自己的项目路径：

```text
python plugins/local-scm-diff/scripts/scm_diff.py open_review --workspace /path/to/project
```

返回 JSON 中的 `url` 为本机差异页地址，可在本地浏览器打开。页面空闲 30 分钟后服务退出；再次调用会创建新服务。访问链接带本地凭证，请勿转发。

## 限制与隐私

- 文本文件上限 8 MiB，单个快照内容总量 48 MiB，差异最多展示 6000 行。
- 二进制、大文件、目录和指向仓库外的符号链接显示状态或说明；未跟踪目录不自动递归。
- SVN 需要 wc-ng 本地数据库及 SHA1 pristine；不支持的格式明确报错。不完整展示属性、移动来源、关键字展开和特殊换行转换。
- 本轮范围比较文件内容，不比较 SVN 属性；提交或更新 BASE/HEAD 后旧快照失效。
- 默认快照位于 `~/.codex/scm-diff-data`，或宿主指定的 `PLUGIN_DATA`。不自动清理历史快照，请按需删除。详见 [隐私说明](PRIVACY.md)。
- 插件自身不上传项目数据；Codex 处理工具结果和聊天数据的规则由客户端自身决定。

## 更新与卸载

通过 Codex 的插件市场管理更新仓库并升级插件，或重新添加/刷新市场后安装新版本。卸载命令见 `codex plugin remove --help`；也可在插件界面卸载。

如果以前安装过本机开发版 `local-scm-diff@local-personal`，请先停用旧版，再启用本市场版本，避免两个同名 MCP 同时运行。卸载插件不会删除你的 SVN/Git 项目；历史快照需单独按需清理。

## 测试与反馈

```text
python -m unittest discover -s plugins/local-scm-diff/tests -v
```

测试需要 `git`、`svn`、`svnadmin`，仅在临时目录创建仓库。GitHub Actions 运行 Ubuntu 回归检查；不等同于所有桌面宿主的兼容保证。

请在 [Issues](https://github.com/123WEIJIE123/codex-scm-diff/issues) 反馈：系统、Codex/Python/SVN/Git 版本、复现步骤和脱敏报错。不要附访问凭证、快照或私有项目源码。欢迎通过 Pull Request 贡献。

[更新记录](CHANGELOG.md) · [贡献说明](CONTRIBUTING.md) · [MIT License](LICENSE)
