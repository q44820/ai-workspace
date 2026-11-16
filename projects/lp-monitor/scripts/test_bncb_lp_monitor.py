import importlib.util,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent.parent;spec=importlib.util.spec_from_file_location('bncb',Path(__file__).resolve().parent/'bncb_lp_monitor.py');assert spec and spec.loader;m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cases=[(110,100,120,'safe'),(102,100,120,'near_lower'),(101,100,120,'near_lower'),(118,100,120,'near_upper'),(117.6,100,120,'near_upper'),(99,100,120,'below'),(120,100,120,'above'),(121,100,120,'above'),(100,100,120,'near_lower'),(101,100,102,'near_both')]
for p,l,u,status in cases:assert m.classify(p,l,u)['status']==status,(p,status,m.classify(p,l,u))
try:m.classify(1,2,1);raise AssertionError('expected bad range')
except ValueError:pass
print('PASS 11 boundary tests')
