"""Small per-source scheduler; no network I/O while holding a view lock."""
import copy
import json
import random
import threading
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime


def utcnow():
    return datetime.now(timezone.utc)


def zoned(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


class ScheduledCache:
    def setup(self, clock=utcnow, interval=30, jitter=None):
        self.clock, self.interval = clock, interval
        self.jitter = jitter or (lambda: random.uniform(.8, 1.2))
        self.refresh_lock = threading.Lock()
        # Old builds persisted minute-scale backoff. Start this process due,
        # while preserving explicit upstream Retry-After restrictions.
        self.state['next_attempt'] = self.state.get('retry_after_until') or ''

    def status(self, state):
        actual = state.get('actual_interval_s', self.interval)
        threshold = 3 * actual
        success = zoned(state.get('last_success'))
        quote = zoned(state.get('quote_time'))
        ages = [(self.clock()-t).total_seconds() for t in (success, quote) if t]
        return dict(target_interval_s=self.interval, actual_interval_s=actual,
                    stale_after_s=threshold, next_attempt=state.get('next_attempt'),
                    retry_after_until=state.get('retry_after_until'),
                    quote_time=state.get('quote_time'),
                    stale=bool(state.get('error')) or not success or max(ages)>threshold)

    def due(self):
        with self.lock:
            deadline = zoned(self.state.get('next_attempt'))
            return deadline is None or self.clock() >= deadline

    def run_refresh(self, collect, apply):
        with self.lock:
            restriction = zoned(self.state.get('retry_after_until'))
            if restriction and self.clock() < restriction:
                return self.view()
        if not self.refresh_lock.acquire(False):
            return self.view()
        try:
            with self.lock:
                self.state['last_attempt'] = self.clock().isoformat()
            try:
                result = collect()
                updates = apply(result)
                updates.update(last_success=self.clock().isoformat(), error=None, failures=0,
                               retry_after_until=None)
            except Exception as exc:
                with self.lock:
                    failures = self.state.get('failures', 0)+1
                updates = dict(error=f'{type(exc).__name__}: {exc}', failures=failures)
                retry = getattr(exc, 'headers', None)
                retry = retry.get('Retry-After') if retry else None
                if retry:
                    try:
                        seconds = max(0, float(retry))
                    except ValueError:
                        try: seconds = max(0, (parsedate_to_datetime(retry)-self.clock()).total_seconds())
                        except (ValueError, TypeError): seconds = 0
                    updates['retry_after_until'] = (self.clock()+timedelta(seconds=seconds)).isoformat()
            with self.lock:
                self.state.update(updates)
                failures = self.state.get('failures', 0)
                delay = min(600, 30*2**min(failures-1, 5)) if failures else self.interval
                delay *= self.jitter()
                retry = zoned(self.state.get('retry_after_until'))
                if retry: delay = max(delay, (retry-self.clock()).total_seconds())
                self.state.update(actual_interval_s=delay,
                    next_attempt=(self.clock()+timedelta(seconds=delay)).isoformat())
                saved = copy.deepcopy(self.state)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                temp.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
                temp.replace(self.path)
            except Exception as exc:
                with self.lock: self.state['error'] = f'缓存写入失败: {exc}'
            return self.view()
        finally:
            self.refresh_lock.release()


class Scheduler:
    def __init__(self, caches):
        self.caches = list(caches)
        self.threads = {}

    def tick(self):
        for cache in self.caches:
            old = self.threads.get(cache)
            if (old is None or not old.is_alive()) and cache.due():
                def work(c=cache):
                    try: c.refresh()
                    except Exception as exc:
                        with c.lock: c.state['error'] = f'工作线程异常: {exc}'
                thread = threading.Thread(target=work, daemon=True)
                self.threads[cache] = thread
                thread.start()

    def run(self, stop):
        while not stop.is_set():
            self.tick()
            stop.wait(.25)
