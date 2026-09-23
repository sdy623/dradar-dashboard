# Changelog

## 0.2.0

- 按官方命名核验，统一为 DRadar Dashboard / dradar-dashboard；保留 DRadar 官方关键词并区分包装和原版。
- 仓库、Python 包、模块、入口、可执行文件、环境变量、配置缓存目录及发布包同步改名；移除旧 radar 命令别名。
- 保留异步滚动看板与官方运行器控制包装，增加面向 IQ 简报、模型推荐和任务提交的 Skill。
- 官方运行器来源固定为 codex-radar/dradar，官网固定为 deng.codexradar.com。

## 0.1.0

- First open-source Python package; Windows, Linux, and macOS entry points without PowerShell.
- aiohttp async connection pool and independently refreshed IQ-first terminal dashboard.
- Account-wide submission history with explicit harness, submitted/graded timestamps, and local timezone.
- User-directory configuration/cache; no personal data or credentials in distributable artifacts.
- Explicit optional runtime command arrays and native executable builds.
