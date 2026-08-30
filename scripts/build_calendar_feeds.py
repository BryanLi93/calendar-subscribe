from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


FEED_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
MAX_FEED_BYTES = 5 * 1024 * 1024


class CalendarMergeError(ValueError):
    """Raised when a source feed cannot be published safely."""


@dataclass(frozen=True)
class CalendarFeed:
    name: str
    path: Path
    lines: tuple[str, ...]
    events: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class BuildResult:
    feeds: int
    events: int
    files: tuple[str, ...]


def unfold_ical_lines(content: str, *, source: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    physical_lines = normalized.split("\n")
    while physical_lines and physical_lines[-1] == "":
        physical_lines.pop()

    logical_lines: list[str] = []
    for number, line in enumerate(physical_lines, start=1):
        if line.startswith((" ", "\t")):
            if not logical_lines:
                raise CalendarMergeError(
                    f"{source}:{number}: continuation line has no parent"
                )
            logical_lines[-1] += line[1:]
        elif line:
            logical_lines.append(line)
        else:
            raise CalendarMergeError(f"{source}:{number}: blank line inside calendar")
    return logical_lines


def _property_name(line: str) -> str:
    head = line.split(":", 1)[0]
    return head.split(";", 1)[0].upper()


def _direct_property(event: Sequence[str], name: str, *, source: str) -> str:
    target = name.upper()
    depth = 0
    values: list[str] = []
    for line in event[1:-1]:
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:"):
            depth -= 1
            continue
        if depth == 0 and _property_name(line) == target:
            if ":" not in line:
                raise CalendarMergeError(f"{source}: malformed {target} property")
            values.append(line.split(":", 1)[1])

    if len(values) != 1 or not values[0].strip():
        raise CalendarMergeError(
            f"{source}: each VEVENT must contain exactly one non-empty {target}"
        )
    return values[0].strip()


def validate_calendar(lines: Sequence[str], *, source: str) -> tuple[tuple[str, ...], ...]:
    if not lines:
        raise CalendarMergeError(f"{source}: empty calendar")

    stack: list[str] = []
    events: list[tuple[str, ...]] = []
    event_start: int | None = None
    calendar_count = 0

    for index, line in enumerate(lines):
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            component = upper[6:]
            if not component:
                raise CalendarMergeError(f"{source}: empty component name")
            if not stack:
                if component != "VCALENDAR":
                    raise CalendarMergeError(f"{source}: root component must be VCALENDAR")
                calendar_count += 1
            elif stack[0] != "VCALENDAR":
                raise CalendarMergeError(f"{source}: component outside VCALENDAR")
            if component == "VEVENT":
                if stack != ["VCALENDAR"]:
                    raise CalendarMergeError(f"{source}: nested VEVENT is not supported")
                event_start = index
            stack.append(component)
            continue

        if upper.startswith("END:"):
            component = upper[4:]
            if not stack or stack[-1] != component:
                expected = stack[-1] if stack else "nothing"
                raise CalendarMergeError(
                    f"{source}: END:{component} does not match {expected}"
                )
            if component == "VEVENT":
                if event_start is None:
                    raise CalendarMergeError(f"{source}: VEVENT end without start")
                event = tuple(lines[event_start : index + 1])
                _direct_property(event, "UID", source=source)
                _direct_property(event, "DTSTART", source=source)
                events.append(event)
                event_start = None
            stack.pop()
            continue

        if not stack:
            raise CalendarMergeError(f"{source}: content outside VCALENDAR")

    if stack:
        raise CalendarMergeError(f"{source}: unclosed component {stack[-1]}")
    if calendar_count != 1:
        raise CalendarMergeError(f"{source}: expected exactly one VCALENDAR")
    if not any(line.upper() == "VERSION:2.0" for line in lines):
        raise CalendarMergeError(f"{source}: VERSION:2.0 is required")
    return tuple(events)


def read_feed(path: Path) -> CalendarFeed:
    if path.stat().st_size > MAX_FEED_BYTES:
        raise CalendarMergeError(f"{path}: feed exceeds {MAX_FEED_BYTES} bytes")
    name = path.stem
    if name == "all" or not FEED_NAME_PATTERN.fullmatch(name):
        raise CalendarMergeError(
            f"{path}: feed name must match {FEED_NAME_PATTERN.pattern} and cannot be all"
        )
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CalendarMergeError(f"{path}: feed must be UTF-8") from exc
    if "\x00" in content:
        raise CalendarMergeError(f"{path}: NUL byte is not allowed")
    lines = tuple(unfold_ical_lines(content, source=str(path)))
    events = validate_calendar(lines, source=str(path))
    return CalendarFeed(name=name, path=path, lines=lines, events=events)


def fold_ical_line(line: str) -> list[str]:
    if len(line.encode("utf-8")) <= 75:
        return [line]

    parts: list[str] = []
    current = ""
    for character in line:
        candidate = current + character
        if len(candidate.encode("utf-8")) > 75:
            if not current:
                raise CalendarMergeError("one character exceeds the iCalendar line limit")
            parts.append(current)
            current = " " + character
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def render_lines(lines: Iterable[str]) -> str:
    folded = [part for line in lines for part in fold_ical_line(line)]
    return "\r\n".join(folded) + "\r\n"


def merge_events(feeds: Sequence[CalendarFeed]) -> list[tuple[str, ...]]:
    seen: dict[str, str] = {}
    merged: list[tuple[str, ...]] = []
    for feed in feeds:
        for event in feed.events:
            uid = _direct_property(event, "UID", source=str(feed.path))
            previous = seen.get(uid)
            if previous is not None:
                raise CalendarMergeError(
                    f"duplicate UID {uid!r} in feeds {previous!r} and {feed.name!r}"
                )
            seen[uid] = feed.name
            merged.append(event)

    return sorted(
        merged,
        key=lambda event: (
            _direct_property(event, "DTSTART", source="merged calendar"),
            _direct_property(event, "UID", source="merged calendar"),
        ),
    )


def render_merged_calendar(events: Sequence[Sequence[str]], calendar_name: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//BryanLi93//Calendar Subscribe//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{calendar_name}",
        "X-PUBLISHED-TTL:PT6H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]
    for event in events:
        lines.extend(event)
    lines.append("END:VCALENDAR")
    return render_lines(lines)


def render_index(feeds: Sequence[CalendarFeed], total_events: int) -> str:
    items = [
        (
            "all",
            "全部日历",
            total_events,
        ),
        *[(feed.name, feed.name, len(feed.events)) for feed in feeds],
    ]
    rows = "\n".join(
        "        <li>"
        f'<a href="{html.escape(name)}.ics">{html.escape(label)}.ics</a>'
        f" <span>{count} 个事件</span>"
        "</li>"
        for name, label, count in items
    )
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Calendar Subscribe</title>
    <style>
      :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
      body {{ max-width: 44rem; margin: 4rem auto; padding: 0 1.25rem; line-height: 1.6; }}
      h1 {{ margin-bottom: .25rem; }}
      ul {{ padding-left: 1.25rem; }}
      li {{ margin: .65rem 0; }}
      span {{ opacity: .65; margin-left: .4rem; }}
      code {{ overflow-wrap: anywhere; }}
    </style>
  </head>
  <body>
    <h1>Calendar Subscribe</h1>
    <p>公开、只读的 iCalendar 订阅。修改请回到各自事实源。</p>
    <ul>
{rows}
    </ul>
    <p>Apple Calendar：文件 → 新建日历订阅，然后粘贴对应 <code>https://</code> 地址。</p>
  </body>
</html>
"""


def build(
    feeds_dir: Path,
    output_dir: Path,
    *,
    calendar_name: str = "全部日历",
    write: bool = True,
) -> BuildResult:
    feed_paths = sorted(feeds_dir.glob("*.ics"))
    if not feed_paths:
        raise CalendarMergeError(f"{feeds_dir}: no .ics feeds found")
    feeds = tuple(read_feed(path) for path in feed_paths)
    merged_events = merge_events(feeds)

    artifacts: dict[str, str] = {
        f"{feed.name}.ics": render_lines(feed.lines) for feed in feeds
    }
    artifacts["all.ics"] = render_merged_calendar(merged_events, calendar_name)
    artifacts["index.html"] = render_index(feeds, len(merged_events))

    if write:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{output_dir.name}-", dir=output_dir.parent
        ) as temporary_directory:
            stage = Path(temporary_directory)
            for name, content in artifacts.items():
                with (stage / name).open("w", encoding="utf-8", newline="") as handle:
                    handle.write(content)
            if output_dir.exists():
                shutil.rmtree(output_dir)
            shutil.copytree(stage, output_dir)

    return BuildResult(
        feeds=len(feeds),
        events=len(merged_events),
        files=tuple(sorted(artifacts)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, copy, and merge public iCalendar feeds."
    )
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--feeds-dir", type=Path, default=root / "feeds")
    parser.add_argument("--output-dir", type=Path, default=root / "public")
    parser.add_argument("--calendar-name", default="全部日历")
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = build(
            arguments.feeds_dir.resolve(),
            arguments.output_dir.resolve(),
            calendar_name=arguments.calendar_name,
            write=not arguments.check,
        )
    except (CalendarMergeError, OSError) as exc:
        print(f"calendar publish failed: {exc}", file=sys.stderr)
        return 1

    action = "checked" if arguments.check else "built"
    print(
        f"{action}; feeds={result.feeds}; events={result.events}; "
        f"files={','.join(result.files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
