"""Tolerant tool/sandbox comparison ported from Crossledger 9a38525.

Both witnesses here share the runner domain; disagreement is not spoof attribution.
"""

import re
import shlex
from dataclasses import dataclass
from datetime import timedelta
from io import StringIO

from inspect_ai.event import SandboxEvent, ToolEvent
from inspect_ai.tool import ToolResult
from inspect_scout import Transcript

_SHELLS = {"bash", "sh", "zsh", "dash"}
_INTERPRETERS = {"python", "python3", "python3.13", "python3.12", "node"}
_WINDOW_SLACK = timedelta(seconds=2)
_HEAD_LINES = 3
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_LINE_BREAK_CHARS = ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
_LINE_OBJECT_WORK = 64


def unwrap_shell(cmd: str | None) -> str | None:
    """Return the payload of `bash --login -c '<payload>'` or `python -c '<code>'`.
    Anything else is returned unchanged. Unparseable strings are returned as-is."""
    if not cmd:
        return cmd
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return cmd
    if not argv:
        return cmd
    exe = argv[0].rsplit("/", 1)[-1]
    if (exe in _SHELLS or exe in _INTERPRETERS) and "-c" in argv:
        i = argv.index("-c")
        if i + 1 < len(argv):
            return argv[i + 1]
    return cmd


def normalize_cmd(cmd: str | None) -> str:
    return re.sub(r"\s+", " ", (cmd or "").strip())


def tool_command(tool: ToolEvent) -> str | None:
    """The command a tool call claims to have run, for the tools that carry one."""
    for key in ("cmd", "command", "code"):
        value = tool.arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def result_text(result: ToolResult) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    text = getattr(result, "text", None)
    return text if isinstance(text, str) else str(result)


@dataclass(frozen=True, slots=True)
class _PreparedOutput:
    head: tuple[str, ...]
    joined: str


def _prepare_output(output: str | None) -> _PreparedOutput:
    text = output or ""
    joined = StringIO()
    head: list[str] = []
    has_line = False
    start = 0
    for match in _LINE_BREAK.finditer(text):
        raw_line = text[start : match.start()]
        start = match.end()
        if not raw_line.strip():
            continue
        line = raw_line.rstrip()
        if has_line:
            joined.write("\n")
        joined.write(line)
        has_line = True
        if len(head) < _HEAD_LINES:
            head.append(line)
    if start < len(text):
        raw_line = text[start:]
        if raw_line.strip():
            line = raw_line.rstrip()
            if has_line:
                joined.write("\n")
            joined.write(line)
            if len(head) < _HEAD_LINES:
                head.append(line)
    return _PreparedOutput(head=tuple(head), joined=joined.getvalue())


def _prepared_outputs_consistent(
    tool_out: _PreparedOutput, sandbox_out: _PreparedOutput
) -> bool | None:
    if not tool_out.head or not sandbox_out.head:
        return None
    if tool_out.joined in sandbox_out.joined or sandbox_out.joined in tool_out.joined:
        return True
    return tool_out.head == sandbox_out.head


def outputs_consistent(tool_out: str, sandbox_out: str | None) -> bool | None:
    """None when either side is empty (nothing to compare). True on containment in
    either direction or when the first few non-empty lines agree. False otherwise."""
    return _prepared_outputs_consistent(_prepare_output(tool_out), _prepare_output(sandbox_out))


def sandbox_candidates(transcript: Transcript, tool: ToolEvent) -> list[SandboxEvent]:
    """Every SandboxEvent in the transcript that could correspond to this tool call.
    Filtered by the tool's time window when both ends are known; otherwise all."""
    sandboxes = [e for e in transcript.events if isinstance(e, SandboxEvent)]
    if tool.completed is None:
        return sandboxes
    lo = tool.timestamp - _WINDOW_SLACK
    hi = tool.completed + _WINDOW_SLACK
    return [s for s in sandboxes if lo <= s.timestamp <= hi]


def compare_pair(tool: ToolEvent, command: str, sandbox: SandboxEvent) -> tuple[str, str]:
    """One tool call against one sandbox execution. Aggregating over candidates would
    let a single genuine match lend support to unrelated executions in the window."""
    if normalize_cmd(unwrap_shell(sandbox.cmd)) != normalize_cmd(command):
        return (
            "unmatched",
            "Sandbox execution in the tool's window ran a different command; it neither corroborates nor contradicts this call",
        )
    consistent = outputs_consistent(result_text(tool.result), sandbox.output)
    if consistent is True:
        return "supported", "Command and tolerant output comparison agree within the runner domain"
    if consistent is None:
        return (
            "not_assessable",
            "Same command, but one side recorded no output; nothing to compare within the runner domain",
        )
    return (
        "contradicted",
        "Same command but the recorded outputs differ within the runner domain; not proof of spoofing",
    )


def check_transcript(transcript: Transcript):
    """Yield (tool, [(sandbox, outcome, detail), ...]) for every tool carrying a command."""
    for tool in (e for e in transcript.events if isinstance(e, ToolEvent)):
        command = tool_command(tool)
        if command is None:
            continue
        candidates = sandbox_candidates(transcript, tool)
        yield tool, [(s, *compare_pair(tool, command, s)) for s in candidates]
