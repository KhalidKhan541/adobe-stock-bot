#!/usr/bin/env python3
"""
Pipeline Alerts — Send email notifications on pipeline failure/success.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path


def send_alert(
    subject: str,
    body: str,
    alert_type: str = "failure",
    pipeline_name: str = "Unknown",
) -> bool:
    """Send email alert."""
    sender = os.environ.get("SENDER_EMAIL", "khalid.khan46571@gmail.com")
    password = os.environ.get("GMAIL_APP_PASSWORD", "velr opzr cwpr vqyt").replace(" ", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)

    if not sender or not password:
        print("ERROR: Email credentials not set. Cannot send alert.")
        return False

    # Color coding
    color = "#e74c3c" if alert_type == "failure" else "#2ecc71"
    icon = "✗" if alert_type == "failure" else "✓"

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background-color: {color}; color: white; padding: 20px; text-align: center;">
            <h1 style="margin: 0;">{icon} Pipeline {alert_type.title()}</h1>
        </div>
        <div style="padding: 20px; border: 1px solid #ddd;">
            <table style="width: 100%; border-collapse: collapse;">
                <tr><td style="padding: 8px; font-weight: bold;">Pipeline:</td><td style="padding: 8px;">{pipeline_name}</td></tr>
                <tr><td style="padding: 8px; font-weight: bold;">Time:</td><td style="padding: 8px;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                <tr><td style="padding: 8px; font-weight: bold;">Status:</td><td style="padding: 8px; color: {color}; font-weight: bold;">{alert_type.upper()}</td></tr>
            </table>
            <h3>Details:</h3>
            <pre style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto;">{body}</pre>
        </div>
        <div style="text-align: center; padding: 10px; color: #999; font-size: 12px;">
            Pipeline Harness Alert System
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"[Pipeline {icon}] {subject}"
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        print(f"Alert sent: {subject}")
        return True
    except Exception as e:
        print(f"Failed to send alert: {e}")
        return False


def alert_pipeline_failure(pipeline_name: str, error: str, log_file: str = "") -> bool:
    """Send pipeline failure alert."""
    body = f"Pipeline: {pipeline_name}\n"
    body += f"Error: {error}\n"
    if log_file:
        body += f"Log: {log_file}\n"
    body += f"\nTime: {datetime.now().isoformat()}\n"
    body += "\nPlease check the pipeline and fix the issue."

    return send_alert(
        subject=f"{pipeline_name} FAILED",
        body=body,
        alert_type="failure",
        pipeline_name=pipeline_name,
    )


def alert_pipeline_success(pipeline_name: str, summary: str) -> bool:
    """Send pipeline success alert."""
    return send_alert(
        subject=f"{pipeline_name} completed successfully",
        body=summary,
        alert_type="success",
        pipeline_name=pipeline_name,
    )


if __name__ == "__main__":
    # Test alert
    send_alert(
        subject="Pipeline Harness Test",
        body="This is a test alert from the pipeline harness.",
        alert_type="success",
        pipeline_name="Test",
    )
