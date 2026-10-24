"""Read-only VM capability check and retrieval pilot; never an LLM QA score."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.corpus import CARDS

PROFILES = ('lexical-balanced', 'lexical-coverage', 'lexical-proximity')
KEY_NAMES = ('MISTRAL_API_KEY', 'OPENAI_API_KEY', 'HF_TOKEN')
SOURCE = 'systems-cards-fr-v2'


def percentile(values, proportion):
    values = sorted(values)
    return round(values[max(0, math.ceil(len(values) * proportion) - 1)], 3) if values else None


def summarize(samples):
    successful = [s['duration_ms'] for s in samples if s['status'] == 200]
    return {'requests': len(samples), 'errors': sum(s['status'] != 200 for s in samples),
            'successful_p50_ms': percentile(successful, .5),
            'successful_p95_ms': percentile(successful, .95),
            'all_attempts_p95_ms': percentile([s['duration_ms'] for s in samples], .95)}


def content_hash(documents):
    return hashlib.sha256(json.dumps(documents, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def configured_keys(root):
    """Only emit booleans for named credentials; never read arbitrary host files."""
    result = {key: bool(os.environ.get(key)) for key in KEY_NAMES}
    for name in ('.env', '.source-secrets.env'):
        path = root / name
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            key, value = key.strip(), value.strip().strip('\"\'')
            if sep and key in result and value and not value.startswith('${'):
                result[key] = True
    # Inspect only the running POC web container, and print booleans inside it.
    try:
        ids = subprocess.run(['docker', 'ps', '--filter', 'label=com.docker.compose.project=search-services-poc',
            '--filter', 'label=com.docker.compose.service=web', '--format', '{{.ID}}'],
            capture_output=True, text=True, check=True, timeout=10).stdout.split()
        if len(ids) == 1:
            code = 'import os,json;print(json.dumps({k:bool(os.getenv(k)) for k in '+repr(KEY_NAMES)+'}))'
            values = json.loads(subprocess.run(['docker', 'exec', ids[0], 'python', '-c', code],
                capture_output=True, text=True, check=True, timeout=10).stdout)
            for key in result:
                result[key] |= bool(values.get(key))
    except (OSError, subprocess.SubprocessError, ValueError):
        result['container_check_unavailable'] = True
    return result


class Service:
    def __init__(self, base, deadline):
        self.base, self.deadline, self.calls = base.rstrip('/'), deadline, 0

    def call(self, path, body=None):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Pilot wall-time budget exceeded')
        headers = {'Accept': 'application/json, text/event-stream'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.base + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        started = time.perf_counter()
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = response.read(2_000_001)
                if len(payload) > 2_000_000:
                    raise ValueError('Response exceeds pilot limit')
                return 200, json.loads(payload), round((time.perf_counter()-started)*1000, 3)
        except urllib.error.HTTPError as exc:
            return exc.code, None, round((time.perf_counter()-started)*1000, 3)
        except (OSError, ValueError):
            return 0, None, round((time.perf_counter()-started)*1000, 3)

    def search(self, query, profile):
        return self.call('/api/search?' + urllib.parse.urlencode({
            'q': query, 'limit': 5, 'source': SOURCE, 'ranking': profile}))


def snapshot(service):
    docs = {}
    for identifier, *_ in CARDS:
        status, data, _ = service.call('/api/documents/' + identifier)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError('Fixed-card snapshot unavailable: ' + identifier)
        docs[identifier] = data
    return docs


def key_configuration_summary(root):
    return {'checked_locations': ['benchmark process environment', '.env', '.source-secrets.env', 'running web container'],
            'configured': configured_keys(root), 'secret_values_collected': False}


def run(root, base, repetitions=5):
    if repetitions not in range(1, 6):
        raise ValueError('Repetitions must be between 1 and 5')
    started = datetime.now(timezone.utc).isoformat()
    service = Service(base, time.monotonic() + 180)
    status, ready, _ = service.call('/readyz')
    if status != 200:
        raise RuntimeError('Search service is not ready')
    tools = []
    for identifier, method, params in [(1, 'initialize', {'protocolVersion':'2025-03-26',
        'capabilities':{},'clientInfo':{'name':'vm-pilot','version':'1'}}), (2, 'tools/list', {})]:
        status, reply, _ = service.call('/tools/mcp', {'jsonrpc':'2.0','id':identifier,'method':method,'params':params})
        if status == 200 and method == 'tools/list':
            tools = [tool['name'] for tool in reply.get('result', {}).get('tools', [])]
    mcp_discovery_status = status
    keys = key_configuration_summary(root)
    datasets = {name: (root/'benchmark-data'/name).is_dir() for name in ('financebench','officeqa-pro-v2')}
    before = snapshot(service)
    _, ingestion_before, _ = service.call('/api/ingestion')
    queries = json.loads((root/'evaluation/queries.json').read_text())
    selected = [q for q in queries if q['split'] == 'dev' and q['id'] in
        {'q01-1','q02-1','q03-1','q04-1','q05-1'}]
    if len(selected) != 5:
        raise RuntimeError('The five predeclared development queries are required')
    for profile in PROFILES:
        service.search(selected[0]['query'], profile)  # Excluded warm-up.
    cases = [(q, profile, repetition) for repetition in range(repetitions)
             for q in selected for profile in PROFILES]
    random.Random(20261024).shuffle(cases)
    samples = []
    for question, profile, repetition in cases:
        status, data, duration = service.search(question['query'], profile)
        hits = data.get('hits', []) if status == 200 and isinstance(data, dict) else []
        samples.append({'question_id':question['id'], 'profile':profile, 'repetition':repetition+1,
            'status':status, 'duration_ms':duration, 'document_ids':[h['id'] for h in hits],
            'scores':[h.get('score') for h in hits],
            'provenance_present':all(bool(h.get('source')) for h in hits) if hits else None,
            'answer_correct':None, 'evidence_coverage':None, 'citation_support':None,
            'tokens':None, 'model_cost':None})
    validations = []
    for params, expected in [({'q':'','source':SOURCE},422), ({'q':'x','limit':100,'source':SOURCE},422),
                            ({'q':'x','ranking':'unknown','source':SOURCE},422)]:
        status, _, _ = service.call('/api/search?'+urllib.parse.urlencode(params))
        validations.append({'check':'input_validation','expected_status':expected,'status':status,'passed':status==expected})
    status, no_match, _ = service.search('zzqvxmjrtp6971835042', PROFILES[0])
    validations.append({'check':'no_lexical_match','status':status,
        'passed':status==200 and isinstance(no_match,dict) and no_match.get('hits')==[]})
    after = snapshot(service)
    _, ingestion_after, _ = service.call('/api/ingestion')
    grouped = defaultdict(list)
    for sample in samples:
        grouped[sample['profile']].append(sample)
    per_query = []
    for q in selected:
        for profile in PROFILES:
            values = [s for s in samples if s['question_id']==q['id'] and s['profile']==profile]
            successes = [s for s in values if s['status']==200]
            per_query.append({'question_id':q['id'],'query':q['query'],'profile':profile,
                **summarize(values), 'distinct_rankings':len({tuple(s['document_ids']) for s in successes}),
                'relevance_score':None})
    return {'schema_version':1, 'run_id':started, 'execution_location':'demo VM',
        'runner_source_commit':os.getenv('BENCHMARK_SOURCE_SHA'), 'transport':'VM loopback HTTP',
        'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'queries_hash':content_hash(selected), 'logical_cpus':os.cpu_count(),
        'kind':'retrieval_pilot_not_agentic_QA', 'repetitions':repetitions, 'top_k':5,
        'request_count':service.calls, 'ready':ready,
        'capabilities':{'mcp_tools':tools,'mcp_discovery_status':mcp_discovery_status,
            'credentials':keys,'datasets_in_benchmark_data':datasets,
            'agentic_llm_runner_in_this_pilot':False,'human_QA_judgments_in_this_pilot':False},
        'corpus':{'source_filter':SOURCE,'documents':len(before),'hash_before':content_hash(before),
            'hash_after':content_hash(after),'documents_unchanged':before==after,'index_frozen':False},
        'summaries':[{ 'profile':profile, **summarize(grouped[profile])} for profile in PROFILES],
        'per_query':per_query, 'samples':samples, 'robustness_checks':validations,
        'ingestion':{'before':ingestion_before,'after':ingestion_after},
        'experiments':[
            {'id':'diagnostic','status':'blocked','reason':'Requires an LLM QA runner, reference pages and verified extracted evidence.'},
            {'id':'budget','status':'partial','reason':'Equal top-k and repetitions measured for three lexical profiles; no LLM tokens or monetary quality comparison.'},
            {'id':'evidence','status':'partial','reason':'Document IDs and source presence collected; no human QA evidence/citation judgments.'},
            {'id':'generalization','status':'blocked','reason':'OfficeQA Pro/V2 data and a frozen agent configuration are required.'},
            {'id':'robustness','status':'partial','reason':'Input validation and no-match retrieval checked; no LLM abstention or corpus mutation test.'},
            {'id':'stability','status':'partial','reason':'Repeated latency and ranking observations; no confidence interval on answer accuracy without labeled answers.'}],
        'limits':[
            'Development queries only: this is not a held-out relevance or QA evaluation.',
            'Source documents are hashed before and after; the global live Vespa index and its BM25 statistics remain unfrozen.',
            'One sequential client; no capacity, concurrency or cost-quality superiority claim.',
            'Latency is loopback and successful-response quantiles exclude errors, which are reported separately.',
            'No external model calls, paid API calls, writes, deletes or ingestion pauses are performed.',
            'Only named local configuration and benchmark-data directories were checked; absence is not an account-wide credential/data search.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://localhost:8000')
    parser.add_argument('--output-dir',default='benchmark-results')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    report=run(root,args.base)
    output=Path(args.output_dir)
    output.mkdir(parents=True,exist_ok=True)
    rendered=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    name=datetime.now(timezone.utc).strftime('retrieval-pilot-%Y%m%dT%H%M%SZ.json')
    (output/name).write_text(rendered)
    temporary=output/'latest.json.tmp'
    temporary.write_text(rendered)
    temporary.replace(output/'latest.json')
    print('VM_BENCHMARK_SUMMARY '+json.dumps({k:report[k] for k in
        ('run_id','kind','request_count','capabilities','corpus','summaries','robustness_checks','experiments')},ensure_ascii=False))
    print('VM_BENCHMARK_REPORT '+str(output/name))


if __name__ == '__main__':
    main()
