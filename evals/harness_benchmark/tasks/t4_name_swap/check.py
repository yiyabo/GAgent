import pathlib
got = [x.strip() for x in pathlib.Path("first_last.txt").read_text().splitlines() if x.strip()]
assert got == ["Xiaoming Wang", "Lei Li", "Jing Chen"], f"got {got!r}"
