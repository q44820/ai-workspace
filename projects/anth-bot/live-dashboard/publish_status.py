"""只读看板发布器；不导入交易代码、不签名、不调用交易/控制接口。
服务器凭据从既有私密文件读取，仅使用PUBLIC_PATH/PORT和状态推送URL/TOKEN。
标准输出仅状态码；永不输出URL、能力路径、响应正文或异常详情。
"""
import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

LEGACY_NOTICE = '双边持仓已核实归零；执行安全修复已部署，策略保持停止，待人工确认'
HISTORY_NOTICE = '历史提示已解除：此前安全修复后等待确认；当前状态以实时运行栏为准。'


def presentation(snapshot):
    data = dict(snapshot)
    if (data.get('mode') == 'live' and data.get('liveEnabled') is True
            and data.get('dryRun') is False and data.get('venuesReady') is True
            and data.get('stale') is False and not data.get('recoveryExposure')
            and not data.get('exit_pending') and data.get('alert') == LEGACY_NOTICE):
        data['alert'] = None
        data['statusNotice'] = HISTORY_NOTICE
    data['statusPublisher'] = 'readonly-sidecar-v1'
    data['heartbeatSeconds'] = 30
    return data


def load_config(path):
    allowed = {'PUBLIC_PATH', 'PUBLIC_PORT', 'STATUS_PUSH_URL', 'STATUS_PUSH_TOKEN'}
    values = {}
    for line in Path(path).read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.strip() in allowed:
            values[key.strip()] = value.strip().strip('\"\x27')
    assert all(values.get(k) for k in allowed), 'Missing publisher config'
    url = urllib.parse.urlsplit(values['STATUS_PUSH_URL'])
    assert url.scheme == 'https' and url.hostname == 'anth-live.vercel.app' and url.path == '/api/ingest', 'Unexpected publish target'
    assert values['PUBLIC_PORT'].isdigit(), 'Invalid local port'
    return values


def publish_once(config, opener=None):
    # Local direct reads must not use a network proxy. No control token is loaded.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    capability = urllib.parse.quote(config['PUBLIC_PATH'].strip('/'), safe='')
    local = 'http://127.0.0.1:' + config['PUBLIC_PORT'] + '/' + capability
    with opener.open(local, timeout=8) as response:
        data = json.load(response)
    source_time = data.get('ts')
    assert (data.get('ok') is True and isinstance(source_time, (int, float))
            and not isinstance(source_time, bool) and math.isfinite(source_time)
            and -10000 <= time.time() * 1000 - source_time <= 90000), 'Stale source'
    # Preserve source timestamp. Do not synthesize freshness from ingestion time.
    body = json.dumps(presentation(data), ensure_ascii=False).encode()
    request = urllib.request.Request(config['STATUS_PUSH_URL'], data=body,
        headers={'Authorization': 'Bearer ' + config['STATUS_PUSH_TOKEN'],
                 'Content-Type': 'application/json'}, method='POST')
    with opener.open(request, timeout=12) as response:
        result = json.load(response)
    assert result.get('ok') is True, 'Publish rejected'
    return not result.get('skipped', False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', default='/opt/anth-bot/.env')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config = load_config(args.env_file)
    while True:
        try:
            written = publish_once(config)
            print('STATUS_WRITTEN' if written else 'STATUS_COALESCED', flush=True)
        except Exception:
            print('STATUS_PUBLISH_FAILED', flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(30)


if __name__ == '__main__':
    main()
