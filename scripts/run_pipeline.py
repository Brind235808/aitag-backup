"""Pack + upload the aitag backup to GitHub Releases.

Phase order: state -> index -> manifest -> meta -> images (encrypted).
Resumable: existing archives are reused, uploaded assets are skipped by size.
"""
import csv
import json
import os
import queue
import subprocess
import threading
import time

import pack as packer
import uploader

WORK = os.environ.get("AITAG_WORK", r"F:\aitag_github_upload")
LOCAL = os.path.join(WORK, "local")
REPO = os.path.join(WORK, "repo")
VOL_CSV = os.path.join(REPO, "mapping", "volumes.csv")
PLAN = os.path.join(LOCAL, "plan.json")
CONCURRENCY = int(os.environ.get("AITAG_UPLOAD_CONC", "8"))
PACK_AHEAD = int(os.environ.get("AITAG_PACK_AHEAD", "16"))
PACK_WORKERS = int(os.environ.get("AITAG_PACK_WORKERS", "4"))
PROXY_URL = os.environ.get("AITAG_PROXY", "")
PROXY_COUNT = int(os.environ.get("AITAG_PROXY_COUNT", "0"))


def load_password():
    with open(os.path.join(LOCAL, "secret.env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("AITAG_PW="):
                return line.split("=", 1)[1]
    raise SystemExit("AITAG_PW missing in local/secret.env")


def log(msg):
    print(time.strftime("[%H:%M:%S]") + " " + str(msg), flush=True)


def git(*args, check=True):
    p = subprocess.run(["git"] + list(args), cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} rc={p.returncode}: {p.stderr[-500:]}")
    return p


def load_volumes():
    rows = {}
    order = []
    with open(VOL_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["archive"]] = row
            order.append(row["archive"])
    return rows, order


def save_volumes(rows, order):
    tmp = VOL_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(rows.values())).keys()))
        w.writeheader()
        for name in order:
            w.writerow(rows[name])
    os.replace(tmp, VOL_CSV)


def password_not_in_repo():
    p = git("grep", "--cached", "-I", "-l", load_password(), check=False)
    if p.returncode == 0 and p.stdout.strip():
        raise RuntimeError("password found in repo: " + p.stdout.strip())


