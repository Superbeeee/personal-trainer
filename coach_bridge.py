"""
Kronos Coach — 路線二:Telegram → 本機 Claude Code (claude CLI) 橋接

Telegram 訊息會直接驅動你本機的 `claude` CLI:它在 COACH_DIR 裡真的讀寫
.md、跑工具,並用你現有的 Claude Code 登入(吃訂閱額度,不另外開
ANTHROPIC_API_KEY)。每個 Telegram chat 各維持一條 claude session 記憶。

跑法:
    1. CLAUDE.md 已在本資料夾,COACH_DIR 預設指到這裡
    2. 填好 .env(見 .env.example)
    3. python coach_bridge.py
    ※ 你的電腦要一直開著,bot 才會在線。
"""

import os
import json
import time
import uuid
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 自動載入同目錄 .env

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ── 設定 ────────────────────────────────────────────────
# 教練資料夾:claude 會 cd 進這裡讀寫(CLAUDE.md + logs/ 都在此)。
# 預設 coach/ 子資料夾,刻意不含 .env 和程式碼,教練的工作目錄碰不到鑰匙。
COACH_DIR = Path(
    os.environ.get("COACH_DIR", Path(__file__).parent / "coach")
).expanduser().resolve()
# claude 是 shell alias,subprocess 認不得,要用真實路徑
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(Path.home() / ".claude/local/claude"))
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# 只允許你自己用:填你的 Telegram 數字 ID(見 README)
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_TELEGRAM_USER_ID", "0"))
# 留空 = 用 Claude Code 的預設模型;要指定就填 opus / claude-opus-4-8
MODEL = os.environ.get("CLAUDE_MODEL", "").strip()
# acceptEdits = 自動允許讀寫檔案(不會跑任意 Bash);要全放行改 bypassPermissions
PERMISSION_MODE = os.environ.get("PERMISSION_MODE", "acceptEdits").strip()
# 免詢問就放行的工具:純本機紀錄不需額外工具(讀寫檔案已由 acceptEdits 放行)
# 若日後又要接 MCP(如 Notion)再填,例如 "mcp__notion"
ALLOWED_TOOLS = os.environ.get("ALLOWED_TOOLS", "").strip()
# 單次回覆逾時(秒);教練若要跑工具會花點時間
TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
# session 閒置自動重開(分鐘):超過這時間沒講話,下一則就自動開新 session,
# 避免對話越拉越長、token 越燒越兇。設 0 = 永不自動重開(維持同一條直到 /new)
SESSION_IDLE_MINUTES = float(os.environ.get("SESSION_IDLE_MINUTES", "30"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("kronos")

# 每個 chat 一條 claude session,維持對話記憶
_sessions: dict[int, str] = {}
# 每個 chat 最後一次講話的時間(monotonic 秒),用來判斷閒置自動重開
_last_seen: dict[int, float] = {}
# 同一個 chat 的訊息要序列化處理,避免並發 resume 同一條 session
_locks: dict[int, asyncio.Lock] = {}


def _lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


def _authorized(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True  # 沒設就不限制(自用時強烈建議要設)
    return bool(update.effective_user) and update.effective_user.id == ALLOWED_USER_ID


async def _run_claude(chat_id: int, text: str, *, resume: bool = True) -> str:
    """呼叫一次 claude CLI(headless),回傳教練的文字回覆。"""
    # 閒置太久就丟掉舊 session、自動開新的(省 token)
    now = time.monotonic()
    if (
        resume
        and SESSION_IDLE_MINUTES > 0
        and chat_id in _sessions
        and now - _last_seen.get(chat_id, now) > SESSION_IDLE_MINUTES * 60
    ):
        log.info("chat %s 閒置逾 %s 分鐘,自動開新 session", chat_id, SESSION_IDLE_MINUTES)
        _sessions.pop(chat_id, None)
    _last_seen[chat_id] = now

    sid = _sessions.get(chat_id) if resume else None
    cmd = [
        CLAUDE_BIN,
        "-p",
        text,
        "--output-format",
        "json",
        "--permission-mode",
        PERMISSION_MODE,
    ]
    if ALLOWED_TOOLS:
        cmd += ["--allowedTools", ALLOWED_TOOLS]
    if MODEL:
        cmd += ["--model", MODEL]
    if sid:
        cmd += ["--resume", sid]  # 續上既有 session
    else:
        sid = str(uuid.uuid4())   # 新對話:自己指定一個 UUID
        cmd += ["--session-id", sid]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(COACH_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"claude 逾時({TIMEOUT}s),可調大 CLAUDE_TIMEOUT")

    if proc.returncode != 0:
        msg = (err or b"").decode(errors="replace").strip()
        # resume 失敗(session 檔不見了)→ 清掉、用新 session 重試一次
        if resume and _sessions.get(chat_id) and "session" in msg.lower():
            log.warning("resume 失敗,改開新 session 重試:%s", msg[:200])
            _sessions.pop(chat_id, None)
            return await _run_claude(chat_id, text, resume=False)
        raise RuntimeError(msg[:500] or f"claude 退出碼 {proc.returncode}")

    data = json.loads((out or b"").decode())
    _sessions[chat_id] = data.get("session_id", sid)
    if data.get("is_error"):
        raise RuntimeError((data.get("result") or "claude 回報錯誤")[:500])
    return (data.get("result") or "").strip() or "（教練沒有回覆,請再試一次）"


# ── Telegram handlers ───────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Kronos 教練(Claude Code 版)已連線。直接傳訊息開始,例如:\n"
        "「今天想練下肢,昨天跑了 15K,RPE 6,給我今天的課表」\n\n"
        "/new 開新的一段對話(清空記憶)"
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id
    _sessions.pop(chat_id, None)
    _last_seen.pop(chat_id, None)
    await update.message.reply_text("已開新對話,先前的記憶已清空。")


async def _keep_typing(bot, chat_id: int, stop: asyncio.Event):
    """每幾秒送一次「輸入中」,讓長回覆期間 Telegram 一直顯示打字狀態。"""
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await update.message.reply_text("未授權的使用者。")
        return
    chat_id = update.effective_chat.id
    stop = asyncio.Event()
    typing = asyncio.create_task(_keep_typing(ctx.bot, chat_id, stop))
    try:
        async with _lock(chat_id):  # 同一 chat 一次只跑一個 claude
            reply = await _run_claude(chat_id, update.message.text)
    except Exception as e:
        log.exception("coach error")
        reply = f"出錯了:{e}"
    finally:
        stop.set()
        await typing

    # Telegram 單則上限 4096 字,長訊息切段
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i : i + 4000])


def main():
    if not COACH_DIR.exists():
        raise SystemExit(f"教練資料夾不存在:{COACH_DIR}")
    if not Path(CLAUDE_BIN).exists():
        raise SystemExit(
            f"找不到 claude 執行檔:{CLAUDE_BIN}\n"
            "請在 .env 設 CLAUDE_BIN 指向真實路徑("
            "用 `which claude` 看 alias 指到哪)。"
        )
    if not (COACH_DIR / "CLAUDE.md").exists():
        log.warning("注意:%s 裡沒有 CLAUDE.md,教練人設可能不會載入", COACH_DIR)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info(
        "Kronos 教練(Claude Code 版)啟動,資料夾:%s,claude:%s,模型:%s",
        COACH_DIR,
        CLAUDE_BIN,
        MODEL or "(預設)",
    )
    app.run_polling()


if __name__ == "__main__":
    main()
