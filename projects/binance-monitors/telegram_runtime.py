"""Environment-only notification adapter; never read OpenClaw credentials."""
import os
import re

def load_telegram_runtime():
    return os.environ.get('TELEGRAM_BOT_TOKEN', ''), os.environ.get('TELEGRAM_CHAT_ID', '')

def redact_log(message):
    message = str(message)
    for key in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'WXPUSHER_APP_TOKEN', 'WXPUSHER_UIDS'):
        value = os.environ.get(key, '')
        if value:
            message = message.replace(value, '[REDACTED]')
    message = re.sub(r'bot[0-9]{6,}:[A-Za-z0-9_-]+', 'bot[REDACTED]', message)
    message = re.sub(r'(?:AT_|UID_)[A-Za-z0-9_-]+', '[REDACTED]', message)
    # Response bodies and arbitrary exceptions can contain unknown credentials.
    if any(word in message.lower() for word in ('exception', 'failed response', '返回失败', '请求异常', '推送失败:', '重试', '尝试直连')):
        return '[通知或网络异常；详细响应已省略]'
    return message
