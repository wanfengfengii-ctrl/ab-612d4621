"""One-shot verification service.

Runs, in order:
  1. build check  - byte-compile every Python module;
  2. code tests   - the test suite (pytest when available, unittest shim
                    otherwise);
  3. smoke test   - boot the API on an ephemeral port and run the
                    pseudo-sync / bit-slip recovery smoke script.

Exits once finished.  The exit code is a bitmask:
  0  all checks passed
  1  code tests failed
  2  build check failed
  4  recovery smoke test failed
"""

import os
import py_compile
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

EXIT_TESTS = 1
EXIT_BUILD = 2
EXIT_SMOKE = 4

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PYTHON_FILES = [
    "app/__init__.py",
    "app/crc.py",
    "app/recovery.py",
    "app/validation.py",
    "app/server.py",
    "smoke.py",
    "verify.py",
]


def section(title):
    print(f"\n=== {title} ===", flush=True)


def build_check():
    section("build check")
    ok = True
    for rel in PYTHON_FILES:
        path = os.path.join(ROOT, rel)
        try:
            py_compile.compile(path, doraise=True)
            print(f"  compiled {rel}")
        except py_compile.PyCompileError as exc:
            ok = False
            print(f"  FAIL {rel}: {exc}")
    # Also make sure every module imports cleanly.
    for mod in ("app.crc", "app.recovery", "app.validation", "app.server"):
        try:
            __import__(mod)
            print(f"  imported {mod}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAIL importing {mod}: {exc}")
    print("BUILD:", "PASS" if ok else "FAIL", flush=True)
    return ok


def run_tests():
    section("code tests")
    tests_dir = os.path.join(ROOT, "tests")
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", tests_dir,
         "-t", ROOT, "-v"],
        cwd=ROOT, env=env)
    ok = proc.returncode == 0
    print("TESTS:", "PASS" if ok else "FAIL", flush=True)
    return ok


def run_smoke():
    section("recovery smoke (pseudo sync + slips)")
    import smoke

    external = os.environ.get("SMOKE_BASE_URL")
    server = None
    if external:
        base = external.rstrip("/")
        print(f"  using API at {base}")
    else:
        from app.server import build_server
        server = build_server(port=0)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"  ephemeral API at {base}")
    try:
        _wait_healthy(base)
        failures = smoke.run_smoke(base)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    if failures:
        for msg in failures:
            print(f"  FAIL {msg}")
    ok = not failures
    print("SMOKE:", "PASS" if ok else "FAIL", flush=True)
    return ok


def _wait_healthy(base, timeout=10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError) as exc:
            last = exc
        time.sleep(0.1)
    raise RuntimeError(f"server did not become healthy: {last}")


def main():
    build_ok = build_check()
    tests_ok = run_tests()
    smoke_ok = False
    if build_ok:
        smoke_ok = run_smoke()
    else:
        print("skipping smoke because build failed")

    code = 0
    if not tests_ok:
        code |= EXIT_TESTS
    if not build_ok:
        code |= EXIT_BUILD
    if not smoke_ok:
        code |= EXIT_SMOKE

    section("summary")
    print(f"  build: {'PASS' if build_ok else 'FAIL'}")
    print(f"  tests: {'PASS' if tests_ok else 'FAIL'}")
    print(f"  smoke: {'PASS' if smoke_ok else 'FAIL'}")
    print(f"  exit code: {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
