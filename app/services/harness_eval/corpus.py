"""Frozen small workflows with independent answer oracles, not model-written grades."""
from __future__ import annotations
import csv
import json
from pathlib import Path

CORPUS_VERSION='research-workflows-v1'
CASES={
 'table_clean':{'prompt':'Read input.csv, discard missing or non-numeric scores and duplicate IDs (keep first). Write clean.csv with id,group,score and summary.json with count and mean for each group.','outputs':['clean.csv','summary.json']},
 'figure':{'prompt':'Read input.csv, ignore missing/non-numeric scores. Calculate mean score for each group. Write summary.json and a bar chart chart.png (at least 320x240 pixels).','outputs':['summary.json','chart.png']},
 'fasta':{'prompt':'Read sequences.fasta. Keep only sequences at least 8 nucleotides long consisting of A,C,G,T, uppercase them, keep first for duplicate sequence strings. Write filtered.fasta and summary.json with retained_ids and count.','outputs':['filtered.fasta','summary.json']},
 'literature_report':{'prompt':'Using only references.json, write report.md with findings and limitations and evidence.json listing cited_ids. Cite source IDs in report; do not invent source IDs or claim a causal effect from observational evidence.','outputs':['report.md','evidence.json']},
 'correction':{'prompt':'The previous request was group means; the current request replaces it with group medians. Read input.csv, ignore missing/non-numeric scores. Overwrite summary.json with group count and median, without a mean field.','outputs':['summary.json']},
 'skill_reuse':{'prompt':'Read the local SKILL.md and follow its data cleaning procedure on input.csv. Write clean.csv and summary.json with count and mean for each group.','outputs':['clean.csv','summary.json']},
}
ROWS=[['id','group','score'],['a','A','10'],['b','A','20'],['a','A','90'],['c','B','30'],['d','B','50'],['e','A',''],['f','B','bad']]


def prepare(case_id:str,root:Path)->dict:
    root.mkdir(parents=True,exist_ok=True)
    with (root/'input.csv').open('w',newline='') as handle:csv.writer(handle).writerows(ROWS)
    (root/'sequences.fasta').write_text('>short\nACG\n>one\nACGTACGT\n>bad\nACGTNNNN\n>two\nTTTTCCCC\n>duplicate\nacgtacgt\n')
    (root/'references.json').write_text(json.dumps([
        {'id':'S1','design':'observational','finding':'Groups differed in a measured biomarker','limitation':'confounding'},
        {'id':'S2','design':'pilot','finding':'Assay feasibility established','limitation':'small sample'}]))
    (root/'SKILL.md').write_text('---\nname: clean-group-data\ndescription: Clean ID-based data before grouped statistics\n---\nRemove missing/non-numeric scores; deduplicate IDs keeping first; then compute group count and mean. Verify that output IDs are unique and counts match retained records.\n')
    if case_id=='correction':(root/'summary.json').write_text('{"A":{"mean":999},"B":{"mean":999}}')
    return CASES[case_id]


def check(case_id:str,root:Path)->dict:
    failures=[]
    def require(ok,message):
        if not ok:failures.append(message)
    try:
        if case_id in {'table_clean','skill_reuse'}:
            with (root/'clean.csv').open() as handle:rows=list(csv.DictReader(handle))
            require([(row['id'],row['group'],float(row['score'])) for row in rows]==[('a','A',10),('b','A',20),('c','B',30),('d','B',50)],'cleaned records must preserve the first valid input rows')
            expected={'A':{'count':2,'mean':15},'B':{'count':2,'mean':40}}
            require(json.loads((root/'summary.json').read_text())==expected,'group statistics after deduplication do not match')
        elif case_id=='figure':
            expected={'A':{'count':3,'mean':40},'B':{'count':2,'mean':40}}
            require(json.loads((root/'summary.json').read_text())==expected,'figure data statistics do not match')
            from PIL import Image,ImageStat
            with Image.open(root/'chart.png') as image:
                require(image.width>=320 and image.height>=240,'chart dimensions too small')
                require(max(ImageStat.Stat(image.convert('RGB')).stddev)>1,'chart is blank')
        elif case_id=='fasta':
            require((root/'filtered.fasta').read_text().split()==['>one','ACGTACGT','>two','TTTTCCCC'],'FASTA filtering or deduplication incorrect')
            require(json.loads((root/'summary.json').read_text())=={'retained_ids':['one','two'],'count':2},'FASTA summary incorrect')
        elif case_id=='correction':
            expected={'A':{'count':3,'median':20},'B':{'count':2,'median':40}}
            require(json.loads((root/'summary.json').read_text())==expected,'correction must use medians and remove stale means')
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
