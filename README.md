# Kronos 教練 — Telegram 隨身耐力肌力教練

用 Telegram 跟「耐力運動員肌力教練」對話。Telegram 訊息會直接驅動你本機的
**Claude Code (`claude` CLI)**:它讀取 `coach/CLAUDE.md`(已整合好的人設 + 你
的身體資料),把你的訓練紀錄寫進 `coach/logs/YYYY-MM.md`,並用你現有的
Claude Code 登入(吃訂閱額度,不必另外申請 API key)。

```
personal-trainer/
├── coach_bridge.py     ← Telegram ↔ claude 橋接(主程式)
├── .env                ← 你的設定(token 等;不進 git)
└── coach/              ← 教練的工作目錄(刻意不含 .env / 程式碼)
    ├── CLAUDE.md       ← 教練人設
    └── logs/
        └── 2026-06.md  ← 訓練紀錄(一月一檔)
```

---

## 一次性設定

### 步驟一：建立 Telegram bot，拿 token
1. Telegram 搜尋 **@BotFather** → `/newbot` → 取名 → 拿到一串 **token**
2. 填進 `.env` 的 `TELEGRAM_BOT_TOKEN`

### 步驟二：拿你自己的 Telegram 數字 ID（限制只有你能用）
1. Telegram 搜尋 **@userinfobot** → 它會回你的數字 ID
2. 填進 `.env` 的 `ALLOWED_TELEGRAM_USER_ID`
   （**務必設**：沒設等於讓任何人遠端驅動你本機的 `claude`）

### 步驟三：確認 claude CLI 路徑
`claude` 多半是 shell alias，subprocess 認不得，要給**真實路徑**：
```bash
which claude          # 例如 /Users/你/.claude/local/claude
```
填進 `.env` 的 `CLAUDE_BIN`。確認你平常 `claude` 能正常用（就吃這份登入）。

---

## 本機跑

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 填好 TELEGRAM_BOT_TOKEN / ALLOWED_TELEGRAM_USER_ID / CLAUDE_BIN
.venv/bin/python coach_bridge.py
```

看到「Kronos 教練（Claude Code 版）啟動」就成功了。打開 Telegram 找你的 bot，
傳 `/start`，然後直接講話，例如：
- 「今天想練下肢，昨天跑了 15K，RPE 6，給我今天的課表」
- 「今天練胸：啞鈴臥推 4 組 12 下 25kg，幫我記錄」→ 教練寫進 `coach/logs/`

### 指令與行為
- `/new`：開新對話、清空記憶（換週期或想重來時用）
- **session 閒置自動重開**：預設閒置 30 分鐘後，下一則訊息自動開新 session，
  避免對話越拉越長、吃太多額度。用 `.env` 的 `SESSION_IDLE_MINUTES` 調整
  （`0` = 不自動重開）。
- ⚠️ **bot 重啟後對話記憶會斷**：session 對應只存在記憶體，重開程式就重來
  （訓練紀錄已落地 `logs/`，不受影響）。
- **電腦關機 / 睡眠時 bot 就停了** —— 它要驅動你本機的 claude，這是本質限制。

### 權限說明
`PERMISSION_MODE` 預設 `acceptEdits`：教練可**自動讀寫 `coach/` 裡的檔案**，但
**不會**跑任意 Bash。`.env` 和程式碼放在 `coach/` 外面，教練的工作目錄碰不到。

---

## 24h 常駐（本機長開）

要驅動本機 claude，所以**不適合搬上一般 VPS**（VPS 上沒有你的 Claude Code
登入）。最務實是讓自己的電腦長開，用 tmux 常駐：

```bash
tmux new -s kronos
.venv/bin/python coach_bridge.py
# Ctrl-b d 離開（程式繼續跑）；之後 tmux attach -t kronos 回來看
```

> 想真正 24h，可考慮一台長開的 Mac mini／舊筆電，在上面登入 Claude Code 後跑。

---

## 備份（選用）

`coach/logs/` 是純 Markdown，丟 iCloud/Dropbox 就有異地備份，或 `git init` 後
推到 GitHub 留版本歷史。
> ⚠️ 訓練紀錄是個人資料，推 GitHub **務必用 private(私有) repo**。

## 安全提醒
- `.env` 絕對不要 commit（`.gitignore` 已排除）
- **一定要設 `ALLOWED_TELEGRAM_USER_ID`**：沒設 = 任何人都能遠端驅動你本機的
  claude、讀寫你的檔案
- 非必要別把 `PERMISSION_MODE` 開成 `bypassPermissions`
