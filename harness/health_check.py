#!/usr/bin/env python3
"""
Pipeline Health Check — Tests all APIs before running pipelines.
Run this BEFORE every pipeline execution.
"""

import json
import os
import sys
import time
import smtplib
import requests
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIG
# ============================================================================

CONFIG_PATH = Path(__file__).parent / "config.yaml"
LOGS_DIR = Path(__file__).parent / "logs"

# API endpoints to test
APIS = {
    "pollinations": {
        "name": "Pollinations.ai (Image Generation)",
        "url": "https://image.pollinations.ai/prompt/test?width=64&height=64",
        "timeout": 30,
        "required": True,
    },
    "groq_wirestock": {
        "name": "Groq API (Wirestock)",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "headers": {"Authorization": "Bearer ${GROQ_API_KEY}", "Content-Type": "application/json"},
        "json": {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        "timeout": 15,
        "required": False,
    },
    "groq_clipforge": {
        "name": "Groq API (ClipForge)",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "headers": {"Authorization": "Bearer ${GROQ_API_KEY}", "Content-Type": "application/json"},
        "json": {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        "timeout": 15,
        "required": False,
    },
    "smtp": {
        "name": "Gmail SMTP",
        "test": "smtp",
        "timeout": 10,
        "required": True,
    },
}

# ============================================================================
# TEST FUNCTIONS
# ============================================================================

def resolve_env_vars(obj):
    """Resolve ${ENV_VAR} references in config."""
    if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        var_name = obj[2:-1]
        return os.environ.get(var_name, obj)
    elif isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_env_vars(item) for item in obj]
    return obj


def test_api(name: str, config: dict) -> dict:
    """Test a single API endpoint."""
    result = {
        "name": config["name"],
        "status": "unknown",
        "latency_ms": 0,
        "error": None,
    }

    start = time.time()

    try:
        if config.get("test") == "smtp":
            # Test SMTP connection
            sender = os.environ.get("SENDER_EMAIL", "khalid.khan46571@gmail.com")
            password = os.environ.get("GMAIL_APP_PASSWORD", "velr opzr cwpr vqyt").replace(" ", "")
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=config["timeout"]) as server:
                server.login(sender, password)
            result["status"] = "ok"
        else:
            # Test HTTP endpoint
            headers = resolve_env_vars(config.get("headers", {}))
            json_data = resolve_env_vars(config.get("json", None))
            method = config.get("method", "GET")
            if method == "POST" or json_data:
                resp = requests.post(
                    config["url"],
                    headers=headers,
                    json=json_data,
                    timeout=config["timeout"],
                )
            else:
                resp = requests.get(
                    config["url"],
                    headers=headers,
                    timeout=config["timeout"],
                )

            if resp.status_code == 200:
                result["status"] = "ok"
            elif resp.status_code == 401:
                result["status"] = "failed"
                result["error"] = "API key expired or invalid (401)"
            elif resp.status_code == 429:
                result["status"] = "warning"
                result["error"] = "Rate limited (429) — will retry"
            else:
                result["status"] = "failed"
                result["error"] = f"HTTP {resp.status_code}"

    except requests.exceptions.Timeout:
        result["status"] = "failed"
        result["error"] = "Timeout"
    except requests.exceptions.ConnectionError as e:
        result["status"] = "failed"
        result["error"] = f"Connection failed: {e}"
    except smtplib.SMTPAuthenticationError:
        result["status"] = "failed"
        result["error"] = "SMTP authentication failed"
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    result["latency_ms"] = round((time.time() - start) * 1000)
    return result


def check_dependencies() -> dict:
    """Check if all required Python packages are installed."""
    required = {
        "requests": "requests",
        "PIL": "Pillow",
        "reportlab": "reportlab",
        "bs4": "beautifulsoup4",
        "lxml": "lxml",
        "groq": "groq",
        "dotenv": "python-dotenv",
    }

    results = {}
    for module, package in required.items():
        try:
            __import__(module)
            results[package] = "installed"
        except ImportError:
            results[package] = "MISSING"

    return results


def check_disk_space() -> dict:
    """Check available disk space."""
    import shutil
    total, used, free = shutil.disk_usage("/")
    return {
        "total_gb": round(total / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
        "warning": free < 5 * 1024**3,  # Less than 5GB free
    }


# ============================================================================
# MAIN
# ============================================================================

def run_health_check(mode: str = "full") -> dict:
    """Run complete health check."""
    print("=" * 60)
    print(f"  PIPELINE HEALTH CHECK — {mode.upper()}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "apis": {},
        "dependencies": {},
        "disk": {},
        "overall": "unknown",
    }

    # Test APIs
    print("\n--- API Tests ---")
    all_ok = True
    for name, config in APIS.items():
        if mode == "quick" and not config.get("required", True):
            continue

        print(f"  Testing {config['name']}...", end=" ")
        result = test_api(name, config)
        report["apis"][name] = result

        if result["status"] == "ok":
            print(f"OK ({result['latency_ms']}ms)")
        elif result["status"] == "warning":
            print(f"WARN {result['error']}")
        else:
            print(f"FAIL {result['error']}")
            all_ok = False

    # Check dependencies
    if mode == "full":
        print("\n--- Dependencies ---")
        deps = check_dependencies()
        report["dependencies"] = deps
        for pkg, status in deps.items():
            icon = "OK" if status == "installed" else "MISSING"
            print(f"  {icon}: {pkg}")
            if status == "MISSING":
                all_ok = False

    # Check disk space
    print("\n--- Disk Space ---")
    disk = check_disk_space()
    report["disk"] = disk
    print(f"  Free: {disk['free_gb']} GB / Total: {disk['total_gb']} GB")
    if disk["warning"]:
        print("  ⚠ Low disk space!")
        all_ok = False

    # Overall status
    report["overall"] = "healthy" if all_ok else "unhealthy"

    print("\n" + "=" * 60)
    if all_ok:
        print("  ALL CHECKS PASSED — Safe to run pipelines")
    else:
        print("  ISSUES FOUND — Fix before running pipelines")
    print("=" * 60)

    # Save log
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    logs = []
    if log_file.exists():
        with open(log_file) as f:
            logs = json.load(f)
    logs.append(report)
    with open(log_file, "w") as f:
        json.dump(logs, f, indent=2)

    return report


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode not in ("quick", "full"):
        mode = "full"
    report = run_health_check(mode)
    sys.exit(0 if report["overall"] == "healthy" else 1)
