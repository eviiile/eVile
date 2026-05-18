#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت روليت سياف - الإصدار النهائي للويب هوك (Render + Python 3.11.11)
يستخدم: python-telegram-bot v20.7 + asyncpg + Flask + Webhook
"""

import asyncio
import logging
import random
import os
import sys
import threading
from typing import List, Tuple, Dict, Optional

import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext,
)
from telegram.error import TelegramError
from flask import Flask, request, jsonify
import signal

# ---------- الإعدادات ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8602564332:AAEU3Juyopfg4l1PXXqe5kwKVqABqZYrO5o")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6689435577"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://roleete_user:Zezw05RI12oaJ3EPYiiTz3lTwefNyqJu@dpg-d831vrbtqb8s73bja8l0-a.oregon-postgres.render.com/roleete")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://evile-roleet.onrender.com")
PORT = int(os.getenv("PORT", "10000"))

MAIN_PHOTO_URL = "https://kommodo.ai/i/T6qXdlO5OHv0yBb3v5Pw"
TARGET_BOT_USERNAME = "VD67_BOT"
TARGET_BOT_URL = f"https://t.me/{TARGET_BOT_USERNAME}"
DEFAULT_CONDITION_CHANNEL = "@Srr990"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def wrap_text(text: str) -> str:
    """تغليف النص بـ blockquote expandable"""
    return f"<blockquote expandable>{text}</blockquote>"


# ---------- قاعدة البيانات ----------
class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=5, max_size=20)
            await self._init_db()
            logger.info("تم الاتصال بقاعدة البيانات بنجاح.")
        except Exception as e:
            logger.error(f"فشل الاتصال بقاعدة البيانات: {e}")
            raise

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("تم إغلاق اتصال قاعدة البيانات.")

    async def _init_db(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_channels (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    channel_username TEXT NOT NULL UNIQUE
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS condition_channels (
                    id SERIAL PRIMARY KEY,
                    channel_username TEXT NOT NULL UNIQUE,
                    is_default BOOLEAN DEFAULT FALSE
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS roulettes (
                    id SERIAL PRIMARY KEY,
                    owner_id BIGINT NOT NULL,
                    channel_username TEXT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    message_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    winner_id BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id SERIAL PRIMARY KEY,
                    roulette_id INTEGER NOT NULL REFERENCES roulettes(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(roulette_id, user_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS banned_channels (
                    channel_username TEXT PRIMARY KEY
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    notifications_enabled BOOLEAN DEFAULT TRUE
                );
            """)
            await conn.execute("""
                INSERT INTO condition_channels (channel_username, is_default)
                VALUES ($1, TRUE)
                ON CONFLICT (channel_username) DO NOTHING
            """, DEFAULT_CONDITION_CHANNEL)
            await conn.execute("""
                INSERT INTO bot_settings (key, value)
                VALUES ('status', 'running'),
                       ('notifications_enabled', 'true')
                ON CONFLICT (key) DO NOTHING
            """)

    # ---------- دوال مساعدة ----------
    async def _execute(self, query: str, *params):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *params)

    async def _fetchrow(self, query: str, *params):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *params)

    async def _fetch(self, query: str, *params):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *params)

    # ---------- قنوات المستخدم ----------
    async def get_user_channels(self, user_id: int) -> List[str]:
        rows = await self._fetch("SELECT channel_username FROM user_channels WHERE user_id = $1", user_id)
        return [r["channel_username"] for r in rows]

    async def add_user_channel(self, user_id: int, channel: str) -> bool:
        try:
            await self._execute(
                "INSERT INTO user_channels (user_id, channel_username) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id, channel,
            )
            return True
        except Exception:
            return False

    async def remove_user_channel(self, channel_id: int):
        await self._execute("DELETE FROM user_channels WHERE id = $1", channel_id)

    # ---------- قنوات الشرط ----------
    async def get_condition_channels(self) -> List[str]:
        rows = await self._fetch("SELECT channel_username FROM condition_channels")
        return [r["channel_username"] for r in rows]

    async def add_condition_channel(self, channel: str, is_default: bool = False) -> bool:
        try:
            await self._execute(
                "INSERT INTO condition_channels (channel_username, is_default) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                channel, is_default,
            )
            return True
        except Exception:
            return False

    async def remove_condition_channel(self, channel_id: int) -> bool:
        row = await self._fetchrow("SELECT is_default FROM condition_channels WHERE id = $1", channel_id)
        if row and row["is_default"]:
            return False
        await self._execute("DELETE FROM condition_channels WHERE id = $1", channel_id)
        return True

    async def get_condition_channel_by_id(self, channel_id: int):
        return await self._fetchrow("SELECT * FROM condition_channels WHERE id = $1", channel_id)

    # ---------- الروليت ----------
    async def create_roulette_get_id(self, owner_id: int, channel: str, chat_id: int, message_id: int, description: str) -> int:
        row = await self._fetchrow(
            "INSERT INTO roulettes (owner_id, channel_username, chat_id, message_id, description) VALUES ($1,$2,$3,$4,$5) RETURNING id",
            owner_id, channel, chat_id, message_id, description,
        )
        return row["id"]

    async def get_active_roulette_in_channel(self, channel: str):
        return await self._fetchrow("SELECT id FROM roulettes WHERE channel_username = $1 AND status = 'active'", channel)

    async def get_roulette(self, roulette_id: int):
        return await self._fetchrow("SELECT * FROM roulettes WHERE id = $1", roulette_id)

    async def get_active_roulettes_by_user(self, user_id: int) -> List[dict]:
        rows = await self._fetch(
            "SELECT id, channel_username, description, message_id, chat_id FROM roulettes WHERE owner_id = $1 AND status = 'active'",
            user_id,
        )
        return [dict(r) for r in rows]

    async def get_roulettes_by_owner_and_description(self, user_id: int, desc: str) -> List[dict]:
        rows = await self._fetch(
            "SELECT id, channel_username, chat_id, message_id FROM roulettes WHERE owner_id = $1 AND description = $2 AND status = 'active'",
            user_id, desc,
        )
        return [dict(r) for r in rows]

    async def get_all_active_channels(self) -> List[Tuple[str, int, int]]:
        rows = await self._fetch("SELECT DISTINCT channel_username, chat_id, message_id FROM roulettes WHERE status = 'active'")
        return [(r["channel_username"], r["chat_id"], r["message_id"]) for r in rows]

    async def update_roulette_winner(self, roulette_id: int, winner_id: int):
        await self._execute("UPDATE roulettes SET status = 'ended', winner_id = $1 WHERE id = $2", winner_id, roulette_id)

    async def delete_roulette_data(self, roulette_id: int):
        await self._execute("DELETE FROM participants WHERE roulette_id = $1", roulette_id)
        await self._execute("DELETE FROM roulettes WHERE id = $1", roulette_id)

    async def delete_roulettes_by_owner_and_description(self, user_id: int, desc: str):
        roulettes = await self.get_roulettes_by_owner_and_description(user_id, desc)
        for r in roulettes:
            await self.delete_roulette_data(r["id"])

    # ---------- المشاركون ----------
    async def add_participant(self, roulette_id: int, user_id: int) -> bool:
        try:
            await self._execute("INSERT INTO participants (roulette_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", roulette_id, user_id)
            return True
        except Exception:
            return False

    async def remove_participant(self, roulette_id: int, user_id: int):
        await self._execute("DELETE FROM participants WHERE roulette_id = $1 AND user_id = $2", roulette_id, user_id)

    async def get_participants(self, roulette_id: int) -> List[int]:
        rows = await self._fetch("SELECT user_id FROM participants WHERE roulette_id = $1", roulette_id)
        return [r["user_id"] for r in rows]

    async def get_participant_count(self, roulette_id: int) -> int:
        row = await self._fetchrow("SELECT COUNT(*) as cnt FROM participants WHERE roulette_id = $1", roulette_id)
        return row["cnt"] if row else 0

    async def get_grouped_user_contests(self, user_id: int) -> Dict[str, List[dict]]:
        rows = await self._fetch(
            "SELECT id, channel_username, description, chat_id, message_id FROM roulettes WHERE owner_id = $1 AND status = 'active'",
            user_id,
        )
        grouped = {}
        for r in rows:
            desc = r["description"]
            grouped.setdefault(desc, []).append({
                "id": r["id"], "channel_username": r["channel_username"],
                "chat_id": r["chat_id"], "message_id": r["message_id"],
            })
        return grouped

    # ---------- الحظر ----------
    async def is_user_banned(self, user_id: int) -> bool:
        row = await self._fetchrow("SELECT 1 FROM banned_users WHERE user_id = $1", user_id)
        return row is not None

    async def ban_user(self, user_id: int):
        await self._execute("INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)

    async def unban_user(self, user_id: int):
        await self._execute("DELETE FROM banned_users WHERE user_id = $1", user_id)

    async def is_channel_banned(self, channel: str) -> bool:
        row = await self._fetchrow("SELECT 1 FROM banned_channels WHERE channel_username = $1", channel)
        return row is not None

    async def ban_channel(self, channel: str):
        await self._execute("INSERT INTO banned_channels (channel_username) VALUES ($1) ON CONFLICT DO NOTHING", channel)

    async def unban_channel(self, channel: str):
        await self._execute("DELETE FROM banned_channels WHERE channel_username = $1", channel)

    # ---------- حالة البوت ----------
    async def get_bot_status(self) -> str:
        row = await self._fetchrow("SELECT value FROM bot_settings WHERE key = 'status'")
        return row["value"] if row else "running"

    async def set_bot_status(self, status: str):
        await self._execute("UPDATE bot_settings SET value = $1 WHERE key = 'status'", status)

    # ---------- إعدادات الإشعارات العامة ----------
    async def get_notifications_enabled(self) -> bool:
        row = await self._fetchrow("SELECT value FROM bot_settings WHERE key = 'notifications_enabled'")
        return row and row["value"] == "true"

    async def set_notifications_enabled(self, enabled: bool):
        await self._execute("UPDATE bot_settings SET value = $1 WHERE key = 'notifications_enabled'", str(enabled).lower())

    # ---------- إعدادات الإشعارات للمستخدم ----------
    async def get_user_notifications_enabled(self, user_id: int) -> bool:
        row = await self._fetchrow("SELECT notifications_enabled FROM user_settings WHERE user_id = $1", user_id)
        return row["notifications_enabled"] if row else True

    async def set_user_notifications(self, user_id: int, enabled: bool):
        await self._execute(
            "INSERT INTO user_settings (user_id, notifications_enabled) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET notifications_enabled = $2",
            user_id, enabled,
        )

    # ---------- جميع المستخدمين النشطين ----------
    async def get_all_active_user_ids(self) -> List[int]:
        rows = await self._fetch("""
            SELECT DISTINCT user_id FROM user_channels
            UNION
            SELECT DISTINCT owner_id FROM roulettes
        """)
        return [r["user_id"] for r in rows]


# إنشاء كائن قاعدة البيانات
db = Database(DATABASE_URL)


# ---------- دوال الإشعارات ----------
async def send_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    """إرسال إشعار للمستخدم إذا كانت الإعدادات تسمح"""
    try:
        if not await db.get_notifications_enabled():
            return
        if not await db.get_user_notifications_enabled(user_id):
            return
        await context.bot.send_message(chat_id=user_id, text=wrap_text(text), parse_mode="HTML")
    except TelegramError as e:
        logger.warning(f"فشل إرسال إشعار إلى {user_id}: {e}")
    except Exception as e:
        logger.error(f"خطأ غير متوقع في إرسال الإشعار: {e}")


async def broadcast_notification(context: ContextTypes.DEFAULT_TYPE, text: str):
    """إرسال إشعار لجميع المستخدمين النشطين"""
    user_ids = await db.get_all_active_user_ids()
    for uid in user_ids:
        await send_notification(context, uid, text)


# ---------- دوال المساعدة العامة ----------
async def check_subscriptions(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[bool, List[str]]:
    channels = await db.get_condition_channels()
    missing = []
    for ch in channels:
        try:
            chat_member = await context.bot.get_chat_member(chat_id=f"@{ch.replace('@', '')}", user_id=user_id)
            if chat_member.status in ("left", "kicked", "banned"):
                missing.append(ch)
        except TelegramError:
            missing.append(ch)
    return len(missing) == 0, missing


def build_main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("قنواتي", callback_data="my_channels")],
        [InlineKeyboardButton("بدء روليت", callback_data="start_roulette"),
         InlineKeyboardButton("قنوات الشرط", callback_data="condition_channels")],
        [InlineKeyboardButton("لوحة التحكم", callback_data="control_panel")],
    ])


