"""Read wrapper dashboard JSON from stdin; emit a credential-free IQ briefing."""
import argparse
import json
import math
import re
import sys


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def model_order(model):
    match = re.match(r'gpt-(\d+)(?:\.(\d+))?(?:-(.*))?$', model)
    if match:
        family = (match[3] or '').split('-')[0]
        return (0, -int(match[1]), -int(match[2] or 0), {'astra': 0, 'sol': 1, 'terra': 2, 'luna': 3}.get(family, 9), model)
    return (1, 0, 0, 0, model)


def summarize(payload, min_coverage=.6):
    if not number(min_coverage) or not 0 <= min_coverage <= 1:
        raise ValueError('min-coverage must be between 0 and 1')
    if 'error' in payload:
        raise ValueError('Dashboard query failed; no recommendation generated')
    data = payload.get('data', payload)
    if data.get('errors', {}).get('iq') or 'iq' in data.get('loading_sections', []):
        raise ValueError('IQ data is unavailable or still loading')
    rows = []
    for source in data.get('iq', []):
        if source.get('effort') == 'ultra' or not number(source.get('iq')):
            continue
        row = {key: source.get(key) for key in ('model', 'effort', 'iq', 'price', 'price_basis', 'samples', 'coverage', 'source_updated_at')}
        coverage = re.fullmatch(r'(\d+)/(\d+)', str(row['coverage']))
        fraction = int(coverage[1]) / int(coverage[2]) if coverage and int(coverage[2]) > 0 and int(coverage[1]) <= int(coverage[2]) else None
        row['coverage_fraction'] = fraction
        row['qualified'] = fraction is not None and fraction >= min_coverage and number(row['samples']) and row['samples'] > 0
        price = row['price']
        ratio = row['iq'] / price if number(price) and price > 0 else None
        row['iq_per_api_usd'] = ratio if number(ratio) else None
        rows.append(row)
    qualified = [row for row in rows if row['qualified']]
    priced = [row for row in qualified if row['iq_per_api_usd'] is not None]
    groups = []
    for model in sorted({row['model'] for row in rows}, key=model_order):
        groups.append({'model': model, 'variants': sorted([row for row in rows if row['model'] == model], key=lambda row: (-row['iq'], row['effort']))})
    return {
        'benchmark': data.get('benchmark'), 'harness': data.get('harness'),
        'observed_at': data.get('observed_at', payload.get('observed_at')),
        'source_updated_at': data.get('source_updated_at'), 'cached': bool(data.get('cached')),
        'minimum_coverage': min_coverage, 'availability': 'Verify local model access before acting on recommendations',
        'cost_basis': 'Website API-equivalent USD per task, not subscription charges or per-token cost',
        'highest_observed_iq': max(rows, key=lambda row: row['iq'], default=None),
        'recommended_highest_iq': max(qualified, key=lambda row: row['iq'], default=None),
        'recommended_best_value': max(priced, key=lambda row: row['iq_per_api_usd'], default=None),
        'groups': groups,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min-coverage', type=float, default=.6)
    args = parser.parse_args()
    try:
        result = summarize(json.load(sys.stdin), args.min_coverage)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError):
        print('Invalid or unavailable IQ snapshot; no recommendation generated.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
