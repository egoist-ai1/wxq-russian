"""Безопасность очистки временной лаборатории сборщика."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT / 'scripts'
for _path in (str(PROJECT), str(SCRIPTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import build_pilot_carrier as bp


class BuilderCleanupTests(unittest.TestCase):
    def test_removes_only_requested_task_and_keeps_tasks_root(self):
        thread_a = 'a' * 32
        thread_b = 'b' * 32
        with tempfile.TemporaryDirectory() as fixture:
            root = Path(fixture)
            tasks = root / 'wxq-pilot' / 'tasks'
            lab_a = tasks / thread_a / 'work' / 'pilot-test'
            lab_b = tasks / thread_b / 'work' / 'pilot-test'
            lab_a.mkdir(parents=True)
            lab_b.mkdir(parents=True)
            marker_b = lab_b / 'marker.txt'
            marker_b.write_text('B', encoding='utf-8')

            with patch.object(bp.tempfile, 'gettempdir', return_value=fixture):
                bp.cleanup_lab_task(lab_a, thread_a)

                self.assertFalse((tasks / thread_a).exists())
                self.assertTrue(marker_b.exists())
                self.assertTrue(tasks.exists())

                bp.cleanup_lab_task(lab_b, thread_b)

            self.assertTrue(tasks.exists())

    def test_bad_thread_ids_raise_without_removing_marker(self):
        valid_thread = 'c' * 32
        with tempfile.TemporaryDirectory() as fixture:
            root = Path(fixture)
            tasks = root / 'wxq-pilot' / 'tasks'
            lab = tasks / valid_thread / 'work' / 'pilot-test'
            lab.mkdir(parents=True)
            marker = lab / 'marker.txt'
            marker.write_text('preserve', encoding='utf-8')

            with patch.object(bp.tempfile, 'gettempdir', return_value=fixture):
                for thread_id in ('', '..', 'not-32-hex'):
                    with self.subTest(thread_id=thread_id):
                        with self.assertRaises(bp.PilotError):
                            bp.cleanup_lab_task(lab, thread_id)
                        self.assertTrue(marker.exists())

    def test_mismatched_and_sibling_labs_are_preserved(self):
        thread_a = 'd' * 32
        thread_b = 'e' * 32
        with tempfile.TemporaryDirectory() as fixture:
            root = Path(fixture)
            tasks = root / 'wxq-pilot' / 'tasks'
            mismatched_lab = tasks / thread_a / 'other' / 'pilot'
            sibling_lab = tasks / thread_b / 'work' / 'pilot'
            mismatched_lab.mkdir(parents=True)
            sibling_lab.mkdir(parents=True)
            marker_mismatched = mismatched_lab / 'marker.txt'
            marker_sibling = sibling_lab / 'marker.txt'
            marker_mismatched.write_text('mismatch', encoding='utf-8')
            marker_sibling.write_text('sibling', encoding='utf-8')

            with patch.object(bp.tempfile, 'gettempdir', return_value=fixture):
                with self.assertRaises(bp.PilotError):
                    bp.cleanup_lab_task(mismatched_lab, thread_a)
                with self.assertRaises(bp.PilotError):
                    bp.cleanup_lab_task(sibling_lab, thread_a)

            self.assertTrue(marker_mismatched.exists())
            self.assertTrue(marker_sibling.exists())

    def test_normalized_temp_alias_is_accepted(self):
        thread_id = 'f' * 32
        with tempfile.TemporaryDirectory() as fixture:
            root = Path(fixture)
            (root / 'sub').mkdir()
            tasks = root / 'wxq-pilot' / 'tasks'
            lab = tasks / thread_id / 'work' / 'pilot-test'
            lab.mkdir(parents=True)
            (lab / 'marker.txt').write_text('alias', encoding='utf-8')
            temp_alias = str(root / 'sub' / '..')
            lexical_lab = Path(temp_alias) / 'wxq-pilot' / 'tasks' / thread_id / 'work' / 'pilot-test'

            with patch.object(bp.tempfile, 'gettempdir', return_value=temp_alias):
                bp.cleanup_lab_task(lexical_lab, thread_id)

            self.assertFalse((tasks / thread_id).exists())
            self.assertTrue(tasks.exists())


if __name__ == '__main__':
    unittest.main()
