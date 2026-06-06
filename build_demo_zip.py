"""打包 demo 資料 ZIP：將指定城市的 4G/5G 合併 CSV 壓成部署用 ZIP。

ZIP 內部結構：TWM_MDT_City_25m/{4G,5G}/All/<City>/<City>.csv
"""
import os
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")

BASE = r"C:\Users\tsuwang\Desktop\我的資料夾\PIN_Cursor\test1\TWM_MDT_City_25m"
CITIES = [
    "Taipei_City",
    "Taichung_City",
    "Kaohsiung_City",
    "Taoyuan_City",
]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data_4cities.zip")


def main():
    members = []
    for net in ("4G", "5G"):
        for city in CITIES:
            src = os.path.join(BASE, net, "All", city, city + ".csv")
            if not os.path.exists(src):
                print(f"[警告] 找不到：{src}")
                continue
            arc = f"TWM_MDT_City_25m/{net}/All/{city}/{city}.csv"
            members.append((src, arc))

    total = sum(os.path.getsize(s) for s, _ in members)
    print(f"共 {len(members)} 個檔案，原始大小 {total/1024/1024:.1f} MB，開始壓縮...")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arc in members:
            print(f"  + {arc} ({os.path.getsize(src)/1024/1024:.1f} MB)")
            zf.write(src, arc)
    print(f"完成：{OUT} = {os.path.getsize(OUT)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
