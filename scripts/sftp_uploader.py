"""
SFTP Uploader for Adobe Stock.

Uploads images and CSV metadata files to Adobe Stock's SFTP server.
Requires: paramiko (add to requirements.txt: paramiko>=3.4.0)
"""

import os
import logging
import time
from pathlib import Path
from typing import Optional

import paramiko

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def _get_sftp_credentials() -> dict:
    credentials = {
        "host": os.environ.get("SFTP_HOST"),
        "username": os.environ.get("SFTP_USERNAME"),
        "password": os.environ.get("SFTP_PASSWORD"),
        "port": int(os.environ.get("SFTP_PORT", "22")),
    }
    missing = [k for k, v in credentials.items() if v is None]
    if missing:
        raise ValueError(f"Missing SFTP credentials: {', '.join(missing)}")
    return credentials


def _create_sftp_client(
    host: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: int = 30,
) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    """Create and return an (SSHClient, SFTPClient) tuple."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    sftp = ssh.open_sftp()
    return ssh, sftp


def _close_sftp(ssh: paramiko.SSHClient, sftp: paramiko.SFTPClient) -> None:
    try:
        sftp.close()
    except Exception:
        pass
    try:
        ssh.close()
    except Exception:
        pass


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def upload_file(local_path: str, remote_path: str) -> bool:
    """Upload a single file via SFTP with retry logic."""
    local = Path(local_path)
    if not local.exists():
        logger.error("Local file does not exist: %s", local_path)
        return False

    file_size = local.stat().st_size
    logger.info("Uploading %s (%s) -> %s", local.name, _human_size(file_size), remote_path)

    ssh, sftp = None, None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            creds = _get_sftp_credentials()
            ssh, sftp = _create_sftp_client(**creds)

            parent = str(Path(remote_path).parent)
            try:
                sftp.stat(parent)
            except FileNotFoundError:
                logger.info("Creating remote directory: %s", parent)
                sftp.mkdir(parent)

            uploaded = 0
            start = time.time()

            with open(local, "rb") as f:
                sftp.putfo(f, remote_path)

            elapsed = time.time() - start
            rate = file_size / elapsed if elapsed > 0 else 0
            logger.info(
                "Uploaded %s in %.1fs (%s/s)",
                local.name,
                elapsed,
                _human_size(int(rate)),
            )
            _close_sftp(ssh, sftp)
            return True

        except (paramiko.SSHException, OSError, IOError) as exc:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, local.name, exc)
            _close_sftp(ssh, sftp)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    logger.error("Failed to upload %s after %d attempts", local_path, MAX_RETRIES)
    return False


def upload_batch(local_dir: str, remote_dir: str = "/incoming") -> dict:
    """Upload all JPEG/JPG files from a directory. Returns summary dict."""
    local_path = Path(local_dir)
    if not local_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {local_dir}")

    image_extensions = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".eps", ".ai"}
    files = [f for f in local_path.iterdir() if f.suffix.lower() in image_extensions]

    if not files:
        logger.warning("No image files found in %s", local_dir)
        return {"total": 0, "success": 0, "failed": 0, "files": []}

    logger.info("Found %d image files to upload", len(files))

    results = {"total": len(files), "success": 0, "failed": 0, "files": []}

    for file in sorted(files):
        remote_path = f"{remote_dir.rstrip('/')}/{file.name}"
        ok = upload_file(str(file), remote_path)
        entry = {"file": file.name, "remote": remote_path, "size": _human_size(file.stat().st_size)}
        if ok:
            results["success"] += 1
            entry["status"] = "ok"
        else:
            results["failed"] += 1
            entry["status"] = "failed"
        results["files"].append(entry)

    logger.info(
        "Batch complete: %d/%d uploaded successfully",
        results["success"],
        results["total"],
    )
    return results


def upload_csv(csv_path: str) -> bool:
    """Upload a metadata CSV file to the remote incoming directory."""
    csv_file = Path(csv_path)
    if not csv_file.exists():
        logger.error("CSV file does not exist: %s", csv_path)
        return False
    if csv_file.suffix.lower() != ".csv":
        logger.warning("File does not have .csv extension: %s", csv_path)

    remote_path = f"/incoming/{csv_file.name}"
    return upload_file(str(csv_file), remote_path)


def test_connection() -> bool:
    """Test SFTP connectivity and credentials."""
    logger.info("Testing SFTP connection...")
    try:
        creds = _get_sftp_credentials()
        ssh, sftp = _create_sftp_client(**creds)
        pwd = sftp.normalize(".")
        logger.info("Connected. Remote root: %s", pwd)
        _close_sftp(ssh, sftp)
        return True
    except ValueError as exc:
        logger.error("Credential error: %s", exc)
        return False
    except (paramiko.SSHException, OSError) as exc:
        logger.error("Connection failed: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=== Adobe Stock SFTP Test ===")
    if test_connection():
        print("Connection successful!")
    else:
        print("Connection failed. Check SFTP_HOST, SFTP_USERNAME, SFTP_PASSWORD env vars.")
