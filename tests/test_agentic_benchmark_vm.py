from scripts.agentic_benchmark_vm import content_hash, percentile, summarize, configured_keys


def test_empty_or_failed_samples_never_become_zero_latency():
    assert percentile([], .95) is None
    stats=summarize([{'status':503,'duration_ms':7}])
    assert stats['errors']==1 and stats['successful_p95_ms'] is None
    assert stats['all_attempts_p95_ms']==7


def test_errors_are_kept_in_denominator_and_all_attempt_latency():
    stats=summarize([{'status':200,'duration_ms':2},{'status':503,'duration_ms':100}])
    assert stats['requests']==2 and stats['errors']==1
    assert stats['successful_p95_ms']==2 and stats['all_attempts_p95_ms']==100


def test_content_digest_ignores_dictionary_order_but_not_evidence_changes():
    assert content_hash({'a':1,'b':2})==content_hash({'b':2,'a':1})
    assert content_hash({'value':1})!=content_hash({'value':2})


def test_key_probe_returns_only_booleans_and_ignores_placeholders(tmp_path,monkeypatch):
    for key in ('MISTRAL_API_KEY','OPENAI_API_KEY','HF_TOKEN'):
        monkeypatch.delenv(key,raising=False)
    (tmp_path/'.env').write_text('MISTRAL_API_KEY=${MISSING}\nOPENAI_API_KEY=\nHF_TOKEN=unit-test-placeholder\n')
    def no_docker(*args,**kwargs):
        raise OSError('docker unavailable in test')
    monkeypatch.setattr('scripts.agentic_benchmark_vm.subprocess.run',no_docker)
    result=configured_keys(tmp_path)
    assert result['MISTRAL_API_KEY'] is False and result['OPENAI_API_KEY'] is False
    assert result['HF_TOKEN'] is True and result['container_check_unavailable'] is True
    assert all(isinstance(value,bool) for value in result.values())


def test_failed_mcp_discovery_is_not_overwritten_by_successful_search(tmp_path,monkeypatch):
    import json
    from scripts import agentic_benchmark_vm as vm
    class FakeService:
        calls=0
        def __init__(self,*args): pass
        def call(self,path,body=None):
            self.calls+=1
            if path=='/tools/mcp': return 403,None,1
            if path.startswith('/api/documents/'): return 200,{'id':path.rsplit('/',1)[1]},1
            if path.startswith('/api/search?'): return 422,None,1
            return 200,{},1
        def search(self,*args):
            self.calls+=1
            return 200,{'hits':[]},1
    monkeypatch.setattr(vm,'Service',FakeService)
    monkeypatch.setattr(vm,'key_configuration_summary',lambda root: {})
    (tmp_path/'evaluation').mkdir()
    (tmp_path/'evaluation/queries.json').write_text(json.dumps([
        {'id':f'q0{i}-1','split':'dev','query':f'term{i}'} for i in range(1,6)]))
    report=vm.run(tmp_path,'http://localhost',repetitions=1)
    assert report['capabilities']['mcp_discovery_status']==403
    assert len(report['samples'])==15
    assert all(row['answer_correct'] is None and row['citation_support'] is None for row in report['samples'])
