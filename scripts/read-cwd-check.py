#!/usr/bin/env python3
"""Approve Read/Glob/Grep when the target path is within the working directory."""

import json
import os
import sys


def main():
    ctx = json.load(sys.stdin)
    cwd = ctx.get("cwd") or ""
    tool_input = ctx.get("toolInput") or {}

    # Extract target path from various tool input formats
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("pattern")
        or ""
    )

    if not file_path or not cwd:
        print(json.dumps({"decision": "passthrough", "reason": "Missing path or cwd"}))
        return

    try:
        resolved = os.path.realpath(
            file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
        )
        cwd_resolved = os.path.realpath(cwd)

        if resolved.startswith(cwd_resolved + os.sep) or resolved == cwd_resolved:
            print(json.dumps({"decision": "approve", "reason": f"Path within workspace: {file_path}"}))
        else:
            print(json.dumps({"decision": "passthrough", "reason": f"Path outside workspace: {file_path}"}))
    except Exception:
        print(json.dumps({"decision": "passthrough", "reason": "Path resolution failed"}))


if __name__ == "__main__":
    main()
