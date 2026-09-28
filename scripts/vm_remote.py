"""SSH helper for the Astra Linux test VM (VirtualBox, offline).

Talks to the throw-away test VM with paramiko so deploy/verify steps can
run scripted (password auth; no keys on an air-gapped box). Credentials
come from the environment with the shared testbed defaults. Not part of
the app's dependencies: ``pip install paramiko`` when needed.

Usage:
  python scripts/vm_remote.py put <local-file> <remote-path>
  python scripts/vm_remote.py run "<shell command>"
"""

from __future__ import annotations

import os
import socket
import sys

import paramiko

HOST = os.environ.get("MEGACODE_VM_HOST", "192.168.1.185")
USER = os.environ.get("MEGACODE_VM_USER", "user")
PASSWORD = os.environ.get("MEGACODE_VM_PASSWORD", "qwerty12")

USAGE = "usage: vm_remote.py put <local> <remote> | run \"<command>\""


def client() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=15,
              look_for_keys=False, allow_agent=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 180):
    _stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    try:
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
    except socket.timeout:
        # exec_command's timeout is an idle cap, not a runtime cap; report
        # it instead of dumping a traceback (a hung/silent remote command)
        raise SystemExit(f"[timeout] no output from VM for {timeout}s: {cmd}")
    return rc, out, err


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(USAGE)
    mode = sys.argv[1]
    c = client()
    try:
        if mode == "put":
            if len(sys.argv) != 4:
                raise SystemExit(USAGE)
            sftp = c.open_sftp()
            sftp.put(sys.argv[2], sys.argv[3])
            print(f"uploaded {sys.argv[2]} -> {USER}@{HOST}:{sys.argv[3]}")
        elif mode == "run":
            if len(sys.argv) != 3:
                raise SystemExit(USAGE)
            rc, out, err = run(c, sys.argv[2])
            print(f"$ {sys.argv[2]}")
            print(f"[rc={rc}]")
            if out:
                print(out, end="" if out.endswith("\n") else "\n")
            if err:
                print("[stderr]", err, end="" if err.endswith("\n") else "\n")
        else:
            raise SystemExit(f"unknown mode: {mode}\n{USAGE}")
    finally:
        c.close()


if __name__ == "__main__":
    main()
