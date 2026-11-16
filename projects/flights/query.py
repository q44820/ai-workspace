"""Read-only flight research entry. No payment, no account login."""
import argparse
import importlib.util
from datetime import date
from pathlib import Path

def validate(dep, arr, day, adults):
    if not (len(dep)==len(arr)==3 and dep.isalpha() and arr.isalpha()):
        raise ValueError('机场代码需为三位字母')
    if dep.upper()==arr.upper():
        raise ValueError('出发和到达不能相同')
    if date.fromisoformat(day)<date.today():
        raise ValueError('不允许使用过期日期')
    if not 1<=adults<=9:
        raise ValueError('成人数量需为1至9')

def main():
    p=argparse.ArgumentParser(description='机票研究查询；输出不是含税/足量座位的已核验报价')
    p.add_argument('--dep',required=True);p.add_argument('--arr',required=True)
    p.add_argument('--date',required=True);p.add_argument('--adults',type=int,default=2)
    p.add_argument('--search',action='store_true',help='显式发起浏览器公开搜索')
    a=p.parse_args();validate(a.dep,a.arr,a.date,a.adults)
    if not a.search:
        print('参数校验通过；未发起查询。加 --search 才打开公开搜索。');return
    from playwright.sync_api import sync_playwright
    # Only Google research function; legacy route/date loops are never called.
    file=Path(__file__).parent/'legacy'/'flight_search_v2.py'
    spec=importlib.util.spec_from_file_location('flight_legacy',file)
    assert spec is not None and spec.loader is not None
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            result=m.search_google_flights(page,a.dep.upper(),a.arr.upper(),a.date)
            print({'research_result':result,'requested_adults':a.adults,'seat_count_verified':False,'tax_included_verified':False,'warning':'页面正则提价可能错配，不是订单报价；需人工核验同航班足量座位与含税总价'})
        finally:browser.close()

if __name__=='__main__':main()
