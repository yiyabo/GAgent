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
    if case_id in {'table_clean','figure','correction','skill_reuse'}:
        with (root/'input.csv').open('w',newline='') as handle:csv.writer(handle).writerows(ROWS)
    if case_id=='fasta':
        (root/'sequences.fasta').write_text('>short\nACG\n>one\nACGTACGT\n>bad\nACGTNNNN\n>two\nTTTTCCCC\n>duplicate\nacgtacgt\n')
    if case_id=='literature_report':
        (root/'references.json').write_text(json.dumps([
            {'id':'S1','design':'observational','finding':'Groups differed in a measured biomarker','limitation':'confounding'},
            {'id':'S2','design':'pilot','finding':'Assay feasibility established','limitation':'small sample'}]))
    if case_id=='skill_reuse':
        (root/'SKILL.md').write_text('---\nname: clean-group-data\ndescription: Clean ID-based data before grouped statistics\n---\nRemove missing/non-numeric scores; deduplicate IDs keeping first; then compute group count and mean. Verify that output IDs are unique and counts match retained records.\n')
    if case_id=='correction':(root/'summary.json').write_text('{"A":{"mean":999},"B":{"mean":999}}')
    return CASES[case_id]


