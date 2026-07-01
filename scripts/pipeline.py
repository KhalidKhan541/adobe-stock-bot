"""Main pipeline orchestrator for the Adobe Stock bot."""

import csv
import json
import logging
import os
import random
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.image_generator import generate_image
from scripts.image_processor import process_image
from scripts.metadata_generator import generate_metadata
from scripts.csv_generator import generate_csv
from scripts.sftp_uploader import upload_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
PROMPT_LIBRARY_PATH = BASE_DIR / "prompts" / "prompt_library.py"
LOG_PATH = BASE_DIR / "data" / "stock_log.json"
OUTPUT_DIR = BASE_DIR / "output"
IMAGES_DIR = OUTPUT_DIR / "images"
METADATA_DIR = OUTPUT_DIR / "metadata"
CSV_DIR = OUTPUT_DIR / "csv"

REQUIRED_ENV_VARS = [
    "GROQ_API_KEY",
    "GMAIL_APP_PASSWORD",
    "SENDER_EMAIL",
    "RECIPIENT_EMAIL",
    "SFTP_HOST",
    "SFTP_USERNAME",
    "SFTP_PASSWORD",
]

TARGET_PROMPT_COUNT = 33
TARGET_MIN_DIMENSION_MP = 4  # 4 megapixels minimum


def _load_config() -> dict:
    """Load configuration from config.json."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_prompt_library() -> list[dict]:
    """Dynamically import the prompt library and return the prompts list."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("prompt_library", PROMPT_LIBRARY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "PROMPTS"):
        raise AttributeError("prompt_library.py must define a PROMPTS list")

    prompts = module.PROMPTS
    logger.info("Loaded %d prompts from library", len(prompts))
    return prompts


def _select_balanced_prompts(prompts: list[dict], count: int) -> list[dict]:
    """Select prompts balanced across niches using round-robin distribution."""
    niche_map: dict[str, list[dict]] = {}
    for p in prompts:
        niche = p.get("niche", "general")
        niche_map.setdefault(niche, []).append(p)

    niches = list(niche_map.keys())
    random.shuffle(niches)

    selected: list[dict] = []
    niche_idx = {n: 0 for n in niches}

    while len(selected) < count:
        added = False
        for niche in niches:
            idx = niche_idx[niche]
            if idx < len(niche_map[niche]):
                selected.append(niche_map[niche][idx])
                niche_idx[niche] = idx + 1
                added = True
                if len(selected) >= count:
                    break
        if not added:
            break

    if len(selected) < count:
        remaining = [p for p in prompts if p not in selected]
        random.shuffle(remaining)
        selected.extend(remaining[: count - len(selected)])

    logger.info("Selected %d prompts across %d niches", len(selected), len(niche_map))
    return selected


def _ensure_dirs() -> None:
    """Create output directories if they don't exist."""
    for d in [OUTPUT_DIR, IMAGES_DIR, METADATA_DIR, CSV_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _append_log(entry: dict) -> None:
    """Append a run entry to the stock_log.json file."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_data = []
    if LOG_PATH.exists():
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            log_data = json.load(f)
    log_data.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, default=str)


def _check_env_vars() -> None:
    """Verify all required environment variables are set."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")


def generate_report(results: dict) -> str:
    """Generate a human-readable daily report from pipeline results."""
    lines = [
        "=" * 50,
        f"  ADOBE STOCK BOT — Daily Report",
        f"  {results['date']}",
        "=" * 50,
        "",
        f"Total prompts processed:  {results['total']}",
        f"Successful:               {results['success']}",
        f"Failed:                   {results['failed']}",
        "",
        "--- Per-image results ---",
    ]

    for i, item in enumerate(results["items"], 1):
        status_icon = "OK" if item["status"] == "success" else "FAIL"
        lines.append(f"  [{status_icon}] {i:02d}. {item['title'][:60]}")
        if item.get("error"):
            lines.append(f"       Error: {item['error']}")
        if item.get("csv_path"):
            lines.append(f"       CSV:   {item['csv_path']}")
    lines.append("")

    if results["upload_results"]:
        upload_ok = sum(1 for u in results["upload_results"] if u.get("success"))
        upload_fail = len(results["upload_results"]) - upload_ok
        lines.extend([
            "--- SFTP Upload ---",
            f"  Uploaded:  {upload_ok}",
            f"  Failed:    {upload_fail}",
            "",
        ])

    lines.extend([
        "--- Timing ---",
        f"  Duration:  {results.get('duration_seconds', 'N/A')}s",
        "",
        "=" * 50,
    ])
    return "\n".join(lines)


