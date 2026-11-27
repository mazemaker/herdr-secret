"""Drives the form in a pseudo-terminal (macOS and Linux): fill and save, then cancel.
Uses the real credential store and cleans up."""
import os
import pty
import select
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "herdr_secret.py")
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import herdr_secret as hs  # noqa: E402

state = tempfile.mkdtemp()


def drive(req, fields, keys, lang="en", deadline=""):
    env = dict(os.environ, SECRET_NAME="ZZ_FORM", SECRET_REQ=req, SECRET_FIELDS=fields,
               SECRET_FROM="test", SECRET_PROMPT="pty test \x1b[2J", SECRET_LANG=lang,
               SECRET_DEADLINE=deadline, XDG_STATE_HOME=state, COLUMNS="78", LINES="24")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, "-I", SCRIPT, "form"], env)
    screen = b""

    def pump(t):
        nonlocal screen
        end = time.time() + t
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    screen += os.read(fd, 65536)
                except OSError:
                    return

    pump(0.8)
    for k in keys:
        os.write(fd, k)
        pump(0.15)
    pump(1.5)
    os.waitpid(pid, 0)
    path = os.path.join(state, "herdr-secret", req + ".status")
    with open(path) as f:
        return f.read().strip(), screen.decode("utf-8", "replace")


failures = []
status, screen = drive("p1", "username,password", [b"bob", b"\t", "s3crét\"\\".encode(), b"\r"])
if not status.startswith("OK"):
    failures.append(f"save: status {status!r}")
if hs.fetch_any("ZZ_FORM.username") != "bob" or hs.fetch_any("ZZ_FORM.password") != "s3crét\"\\":
    failures.append("save: stored values differ")
if "s3cr" in screen:
    failures.append("save: hidden value was echoed to the screen")
if "\x1b[2J" in screen.split("pty test")[1][:10]:
    failures.append("prompt control characters reached the screen")
for acct in ("ZZ_FORM.username", "ZZ_FORM.password"):
    hs.remove_any(acct)

status, screen = drive("p2", "username,password", [b"x", b"\x1b"], lang="id")
if status != "CANCELLED":
    failures.append(f"cancel: status {status!r}")
if "Credential diminta" not in screen:
    failures.append("lang=id not applied")
if any(n.startswith("ZZ_FORM") for n in hs.names_any()):
    failures.append("cancel stored something")

hs.store_any("ZZ_FORM", "old")
status, screen = drive("p3", "", [b"new", b"\r"])
if not status.startswith("OK") or hs.fetch_any("ZZ_FORM") != "new":
    failures.append(f"overwrite: status {status!r}")
if "Replaces the stored value of: ZZ_FORM" not in screen:
    failures.append("overwrite notice not shown")
hs.remove_any("ZZ_FORM")

status, screen = drive("p4", "username,password", [b"slow"], deadline=str(time.time() + 2))
if status != "TIMEOUT":
    failures.append(f"deadline: status {status!r}")
if any(n.startswith("ZZ_FORM") for n in hs.names_any()):
    failures.append("deadline stored something")

print("\n".join(failures) if failures else "form: save, cancel, overwrite notice and deadline behave")
sys.exit(1 if failures else 0)
