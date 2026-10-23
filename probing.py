"""Cancellable, two-worker node refreshes owned by a panel opening."""

import concurrent.futures
import fcntl
import json
import os
import threading
import time

import inventory


def save_regions(manager, updates):
    if not updates:
        return
    manager.private_dir(manager.DATA)
    with (manager.DATA / "regions.lock").open("a") as stream:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        regions = manager.read_json(manager.DATA / "regions.json", {})
        regions.update(updates)
        manager.atomic(manager.DATA / "regions.json", json.dumps(regions))


class PanelProbes:
    def __init__(self, manager, emit):
        self.manager = manager
        self.emit = emit
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.cancel = threading.Event()
        self.futures = []
        self.visible = False
        self.session = None
        self.lock = threading.Lock()
        self.completed = 0
        self.total = 0

    def set_visible(self, visible, session):
        if self.visible == visible and self.session == session:
            return
        self.cancel.set()
        for future in self.futures:
            future.cancel()
        self.futures = []
        self.visible = visible
        self.session = session
        if not visible:
            return
        cancel = self.cancel = threading.Event()
        jobs = {}
        if self.manager.CORE.is_file():
            try:
                items = self.manager.state().get("profiles", [])
            except Exception:
                items = []
            for item in items:
                try:
                    _, nodes, _ = inventory.read_profile(self.manager, item)
                except Exception:
                    continue
                for node in nodes:
                    key = inventory.fingerprint(node)
                    jobs.setdefault(key, node)
        with self.lock:
            self.completed = 0
            self.total = len(jobs)
        self.emit({"event": "probe", "latencySession": session, "delays": {},
                   "regions": {}, "completed": 0, "total": len(jobs)})
        for key, job in jobs.items():
            self.futures.append(self.executor.submit(self._run, key, job, session, cancel))

    def _run(self, fingerprint, job, session, cancel):
        if cancel.is_set():
            return
        def publish_delay(delay):
            with self.lock:
                if cancel.is_set() or self.cancel is not cancel:
                    return
                self.emit({"event": "probe", "latencySession": session,
                           "delays": {fingerprint: delay}, "regions": {},
                           "completed": self.completed, "total": self.total})
        try:
            result = inventory.probe_node(self.manager, job, cancel, publish_delay)
        except inventory.ProbeCancelled:
            return
        except Exception:
            result = {"regionCode": "", "delay": 0}
        if cancel.is_set():
            return
        code = result.get("regionCode", "")
        if code:
            try:
                save_regions(self.manager, {fingerprint: {"code": code, "updated": time.time()}})
            except Exception:
                pass
        with self.lock:
            if cancel.is_set() or self.cancel is not cancel:
                return
            self.completed += 1
            self.emit({"event": "probe", "latencySession": session,
                       "delays": {fingerprint: result["delay"]},
                       "regions": {fingerprint: code} if code else {},
                       "completed": self.completed, "total": self.total})

    def close(self):
        self.cancel.set()
        for future in self.futures:
            future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