async def edit_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_media(
            media=InputMediaPhoto(media=MAIN_PHOTO_URL, caption=wrap_text("القائمة الرئيسية لبوت روليت سياف")),
            reply_markup=build_main_menu_keyboard(),
        )
    except TelegramError:
        pass


async def edit_caption_and_buttons(query, caption: str, reply_markup: InlineKeyboardMarkup):
    try:
        await query.edit_message_caption(caption=caption, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramError:
        pass


async def verify_bot_admin(context, channel_username: str) -> bool:
    try:
        chat = await context.bot.get_chat(f"@{channel_username.replace('@', '')}")
        bot_member = await chat.get_member(context.bot.id)
        return bot_member.status == "administrator"
    except TelegramError:
        return False


# ---------- تحديث رسالة الروليت ----------
async def update_roulette_message(roulette_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        roulette = await db.get_roulette(roulette_id)
        if not roulette or roulette["status"] == "ended":
            return
        count = await db.get_participant_count(roulette_id)
        count_str = str(count).zfill(2)
        keyboard = [
            [InlineKeyboardButton("انضمام", callback_data=f"join_{roulette_id}"),
             InlineKeyboardButton(count_str, callback_data="none"),
             InlineKeyboardButton("روليت سياف", url=TARGET_BOT_URL)],
            [InlineKeyboardButton("بدء الروليت", callback_data=f"draw_{roulette_id}")],
        ]
        await context.bot.edit_message_reply_markup(
            chat_id=roulette["chat_id"],
            message_id=roulette["message_id"],
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"خطأ في تحديث رسالة الروليت {roulette_id}: {e}")


# ---------- تنفيذ السحب (شريط متحرك 4 ثوان) ----------
async def perform_draw(roulette_id: int, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        roulette = await db.get_roulette(roulette_id)
        if not roulette:
            return
        participants = await db.get_participants(roulette_id)
        if not participants:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=wrap_text("لا يوجد مشاركون لإجراء السحب."), parse_mode="HTML",
            )
            await db.update_roulette_winner(roulette_id, 0)
            await db.delete_roulette_data(roulette_id)
            return

        steps = 8
        for i in range(steps + 1):
            percent = i * 100 // steps
            filled = "▓" * (i * 10 // steps)
            empty = "░" * (10 - len(filled))
            bar = filled + empty
            text = f"جاري اختيار الفائز...\n\n[{bar}] {percent}%"
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=wrap_text(text), parse_mode="HTML",
                )
            except TelegramError:
                pass
            await asyncio.sleep(0.5)

        winner_id = random.choice(participants)
        try:
            winner_chat = await context.bot.get_chat(winner_id)
            winner_name = f"@{winner_chat.username}" if winner_chat.username else winner_chat.first_name
        except:
            winner_name = str(winner_id)

        final_text = f"الفائز: {winner_name}\nالمسابقة منتهية."

        db_error = None
        try:
            await db.update_roulette_winner(roulette_id, winner_id)
            await db.delete_roulette_data(roulette_id)
        except Exception as e:
            logger.error(f"فشل تحديث قاعدة البيانات بعد السحب {roulette_id}: {e}")
            db_error = str(e)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=wrap_text(final_text), parse_mode="HTML",
            )
        except TelegramError:
            try:
                await context.bot.send_message(chat_id=chat_id, text=wrap_text(final_text), parse_mode="HTML")
            except:
                pass

        owner_id = roulette["owner_id"]
        await send_notification(context, owner_id, f"فاز {winner_name} في روليتك في {roulette['channel_username']}.")

        if db_error:
            await send_notification(context, owner_id, f"حدث خطأ أثناء حفظ نتيجة السحب: {db_error}")
    except Exception as e:
        logger.error(f"استثناء غير متوقع أثناء السحب {roulette_id}: {e}")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=wrap_text("حدث خطأ غير متوقع أثناء السحب. يرجى التواصل مع المشرف."),
                parse_mode="HTML",
            )
        except:
            pass


