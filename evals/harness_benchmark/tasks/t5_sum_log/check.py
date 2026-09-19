import pathlib
v = float(pathlib.Path("total.txt").read_text().strip())
assert abs(v - 17.0) < 0.001, f"got {v!r}"
