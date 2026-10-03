"""Synthetic, dedicated CI database only. Exercise the same failure cases on PG."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import psycopg

if os.environ.get('GITHUB_ACTIONS') != 'true': raise SystemExit('CLOUD_CI_ONLY')
sys.path.insert(0, str(Path('packages/video-factory-core/tests').resolve()))
from video_factory.postgres_store import PostgresStore
import test_feishu_bridge as fixtures
import test_base_results

root = Path(os.environ['RUNNER_TEMP'])
dsn_file = root/'base-results-fixture-dsn'
dsn = 'postgresql://fixture:synthetic-ci-only-password@127.0.0.1:5432/vf_results'
dsn_file.write_text(dsn); dsn_file.chmod(0o600)


def install(path, deployment, password):
    # This database is created by this job's disposable service declaration,
    # never from a caller-provided DSN, customer configuration or secret.
    with psycopg.connect(dsn) as connection:
        connection.execute('DROP SCHEMA public CASCADE')
        connection.execute('CREATE SCHEMA public')
    return PostgresStore.install(path, deployment, password, dsn_file=dsn_file)


with patch.object(fixtures.RuntimeStore, 'install', side_effect=install):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(test_base_results.ResultsTests))
raise SystemExit(not result.wasSuccessful())
