"""Exploratory raw vs scored top-five comparison; no relevance judgments."""
import json
import os
from pathlib import Path
import statistics
import time
import urllib.parse
import urllib.request

BASE = os.getenv('POC_URL', 'http://localhost:8000').rstrip('/')
QUERIES = ('index inversé', 'recherche distribuée', 'réplication reprise')
REPEATS = 3


def get(path):
    start = time.perf_counter()
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        data = json.load(response)
    return data, (time.perf_counter() - start) * 1000


def compare(query, profiles):
    paths = {p['id']: '/api/search?' + urllib.parse.urlencode({
        'q': query, 'ranking': p['id'], 'source': 'all', 'limit': 5}) for p in profiles}
    for path in paths.values():
        get(path)  # Warm each mode outside the recorded sample.
    samples = {key: [] for key in paths}
    last = {}
    totals = set()
    for round_number in range(REPEATS):
        keys = list(paths)
        keys = keys[round_number:] + keys[:round_number]
        for key in keys:
            payload, elapsed = get(paths[key])
            samples[key].append(elapsed)
            last[key] = payload
            totals.add(payload['total'])
    raw = {h['id']: rank for rank, h in enumerate(last['unranked']['hits'], 1)}
    rows = []
    for profile in profiles:
        key = profile['id']
        hits = last[key]['hits']
        rows.append({'mode': key, 'title': profile['title'],
            'median_roundtrip_ms': round(statistics.median(samples[key]), 2),
            'samples': REPEATS, 'total_matches': last[key]['total'],
            'common_with_raw_top5': sum(h['id'] in raw for h in hits),
            'raw_top_size': len(raw),
            'top5': [{'rank': rank, 'id': h['id'], 'title': h['title'],
                'raw_rank_in_top5': raw.get(h['id']), 'score': h['score']}
                for rank, h in enumerate(hits, 1)]})
    return {'query': query, 'source': 'all', 'observed_match_counts': sorted(totals), 'results': rows}


def main():
    metadata, _ = get('/api/rankings')
    profiles = sorted(metadata['profiles'], key=lambda p: p['id'] != 'unranked')
    report = {'measured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'comparison': [compare(query, profiles) for query in QUERIES],
        'limits': 'Same lexical query, type=any, French analysis and source=all. Live corpus, non-atomic calls; '
            'raw order is unspecified. One warm-up per query/mode then three sequential measured calls, '
            'interleaved by mode with a rotated start. Durations include network, HTTP and decoding, '
            'not isolated ranking CPU cost. Top-five overlap measures agreement, not quality. '
            'No relevance labels, significance, capacity or superiority claim.'}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if output := os.getenv('COMPARE_OUTPUT'):
        Path(output).write_text(rendered + '\n', encoding='utf-8')
    if summary := os.getenv('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write('\n### Recherche brute et scoring — échantillon exploratoire\n\n')
            stream.write('Trois appels mesurés après échauffement par mode et requête. Corpus vivant ; '
                'le recouvrement ne mesure pas la pertinence.\n\n')
            stream.write('| Requête | Mode | Premier résultat | Communs avec le top brut | Médiane aller-retour |\n')
            stream.write('| --- | --- | --- | --- | --- |\n')
            for comparison in report['comparison']:
                for row in comparison['results']:
                    first = row['top5'][0]['title'] if row['top5'] else 'Aucun'
                    values = [comparison['query'], row['title'], first,
                        f"{row['common_with_raw_top5']}/{row['raw_top_size']}", f"{row['median_roundtrip_ms']} ms"]
                    stream.write('| ' + ' | '.join(v.replace('|', '\\|').replace('\n', ' ') for v in values) + ' |\n')
            stream.write('\n```json\n' + rendered + '\n```\n')


if __name__ == '__main__':
    main()
