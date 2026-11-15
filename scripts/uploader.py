import hashlib
import json
import os
import subprocess
import time

import requests

OWNER = "Brind235808"
REPO = "aitag-backup"
TAG = "data-v1"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

_TOKEN = {"value": None, "ts": 0}


def log(msg):
    print(time.strftime("[%H:%M:%S]") + " " + str(msg), flush=True)


def token(force=False):
    if not force and _TOKEN["value"] and time.time() - _TOKEN["ts"] < 1800:
        return _TOKEN["value"]
    p = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=True,
    )
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            _TOKEN["value"] = line[len("password="):]
            _TOKEN["ts"] = time.time()
            return _TOKEN["value"]
    raise RuntimeError("no github token")


def headers(extra=None):
    h = {"Authorization": "Bearer " + token(), "Accept": "application/vnd.github+json",
         "User-Agent": "aitag-backup"}
    if extra:
        h.update(extra)
    return h


def api(method, path, payload=None, retries=4):
    body = json.dumps(payload).encode() if payload is not None else None
    extra = {"Content-Type": "application/json"} if body else None
    last = None
    for i in range(retries):
        try:
            r = requests.request(method, API + path, headers=headers(extra), data=body, timeout=90)
            if r.status_code < 500 and r.status_code != 429:
                try:
                    return r.status_code, r.json()
                except Exception:
                    return r.status_code, {}
        except Exception as e:
            last = str(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"api {method} {path} failed: {last}")


def ensure_release():
    s, d = api("GET", f"/repos/{OWNER}/{REPO}/releases/tags/{TAG}")
    if s == 200:
        return d["id"], d["html_url"]
    s, d = api("POST", f"/repos/{OWNER}/{REPO}/releases", {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": "aitag backup data v1",
        "body": "Encrypted image archives + source metadata. Password is not stored in this repository.",
        "draft": False,
        "prerelease": False,
    })
    if s not in (200, 201):
        raise RuntimeError(f"create release failed {s} {d}")
    return d["id"], d["html_url"]


def list_assets(release_id):
    out = {}
    page = 1
    while True:
        s, d = api("GET", f"/repos/{OWNER}/{REPO}/releases/{release_id}/assets?per_page=100&page={page}")
        if s != 200:
            raise RuntimeError(f"list assets {s} {d}")
        for a in d:
            out[a["name"]] = a
        if len(d) < 100:
            break
        page += 1
    return out


class HashReader:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.h = hashlib.sha256()
        self.size = os.path.getsize(path)
        self.name = os.path.basename(path)

    def __len__(self):
        return self.size

    def fileno(self):
        return self.f.fileno()

    def tell(self):
        return self.f.tell()

    def read(self, n=-1):
        b = self.f.read(n)
        if b:
            self.h.update(b)
        return b

    def close(self):
        self.f.close()


def delete_asset(asset_id):
    try:
        api("DELETE", f"/repos/{OWNER}/{REPO}/releases/assets/{asset_id}")
    except Exception as e:
        log(f"delete asset {asset_id} failed: {e}")


def upload(path, name, release_id, existing=None, retries=8):
    existing = existing or {}
    a = existing.get(name)
    size = os.path.getsize(path)
    if a and a.get("size") == size:
        return {"skipped": True, "size": size, "digest": (a.get("digest") or "").replace("sha256:", ""),
                "id": a["id"], "state": a.get("state")}
    if a:
        log(f"asset {name} exists with size {a.get('size')} != {size}, deleting")
        delete_asset(a["id"])

    for attempt in range(1, retries + 1):
        hr = HashReader(path)
        t0 = time.time()
        try:
            r = requests.post(
                f"{UPLOADS}/repos/{OWNER}/{REPO}/releases/{release_id}/assets",
                params={"name": name},
                headers=headers({"Content-Type": "application/octet-stream"}),
                data=hr,
                timeout=(30, 7200),
            )
            elapsed = max(time.time() - t0, 0.01)
            if r.status_code in (200, 201):
                d = r.json()
                digest = (d.get("digest") or "").replace("sha256:", "")
                ok = d.get("size") == size and (not digest or digest.lower() == hr.h.hexdigest())
                log(f"upload ok {name} {size/2**20:.1f}MB {size/2**20/elapsed:.2f}MB/s attempt={attempt} "
                    f"digest={'match' if digest else 'none'}")
                return {"skipped": False, "size": size, "digest": digest or hr.h.hexdigest(),
                        "id": d.get("id"), "seconds": round(elapsed, 1), "verified": ok}
            log(f"upload fail {name} http={r.status_code} attempt={attempt} body={r.text[:200]}")
            if r.status_code in (401,):
                token(force=True)
        except Exception as e:
            log(f"upload error {name} attempt={attempt}: {e}")
        finally:
            hr.close()
        time.sleep(min(60, 5 * attempt))
    raise RuntimeError(f"upload failed after {retries} attempts: {name}")
