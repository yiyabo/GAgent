import pathlib
got = [x.strip() for x in pathlib.Path("top.txt").read_text().splitlines() if x.strip()]
assert got == ["the 4", "fox 3", "dog 2"], f"got {got!r}"
