"""Private answers: imported by the supervisor only after execution."""
import csv,json
from pathlib import Path
ORACLE_VERSION="required-fields-v4"

def _required_statistics(actual,expected,*,forbidden=()):
    if not isinstance(actual,dict) or set(actual)-{'row_count'}!=set(expected):return False
    # The public contract permits metadata; only this independently checkable
    # field is recognized. Unknown groups and arbitrary metadata still fail.
    if 'row_count' in actual:
        count=actual['row_count']
        if isinstance(count,bool) or not isinstance(count,(int,float)) or count!=sum(fields['count'] for fields in expected.values()):return False
    for group,fields in expected.items():
        values=actual[group]
        if not isinstance(values,dict) or any(key in values for key in forbidden):return False
        for key,wanted in fields.items():
            value=values.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or value!=wanted:return False
    return True


def check(case_id:str,root:Path)->dict:
    failures=[]
    def require(ok,message):
        if not ok:failures.append(message)
    try:
        if case_id in {'table_clean','skill_reuse'}:
            with (root/'clean.csv').open() as handle:rows=list(csv.DictReader(handle))
            require([(row['id'],row['group'],float(row['score'])) for row in rows]==[('a','A',10),('b','A',20),('c','B',30),('d','B',50)],'cleaned records must preserve the first valid input rows')
            expected={'A':{'count':2,'mean':15},'B':{'count':2,'mean':40}}
            require(_required_statistics(json.loads((root/'summary.json').read_text()),expected),'group statistics after deduplication do not match')
        elif case_id=='figure':
            expected={'A':{'count':3,'mean':40},'B':{'count':2,'mean':40}}
            require(_required_statistics(json.loads((root/'summary.json').read_text()),expected),'figure data statistics do not match')
            from PIL import Image,ImageStat
            with Image.open(root/'chart.png') as image:
                require(image.width>=320 and image.height>=240,'chart dimensions too small')
                require(max(ImageStat.Stat(image.convert('RGB')).stddev)>1,'chart is blank')
        elif case_id=='fasta':
            require((root/'filtered.fasta').read_text().split()==['>one','ACGTACGT','>two','TTTTCCCC'],'FASTA filtering or deduplication incorrect')
            summary=json.loads((root/'summary.json').read_text())
            require(summary.get('retained_ids')==['one','two'] and isinstance(summary.get('count'),(int,float)) and summary['count']==2,'FASTA summary incorrect')
        elif case_id=='correction':
            expected={'A':{'count':3,'median':20},'B':{'count':2,'median':40}}
            require(_required_statistics(json.loads((root/'summary.json').read_text()),expected,forbidden=('mean',)),'correction must use medians and remove stale means')
        else:
            evidence=json.loads((root/'evidence.json').read_text())
            require(set(evidence.get('cited_ids',[]))=={'S1','S2'},'citation inventory is incomplete or fabricated')
            text=(root/'report.md').read_text()
            require('S1' in text and 'S2' in text,'report misses required source citations')
            require(any(term in text.lower() for term in ['limitation','局限','限制']),'report misses limitations')
            require(len(text)>100,'report too short')
    except Exception as exc:failures.append(type(exc).__name__+': '+str(exc)[:300])
    return {'passed':not failures,'failures':failures,'manual_review_required':case_id in {'figure','literature_report'},
            'checked_dimensions':['file_content','declared_values'] if case_id not in {'figure','literature_report'} else ['file_readability','data_values','citation_inventory']}
