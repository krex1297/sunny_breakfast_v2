# Sunny 陽光早餐（全 Python 架構版 v2）

一套以 Python 為核心的雲端早餐店點餐與營運管理系統，整合 **FastAPI** 後端服務、**TiDB Cloud** 雲端資料庫、**Firebase Authentication** 身分驗證（支援 `ADMIN_EMAILS` 自動綁定店長權限）、**Flet** 桌機點餐客戶端（固定 `flet==0.28.3`）與 **Streamlit** 店長營運後台。

## 系統架構與目錄結構

```text
sunny_breakfast_v2/
├── backend/
│   ├── main.py            # FastAPI 主程式、REST API、防超賣與 Gemini 點餐解析
│   ├── db.py              # TiDB Cloud 連線池管理（db_conn）
│   └── auth.py            # Firebase Token 驗證與店長（ADMIN）權限控管
├── client_flet/
│   └── app.py             # Flet 顧客點餐與查單介面（flet==0.28.3）
├── admin_streamlit/
│   └── dashboard.py       # Streamlit 店長營運後台（儀表板、即時訂單、菜單庫存）
├── test_setup.py          # TiDB 與 Firebase 連線自我檢測腳本
├── requirements.txt       # 專案套件版本清單
├── .gitignore             # 排除 .venv/、__pycache__/、.env 與 serviceAccountKey.json
└── start_all.bat          # Windows 一鍵啟動後端、Flet 與 Streamlit 批次檔