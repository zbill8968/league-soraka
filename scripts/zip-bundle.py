"""Create a UTF-8 filename zip for the portable Windows bundle.

Compress-Archive on Windows PowerShell 5.1 can mark filenames as UTF-8 while
writing OEM bytes, which garbles Chinese launcher names on extraction.
Python's zipfile writes proper UTF-8 names and sets the UTF-8 flag.
"""

import os
import sys
import zipfile

if len(sys.argv) != 3:
    raise SystemExit("usage: zip-bundle.py <bundle-dir> <out.zip>")

bundle = os.path.abspath(sys.argv[1])
out = os.path.abspath(sys.argv[2])
parent = os.path.dirname(bundle)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for root, dirs, files in os.walk(bundle):
        dirs.sort()
        files.sort()
        for name in dirs:
            full = os.path.join(root, name)
            arc = os.path.relpath(full, parent).replace(os.sep, "/") + "/"
            archive.write(full, arc)
        for name in files:
            full = os.path.join(root, name)
            arc = os.path.relpath(full, parent).replace(os.sep, "/")
            archive.write(full, arc)

print(out)
