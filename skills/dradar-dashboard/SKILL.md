---
name: dradar-dashboard
description: Use the DRadar Dashboard CLI wrapper for Crowd Radar model IQ briefings, cost-efficiency recommendations, personal scoring records, and authorized benchmark runs or submissions through the official DRadar backend. Use for deng.codexradar.com tasks, not unrelated model benchmarks.
---

# DRadar Dashboard

Use the user's **dradar-dashboard CLI wrapper as the primary entry point**. It combines the website's information with the official DRadar execution backend. Do not replace this wrapper with an independently designed client.

## Identity and entry points

- Website: [众测雷达 / Crowd Radar](https://deng.codexradar.com/).
- Official executable/package: `dradar`, from [codex-radar/dradar](https://github.com/codex-radar/dradar).
- This wrapper: `dradar-dashboard`, from [sdy623/dradar-dashboard](https://github.com/sdy623/dradar-dashboard).
- API means the website's HTTP interface; it is not a separate CLI product.
- `dradar-dashboard codex ...` and `dradar-dashboard claude-code ...` select views. No bare `radar` aliases.

Read the active workspace's AGENTS.md before operating. Set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for Python processes. Preserve the selected `DRADAR_HOME`, account, harness, provider, model, effort, version and route. Start with the installed wrapper's `--version` / `--help` and `config`. If unavailable, a portable query entry is `uvx --from git+https://github.com/sdy623/dradar-dashboard@v0.2.0 dradar-dashboard ...`; use the project's pinned launcher instead wherever required.

Do not inspect or copy model authentication. Reuse the wrapper's existing website identity. Missing identity should produce an explicit unavailable state, not fabricated empty records. Querying the website is independent of Tenbin and does not start a model.

## Choose the requested workflow

- **IQ briefing or model recommendation:** read [references/briefing.md](references/briefing.md). Use `dashboard --json`; the included summarizer separates highest IQ from best IQ/cost and preserves evidence limitations.
- **My records or task status:** use `submissions --records-scope all --period all --json`, `tasks --json`, and `local status --json`. Personal records cover both harnesses by default; IQ still follows the selected harness. Report collection time, pagination truncation, submitted time and graded time separately.
- **Run, resume, upload or stop:** read [references/tasks.md](references/tasks.md). Keep the wrapper as the user entry point and delegate actual execution to official DRadar using exact website/versioned command contracts.

A query, recommendation or request to install this skill does not authorize new benchmark runs, submissions or automatic refill. When the user has already authorized a precise action, carry it through without asking again. Ask only for essential missing scope or an official decision that has not already been authorized.

For an automation explicitly requested by the user, preserve the requested model, schedule, timezone and retention; use the host's supported scheduler. A briefing invocation by itself does not create or change an automation.
