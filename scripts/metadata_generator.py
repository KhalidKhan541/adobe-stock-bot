import os
import json
import time
import logging

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are an expert stock photography SEO specialist. Generate optimized metadata for stock image submissions.

Rules:
- Title: 5-200 characters, descriptive, engaging, must NOT mention "generative AI", "AI-generated", "digital art", or similar terms
- Keywords: exactly 25 relevant keywords/phrases ordered by relevance (most important first), comma-separated. Include commercial and search-intent keywords. No artist names, no real people names, no brand names, no trademarked terms.
- Description: 1-2 clear, descriptive sentences about the image. No AI mentions.
- Category: one of: "Business", "Nature", "Technology", "People", "Travel", "Food and Drink", "Health", "Education", "Animals", "Sports", "Transportation", "Buildings and Architecture", "Backgrounds", "Holidays and Events", "Abstract", "Arts and Culture", "Environment", "Industry", "Science"

Respond ONLY with valid JSON:
{
  "title": "string",
  "keywords": "string (comma-separated, 25 keywords)",
  "description": "string",
  "category": "string"
}"""


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
                logger.warning("Rate limited. Waiting %.1fs", wait)
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue

            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            logger.info("Groq API response received")
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
        logger.error("Failed to parse JSON: %s", exc)
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
        "keywords_csv": ", ".join(keywords),
        "description": description,
        "category": category,
    }


def generate_metadata(prompt: str, title: str = "", image_path: str = "", niche: str = "") -> dict:
    description_section = f"Image prompt: {prompt}"
    if title:
        description_section += f"\nSuggested title: {title}"
    if niche:
        description_section += f"\nNiche/category: {niche}"

    user_prompt = f"Generate optimized stock metadata for this image.\n\n{description_section}\n\nProvide as valid JSON with keys: title, keywords, description, category."

    logger.info("Generating metadata for: %s", prompt[:80])
    raw_response = _call_groq(user_prompt)
    metadata = _parse_metadata(raw_response)
    logger.info("Metadata: title='%s', %d keywords", metadata["title"][:50], len([k for k in metadata["keywords"] if k]))
    return metadata
