import os
import logging
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

MIN_MP = 6_000_000
MIN_WIDTH = 3000
MIN_HEIGHT = 3000
MIN_QUALITY = 90
MAX_QUALITY = 100
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def convert_to_jpeg(input_path: str, output_path: str, quality: int = 95) -> bool:
    """Convert any image to JPEG with sRGB color profile."""
    try:
        img = Image.open(input_path)

        if img.mode in ("RGBA", "LA", "PA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img = ImageOps.exif_transpose(img)

        img.save(
            output_path,
            format="JPEG",
            quality=quality,
            subsampling=0,
            optimize=True,
        )
        logger.info("Converted %s -> %s (quality=%d)", input_path, output_path, quality)
        return True
    except Exception:
        logger.exception("Failed to convert %s to JPEG", input_path)
        return False


def upscale_image(
    input_path: str,
    output_path: str,
    target_width: int = MIN_WIDTH,
    target_height: int = MIN_HEIGHT,
) -> bool:
    """Upscale image using Lanczos resampling to maintain quality."""
    try:
        img = Image.open(input_path)

        width, height = img.size
        current_mp = width * height

        if width >= target_width and height >= target_height:
            img.save(output_path, format="JPEG", quality=95, subsampling=0, optimize=True)
            logger.info("Image already meets resolution: %s", input_path)
            return True

        scale_w = target_width / width
        scale_h = target_height / height
        scale = max(scale_w, scale_h)

        new_width = int(width * scale)
        new_height = int(height * scale)

        img = img.resize((new_width, new_height), Image.LANCZOS)

        if img.mode in ("RGBA", "LA", "PA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(output_path, format="JPEG", quality=95, subsampling=0, optimize=True)
        logger.info(
            "Upscaled %s (%dx%d) -> %s (%dx%d)",
            input_path, width, height, output_path, new_width, new_height,
        )
        return True
    except Exception:
        logger.exception("Failed to upscale %s", input_path)
        return False


def process_image(input_path: str, output_dir: str) -> dict:
    """Full pipeline: convert to JPEG + upscale to 4MP+ + validate."""
    result = {
        "input": input_path,
        "success": False,
        "output": None,
        "validation": None,
        "error": None,
    }

    try:
        input_path = os.path.abspath(input_path)
        output_dir = os.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        stem = Path(input_path).stem
        converted_path = os.path.join(output_dir, f"{stem}_converted.jpg")
        final_path = os.path.join(output_dir, f"{stem}_final.jpg")

        if not convert_to_jpeg(input_path, converted_path, quality=95):
            result["error"] = "JPEG conversion failed"
            return result

        if not upscale_image(converted_path, final_path):
            result["error"] = "Upscaling failed"
            return result

        validation = validate_image(final_path)
        result["validation"] = validation

        if validation["valid"]:
            result["success"] = True
            result["output"] = final_path
        else:
            result["error"] = f"Validation failed: {validation['issues']}"

        return result
    except Exception:
        logger.exception("Failed to process %s", input_path)
        result["error"] = "Unexpected error during processing"
        return result


def validate_image(image_path: str) -> dict:
    """Check resolution, format, file size. Return validation results."""
    issues = []
    info = {}

    try:
        with Image.open(image_path) as img:
            width, height = img.size
            mode = img.mode
            fmt = img.format

            info["width"] = width
            info["height"] = height
            info["mode"] = mode
            info["format"] = fmt
            info["megapixels"] = round((width * height) / 1_000_000, 2)

        file_size = os.path.getsize(image_path)
        info["file_size_bytes"] = file_size
        info["file_size_mb"] = round(file_size / (1024 * 1024), 2)

        if fmt != "JPEG":
            issues.append(f"Format is {fmt}, expected JPEG")

        if width < MIN_WIDTH or height < MIN_HEIGHT:
            issues.append(
                f"Resolution {width}x{height} below minimum {MIN_WIDTH}x{MIN_HEIGHT}"
            )

        if width * height < MIN_MP:
            issues.append(
                f"Total pixels {width * height} below {MIN_MP} (4 megapixels)"
            )

        if file_size > 50 * 1024 * 1024:
            issues.append(f"File size {info['file_size_mb']}MB exceeds 50MB limit")

        if file_size < 1024:
            issues.append("File size too small, likely corrupt")

        return {"valid": len(issues) == 0, "issues": issues, "info": info}
    except Exception:
        logger.exception("Failed to validate %s", image_path)
        return {"valid": False, "issues": ["Could not open or read image file"], "info": {}}


def process_batch(input_dir: str, output_dir: str) -> list[dict]:
    """Process all images in a directory."""
    input_dir = os.path.abspath(input_dir)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    results = []
    image_files = []

    for entry in os.scandir(input_dir):
        if entry.is_file() and Path(entry.name).suffix.lower() in SUPPORTED_FORMATS:
            image_files.append(entry.path)

    if not image_files:
        logger.warning("No supported images found in %s", input_dir)
        return results

    image_files.sort()
    logger.info("Found %d images to process", len(image_files))

    for image_path in image_files:
        logger.info("Processing %s", image_path)
        result = process_image(image_path, output_dir)
        results.append(result)

        status = "OK" if result["success"] else f"FAILED: {result['error']}"
        logger.info("  -> %s", status)

    succeeded = sum(1 for r in results if r["success"])
    logger.info(
        "Batch complete: %d/%d succeeded", succeeded, len(results),
    )

    return results


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Process images for Adobe Stock submission")
    parser.add_argument("input", help="Input image file or directory")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("--quality", type=int, default=95, help="JPEG quality (90-100)")
    parser.add_argument("--batch", action="store_true", help="Process entire directory")
    args = parser.parse_args()

    if args.batch:
        results = process_batch(args.input, args.output)
        failed = [r for r in results if not r["success"]]
        sys.exit(1 if failed else 0)
    else:
        if not os.path.isfile(args.input):
            logger.error("Input file not found: %s", args.input)
            sys.exit(1)
        result = process_image(args.input, args.output)
        if not result["success"]:
            logger.error("Processing failed: %s", result["error"])
            sys.exit(1)
        print(f"Output: {result['output']}")
