from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
DOCS_DIR = ROOT / "docs"


def main() -> None:
    now = datetime.now(timezone.utc)
    OUTPUT_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)

    payload = {
        "project": "vimo-VN",
        "status": "ok",
        "data_found": True,
        "generated_at_utc": now.isoformat(),
        "note": "This is the first automated GitHub Actions output. Replace scripts/run_report.py with the real macro-data pipeline when ready.",
    }

    (OUTPUT_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    html = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>vimo-VN</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.5; color: #18212f; }}
    main {{ max-width: 820px; }}
    code {{ background: #eef2f7; padding: 2px 6px; border-radius: 4px; }}
  </style>
</head>
<body>
  <main>
    <h1>vimo-VN</h1>
    <p>Status: <strong>{payload["status"]}</strong></p>
    <p>Generated UTC: <code>{payload["generated_at_utc"]}</code></p>
    <p>{payload["note"]}</p>
  </main>
</body>
</html>
"""
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