# ---------- أمر /admin ----------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(wrap_text("غير مصرح لك."), parse_mode="HTML")
        return
    keyboard = [
        [InlineKeyboardButton("إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("إدارة القنوات", callback_data="admin_channels")],
        [InlineKeyboardButton("قنوات الاشتراك الإجباري", callback_data="admin_conditions")],
        [InlineKeyboardButton("إعدادات الإشعارات العامة", callback_data="admin_notifications")],
        [InlineKeyboardButton("تشغيل البوت" if await db.get_bot_status() == "maintenance" else "إيقاف تشغيل البوت",
                              callback_data="toggle_bot_status")],
        [InlineKeyboardButton("رجوع", callback_data="main_menu")],
    ]
    await update.message.reply_text(
        text=wrap_text("لوحة تحكم الأدمن"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ---------- معالج الأزرار ----------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    user_data = context.user_data

    if user_id != ADMIN_ID:
        status = await db.get_bot_status()
        if status == "maintenance" and data != "none":
            await query.answer("البوت قيد الصيانة حالياً، يرجى المحاولة لاحقاً", show_alert=True)
            return
        if await db.is_user_banned(user_id):
            await query.answer("لقد تم حظرك من استخدام البوت.", show_alert=True)
            return

    if data == "none":
        await query.answer()
        return

    # ---------- لوحة الأدمن ----------
    if data == "admin_users":
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("حظر مستخدم", callback_data="ban_user")],
            [InlineKeyboardButton("إلغاء حظر مستخدم", callback_data="unban_user")],
            [InlineKeyboardButton("رجوع", callback_data="admin_back")],
        ]
        await edit_caption_and_buttons(query, wrap_text("إدارة المستخدمين"), InlineKeyboardMarkup(keyboard))

    elif data == "ban_user":
        user_data["admin_state"] = "awaiting_ban_user"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف المستخدم الذي تريد حظره:"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_users")]]))
    elif data == "unban_user":
        user_data["admin_state"] = "awaiting_unban_user"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف المستخدم الذي تريد إلغاء حظره:"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_users")]]))

    elif data == "admin_channels":
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("حظر قناة", callback_data="ban_channel")],
            [InlineKeyboardButton("إلغاء حظر قناة", callback_data="unban_channel")],
            [InlineKeyboardButton("رجوع", callback_data="admin_back")],
        ]
        await edit_caption_and_buttons(query, wrap_text("إدارة القنوات"), InlineKeyboardMarkup(keyboard))

    elif data == "ban_channel":
        user_data["admin_state"] = "awaiting_ban_channel"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف القناة المراد حظرها: @username"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_channels")]]))
    elif data == "unban_channel":
        user_data["admin_state"] = "awaiting_unban_channel"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف القناة المراد إلغاء حظرها: @username"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_channels")]]))

    elif data == "admin_conditions":
        await query.answer()
        channels = await db.get_condition_channels()
        keyboard = []
        for ch in channels:
            row = await db._fetchrow("SELECT id, is_default FROM condition_channels WHERE channel_username = $1", ch)
            if row and not row["is_default"]:
                keyboard.append([InlineKeyboardButton(ch, callback_data="none"),
                                 InlineKeyboardButton("🗑", callback_data=f"del_cond_admin_{row['id']}")])
            else:
                keyboard.append([InlineKeyboardButton(ch + " (افتراضية)", callback_data="none")])
        keyboard.append([InlineKeyboardButton("➕", callback_data="add_cond_admin")])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data="admin_back")])
        await edit_caption_and_buttons(query, wrap_text("قنوات الاشتراك الإجباري:"), InlineKeyboardMarkup(keyboard))

    elif data == "add_cond_admin":
        user_data["admin_state"] = "awaiting_cond_admin"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف القناة الجديدة: @username"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="admin_conditions")]]))

    elif data.startswith("del_cond_admin_"):
        ch_id = int(data.split("_")[3])
        success = await db.remove_condition_channel(ch_id)
        await query.answer("تم الحذف" if success else "لا يمكن حذف القناة الافتراضية", show_alert=True)
        fake = update
        fake.callback_query.data = "admin_conditions"
        await button_handler(fake, context)

    elif data == "admin_notifications":
        await query.answer()
        current = await db.get_notifications_enabled()
        toggle_text = "تعطيل الإشعارات العامة" if current else "تفعيل الإشعارات العامة"
        keyboard = [
            [InlineKeyboardButton(toggle_text, callback_data="toggle_notifications")],
            [InlineKeyboardButton("رجوع", callback_data="admin_back")],
        ]
        await edit_caption_and_buttons(query, wrap_text(f"حالة الإشعارات العامة: {'مفعلة' if current else 'معطلة'}"),
                                       InlineKeyboardMarkup(keyboard))

    elif data == "toggle_notifications":
        current = await db.get_notifications_enabled()
        await db.set_notifications_enabled(not current)
        await query.answer(f"تم {'تعطيل' if current else 'تفعيل'} الإشعارات العامة", show_alert=True)
        fake = update
        fake.callback_query.data = "admin_notifications"
        await button_handler(fake, context)

    elif data == "toggle_bot_status":
        current = await db.get_bot_status()
        new_status = "maintenance" if current == "running" else "running"
        await db.set_bot_status(new_status)
        await query.answer(f"تم تغيير حالة البوت إلى: {new_status}", show_alert=True)
        if new_status == "maintenance":
            await broadcast_notification(context, "البوت قيد الصيانة حالياً، يرجى المحاولة لاحقاً.")
        else:
            await broadcast_notification(context, "تم تشغيل البوت مرة أخرى، يمكنك متابعة الاستخدام.")
        await admin_command(update, context)

    elif data == "admin_back":
        await admin_command(update, context)

    # ---------- القائمة الرئيسية ----------
    elif data == "main_menu":
        await edit_to_main_menu(update, context)

    # ---------- قنواتي ----------
    elif data == "my_channels":
        await query.answer()
        channels = await db.get_user_channels(user_id)
        keyboard = []
        for ch in channels:
            row = await db._fetchrow("SELECT id FROM user_channels WHERE channel_username = $1", ch)
            if row:
                keyboard.append([InlineKeyboardButton(ch, callback_data=f"channel_info_{row['id']}"),
                                 InlineKeyboardButton("حذف", callback_data=f"del_channel_{row['id']}")])
        keyboard.append([InlineKeyboardButton("اضافة قناة", callback_data="add_channel")])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data="main_menu")])
        await edit_caption_and_buttons(query, wrap_text("قنواتك المضافة:"), InlineKeyboardMarkup(keyboard))

    elif data == "add_channel":
        user_data["state"] = "awaiting_channel"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف القناة: @username"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="my_channels")]]))

    elif data.startswith("del_channel_"):
        ch_id = int(data.split("_")[2])
        await db.remove_user_channel(ch_id)
        await query.answer("تمت الإزالة", show_alert=True)
        fake = update
        fake.callback_query.data = "my_channels"
        await button_handler(fake, context)

    # ---------- قنوات الشرط (للمستخدم العادي) ----------
    elif data == "condition_channels":
        await query.answer()
        channels = await db.get_condition_channels()
        txt = "قنوات الاشتراك الإجباري:\n" + "\n".join(channels)
        await edit_caption_and_buttons(query, wrap_text(txt), InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="main_menu")]]))

    # ---------- بدء روليت ----------
    elif data == "start_roulette":
        await query.answer()
        channels = await db.get_user_channels(user_id)
        if not channels:
            await query.answer("ليس لديك قنوات", show_alert=True)
            return
        keyboard = []
        for ch in channels:
            if await db.is_channel_banned(ch):
                continue
            keyboard.append([InlineKeyboardButton(ch, callback_data=f"begin_roulette_{ch}")])
        if not keyboard:
            await query.answer("جميع قنواتك محظورة أو لا توجد قنوات.", show_alert=True)
            return
        keyboard.append([InlineKeyboardButton("نشر في كل القنوات", callback_data="post_all")])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data="main_menu")])
        await edit_caption_and_buttons(query, wrap_text("اختر قناة بدء الروليت:"), InlineKeyboardMarkup(keyboard))

    elif data.startswith("begin_roulette_"):
        ch = data.split("_", 2)[2]
        if await db.is_channel_banned(ch):
            await query.answer("هذه القناة محظورة من قبل الإدارة.", show_alert=True)
            return
        if await db.get_active_roulette_in_channel(ch):
            await query.answer("يوجد روليت نشط بالفعل في هذه القناة", show_alert=True)
            return
        user_data["roulette_channel"] = ch
        user_data["state"] = "awaiting_description"
        await edit_caption_and_buttons(query, wrap_text(f"أرسل وصف المسابقة التي ستنشر في {ch}"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="start_roulette")]]))

    elif data == "post_all":
        user_data["roulette_channel"] = "all"
        user_data["state"] = "awaiting_description"
        await edit_caption_and_buttons(query, wrap_text("أرسل وصف المسابقة الذي سينشر في جميع قنواتك"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data="start_roulette")]]))

    # ---------- انضمام / سحب ----------
    elif data.startswith("join_"):
        rid = int(data.split("_")[1])
        ok, missing = await check_subscriptions(user_id, context)
        if not ok:
            await query.answer("يجب الاشتراك في:\n" + "\n".join(missing), show_alert=True)
            return
        roulette = await db.get_roulette(rid)
        if not roulette:
            await query.answer("المسابقة غير موجودة", show_alert=True)
            return
        added = await db.add_participant(rid, user_id)
        if added:
            await query.answer("تم الانضمام", show_alert=True)
            try:
                owner_id = roulette["owner_id"]
                new_user = query.from_user
                participant_name = f"@{new_user.username}" if new_user.username else new_user.first_name
                await send_notification(context, owner_id, f"انضم {participant_name} إلى روليتك في {roulette['channel_username']}.")
            except:
                pass
        else:
            await query.answer("أنت مسجل بالفعل", show_alert=True)
        await update_roulette_message(rid, context)

    elif data.startswith("draw_"):
        rid = int(data.split("_")[1])
        r = await db.get_roulette(rid)
        if not r or r["owner_id"] != user_id:
            await query.answer("هذا الزر لصاحب المسابقة فقط", show_alert=True)
            return
        await query.answer()
        await perform_draw(rid, r["chat_id"], r["message_id"], context)

    # ---------- لوحة التحكم (للمستخدم) ----------
    elif data == "control_panel":
        await query.answer()
        if update.effective_chat.type != "private":
            await query.answer("لوحة التحكم متاحة في الدردشة الخاصة فقط", show_alert=True)
            return
        notif_status = await db.get_user_notifications_enabled(user_id)
        notif_btn_text = "الاشعارات 🟢" if notif_status else "الاشعارات 🔴"
        keyboard = [
            [InlineKeyboardButton("مسابقاتي", callback_data="my_contests")],
            [InlineKeyboardButton("مسابقات نشطة حاليا", callback_data="active_contests")],
            [InlineKeyboardButton(notif_btn_text, callback_data="toggle_user_notifications_direct")],
            [InlineKeyboardButton("رجوع", callback_data="main_menu")],
        ]
        await edit_caption_and_buttons(query, wrap_text("لوحة التحكم"), InlineKeyboardMarkup(keyboard))

    elif data == "toggle_user_notifications_direct":
        current = await db.get_user_notifications_enabled(user_id)
        await db.set_user_notifications(user_id, not current)
        await query.answer(f"تم {'تعطيل' if current else 'تفعيل'} الإشعارات", show_alert=True)
        new_status = await db.get_user_notifications_enabled(user_id)
        notif_btn_text = "الاشعارات 🟢" if new_status else "الاشعارات 🔴"
        keyboard = [
            [InlineKeyboardButton("مسابقاتي", callback_data="my_contests")],
            [InlineKeyboardButton("مسابقات نشطة حاليا", callback_data="active_contests")],
            [InlineKeyboardButton(notif_btn_text, callback_data="toggle_user_notifications_direct")],
            [InlineKeyboardButton("رجوع", callback_data="main_menu")],
        ]
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        except TelegramError:
            pass

    elif data == "my_contests":
        await query.answer()
        grouped = await db.get_grouped_user_contests(user_id)
        if not grouped:
            await edit_caption_and_buttons(query, wrap_text("لا توجد مسابقات نشطة لديك."),
                                           InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="control_panel")]]))
            return
        keyboard = []
        for desc, items in grouped.items():
            cnt = len(items)
            if cnt == 1:
                btn_text = items[0]["channel_username"]
            else:
                btn_text = f"روليت في {cnt} قنوات"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"manage_group_{desc}"),
                             InlineKeyboardButton("حذف", callback_data=f"delete_group_{desc}")])
        keyboard.append([InlineKeyboardButton("مسابقات نشطة حاليا", callback_data="active_contests")])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data="control_panel")])
        await edit_caption_and_buttons(query, wrap_text("مسابقاتك النشطة:"), InlineKeyboardMarkup(keyboard))

    elif data.startswith("delete_group_"):
        desc = data[len("delete_group_"):]
        await db.delete_roulettes_by_owner_and_description(user_id, desc)
        await query.answer("تم حذف المسابقة", show_alert=True)
        fake = update
        fake.callback_query.data = "my_contests"
        await button_handler(fake, context)

    elif data.startswith("manage_group_"):
        desc = data[len("manage_group_"):]
        roulettes = await db.get_roulettes_by_owner_and_description(user_id, desc)
        if not roulettes:
            await query.answer("لم تعد موجودة", show_alert=True)
            return
        all_parts = set()
        for r in roulettes:
            parts = await db.get_participants(r["id"])
            all_parts.update(parts)
        parts_list = sorted(all_parts)
        text = "المشاركون:\n" + "\n".join(str(p) for p in parts_list) if parts_list else "لا يوجد مشاركون بعد."
        keyboard = [
            [InlineKeyboardButton("اضف مشارك", callback_data=f"addpart_group_{desc}"),
             InlineKeyboardButton("ازالة مشارك", callback_data=f"rempart_group_{desc}")],
            [InlineKeyboardButton("رجوع", callback_data="my_contests")],
        ]
        await edit_caption_and_buttons(query, wrap_text(text), InlineKeyboardMarkup(keyboard))

    elif data.startswith("addpart_group_"):
        desc = data[len("addpart_group_"):]
        user_data["state"] = f"awaiting_addpart_group_{desc}"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف المستخدم لإضافته:"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data=f"manage_group_{desc}")]]))

    elif data.startswith("rempart_group_"):
        desc = data[len("rempart_group_"):]
        user_data["state"] = f"awaiting_rempart_group_{desc}"
        await edit_caption_and_buttons(query, wrap_text("أرسل معرف المستخدم لإزالته:"),
                                       InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء", callback_data=f"manage_group_{desc}")]]))

    elif data == "active_contests":
        await query.answer()
        channels = await db.get_all_active_channels()
        if not channels:
            await edit_caption_and_buttons(query, wrap_text("لا توجد مسابقات نشطة."),
                                           InlineKeyboardMarkup([[InlineKeyboardButton("رجوع", callback_data="control_panel")]]))
            return
        keyboard = []
        for ch, cid, mid in channels:
            cid_str = str(cid)[4:] if str(cid).startswith("-100") else str(cid)
            link = f"https://t.me/c/{cid_str}/{mid}"
            keyboard.append([InlineKeyboardButton(ch, callback_data="none"),
                             InlineKeyboardButton("عرض", url=link)])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data="control_panel")])
        await edit_caption_and_buttons(query, wrap_text("قنوات تحتوي على روليت نشط:"), InlineKeyboardMarkup(keyboard))

    else:
        await query.answer("خيار غير معروف", show_alert=True)


