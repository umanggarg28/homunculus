"""File tools for the agent's workspace."""

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import IO, Any, NoReturn

import mcp_types as mcp

from homunculus.tools import Tool, ToolError

# Opening one folder of a path: only a folder, and never through a link.
FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
LINK = "{path} is a link, or goes through one; links are not followed."
# The largest file the tools read whole. A command can make a huge file that takes no
# disk space, which read, edit or search would otherwise try to hold in memory.
MAX_FILE_BYTES = 10 * 1024 * 1024

# Enough to answer most questions without flooding the model's context.
MAX_SEARCH_RESULTS = 50

# What each kind of file tool does to its surroundings, in the Model Context Protocol's
# terms. None of them reaches outside the workspace.
READS = mcp.ToolAnnotations(read_only_hint=True, open_world_hint=False)
# write can replace a file and delete removes one. Repeating either changes nothing more.
REPLACES = mcp.ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
# edit replaces text, and rename removes the old name. Neither is safe to repeat blindly.
CHANGES = mcp.ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def file_tools(workspace: Path) -> list[Tool]:
    """The file tools, each confined to `workspace`."""
    return Files(workspace).tools()


def _file_tool(
    name: str,
    title: str,
    description: str,
    arguments: dict[str, str],
    annotations: mcp.ToolAnnotations,
    run: Callable[[dict[str, Any]], str],
    paths: tuple[str, ...] = ("path",),
) -> Tool:
    """A file tool whose arguments are all required strings, described by `arguments`."""
    definition = mcp.Tool(
        name=name,
        title=title,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                argument: {"type": "string", "description": text}
                for argument, text in arguments.items()
            },
            "required": list(arguments),
        },
        annotations=annotations,
    )
    return Tool(definition, run, workspace_paths=paths)


