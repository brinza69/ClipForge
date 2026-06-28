"""Poll both backends (3060 :8420, 1660 :8421) and write a combined status file
that the live page reads same-origin (no CORS). Runs forever."""
import json, time, re, os, subprocess, urllib.request

OUT = r"D:\clipforge\data\exports\dual_status.json"
DISPATCH_LOG = r"D:\clipforge\data\dispatch.log"
_CF = 0x08000000 if os.name == "nt" else 0


def detect_backends():
    """One (gpu_name, url) per detected GPU — GPU 0 -> :8420, GPU 1 -> :8421.
    A single-GPU PC gets a single backend (no phantom 2nd card on the dashboard)."""
    names = []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, creationflags=_CF,
        ).stdout
        names = [n.strip() for n in out.splitlines() if n.strip()]
    except Exception:
        pass
    if not names:
        names = ["GPU 0"]
    ports = [8420, 8421]
    return [(names[i], f"http://127.0.0.1:{ports[i]}") for i in range(min(len(ports), len(names)))]


BACKENDS = detect_backends()


def job_row_map():
    m = {}
    try:
        with open(DISPATCH_LOG, encoding="utf-8", errors="ignore") as f:
            for line in f:
                mo = re.search(r"row (\d+) -> (\w+)", line)
                if mo:
                    m[mo.group(2)] = int(mo.group(1))
    except Exception:
        pass
    return m


while True:
    rowmap = job_row_map()
    out = {"updated": time.strftime("%H:%M:%S"), "backends": []}
    for gpu, url in BACKENDS:
        info = {"gpu": gpu, "running": False}
        try:
            arr = json.load(urllib.request.urlopen(url + "/api/jobs/?status=running", timeout=8))
            j = next((x for x in arr if x.get("type") == "parallel_pipeline"), None)
            if j:
                info.update(running=True, job_id=j["id"], progress=j.get("progress") or 0,
                            message=j.get("progress_message"), row=rowmap.get(j["id"]))
            else:
                info["message"] = "idle (waiting for next row)"
        except Exception as e:
            info["message"] = "backend unreachable"
            info["error"] = str(e)[:40]
        out["backends"].append(info)
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(json.dumps(out))
    except Exception:
        pass
    time.sleep(3)
