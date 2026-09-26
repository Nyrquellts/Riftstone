"""Terminal output in Riftstone's colours when the console supports them, plain text otherwise.

Obsidian and rift cyan: cyan marks what is live, done or selected, ruby only a failure's marker, and
the words themselves stay light grey so every line reads the same without colour (NO_COLOR, a pipe or
an old console get the same wording).
"""
from __future__ import annotations

import os
import sys
import time

_ENABLED: bool | None = None

# The design kit's values (truecolor escapes: ESC[38;2;R;G;Bm).
CYAN = (0, 240, 255)
TEXT = (226, 232, 240)
SECONDARY = (148, 157, 175)
RUBY = (220, 38, 38)
# The twin fangs, one chrome shade per row, top to bottom.
FANG_ROWS = ((120, 131, 145), (226, 232, 240), (167, 178, 193), (120, 131, 145), (89, 99, 114), (53, 61, 74))
FANG = ("  ██▌       ▐██",
        "   ██▌     ▐██",
        "    ██▌   ▐██",
        "     █▌   ▐█",
        "      ▌   ▐",
        "      ▏   ▕")


def _enable() -> bool:
    global _ENABLED
    if _ENABLED is not None:
        return _ENABLED
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        _ENABLED = False
        return False
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not k.GetConsoleMode(h, ctypes.byref(mode)) or not k.SetConsoleMode(h, mode.value | 0x0004):
                _ENABLED = False         # ENABLE_VIRTUAL_TERMINAL_PROCESSING refused: an old console
                return False
        except Exception:  # noqa: BLE001 - colour is optional
            _ENABLED = False
            return False
    _ENABLED = True
    return True


def _rgb(rgb: tuple[int, int, int], text: str) -> str:
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[0m" if _enable() else text


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _enable() else text


def cyan(t: str) -> str:
    return _rgb(CYAN, t)


def ruby(t: str) -> str:
    return _rgb(RUBY, t)


def mist(t: str) -> str:
    return _rgb(SECONDARY, t)


def bold(t: str) -> str:
    return _c("1", t)


# The older names, kept for every command that prints with them: accents are cyan now, failures ruby.
gold = cyan
moss = cyan
ember = ruby


def _sym(fancy: str, plain: str) -> str:
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return fancy if "utf" in enc else plain


def banner(subtitle: str = "", mark: bool = False) -> None:
    """``NryQ // RIFTSTONE`` with a subtitle; ``mark`` adds the twin fangs above it."""
    if mark and _sym("x", "") == "x":
        print()
        for row, colour in zip(FANG, FANG_ROWS):
            print(_rgb(colour, row))
        print()
    print(_rgb(TEXT, "  NryQ // ") + cyan(bold("RIFTSTONE")) + (mist("  " + subtitle) if subtitle else ""))
    print(mist("  Dragon's Dogma: Dark Arisen  |  Dragon's Dogma Online"))


def status(tag: str, label: str, detail: str = "") -> None:
    """``[PASS] label   detail``: the tag in cyan, or ruby for FAIL."""
    painted = ruby(f"[{tag}]") if tag == "FAIL" else cyan(f"[{tag}]")
    pad = " " * max(1, 9 - len(tag) - 2)
    print(f"  {painted}{pad}{label:<16} {detail}")


def ok(msg: str) -> None:
    print(cyan(_sym("✔", "OK")) + " " + msg)


def warn(msg: str) -> None:
    print(_rgb(TEXT, _sym("▲", "!!")) + " " + msg)


def fail(msg: str) -> None:
    print(ruby(_sym("✖", "XX")) + " " + msg, file=sys.stderr)


def step(msg: str) -> None:
    print(cyan(_sym("▸", ">")) + " " + msg)


def info(msg: str) -> None:
    print("  " + mist(msg))


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def pause_if_own_console() -> None:
    """Keep a drag-and-drop / double-click window open so its result can be read.

    A console opened just for this launch has only cmd.exe and python attached;
    a terminal someone typed into has its shell too, and must not wait.
    """
    if os.name != "nt" or not os.environ.get("RIFTSTONE_LAUNCHER") or not sys.stdin.isatty():
        return
    try:
        import ctypes

        buf = (ctypes.c_uint32 * 8)()
        attached = ctypes.windll.kernel32.GetConsoleProcessList(buf, 8)
    except Exception:  # noqa: BLE001
        return
    if 0 < attached <= 2:
        try:
            input(mist("\nPress Enter to close..."))
        except (EOFError, KeyboardInterrupt):
            pass


class Progress:
    """A one-line progress bar; silent when output is not a console."""

    def __init__(self, total: int, label: str):
        self.total = max(total, 1)
        self.label = label
        self.n = 0
        self.live = sys.stdout.isatty()
        self.last = 0.0
        self.start = time.time()

    def advance(self, k: int = 1, note: str = "") -> None:
        self.n += k
        now = time.time()
        if not self.live or (now - self.last < 0.08 and self.n < self.total):
            return
        self.last = now
        width = 28
        filled = int(width * self.n / self.total)
        bar = cyan(_sym("█", "#") * filled) + mist(_sym("░", "-") * (width - filled))
        pct = 100 * self.n // self.total
        tail = (note[:38] + "…") if len(note) > 39 else note
        sys.stdout.write(f"\r  {bar} {pct:3d}%  {self.label} {mist(tail):<40}")
        sys.stdout.flush()

    def done(self, msg: str | None = None) -> None:
        if self.live:
            sys.stdout.write("\r" + " " * 100 + "\r")
            sys.stdout.flush()
        if msg:
            ok(f"{msg} {mist(f'({time.time() - self.start:.1f}s)')}")
