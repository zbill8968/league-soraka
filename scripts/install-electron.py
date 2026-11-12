import os, sys, zipfile, urllib.request

ver = "45.2.3"
url = f"https://npmmirror.com/mirrors/electron/{ver}/electron-v{ver}-win32-x64.zip"
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dest = os.path.join(root, "node_modules", "electron")
tmp = os.path.join(root, ".cache", f"electron-v{ver}-win32-x64.zip")
os.makedirs(os.path.dirname(tmp), exist_ok=True)

if not os.path.exists(tmp) or os.path.getsize(tmp) < 150_000_000:
    print("downloading", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        read = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if total and read % (20 << 20) < (1 << 20):
                print(f"  {read/1048576:.0f}/{total/1048576:.0f} MB", flush=True)
print("downloaded", os.path.getsize(tmp), flush=True)

dist = os.path.join(dest, "dist")
os.makedirs(dist, exist_ok=True)
with zipfile.ZipFile(tmp) as z:
    z.extractall(dist)
print("extracted", flush=True)

with open(os.path.join(dest, "path.txt"), "w", encoding="utf-8") as f:
    f.write("electron.exe")
print("done", os.path.exists(os.path.join(dist, "electron.exe")), flush=True)
