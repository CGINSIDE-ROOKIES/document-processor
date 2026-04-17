from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from document_processor.env_utils import load_dotenv_file


def run_openai_pdf_vlm_smoke(*, limit: int = 1) -> list[dict[str, object]]:
    """Call the OpenAI VLM compare script against a small page subset.

    This is an opt-in smoke helper for local verification only.
    It expects `.env` to define `OPENAI_API_KEY` and `RUN_OPENAI_VLM_TEST=1`.
    """
    load_dotenv_file(REPO_ROOT / ".env")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    review_root = Path(os.environ.get("PDF_VLM_REVIEW_ROOT", "out/pdf-review/text-pdf"))
    manifest_path = Path(os.environ.get("PDF_VLM_MANIFEST", review_root / "vlm-manifest.jsonl"))
    script_path = REPO_ROOT / "scripts" / "compare_pdf_review_pages_openai.py"

    with tempfile.TemporaryDirectory(prefix="pdf-vlm-smoke-") as temp_dir:
        output_path = Path(temp_dir) / "findings.jsonl"
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = api_key
        subprocess.run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "python"),
                str(script_path),
                "--review-root",
                str(review_root),
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
                "--limit",
                str(limit),
            ],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        return [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class PdfVlmCompareSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        (
            load_dotenv_file(REPO_ROOT / ".env") or True
        )
        and os.environ.get("RUN_OPENAI_VLM_TEST") == "1"
        and bool(os.environ.get("OPENAI_API_KEY")),
        "Set OPENAI_API_KEY and RUN_OPENAI_VLM_TEST=1 in .env to run this smoke test.",
    )
    def test_openai_pdf_vlm_smoke(self) -> None:
        records = run_openai_pdf_vlm_smoke(limit=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["vlm_status"], "ok")
        self.assertIn("vlm_findings", records[0])
        self.assertIsInstance(records[0]["vlm_findings"], dict)


if __name__ == "__main__":
    unittest.main()
