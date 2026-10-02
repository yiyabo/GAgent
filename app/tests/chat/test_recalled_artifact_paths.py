from app.services.memory.artifact_recall import artifact_references


def test_relative_links_keep_source_session_and_missing_state(isolated_app_env,monkeypatch):
    from app.services import path_router
    monkeypatch.setattr(path_router,'_default_router',None)
    root=path_router.get_path_router().get_session_dir('source',create=True)
    (root/'workspace').mkdir();(root/'workspace/a.csv').write_text('n\n1\n')
    links='[a](workspace/a.csv) [duplicate](/artifacts/sessions/source/file?path=workspace%2Fa.csv) [gone](workspace/gone.csv)'
    refs=artifact_references('source','long history '*100+links)
    assert refs==[{'path':'workspace/a.csv','source_path':str(root/'workspace/a.csv'),'exists':True,'version_status':'untracked'},
                  {'path':'workspace/gone.csv','source_path':str(root/'workspace/gone.csv'),'exists':False,'version_status':'untracked'}]
    assert not (root/'workspace/gone.csv').exists()


def test_external_escape_and_other_session_links_are_not_rebound(isolated_app_env,monkeypatch):
    from app.services import path_router
    monkeypatch.setattr(path_router,'_default_router',None)
    text='[web](https://example.com/a.csv) [outside](workspace/../../other.csv) [other](/artifacts/sessions/other/file?path=workspace/a.csv)'
    assert artifact_references('source',text)==[]
