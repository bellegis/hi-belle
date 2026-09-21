#!/usr/bin/env python3
"""
Lists the objects in an S3-compatible bucket (Wasabi endpoint by default) and writes songs.json.

Requirements:
    pip install boto3        (not needed with --from-file)

Credentials, as environment variables:
    WASABI_ACCESS_KEY and WASABI_SECRET_KEY (or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)

Usage:
    python3 build_index.py --bucket BUCKET --region us-east-1
    python3 build_index.py --bucket BUCKET --region us-east-1 --from-file keys.txt

--from-file reads object keys, one per line (for example the output of `rclone lsf -R --files-only`).

Every object with an audio extension, or with no extension, is written to songs.json.
Everything else is written to left_out.txt with the reason.

Outputs (next to --out):
    songs.json        index loaded by the web page
    left_out.txt      objects not in the index, with reasons
    sample_keys.txt   150 random and 100 deepest paths from the index

Options:
    --include-all-files   index every object regardless of extension
    --drop-mac-junk       omit files named ._* (macOS resource-fork files)
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter

# Extensions treated as audio files.
AUDIO_EXT = {
    "mp3", "m4a", "aac", "flac", "wav", "aif", "aiff", "aifc", "ogg", "oga", "opus", "wma", "ape",
    "mpc", "wv", "mka", "m4b", "m4p", "amr", "mid", "midi", "mp2", "mp1", "ac3", "dts", "caf",
    "alac", "au", "snd", "ra", "tta", "dsf", "dff", "webm", "3gp", "mp4", "wave", "spx", "gsm",
}
# Extensions most browsers cannot decode (used only in the summary).
CHROME_CANT_PLAY = {"wma", "ape", "mpc", "wv", "aif", "aiff", "aifc", "mka", "ra", "tta", "dsf", "dff", "ac3", "dts", "caf", "mid", "midi"}


def list_bucket(bucket, region, endpoint, prefix):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        sys.exit("boto3 is not installed. Run:  python3 -m pip install boto3   (or use --from-file)")

    kw = {}
    access = os.environ.get("WASABI_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("WASABI_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access and secret:
        kw = dict(aws_access_key_id=access, aws_secret_access_key=secret)
    else:
        print("No WASABI_ACCESS_KEY / WASABI_SECRET_KEY found; trying default AWS credentials.", file=sys.stderr)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(retries={"max_attempts": 10, "mode": "standard"}),
        **kw,
    )
    keys = []
    started = time.time()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
        print(f"\r  listed {len(keys):,} objects ({time.time() - started:.0f}s)...", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return keys


def read_keys_file(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\r\n") for line in f if line.strip()]


def ext_of(key):
    base = key.rsplit("/", 1)[-1]
    i = base.rfind(".")
    return base[i + 1:].lower() if i > 0 else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="us-east-1", help="Wasabi region, e.g. us-east-1, us-east-2, us-central-1, us-west-1, eu-central-1")
    ap.add_argument("--endpoint", help="Override the endpoint (default https://s3.<region>.wasabisys.com)")
    ap.add_argument("--prefix", default="", help="Only index keys under this prefix")
    ap.add_argument("--base-url", help="Override the public URL the player uses for audio")
    ap.add_argument("--from-file", help="Read keys (one per line) from a text file instead of listing the bucket")
    ap.add_argument("--include-all-files", action="store_true", help="Index every object, whatever its extension")
    ap.add_argument("--drop-mac-junk", action="store_true", help='Omit files named ._* (macOS resource-fork files)')
    ap.add_argument("--out", default="songs.json")
    args = ap.parse_args()

    endpoint = args.endpoint or f"https://s3.{args.region}.wasabisys.com"
    base = args.base_url or f"{endpoint}/{args.bucket}/"
    if not base.endswith("/"):
        base += "/"

    if args.from_file:
        all_keys = read_keys_file(args.from_file)
        print(f"Read {len(all_keys):,} keys from {args.from_file}")
    else:
        print(f"Listing {args.bucket} on {endpoint} ...")
        all_keys = list_bucket(args.bucket, args.region, endpoint, args.prefix)

    kept, left_out = [], []          # left_out: (key, reason)
    for key in all_keys:
        base_name = key.rsplit("/", 1)[-1]
        if key.endswith("/"):
            left_out.append((key, "empty folder marker (not a file)"))
        elif args.drop_mac_junk and base_name.startswith("._"):
            left_out.append((key, "Mac stub file (--drop-mac-junk)"))
        elif args.include_all_files or ext_of(key) in AUDIO_EXT:
            kept.append(key)
        elif ext_of(key) == "" and not base_name.startswith("."):
            kept.append(key)         # no extension: included
        elif ext_of(key) == "":
            left_out.append((key, "hidden system file with no extension (e.g. .DS_Store)"))
        else:
            left_out.append((key, f"not an audio file (.{ext_of(key)})"))
    kept = sorted(set(kept))
    assert len(kept) + len(left_out) >= len(set(all_keys)) - 0, "accounting error"

    out_dir = os.path.dirname(os.path.abspath(args.out))

    # ---- index ----
    payload = {"v": 1, "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "base": base, "count": len(kept), "keys": kept}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_mb = os.path.getsize(args.out) / 1e6

    # ---- objects not included ----
    left_path = os.path.join(out_dir, "left_out.txt")
    with open(left_path, "w", encoding="utf-8") as f:
        f.write(f"# {len(left_out):,} objects NOT in songs.json (everything else is)\n")
        for k, why in sorted(left_out):
            f.write(f"{k}\t{why}\n")

    # ---- sample of paths ----
    rnd = random.Random(1)
    random_part = rnd.sample(kept, min(150, len(kept)))
    deepest = sorted(kept, key=lambda k: (-k.count("/"), k))[:100]
    sample_path = os.path.join(out_dir, "sample_keys.txt")
    with open(sample_path, "w", encoding="utf-8") as f:
        f.write("# --- 150 random ---\n" + "\n".join(sorted(random_part)) + "\n")
        f.write("# --- 100 deepest ---\n" + "\n".join(deepest) + "\n")

    # ---- summary ----
    print("\n================ SUMMARY ================")
    print(f"Objects found in bucket:  {len(all_keys):,}")
    print(f"In songs.json:            {len(kept):,}   ({size_mb:.1f} MB; GitHub Pages compresses it to about a fifth)")
    print(f"Not included:             {len(left_out):,}   (every one is listed in left_out.txt)")
    ext_kept = Counter(ext_of(k) or "(none)" for k in kept)
    print("File types in the index:  " + ", ".join(f"{e} {c:,}" for e, c in ext_kept.most_common()))
    if left_out:
        why = Counter(w for _, w in left_out)
        print("Not included, by reason:  " + "; ".join(f"{w} x{c:,}" for w, c in why.most_common(8)))
    noext = sum(1 for k in kept if ext_of(k) == "")
    if noext:
        print(f"No file extension:        {noext:,} files (included in the index).")
    stubs = sum(1 for k in kept if k.rsplit("/", 1)[-1].startswith("._"))
    if stubs:
        print(f"Files named ._*:          {stubs:,} (included in the index; omit with --drop-mac-junk).")
    cant = sum(c for e, c in ext_kept.items() if e in CHROME_CANT_PLAY)
    if cant:
        print(f"Formats Chrome cannot decode: {cant:,} files (wma, ape, etc.), included in the index.")
    depth = Counter(min(k.count("/"), 8) for k in kept)
    print("Folder depth of songs:    " + ", ".join(f"{d} levels: {c:,}" for d, c in sorted(depth.items())))
    tops = Counter(k.split("/", 1)[0] if "/" in k else "(loose in bucket root)" for k in kept)
    print(f"Top-level folders:        {len(tops):,}   (biggest: " + ", ".join(f"{n} [{c:,}]" for n, c in tops.most_common(5)) + ")")
    print()
    print(f"Wrote {args.out}, {left_path}, {sample_path}")


if __name__ == "__main__":
    main()