# ---------- معالج النصوص ----------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    if user_id != ADMIN_ID:
        if await db.get_bot_status() == "maintenance":
            await update.message.reply_text(wrap_text("البوت قيد الصيانة حالياً، يرجى المحاولة لاحقاً"), parse_mode="HTML")
            return
        if await db.is_user_banned(user_id):
            await update.message.reply_text(wrap_text("لقد تم حظرك من استخدام البوت."), parse_mode="HTML")
            return

    admin_state = user_data.get("admin_state")
    if admin_state:
        if admin_state == "awaiting_ban_user":
            try:
                target = int(text)
                await db.ban_user(target)
                await update.message.reply_text(wrap_text("تم حظر المستخدم."), parse_mode="HTML")
                await send_notification(context, target, "تم حظرك من استخدام بوت روليت سياف.")
            except ValueError:
                await update.message.reply_text(wrap_text("معرف غير صحيح."), parse_mode="HTML")
            user_data.pop("admin_state", None)
            return
        elif admin_state == "awaiting_unban_user":
            try:
                target = int(text)
                await db.unban_user(target)
                await update.message.reply_text(wrap_text("تم إلغاء حظر المستخدم."), parse_mode="HTML")
                await send_notification(context, target, "تم إلغاء حظرك من بوت روليت سياف، يمكنك استخدامه الآن.")
            except ValueError:
                await update.message.reply_text(wrap_text("معرف غير صحيح."), parse_mode="HTML")
            user_data.pop("admin_state", None)
            return
        elif admin_state == "awaiting_ban_channel":
            if not text.startswith("@"):
                await update.message.reply_text(wrap_text("معرف قناة غير صحيح."), parse_mode="HTML")
                return
            await db.ban_channel(text)
            await update.message.reply_text(wrap_text("تم حظر القناة."), parse_mode="HTML")
            user_data.pop("admin_state", None)
            return
        elif admin_state == "awaiting_unban_channel":
            if not text.startswith("@"):
                await update.message.reply_text(wrap_text("معرف قناة غير صحيح."), parse_mode="HTML")
                return
            await db.unban_channel(text)
            await update.message.reply_text(wrap_text("تم إلغاء حظر القناة."), parse_mode="HTML")
            user_data.pop("admin_state", None)
            return
        elif admin_state == "awaiting_cond_admin":
            if not text.startswith("@"):
                await update.message.reply_text(wrap_text("معرف غير صحيح."), parse_mode="HTML")
                return
            await db.add_condition_channel(text)
            await update.message.reply_text(wrap_text("تمت إضافة القناة."), parse_mode="HTML")
            user_data.pop("admin_state", None)
            return

    state = user_data.get("state")
    if not state:
        return

    if state == "awaiting_channel":
        if not (text.startswith("@") and len(text) > 1):
            await update.message.reply_text(wrap_text("يرجى إرسال معرف صالح يبدأ بـ @"), parse_mode="HTML")
            return
        if not await verify_bot_admin(context, text):
            await update.message.reply_text(wrap_text("البوت ليس مشرفًا في القناة."), parse_mode="HTML")
            return
        if await db.is_channel_banned(text):
            await update.message.reply_text(wrap_text("هذه القناة محظورة من الإدارة."), parse_mode="HTML")
            return
        added = await db.add_user_channel(user_id, text)
        msg = "تمت الإضافة." if added else "القناة مضافة مسبقاً."
        await update.message.reply_text(wrap_text(msg), parse_mode="HTML")
        if added:
            await send_notification(context, user_id, f"تمت إضافة القناة {text} إلى قنواتك بنجاح.")
        user_data.pop("state", None)

    elif state == "awaiting_description":
        ch_choice = user_data.get("roulette_channel")
        if not ch_choice:
            await update.message.reply_text(wrap_text("حدث خطأ."), parse_mode="HTML")
            user_data.pop("state", None)
            return
        description = text
        user_data.pop("state", None)
        user_data.pop("roulette_channel", None)

        cond_channels = await db.get_condition_channels()
        if ch_choice == "all":
            channels = await db.get_user_channels(user_id)
            for ch in channels:
                if await db.is_channel_banned(ch):
                    continue
                await create_roulette_post(user_id, ch, description, cond_channels, context, update)
        else:
            if await db.is_channel_banned(ch_choice):
                await update.message.reply_text(wrap_text("القناة محظورة."), parse_mode="HTML")
                return
            await create_roulette_post(user_id, ch_choice, description, cond_channels, context, update)

    elif state.startswith("awaiting_addpart_group_"):
        desc = state[len("awaiting_addpart_group_"):]
        user_data.pop("state", None)
        try:
            target = int(text)
        except ValueError:
            await update.message.reply_text(wrap_text("معرف غير صحيح."), parse_mode="HTML")
            return
        roulettes = await db.get_roulettes_by_owner_and_description(user_id, desc)
        for r in roulettes:
            await db.add_participant(r["id"], target)
        await update.message.reply_text(wrap_text("تمت الإضافة."), parse_mode="HTML")

    elif state.startswith("awaiting_rempart_group_"):
        desc = state[len("awaiting_rempart_group_"):]
        user_data.pop("state", None)
        try:
            target = int(text)
        except ValueError:
            await update.message.reply_text(wrap_text("معرف غير صحيح."), parse_mode="HTML")
            return
        roulettes = await db.get_roulettes_by_owner_and_description(user_id, desc)
        for r in roulettes:
            await db.remove_participant(r["id"], target)
        await update.message.reply_text(wrap_text("تمت الإزالة."), parse_mode="HTML")


