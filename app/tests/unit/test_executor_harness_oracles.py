"""Independent oracle accepts verified row metadata without relaxing statistics."""
from copy import deepcopy
import json

import pytest

from app.services.harness_eval.oracles import ORACLE_VERSION, check


MEANS = {'A': {'count': 2, 'mean': 15}, 'B': {'count': 2, 'mean': 40}}
MEDIANS = {'A': {'count': 3, 'median': 20}, 'B': {'count': 2, 'median': 40}}


def evaluate(tmp_path, summary, case='table_clean'):
    (tmp_path / 'clean.csv').write_text('id,group,score\na,A,10\nb,A,20\nc,B,30\nd,B,50\n')
    (tmp_path / 'summary.json').write_text(json.dumps(summary))
    return check(case, tmp_path)['passed']


@pytest.mark.parametrize('case', ['table_clean', 'skill_reuse'])
def test_verified_row_count_is_allowed_without_changing_group_requirements(tmp_path, case):
    assert ORACLE_VERSION == 'required-fields-v4'
    assert evaluate(tmp_path, {**MEANS, 'row_count': 4}, case)
    assert evaluate(tmp_path, MEANS, case)


@pytest.mark.parametrize('value', [3, 5, -1, True, False, '4', None, float('nan')])
def test_wrong_or_non_numeric_row_count_is_rejected(tmp_path, value):
    assert not evaluate(tmp_path, {**MEANS, 'row_count': value})


@pytest.mark.parametrize('summary', [
    {'row_count': 4, 'A': {'count': 2, 'mean': 15}},
    {**MEANS, 'row_count': 4, 'C': {'count': 0, 'mean': 0}},
    {**MEANS, 'row_count': 4, 'total_rows': 4},
    {**MEANS, 'row_count': 4, 'A': {'count': 3, 'mean': 15}},
    {**MEANS, 'row_count': 4, 'B': {'count': True, 'mean': 40}},
    {**MEANS, 'row_count': 4, 'B': {'count': 2, 'mean': True}},
    {**MEANS, 'row_count': 4, 'A': {'count': 2, 'mean': 40}},
    {**MEANS, 'row_count': 4, 'A': {'count': 2, 'mean': '15'}},
])
def test_row_metadata_does_not_hide_missing_extra_or_wrong_groups(tmp_path, summary):
    assert not evaluate(tmp_path, summary)


def test_correction_counts_valid_input_rows_and_preserves_no_mean_rule(tmp_path):
    assert evaluate(tmp_path, {**MEDIANS, 'row_count': 5}, 'correction')
    assert not evaluate(tmp_path, {**MEDIANS, 'row_count': 4}, 'correction')
    wrong = deepcopy(MEDIANS)
    wrong['A']['median'] = 40
    assert not evaluate(tmp_path, {**wrong, 'row_count': 5}, 'correction')
    wrong['A']['median'] = True
    assert not evaluate(tmp_path, {**wrong, 'row_count': 5}, 'correction')
    stale = deepcopy(MEDIANS)
    stale['A']['mean'] = 40
    assert not evaluate(tmp_path, {**stale, 'row_count': 5}, 'correction')
    assert not evaluate(tmp_path, {**MEDIANS, 'row_count': 5, 'mean': 40}, 'correction')


def test_valid_metadata_does_not_rescue_wrong_cleaned_records(tmp_path):
    assert evaluate(tmp_path, {**MEANS, 'row_count': 4})
    (tmp_path / 'clean.csv').write_text('id,group,score\na,A,999\nb,A,20\nc,B,30\nd,B,50\n')
    assert not check('table_clean', tmp_path)['passed']
