# DRadar Dashboard · 众测雷达终端看板

[![CI](https://github.com/sdy623/dradar-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/sdy623/dradar-dashboard/actions/workflows/ci.yml)

基于 [众测雷达（Crowd Radar）](https://deng.codexradar.com/) 与[官方 DRadar CLI](https://github.com/codex-radar/dradar) 的非官方包装。网页信息汇总、IQ 简报、模型推荐和任务控制集中在这个终端看板中。Python 3.11+，支持 Windows、Linux、macOS，无需 PowerShell。

| 名称 | 对应内容 |
| --- | --- |
| 众测雷达 / Crowd Radar | 官方网站 `deng.codexradar.com` |
| DRadar / `dradar` | 官方运行器，负责领题、执行、上传与查询服务端判分 |
| DRadar Dashboard / `dradar-dashboard` | 本仓库的 CLI 包装与终端看板 |
| 网站 API | 网站提供的数据接口；不是另一个 CLI 或项目名称 |

命名依据：[官方项目元数据](https://github.com/codex-radar/dradar/blob/main/pyproject.toml)。本项目不占用 `dradar` 命令、不复制官方运行器，也不自行实现判分与上传协议。

## 基于包装 CLI 的 Skill

仓库自带 [dradar-dashboard Skill](skills/dradar-dashboard/SKILL.md)，支持 Codex 和 Claude Code：

- 模型 IQ 简报：分别给出最高 IQ 与最高 IQ／API 等价费用，保留覆盖率、样本和数据时间。
- 使用推荐：按模型代际分组，只从已确认可用的模型中推荐；没有可用性证据时明确标注。
- 任务提交：经本包装的 `prepare` / `run` / `progress` / `upload`，交给已配置的官方 DRadar 运行器；上传成功和判分完成分别验证。

将 `skills/dradar-dashboard` 文件夹放到 Codex 的 `~/.codex/skills/` 或 Claude Code 的 `~/.claude/skills/`。调用示例：`使用 dradar-dashboard，给我 Codex 模型 IQ 简报，并推荐性价比最好和 IQ 最高的各一个。` Skill 不会把查询需求当成运行题目的授权。

v0.2.0 将原 `zhongce-radar-cli` 更名为 `dradar-dashboard`。原 `radar` / `radar-codex` / `radar-claude` 入口已移除，改用本页新命令；旧包若仍安装，请先卸载再安装新版。官方 `DRADAR_HOME` 不变；包装配置和缓存目录改为 `dradar-dashboard`，可用 `DRADAR_DASHBOARD_CONFIG` 显式指向原配置，无需迁移官方数据或凭据。

首页优先显示模型 IQ，可用方向键、鼠标滚轮上下浏览。包含站点在线人数、并发任务、本人排名、全账号提交与判分记录、高倍率候选、题目大表。网站请求统一使用 **aiohttp + asyncio**，各板块独立更新，慢大表不阻塞 IQ。

## 快速运行

从 GitHub 固定版本运行，无需克隆：

```sh
uvx --from git+https://github.com/sdy623/dradar-dashboard@v0.2.0 dradar-dashboard
uvx --from git+https://github.com/sdy623/dradar-dashboard@v0.2.0 dradar-dashboard-claude
```

`uv tool run` 与 `uvx` 等价。首次运行需要下载依赖，之后使用 uv 缓存。**目前未发布到 PyPI**，不能省略 `--from` 直接把包名当作已发布的 PyPI 包。

克隆后使用 uv：

```sh
git clone https://github.com/sdy623/dradar-dashboard.git
cd dradar-dashboard
uv run dradar-dashboard
uv run dradar-dashboard-codex submissions --limit 10
uv run dradar-dashboard-claude dashboard --json
```

也可直接使用 Python：

```sh
python -m pip install .
python -m dradar_dashboard
python dashboard.py codex submissions --limit 10
```

Windows 的 Python 启动器也可用 `py -m dradar_dashboard`。二进制下载见 [Releases](https://github.com/sdy623/dradar-dashboard/releases)：解压后运行 `dradar-dashboard` / `dradar-dashboard.exe`，不需要单独安装 Python。通用二进制用 `dradar-dashboard claude-code` 切换 Claude。

## 入口与常用命令

```sh
dradar-dashboard                         # 默认 Codex；交互终端打开滚动页面
dradar-dashboard claude-code             # Claude 视图
dradar-dashboard benchmarks              # 公共题库，无需登录
dradar-dashboard whoami --json
dradar-dashboard rank --period month
dradar-dashboard submissions --limit 20
dradar-dashboard submissions --records-scope current
dradar-dashboard hot --model gpt-6-astra --effort max
dradar-dashboard dashboard --json        # 一次性快照，适合自动化
dradar-dashboard local status --json     # 只读本地保存状态
dradar-dashboard config                  # 显示数据、配置、缓存位置，不显示凭据
```

`dradar-dashboard-codex`、`dradar-dashboard-claude` 是同一程序的便捷入口。管道和 `--json` 默认使用一次性看板。`--benchmark` 切换题库，`--period all` 查看所有月份，`--watch --interval 60` 定期查询。

| 按键 | 功能 |
| --- | --- |
| ↑↓、j/k、滚轮 | 上下浏览 |
| PgUp/PgDn、空格 | 翻页 |
| Home/End、g/G | 首尾 |
| 1–6、Tab | 跳转板块 |
| /、Enter、n | 搜索、确认、下一项 |
| r、q | 刷新、退出 |

## 身份与配置

默认读取官方客户端已有的 `~/.dradar/config.json` 中的雷达身份，也支持 `DRADAR_HOME` 或 `--home` 指定数据目录。**不会读取模型登录文件，也不会复制原凭据**。可选择通过进程环境变量 `DRADAR_DASHBOARD_TOKEN` 传入雷达 Token；勿将它写进命令行参数、脚本仓库或 issue。

公开 IQ 和题库不要求个人身份。未登录时个人板块显示缺失，不伪造 0 条记录。凭据固定发送到 `https://api.codexradar.com`；拒绝重定向，终端输出脱敏。

`dradar-dashboard config` 显示平台对应的客户端配置位置。`--config` / `DRADAR_DASHBOARD_CONFIG` 可覆盖。此配置与官方认证文件分离。例如：

```json
{
  "models": {
    "codex": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "claude-code": ["claude-opus-5", "claude-sonnet-5"]
  }
}
```

只展示配置与网站目录的交集，排除 ultra。未配置时展示对应 harness 的网站目录；这不代表本机认证或执行能力已经验证。在已有路由项目中，还可识别本地 `tenbin-route/relay.mjs` 的离线支持列表；查询不连接、启动或依赖该路由。

缓存位于用户缓存目录，绝不写入包安装目录或 uvx 临时环境。仅缓存公共 IQ，并显示缓存时间；个人记录、Token、租约和在线人数不落入缓存。

## 数据口径

- 模型 IQ 使用网站 `equal_latest_3` 专门接口，保留样本/覆盖信息，不用个人通过率替代。
- 费用是网站 API 等价估计，不是订阅实际扣款。未知成本保留未知。
- 在线人数、参与人数、worker、并发题数分别显示，区分全站、当前题库、当前模型范围。
- 个人记录默认包含当前题库内所有 harness，最新提交在前，分别显示本机时区的提交与判分时间。`--records-scope current` 仅看当前 harness，IQ 范围不受影响。
- 交互页最多加载 200 条记录；搜索仅查已加载内容，截断会明确提示。
- 当前任务来自只读租约接口，不把已完成历史误报为活跃任务。本地 Fleet 文件也不代表进程仍在运行。
- 单请求总超时 15 秒，看板最多等待 20 秒；独立失败不会隐藏其他板块。每进程共用最多 10 条连接，不自动重试失败请求。

## 可选的本地控制

查询和浏览默认不执行评测。需要控制时，在客户端配置中显式设置可执行文件及参数数组：

```json
{
  "runtime": {
    "command": ["dradar"],
    "cwd": "/absolute/path/to/your/project"
  }
}
```

运行器须由你按网站的版本和模型路由要求准备，本工具不下载、不升级、不替换官方运行器。也可将 `command` 配置为自己的跨平台包装器。缺少配置时直接失败，不回退另一通道。所有参数以数组交给 `subprocess`，不经过 shell 字符串拼接；子进程继承 UTF-8 和 DRADAR_HOME。

支持 `prepare`、`run --dry-run`、`run`、`progress`、`stop`、`upload`，保留计划 harness、到期、并发、非 ultra 和补领范围检查。运行码建议通过 `DRADAR_DASHBOARD_PLAN_CODE` 环境变量提供。写操作不允许 `--watch`。精确命令文件保留 `FilePath` / `ArgumentList`；需要显式 `runtime.allow_exact_commands=true`，不会改写版本和参数。

已包含 `dradar-tenbin.ps1` 的受管理项目仍使用原项目控制入口；通用客户端拒绝替换其强制路由。此发行版没有执行真实评测的自动验收，只通过运行器替身验证控制分发。

## 开发与打包

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
uv run --group build python scripts/build_executable.py
```

可执行文件按操作系统原生构建，不能用 Windows 构建声称验证了 macOS/Linux。GitHub Actions 在三种系统运行测试，并在版本标签下构建独立可执行文件、Python wheel/sdist 和 SHA-256 清单。支持与用法参见 [uv tools](https://docs.astral.sh/uv/concepts/tools/) 和 [PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)。

这是社区客户端，与众测雷达官方无隶属关系。接口来自网站现有查询行为，不保证长期兼容；遇到变化会显示错误，避免误报数据。MIT License。
