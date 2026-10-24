"""File tools for the agent's workspace."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import mcp_types as mcp

from homunculus.tools import Tool, ToolError

# Enough to answer most questions without flooding the model's context.
MAX_SEARCH_RESULTS = 50

# What each kind of file tool does to its surroundings, in the Model Context Protocol's
# terms. None of them reaches outside the workspace.
READS = mcp.ToolAnnotations(read_only_hint=True, open_world_hint=False)
# write and edit can replace text, and delete removes a file, so all three are destructive.
REPLACES = mcp.ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
# rename never overwrites, so nothing is lost.
MOVES = mcp.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


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
        annotations=annotations.model_copy(update={"title": title}),
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
                REPLACES,
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
                MOVES,
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

    def write(self, arguments: dict[str, Any]) -> str:
        target = self._resolve(arguments["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(arguments["content"])
        return f"Wrote {arguments['path']}."

    def _resolve(self, path: str) -> Path:
        """The real location of `path`, which must be inside the workspace."""
        target = (self._root / path).resolve()
        if not target.is_relative_to(self._root):
            raise ToolError(f"{path} is outside the workspace.")
        return target

    def _plain_file(self, path: str) -> Path:
        """An existing regular file. Links are refused, so an operation on a link can
        never reach the file it points to."""
        if (self._root / path).is_symlink():
            raise ToolError(f"{path} is a link; links cannot be deleted or renamed.")
        target = self._resolve(path)
        if target.is_dir():
            raise ToolError(f"{path} is a folder; only single files can be deleted.")
        return self._existing_file(path)

    def _existing_file(self, path: str) -> Path:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"{path} does not exist.")
        return target

    def read(self, arguments: dict[str, Any]) -> str:
        lines = self._existing_file(arguments["path"]).read_text().splitlines()
        return "\n".join(f"{number}\t{line}" for number, line in enumerate(lines, start=1))

    def list_folder(self, arguments: dict[str, Any]) -> str:
        folder = self._resolve(arguments["path"])
        if not folder.is_dir():
            raise ToolError(f"{arguments['path']} is not a folder.")
        names = sorted(
            f"{entry.name}/" if entry.is_dir() else entry.name for entry in folder.iterdir()
        )
        return "\n".join(names) or "(empty)"

    def edit(self, arguments: dict[str, Any]) -> str:
        path, old, new = arguments["path"], arguments["old"], arguments["new"]
        target = self._existing_file(path)
        text = target.read_text()
        count = text.count(old)
        if count != 1:
            raise ToolError(f"the text appears {count} times in {path}; it must appear once.")
        target.write_text(text.replace(old, new))
        return f"Edited {path}."

    def search(self, arguments: dict[str, Any]) -> str:
        folder = self._resolve(arguments["path"])
        wanted = arguments["text"].lower()
        matches: list[str] = []
        for file in sorted(folder.rglob("*")):
            if not file.is_file() or not file.resolve().is_relative_to(self._root):
                continue
            try:
                lines = file.read_text().splitlines()
            except UnicodeDecodeError:
                continue  # not a text file
            relative = file.relative_to(self._root).as_posix()
            for number, line in enumerate(lines, start=1):
                if wanted in line.lower():
                    matches.append(f"{relative}:{number}: {line}")
        if len(matches) > MAX_SEARCH_RESULTS:
            shown = matches[:MAX_SEARCH_RESULTS]
            return "\n".join(shown) + f"\n({len(matches) - MAX_SEARCH_RESULTS} more not shown)"
        return "\n".join(matches) or "No matches."

    def delete(self, arguments: dict[str, Any]) -> str:
        self._plain_file(arguments["path"]).unlink()
        return f"Deleted {arguments['path']}."

    def rename(self, arguments: dict[str, Any]) -> str:
        source = self._plain_file(arguments["path"])
        target = self._resolve(arguments["to"])
        if target.exists() or target.is_symlink():
            raise ToolError(f"{arguments['to']} already exists; rename will not overwrite it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        return f"Moved {arguments['path']} to {arguments['to']}."
