# 行動網路 RF 關鍵指標與判讀準則（4G LTE / 5G NR）

> 本文件作為「資深 RF 工程師問答助理」的知識來源。請以此為準回答數值門檻與判讀邏輯。

## 1. RSRP（Reference Signal Received Power，參考訊號接收功率）
- 定義：UE 接收到參考訊號的平均功率，反映「訊號強度 / 覆蓋」。單位 dBm，數值越大越好。
- 分級（本系統採用）：
  - 優良：RSRP ≥ -85 dBm
  - 良好：-95 ≤ RSRP < -85 dBm
  - 普通：-105 ≤ RSRP < -95 dBm
  - 較弱：-115 ≤ RSRP < -105 dBm
  - 微弱：RSRP < -115 dBm
- Poor RSRP 門檻：RSRP < -110 dBm 視為「弱訊」。
- 典型成因（RSRP 偏低）：基地台距離遠、建物穿透損耗（室內深處）、地形遮蔽、天線下傾/方位不當、覆蓋空洞（coverage hole）。

## 2. RSRQ（Reference Signal Received Quality，參考訊號接收品質）
- 定義：RSRQ = N × RSRP / RSSI，反映訊號「品質」，同時受負載與干擾影響。單位 dB。
- 參考：RSRQ ≤ -15 dB 通常代表高負載或干擾偏高；-10 dB 以上較佳。
- 用途：當 RSRP 尚可但 RSRQ 很差，常代表鄰區干擾或細胞負載過高。

## 3. SINR（Signal to Interference plus Noise Ratio）
- 定義：訊號相對於干擾加雜訊的比值，反映「可達速率 / 品質」。單位 dB。
- 參考：SINR > 13 dB 很好；0 ~ 13 dB 尚可；< 0 dB 偏差（干擾嚴重）。
- 當 SINR 為負值的格點比例偏高（如 ≥ 20%），通常代表重疊覆蓋（overshooting）或 PCI/鄰區干擾。

## 4. CQI（Channel Quality Indicator，0~15）
- 反映通道品質，越高越好。CQI < 7 通常代表通道品質不佳，調變階數受限、吞吐量下降。

## 5. 下行吞吐量（Downlink Throughput）
- 反映實際用戶體驗。即使 RSRP/SINR 良好，若下行速率偏低（尤其最差 10% 分位 p10 很低），常代表容量壅塞（congestion）。

## 6. PoorRSRPPercentage（弱訊樣本佔比）
- 定義：某 binning 點內，RSRP 低於 Poor RSRP 門檻（-110 dBm）的量測（MR）數量，佔該點所有 MR 數量的比例（%）。
- 用途：用來標示「該點長期/多數量測都很弱」，比單一平均值更能反映真實弱訊熱點。

## 7. PA 區域（弱覆蓋 / 待優化區域）判定（本系統定義）
以 DBSCAN 空間分群（鄰域半徑 eps = 100 公尺），把符合條件的點分群後框出：
- **4G PA 區域**：組成點為「iPhone 1 Bar」；DBSCAN minPoints = 12。
  - iPhone 1 Bar 判定（滿足任一）：
    - RSRQ ≤ -15 dB 且 RSRP ≤ -85 dBm
    - -15 < RSRQ ≤ -14 dB 且 RSRP ≤ -100 dBm
- **5G PA 區域**：組成點為 PoorRSRPPercentage ≥ 20%；DBSCAN minPoints = 3。
- DBSCAN：在 eps 半徑內鄰點數 ≥ minPoints 者為核心點，密度相連的核心點與其邊界點形成一個群；密度不足者視為雜訊不框選。

## 8. 根因分析（綜合多指標）
- **覆蓋不足（coverage gap）**：RSRP 偏低（< -105）或弱訊格點比例高。對策：新站/補點、調整天線下傾與方位、提高功率、室內覆蓋（DAS / small cell / femto）。
- **干擾 / 重疊覆蓋（interference / overshooting）**：RSRP 尚可但 SINR/RSRQ 偏低、SINR 負值比例高。對策：PCI 重規劃、調整天線下傾收斂覆蓋、ICIC/鄰區優化。
- **容量壅塞（congestion）**：RSRP 與 SINR 都好但下行速率偏低（尤其 p10）。對策：載波聚合 / 加頻段、擴容、負載平衡、分流。
- **點狀死角（spotty dead zone）**：RSRP 標準差大、覆蓋不均。對策：現場逐點勘查、補點、室內方案。

## 9. 客訴 / 門市常見情境對應
- 「在家收訊差、上網慢」：先確認室內 vs 室外、樓層；查該址周邊 RSRP/SINR 與弱訊比例；室內深處弱訊多半為穿透損耗，建議測試靠窗、或評估室內增強。
- 「特定路段斷訊」：查該區是否為 PA 區域（弱覆蓋熱點），回報優化。
- 「速度慢但格數滿」：可能容量壅塞或 SINR 偏低（干擾），非單純覆蓋問題。
- 回覆原則：先安撫並說明可能原因，提供可行建議（重開機、APN、靠窗、改 Wi-Fi 通話 VoWiFi），需要工程處理者登記回報。

## 10. 注意事項
- 數值門檻可能因營運商策略不同而調整；回答時若引用門檻，請說明依據。
- 真正的派工、調參與新站決策需工程團隊以完整 OSS/路測資料複核，本助理提供初步判讀與方向。
