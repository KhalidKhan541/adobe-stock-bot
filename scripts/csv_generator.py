"""CSV generator for Adobe Stock bulk metadata upload."""

import csv
import os
from typing import Optional

ADOBE_STOCK_CATEGORIES: dict[str, str] = {
    "1075": "Buildings and Architecture",
    "1157": "Business",
    "1166": "Computer and Communication",
    "1311": "Education",
    "1363": "Environmental",
    "1482": "Healthcare",
    "1638": "Industry",
    "1751": "Nature and Landscape",
    "1903": "People",
    "2071": "Social Issues",
    "2162": "Transportation",
    "2353": "Lifestyle",
}

CSV_HEADERS = [
    "filename",
    "title",
    "keywords",
    "description",
    "category",
    "model_release",
    "property_release",
    "is_ai_generated",
]

MAX_KEYWORDS = 25
MAX_TITLE_LENGTH = 200


def generate_csv_row(
    filename: str,
    title: str,
    keywords: list[str],
    description: str,
    category: str = "1075",
    model_release: bool = False,
    property_release: bool = False,
    is_ai_generated: bool = False,
) -> dict:
    """Generate a single CSV row dict.

    Args:
        filename: Name of the image file.
        title: Title for the stock image (max 200 characters).
        keywords: List of keywords (max 25).
        description: Description of the image.
        category: Adobe Stock category ID (default: 1075 - Buildings and Architecture).
        model_release: Whether a model release is available.
        property_release: Whether a property release is available.
        is_ai_generated: Whether the image was AI-generated.

    Returns:
        Dict representing a single CSV row.

    Raises:
        ValueError: If validation fails.
    """
    if not filename:
        raise ValueError("Filename cannot be empty")
    if not title:
        raise ValueError("Title cannot be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"Title exceeds {MAX_TITLE_LENGTH} characters: {len(title)}"
        )
    if not description:
        raise ValueError("Description cannot be empty")
    if category not in ADOBE_STOCK_CATEGORIES:
        valid = ", ".join(sorted(ADOBE_STOCK_CATEGORIES.keys()))
        raise ValueError(
            f"Invalid category ID '{category}'. Valid IDs: {valid}"
        )

    limited_keywords = keywords[:MAX_KEYWORDS]
    keywords_str = ", ".join(k.strip() for k in limited_keywords if k.strip())

    return {
        "filename": filename,
        "title": title,
        "keywords": keywords_str,
        "description": description,
        "category": category,
        "model_release": str(model_release).lower(),
        "property_release": str(property_release).lower(),
        "is_ai_generated": str(is_ai_generated).lower(),
    }


def generate_csv(images_data: list[dict], output_path: str) -> str:
    """Generate a CSV file for Adobe Stock bulk metadata upload.

    Args:
        images_data: List of dicts with keys: filename, title, keywords (list),
                     description, category (optional).
        output_path: Path where the CSV file will be written.

    Returns:
        Absolute path to the generated CSV file.

    Raises:
        ValueError: If images_data is empty or any entry fails validation.
    """
    if not images_data:
        raise ValueError("images_data cannot be empty")

    rows = []
    errors = []

    for i, image in enumerate(images_data):
        try:
            row = generate_csv_row(
                filename=image["filename"],
                title=image["title"],
                keywords=image.get("keywords", []),
                description=image.get("description", ""),
                category=image.get("category", "1075"),
                model_release=image.get("model_release", False),
                property_release=image.get("property_release", False),
                is_ai_generated=image.get("is_ai_generated", False),
            )
            rows.append(row)
        except (KeyError, ValueError) as e:
            errors.append(f"Row {i + 1} ({image.get('filename', '?')}): {e}")

    if errors:
        raise ValueError(
            f"Validation failed for {len(errors)} row(s):\n" + "\n".join(errors)
        )

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    return os.path.abspath(output_path)


def validate_csv(csv_path: str) -> dict:
    """Validate CSV format and content for Adobe Stock upload.

    Args:
        csv_path: Path to the CSV file to validate.

    Returns:
        Dict with keys: valid (bool), errors (list[str]), warnings (list[str]),
        row_count (int).
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "row_count": 0,
    }

    if not os.path.isfile(csv_path):
        result["valid"] = False
        result["errors"].append(f"File not found: {csv_path}")
        return result

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames != CSV_HEADERS:
                result["valid"] = False
                result["errors"].append(
                    f"Invalid headers. Expected: {CSV_HEADERS}, "
                    f"Got: {reader.fieldnames}"
                )
                return result

            for i, row in enumerate(reader, start=2):
                result["row_count"] += 1
                row_errors = _validate_row(row, i)
                if row_errors:
                    result["errors"].extend(row_errors)
                    result["valid"] = False

    except csv.Error as e:
        result["valid"] = False
        result["errors"].append(f"CSV parsing error: {e}")
    except UnicodeDecodeError:
        result["valid"] = False
        result["errors"].append(
            "File encoding error. Please save the file as UTF-8."
        )

    return result


def _validate_row(row: dict, line_number: int) -> list[str]:
    """Validate a single CSV row."""
    errors = []
    prefix = f"Line {line_number}"

    if not row.get("filename", "").strip():
        errors.append(f"{prefix}: Empty filename")

    title = row.get("title", "").strip()
    if not title:
        errors.append(f"{prefix}: Empty title")
    elif len(title) > MAX_TITLE_LENGTH:
        errors.append(
            f"{prefix}: Title exceeds {MAX_TITLE_LENGTH} characters "
            f"({len(title)})"
        )

    keywords_str = row.get("keywords", "").strip()
    if not keywords_str:
        errors.append(f"{prefix}: Empty keywords")
    else:
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if len(keywords) > MAX_KEYWORDS:
            errors.append(
                f"{prefix}: Too many keywords ({len(keywords)}, max {MAX_KEYWORDS})"
            )

    if not row.get("description", "").strip():
        errors.append(f"{prefix}: Empty description")

    category = row.get("category", "").strip()
    if category not in ADOBE_STOCK_CATEGORIES:
        errors.append(f"{prefix}: Invalid category '{category}'")

    for field in ("model_release", "property_release", "is_ai_generated"):
        value = row.get(field, "").strip().lower()
        if value not in ("true", "false"):
            errors.append(f"{prefix}: Invalid {field} value '{value}'")

    return errors


if __name__ == "__main__":
    sample_data = [
        {
            "filename": "skyline_photo.jpg",
            "title": "City skyline at sunset with golden light",
            "keywords": ["city", "skyline", "sunset", "urban", "buildings"],
            "description": "A panoramic view of a city skyline during golden hour.",
            "category": "1075",
        },
        {
            "filename": "office_meeting.jpg",
            "title": "Team collaboration in modern office space",
            "keywords": ["business", "meeting", "teamwork", "office"],
            "description": "Professionals working together in a bright office.",
            "category": "1157",
        },
    ]

    output = "stock_metadata.csv"
    path = generate_csv(sample_data, output)
    print(f"Generated CSV: {path}")

    validation = validate_csv(path)
    print(f"Validation: {validation}")
