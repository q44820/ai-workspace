"""Offline harness: no network, no credentials, temporary state only."""
import asyncio
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent

def deny(*args, **kwargs):
    raise AssertionError('NETWORK_FORBIDDEN_IN_OFFLINE_TEST')

def main():
    with tempfile.TemporaryDirectory(prefix='anth-archive-test-') as tmp:
        env = dict(os.environ, ANTH_RUNTIME=tmp, STATE_FILE=str(Path(tmp)/'state.json'), LOG_FILE=str(Path(tmp)/'bot.log'), LIVE_ENABLED='1', DRY_RUN='0', HL_ACCOUNT_ADDRESS='ignored-test-marker', HL_API_PRIVATE_KEY='ignored-test-marker')
        p = subprocess.run([sys.executable, str(HERE/'bot.py')], env=env, capture_output=True, text=True)
        assert p.returncode != 0 and '公开归档副本禁止' in p.stderr
        with patch.dict(os.environ, env), patch.object(socket.socket, 'connect', deny), patch.object(socket, 'create_connection', deny), patch.object(socket, 'getaddrinfo', deny):
            import bot
            assert bot.DRY_RUN and not bot.LIVE_ENABLED
            assert bot.ENV('HL_ACCOUNT_ADDRESS', '') == ''
            assert bot.ENV('HL_API_PRIVATE_KEY', '') == ''
            assert not bot.PUBLIC_PATH and not bot.STATUS_PUSH_URL
            try:
                asyncio.run(bot.main())
            except RuntimeError as e:
                assert str(e) == 'PUBLIC_ARCHIVE_SERVICE_START_DISABLED'
            else:
                raise AssertionError('main must be disabled')
            bot.DRY_RUN = False
            try:
                asyncio.run(bot.Venues().init())
            except RuntimeError as e:
                assert str(e) == 'PUBLIC_ARCHIVE_LIVE_EXECUTION_DISABLED'
            else:
                raise AssertionError('real client init must be disabled')
            bot.DRY_RUN = True
            runpy.run_path(str(HERE/'simtest.py'), run_name='__main__')
    print('ARCHIVE_SAFETY_AND_SIMULATION_OK')

if __name__ == '__main__':
    main()
