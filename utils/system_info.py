import os
import subprocess
import sys


def session_info():
    if sys.platform == "win32" or os.name == "nt":
        return {"session": "windows", "display": "Win32", "global_overlay_supported": True}
    session = os.getenv("XDG_SESSION_TYPE", "unknown")
    return {"session": session, "display": os.getenv("DISPLAY", ""),
            "global_overlay_supported": session.lower() == "x11"}


def gpu_memory_mb():
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=1, check=True,
                                creationflags=creationflags)
        return int(result.stdout.splitlines()[0])
    except Exception:
        return None
