import pathlib
lines = [x.strip() for x in pathlib.Path("names.txt").read_text().splitlines() if x.strip()]
assert lines == ["alpha", "beta"], f"got {lines!r}"
