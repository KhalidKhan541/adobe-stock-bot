import os
import json
import time
import logging
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are an expert stock photography SEO specialist for Adobe Stock. Your task is to generate optimized metadata that maximizes discoverability and sales.

Rules:
- Title: 5-200 characters, descriptive, engaging, must NOT mention "generative AI", "AI-generated", "digital art", or similar terms
- Keywords: exactly 25 relevant keywords/phrases ordered by relevance (most important first), comma-separated. Include commercial and search-intent keywords. No artist names, no real people names, no brand names, no trademarked terms.
- Description: 1-2 clear, descriptive sentences about the image. No AI mentions.
- Category: one of the valid Adobe Stock categories (e.g., "Business", "Nature", "Technology", "People", "Travel", "Food and Drink", "Health", "Education", "Animals", "Sports", "Transportation", "Buildings and Architecture", "Backgrounds", "Holidays and Events", "Abstract", "Arts and Culture", "Crime and Law", "Environment", "Industry", "Military", "Religion", "Science", "Social Issues", "Fashion and Beauty", "Home and Garden", "Interiors")

Respond ONLY with valid JSON matching this exact structure:
{
  "title": "string",
  "keywords": "string (comma-separated)",
  "description": "string",
  "category": "string"
}"""

USER_PROMPT_TEMPLATE = """Generate optimized Adobe Stock metadata for this image.

{description_section}

Provide the metadata as valid JSON with keys: title, keywords, description, category."""


def _get_api_key() -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")
    return api_key


def _call_groq(prompt: str, max_retries: int = 5) -> str:
    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Groq API request attempt %d/%d", attempt, max_retries)
            resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "Rate limited (429). Waiting %.1fs before retry %d/%d",
                    wait, attempt, max_retries,
                )
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue

            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("Groq API response received (%d tokens)", data.get("usage", {}).get("total_tokens", 0))
            return content

        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error on attempt %d: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise

    raise RuntimeError("Max retries exceeded for Groq API call")


def _parse_metadata(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON response: %s\nRaw output: %s", exc, raw[:500])
        raise ValueError(f"Invalid JSON from Groq: {exc}") from exc

    keywords_raw = data.get("keywords", "")
    if isinstance(keywords_raw, str):
        keywords = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]
    elif isinstance(keywords_raw, list):
        keywords = [str(kw).strip() for kw in keywords_raw if str(kw).strip()]
    else:
        keywords = []

    keywords = keywords[:25]
    while len(keywords) < 25:
        keywords.append("")

    title = data.get("title", "")
    if len(title) > 200:
        title = title[:197] + "..."
    if len(title) < 5:
        title = title.ljust(5, ".")

    description = data.get("description", "")
    category = data.get("category", "Business")

    return {
        "title": title,
        "keywords": keywords,
        "description": description,
        "category": category,
    }


def generate_metadata(prompt: str, image_description: str = "") -> dict:
    description_section = f"Image prompt/description: {prompt}"
    if image_description:
        description_section += f"\nAdditional context: {image_description}"

    user_prompt = USER_PROMPT_TEMPLATE.format(description_section=description_section)

    logger.info("Generating metadata for prompt: %s", prompt[:100])
    raw_response = _call_groq(user_prompt)
    metadata = _parse_metadata(raw_response)
    logger.info("Metadata generated: title='%s', keywords=%d", metadata["title"][:50], len([k for k in metadata["keywords"] if k]))
    return metadata


def generate_batch_metadata(prompts: list[dict]) -> list[dict]:
    results = []
    total = len(prompts)

    for idx, entry in enumerate(prompts, 1):
        prompt = entry.get("prompt", "")
        image_description = entry.get("image_description", "")

        logger.info("Processing batch item %d/%d", idx, total)
        try:
            metadata = generate_metadata(prompt, image_description)
            metadata["prompt"] = prompt
            metadata["status"] = "success"
        except Exception as exc:
            logger.error("Failed to generate metadata for item %d: %s", idx, exc)
            metadata = {
                "prompt": prompt,
                "title": "",
                "keywords": [""] * 25,
                "description": "",
                "category": "Business",
                "status": "error",
                "error": str(exc),
            }

        results.append(metadata)

        if idx < total:
            time.sleep(0.5)

    success_count = sum(1 for r in results if r["status"] == "success")
    logger.info("Batch complete: %d/%d succeeded", success_count, total)
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        test_prompt = " ".join(sys.argv[1:])
    else:
        test_prompt = "A professional team collaborating in a modern office workspace with natural lighting"

    result = generate_metadata(test_prompt)
    print(json.dumps(result, indent=2, ensure_ascii=False))
