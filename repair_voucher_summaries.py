"""Repair proven untouched automatic summaries, with a consistent backup and audit.

Default is read-only. Use --apply --backup-dir PATH after reviewing the dry run.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import db
import finance


def plan_repairs(conn):
    repairs=[]
    rows=conn.execute("""SELECT v.* FROM vouchers v WHERE source_type IN
        ('reconciliation','bank_receipt','bank_statement') AND status='active'
        AND reviewed=0 AND version=1 AND NOT EXISTS
        (SELECT 1 FROM book_periods p WHERE p.client_id=v.client_id AND p.period=v.period AND p.closed=1)
        ORDER BY v.id""").fetchall()
    for stored in rows:
        voucher=dict(stored);vid=voucher['id'];cid=voucher['client_id']
        lines=[dict(r) for r in conn.execute('SELECT * FROM accounting_voucher_lines WHERE voucher_id=? ORDER BY id',(vid,))]
        if not lines or len({l['summary'] for l in lines})!=1:continue
        audits=[dict(r) for r in conn.execute("SELECT action,entity,detail FROM audit_logs WHERE entity IN ('voucher','vouchers') AND entity_id=? ORDER BY id",(str(vid),))]
        created=next((json.loads(r['detail']) for r in audits if r['action']=='create' and r['entity']=='voucher'),{})
        original=created.get('lines',[])
        def signature(line):return tuple(line.get(k,0 if k.endswith('_fen') else '') for k in ('account_code','summary','debit_fen','credit_fen'))
        if [signature(l) for l in lines]!=[signature(l) for l in original]:continue
        transaction=None
        if voucher['source_type']=='reconciliation':
            record=conn.execute('SELECT transaction_json,batch_metadata_json FROM reconciliation_records WHERE id=? AND client_id=?',(voucher['source_id'],cid)).fetchone()
            if record:
                transaction=json.loads(record['transaction_json'])
        else:
            for row in audits:
                detail=json.loads(row['detail'])
                if row['action'] in ('reconcile_statement_post','reconcile_receipt_post'):
                    transaction=detail.get('transaction')
        if not transaction:continue
        note=str(transaction.get('note') or '').strip()
        previous=lines[0]['summary']
        allowed={note} if note else {voucher['summary'],str(transaction.get('counterparty') or '').strip()}
        if previous not in allowed:continue
        changed=[{'line_id':l['id'],'before':l['summary'],'after':finance.account_category_summary(conn,cid,l)} for l in lines]
        changed=[l for l in changed if l['after'] and l['before']!=l['after']]
        if changed:repairs.append({'voucher_id':vid,'client_id':cid,'version':voucher['version'],'lines':changed})
    return repairs


def apply_repairs(conn,repairs):
    # Caller owns the write lock; re-evaluate the complete plan to reject stale edits.
    if plan_repairs(conn)!=repairs:raise RuntimeError('凭证状态已变化，请重新检查')
    for repair in repairs:
        for line in repair['lines']:
            conn.execute('UPDATE voucher_lines SET summary=? WHERE id=? AND voucher_id=?',(line['after'],line['line_id'],repair['voucher_id']))
        conn.execute('UPDATE vouchers SET version=version+1 WHERE id=?',(repair['voucher_id'],))
        db.audit(conn,None,'repair_generated_summaries','voucher',repair['voucher_id'],repair)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,default=Path(__file__).parent/'data'/'app.sqlite3')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--backup-dir',type=Path)
    args=parser.parse_args();database=args.db.resolve()
    with closing(db.connect(database) if args.apply else sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        repairs=plan_repairs(conn)
        if args.apply and repairs:
            if not args.backup_dir:parser.error('--apply requires --backup-dir')
            folder=args.backup_dir.resolve();folder.mkdir(parents=True,exist_ok=True)
            backup=folder/'before-summary-repair.sqlite3'
            if backup.exists():raise RuntimeError('备份文件已存在，不覆盖原备份')
            with closing(sqlite3.connect(backup)) as snapshot:conn.backup(snapshot)
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                apply_repairs(conn,repairs)
            (folder/'summary-repairs.json').write_text(json.dumps(repairs,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'applied':args.apply,'vouchers':len(repairs),'lines':sum(len(r['lines']) for r in repairs),'voucher_ids':[r['voucher_id'] for r in repairs]},ensure_ascii=False))


if __name__=='__main__':main()
