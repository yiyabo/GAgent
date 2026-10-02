"""External delivery is a file fact, never a claim that a CLI read it."""
import hashlib
from pathlib import Path
from app.services.foundation.settings import get_settings
from app.repository import skill_learning as repository
from app.repository.context_recall import session_scope
from app.services.chat_run_state import chat_run_claim
from .context import in_scope
from .models import SkillDraft


def external_files(context,work_dir):
    if not getattr(get_settings(),'skill_context_progressive_enabled',False):return []
    selected=(context.get('learned_skill_context') or {}).get('skills',[])
    scope=session_scope(context.get('session_id'))
    if not scope or not work_dir:return []
    refs=[]
    for item in selected:
        skill=repository.get_skill(item['id'])
        if not skill or not in_scope(skill,scope) or skill['current_version']!=item['version'] or skill['state'] in {'disabled','suspended'}:raise ValueError('external_skill_version_changed')
        body=SkillDraft.model_validate(skill['draft']).markdown(skill['public_name']);checksum=hashlib.sha256(body.encode()).hexdigest()
        path=Path(work_dir)/'.gagent-skills'/skill['id']/f'v{item["version"]}-{checksum}.md';path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:raise ValueError('external_skill_file_changed')
        if not path.exists():path.write_text(body)
        refs.append({'id':skill['id'],'version':item['version'],'content_hash':checksum,'path':str(path.resolve())})
        claim=chat_run_claim.get()
        if claim:repository.record_loaded(skill['id'],item['version'],claim[0],delivery='external_delivered')
    return refs
