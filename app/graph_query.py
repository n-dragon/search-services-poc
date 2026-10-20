"""Isolated server-generated SPARQL SELECT on a bundled graph."""
import json
from pathlib import Path
import resource
import sys
from rdflib import Graph,Literal,Namespace,RDFS


def load_graph():
    snapshot=json.loads((Path(__file__).parent/'data'/'wikidata-graph.json').read_bytes())
    if len(snapshot['entities'])>60:raise ValueError('Graph exceeds entity bound')
    graph=Graph();wd=Namespace('http://www.wikidata.org/entity/');prop=Namespace('http://www.wikidata.org/prop/direct/')
    for row in snapshot['entities']:
        for lang,label in row['labels'].items():graph.add((wd[row['id']],RDFS.label,Literal(label,lang=lang)))
        for predicate,target in row['edges']:graph.add((wd[row['id']],prop[predicate],wd[target]))
    if len(graph)>2000:raise ValueError('Graph exceeds triple bound')
    return graph


def main():
    resource.setrlimit(resource.RLIMIT_CPU,(2,2))
    resource.setrlimit(resource.RLIMIT_AS,(192*1024*1024,192*1024*1024))
    query=sys.stdin.read(4001)
    if len(query)>4000:raise ValueError('Query exceeds bound')
    graph=load_graph()
    result=json.loads(graph.query(query).serialize(format='json'))
    print(json.dumps({'columns':result['head']['vars'],'rows':result['results']['bindings'],'triples':len(graph),'row_format':'sparql-json-bindings'}))


if __name__=='__main__':main()
