"""Runs `setup --yes`, setup again, then `uninstall --yes` in throwaway home directories: once
with only the herdr TUI, once with only Herdr.app, once with both (fake herdr and fake herdr-app
programs), and checks what was written and removed. Runs in-process with the credential store
swapped for a file store inside the throwaway home, so the real home, keychain, credential store
and Herdr.app are never touched."""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IS_WIN = os.name == "nt"

sys.path.insert(0, os.path.join(ROOT, "bin"))
import herdr_secret as hs  # noqa: E402

hs.backends = lambda: [hs.FileStore()]


def has_herdr(d):
    return any(os.path.exists(os.path.join(d, n + x)) for n in ("herdr", "herdr-app", "hc")
               for x in ("", ".exe", ".cmd"))


# No real herdr or herdr-app, and make sure ~/.local/bin is not already on PATH.
BASE_PATH = [d for d in os.environ.get("PATH", "").split(os.pathsep)
             if d and not has_herdr(d) and not d.rstrip("/\\").endswith(".local/bin")]
failures = []


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hs.main(list(args))
    return code, out.getvalue()


def same(a, b):
    try:
        with open(a, "rb") as f1, open(b, "rb") as f2:
            return f1.read() == f2.read()
    except OSError:
        return False


def scenario(label, tui, app):
    def fail(msg):
        failures.append(f"[{label}] {msg}")

    home = tempfile.mkdtemp()
    fakebin = os.path.join(home, "fakebin")
    os.makedirs(fakebin)
    log = os.path.join(home, "fake.log")

    def read(*parts):
        try:
            with open(os.path.join(home, *parts), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    with open(os.path.join(fakebin, "fake_herdr.py"), "w") as f:
        f.write(f'''import json, sys
args = sys.argv[1:]
open({log!r}, "a").write("herdr " + " ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("herdr 0.9.5")
elif args[:3] == ["plugin", "list", "--json"]:
    print(json.dumps({{"result": {{"plugins": [{{"plugin_id": "mazemaker.secret", "plugin_root": {ROOT!r},
                                              "source": {{"kind": "local"}}}}]}}}}))
''')
    with open(os.path.join(fakebin, "fake_herdr_app.py"), "w") as f:
        f.write(f'''import os, sys
args = sys.argv[1:]
open({log!r}, "a").write("herdr-app " + " ".join(args) + "\\n")
if args[:1] == ["check"]:
    if os.environ.get("FAKE_CHECK") == "fail":
        print("main.js:1: broken", file=sys.stderr)
        sys.exit(1)
    print("check passed: " + args[1])
''')
    for name, script, wanted in (("herdr", "fake_herdr.py", tui), ("herdr-app", "fake_herdr_app.py", app)):
        if not wanted:
            continue
        if IS_WIN:
            with open(os.path.join(fakebin, name + ".cmd"), "w") as f:
                f.write(f'@"{sys.executable}" "%~dp0{script}" %*\r\n')
        else:
            p = os.path.join(fakebin, name)
            with open(p, "w") as f:
                f.write(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/{script}" "$@"\n')
            os.chmod(p, 0o755)

    for d in (".claude", ".codex"):
        os.makedirs(os.path.join(home, d))
    with open(os.path.join(home, ".claude", "CLAUDE.md"), "w") as f:
        f.write("# My rules\n\nBe brief.\n")
    if not IS_WIN:
        with open(os.path.join(home, ".zshrc"), "w") as f:
            f.write("alias ll='ls -l'\n")
        os.chmod(os.path.join(home, ".zshrc"), 0o640)
    if app:
        os.makedirs(os.path.join(home, ".config", "herdr-app"))      # Herdr.app has run once

    os.environ.update(HOME=home, USERPROFILE=home, SHELL="/bin/zsh",
                      PATH=os.pathsep.join([fakebin, os.path.dirname(sys.executable)] + BASE_PATH),
                      LOCALAPPDATA=os.path.join(home, "AppData", "Local"))
    for k in ("XDG_STATE_HOME", "XDG_DATA_HOME", "HERDR_BIN_PATH", "HERDR_PLUGIN_ENTRYPOINT_ID", "HERDR_APP_HOME",
              "HERDR_PANE_ID", "HC_TERMINAL_SOCKET", "HC_TERMINAL_ID", "HC_SCOPE", "FAKE_CHECK"):
        os.environ.pop(k, None)
    hs.ROOT = ROOT
    engine = hs.engine_home()
    mod = os.path.join(home, ".config", "herdr-app", "mods", "herdr-secret")
    if not engine.startswith(home) or not hs.app_mod_dir() == mod:
        sys.exit(f"[{label}] refusing to run: paths outside the throwaway home")

    if not tui and not app:
        code, out = run("setup", "--yes")
        if code != 1 or "Neither herdr (the TUI) nor Herdr.app was found" not in out:
            fail(f"setup without herdr or Herdr.app: exit {code}: {out[-400:]}")
        return

    for attempt in (1, 2):
        if attempt == 2 and app:
            with open(os.path.join(mod, "main.js"), "a", encoding="utf-8") as f:
                f.write("// an older version\n")
            if tui:
                os.environ["FAKE_CHECK"] = "fail"
        code, out = run("setup", "--yes")
        if code != 0:
            fail(f"setup #{attempt} exit {code}: {out[-800:]}")
            return
        if attempt == 2 and tui and app and "herdr-app check failed" not in out:
            fail(f"a failing herdr-app check was not reported: {out[-600:]}")
    os.environ.pop("FAKE_CHECK", None)

    claude = read(".claude", "CLAUDE.md") or ""
    if not claude.startswith("# My rules") or claude.count("herdr-secret:begin") != 1 or "herdr-secret ask" not in claude:
        fail(f"CLAUDE.md block wrong:\n{claude}")
    if "name: herdr-secret" not in (read(".claude", "skills", "herdr-secret", "SKILL.md") or ""):
        fail("skill not installed")
    if (read(".codex", "AGENTS.md") or "").count("herdr-secret:begin") != 1:
        fail("Codex AGENTS.md block missing or duplicated")
    if read(".gemini", "GEMINI.md") is not None:
        fail("wrote instructions for an agent that is not installed")
    marker = json.loads(read(".local", "state", "herdr-secret", "setup.json") or
                        read("AppData", "Local", "herdr-secret", "setup.json") or "{}")
    if marker.get("status") != "done" or marker.get("version") != hs.plugin_version():
        fail(f"marker: {marker}")

    if tui:      # run as the herdr plugin: nothing copied, the command is the plugin's
        if os.path.exists(engine):
            fail("copied the engine although the herdr plugin is installed")
        want_target = os.path.join(ROOT, "bin", "herdr-secret")
    else:        # Herdr.app only: an installed copy of the engine
        for rel in hs.ENGINE_FILES:
            if not same(os.path.join(ROOT, *rel.split("/")), os.path.join(engine, *rel.split("/"))):
                fail(f"engine copy: {rel} missing or different")
        if not IS_WIN and not os.access(os.path.join(engine, "bin", "herdr-secret"), os.X_OK):
            fail("engine launcher is not executable")
        if marker.get("engine") != engine:
            fail(f"marker engine: {marker.get('engine')}")
        want_target = os.path.join(engine, "bin", "herdr-secret")

    if app:
        for name in hs.APP_MOD_FILES:
            p = os.path.join(mod, name)
            if os.path.islink(p) or not same(os.path.join(ROOT, "mods", "herdr-secret", name), p):
                fail(f"mod file {name} missing, linked or not updated")
        checks = [ln for ln in (read("fake.log") or "").splitlines() if ln.startswith("herdr-app check ")]
        if len(checks) != 2 or not all(ln.endswith(mod) for ln in checks):
            fail(f"herdr-app check runs: {checks}")
        if marker.get("mod") != mod:
            fail(f"marker mod: {marker.get('mod')}")
    elif os.path.exists(os.path.join(home, ".config", "herdr-app")) or marker.get("mod") is not None:
        fail("installed the Herdr.app mod without Herdr.app")

    if not IS_WIN:
        zshrc = read(".zshrc") or ""
        if zshrc.count("herdr-secret:begin") != 1 or '$HOME/.local/bin' not in zshrc or not zshrc.startswith("alias ll"):
            fail(f".zshrc wrong:\n{zshrc}")
        if oct(os.stat(os.path.join(home, ".zshrc")).st_mode & 0o777) != "0o640":
            fail(".zshrc permissions changed")
        link = os.path.join(home, ".local", "bin", "herdr-secret")
        if not os.path.islink(link) or not hs.same_path(os.readlink(link), want_target):
            fail(f"command linked to {os.readlink(link) if os.path.islink(link) else None}, not {want_target}")

    hs.FileStore().store("ZZ_KEEP", "dummy")
    code, out = run("uninstall", "--yes")
    if code != 0:
        fail(f"uninstall exit {code}: {out[-600:]}")
    if (read(".claude", "CLAUDE.md") or "") != "# My rules\n\nBe brief.\n":
        fail(f"CLAUDE.md not restored: {read('.claude', 'CLAUDE.md')!r}")
    if read(".claude", "skills", "herdr-secret", "SKILL.md") is not None:
        fail("skill left behind")
    if "herdr-secret" in (read(".codex", "AGENTS.md") or ""):
        fail("Codex block left behind")
    if not IS_WIN:
        if (read(".zshrc") or "") != "alias ll='ls -l'\n":
            fail(f".zshrc not restored: {read('.zshrc')!r}")
        if os.path.lexists(os.path.join(home, ".local", "bin", "herdr-secret")):
            fail("link left behind")
    if os.path.exists(mod):
        fail("Herdr.app mod left behind")
    if os.path.exists(engine):
        fail("engine copy left behind")
    if hs.FileStore().fetch("ZZ_KEEP") != "dummy":
        fail("uninstall --yes deleted a stored credential")
    if tui and "herdr plugin unlink mazemaker.secret" not in (read("fake.log") or ""):
        fail("uninstall did not remove the plugin from herdr")

    if app and not tui:      # a mod folder of the same name that is not ours stays untouched
        os.makedirs(mod)
        with open(os.path.join(mod, "main.js"), "w") as f:
            f.write("// someone else's mod\n")
        hs.ROOT = ROOT           # uninstall removed the copy this process last ran from
        code, out = run("setup", "--yes")
        if "is not herdr-secret's mod; left it alone" not in out or read(*os.path.relpath(
                os.path.join(mod, "main.js"), home).split(os.sep)) != "// someone else's mod\n":
            fail(f"overwrote a foreign mod: {out[-400:]}")
        run("uninstall", "--yes")
        if not os.path.exists(os.path.join(mod, "main.js")):
            fail("uninstall removed a foreign mod")


scenario("herdr TUI only", tui=True, app=False)
scenario("Herdr.app only", tui=False, app=True)
scenario("herdr TUI and Herdr.app", tui=True, app=True)
scenario("neither", tui=False, app=False)

print("\n".join(failures) if failures else "setup, repeat setup and uninstall behave (TUI, Herdr.app, both)")
sys.exit(1 if failures else 0)