class Files:
    def __init__(self, workspace: Path) -> None:
        self._root = workspace.resolve()

    def tools(self) -> list[Tool]:
        in_workspace = "Path relative to the workspace."
        return [
            _file_tool(
                "read",
                "Read file",
                "Read a text file in the workspace. Lines are numbered from 1. "
                "Use it freely, before asking the operator something a file may answer.",
                {"path": in_workspace},
                READS,
                self.read,
            ),
            _file_tool(
                "edit",
                "Edit file",
                "Replace a piece of text in a workspace file. The old text must appear "
                "exactly once. Use it when only part of a file needs to change.",
                {
                    "path": in_workspace,
                    "old": "The exact text to replace.",
                    "new": "The text to put in its place.",
                },
                CHANGES,
                self.edit,
            ),
            _file_tool(
                "list",
                "List folder",
                "List the folders and files in a workspace folder. Folders end with '/'. "
                "Use it freely to see what the workspace holds.",
                {"path": "Folder relative to the workspace; '.' is the top."},
                READS,
                self.list_folder,
            ),
            _file_tool(
                "search",
                "Search files",
                "Find lines containing some text, in every file under a workspace folder. "
                "Matching ignores case. Each result is 'file:line: text'. "
                "Use it freely to find something before asking the operator.",
                {
                    "path": "Folder to search; '.' is the whole workspace.",
                    "text": "The text to look for.",
                },
                READS,
                self.search,
            ),
            _file_tool(
                "delete",
                "Delete file",
                "Delete one file in the workspace. Folders and links cannot be deleted. "
                "It cannot be undone, so the operator approves each delete. Only use it "
                "when the operator asked for the file to be deleted, or for a file you made "
                "that is no longer needed.",
                {"path": in_workspace},
                REPLACES,
                self.delete,
            ),
            _file_tool(
                "rename",
                "Move file",
                "Move or rename one file in the workspace. Never overwrites an existing file. "
                "Use it when a file needs a new name or place.",
                {
                    "path": "The file to move, relative to the workspace.",
                    "to": "Its new path, relative to the workspace.",
                },
                CHANGES,
                self.rename,
                paths=("path", "to"),
            ),
            _file_tool(
                "write",
                "Write file",
                "Create or overwrite a text file in the workspace. Use it to save work. "
                "Overwriting replaces the whole file, so read a file first if it may hold "
                "something worth keeping.",
                {"path": in_workspace, "content": "The full file content."},
                REPLACES,
                self.write,
            ),
        ]

    # Every operation reaches its file one folder at a time from the workspace's own
    # folder handle, refusing a link at every step (the `openat` pattern, with
    # O_NOFOLLOW). The sandbox can change the workspace while a tool works on it, so a
    # path is never checked first and opened afterwards: a link swapped in between the
    # two could lead anywhere.

    def write(self, arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        *folders, name = _parts(path)
        with self._folder(path, folders, create=True) as folder:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            with _opened(path, name, flags, folder) as file:
                file.write(arguments["content"].encode())
        return f"Wrote {path}."

    def read(self, arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        *folders, name = _parts(path)
        with (
            self._folder(path, folders) as folder,
            _opened(path, name, os.O_RDONLY, folder) as file,
        ):
            lines = file.read().decode().splitlines()
        return "\n".join(f"{number}\t{line}" for number, line in enumerate(lines, start=1))

    def edit(self, arguments: dict[str, Any]) -> str:
        path, old, new = arguments["path"], arguments["old"], arguments["new"]
        *folders, name = _parts(path)
        with self._folder(path, folders) as folder, _opened(path, name, os.O_RDWR, folder) as file:
            text = file.read().decode()
            count = text.count(old)
            if count != 1:
                raise ToolError(f"the text appears {count} times in {path}; it must appear once.")
            file.seek(0)
            file.truncate()
            file.write(text.replace(old, new).encode())
        return f"Edited {path}."

    def list_folder(self, arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        with self._folder(path, _parts(path), want_folder=True) as folder:
            names = sorted(
                f"{entry.name}/" if entry.is_dir(follow_symlinks=False) else entry.name
                for entry in os.scandir(folder)
            )
        return "\n".join(names) or "(empty)"

    def search(self, arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        parts = _parts(path)
        wanted = arguments["text"].lower()
        matches: list[tuple[str, int, str]] = []
        with self._folder(path, parts, want_folder=True) as folder:
            walk = os.fwalk(".", dir_fd=folder, follow_symlinks=False)
            for here, _, names, here_fd in walk:
                for name in names:
                    relative = PurePosixPath(*parts, here, name).as_posix()
                    try:
                        with _opened(relative, name, os.O_RDONLY, here_fd) as file:
                            lines = file.read().decode().splitlines()
                    except (ToolError, UnicodeDecodeError):
                        continue  # a link, or not a text file
                    for number, line in enumerate(lines, start=1):
                        if wanted in line.lower():
                            matches.append((relative, number, line))
        shown = [f"{file}:{number}: {line}" for file, number, line in sorted(matches)]
        if len(shown) > MAX_SEARCH_RESULTS:
            more = len(shown) - MAX_SEARCH_RESULTS
            return "\n".join(shown[:MAX_SEARCH_RESULTS]) + f"\n({more} more not shown)"
        return "\n".join(shown) or "No matches."

    def delete(self, arguments: dict[str, Any]) -> str:
        path = arguments["path"]
        *folders, name = _parts(path)
        with self._folder(path, folders) as folder:
            _plain_file(path, name, folder)
            os.unlink(name, dir_fd=folder)
        return f"Deleted {path}."

    def rename(self, arguments: dict[str, Any]) -> str:
        path, to = arguments["path"], arguments["to"]
        *folders, name = _parts(path)
        *new_folders, new_name = _parts(to)
        with (
            self._folder(path, folders) as folder,
            self._folder(to, new_folders, create=True) as new_folder,
        ):
            _plain_file(path, name, folder)
            if _exists(new_name, new_folder):
                raise ToolError(f"{to} already exists; rename will not overwrite it.")
            os.rename(name, new_name, src_dir_fd=folder, dst_dir_fd=new_folder)
        return f"Moved {path} to {to}."

    @contextmanager
    def _folder(
        self, path: str, parts: list[str], create: bool = False, want_folder: bool = False
    ) -> Iterator[int]:
        """A handle on the folder at `parts` inside the workspace, reached one folder at a
        time with no link followed, making missing folders when `create` is set."""
        handle = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, dir_fd=handle)
                try:
                    inner = os.open(part, FOLDER_FLAGS, dir_fd=handle)
                except OSError as error:
                    _explain(path, part, handle, error, want_folder)
                os.close(handle)
                handle = inner
            yield handle
        finally:
            os.close(handle)


def _parts(path: str) -> list[str]:
    """The folders and name in a workspace path. The gate refuses these first; this is
    the tools' own fence behind it."""
    relative = PurePosixPath(path)
    parts = [part for part in relative.parts if part != "."]
    if relative.is_absolute() or ".." in parts:
        raise ToolError(f"{path} is outside the workspace.")
    return parts or ["."]


@contextmanager
def _opened(path: str, name: str, flags: int, folder: int) -> Iterator[IO[bytes]]:
    """A regular file in `folder`, opened without following a link and without waiting:
    a named pipe would otherwise hold the open until something wrote to it."""
    try:
        handle = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=folder)
    except OSError as error:
        _explain(path, name, folder, error, want_folder=False)
    file = os.fdopen(handle, "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb")
    with file:
        found = os.fstat(file.fileno())
        if stat.S_ISDIR(found.st_mode):
            raise ToolError(f"{path} does not exist.")
        if not stat.S_ISREG(found.st_mode):
            raise ToolError(f"{path} is not a regular file.")
        if found.st_size > MAX_FILE_BYTES and not flags & os.O_TRUNC:
            limit = f"the file tools read files up to {MAX_FILE_BYTES}."
            raise ToolError(f"{path} is {found.st_size} bytes; {limit}")
        os.set_blocking(file.fileno(), True)
        yield file


def _explain(path: str, part: str, folder: int, error: OSError, want_folder: bool) -> NoReturn:
    """Turn a refusal to open `part` into the reason, in words for the model."""
    try:
        found = os.stat(part, dir_fd=folder, follow_symlinks=False).st_mode
    except FileNotFoundError:
        raise ToolError(f"{path} does not exist.") from error
    if stat.S_ISLNK(found):
        raise ToolError(LINK.format(path=path)) from error
    if stat.S_ISDIR(found):
        raise ToolError(f"{path} is a folder.") from error
    if not stat.S_ISREG(found) and not want_folder:
        raise ToolError(f"{path} is not a regular file.") from error
    if want_folder:
        raise ToolError(f"{path} is not a folder.") from error
    raise ToolError(f"{path} does not exist.") from error


def _plain_file(path: str, name: str, folder: int) -> None:
    """Refuse anything but a regular file: a link, so the operation cannot reach the file
    it points to, or a folder."""
    try:
        found = os.stat(name, dir_fd=folder, follow_symlinks=False).st_mode
    except FileNotFoundError as error:
        raise ToolError(f"{path} does not exist.") from error
    if stat.S_ISLNK(found):
        raise ToolError(f"{path} is a link; links cannot be deleted or renamed.")
    if stat.S_ISDIR(found):
        raise ToolError(f"{path} is a folder; only single files can be deleted.")


def _exists(name: str, folder: int) -> bool:
    try:
        os.stat(name, dir_fd=folder, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True
