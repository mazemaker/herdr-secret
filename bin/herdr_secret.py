#!/usr/bin/env python3
"""herdr-secret: an agent asks the human for a credential through a herdr popup form.

  herdr-secret ask NAME "what it is for" [--fields "username,password"] [--plain] [--timeout S]
      Opens the form in herdr and waits. Prints one line only:
      OK|CANCELLED|TIMEOUT|FAILED NAME [(fields)] [[where it was stored]]
      With --fields each value is stored as NAME.<field>; without, one hidden value as NAME.
  herdr-secret get NAME     value to stdout; refuses a terminal, so pipe it into the target
  herdr-secret list         stored names, never values
  herdr-secret rm NAME      delete NAME and any NAME.<field> entries
  herdr-secret setup        link the herdr-secret command into ~/.local/bin (Windows: print PATH hint)

Storage: macOS Keychain, Windows Credential Manager, Linux Secret Service (secret-tool),
or a 0600 file on Linux machines without a Secret Service. Values never pass through argv.
Python 3.8+, standard library only.
"""
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
SERVICE = "herdr-secret"
PLUGIN_ID = "mazemaker.secret"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
REQ_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
FIELD_RE = re.compile(r"[a-z0-9_]{1,32}")
MAX_FIELDS = 6
MAX_VALUE = 1000   # characters; Windows Credential Manager holds at most 2560 bytes of UTF-16
PLAIN_BY_DEFAULT = {"user", "username", "email", "login", "host", "url"}
DEVNULL = subprocess.DEVNULL
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

TEXT = {
    "en": {"title": "Credential requested: ", "from": "From  : ", "for": "For   : ", "hidden": " (hidden)",
           "save": "[ Save ]", "cancel": "[ Cancel ]", "value": "Value", "unknown": "unknown",
           "help": "Tab/↑↓ move · Enter next · Ctrl-R reveal · Esc cancel",
           "required": "All fields are required.", "saved": "Saved to {}.", "failed": "Could not save.",
           "invalid": "Invalid request.", "reported": "  (as reported by the requester)",
           "replaces": "Replaces the stored value of: {}", "timeout": "Timed out. Nothing was saved.",
           "toolong": "Too long (max {} characters)."},
    "id": {"title": "Credential diminta: ", "from": "Dari  : ", "for": "Untuk : ", "hidden": " (tersembunyi)",
           "save": "[ Simpan ]", "cancel": "[ Batal ]", "value": "Nilai", "unknown": "tidak diketahui",
           "help": "Tab/↑↓ pindah · Enter lanjut · Ctrl-R lihat isian · Esc batal",
           "required": "Semua kolom wajib diisi.", "saved": "Tersimpan di {}.", "failed": "Gagal menyimpan.",
           "invalid": "Permintaan tidak valid.", "reported": "  (menurut peminta)",
           "replaces": "Menimpa nilai lama: {}", "timeout": "Waktu habis. Tidak ada yang disimpan.",
           "toolong": "Terlalu panjang (maks {} karakter)."},
}


def lang():
    """SECRET_LANG (forwarded from the asking side) > lang= in the plugin config > the locale."""
    want = os.environ.get("SECRET_LANG", "")
    cfg = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not want and cfg:
        try:
            with open(os.path.join(cfg, "config"), encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.partition("=")
                    if k.strip() == "lang":
                        want = v.strip()
        except OSError:
            pass
    if not want:
        want = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "")[:2]
    return want if want in TEXT else "en"


def clean(text, limit=300):
    """Text from the asking agent is shown in the form: strip control characters so it cannot
    move the cursor, recolour, overwrite the "From" line, or reorder text with bidi marks."""
    return "".join(ch for ch in text
                   if not CONTROL.match(ch) and unicodedata.category(ch) not in ("Cf", "Zl", "Zp", "Co", "Cs"))[:limit]


