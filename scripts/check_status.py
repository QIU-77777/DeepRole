# -*- coding: utf-8 -*-
with open("data/runtime/characters/narrator/status.md", "rb") as f:
    data = f.read()

print("file size:", len(data))
lines = data.split(b"\n")
print("total lines:", len(lines))
for i, line in enumerate(lines, 1):
    try:
        text = line.decode("utf-8")
        if i in (21, 25):
            print(f"line {i} OK: {text[:60]}")
    except UnicodeDecodeError as e:
        print(f"line {i} CORRUPT: {e} | hex: {line[:40].hex()}")
