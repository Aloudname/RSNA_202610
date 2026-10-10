"""Bootstrap for `download.sh` / `download.bat`.

Steps in order:

  1. resolve the target directory and record it in `.env` as RAW_DIR
  2. preflight the target: free disk space, Windows long-path risk
  3. preflight the Kaggle credentials, offering `kaggle auth login` if missing
  4. make sure some interpreter has the downloader's dependencies installed
  5. take a run lock next to the dataset so two runs cannot collide
  6. run `scripts/download_dataset.py`, teeing its output to
     `<dest>/download-<UTC>.log`, and forward its exit code

Standard library only, Python 3.8+, no import side effects: this file has to run
before any dependency is installed. Arguments it does not recognise are passed
through to the downloader unchanged, so:

    ./download.sh --dest /data/rsna --limit 300

Run `./download.sh --help` for the options handled here, and
`./download.sh -- --help` for the downloader's own.

Nothing here prints a token, and the credentials file is only ever read for its
`username` field. See docs/background/download.md.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

GIB = 1024 ** 3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADER = os.path.join(ROOT, "scripts", "download_dataset.py")
ENV_FILE = os.path.join(ROOT, ".env")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")

DEFAULT_NEED_GIB = 600.0  # ~530 GiB of payload + room for the archive metadata
SMOKE_NEED_GIB = 5.0  # --limit / --verify-only / --dry-run runs are tiny
SMOKE_FLAGS = ("--limit", "--verify-only", "--dry-run")

# Worst case for one entry of the bundle: three 64-char UIDs plus a 68-char
# file name, i.e. ~213 characters below the destination root.
LONGEST_ENTRY_SUFFIX = 220
WINDOWS_PATH_LIMIT = 259

EXIT_OK = 0
EXIT_ERROR = 1  # environment could not be prepared, or the download failed
EXIT_PRECHECK = 2  # preflight refused, or another run owns the target
EXIT_AUTH = 3  # no credentials and we are not allowed to log in interactively
EXIT_INTERRUPT = 130

# After Ctrl+C the downloader gets this long to finish on its own before it is
# killed; a second Ctrl+C arriving this long after the first kills at once.
KILL_GRACE_SECONDS = 20.0
FORCE_KILL_SECONDS = 2.0

VALUE_FLAGS = {"--dest": "dest", "--need-gb": "need_gb", "--log": "log"}
BOOL_FLAGS = {
    "--yes": "yes",
    "--force": "force",
    "--no-log": "no_log",
    "--skip-auth-check": "skip_auth_check",
    "--relogin": "relogin",
    "--check-longpaths": "check_longpaths",
}

# Set by main() so that say()/_emit() can mirror everything into the log file.
_LOG = None
_STDOUT_BIN = getattr(sys.stdout, "buffer", None)


# small helpers
def _encode(text):
    return (text + "\n").encode("utf-8", "replace")


def _emit(data, log=True):
    """Write raw bytes to our stdout and, if it is open, to the log file."""
    if _STDOUT_BIN is not None:
        try:
            _STDOUT_BIN.write(data)
            _STDOUT_BIN.flush()
        except (BrokenPipeError, ValueError):
            pass
    if log and _LOG is not None:
        try:
            _LOG.write(data)
            _LOG.flush()
        except OSError:
            pass


def say(text=""):
    print(text, flush=True)
    if _LOG is not None:
        _LOG.write(_encode(text))
        _LOG.flush()


def warn(text):
    say("[warn] " + text)


def die(code, text, *more):
    """Print a final message (also to the log) and terminate with `code`."""
    say("")
    say(text)
    for line in more:
        say(line)
    sys.exit(code)


def which(program):
    return shutil.which(program)


def safe(text, limit=200):
    """A value from a file or a user, made fit to print."""
    if text is None:
        return ""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))
    return cleaned[:limit]


def human_duration(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def utc_stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# arg handling
def parse_args(argv):
    opts = {
        "dest": None,
        "yes": False,
        "force": False,
        "need_gb": None,
        "log": None,
        "no_log": False,
        "skip_auth_check": False,
        "relogin": False,
        "check_longpaths": False,
        "help": False,
    }
    passthrough = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            passthrough.extend(argv[index + 1 :])
            break
        if arg == "-y":
            arg = "--yes"
        name, sep, inline = arg.partition("=")
        if arg in ("-h", "--help"):
            opts["help"] = True
        elif name in VALUE_FLAGS:
            if sep:
                value = inline
            else:
                if index + 1 >= len(argv):
                    die(EXIT_PRECHECK, f"[args] {name} needs a value.")
                value = argv[index + 1]
                index += 1
            key = VALUE_FLAGS[name]
            if key == "need_gb":
                try:
                    opts[key] = float(value)
                except ValueError:
                    die(EXIT_PRECHECK, f"[args] --need-gb wants a number, got {safe(value)}.")
            else:
                opts[key] = value
        elif name in BOOL_FLAGS:
            if sep:
                die(EXIT_PRECHECK, f"[args] {name} does not take a value.")
            opts[BOOL_FLAGS[name]] = True
        else:
            passthrough.append(arg)
        index += 1
    return opts, passthrough


def forwards_flag(passthrough, names):
    for arg in passthrough:
        if arg.split("=", 1)[0] in names:
            return True
    return False


def print_help():
    if __doc__ is None:
        return

    say(__doc__.strip().splitlines()[0])
    say("")
    say("Usage:")
    say("  ./download.sh   [options]        (Linux, macOS, WSL, Git-Bash)")
    say("  download.bat    [options]        (Windows)")
    say("")
    say("Options handled by the bootstrap:")
    say("  --dest PATH        where the dataset goes; also saved as RAW_DIR in .env")
    say("  --yes, -y          never prompt; fail instead if something is missing")
    say("  --force            continue past failed preflight checks")
    say("  --need-gb N        free space required in GiB (default 600, 5 for smoke runs)")
    say("  --log PATH         log file (default <dest>/download-<UTC>.log)")
    say("  --no-log           do not write a log file")
    say("  --skip-auth-check  skip the extra 'can these credentials still mint a token'")
    say("                     check")
    say("  --relogin          force a fresh `kaggle auth login`")
    say("  --check-longpaths  run the Windows long-path check on any OS (for testing)")
    say("  -h, --help         this text")
    say("")
    say("Anything else is forwarded to scripts/download_dataset.py untouched:")
    say("  ./download.sh --dest /data/rsna --limit 300")
    say("  ./download.sh -- --help          # the downloader's own options")
    say("")
    say("Exit codes: 0 ok, 1 environment or download problem, 2 preflight refusal or")
    say("another run in progress, 3 not authenticated, 130 interrupted.")
    say("Interrupting is safe: re-run the same command and it resumes.")


# 1. target directory and .env
_RAW_DIR_RE = re.compile(r"^\s*RAW_DIR\s*=\s*(.*?)\s*$")
PLACEHOLDERS = {"", "/where?", "where?", "/where", "none", "null", "changeme", "todo"}


def dequote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def usable_path(value):
    """True when a configured RAW_DIR is a real path and not a placeholder.

    Values with control characters are rejected too: python-dotenv decodes
    escapes inside double quotes, so a hand-written "D:\\rsna" comes back as
    "D:" plus a carriage return plus "sna" rather than a path.
    """
    if value is None:
        return False
    text = value.strip()
    if text.lower() in PLACEHOLDERS:
        return False
    return not any(ord(ch) < 32 for ch in text)


def read_env_raw_dir(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = _RAW_DIR_RE.match(line.rstrip("\r\n"))
                if match:
                    return dequote(match.group(1))
    except OSError:
        pass
    return None


def write_env_raw_dir(dest):
    """Set RAW_DIR in .env, keeping every other line as it is.

    Always written with forward slashes: they work on Windows too and cannot be
    eaten by python-dotenv's backslash-escape decoding.
    """
    value = dest.replace("\\", "/")
    lines = []
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            lines = []
    elif os.path.exists(ENV_EXAMPLE):
        try:
            with open(ENV_EXAMPLE, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            lines = []

    new_line = f'RAW_DIR = "{value}"'
    for index, line in enumerate(lines):
        if _RAW_DIR_RE.match(line):
            lines[index] = new_line
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)

    try:
        with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as error:
        die(EXIT_PRECHECK, f"[env] could not write {ENV_FILE}: {safe(error)}")


def default_dest():
    if os.name == "nt":
        for drive in ("D:/", "E:/", "F:/"):
            if os.path.exists(drive):
                return drive + "rsna-data"
        return "C:/rsna-data"
    return os.path.join(os.path.expanduser("~"), "rsna-202610-data")


def can_prompt(opts):
    if opts["yes"]:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def ask_dest(default):
    say("[setup] No dataset directory is configured yet (.env has no usable RAW_DIR).")
    say("        The full dataset is ~570 GB once extracted, so pick a volume with")
    say("        room to spare.")
    while True:
        try:
            answer = input(f"[setup] Where should the dataset go? [{default}] ").strip()
        except EOFError:
            say("")
            return default
        if not answer:
            return default
        candidate = os.path.expanduser(dequote(answer))
        if '"' in candidate:
            say("[setup] Please avoid double quotes in the path.")
            continue
        return candidate


def is_inside(path, parent):
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    return path == parent or path.startswith(parent + os.sep)


def resolve_dest(opts):
    dest = opts["dest"]
    if dest is not None and not dest.strip():
        die(EXIT_PRECHECK, "[dest] --dest was given an empty path.")
    if dest is None:
        from_env = os.environ.get("RAW_DIR")
        if usable_path(from_env):
            dest = from_env
            say("[dest] using RAW_DIR from the environment.")
        elif from_env is not None and from_env.strip():
            die(
                EXIT_PRECHECK,
                f"[dest] the RAW_DIR environment variable is not a usable path: {safe(from_env)}",
                "       Pass --dest PATH instead, or unset RAW_DIR.",
            )

    if dest is None:
        from_file = read_env_raw_dir(ENV_FILE)
        if usable_path(from_file):
            dest = from_file
            say("[dest] using RAW_DIR from .env.")
        elif from_file is not None and from_file.strip().lower() not in PLACEHOLDERS:
            warn(f"the RAW_DIR in .env is not a usable path ({safe(from_file)}); ignoring it.")

    if dest is None:
        if not can_prompt(opts):
            die(
                EXIT_PRECHECK,
                "[dest] no dataset directory was given and I cannot ask for one.",
                "       Pass it explicitly, or set it once and for all:",
                "         ./download.sh --dest /path/to/data",
            )
        dest = ask_dest(default_dest())

    if '"' in dest:
        die(EXIT_PRECHECK, f'[dest] the path must not contain a double quote: {safe(dest)}')

    dest = os.path.abspath(os.path.expanduser(dest.strip()))
    if is_inside(dest, ROOT) and not opts["force"]:
        die(
            EXIT_PRECHECK,
            "[dest] the dataset would be extracted inside the repository itself:",
            f"       {safe(dest)}",
            f"       Pick a directory outside {safe(ROOT)} (a 570 GB payload has no business",
            "       in a git working tree), or pass --force if you really mean it.",
        )
    return dest


# 2. preflights: disk space and the Windows path-length limit
def nearest_existing(path):
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def volume_candidates(dest):
    """(mountpoint, free bytes) for the volumes a user could pick instead."""
    roots = [nearest_existing(dest), os.path.expanduser("~")]
    if os.name == "nt":
        for letter in "CDEFGH":
            roots.append(letter + ":\\")
    else:
        try:
            with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] in ("ext4", "xfs", "btrfs", "zfs", "apfs", "nfs", "nfs4"):
                        roots.append(parts[1].replace("\\040", " "))
        except OSError:
            pass
        for mount in ("/Volumes", "/mnt", "/media", "/data"):
            if os.path.isdir(mount):
                try:
                    roots.extend(os.path.join(mount, name) for name in os.listdir(mount))
                except OSError:
                    pass

    seen = set()
    found = []
    for root in roots:
        if not root:
            continue
        try:
            stat = os.stat(root)
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        key = (stat.st_dev, usage.total)
        if key in seen:
            continue
        seen.add(key)
        found.append((root, usage.free))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def required_gib(opts, passthrough):
    if opts["need_gb"] is not None:
        return opts["need_gb"]
    if forwards_flag(passthrough, SMOKE_FLAGS):
        return SMOKE_NEED_GIB
    return DEFAULT_NEED_GIB


def check_disk(dest, opts, passthrough):
    need = required_gib(opts, passthrough)
    anchor = nearest_existing(dest)
    try:
        usage = shutil.disk_usage(anchor)
    except OSError as error:
        warn(f"could not read the free space of {anchor} ({safe(error)}); skipping the check.")
        return
    free = usage.free / float(GIB)
    say(f"[disk] {anchor}: {free:.0f} GiB free, {need:.0f} GiB needed.")
    if free >= need:
        return

    say("")
    say(f"[disk] not enough free space on {anchor}: {free:.0f} GiB free, {need:.0f} GiB needed.")
    roomy = [(root, free) for root, free in volume_candidates(dest) if free / float(GIB) >= need]
    if roomy:
        say("[disk] volumes with room for the full dataset:")
        for root, free in roomy[:6]:
            say(f"         {root}: {free / float(GIB):.0f} GiB free")
    else:
        say("[disk] no filesystem reachable from here has that much free space.")
    say("")
    if opts["force"]:
        warn("--force: continuing although the disk looks too small.")
        return
    die(
        EXIT_PRECHECK,
        "[disk] refusing to start: filling this filesystem would be worse than not",
        "       starting at all. Point --dest at a bigger volume and run again,",
        "       or pass --need-gb N / --force if you know better.",
    )


def long_paths_enabled():
    """Windows-only: is the LongPathsEnabled registry switch on?"""
    if os.name != "nt":
        return None
    try:
        import winreg  # only 4 windows

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value) 
    except Exception:  # noqa: BLE001
        return None


def check_long_paths(dest, opts):
    if os.name != "nt" and not opts["check_longpaths"]:
        return
    simulated = ""
    if os.name != "nt":
        simulated = " (simulated: this is not Windows, requested by --check-longpaths)"

    worst = len(os.path.abspath(dest)) + LONGEST_ENTRY_SUFFIX
    say(f"[paths] longest dataset path would be about {worst} characters{simulated}")
    if worst <= 240:
        return

    enabled = long_paths_enabled()
    if worst <= WINDOWS_PATH_LIMIT or enabled:
        reason = "the Windows long-path switch is enabled" if enabled else "still under the limit"
        warn(
            f"the target directory is deep: about {worst} characters for the longest entry,"
            f" but {reason}, so extraction should work."
        )
        return

    if opts["force"]:
        warn(f"the target directory is too deep ({worst} characters) but --force was given.")
        return

    die(
        EXIT_PRECHECK,
        f"[paths] this target is too deep for Windows: {safe(os.path.abspath(dest))}",
        f"        The longest file in the bundle adds ~{LONGEST_ENTRY_SUFFIX} characters, reaching about",
        f"        {worst} - past the {WINDOWS_PATH_LIMIT} character limit, so extraction would fail with",
        '        "OSError [WinError 206]". Either:',
        "          * use a short root, e.g. --dest C:/rsna",
        "          * or enable long paths and reboot:",
        "              reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem "
        "/v LongPathsEnabled /t REG_DWORD /d 1 /f",
        "          * or pass --force to start anyway.",
    )


# 3. Kaggle credentials
def credentials_path():
    return os.path.join(os.path.expanduser("~"), ".kaggle", "credentials.json")


def read_credentials(path):
    """Return the credentials dict, or None. Never logs anything but username."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for value in data.values():
        if isinstance(value, dict) and value.get("refresh_token"):
            return value
    if data.get("refresh_token"):
        return data
    return None