async def create_roulette_post(owner_id: int, channel: str, description: str, condition_channels: List[str], context, update):
    if await db.get_active_roulette_in_channel(channel):
        await update.message.reply_text(wrap_text(f"يوجد روليت نشط مسبقاً في {channel}"), parse_mode="HTML")
        return

    cond_lines = "\n".join([f"<blockquote expandable>{ch}</blockquote>" for ch in condition_channels]) if condition_channels else ""
    full_text = f"{description}\n\n{cond_lines}" if cond_lines else description

    try:
        msg = await context.bot.send_message(
            chat_id=f"@{channel.replace('@', '')}",
            text=wrap_text(full_text),
            parse_mode="HTML",
        )
    except TelegramError as e:
        await update.message.reply_text(wrap_text(f"فشل النشر في {channel}: {e}"), parse_mode="HTML")
        return

    roulette_id = await db.create_roulette_get_id(owner_id, channel, msg.chat_id, msg.message_id, description)
    await update_roulette_message(roulette_id, context)
    await update.message.reply_text(wrap_text(f"تم نشر الروليت في {channel}"), parse_mode="HTML")
    await send_notification(context, owner_id, f"تم نشر الروليت في {channel} بنجاح.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_photo(
        photo=MAIN_PHOTO_URL,
        caption=wrap_text("القائمة الرئيسية لبوت روليت سياف"),
        reply_markup=build_main_menu_keyboard(),
        parse_mode="HTML",
    )


# ---------- Flask للويب هوك ----------
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({
        "bot": "Roulette Bot",
        "status": "running",
        "version": "2.0.0",
        "webhook_url": WEBHOOK_URL
    })

@flask_app.route('/webhook', methods=['POST'])
async def webhook():
    """استقبال التحديثات من تيليجرام عبر ويب هوك"""
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"خطأ في معالجة التحديث: {e}")
        return "Error", 500

@flask_app.route('/ping')
def ping():
    return "pong", 200


# ---------- التشغيل الرئيسي ----------
async def main():
    """تهيئة وتشغيل البوت"""
    try:
        # الاتصال بقاعدة البيانات
        await db.connect()
        
        # تهيئة البوت
        await application.initialize()
        await application.start()
        
        # إضافة المعالجات
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
        # تعيين الويب هوك
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"تم تعيين الويب هوك إلى: {WEBHOOK_URL}/webhook")
        
        logger.info("البوت جاهز للعمل...")
        
    except Exception as e:
        logger.error(f"فشل في تشغيل البوت: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # تشغيل البوت في الخلفية
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    
    # تشغيل Flask كخادم رئيسي
    flask_app.run(host='0.0.0.0', port=PORT, debug=False)
