"""Curated procedure inputs for an isolated recommendation experiment, no answers."""
import hashlib,json
from uuid import uuid4
from .fixtures import CASES


def seed(session_id):
    from app.database import get_db
    from app.repository.skill_learning import now
    from app.services.skill_learning.models import SkillDraft
    with get_db() as con:
        for case,spec in CASES.items():
            identity=uuid4().hex
            if case=="skill_reuse":spec={**spec,"prompt":CASES["table_clean"]["prompt"]}
            draft=SkillDraft(name='workflow-'+case.replace('_','-'),description='Reusable procedure for '+case.replace('_',' '),domain='routine' if case!='literature_report' else 'research',when_to_use=spec['prompt'],inputs=['The task provided local input files'],steps=[{'instruction':spec['prompt'],'evidence_ids':[]}],verification=['Read back all outputs and verify them against the requested transformations.'],limitations=['This is a curated evaluation fixture, not a business-verified method.'],keywords=[case,*case.split('_'),*{'table_clean':['csv','score','duplicate','mean'],'figure':['chart','plot','mean'],'fasta':['sequence','nucleotides','fasta'],'literature_report':['references','findings','limitations'],'correction':['medians','median','replaces'],'skill_reuse':['clean','csv','duplicate']}[case]],pitfalls=[])
            data=draft.model_dump();hash_value=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
            con.execute('INSERT INTO learned_skills(id,owner_id,session_id,current_version,state,review_status,created_at,updated_at) VALUES(?,?,?,1,?,?,?,?)',(identity,'harness-eval',session_id,'stable','accepted',now(),now()))
            con.execute('INSERT INTO learned_skill_versions VALUES(?,?,?,?,?,?)',(identity,1,json.dumps(data,ensure_ascii=False),hash_value,json.dumps({'evaluation_fixture':True,'validated_dimensions':[],'requires_human_review':True}),now()))
