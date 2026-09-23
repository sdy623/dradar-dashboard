# IQ briefing and recommendations

Collect a fresh snapshot with `dradar-dashboard codex dashboard --json` or the `claude-code` view. The wrapper uses the website's `intelligence-efficiency` feed and independent asynchronous requests. IQ must use `equal_latest_3`; do not derive it from personal pass rates. Official DRadar does not document an `iq` command in the audited CLI, so do not invent `dradar iq`.

For a compact deterministic projection, pipe the JSON to the skill's helper:

```sh
dradar-dashboard codex dashboard --json | python <skill-directory>/scripts/summarize_iq.py
```

The helper only transforms stdin and has no network or execution side effects. A partial dashboard can still provide valid IQ; an IQ-feed error blocks recommendations. Table errors can leave coverage unknown, which excludes those rows from the default qualified selection. Do not retry all HTTP requests merely because an unrelated personal section failed.

## Selection

1. Preserve benchmark, harness, observation time and source update time. Do not combine different benchmarks into one IQ ranking.
2. Include model and effort in every candidate. Exclude ultra; preserve the user's lower effort. The wrapper's configured model whitelist limits display, but website presence alone is not proof that the user can run a model. Before recommending actual use, intersect with explicit user capability constraints and known provider availability. Mark unverified availability rather than changing credentials or sending trial model calls.
3. Show **highest observed IQ** separately from the **recommended highest IQ**. Default recommendation requires at least 60% task coverage and a positive sample count; show sparse leaders as preliminary, never hide their low coverage. The 60% threshold is this companion's comparison policy, not an official benchmark rule. Use the user's explicit threshold when given.
4. Compute **IQ / API-equivalent USD per task** only when IQ and cost are finite and cost is strictly positive. Unknown or zero cost cannot win through division by zero. This is not IQ per token, subscription billing, profit, or actual money charged. Preserve the site's cost aggregation label.
5. Group by model generation/family, e.g. GPT-6 Astra, GPT-5.6 Sol, Terra, Luna, then older models. The helper orders generations then known families; this presentation order is not evidence of capability. Within a model group show effort variants ordered by IQ. Include unfamiliar model names without inventing their tier.
6. Report two distinct recommendations: qualified highest IQ and qualified highest IQ/cost. If one model wins both, say so. If none qualify, report insufficient evidence.

Keep the briefing short: data time and benchmark; the two recommendations with IQ, cost, ratio, coverage and samples; a small grouped comparison; freshness/availability limitations. Do not turn a recommendation into automatic claiming or task submission.

Sources audited 2026-09-23: [website](https://deng.codexradar.com/), [official CLI commands](https://github.com/codex-radar/dradar/blob/main/README.md), [official package identity](https://github.com/codex-radar/dradar/blob/main/pyproject.toml). Recheck installed help when the official CLI version changes.
