"""Command line: python -m meshsim <command>  (run from the sim/ directory)."""
import argparse
from pathlib import Path

from . import firmware_params, params


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meshsim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("params", help="print / export the parameter catalogue")
    p.add_argument("--md", type=Path, help="write the Markdown catalogue here")
    p.add_argument("--json", type=Path, help="write the JSON catalogue here")
    sub.add_parser("snapshot", help="re-parse firmware sources into firmware_snapshot.json")
    args = ap.parse_args(argv)

    if args.cmd == "snapshot":
        firmware_params.write_snapshot()
        print(f"wrote {firmware_params.SNAPSHOT}")
        return
    cat = params.build()
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(params.to_markdown(cat) + "\n", encoding="utf-8")
        print(f"wrote {args.md.resolve()} ({len(cat.params)} parameters)")
    if args.json:
        args.json.write_text(params.to_json(cat) + "\n", encoding="utf-8")
        print(f"wrote {args.json.resolve()}")
    if not (args.md or args.json):
        for prm in cat.params.values():
            print(f"{prm.group:3} {prm.key:40} {params._fmt(prm.value):>28} {prm.unit:10} {prm.source}")


if __name__ == "__main__":
    main()
