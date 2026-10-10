"""Receipt boundary regressions, independent of a browser or live Core."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/p6'))
from context_budget_integrity import credential,product_claims,png_capture,observer_witness,scalar_columns


def chunk(kind,body):
    return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)


def png(width=1400,height=2):
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*(width*3))*height))+chunk(b'IEND',b'')


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_F02_extra_alert_claim(self):
        with self.assertRaisesRegex(ValueError,'F02'):product_claims({'html':'<p>构建不等于注入。</p><div role="alert">此包已经被 Agent 实际接收，可以直接使用。</div>'})
    def test_F02_extra_summary_claim(self):
        with self.assertRaisesRegex(ValueError,'F02'):product_claims({'html':'<summary>注入成功</summary>'})
    def test_F02_status_code_claim(self):
        with self.assertRaisesRegex(ValueError,'F02'):product_claims({'html':'<div role="status"><code>已实际注入</code></div>'})
    def test_F02_historical_raw_quote_passes(self):
        product_claims({'html':'<p>本页没有 Agent 实际接收回执，构建不等于注入。</p><div class="rawtext"><pre>此包已经被 Agent 实际接收，可以直接使用。</pre></div>'})
    def test_F02_historical_JSON_quote_passes(self):
        product_claims({'html':'<div class="jsonblock"><pre>{"text":"注入成功"}</pre></div>'})
    def test_F02_negative_statement_passes(self):
        product_claims({'html':'<p>重新读取不证明当前可注入。本页没有 Agent 实际接收回执，构建不等于注入。</p>'})
    def image(self,data):
        p=self.root/'capture.png';p.write_bytes(data);return p
    def clip(self):return {'x':0,'y':0,'width':1400,'height':2,'scale':1}
    def test_F07_native_image_passes(self):self.assertEqual(png_capture(self.image(png()),self.clip()),(1400,2))
    def test_F07_signature_only_refused(self):
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(png()[:8]),self.clip())
    def test_F07_truncated_IDAT_refused(self):
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(png()[:-16]),self.clip())
    def test_F07_wrong_dimensions_refused(self):
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(png(1399)),self.clip())
    def test_F07_wrong_CRC_refused(self):
        data=bytearray(png());data[25]^=1
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(data),self.clip())
    def test_F07_signature_with_arbitrary_bytes_refused(self):
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(b'\x89PNG\r\n\x1a\n'+b'x'*100),self.clip())
    def test_F07_trailing_bytes_refused(self):
        with self.assertRaisesRegex(ValueError,'F07'):png_capture(self.image(png()+b'junk'),self.clip())
    def key(self):
        path=self.root/'registry.db';con=sqlite3.connect(path)
        con.executescript('CREATE TABLE actors(actor_id TEXT,kind TEXT,home_host_id TEXT);CREATE TABLE api_keys(key_id TEXT,actor_id TEXT,scopes TEXT,revoked_at TEXT);')
        con.execute('INSERT INTO actors VALUES(?,?,?)',('actor-real','human','host-real'));con.execute('INSERT INTO api_keys VALUES(?,?,?,NULL)',('key-real','actor-real',json.dumps(['context:read'])));con.commit();con.close()
        return path,{'key_id':'key-real','actor_id':'actor-real','scopes':['context:read']}
    def test_F03_registered_identity_passes(self):
        path,meta=self.key();actor,scopes=credential(path,meta);self.assertEqual(actor['actor_id'],'actor-real');self.assertEqual(scopes,{'context:read'})
    def test_F03_forged_actor_refused(self):
        path,meta=self.key();meta['actor_id']='actor-false'
        with self.assertRaisesRegex(ValueError,'F03'):credential(path,meta)
    def test_F03_forged_scope_refused(self):
        path,meta=self.key();meta['scopes'].append('admin')
        with self.assertRaisesRegex(ValueError,'F03'):credential(path,meta)
    def test_F03_unregistered_key_refused(self):
        path,meta=self.key();meta['key_id']='missing'
        with self.assertRaisesRegex(ValueError,'F03'):credential(path,meta)
    def witness(self):
        source=self.root/'src/jasmine_core';source.mkdir(parents=True)
        for name in ('continuity.py','continuity_scan.py'):(source/name).write_text('source\n')
        sha=hashlib.sha256(b'source\n').hexdigest();start={'kind':'observer_started','pid':123,'seq':1,'at_unix':1,'scanner_source_sha256':sha,'continuity_source_sha256':sha,'method':'CPython profile entry/exit plus audit hook for every continuity scanner Popen attempt'}
        kinds=['request_start','scanner_call_enter','scanner_popen_attempt','scanner_call_exit','request_end'];rows=[start]+[{'kind':kind,'pid':123,'seq':i+2,'at_unix':i+2,'request_id':'request-one'} for i,kind in enumerate(kinds)]+[{'kind':'observer_stopped','pid':123,'seq':7,'at_unix':7}]
        return rows,[{'name':'private-core','pid':123,'group_gone':True}]
    def test_F05_positive_control_and_owned_witness_pass(self):
        rows,cleanup=self.witness();self.assertEqual(observer_witness(rows,self.root,cleanup),rows[0])
    def test_F05_missing_start_refused(self):
        rows,cleanup=self.witness()
        with self.assertRaisesRegex(ValueError,'F05'):observer_witness(rows[1:],self.root,cleanup)
    def test_F05_sequence_gap_refused(self):
        rows,cleanup=self.witness();rows[2]['seq']+=1
        with self.assertRaisesRegex(ValueError,'F05'):observer_witness(rows,self.root,cleanup)
    def test_F05_wrong_source_hash_refused(self):
        rows,cleanup=self.witness();rows[0]['scanner_source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'F05'):observer_witness(rows,self.root,cleanup)
    def test_F05_missing_stop_refused(self):
        rows,cleanup=self.witness()
        with self.assertRaisesRegex(ValueError,'F05'):observer_witness(rows[:-1],self.root,cleanup)
    def test_F05_zero_without_scanner_positive_refused(self):
        rows,cleanup=self.witness();rows=[rows[0],rows[1],rows[-2],rows[-1]]
        for i,row in enumerate(rows):row['seq']=i+1
        with self.assertRaisesRegex(ValueError,'F05'):observer_witness(rows,self.root,cleanup)
    def test_F01_scalar_column_disagrees_with_JSON(self):
        with self.assertRaisesRegex(ValueError,'F01'):scalar_columns({'token_count':999,'full_result_json':json.dumps({'token_count':1})},{'token_count':1})


if __name__=='__main__':unittest.main()
