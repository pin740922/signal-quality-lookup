# -*- coding: utf-8 -*-
"""知識庫匯入 / 檢視工具。

用法
----
把文件（.md / .txt，安裝 pypdf 後亦支援 .pdf）放進 `knowledge/` 資料夾，執行：

    python ingest_knowledge.py

即會重新掃描並印出每個檔案切出的知識塊數量。索引為「記憶體內」建立，
伺服器啟動時會自動載入 `knowledge/`，因此新增文件後重啟伺服器即可生效
（本機用 start_local.bat；雲端 push 後自動重新部署）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rf_assistant as ra  # noqa: E402


def main():
    n = ra.load_knowledge(force=True)
    by_src = {}
    for ch in ra._chunks:  # noqa: SLF001 - 工具腳本，可存取內部結構
        by_src[ch["source"]] = by_src.get(ch["source"], 0) + 1
    print(f"知識庫資料夾：{ra.KB_DIR}")
    if not by_src:
        print("（目前沒有任何可讀取的文件，請放入 .md / .txt / .pdf）")
        return
    print(f"總計 {n} 個知識塊：")
    for src, cnt in sorted(by_src.items()):
        print(f"  - {src}: {cnt} 塊")
    # 簡單檢索測試
    q = "PA 區域怎麼判定"
    print(f"\n檢索測試「{q}」前 3 名：")
    for r in ra.retrieve(q, k=3):
        preview = r["text"].replace("\n", " ")[:60]
        print(f"  [{r['score']}] {r['source']}：{preview}…")


if __name__ == "__main__":
    main()
