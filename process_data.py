# -*- coding: utf-8 -*-
"""來源資料整理腳本。

依使用者需求：
  a. 在來源資料夾中建立全台灣的縣/市名稱資料夾。
  b. 依 CSV 檔名前三碼 (post code) 將檔案搬移到對應的縣/市資料夾。
  c. 將各縣/市資料夾中的 CSV 合併成一個 CSV，檔名為該縣市之英文名稱。

使用方式：
  python process_data.py            # 完整執行 (搬移 + 合併)
  python process_data.py --dry-run  # 僅顯示將執行的動作，不實際變動
  python process_data.py --no-merge # 只搬移，不合併
"""

import argparse
import csv
import os
import shutil
import sys

from postcode_mapping import lookup, all_counties

SOURCE_DIR = r"C:\Users\tsuwang\Desktop\我的資料夾\PIN_Cursor\test1\TWM_MDT_City_25m\4G\All"


def is_county_folder_name(name):
    """判斷某資料夾名稱是否為我們建立的縣市英文資料夾。"""
    return name in {en for _zh, en in all_counties()}


def create_county_folders(source_dir, dry_run=False):
    """步驟 a：建立全台灣縣/市資料夾 (以英文名稱命名)。"""
    created = []
    for zh, en in all_counties():
        path = os.path.join(source_dir, en)
        if not os.path.isdir(path):
            if not dry_run:
                os.makedirs(path, exist_ok=True)
            created.append(f"{en} ({zh})")
    print(f"[步驟 a] 建立縣市資料夾：新建 {len(created)} 個，共 {len(all_counties())} 個縣市。")
    for c in created:
        print(f"         + {c}")
    return created


def move_csv_files(source_dir, dry_run=False):
    """步驟 b：依檔名前三碼搬移 CSV 至對應縣市資料夾。"""
    moved = 0
    skipped = []
    # 只處理 All 根目錄下的 csv (不含已分類到子資料夾的檔案)
    entries = [f for f in os.listdir(source_dir)
               if f.lower().endswith(".csv") and os.path.isfile(os.path.join(source_dir, f))]
    for fname in sorted(entries):
        postcode = fname[:3]
        zh, en = lookup(postcode)
        if en is None:
            skipped.append((fname, postcode))
            continue
        dst_dir = os.path.join(source_dir, en)
        dst = os.path.join(dst_dir, fname)
        if not dry_run:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(os.path.join(source_dir, fname), dst)
        moved += 1
    print(f"[步驟 b] 搬移 CSV：成功 {moved} 個。")
    if skipped:
        print(f"         ! 無法對應的檔案 {len(skipped)} 個：")
        for fname, pc in skipped:
            print(f"           - {fname} (post code={pc})")
    return moved, skipped


def merge_county_csv(source_dir, dry_run=False):
    """步驟 c：將每個縣市資料夾中的 CSV 合併成 <英文名稱>.csv。

    合併檔放在該縣市資料夾內，標頭只保留一份。
    """
    summary = []
    for zh, en in all_counties():
        folder = os.path.join(source_dir, en)
        if not os.path.isdir(folder):
            continue
        merged_name = f"{en}.csv"
        merged_path = os.path.join(folder, merged_name)
        # 取得待合併的來源檔 (排除合併檔本身)
        parts = sorted(f for f in os.listdir(folder)
                       if f.lower().endswith(".csv") and f != merged_name
                       and os.path.isfile(os.path.join(folder, f)))
        if not parts:
            continue
        if dry_run:
            print(f"[步驟 c] {en} ({zh})：將合併 {len(parts)} 個檔 -> {merged_name}")
            summary.append((en, len(parts), 0))
            continue

        rows_written = 0
        header_written = False
        with open(merged_path, "w", newline="", encoding="utf-8") as out_f:
            writer = None
            for part in parts:
                ppath = os.path.join(folder, part)
                with open(ppath, "r", newline="", encoding="utf-8-sig") as in_f:
                    reader = csv.reader(in_f)
                    try:
                        header = next(reader)
                    except StopIteration:
                        continue
                    if not header_written:
                        writer = csv.writer(out_f)
                        writer.writerow(header)
                        header_written = True
                    for row in reader:
                        if not row:
                            continue
                        writer.writerow(row)
                        rows_written += 1
        summary.append((en, len(parts), rows_written))
        print(f"[步驟 c] {en} ({zh})：合併 {len(parts)} 個檔、{rows_written:,} 筆 -> {merged_name}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="整理 TWM MDT 來源資料")
    parser.add_argument("--source", default=SOURCE_DIR, help="來源 All 資料夾路徑")
    parser.add_argument("--dry-run", action="store_true", help="僅顯示動作，不實際變動")
    parser.add_argument("--no-merge", action="store_true", help="只搬移，不合併")
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        print(f"找不到來源資料夾：{args.source}", file=sys.stderr)
        sys.exit(1)

    print(f"來源資料夾：{args.source}")
    print(f"模式：{'乾跑 (dry-run)' if args.dry_run else '實際執行'}")
    print("=" * 60)

    create_county_folders(args.source, dry_run=args.dry_run)
    print("-" * 60)
    move_csv_files(args.source, dry_run=args.dry_run)
    print("-" * 60)
    if not args.no_merge:
        merge_county_csv(args.source, dry_run=args.dry_run)
    print("=" * 60)
    print("完成。")


if __name__ == "__main__":
    main()
