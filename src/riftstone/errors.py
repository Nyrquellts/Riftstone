"""Errors a modder can read.  Every failure the toolchain expects is a RiftError."""
from __future__ import annotations


class RiftError(Exception):
    """A failure with a message written for the person running the tool."""


class FormatError(RiftError):
    """Input bytes do not match the format they claim to be."""

    def __init__(self, fmt: str, message: str, offset: int | None = None):
        self.fmt = fmt
        self.offset = offset
        where = f" at byte 0x{offset:x}" if offset is not None else ""
        super().__init__(f"{fmt}: {message}{where}")


class UnsafePathError(RiftError):
    """A name or path would escape its folder or cannot exist on Windows."""


class BuildError(RiftError):
    """A mod or archive cannot be built as asked."""


class ParamError(RiftError):
    """A parameter (YAML) file does not fit the structure the game expects."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None, source: str | None = None):
        self.line = line
        self.column = column
        self.source = source
        where = ""
        if line is not None:
            where = f"line {line}" + (f", column {column}" if column is not None else "")
            where = f"{source}: {where}: " if source else f"{where}: "
        elif source:
            where = f"{source}: "
        super().__init__(f"{where}{message}")
