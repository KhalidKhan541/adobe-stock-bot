import os
import time
import logging
import requests
from urllib.parse import quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://gen.pollinations.ai/image"
TIMEOUT = 60
MAX_RETRIES = 3
RETRY_DELAY = 5
RATE_LIMIT_DELAY = 2

_last_request_time = 0.0


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        wait = RATE_LIMIT_DELAY - elapsed
        logger.debug("Rate limiting: waiting %.1fs", wait)
        time.sleep(wait)
    _last_request_time = time.time()


def generate_image(
    prompt: str,
    output_path: str,
    width: int = 1024,
    height: int = 1024,
) -> bool:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    encoded_prompt = quote(prompt, safe="")
    url = f"{BASE_URL}/{encoded_prompt}"
    params = {"model": "flux", "width": width, "height": height}

    for attempt in range(1, MAX_RETRIES + 1):
        _rate_limit()
        logger.info(
            "Generating image (attempt %d/%d): %s",
            attempt,
            MAX_RETRIES,
            prompt[:80],
        )
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type and len(resp.content) < 1024:
                logger.warning("Response is not an image (Content-Type: %s)", content_type)
                if attempt < MAX_RETRIES:
                    logger.info("Retrying in %ds...", RETRY_DELAY)
                    time.sleep(RETRY_DELAY)
                    continue
                return False

            with open(output_path, "wb") as f:
                f.write(resp.content)

            size_kb = len(resp.content) / 1024
            logger.info("Saved image to %s (%.1f KB)", output_path, size_kb)
            return True

        except requests.exceptions.Timeout:
            logger.warning("Request timed out (attempt %d/%d)", attempt, MAX_RETRIES)
        except requests.exceptions.ConnectionError as e:
            logger.warning("Connection error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
        except requests.exceptions.HTTPError as e:
            logger.warning("HTTP error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
            if resp.status_code == 429:
                logger.info("Rate limited by server, waiting %ds...", RETRY_DELAY * 2)
                time.sleep(RETRY_DELAY * 2)
                continue
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return False

        if attempt < MAX_RETRIES:
            logger.info("Retrying in %ds...", RETRY_DELAY)
            time.sleep(RETRY_DELAY)

    logger.error("All %d attempts failed for prompt: %s", MAX_RETRIES, prompt[:80])
    return False


def generate_batch(
    prompts: list[dict],
    output_dir: str,
) -> list[dict]:
    """
    Generate multiple images.

    Args:
        prompts: List of dicts with keys:
            - prompt (str): The image prompt
            - filename (str, optional): Output filename (default: hash-based name)
            - width (int, optional): Image width (default: 1024)
            - height (int, optional): Image height (default: 1024)
        output_dir: Directory to save all images

    Returns:
        List of dicts with keys:
            - prompt (str): Original prompt
            - output_path (str): Saved file path or empty string on failure
            - success (bool): Whether generation succeeded
    """
    os.makedirs(output_dir, exist_ok=True)
    results = []
    total = len(prompts)

    logger.info("Starting batch generation of %d images to %s", total, output_dir)

    for i, entry in enumerate(prompts, 1):
        prompt = entry["prompt"]
        filename = entry.get("filename", f"image_{i:04d}.jpg")
        width = entry.get("width", 1024)
        height = entry.get("height", 1024)

        if not os.path.splitext(filename)[1]:
            filename += ".jpg"

        output_path = os.path.join(output_dir, filename)

        logger.info("[%d/%d] Processing: %s", i, total, prompt[:80])
        success = generate_image(prompt, output_path, width, height)

        results.append({
            "prompt": prompt,
            "output_path": output_path if success else "",
            "success": success,
        })

    succeeded = sum(1 for r in results if r["success"])
    logger.info(
        "Batch complete: %d/%d images generated successfully", succeeded, total
    )
    return results


if __name__ == "__main__":
    test_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    test_prompts = [
        {"prompt": "A golden sunset over a calm ocean, photorealistic, 8k"},
        {"prompt": "A minimalist logo design, flat vector, white background"},
    ]
    results = generate_batch(test_prompts, test_dir)
    for r in results:
        status = "OK" if r["success"] else "FAILED"
        print(f"  [{status}] {r['prompt'][:60]}")
