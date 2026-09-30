#!/usr/bin/env python3
"""Host-side explicit operator commands; never exposed as a main Codex tool."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from jasmine_core.capture.p1_codex_hook import _private_file
from jasmine_core.capture.p2_runtime import Deadline, DeadlineClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-url',required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('action', choices=['show-review','approve','reject-review','correct','reject','rerun'])
    parser.add_argument('object_id')
    parser.add_argument('--request-file',type=Path,help='exact fully reviewed request body with key/revisions/reason')
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    token=_private_file(args.token_file.resolve(strict=True)).decode().strip()
    client=DeadlineClient(args.core_url,token,Deadline(140))
    if args.action=='show-review':
        result=client.get('/v1/reviews/'+args.object_id)
    else:
        if not args.request_file: parser.error('mutation requires exact --request-file')
        body=json.loads(_private_file(args.request_file.resolve(strict=True)))
        review=args.action in ('approve','reject-review')
        path=('/v1/reviews/' if review else '/v1/interpretations/')+args.object_id+'/'+('reject' if args.action=='reject-review' else args.action)
        result=client.post(path,body,cap=125 if args.action=='rerun' else 3)
    raw=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.receipt:
        from jasmine_core.capture.p2_runtime import atomic_json
        atomic_json(args.receipt,result)
    print(raw,end='')

if __name__=='__main__': main()
