#!/usr/bin/env python3
"""Whole-packet lexical accounting with a fixed legacy-card counterfactual.

This is not a model experiment. The counterfactual changes only the language-card
text in the same current packet, for programs supported by both language versions.
New features have no executable 0.5 baseline and are reported separately.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from cairn.agent_tools import EditSession
from cairn.syntax import Parser
from cairn.teaching import CARDS


def text(value: object) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n'


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tiktoken',default=None)
    ap.add_argument('--output',type=Path,default=ROOT/'results/context_06.json')
    args=ap.parse_args()
    if args.tiktoken:
        import tiktoken
        encoding=tiktoken.get_encoding(args.tiktoken)
        count=lambda s:len(encoding.encode(s))
        tokenizer='tiktoken/'+args.tiktoken
    else:
        count=lambda s:len(s.encode('utf-8'))
        tokenizer='ByT5 plain byte-token encoding; no special tokens'
    prior=json.loads((ROOT/'bench/fixtures/cards_05.json').read_text())
    source=(ROOT/'examples/native.cairn').read_text()
    rows=[]
    for function in Parser(source).parse().functions:
        if function.static:continue
        packet=EditSession(source,function.name).packet()
        if set(packet['rule_cards'])-set(prior['cards']):
            raise ValueError('New-feature packet must not be called a legacy comparison.')
        old=copy.deepcopy(packet)
        old['rule_cards']={name:prior['cards'][name] for name in packet['rule_cards']}
        # Non-card source, task, type/effect metadata and all transport are equal.
        assert {k:v for k,v in old.items() if k!='rule_cards'}=={k:v for k,v in packet.items() if k!='rule_cards'}
        before,after=count(text(old)),count(text(packet))
        rows.append({'symbol':function.name,'cards':list(packet['rule_cards']),
            'legacy_card_packet_tokens':before,'current_packet_tokens':after,
            'saving_tokens':before-after,'saving_fraction':1-after/before,
            'current_packet_sha256':hashlib.sha256(text(packet).encode()).hexdigest(),
            'counterfactual_packet_sha256':hashlib.sha256(text(old).encode()).hexdigest()})
    current_only=[]
    systems='\n'.join((ROOT/'examples/systems/src'/name).read_text() for name in ['parse.cairn','sort.cairn','main.cairn'])
    for symbol in ['decimal','sort_bytes','sorted_even']:
        packet=EditSession(systems,symbol).packet()
        current_only.append({'symbol':symbol,'complete_packet_tokens':count(text(packet)),
            'cards':list(packet['rule_cards']),'legacy_comparison':None,
            'reason':'Scoped storage or tagged sums were not accepted in 0.5.'})
    before=sum(r['legacy_card_packet_tokens'] for r in rows)
    after=sum(r['current_packet_tokens'] for r in rows)
    result={'tokenizer':tokenizer,'model_trials':0,'method':'Current complete JSON packet; only prior/current card texts varied; repeated packets counted in full.',
        'baseline_card_source_sha256':prior['source_sha256'],
        'current_card_source_sha256':hashlib.sha256((ROOT/'src/cairn/teaching.py').read_bytes()).hexdigest(),
        'legacy_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'rows':rows,'current_only':current_only,
        'aggregate':{'packet_count':len(rows),'before':before,'after':after,'saving_fraction':1-after/before},
        'full_card_text':{'baseline':count('\n\n'.join(prior['cards'].values())),
                          'current':count('\n\n'.join(CARDS.values()))},
        'limitations':['Not a real edit transcript or model proficiency measurement.',
            'Task/reference authorship, setup and repair history are not included in this single-packet comparison.',
            'Broader current cards have different feature coverage; text savings are not a controlled semantic-information ablation.',
            'New-feature packets retain the entire bidirectional call-graph component and can be much larger than source.',
            'Default byte-level counts are not frontier-model BPE counts.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(text(result))
    print(text({'aggregate':result['aggregate'],'full_card_text':result['full_card_text'],'tokenizer':tokenizer}))
    return 0

if __name__=='__main__':raise SystemExit(main())