def send_report_email(report: str) -> bool:
    """Send the daily report via Gmail SMTP. Returns True on success."""
    sender = os.environ["SENDER_EMAIL"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"Adobe Stock Bot — Report {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    msg.attach(MIMEText(report, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info("Report email sent to %s", recipient)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


def run_pipeline() -> dict:
    """Execute the full daily pipeline. Returns a results dict."""
    _check_env_vars()
    _ensure_dirs()

    results = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total": 0,
        "success": 0,
        "failed": 0,
        "items": [],
        "upload_results": [],
        "email_sent": False,
        "duration_seconds": 0,
    }

    start_time = datetime.now(timezone.utc)

    try:
        config = _load_config()
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        results["items"].append({"status": "error", "title": "config_load", "error": str(exc)})
        _append_log(results)
        return results

    try:
        prompts = _load_prompt_library()
    except Exception as exc:
        logger.error("Failed to load prompt library: %s", exc)
        results["items"].append({"status": "error", "title": "prompt_load", "error": str(exc)})
        _append_log(results)
        return results

    selected = _select_balanced_prompts(prompts, TARGET_PROMPT_COUNT)
    results["total"] = len(selected)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    for idx, prompt_data in enumerate(selected, 1):
        title = prompt_data.get("title", f"image_{idx:02d}")
        prompt_text = prompt_data.get("prompt", "")
        niche = prompt_data.get("niche", "general")
        safe_name = f"{timestamp}_{idx:02d}_{niche}"

        item_result = {
            "status": "success",
            "title": title,
            "niche": niche,
            "filename": safe_name,
            "image_path": None,
            "processed_path": None,
            "metadata_path": None,
            "csv_path": None,
            "error": None,
        }

        try:
            # Step 1: Generate image
            logger.info("[%d/%d] Generating image: %s", idx, len(selected), title)
            raw_path = IMAGES_DIR / f"{safe_name}_raw.png"
            generate_image(prompt_text, str(raw_path), config=config)
            item_result["image_path"] = str(raw_path)

            # Step 2: Process image
            logger.info("[%d/%d] Processing image", idx, len(selected))
            processed_path = IMAGES_DIR / f"{safe_name}_processed.jpg"
            process_image(str(raw_path), str(processed_path))
            item_result["processed_path"] = str(processed_path)

            # Step 3: Generate metadata
            logger.info("[%d/%d] Generating metadata", idx, len(selected))
            metadata_path = METADATA_DIR / f"{safe_name}_metadata.json"
            metadata = generate_metadata(
                prompt_text=prompt_text,
                title=title,
                image_path=str(processed_path),
                niche=niche,
            )
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            item_result["metadata_path"] = str(metadata_path)

            # Step 4: Generate CSV row
            logger.info("[%d/%d] Generating CSV", idx, len(selected))
            csv_path = CSV_DIR / f"{safe_name}.csv"
            generate_csv(metadata, str(csv_path))
            item_result["csv_path"] = str(csv_path)

            results["success"] += 1
            logger.info("[%d/%d] SUCCESS: %s", idx, len(selected), title)

        except Exception as exc:
            results["failed"] += 1
            item_result["status"] = "error"
            item_result["error"] = str(exc)
            logger.error("[%d/%d] FAILED: %s — %s", idx, len(selected), title, exc)

        results["items"].append(item_result)

    # Step 5: Upload to Adobe Stock via SFTP
    logger.info("Starting SFTP upload...")
    csv_files = [item["csv_path"] for item in results["items"] if item.get("csv_path")]
    if csv_files:
        try:
            for csv_path in csv_files:
                upload_batch(local_dir=str(Path(csv_path).parent), remote_dir="/incoming")
            results["upload_results"] = [{"success": True, "csv_count": len(csv_files)}]
            logger.info("SFTP upload completed: %d CSV files", len(csv_files))
        except Exception as exc:
            logger.error("SFTP upload failed: %s", exc)
            results["upload_results"] = [{"success": False, "error": str(exc)}]

    # Step 6: Generate and send report
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    results["duration_seconds"] = round(elapsed, 1)

    report = generate_report(results)
    logger.info("\n%s", report)

    results["email_sent"] = send_report_email(report)

    _append_log(results)

    logger.info(
        "Pipeline complete: %d/%d succeeded in %ds",
        results["success"],
        results["total"],
        results["duration_seconds"],
    )
    return results


if __name__ == "__main__":
    run_pipeline()
