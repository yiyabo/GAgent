import pathlib
got = [x.strip() for x in pathlib.Path("output.txt").read_text().splitlines() if x.strip()]
assert got == ["apple", "banana", "cherry", "date"], f"got {got!r}"