def cwidth(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(text):
    return sum(cwidth(c) for c in text)


def fit_head(text, width):
    out, w = [], 0
    for ch in text:
        if w + cwidth(ch) > width:
            break
        out.append(ch)
        w += cwidth(ch)
    return "".join(out), w


def fit_tail(text, width):
    out, w = [], 0
    for ch in reversed(text):
        if w + cwidth(ch) > width:
            break
        out.append(ch)
        w += cwidth(ch)
    return "".join(reversed(out)), w


def write_atomic(path, text):
    """Readers never see a half-written file: write a sibling temp file, then rename over."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")   # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def state_dir():
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, "herdr-secret")
    os.makedirs(d, mode=0o700, exist_ok=True)
    if not IS_WIN:
        os.chmod(d, 0o700)
    return d


# ---------------------------------------------------------------- storage backends

class MacKeychain:
    label = "macOS Keychain"

    def store(self, acct, value):
        if CONTROL.search(value):
            return False
        esc = value.replace("\\", "\\\\").replace('"', '\\"')
        cmd = f'add-generic-password -U -s {SERVICE} -a "{acct}" -w "{esc}"\n'
        subprocess.run(["security", "-i"], input=cmd, text=True, stdout=DEVNULL, stderr=DEVNULL)
        return self.fetch(acct) == value

    def fetch(self, acct):
        # `-w` alone prints non-ASCII data as bare hex, indistinguishable from a hex-looking
        # password. `-g` marks it: `password: 0x<HEX>  "..."` versus `password: "<text>"`.
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", acct, "-g"],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            return None
        for line in r.stderr.splitlines():
            if line.startswith("password: 0x"):
                return bytes.fromhex(line[len("password: 0x"):].split()[0]).decode("utf-8", "replace")
            if line.startswith('password: "') and line.endswith('"'):
                return line[len('password: "'):-1]
            if line == "password: ":
                return ""
        return None

    def remove(self, acct):
        return subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", acct],
                              stdout=DEVNULL, stderr=DEVNULL).returncode == 0

    def names(self):
        r = subprocess.run(["security", "dump-keychain"], capture_output=True, text=True)
        out, acct = [], None
        for line in r.stdout.splitlines():
            if line.startswith("keychain:"):
                acct = None
            m = re.search(r'"acct"<blob>="(.*)"$', line)
            if m:
                acct = m.group(1)
            if f'"svce"<blob>="{SERVICE}"' in line and acct:
                out.append(acct)
        return out


class SecretService:
    label = "Secret Service"

    def _run(self, args, value=None):
        return subprocess.run(["secret-tool"] + args, input=value, text=True,
                              capture_output=True, timeout=30)

    def store(self, acct, value):
        r = self._run(["store", "--label", f"{SERVICE} {acct}", "service", SERVICE, "account", acct], value)
        return r.returncode == 0 and self.fetch(acct) == value

    def fetch(self, acct):
        r = self._run(["lookup", "service", SERVICE, "account", acct])
        return r.stdout.rstrip("\n") if r.returncode == 0 and r.stdout else None

    def remove(self, acct):
        return self._run(["clear", "service", SERVICE, "account", acct]).returncode == 0 and self.fetch(acct) is None

    def names(self):
        # `search` also prints each secret; only the account attribute is kept, nothing is shown.
        r = self._run(["search", "--all", "service", SERVICE])
        return [m.group(1) for m in re.finditer(r"^attribute\.account = (.+)$", r.stdout + r.stderr, re.M)]


class FileStore:
    label = "file 0600"

    def __init__(self):
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.dir = os.path.join(base, "herdr-secret", "store")

    def _path(self, acct):
        return os.path.join(self.dir, acct)

    def store(self, acct, value):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        write_atomic(self._path(acct), value)
        return self.fetch(acct) == value

    def fetch(self, acct):
        try:
            with open(self._path(acct), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def remove(self, acct):
        try:
            os.unlink(self._path(acct))
            return True
        except OSError:
            return False

    def names(self):
        try:
            return sorted(n for n in os.listdir(self.dir) if not n.startswith("."))
        except OSError:
            return []


class WinCred:
    label = "Windows Credential Manager"

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self.CRED = CREDENTIAL
        P = ctypes.POINTER(CREDENTIAL)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        a.CredWriteW.argtypes = [P, wintypes.DWORD]
        a.CredWriteW.restype = wintypes.BOOL
        a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(P)]
        a.CredReadW.restype = wintypes.BOOL
        a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        a.CredDeleteW.restype = wintypes.BOOL
        a.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(ctypes.POINTER(P))]
        a.CredEnumerateW.restype = wintypes.BOOL
        a.CredFree.argtypes = [ctypes.c_void_p]
        a.CredFree.restype = None
        self.a = a

    @staticmethod
    def _target(acct):
        return f"{SERVICE}:{acct}"

    def store(self, acct, value):
        ct = self.ct
        blob = value.encode("utf-16-le")
        buf = ct.create_string_buffer(blob, len(blob))
        c = self.CRED()
        c.Type = 1                      # CRED_TYPE_GENERIC
        c.TargetName = self._target(acct)
        c.UserName = acct
        c.CredentialBlobSize = len(blob)
        c.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        c.Persist = 2                   # CRED_PERSIST_LOCAL_MACHINE
        ok = self.a.CredWriteW(ct.byref(c), 0)
        ct.memset(buf, 0, len(blob))
        return bool(ok) and self.fetch(acct) == value

    def fetch(self, acct):
        ct = self.ct
        p = ct.POINTER(self.CRED)()
        if not self.a.CredReadW(self._target(acct), 1, 0, ct.byref(p)):
            return None
        try:
            c = p.contents
            return ct.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            self.a.CredFree(p)

    def remove(self, acct):
        return bool(self.a.CredDeleteW(self._target(acct), 1, 0))

    def names(self):
        ct = self.ct
        from ctypes import wintypes
        n = wintypes.DWORD()
        arr = ct.POINTER(ct.POINTER(self.CRED))()
        if not self.a.CredEnumerateW(f"{SERVICE}:*", 0, ct.byref(n), ct.byref(arr)):
            return []
        try:
            return sorted(arr[i].contents.TargetName[len(SERVICE) + 1:] for i in range(n.value))
        finally:
            self.a.CredFree(arr)


def backends():
    if IS_MAC:
        return [MacKeychain()]
    if IS_WIN:
        return [WinCred()]
    chain = [SecretService()] if shutil.which("secret-tool") else []
    return chain + [FileStore()]


def store_any(acct, value):
    chain = backends()
    for i, b in enumerate(chain):
        try:
            stored = b.store(acct, value)
        except Exception:
            stored = False
        if not stored:
            continue
        for other in chain[:i] + chain[i + 1:]:
            try:
                other.remove(acct)
            except Exception:
                pass
        # A store that refused this write (say a locked keyring) may still hold an older copy,
        # and `get` asks it first. Report failure rather than leave the old value in front.
        return b.label if fetch_any(acct) == value else None
    return None


def fetch_any(acct):
    for b in backends():
        try:
            v = b.fetch(acct)
        except Exception:
            v = None
        if v is not None:
            return v
    return None


def remove_any(acct):
    hit = False
    for b in backends():
        try:
            hit = b.remove(acct) or hit
        except Exception:
            pass
    return hit


def names_any():
    out = set()
    for b in backends():
        try:
            out.update(b.names())
        except Exception:
            pass
    return sorted(out)


# ---------------------------------------------------------------- terminal form

BOLD, DIM, CYAN, RED, YELLOW, REV, RESET = "\x1b[1m", "\x1b[2m", "\x1b[36m", "\x1b[31m", "\x1b[33m", "\x1b[7m", "\x1b[0m"


class Term:
    def __enter__(self):
        if IS_WIN:
            import msvcrt
            self.msvcrt = msvcrt
            os.system("")                      # turns on VT escape processing in the console
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        else:
            import termios
            import tty
            self.termios = termios
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        self.out("\x1b[?1049h\x1b[H\x1b[2J")
        return self

    def __exit__(self, *exc):
        self.out("\x1b[?25h\x1b[?1049l")
        if not IS_WIN:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.saved)

    @staticmethod
    def out(s):
        sys.stdout.write(s)
        sys.stdout.flush()

    @staticmethod
    def size():
        return shutil.get_terminal_size((76, 24))

    def key(self, timeout):
        """One key, or None after `timeout` seconds without input."""
        return self._key_win(timeout) if IS_WIN else self._key_unix(timeout)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = os.read(self.fd, n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _key_unix(self, timeout):
        import select
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        b = os.read(self.fd, 1)
        if not b:
            return "CANCEL"
        c = b[0]
        if c == 0x1B:
            if not select.select([self.fd], [], [], 0.03)[0]:
                return "ESC"
            nxt = os.read(self.fd, 1)
            if nxt not in (b"[", b"O"):
                return "ESC"
            seq = b""
            while len(seq) < 8:
                ch = os.read(self.fd, 1)
                seq += ch
                if ch.isalpha() or ch == b"~":
                    break
            return {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT", b"Z": "BTAB"}.get(seq[-1:])
        simple = {0x0D: "ENTER", 0x0A: "ENTER", 0x09: "TAB", 0x7F: "BS", 0x08: "BS",
                  0x03: "CANCEL", 0x04: "CANCEL", 0x15: "CLEAR", 0x12: "REVEAL"}
        if c in simple:
            return simple[c]
        if c < 0x20:
            return None
        n = 1 if c < 0x80 else 2 if c >> 5 == 0b110 else 3 if c >> 4 == 0b1110 else 4 if c >> 3 == 0b11110 else 1
        if n > 1:
            b += self._read(n - 1)
        ch = b.decode("utf-8", "ignore")
        return ("CHAR", ch) if ch and ch.isprintable() else None

    def _key_win(self, timeout):
        m = self.msvcrt
        end = time.time() + timeout
        while not m.kbhit():
            if time.time() >= end:
                return None
            time.sleep(0.03)
        ch = m.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT", "\x0f": "BTAB"}.get(m.getwch())
        simple = {"\r": "ENTER", "\n": "ENTER", "\t": "TAB", "\x08": "BS", "\x1b": "ESC",
                  "\x03": "CANCEL", "\x15": "CLEAR", "\x12": "REVEAL"}
        if ch in simple:
            return simple[ch]
        return ("CHAR", ch) if ch.isprintable() else None


def wrap(text, width):
    width = max(8, width)
    lines, cur = [], ""
    for word in text.split():
        while dwidth(word) > width:                 # hard-split words longer than a line
            head, _ = fit_head(word, width)
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(head)
            word = word[len(head):]
        if cur and dwidth(cur) + 1 + dwidth(word) > width:
            lines.append(cur)
            cur = word
        elif word:
            cur = f"{cur} {word}" if cur else word
    return lines + [cur] if cur else (lines or [""])


class Form:
    def __init__(self, fields, name, who, purpose, notice, T):
        self.fields, self.name, self.who, self.purpose, self.notice, self.T = fields, name, who, purpose, notice, T
        self.values = [[] for _ in fields]
        self.reveal = [False] * len(fields)
        self.focus = 0          # 0..n-1 fields, n = save, n+1 = cancel
        self.error = ""

    def build(self, cols, compact, pmax, nmax, blanks, show_help):
        T, n = self.T, len(self.fields)
        bw = max(24, min(cols - 4, 68))
        lines, cursor = [], None

        def add(text, style=""):
            text, _ = fit_head(text, max(10, cols - 3))
            lines.append(f"  {style}{text}{RESET}" if style else f"  {text}")

        add(T["title"] + self.name, BOLD)
        add(T["from"] + self.who + T["reported"], DIM)
        pad = " " * dwidth(T["for"])
        for i, part in enumerate(wrap(self.purpose, bw - len(pad))[:pmax]):
            add((T["for"] if i == 0 else pad) + part)
        if self.notice:
            for part in wrap(self.notice, bw)[:nmax]:
                add(part, YELLOW)
        if blanks:
            add("")
        for i, (_f, label, hidden) in enumerate(self.fields):
            style = CYAN + BOLD if self.focus == i else ""
            masked = hidden and not self.reveal[i]
            raw = "".join(self.values[i])
            full = ("•" * len(raw)) if masked else raw
            if compact:
                prefix = f"{'›' if self.focus == i else ' '} {label}{T['hidden'] if masked else ''}: "
                shown, w = fit_tail(full, max(4, bw - dwidth(prefix)))
                lines.append(f"  {style}{prefix}{RESET}{shown}")
                if self.focus == i:
                    cursor = (len(lines), 2 + dwidth(prefix) + w + 1)
            else:
                tag = f" {label}{T['hidden'] if masked else ''} "
                inner = bw - 4
                shown, w = fit_tail(full, inner)
                add("┌" + tag + "─" * max(0, bw - 2 - dwidth(tag)) + "┐", style)
                lines.append(f"  {style}│{RESET} {shown}{' ' * (inner - w)} {style}│{RESET}")
                if self.focus == i:
                    cursor = (len(lines), 2 + 2 + w + 1)
                add("└" + "─" * (bw - 2) + "┘", style)
        if self.error:
            add(self.error, RED)
        elif blanks:
            add("")
        save = f"{REV}{BOLD}{T['save']}{RESET}" if self.focus == n else f"{BOLD}{T['save']}{RESET}"
        cancel = f"{REV}{T['cancel']}{RESET}" if self.focus == n + 1 else T["cancel"]
        lines.append(f"  {save}  {cancel}")
        if show_help:
            if blanks:
                add("")
            add(T["help"], DIM)
        return lines, cursor

    def render(self, cols, rows):
        """Largest layout that fits. The title and "From" lines always come first and stay."""
        for cfg in ((False, 3, 2, True, True), (True, 3, 2, True, True), (True, 2, 1, False, True),
                    (True, 1, 1, False, True), (True, 1, 1, False, False)):
            lines, cursor = self.build(cols, *cfg)
            if len(lines) <= rows:
                break
        lines = lines[:rows]
        if cursor and cursor[0] > rows:
            cursor = None
        return lines, cursor

    def handle(self, k):
        """Returns "SAVE", "CANCEL" or None."""
        n = len(self.fields)
        self.error = ""
        if k in ("ESC", "CANCEL"):
            return "CANCEL"
        if k in ("TAB", "DOWN"):
            self.focus = (self.focus + 1) % (n + 2)
        elif k in ("BTAB", "UP"):
            self.focus = (self.focus - 1) % (n + 2)
        elif k in ("LEFT", "RIGHT") and self.focus >= n:
            self.focus = n + 1 if self.focus == n else n
        elif k == "ENTER":
            if self.focus == n + 1:
                return "CANCEL"
            if self.focus >= n - 1:
                empty = [i for i in range(n) if not self.values[i]]
                if not empty:
                    return "SAVE"
                self.focus, self.error = empty[0], self.T["required"]
            else:
                self.focus += 1
        elif self.focus < n:
            v = self.values[self.focus]
            if k == "BS" and v:
                v.pop()
            elif k == "CLEAR":
                v.clear()
            elif k == "REVEAL":
                self.reveal[self.focus] = not self.reveal[self.focus]
            elif isinstance(k, tuple):
                if len(v) >= MAX_VALUE:
                    self.error = self.T["toolong"].format(MAX_VALUE)
                else:
                    v.append(k[1])
        return None


def run_form(term, form, deadline):
    while True:
        if deadline and time.time() >= deadline:
            return "TIMEOUT"
        cols, rows = term.size()
        lines, cursor = form.render(cols, rows)
        frame = "\x1b[H" + "\r\n".join(line + "\x1b[K" for line in lines) + "\x1b[J"
        frame += f"\x1b[{cursor[0]};{cursor[1]}H\x1b[?25h" if cursor else "\x1b[?25l"
        term.out(frame)
        k = term.key(1.0)
        if k is None:
            continue                              # also redraws after a resize
        action = form.handle(k)
        if action == "CANCEL":
            return None
        if action == "SAVE":
            return ["".join(v) for v in form.values]


def parse_fields(spec, value_label="Value"):
    if not spec:
        return [("value", value_label, True)], True
    out = []
    for part in spec.split(","):
        fname, _, kind = part.strip().partition(":")
        if not FIELD_RE.fullmatch(fname) or kind not in ("", "plain", "hidden"):
            raise ValueError(f"invalid field: {part.strip()!r}")
        hidden = (kind == "hidden") if kind else fname not in PLAIN_BY_DEFAULT
        out.append((fname, fname.replace("_", " ").capitalize(), hidden))
    if len(out) > MAX_FIELDS or len({f for f, _, _ in out}) != len(out):
        raise ValueError(f"up to {MAX_FIELDS} fields, no duplicates")
    return out, len(out) == 1 and out[0][0] == "value"


def cmd_form():
    T = TEXT[lang()]
    name = os.environ.get("SECRET_NAME", "")
    req = os.environ.get("SECRET_REQ", "")
    try:
        deadline = float(os.environ.get("SECRET_DEADLINE") or 0)
    except ValueError:
        deadline = 0
    reported = []

    def status(s):
        if not reported and REQ_RE.fullmatch(req):
            write_atomic(os.path.join(state_dir(), req + ".status"), s + "\n")
            reported.append(s)

    def bail(*_):
        raise SystemExit(1)

    guarded = () if IS_WIN else (signal.SIGHUP, signal.SIGTERM)
    for sig in guarded:
        signal.signal(sig, bail)
    try:
        if not NAME_RE.fullmatch(name) or not REQ_RE.fullmatch(req):
            print(T["invalid"])
            time.sleep(2)
            return 1
        fields, single = parse_fields(os.environ.get("SECRET_FIELDS", "").strip(), T["value"])
        accts = [name if single else f"{name}.{f}" for f, _, _ in fields]
        prev = {a: fetch_any(a) for a in accts}
        existing = [a for a in accts if prev[a] is not None]
        notice = T["replaces"].format(", ".join(existing)) if existing else ""
        who = clean(os.environ.get("SECRET_FROM", ""), 80) or T["unknown"]
        form = Form(fields, name, who, clean(os.environ.get("SECRET_PROMPT", "")), notice, T)
        try:
            with Term() as t:
                result = run_form(t, form, deadline)
        except KeyboardInterrupt:
            result = None
        if result is None:
            status("CANCELLED")
            return 1
        if result == "TIMEOUT":
            status("TIMEOUT")
            print("\n  " + T["timeout"])
            time.sleep(1.5)
            return 1
        if guarded:   # a stored value must never be reported as CANCELLED
            signal.pthread_sigmask(signal.SIG_BLOCK, set(guarded))
        where, done = set(), []
        for a, value in zip(accts, result):
            label = store_any(a, value)
            if label is None:
                break
            where.add(label)
            done.append(a)
        ok = len(done) == len(accts)
        if not ok:    # roll back, so a login is never left half new and half old
            for a in done:
                if prev[a] is None:
                    remove_any(a)
                else:
                    store_any(a, prev[a])
        result[:] = []
        prev.clear()
        status(("OK " + ", ".join(sorted(where))) if ok else "FAILED")
        if guarded:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, set(guarded))
        print("\n  " + (T["saved"].format(", ".join(sorted(where))) if ok else T["failed"]))
        time.sleep(0.8 if ok else 3)
        return 0 if ok else 1
    finally:
        status("CANCELLED")


# ---------------------------------------------------------------- agent-side commands

def cmd_ask(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    name, rest = argv[0], argv[1:]
    purpose = rest.pop(0) if rest and not rest[0].startswith("--") else ""
    fields, timeout = "", 300
    while rest:
        a = rest.pop(0)
        if a == "--fields" and rest:
            fields = rest.pop(0)
        elif a == "--plain":
            fields = "value:plain"
        elif a == "--timeout" and rest:
            timeout = max(5, int(rest.pop(0)))
        else:
            print(f"unknown option {a!r}", file=sys.stderr)
            return 2
    if not NAME_RE.fullmatch(name):
        print("invalid NAME: letters, digits, _ . - and starting with a letter or digit", file=sys.stderr)
        return 2
    try:
        parse_fields(fields)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    req = f"{int(time.time())}-{os.getpid()}-{secrets.token_hex(4)}"
    deadline = time.time() + timeout
    herdr = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
    cmd = [herdr, "plugin", "pane", "open", "--plugin", PLUGIN_ID,
           "--entrypoint", "ask-windows" if IS_WIN else "ask", "--focus"]
    env = {"SECRET_NAME": name, "SECRET_PROMPT": clean(purpose), "SECRET_FIELDS": fields,
           "SECRET_REQ": req, "SECRET_DEADLINE": f"{deadline:.0f}",
           "SECRET_FROM": os.environ.get("HERDR_PANE_ID", "outside herdr"),
           "SECRET_LANG": os.environ.get("HERDR_SECRET_LANG", "")}
    for k, v in env.items():
        cmd += ["--env", f"{k}={v}"]
    tag = f" ({fields})" if fields and fields != "value:plain" else ""
    try:
        r = subprocess.run(cmd, stdout=DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        print(f"FAILED {name}{tag} [herdr not runnable: {e}]")
        return 1
    if r.returncode != 0:
        print(f"FAILED {name}{tag} [herdr: {r.stderr.strip()[:200]}]")
        return 1
    path = os.path.join(state_dir(), req + ".status")
    while time.time() < deadline + 5:            # the form closes itself at the deadline
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                s = f.read().strip()
            os.unlink(path)
            word, _, where = s.partition(" ")
            print(f"{word} {name}{tag}" + (f" [{where}]" if where else ""))
            return 0 if word == "OK" else 1
        time.sleep(0.5)
    print(f"TIMEOUT {name}{tag}")
    return 1


def cmd_setup():
    here = os.path.dirname(os.path.realpath(__file__))
    if IS_WIN:
        print("Add this folder to your user PATH, then open a new terminal:")
        print(f"  {here}")
        print("PowerShell:")
        print(f'  [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";{here}", "User")')
        return 0
    target = os.path.join(here, "herdr-secret")
    bindir = os.path.expanduser("~/.local/bin")
    link = os.path.join(bindir, "herdr-secret")
    os.makedirs(bindir, exist_ok=True)
    if os.path.lexists(link):
        if not (os.path.islink(link) and os.readlink(link).endswith(os.path.join("bin", "herdr-secret"))):
            print(f"{link} already exists and is not a herdr-secret link; left it alone")
            return 1
        os.unlink(link)
    os.symlink(target, link)
    print(f"linked {link} -> {target}")
    if bindir not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"note: {bindir} is not on PATH yet; add it in your shell profile")
    return 0


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "form":
        return cmd_form()
    if cmd == "ask":
        return cmd_ask(args)
    if cmd == "setup":
        return cmd_setup()
    if cmd == "list":
        for n in names_any():
            print(n)
        return 0
    if cmd in ("get", "rm") and args:
        if not NAME_RE.fullmatch(args[0]):
            print(f"herdr-secret {cmd}: invalid NAME", file=sys.stderr)
            return 2
        if cmd == "rm":
            targets = [args[0]] + [n for n in names_any() if n.startswith(args[0] + ".")]
            gone = [t for t in dict.fromkeys(targets) if remove_any(t)]
            print(f"removed {', '.join(gone)}" if gone else f"not found {args[0]}")
            return 0 if gone else 1
        if sys.stdout.isatty():
            print("herdr-secret get: stdout is a terminal; pipe it into the target", file=sys.stderr)
            return 2
        v = fetch_any(args[0])
        if v is None:
            print(f"herdr-secret get: {args[0]} not found", file=sys.stderr)
            return 1
        sys.stdout.buffer.write(v.encode("utf-8"))
        sys.stdout.flush()
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
