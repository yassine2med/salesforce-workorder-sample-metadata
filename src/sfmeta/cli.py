"""Command line entry for the Salesforce metadata checker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checker import check_project


def main(argv: list[str] | None = None) -> int:
    """Return 0 when the project is clean, 1 when issues exist, 2 when the folder is missing."""
    parser = argparse.ArgumentParser(
        prog="sfmeta",
        description="Check Salesforce DX metadata files offline. Nothing is deployed and no org is contacted.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate the project and print every issue")
    check.add_argument("path", nargs="?", default=".", help="project folder holding sfdx-project.json")
    inv = sub.add_parser("inventory", help="list objects and custom fields found")
    inv.add_argument("path", nargs="?", default=".")
    args = parser.parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print(f"Folder not found: {root}", file=sys.stderr)
        return 2
    issues, inventory = check_project(root)
    if args.command == "inventory":
        for obj, fields in inventory.get("objects", {}).items():
            print(f"{obj}: {len(fields)} custom field(s): {', '.join(fields)}")
        print(f"{inventory.get('files', 0)} metadata files, {inventory.get('flows', 0)} flow(s)")
        return 1 if issues else 0
    for issue in issues:
        print(issue)
    print(f"{inventory.get('files', 0)} metadata files checked, {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
