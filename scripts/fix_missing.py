"""Verify every planned archive exists on GitHub fully uploaded; re-upload gaps."""
import concurrent.futures as cf
import json
import os
import sys
import threading
import time

REPO = r"F:\aitag_github_upload\repo"
sys.path.insert(0, os.path.join(REPO, "scripts"))
import pack  # noqa: E402
import uploader  # noqa: E402

PLAN = r"F:\aitag_github_upload\local\plan.json"
LOCK = threading.Lock()


def log(msg):
    with LOCK:
        print(time.strftime("[%H:%M:%S] ") + str(msg), flush=True)


def main():
    plan = json.load(open(PLAN, encoding="utf-8"))
    rid, url = uploader.ensure_release()
    assets = uploader.list_assets(rid)
    log(f"release {rid} has {len(assets)} assets; plan {len(plan)}")

    def status(e):
        p = pack.archive_path(e)
        a = assets.get(e["name"])
        if not os.path.isfile(p):
            return "pack"
        size = os.path.getsize(p)
        if not a or a.get("size") != size or a.get("state") != "uploaded":
            return "upload"
        return "ok"

    states = [(e, status(e)) for e in plan]
    counts = {k: sum(1 for _, s in states if s == k) for k in ("ok", "pack", "upload")}
    log(f"ok={counts['ok']} need_pack={counts['pack']} need_upload={counts['upload']}")

    def pack_one(e):
        try:
            m = pack.pack(e)
            log(f"packed {e['name']} {m['archive_bytes']}")
            return True
        except Exception as ex:
            log(f"PACK FAILED {e['name']}: {ex}")
            return False

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(pack_one, [e for e, s in states if s == "pack"]))

    todo = [e for e, s in states if s in ("pack", "upload")]

    def upload_one(e):
        try:
            up = uploader.upload(pack.archive_path(e), e["name"], rid, existing=assets, retries=8)
            log(f"uploaded {e['name']} {up.get('size')} {up.get('state', '')}")
            return True
        except Exception as ex:
            log(f"UPLOAD FAILED {e['name']}: {ex}")
            return False

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(upload_one, todo))
    log(f"fix done: {sum(1 for r in res if r)}/{len(res)} repaired")


if __name__ == "__main__":
    main()