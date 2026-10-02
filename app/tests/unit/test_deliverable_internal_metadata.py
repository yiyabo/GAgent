"""Bookkeeping must not occupy the user's artifact list or its result limit."""
import json

import pytest

from app.config.deliverable_config import DeliverableSettings, RESEARCH_MODULES
from app.routers.artifact_routes.deliverable_store import _materialize_deliverable_items
from app.services.deliverables.publisher import DeliverablePublisher


@pytest.mark.parametrize('legacy_manifest', [False, True])
def test_internal_map_is_hidden_in_legacy_and_scanned_lists(tmp_path, legacy_manifest):
    module = tmp_path / 'image_tabular'
    module.mkdir()
    mapping = module / '.source_owners.json'
    mapping.write_text('{"clean.csv":"original.csv"}')
    (module / 'clean.csv').write_text('id,score\na,10\n')
    manifest = {'items': [{'module': 'image_tabular', 'path': 'image_tabular/' + name}
                          for name in ('.source_owners.json', 'clean.csv')]} if legacy_manifest else {}
    items, _ = _materialize_deliverable_items(manifest=manifest, files_root=tmp_path,
                                             include_draft=True, module_filter=None, limit=1)
    assert [item.name for item in items] == ['clean.csv']
    assert json.loads(mapping.read_text()) == {'clean.csv': 'original.csv'}


def test_republish_drops_old_trusted_map_record_without_deleting_map(tmp_path):
    root = tmp_path / 'latest'
    module = root / 'image_tabular'
    module.mkdir(parents=True)
    mapping = module / '.source_owners.json'
    mapping.write_text('{}')
    (module / 'clean.csv').write_text('id,score\na,10\n')
    publisher = DeliverablePublisher(settings=DeliverableSettings(modules=RESEARCH_MODULES),
                                     project_root=tmp_path, runtime_dir=tmp_path / 'runtime')
    items = publisher._collect_latest_items(
        latest_root=root, previous_manifest={'items': [{'module': 'image_tabular',
        'path': 'image_tabular/.source_owners.json', 'trusted_publish': True}]},
        updated_items=[], fallback_timestamp='2026-10-03T00:00:00Z', fallback_status='final')
    assert [item['path'] for item in items] == ['image_tabular/clean.csv']
    assert mapping.is_file()
