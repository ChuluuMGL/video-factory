"""Legacy bind-only callers stay compatible; setup-run now supports creation."""
import json
from pathlib import Path
import tempfile
import unittest

from video_factory.onboarding import SessionStore, FIELDS
from video_factory.setup_cli import interactive


class SetupEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self.temp.name) / 'setup.json')
        self.store.start()
        self.answers = json.loads((Path(__file__).resolve().parents[1] / 'examples/setup/answers.json').read_text())

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_install_rejects_create_and_default_selects_bind(self):
        preceding = dict(list(self.answers.items())[:17])
        self.store.answer(preceding, 0)
        output = []; answers = iter(['create', '', 'bascnCustomer', ':quit'])
        result = interactive(self.store, read=lambda _: next(answers), write=output.append, existing_base_only=True)
        project = self.store.read()['configuration']['project']
        self.assertEqual((project['base_mode'], project['base_target']), ('bind', 'bascnCustomer'))
        self.assertIn('未保存此项：SETUP_EXISTING_BASE_REQUIRED', output)
        self.assertTrue(result['interrupted'])
        self.assertEqual(FIELDS['project.base_mode'].default, 'create')  # Offline planner stays compatible.

    def test_complete_create_draft_requires_explicit_change_and_target_reentry(self):
        self.store.answer(self.answers, 0)
        original = self.store.read()
        result = interactive(self.store, read=lambda _: ':quit', write=lambda _: None, existing_base_only=True)
        self.assertTrue(result['interrupted'])
        self.assertEqual(self.store.read(), original)
        answers = iter(['', ':quit'])
        interactive(self.store, read=lambda _: next(answers), write=lambda _: None, existing_base_only=True)
        project = self.store.read()['configuration']['project']
        self.assertEqual(project['base_mode'], 'bind')
        self.assertNotIn('base_target', project)
        answers = iter(['bascnCustomer'])
        result = interactive(self.store, read=lambda _: next(answers), write=lambda _: None, existing_base_only=True)
        self.assertEqual(result['status'], 'plan_ready')

    def test_offline_planner_complete_create_draft_still_returns_without_reprompt(self):
        self.store.answer(self.answers, 0)
        result = interactive(self.store, read=lambda _: self.fail('unexpected prompt'), write=lambda _: None)
        self.assertEqual(result['status'], 'plan_ready')
        self.assertEqual(self.store.read()['configuration']['project']['base_mode'], 'create')