class Heartbeat(threading.Thread):
    def __init__(self, total_bytes, total_assets):
        super().__init__(daemon=True)
        self.total_bytes = total_bytes
        self.total_assets = total_assets
        self.done_bytes = 0
        self.done_assets = 0
        self.current = []
        self.status = "starting"
        self.started = time.time()
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "assets_done": self.done_assets,
                "assets_total": self.total_assets,
                "bytes_done": self.done_bytes,
                "bytes_total": self.total_bytes,
                "current": list(self.current),
                "elapsed_s": round(time.time() - self.started, 1),
                "rate_mb_s": round(self.done_bytes / max(time.time() - self.started, 1) / 2**20, 3),
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

    def run(self):
        while not self.stop_evt.wait(10):
            with open(os.path.join(LOCAL, "heartbeat.json"), "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=1)


def main():
    os.makedirs(LOCAL, exist_ok=True)
    if not os.path.isfile(PLAN):
        raise SystemExit("plan.json missing; run build_mapping.py first")
    with open(PLAN, encoding="utf-8") as f:
        plan = json.load(f)
    order = {"state": 0, "index": 1, "manifest": 2, "images": 3, "meta": 4}
    plan.sort(key=lambda e: (order[e["kind"]], e["name"]))
    with open(os.path.join(LOCAL, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)

    volumes, vol_order = load_volumes()
    total_bytes = sum(int(volumes[e["name"]]["source_bytes"]) for e in plan if e["name"] in volumes)
    hb = Heartbeat(total_bytes, len(plan))
    hb.start()

    # repo needs at least one commit before a release can be tagged
    git("add", "-A")
    password_not_in_repo()
    p = git("commit", "-m", "aitag backup: mapping tables, archive plan, upload scripts", check=False)
    if p.returncode != 0 and "nothing to commit" not in (p.stdout + p.stderr):
        raise RuntimeError("initial commit failed: " + p.stderr[-500:])
    p = git("push", "origin", "HEAD:main", check=False)
    if p.returncode != 0:
        log("initial push failed (continuing): " + p.stderr[-300:])
    else:
        log("git initial push ok")

    release_id, release_url = uploader.ensure_release()
    existing = uploader.list_assets(release_id)
    log(f"release {release_id} {release_url} existing_assets={len(existing)} plan_assets={len(plan)}")

    q = queue.Queue(maxsize=PACK_AHEAD)
    stop_evt = threading.Event()
    results = {}
    failures = {}
    results_lock = threading.Lock()
    vol_dirty = [0]
    fail_streak = [0]

    def record(name, meta, up):
        with results_lock:
            results[name] = up
            row = volumes.get(name)
            if row:
                row["sha256"] = up.get("digest", "")
                row["github_digest"] = up.get("digest", "")
                vol_dirty[0] += 1
            with hb.lock:
                hb.done_assets += 1
                hb.done_bytes += int(meta["archive_bytes"])
            if vol_dirty[0] >= 5:
                save_volumes(volumes, vol_order)
                vol_dirty[0] = 0

    def upload_worker(idx):
        while True:
            item = q.get()
            if item is None:
                q.task_done()
                return
            entry, meta = item
            with hb.lock:
                hb.current.append(entry["name"])
            try:
                proxy = PROXY_URL if (PROXY_URL and idx < PROXY_COUNT) else None
                up = uploader.upload(packer.archive_path(entry), entry["name"], release_id,
                                     existing=existing, retries=8, proxy=proxy)
                record(entry["name"], meta, up)
                with results_lock:
                    fail_streak[0] = 0
            except Exception as e:
                log(f"upload failed {entry['name']}: {e}")
                with results_lock:
                    failures[entry["name"]] = str(e)
                    fail_streak[0] += 1
                    if fail_streak[0] >= 5:
                        log("5 consecutive upload failures, stopping pipeline")
                        stop_evt.set()
            finally:
                with hb.lock:
                    if entry["name"] in hb.current:
                        hb.current.remove(entry["name"])
                q.task_done()

    workers = [threading.Thread(target=upload_worker, args=(i,), daemon=True) for i in range(CONCURRENCY)]
    for w in workers:
        w.start()

    pack_q = queue.Queue()
    for i, entry in enumerate(plan, 1):
        pack_q.put((i, entry))
    for _ in range(PACK_WORKERS):
        pack_q.put(None)

    def pack_worker():
        while not stop_evt.is_set():
            try:
                item = pack_q.get(timeout=2)
            except queue.Empty:
                continue
            if item is None:
                return
            i, entry = item
            hb.status = f"packing {entry['kind']}"
            try:
                meta = packer.pack(entry)
            except Exception as e:
                log(f"pack failed {entry['name']}: {e}")
                continue
            log(f"queue {i}/{len(plan)} {entry['name']} {meta['archive_bytes']/2**30:.2f}GB")
            while not stop_evt.is_set():
                try:
                    q.put((entry, meta), timeout=10)
                    break
                except queue.Full:
                    continue

    packers = [threading.Thread(target=pack_worker, daemon=True) for _ in range(PACK_WORKERS)]
    for w in packers:
        w.start()
    for w in packers:
        w.join()

    for _ in workers:
        while True:
            try:
                q.put(None, timeout=10)
                break
            except queue.Full:
                continue
    for w in workers:
        w.join()
    hb.stop_evt.set()
    hb.status = "done"

    try:
        save_volumes(volumes, vol_order)
        git("add", "mapping/volumes.csv")
        git("commit", "-m", "final archive digest table")
        git("push", "origin", "HEAD:main")
    except Exception as e:
        log(f"final git push failed: {e}")

    with open(os.path.join(LOCAL, "DONE.json"), "w", encoding="utf-8") as f:
        json.dump({"finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "assets": len(results),
                   "bytes": hb.done_bytes, "failed": failures,
                   "stopped_early": stop_evt.is_set()}, f, ensure_ascii=False)
    log(f"pipeline finished: assets={len(results)} failed={len(failures)} stopped_early={stop_evt.is_set()}")


if __name__ == "__main__":
    main()
