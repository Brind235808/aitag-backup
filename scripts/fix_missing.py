"""Verify every planned archive exists on GitHub fully uploaded; re-upload gaps."""
import json
import os
import sys

REPO = r"F:\aitag_github_upload\repo"
sys.path.insert(0, os.path.join(REPO, "scripts"))
import pack  # noqa: E402
import uploader  # noqa: E402

PLAN = r"F:\aitag_github_upload\local\plan.json"


def log(msg):
    print(msg, flush=True)


def main():
    plan = json.load(open(PLAN, encoding="utf-8"))
    rid, url = uploader.ensure_release()
    assets = uploader.list_assets(rid)
    log(f"release {rid} has {len(assets)} assets; plan {len(plan)}")
    missing, bad, ok = [], [], 0
    for e in plan:
        p = pack.archive_path(e)
        a = assets.get(e["name"])
        if not os.path.isfile(p):
            missing.append(e)
            continue
        size = os.path.getsize(p)
        if not a or a.get("size") != size or a.get("state") != "uploaded":
            bad.append(e)
        else:
            ok += 1
    log(f"ok={ok} missing_local={len(missing)} needs_upload={len(bad)}")
    for e in bad:
        try:
            up = uploader.upload(pack.archive_path(e), e["name"], rid, existing=assets, retries=8)
            log(f"fixed {e['name']} {up.get('size')} {up.get('state', '')}")
        except Exception as ex:
            log(f"FIX FAILED {e['name']}: {ex}")
    for e in missing:
        try:
            meta = pack.pack(e)
            up = uploader.upload(pack.archive_path(e), e["name"], rid, existing=assets, retries=8)
            log(f"packed+fixed {e['name']} {meta['archive_bytes']}")
        except Exception as ex:
            log(f"PACK/FIX FAILED {e['name']}: {ex}")


if __name__ == "__main__":
    main()
