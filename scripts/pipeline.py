"""Main pipeline orchestrator — generates images + metadata, packs into Wirestock-ready ZIP."""

import csv
import io
import json
import logging
import os
import random
import smtplib
import sys
import time
import functools
import zipfile
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.image_generator import generate_image
from scripts.image_processor import process_image
from scripts.metadata_generator import generate_metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Niches for Wirestock
NICHES = [
    "AI and human collaboration in workplace",
    "Sustainable living and eco-friendly lifestyle",
    "Mental wellness and mindfulness"
]

USED_PROMPTS_PATH = Path(__file__).resolve().parent.parent / "data" / "used_prompts.json"


def _load_used_prompts() -> list[str]:
    """Load list of previously used prompts to avoid duplicates."""
    if USED_PROMPTS_PATH.exists():
        with open(USED_PROMPTS_PATH, "r") as f:
            return json.load(f)
    return []


def _save_used_prompts(prompts: list[str]) -> None:
    """Save used prompts to file."""
    USED_PROMPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USED_PROMPTS_PATH, "w") as f:
        json.dump(prompts, f, indent=2)


def _generate_prompts_with_groq(niche: str, count: int, used_prompts: list[str]) -> list[dict]:
    """Generate unique prompts using Groq API."""
    import requests
    
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        logger.error("GROQ_API_KEY not set")
        return []
    
    used_text = "\n".join(f"- {p}" for p in used_prompts[-50:]) if used_prompts else "None yet"
    
    system_prompt = f"""You are a stock photography prompt generator. Generate {count} unique, detailed prompts for AI image generation.

NICHE: {niche}

REQUIREMENTS:
- Each prompt must be a single sentence describing a realistic stock photo
- Include specific details: lighting, composition, people diversity, setting
- NO duplicates with previously used prompts
- Images must be Wirestock-approved: sharp, well-lit, authentic feel
- Include diverse people (different ages, ethnicities, genders)
- Focus on commercial appeal (businesses would buy these)

PREVIOUSLY USED PROMPTS (DO NOT DUPLICATE):
{used_text}

OUTPUT FORMAT (JSON array):
[
  {{"title": "Short title", "prompt": "Detailed prompt sentence", "niche": "{niche}"}},
  ...
]

Generate exactly {count} prompts. Output ONLY valid JSON, no other text."""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Generate {count} unique stock photography prompts for: {niche}"}
                ],
                "temperature": 0.9,
                "max_tokens": 4000
            },
            timeout=60
        )
        response.raise_for_status()
        
        content = response.json()["choices"][0]["message"]["content"]
        
        # Extract JSON from response
        import re
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            prompts = json.loads(json_match.group())
            logger.info("Generated %d prompts for niche: %s", len(prompts), niche)
            return prompts
        else:
            logger.error("No JSON array found in Groq response")
            return []
            
    except Exception as e:
        logger.error("Failed to generate prompts: %s", e)
        return []


def _generate_all_prompts(count_per_niche: int = 50) -> list[dict]:
    """Generate prompts for all niches, avoiding duplicates."""
    used_prompts = _load_used_prompts()
    all_prompts = []
    
    for niche in NICHES:
        prompts = _generate_prompts_with_groq(niche, count_per_niche, used_prompts)
        all_prompts.extend(prompts)
        # Add generated prompts to used list
        for p in prompts:
            used_prompts.append(p.get("prompt", ""))
    
    # Save all used prompts
    _save_used_prompts(used_prompts)
    
    logger.info("Total prompts generated: %d", len(all_prompts))
    return all_prompts


def retry_with_backoff(max_retries=3, base_delay=2, max_delay=30):
    """Retry decorator with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    print(f"  Attempt {attempt + 1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        print(f"  Retrying in {delay}s...")
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


@retry_with_backoff(max_retries=3, base_delay=5)
def generate_image_with_retry(prompt_text, raw_path, gen_width, gen_height):
    from scripts.image_generator import generate_image
    success = generate_image(prompt_text, str(raw_path), width=gen_width, height=gen_height)
    if not success:
        raise RuntimeError("Image generation failed")
    return True


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
PROMPT_LIBRARY_PATH = BASE_DIR / "prompts" / "prompt_library.py"
LOG_PATH = BASE_DIR / "data" / "stock_log.json"
OUTPUT_DIR = BASE_DIR / "output"
IMAGES_DIR = OUTPUT_DIR / "images"
ZIP_DIR = OUTPUT_DIR / "zip"

DEFAULT_IMAGE_COUNT = 50


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_prompt_library() -> list[dict]:
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
    for d in [OUTPUT_DIR, IMAGES_DIR, ZIP_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _append_log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_data = []
    if LOG_PATH.exists():
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            log_data = json.load(f)
    log_data.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, default=str)


def _create_metadata_csv(items: list[dict], csv_path: str) -> str:
    """Create a CSV with metadata for all images (Wirestock format)."""
    fieldnames = ["filename", "title", "description", "keywords", "category"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            if item.get("status") == "success" and item.get("metadata"):
                meta = item["metadata"]
                # Handle keywords - could be list or string
                keywords = meta.get("keywords_csv", "")
                if not keywords:
                    kw_list = meta.get("keywords", [])
                    if isinstance(kw_list, list):
                        keywords = ", ".join([k for k in kw_list if k])
                    else:
                        keywords = str(kw_list)
                
                row = {
                    "filename": item["final_filename"],
                    "title": meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "keywords": keywords,
                    "category": meta.get("category", ""),
                }
                logger.info("CSV row: %s", row["filename"])
                writer.writerow(row)
    return csv_path


def _create_zip(images_dir: Path, csv_path: str, zip_path: Path) -> int:
    """Pack all final JPEG images + metadata CSV into a ZIP file. Returns count of images added."""
    image_count = 0
    image_files = []

    for pattern in ["*.jpg", "*.jpeg", "*.png"]:
        for img_file in sorted(images_dir.glob(pattern)):
            if "_raw" in img_file.name or "_processed" in img_file.name or "_converted" in img_file.name:
                continue
            image_files.append(img_file)

    if not image_files:
        logger.warning("No images found to add to ZIP")
        return 0

    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(csv_path):
            zf.write(csv_path, "metadata.csv")
            logger.info("Added metadata.csv to ZIP")

        for img_file in image_files:
            try:
                zf.write(str(img_file), img_file.name)
                image_count += 1
                logger.info("Added to ZIP: %s", img_file.name)
            except Exception as exc:
                logger.error("Failed to add %s to ZIP: %s", img_file, exc)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    logger.info("Created ZIP: %s (%.1f MB, %d images)", zip_path, size_mb, image_count)
    return image_count


def _send_email_with_zip(report: str, zip_path: Path) -> bool:
    """Send email with the ZIP file attached."""
    sender = os.environ["SENDER_EMAIL"]
    recipient = os.environ["RECIPIENT_EMAIL"]
    password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"Wirestock Upload Package — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    body = f"""Wirestock Upload Package

