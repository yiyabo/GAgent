import pathlib
v = pathlib.Path("result.txt").read_text().strip()
assert v == "10", f"expected 10 got {v!r}"
