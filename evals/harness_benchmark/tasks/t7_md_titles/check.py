import pathlib
s = pathlib.Path("summary.md").read_text()
for t in ["Alpha Report", "Beta Findings", "Gamma Notes"]:
    assert t in s, f"missing {t}"
