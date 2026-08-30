from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import build_calendar_feeds as calendar


def make_feed(uid: str, title: str, start: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Test//Calendar//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "DTSTAMP:20260830T120000Z\r\n"
        f"SUMMARY:{title}\r\n"
        f"DTSTART;VALUE=DATE:{start}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


class CalendarFeedTests(unittest.TestCase):
    def test_build_keeps_individual_feeds_and_merges_all_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds"
            feeds.mkdir()
            (feeds / "projects.ics").write_text(
                make_feed("project-1@example", "项目阶段", "20260901"),
                encoding="utf-8",
            )
            (feeds / "travel.ics").write_text(
                make_feed("travel-1@example", "旅行", "20261001"),
                encoding="utf-8",
            )

            result = calendar.build(feeds, root / "public")
            merged = (root / "public" / "all.ics").read_text(encoding="utf-8")

            self.assertEqual(result.feeds, 2)
            self.assertEqual(result.events, 2)
            self.assertEqual(merged.count("BEGIN:VEVENT"), 2)
            self.assertTrue((root / "public" / "projects.ics").is_file())
            self.assertTrue((root / "public" / "travel.ics").is_file())
            self.assertTrue((root / "public" / "index.html").is_file())

    def test_duplicate_uid_across_feeds_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds"
            feeds.mkdir()
            (feeds / "projects.ics").write_text(
                make_feed("same@example", "项目", "20260901"),
                encoding="utf-8",
            )
            (feeds / "travel.ics").write_text(
                make_feed("same@example", "旅行", "20261001"),
                encoding="utf-8",
            )

            with self.assertRaises(calendar.CalendarMergeError):
                calendar.build(feeds, root / "public")

    def test_invalid_calendar_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds"
            feeds.mkdir()
            (feeds / "broken.ics").write_text(
                "BEGIN:VCALENDAR\nVERSION:2.0\n",
                encoding="utf-8",
            )

            with self.assertRaises(calendar.CalendarMergeError):
                calendar.build(feeds, root / "public")

    def test_invalid_or_reserved_feed_name_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds"
            feeds.mkdir()
            (feeds / "all.ics").write_text(
                make_feed("all@example", "全部", "20260901"),
                encoding="utf-8",
            )

            with self.assertRaises(calendar.CalendarMergeError):
                calendar.build(feeds, root / "public")

    def test_utf8_folded_lines_respect_75_octets(self) -> None:
        rendered = calendar.render_lines(["SUMMARY:" + "阶段" * 40])
        physical = rendered.split("\r\n")
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in physical if line))
        self.assertTrue(all(line.startswith(" ") for line in physical[1:-1]))

    def test_rebuild_removes_stale_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feeds = root / "feeds"
            feeds.mkdir()
            (feeds / "projects.ics").write_text(
                make_feed("project@example", "项目", "20260901"),
                encoding="utf-8",
            )
            output = root / "public"
            output.mkdir()
            (output / "stale.txt").write_text("stale", encoding="utf-8")

            calendar.build(feeds, output)

            self.assertFalse((output / "stale.txt").exists())
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["all.ics", "index.html", "projects.ics"],
            )


if __name__ == "__main__":
    unittest.main()

