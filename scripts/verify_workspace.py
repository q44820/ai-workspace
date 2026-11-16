"""Offline checks only: no browsers, account APIs, notifications or deployments."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parent.parent

def run(args,cwd):
    subprocess.run(args,cwd=cwd,check=True)

def main():
    files=list((ROOT/'projects').rglob('*.py'))
    for p in files:ast.parse(p.read_text(),filename=str(p))
    print('python_syntax_files='+str(len(files)),flush=True)
    run([sys.executable,'-m','unittest','discover','-s','tests','-v'],ROOT)
    run([sys.executable,'test_dashboard.py'],ROOT/'projects/program-dashboard')
    run([sys.executable,'test_hub.py'],ROOT/'projects/arb-hub')
    run([sys.executable,'test_offline.py'],ROOT/'projects/anth-bot')
    run([sys.executable,'-m','unittest','test_monitors','-v'],ROOT/'projects/binance-monitors')
    run([sys.executable,'-m','unittest','discover','-p','test_*.py','-v'],ROOT/'projects/basis-radar')
    run([sys.executable,'test_bncb_lp_monitor.py'],ROOT/'projects/lp-monitor/scripts')
    run([sys.executable,'-m','unittest','test_lp_wallet_watchdog','-v'],ROOT/'projects/lp-monitor/scripts')
    p=ROOT/'projects/flights/query.py';spec=importlib.util.spec_from_file_location('flight_query',p)
    assert spec and spec.loader
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    future=(date.today()+timedelta(days=30)).isoformat()
    m.validate('PEK','SHA',future,2)
    for values in [('PEK','PEK',future,2),('P','SHA',future,2),('PEK','SHA','2000-01-01',2),('PEK','SHA',future,0)]:
        try:m.validate(*values)
        except ValueError:pass
        else:raise AssertionError('invalid flight parameters accepted')
    run([sys.executable,str(p),'--dep','PEK','--arr','SHA','--date',future],ROOT)
    for slug in ['basis-radar','lp-monitor','polymarket-research','weather-research','prediction-cross','flights','binance-monitors','arb-hub','anth-bot','program-dashboard']:
        for name in ['README.md','WORKFLOW_工作流程.md','STATUS_当前状态.md']:assert (ROOT/'projects'/slug/name).is_file()
    migration=json.loads((ROOT/'docs/MIGRATION.json').read_text())
    import hashlib
    for row in migration['files']:
        assert hashlib.sha256((ROOT/row['target']).read_bytes()).hexdigest()==row['target_sha256'],row['target']
    for slug in ['binance-monitors','arb-hub','anth-bot']:
        directory=ROOT/'projects'/slug
        manifest=json.loads((directory/'MIGRATION_迁移来源.json').read_text())
        for row in manifest['files']:
            target=ROOT/row['target'] if slug=='binance-monitors' else directory/row['target']
            assert hashlib.sha256(target.read_bytes()).hexdigest()==row['target_sha256'],str(target)
    print('PASS offline checks; live services and profitability NOT verified',flush=True)

if __name__=='__main__':main()
