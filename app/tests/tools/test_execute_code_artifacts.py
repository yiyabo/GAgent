from tool_box.tools_impl.execute_code.artifacts import snapshot,observe


def test_file_changes_are_observed_without_promoting_unchanged_inputs_or_scratch(tmp_path):
    input_path=tmp_path/'input.csv';input_path.write_text('original')
    before=snapshot(str(tmp_path))
    (tmp_path/'scratch').mkdir();(tmp_path/'scratch'/'kernel.json').write_text('{}')
    output=tmp_path/'summary.json';output.write_text('{"count":2}')
    result=observe({'success':True},before,str(tmp_path))
    assert result['produced_files']==[str(output)] and str(input_path) not in result['produced_files']


def test_in_place_change_is_observed_and_empty_outputs_are_not(tmp_path):
    target=tmp_path/'table.csv';target.write_text('old')
    before=snapshot(str(tmp_path));target.write_text('new data')
    (tmp_path/'empty.json').write_text('')
    assert observe({'success':True},before,str(tmp_path))['produced_files']==[str(target)]


def test_read_only_cell_has_no_produced_artifacts(tmp_path):
    (tmp_path/'input.json').write_text('{}')
    before=snapshot(str(tmp_path))
    assert 'produced_files' not in observe({'success':True},before,str(tmp_path))