def headless():
    if os.name == "nt":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def login_command():
    """How to log in, or None when only the prepared venv can do it."""
    if which("kaggle"):
        return [which("kaggle"), "auth", "login"]
    uv = which("uv")
    if uv:
        return [uv, "tool", "run", "kaggle", "auth", "login"]
    return None


def do_login(command, opts):
    if headless():
        command = command + ["--no-launch-browser"]
    if not can_prompt(opts):
        die(
            EXIT_AUTH,
            "[auth] Kaggle credentials are missing and I am not allowed to start an",
            "       interactive login. Run this once in a terminal yourself:",
            f"         {command}",
            "       then run this script again.",
        )
    say(f"[auth] starting `{command}`")
    say("[auth] follow the prompt; nothing you type is written to the log.")
    try:
        code = subprocess.call(command)
    except OSError as error:
        die(EXIT_AUTH, f"[auth] could not run the login: {safe(error)}")
    if code != 0:
        die(
            EXIT_AUTH,
            f"[auth] the login did not complete (exit code {code}).",
            f"       Run `{command}` yourself and try again.",
        )
    if read_credentials(credentials_path()) is None:
        die(
            EXIT_AUTH,
            f"[auth] the login finished but {credentials_path()} is still unusable.",
        )


def deep_auth_check():
    """Ask the CLI to mint a token, but throw the token away."""
    cli = which("kaggle")
    if not cli:
        say("[auth] `kaggle` is not on PATH; skipping the extra token check.")
        return
    try:
        result = subprocess.run(
            [cli, "auth", "print-access-token"],
            # stdout=subprocess.PIPE,
            # stderr=subprocess.PIPE,
            check=False, capture_output=True,
        )
    except OSError as error:
        warn(f"could not run the token check: {safe(error)}")
        return
    token = (result.stdout or b"").strip()
    if result.returncode == 0 and token:
        say("[auth] the stored credentials still mint an access token.")
        return
    detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    warn("the stored credentials did not produce an access token%s" % ((": " + safe(detail[-1])) if detail else "."))
    say("[auth] if the download fails immediately, refresh the login with:")
    say("         ./download.sh --relogin")


