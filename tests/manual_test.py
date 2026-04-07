from document_processor import DocIR
from pydantic import BaseModel

import json
from os import listdir
from pathlib import Path

file_name = "style_test_sample.docx"
# "style_test_sample.docx"
# "251029 2025년 3회 추경 사업설명서(평화협력국)_최종.hwpx"
# "2026년_전통시장_육성사업(백년시장)_모집공고(수정).hwpx"

doc_dir = Path("/home/maxjo/Work/LAS-system/apps/backend/doc_processor/tests/doc_samples/new_test")
out_dir = Path("/home/maxjo/Work/LAS-system/apps/backend/doc_processor/tests/results")

files = [doc_dir / file for file in listdir(doc_dir)]


for file_ in files:
    doc = DocIR.from_file(file_)

    # == Add metadata == #

    class ClauseMeta(BaseModel):
        a: int = 1
        b: str = "test"

    metainfo = ClauseMeta(a=1)
    doc.paragraphs[0].runs[0].meta = metainfo

    # ================== #


    with \
        open((out_dir / file_.stem).with_suffix(".json"), "w", encoding="utf-8") as json_f, \
        open((out_dir / file_.stem).with_suffix(".html"), "w", encoding="utf-8") as html_f:
        
        json.dump(doc.model_dump(mode="json"), json_f, indent=4, ensure_ascii=False)
        html_f.write(doc.to_html())

    print(f"completed: {file_}")
