"""Exercise release assembly against a real synthetic Git index."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest


COPY = runpy.run_path(str(Path(__file__).resolve().parents[3] /
                         '.github/scripts/build-release.py'))['copy_tracked_skill']


class ReleaseSkillAllowlist(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)
        subprocess.run(['git', 'init', '-q'], check=True)
        self.source = Path('skills/video-factory-setup')
        self.source.mkdir(parents=True)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def track(self, path):
        subprocess.run(['git', 'add', '--', str(path)], check=True)

    def test_untracked_credentials_and_interpreter_cache_never_ship(self):
        source = self.source / 'SKILL.md'
        source.write_text('synthetic tracked source')
        self.track(source)
        (self.source / '.env').write_text('SYNTHETIC_NOT_A_REAL_SECRET')
        cache = self.source / '__pycache__'
        cache.mkdir()
        (cache / 'module.pyc').write_bytes(b'synthetic cache')
        COPY(Path('output'))
        self.assertEqual([p.name for p in Path('output').iterdir()], ['SKILL.md'])
        self.assertEqual(Path('output/SKILL.md').read_text(), source.read_text())

    def test_tracked_link_cannot_export_private_target(self):
        Path('private.txt').write_text('SYNTHETIC_PRIVATE_TARGET')
        source = self.source / 'linked.txt'
        source.symlink_to('../../private.txt')
        self.track(source)
        with self.assertRaisesRegex(AssertionError, 'REGULAR'):
            COPY(Path('output'))

    def test_missing_tracked_source_is_not_a_valid_empty_package(self):
        with self.assertRaisesRegex(AssertionError, 'TRACKED_SKILL_REQUIRED'):
            COPY(Path('output'))
