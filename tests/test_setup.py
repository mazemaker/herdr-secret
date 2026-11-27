"""Runs `setup --yes`, setup again, then `uninstall --yes` in a throwaway home directory with a
fake herdr, and checks what was written and removed. Runs in-process with the credential store
swapped for a file store inside the throwaway home, so the real home, keychain and credential
store are never touched."""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "bin", "herdr_secret.py")
IS_WIN = os.name == "nt"

home = tempfile.mkdtemp()
fakebin = os.path.join(home, "fakebin")
os.makedirs(fakebin)
log = os.path.join(home, "herdr.log")
with open(os.path.join(fakebin, "fake_herdr.py"), "w") as f:
    f.write(f'''import json, sys
args = sys.argv[1:]
open({log!r}, "a").write(" ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("herdr 0.9.5")
elif args[:3] == ["plugin", "list", "--json"]:
    print(json.dumps({{"result": {{"plugins": [{{"plugin_id": "mazemaker.secret", "plugin_root": {ROOT!r},
                                              "source": {{"kind": "local"}}}}]}}}}))
''')
if IS_WIN:
    with open(os.path.join(fakebin, "herdr.cmd"), "w") as f:
        f.write(f'@"{sys.executable}" "%~dp0fake_herdr.py" %*\r\n')
else:
    p = os.path.join(fakebin, "herdr")
    with open(p, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_herdr.py" "$@"\n')
    os.chmod(p, 0o755)

for d in (".claude", ".codex"):
    os.makedirs(os.path.join(home, d))
with open(os.path.join(home, ".claude", "CLAUDE.md"), "w") as f:
    f.write("# My rules\n\nBe brief.\n")
if not IS_WIN:
    with open(os.path.join(home, ".zshrc"), "w") as f:
        f.write("alias ll='ls -l'\n")
    os.chmod(os.path.join(home, ".zshrc"), 0o640)

PATH = fakebin + os.pathsep + os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
if not IS_WIN:   # make sure ~/.local/bin is not already on PATH
    PATH = os.pathsep.join(p for p in PATH.split(os.pathsep) if not p.endswith(".local/bin"))
os.environ.update(HOME=home, USERPROFILE=home, SHELL="/bin/zsh", PATH=PATH,
                  LOCALAPPDATA=os.path.join(home, "AppData", "Local"))
for k in ("XDG_STATE_HOME", "XDG_DATA_HOME", "HERDR_BIN_PATH", "HERDR_PLUGIN_ENTRYPOINT_ID"):
    os.environ.pop(k, None)

sys.path.insert(0, os.path.join(ROOT, "bin"))
import herdr_secret as hs  # noqa: E402

hs.backends = lambda: [hs.FileStore()]


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hs.main(list(args))
    return code, out.getvalue()


def read(*parts):
    try:
        with open(os.path.join(home, *parts), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


failures = []
for attempt in (1, 2):
    code, out = run("setup", "--yes")
    if code != 0:
        failures.append(f"setup #{attempt} exit {code}: {out[-800:]}")
        break
claude = read(".claude", "CLAUDE.md") or ""
if not claude.startswith("# My rules") or claude.count("herdr-secret:begin") != 1 or "herdr-secret ask" not in claude:
    failures.append(f"CLAUDE.md block wrong:\n{claude}")
if "name: herdr-secret" not in (read(".claude", "skills", "herdr-secret", "SKILL.md") or ""):
    failures.append("skill not installed")
if (read(".codex", "AGENTS.md") or "").count("herdr-secret:begin") != 1:
    failures.append("Codex AGENTS.md block missing or duplicated")
if read(".gemini", "GEMINI.md") is not None:
    failures.append("wrote instructions for an agent that is not installed")
marker = json.loads(read(".local", "state", "herdr-secret", "setup.json") or
                    read("AppData", "Local", "herdr-secret", "setup.json") or "{}")
if marker.get("status") != "done":
    failures.append(f"marker: {marker}")
if not IS_WIN:
    zshrc = read(".zshrc") or ""
    if zshrc.count("herdr-secret:begin") != 1 or '$HOME/.local/bin' not in zshrc or not zshrc.startswith("alias ll"):
        failures.append(f".zshrc wrong:\n{zshrc}")
    if oct(os.stat(os.path.join(home, ".zshrc")).st_mode & 0o777) != "0o640":
        failures.append(".zshrc permissions changed")
    if not os.path.islink(os.path.join(home, ".local", "bin", "herdr-secret")):
        failures.append("command not linked")

code, out = run("uninstall", "--yes")
if code != 0:
    failures.append(f"uninstall exit {code}: {out[-600:]}")
if (read(".claude", "CLAUDE.md") or "") != "# My rules\n\nBe brief.\n":
    failures.append(f"CLAUDE.md not restored: {read('.claude', 'CLAUDE.md')!r}")
if read(".claude", "skills", "herdr-secret", "SKILL.md") is not None:
    failures.append("skill left behind")
if "herdr-secret" in (read(".codex", "AGENTS.md") or ""):
    failures.append("Codex block left behind")
if not IS_WIN:
    if (read(".zshrc") or "") != "alias ll='ls -l'\n":
        failures.append(f".zshrc not restored: {read('.zshrc')!r}")
    if os.path.lexists(os.path.join(home, ".local", "bin", "herdr-secret")):
        failures.append("link left behind")
if "plugin unlink mazemaker.secret" not in (open(log).read() if os.path.exists(log) else ""):
    failures.append("uninstall did not remove the plugin from herdr")

print("\n".join(failures) if failures else "setup, repeat setup and uninstall behave")
sys.exit(1 if failures else 0)