{report}

INSTRUCTIONS:
1. Log in to wirestock.io
2. Click Upload
3. Select images from this ZIP package
4. Wirestock will auto-distribute to Shutterstock, Adobe Stock, Getty, Alamy, Freepik

Your images are ready to upload!
"""
    msg.attach(MIMEText(body, "plain"))

    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={zip_path.name}")
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info("Email with ZIP sent to %s", recipient)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


def generate_report(results: dict) -> str:
    lines = [
        "=" * 50,
        "  WIRESTOCK BOT — Daily Report",
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

    lines.append("")
    lines.append(f"ZIP file: {results.get('zip_path', 'N/A')}")
    lines.append(f"Images in ZIP: {results.get('images_in_zip', 'N/A')}")
    lines.append(f"Email sent: {results.get('email_sent', False)}")
    lines.append(f"Duration: {results.get('duration_seconds', 'N/A')}s")
    lines.append("")
    lines.append("=" * 50)
    return "\n".join(lines)


def run_pipeline(images: int = None) -> dict:
    if images is None:
        images = DEFAULT_IMAGE_COUNT
    logger.info("Starting pipeline — generating %d images", images)
    _ensure_dirs()

    results = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total": 0,
        "success": 0,
        "failed": 0,
        "items": [],
        "zip_path": None,
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
        prompts = _generate_all_prompts(count_per_niche=50)
        if not prompts:
            logger.warning("Dynamic generation returned empty, falling back to static library")
            prompts = _load_prompt_library()
    except Exception as exc:
        logger.warning("Dynamic generation failed (%s), falling back to static library", exc)
        prompts = _load_prompt_library()

    selected = _select_balanced_prompts(prompts, images)
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
            "final_filename": f"{safe_name}.jpg",
            "metadata": None,
            "error": None,
        }

        try:
            logger.info("[%d/%d] Generating image: %s", idx, len(selected), title)
            raw_path = IMAGES_DIR / f"{safe_name}_raw.png"
            gen_width = config.get("generation_resolution", {}).get("width", 2048)
            gen_height = config.get("generation_resolution", {}).get("height", 2048)
            generate_image_with_retry(prompt_text, raw_path, gen_width, gen_height)

            logger.info("[%d/%d] Processing image", idx, len(selected))
            processed = process_image(str(raw_path), str(IMAGES_DIR))
            if not processed["success"]:
                raise RuntimeError(f"Image processing failed: {processed['error']}")

            final_path = Path(processed["output"])
            clean_final = IMAGES_DIR / f"{safe_name}.jpg"
            if final_path != clean_final:
                os.replace(str(final_path), str(clean_final))

            logger.info("[%d/%d] Generating metadata", idx, len(selected))
            metadata = generate_metadata(
                prompt=prompt_text,
                title=title,
                image_path=str(clean_final),
                niche=niche,
            )
            item_result["metadata"] = metadata
            results["success"] += 1
            logger.info("[%d/%d] SUCCESS: %s", idx, len(selected), title)

        except Exception as exc:
            results["failed"] += 1
            item_result["status"] = "error"
            item_result["error"] = str(exc)
            logger.error("[%d/%d] FAILED: %s — %s", idx, len(selected), title, exc)

        results["items"].append(item_result)

    csv_path = str(ZIP_DIR / "metadata.csv")
    _create_metadata_csv(results["items"], csv_path)

    # Log what's in IMAGES_DIR before creating ZIP
    all_files = list(IMAGES_DIR.glob("*"))
    logger.info("Files in IMAGES_DIR: %d total", len(all_files))
    for f in all_files[:10]:
        logger.info("  %s", f.name)

    timestamp_zip = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_path = ZIP_DIR / f"wirestock_upload_{timestamp_zip}.zip"
    image_count = _create_zip(IMAGES_DIR, csv_path, zip_path)
    results["zip_path"] = str(zip_path)
    results["images_in_zip"] = image_count

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    results["duration_seconds"] = round(elapsed, 1)

    report = generate_report(results)
    logger.info("\n%s", report)

    try:
        results["email_sent"] = _send_email_with_zip(report, zip_path)
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        results["email_sent"] = False

    _append_log(results)

    logger.info(
        "Pipeline complete: %d/%d succeeded in %ds",
        results["success"],
        results["total"],
        results["duration_seconds"],
    )
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wirestock Image Pipeline")
    parser.add_argument("--images", type=int, default=50, help="Number of images to generate (default: 50)")
    args = parser.parse_args()
    run_pipeline(images=args.images)