def check_auth(opts):
    """Returns a login command to run later, or None when nothing is pending."""
    path = credentials_path()
    info = read_credentials(path)

    if info is not None and not opts["relogin"]:
        say(f"[auth] ok (account: {safe(info.get('username') or info.get('user_name') or 'unknown')})")
        if not opts["skip_auth_check"]:
            deep_auth_check()
        return None

    if info is not None:
        say(f"[auth] --relogin: ignoring the credentials at {path}.")
    else:
        say(f"[auth] no usable credentials at {path}")
    say("[auth] the dataset is only downloadable after joining the competition and")
    say("       accepting its rules on kaggle.com.")

    command = login_command()
    if command is not None:
        do_login(command, opts)
        return None
    say("[auth] neither `kaggle` nor `uv` is on PATH; the login will run from the")
    say("       virtualenv once it exists.")
    return "pending"


# 4. an interpreter with the downloader's dependencies
DEPENDENCY_IMPORTS = "import requests, dotenv, kagglesdk"
DEPS_BLOCK_RE = re.compile(r"(?ms)^[ \t]*dependencies[ \t]*=[ \t]*\[(.*?)\]")
QUOTED_RE = re.compile(r"""["']([^"']+)["']""")


def imports_ok(python):
    try:
        result = subprocess.run(
            [python, "-c", DEPENDENCY_IMPORTS],
            # stdout=subprocess.DEVNULL,
            # stderr=subprocess.DEVNULL,
            check=False, capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def project_dependencies():
    try:
        with open(os.path.join(ROOT, "pyproject.toml"), "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ["kagglesdk", "python-dotenv", "requests"]
    match = DEPS_BLOCK_RE.search(text)
    if not match:
        return ["kagglesdk", "python-dotenv", "requests"]
    deps = QUOTED_RE.findall(match.group(1))
    return deps or ["kagglesdk", "python-dotenv", "requests"]


def venv_python():
    if os.name == "nt":
        return os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return os.path.join(ROOT, ".venv", "bin", "python")


def pump(process):
    """Copy the child's output to our stdout and the log until it closes."""
    if process.stdout is None:
        return
    handle = process.stdout.fileno()
    while True:
        try:
            chunk = os.read(handle, 65536)
        except OSError:
            break
        if not chunk:
            break
        _emit(chunk)


def run_and_tee(command, cwd=None):
    say(f"[env] $ {' '.join(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as error:
        say(f"[env] failed to start: {safe(error)}")
        return 127
    pump(process)
    return process.wait()


def venv_fallback():
    python = venv_python()
    if not os.path.exists(python):
        say(f"[env] creating {os.path.join(ROOT, '.venv')} with {sys.executable}")
        code = run_and_tee([sys.executable, "-m", "venv", os.path.join(ROOT, ".venv")], cwd=ROOT)
        if code != 0 or not os.path.exists(python):
            die(
                EXIT_ERROR,
                "[env] could not create a virtualenv.",
                "       On Debian/Ubuntu this usually means the venv module is missing:",
                "         sudo apt install python3-venv python3-pip",
                "       Installing uv is usually the easier route - it also fetches the",
                "       Python version this project pins: https://docs.astral.sh/uv/",
            )

    if run_and_tee([python, "-m", "pip", "--version"]) != 0:
        run_and_tee([python, "-m", "ensurepip", "--upgrade"])
    if run_and_tee([python, "-m", "pip", "--version"]) != 0:
        die(
            EXIT_ERROR,
            "[env] the virtualenv has no working pip.",
            "       Debian/Ubuntu: sudo apt install python3-venv python3-pip",
        )

    deps = project_dependencies()
    say(f"[env] installing {' '.join(deps)}")
    if run_and_tee([python, "-m", "pip", "install", "--upgrade", "pip"] + deps) != 0:
        die(
            EXIT_ERROR,
            "[env] pip could not install the dependencies.",
            "       Behind a proxy or a mirror? Export PIP_INDEX_URL / HTTPS_PROXY first.",
            "       Or install uv, which reads the lock file and needs no pip at all:",
            "         https://docs.astral.sh/uv/",
        )
    return python


def ensure_environment():
    if imports_ok(sys.executable):
        say(f"[env] using {sys.executable}")
        return sys.executable

    uv = which("uv")
    if uv:
        try:
            version = subprocess.run(
                [uv, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            ).stdout.decode("utf-8", "replace").strip()
        except OSError:
            version = "uv"
        say(f"[env] found {safe(version)} at {safe(uv)}")
        say("[env] the current interpreter lacks the dependencies; syncing the locked")
        say("      environment with uv ...")
        code = run_and_tee([uv, "sync", "--frozen"], cwd=ROOT)
        python = venv_python()
        if code == 0 and os.path.exists(python) and imports_ok(python):
            say(f"[env] using {python}")
            return python
        warn("`uv sync --frozen` did not produce a usable environment; falling back.")

    if sys.version_info < (3, 9):  # noqa: UP036
        die(
            EXIT_ERROR,
            f"[env] Python {sys.version_info[0]}.{sys.version_info[1]} cannot install this project's dependencies, and uv is"
            f"      not installed to provide a newer one.",
            "      Install uv (https://docs.astral.sh/uv/) or Python 3.9+ and retry.",
        )
    return venv_fallback()


# 5. run lock
def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


def release_lock(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def take_lock(dest, opts, passthrough):
    if forwards_flag(passthrough, ("--verify-only", "--dry-run")):
        return None
    path = os.path.join(dest, ".download.lock")
    for attempt in (0, 1):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            owner = {}
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as existing:
                    owner = json.load(existing)
            except (OSError, ValueError):
                owner = {}
            owner_pid = owner.get("pid")
            same_host = owner.get("host") == socket.gethostname()
            if same_host and isinstance(owner_pid, int) and pid_alive(owner_pid) and not opts["force"]:
                die(
                    EXIT_PRECHECK,
                    "[lock] another download is already running against this directory:",
                    f"       pid {owner_pid} started {safe(owner.get('started'))}",
                    "       If that is wrong or the process is stuck elsewhere, re-run",
                    "       with --force.",
                )
            if attempt == 0:
                warn(
                    f"taking over {path} (left behind by pid {safe(owner.get('pid'))} on {safe(owner.get('host'))})"
                )
                release_lock(path)
                continue
            die(EXIT_PRECHECK, f"[lock] could not take the run lock at {path}.")
        except OSError as error:
            warn(f"could not create {path} ({safe(error)}); continuing without a lock.")
            return None

        payload = json.dumps(
            {"pid": os.getpid(), "host": socket.gethostname(), "started": utc_stamp(), "dest": dest}
        )
        os.write(handle, payload.encode("utf-8"))
        os.close(handle)
        return path
    return None


# 6. run the downloader with a log
def open_log(dest, opts):
    if opts["no_log"]:
        return None
    path = opts["log"]
    if path is None:
        stem = f"download-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        path = os.path.join(dest, stem + ".log")
    else:
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            path = os.path.join(path, f"download-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.log")

    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as error:
            warn(f"could not create {parent} ({safe(error)}); running without a log file.")
            return None

    for suffix in ("", "-2", "-3"):
        candidate = path if not suffix else f"{path}{suffix}"
        try:
            # "xb": a fresh file, never an existing one. An old log is never
            # truncated and never renamed.
            return open(candidate, "xb")
        except FileExistsError:
            continue
        except OSError as error:
            warn(f"could not write the log to {candidate} ({safe(error)}); continuing without it.")
            return None
    return None


def banner(dest, opts, passthrough):
    say(f"[bootstrap] {utc_stamp()}")
    say(f"[bootstrap] repo     {ROOT}")
    say(f"[bootstrap] dataset  {dest}")
    say(f"[bootstrap] command  {' '.join(sys.argv)}")
    if _LOG is not None:
        say(f"[bootstrap] log      {_LOG.name}")
    if passthrough:
        say(f"[bootstrap] forwarded to the downloader: {' '.join(passthrough)}")


def run_downloader(python, dest, passthrough):
    command = [python, DOWNLOADER] + list(passthrough) + ["--dest", dest]
    environment = dict(os.environ)
    environment["RAW_DIR"] = dest
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"

    say(f"[run] $ {' '.join(command)}")
    say("")
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except OSError as error:
        die(EXIT_ERROR, f"[run] could not start the downloader: {safe(error)}")

    interrupted = False

    def kill_quietly():
        try:
            process.kill()
        except OSError:
            pass

    try:
        pump(process)
    except KeyboardInterrupt:
        interrupted = True
        first = time.time()
        say("")
        say("[bootstrap] interrupt received: letting the downloader stop cleanly.")
        say("            Press Ctrl+C again to kill it now.")
        # Whatever the downloader does, we do not hang here forever.
        timer = threading.Timer(KILL_GRACE_SECONDS, kill_quietly)
        timer.daemon = True
        timer.start()
        try:
            while True:
                try:
                    pump(process)
                    break
                except KeyboardInterrupt:
                    # One Ctrl+C can reach us twice: uv re-signals the process
                    # group while we drain. Only a later press means the user
                    # really wants it dead now.
                    if time.time() - first >= FORCE_KILL_SECONDS:
                        say("[bootstrap] killing the downloader now.")
                        kill_quietly()
                        first = time.time()
        finally:
            timer.cancel()

    try:
        code = process.wait()
    except KeyboardInterrupt:
        kill_quietly()
        code = process.wait()
    say("")
    if interrupted and code not in (EXIT_OK, EXIT_INTERRUPT):
        code = EXIT_INTERRUPT  # killed on our way out; its own 130 never arrived
    return code


def report(code, started_at):
    elapsed = human_duration(time.time() - started_at)
    if code == EXIT_OK:
        say(f"[bootstrap] finished in {elapsed}: the dataset is complete and verified.")
    elif code == EXIT_INTERRUPT:
        say(f"[bootstrap] interrupted after {elapsed}. Nothing is lost: run the same command")
        say("            again and it continues where it stopped.")
    elif code == EXIT_PRECHECK:
        say(f"[bootstrap] refused to start ({elapsed}).")
    elif code == EXIT_AUTH:
        say(f"[bootstrap] stopped on authentication ({elapsed}).")
    else:
        say(f"[bootstrap] the downloader exited with code {code} after {elapsed}.")
        say("            Re-run the same command: finished files are skipped, and a")
        say("            final pass verifies every entry (look for [verified]).")
    if _LOG is not None:
        say(f"[bootstrap] log: {_LOG.name}")


# main
def main(argv):
    global _LOG

    opts, passthrough = parse_args(argv)
    if opts["help"]:
        print_help()
        return EXIT_OK

    started_at = time.time()
    dest = resolve_dest(opts)
    say(f"[dest] {dest}")

    _LOG = open_log(dest, opts)
    try:
        banner(dest, opts, passthrough)
        write_env_raw_dir(dest)

        if not os.path.exists(DOWNLOADER):
            die(EXIT_ERROR, f"[bootstrap] cannot find {DOWNLOADER}")
        if not can_prompt(opts):
            say("[bootstrap] running non-interactively (--yes or no terminal).")

        check_disk(dest, opts, passthrough)
        check_long_paths(dest, opts)
        pending_login = check_auth(opts)
        python = ensure_environment()

        if pending_login == "pending":
            do_login([python, "-m", "kaggle", "auth", "login"], opts)

        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as error:
            die(EXIT_PRECHECK, f"[dest] could not create {dest}: {safe(error)}")

        lock = take_lock(dest, opts, passthrough)
        try:
            code = run_downloader(python, dest, passthrough)
        finally:
            release_lock(lock)

        report(code, started_at)
        return code
    finally:
        if _LOG is not None:
            try:
                _LOG.close()
            except OSError:
                pass
            _LOG = None


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(file=sys.stderr, flush=True)
        print("[bootstrap] interrupted. Nothing is lost: run the same command again.",
              file=sys.stderr, flush=True)
        sys.exit(EXIT_INTERRUPT)
