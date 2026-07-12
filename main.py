"""
وكيل الذهب الذكي - بوت تليجرام
النسخة النهائية المتكاملة مع تصنيفات العيارات (18, 21, 24 قيراط)
ودعم العملات المتعددة (USD, SAR, YER)
"""

import os
import logging
import sys
import re
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

import requests
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from newsapi import NewsApiClient
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telethon import TelegramClient

# ============================================================
# 1. إعداد التسجيل والسجلات
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 2. قراءة متغيرات البيئة مع تحقق متقدم
# ============================================================

def get_env_var(var_name: str, required: bool = False, default: Optional[str] = None) -> Optional[str]:
    """
    دالة مساعدة للحصول على متغيرات البيئة مع تحقق إضافي
    وقراءة من ملف .env للتشغيل المحلي
    """
    value = os.environ.get(var_name)

    # محاولة قراءة من ملف .env للتشغيل المحلي
    if not value:
        try:
            with open('.env', 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, val = line.split('=', 1)
                            if key.strip() == var_name:
                                value = val.strip().strip('"\'')
                                logger.info(f"✅ تم قراءة {var_name} من ملف .env")
                                break
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ خطأ في قراءة ملف .env: {e}")

    if required and not value:
        error_msg = (
            f"\n❌ {var_name} غير موجود!\n"
            f"📋 لإضافة {var_name}:\n"
            f"   - في Render: Settings → Environment Variables\n"
            f"   - محلياً: أضف {var_name}=value في ملف .env\n"
        )
        logger.error(error_msg)
        if os.environ.get("RENDER"):
            raise ValueError(f"{var_name} is required for deployment")
        else:
            print(error_msg)
            return default

    return value or default

# قراءة المتغيرات الأساسية
logger.info("🚀 جاري تحميل متغيرات البيئة...")

BOT_TOKEN = get_env_var("BOT_TOKEN", required=True)
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN مطلوب لتشغيل البوت")
    sys.exit(1)

logger.info("✅ تم تحميل BOT_TOKEN بنجاح")

# المتغيرات الاختيارية
NEWS_API_KEY = get_env_var("NEWS_API_KEY", required=False, default="")
TELEGRAM_API_ID = get_env_var("TELEGRAM_API_ID", required=False, default="")
TELEGRAM_API_HASH = get_env_var("TELEGRAM_API_HASH", required=False, default="")
CHANNELS = get_env_var("CHANNELS", required=False, default="")
EXCHANGE_RATE_SAR = float(get_env_var("EXCHANGE_RATE_SAR", required=False, default="3.75"))
EXCHANGE_RATE_YER = float(get_env_var("EXCHANGE_RATE_YER", required=False, default="530"))
PORT = int(get_env_var("PORT", required=False, default="8443"))

logger.info(f"✅ سعر الصرف: 1 دولار = {EXCHANGE_RATE_SAR} ريال سعودي")
logger.info(f"✅ سعر الصرف: 1 ريال سعودي = {EXCHANGE_RATE_YER:.0f} ريال يمني")

# ============================================================
# 3. الثوابت والإعدادات
# ============================================================

# تصنيفات العيارات
GOLD_CARATS = {
    24: {"name": "ذهب 24 قيراط", "purity": 0.9999, "emoji": "🪙"},
    21: {"name": "ذهب 21 قيراط", "purity": 0.875, "emoji": "🥇"},
    18: {"name": "ذهب 18 قيراط", "purity": 0.750, "emoji": "💛"}
}

# المدن اليمنية للمراجحة
LOCAL_CITIES = ["صنعاء", "إب", "عدن"]

# وزن الأونصة بالجرام
OUNCE_GRAMS = 31.1035

# ============================================================
# 4. تهيئة الأدوات والخدمات
# ============================================================

# محلل المشاعر
analyzer = SentimentIntensityAnalyzer()

# عميل أخبار
newsapi = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None
if newsapi:
    logger.info("✅ تم تهيئة NewsAPI")
else:
    logger.warning("⚠️ NewsAPI غير مهيأ (NEWS_API_KEY غير موجود)")

# عميل تيليثون للقنوات المحلية
telethon_client = None
if TELEGRAM_API_ID and TELEGRAM_API_HASH:
    try:
        telethon_client = TelegramClient('agent_session', int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        logger.info("✅ تم تهيئة Telethon")
    except Exception as e:
        logger.warning(f"⚠️ خطأ في تهيئة Telethon: {e}")

# تخزين مؤقت للأسعار المحلية
local_prices = {}

# ============================================================
# 5. دوال جلب البيانات وتحليلها
# ============================================================

def fetch_gold_price() -> Optional[float]:
    """
    جلب سعر الذهب العالمي من عدة مصادر
    """
    sources = [
        {
            "url": "https://api.metals.live/v1/spot/gold",
            "parser": lambda data: float(data.get("price", 0))
        },
        {
            "url": "https://www.gold-api.com/price/XAU",
            "parser": lambda data: float(data.get("price", 0))
        },
        {
            "url": "https://api.goldprice.org/v1/spot/XAU/USD",
            "parser": lambda data: float(data.get("price", 0))
        }
    ]

    for source in sources:
        try:
            logger.info(f"📡 محاولة جلب السعر من: {source['url']}")
            resp = requests.get(source['url'], timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                price = source['parser'](data)
                if price and price > 0:
                    logger.info(f"✅ تم جلب السعر: {price:.2f} دولار")
                    return round(price, 2)
        except Exception as e:
            logger.warning(f"⚠️ فشل المصدر {source['url']}: {e}")
            continue

    logger.error("❌ فشل جلب سعر الذهب من جميع المصادر")
    return None

def calculate_gold_prices(oz_price_usd: float) -> Optional[Dict[int, Dict]]:
    """
    حساب أسعار الذهب لجميع العيارات
    """
    if not oz_price_usd or oz_price_usd <= 0:
        return None

    results = {}

    for carat, carat_info in GOLD_CARATS.items():
        # سعر الجرام بالدولار
        gram_price_usd = (oz_price_usd / OUNCE_GRAMS) * carat_info["purity"]

        # التحويل للعملات
        gram_price_sar = gram_price_usd * EXCHANGE_RATE_SAR
        gram_price_yer = gram_price_sar * EXCHANGE_RATE_YER

        # أسعار الأجزاء المختلفة
        fractions = {
            "ربع جرام": 0.25,
            "نصف جرام": 0.5,
            "1 جرام": 1.0,
            "5 جرام": 5.0,
            "10 جرام": 10.0,
            "أونصة": OUNCE_GRAMS
        }

        fraction_prices = {}
        for fraction_name, weight in fractions.items():
            fraction_prices[fraction_name] = {
                "usd": round(gram_price_usd * weight, 2),
                "sar": round(gram_price_sar * weight, 2),
                "yer": round(gram_price_yer * weight, 0)
            }

        results[carat] = {
            "name": carat_info["name"],
            "purity": carat_info["purity"],
            "emoji": carat_info["emoji"],
            "oz_price_usd": round(oz_price_usd, 2),
            "gram_price": {
                "usd": round(gram_price_usd, 2),
                "sar": round(gram_price_sar, 2),
                "yer": round(gram_price_yer, 0)
            },
            "fractions": fraction_prices
        }

    return results

def fetch_news_sentiment() -> Tuple[Optional[float], str, list]:
    """
    تحليل المشاعر الإخبارية
    """
    if not newsapi:
        return None, "⚠️ NewsAPI غير متاح", []

    try:
        queries = [
            'gold price',
            'Federal Reserve interest rates',
            'geopolitics gold',
            'inflation gold',
            'central bank gold'
        ]

        all_headlines = []
        all_scores = []

        for query in queries[:3]:  # حد للاستعلامات لتوفير الحصص
            articles = newsapi.get_everything(
                q=query,
                language='en',
                sort_by='relevancy',
                page_size=10
            )

            for article in articles.get('articles', []):
                title = article.get('title', '') or ''
                description = article.get('description', '') or ''
                text = f"{title} {description}".strip()

                if len(text) > 10:
                    score = analyzer.polarity_scores(text)['compound']
                    all_scores.append(score)
                    if len(all_headlines) < 5:
                        all_headlines.append(title[:100])

        if not all_scores:
            return None, "لا توجد أخبار كافية للتحليل", []

        avg_score = sum(all_scores) / len(all_scores)

        if avg_score > 0.2:
            sentiment = "🟢 إيجابي (تفاؤل)"
        elif avg_score < -0.2:
            sentiment = "🔴 سلبي (قلق)"
        else:
            sentiment = "🟡 محايد"

        return avg_score, sentiment, all_headlines[:5]

    except Exception as e:
        logger.error(f"خطأ تحليل الأخبار: {e}")
        return None, f"فشل تحليل المشاعر: {str(e)}", []

def fetch_cot_report() -> Tuple[Optional[float], Optional[float], str]:
    """
    تحليل تقرير COT
    """
    try:
        # محاولة جلب ملف Excel
        url = "https://www.cftc.gov/dea/futures/deacmxsf.xls"
        logger.info("📡 جاري تحميل تقرير COT...")

        df = pd.read_excel(url, sheet_name="Gold (Comex)", skiprows=2)

        # البحث عن الأعمدة المناسبة
        long_cols = [c for c in df.columns if 'LONG' in str(c).upper() and 'NON-COMM' in str(c).upper()]
        short_cols = [c for c in df.columns if 'SHORT' in str(c).upper() and 'NON-COMM' in str(c).upper()]

        if not long_cols or not short_cols:
            return None, None, "لم يتم العثور على بيانات المراكز غير التجارية"

        long_col = long_cols[0]
        short_col = short_cols[0]

        # استخدام آخر صفين للتحليل
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row

        net = float(last_row[long_col]) - float(last_row[short_col])
        prev_net = float(prev_row[long_col]) - float(prev_row[short_col])
        change = net - prev_net

        # تحليل التغير
        if change > 5000:
            trend = "📈 زيادة كبيرة في الشراء (صعودي بقوة)"
        elif change > 0:
            trend = "📈 زيادة طفيفة في الشراء (صعودي)"
        elif change < -5000:
            trend = "📉 زيادة كبيرة في البيع (هبوطي بقوة)"
        elif change < 0:
            trend = "📉 زيادة طفيفة في البيع (هبوطي)"
        else:
            trend = "➖ ثبات المراكز"

        logger.info(f"✅ تم تحليل COT: صافي العقود {net:,.0f}, تغير {change:+,.0f}")
        return net, change, trend

    except Exception as e:
        logger.error(f"خطأ COT: {e}")
        return None, None, f"فشل تحليل تقرير COT: {str(e)}"

async def scrape_local_prices():
    """
    جلب أسعار الذهب المحلية من قنوات تيليجرام
    """
    if not telethon_client or not CHANNELS:
        logger.warning("⚠️ Telethon أو CHANNELS غير مهيأ")
        return

    try:
        if not telethon_client.is_connected():
            await telethon_client.start()
            logger.info("✅ تم الاتصال بـ Telethon")

        channels = [ch.strip() for ch in CHANNELS.split(',') if ch.strip()]
        logger.info(f"📡 جاري قراءة {len(channels)} قناة")

        for channel in channels:
            try:
                async for message in telethon_client.iter_messages(channel, limit=5):
                    if message.text:
                        text = message.text
                        for city in LOCAL_CITIES:
                            if city in text and ('ريال سعودي' in text or 'SAR' in text.upper()):
                                numbers = re.findall(r'\d+\.?\d*', text)
                                if numbers:
                                    price = float(numbers[-1])
                                    if 50 < price < 500:  # التحقق من منطقية السعر
                                        local_prices[city] = price
                                        logger.info(f"✅ تم تحديث {city}: {price} ريال/جرام")
                                        break
            except Exception as e:
                logger.warning(f"⚠️ خطأ في قراءة القناة {channel}: {e}")
                continue

    except Exception as e:
        logger.error(f"خطأ في قراءة القنوات: {e}")

def check_arbitrage() -> str:
    """
    تحليل فرص المراجحة بين المدن
    """
    if len(local_prices) < 2:
        return "⚠️ لا توجد بيانات كافية للمقارنة (يلزم سعر مدينتين على الأقل)."

    sorted_cities = sorted(local_prices.items(), key=lambda x: x[1], reverse=True)
    high_city, high_price = sorted_cities[0]
    low_city, low_price = sorted_cities[-1]
    diff = high_price - low_price

    # حساب نسبة الربح
    profit_pct = (diff / low_price) * 100 if low_price > 0 else 0

    msg = f"🏙️ **المدينة الأعلى**: {high_city} - {high_price:.2f} ريال/جرام\n"
    msg += f"🏙️ **المدينة الأقل**: {low_city} - {low_price:.2f} ريال/جرام\n"
    msg += f"📊 **الفارق**: {diff:.2f} ريال/جرام ({profit_pct:.1f}%)\n\n"

    if diff > 3 and profit_pct > 1:
        msg += f"🚀 **فرصة مراجحة مجدية**!\n"
        msg += f"💡 شراء من {low_city} بسعر {low_price:.2f} ريال\n"
        msg += f"💡 بيع في {high_city} بسعر {high_price:.2f} ريال\n"
        msg += f"💰 الربح المحتمل: {diff:.2f} ريال/جرام"
    else:
        msg += "📌 لا توجد فجوة سعرية مجدية حالياً"

    return msg

# ============================================================
# 6. دوال توليد التقارير
# ============================================================

def format_price_table(gold_prices: Dict[int, Dict]) -> str:
    """
    تنسيق جدول الأسعار بشكل احترافي
    """
    lines = []
    lines.append("╔" + "═" * 68 + "╗")
    lines.append("║" + " " * 22 + "أسعار الذهب حسب العيار" + " " * 23 + "║")
    lines.append("╠" + "═" * 68 + "╣")

    for carat, data in gold_prices.items():
        # اسم العيار والنقاوة
        name = f"{data['emoji']} {data['name']}"
        purity = f"نقاوة {data['purity']*100:.1f}%"
        line = f"║ {name} ({purity})"
        line += " " * (67 - len(line)) + "║"
        lines.append(line)

        # سعر الأونصة
        line = f"║   💰 الأونصة: {data['oz_price_usd']:,.2f} دولار"
        line += " " * (67 - len(line)) + "║"
        lines.append(line)

        # سعر الجرام بالعملات
        g = data['gram_price']
        line = f"║   📍 الجرام: ${g['usd']:.2f} | SAR {g['sar']:.2f} | YER {g['yer']:,.0f}"
        line += " " * (67 - len(line)) + "║"
        lines.append(line)

        lines.append("╠" + "─" * 68 + "╣")
        lines.append("║   📊 أسعار الأجزاء:" + " " * 48 + "║")

        for fraction, prices in data['fractions'].items():
            line = f"║     {fraction:8}: ${prices['usd']:8.2f} | SAR {prices['sar']:8.2f} | YER {prices['yer']:10,.0f}"
            line += " " * (67 - len(line)) + "║"
            lines.append(line)

        lines.append("╠" + "═" * 68 + "╣")

    lines.append("╚" + "═" * 68 + "╝")
    return "\n".join(lines)

async def generate_full_report() -> str:
    """
    توليد التقرير المفصل الكامل
    """
    lines = []
    lines.append("📊 **التقرير الذهبي الشامل**")
    lines.append("═" * 40)
    lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 1. السعر العالمي والعيارات
    lines.append("🪙 **أولاً: سعر الذهب العالمي**")
    gold_price = fetch_gold_price()

    if gold_price:
        lines.append(f"سعر الأونصة: {gold_price:.2f} دولار أمريكي")
        lines.append("")

        # حساب أسعار العيارات
        gold_prices = calculate_gold_prices(gold_price)
        if gold_prices:
            lines.append("📊 **أسعار الذهب حسب العيار**")
            lines.append("```")
            lines.append(format_price_table(gold_prices))
            lines.append("```")
            lines.append("")
            lines.append(f"💱 سعر الصرف: 1 دولار = {EXCHANGE_RATE_SAR} ريال سعودي")
            lines.append(f"💱 سعر الصرف: 1 ريال سعودي = {EXCHANGE_RATE_YER:.0f} ريال يمني")
    else:
        lines.append("❌ تعذر جلب السعر العالمي")
    lines.append("")

    # 2. تحليل المشاعر
    lines.append("🧠 **ثانياً: تحليل المشاعر الإخبارية**")
    sentiment_score, sentiment_label, headlines = fetch_news_sentiment()

    if sentiment_score is not None:
        lines.append(f"درجة المشاعر: {sentiment_score:.2f} - {sentiment_label}")
        if headlines:
            lines.append("أهم العناوين:")
            for h in headlines:
                lines.append(f"  • {h}")
    else:
        lines.append(sentiment_label)
    lines.append("")

    # 3. تقرير COT
    lines.append("📈 **ثالثاً: تقرير التزامات المتداولين (COT)**")
    cot_net, cot_change, cot_trend = fetch_cot_report()

    if cot_net is not None:
        lines.append(f"صافي عقود المضاربة: {cot_net:,.0f}")
        lines.append(f"التغير الأسبوعي: {cot_change:+,.0f}")
        lines.append(f"الاستنتاج: {cot_trend}")
    else:
        lines.append(cot_trend)
    lines.append("")

    # 4. الأسعار المحلية
    lines.append("🇾🇪 **رابعاً: أسعار الذهب المحلية (عيار 21 كسر)**")
    await scrape_local_prices()

    if local_prices:
        for city, price in sorted(local_prices.items()):
            lines.append(f"• {city}: {price:.2f} ريال سعودي/جرام")
    else:
        lines.append("⚠️ لا توجد بيانات محلية")
        if not CHANNELS:
            lines.append("💡 أضف CHANNELS في متغيرات البيئة")
        if not TELEGRAM_API_ID:
            lines.append("💡 أضف TELEGRAM_API_ID و TELEGRAM_API_HASH")
    lines.append("")

    # 5. فرص المراجحة
    lines.append("💱 **خامساً: فرص المراجحة الجغرافية**")
    lines.append(check_arbitrage())
    lines.append("")

    # 6. توقعات الافتتاح
    lines.append("🔮 **سادساً: توقعات الافتتاح ونصائح التداول**")

    # تحليل الإشارات
    is_bearish = False
    if sentiment_score is not None and sentiment_score < -0.2:
        is_bearish = True
    if cot_trend and ("بيع" in cot_trend or "هبوطي" in cot_trend):
        is_bearish = True

    is_bullish = False
    if sentiment_score is not None and sentiment_score > 0.2:
        is_bullish = True
    if cot_trend and ("شراء" in cot_trend or "صعودي" in cot_trend):
        is_bullish = True

    if is_bearish:
        lines.append("⚠️ **إشارات سلبية**: احتمال فجوة هابطة يوم الإثنين")
        lines.append("💡 نصيحة: تجنب الشراء المباشر مع الافتتاح")
        lines.append("💡 انتظر 30 دقيقة لتهدأ فروق الأسعار")
        lines.append("💡 راقب فرصة سد الفجوة (شراء بعد التصحيح)")
    elif is_bullish:
        lines.append("🌟 **إشارات إيجابية**: احتمال فجوة صاعدة")
        lines.append("💡 نصيحة: لا تلاحق السعر مع الافتتاح")
        lines.append("💡 استخدم أوامر معلقة (Limit) بعد انحسار التذبذب")
    else:
        lines.append("⚖️ **إشارات متضاربة أو محايدة**")
        lines.append("💡 يفضل مراقبة السوق أول 30 دقيقة")
        lines.append("💡 عدم الدخول في اتجاه الفجوة قبل التأكد")
    lines.append("")

    # 7. ملخص سريع
    if gold_price and gold_prices:
        lines.append("📊 **ملخص سريع**")
        for carat, data in gold_prices.items():
            g = data['gram_price']
            lines.append(f"• {data['name']}: ${g['usd']:.2f} | SAR {g['sar']:.2f}")

    lines.append("")
    lines.append("─" * 40)
    lines.append("🚀 تم إنشاء التقرير بواسطة وكيل الذهب الذكي")
    lines.append("📱 للإبلاغ عن مشكلة: @YourSupportBot")

    return "\n".join(lines)

# ============================================================
# 7. أوامر البوت
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب والأوامر"""
    welcome_msg = (
        "🪙 **مرحباً بك في وكيل الذهب الذكي**\n"
        "═" * 20 + "\n\n"
        "✨ **المميزات**:\n"
        "• أسعار 18, 21, 24 قيراط\n"
        "• أسعار الجرام وأجزائه\n"
        "• العملات: USD, SAR, YER\n"
        "• تحليل المشاعر الإخبارية\n"
        "• تقرير COT\n"
        "• فرص المراجحة المحلية\n\n"
        "📋 **الأوامر**:\n"
        "/report - التقرير الشامل\n"
        "/gold - سعر الذهب العالمي\n"
        "/prices - أسعار العيارات\n"
        "/arbitrage - فرص المراجحة\n"
        "/risk - إدارة المخاطر\n"
        "/help - هذه الرسالة"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المساعدة"""
    await start(update, context)

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توليد التقرير المفصل"""
    await update.message.reply_text("⏳ جارٍ إعداد التقرير المفصل...")
    try:
        report = await generate_full_report()

        # تقسيم الرسالة إذا كانت طويلة
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for part in parts:
                await update.message.reply_text(
                    part,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
        else:
            await update.message.reply_text(
                report,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
    except Exception as e:
        logger.error(f"خطأ في إنشاء التقرير: {e}")
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

async def gold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض سعر الذهب العالمي"""
    price = fetch_gold_price()
    if price:
        gold_prices = calculate_gold_prices(price)
        msg = f"🪙 **سعر الذهب العالمي**: {price:.2f} دولار/الأونصة\n\n"
        msg += "📊 **أسعار الجرام**:\n"
        for carat, data in gold_prices.items():
            g = data['gram_price']
            msg += f"• {data['name']}: ${g['usd']:.2f} | SAR {g['sar']:.2f} | YER {g['yer']:,.0f}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر العالمي")

async def prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جدول أسعار العيارات"""
    price = fetch_gold_price()
    if price:
        gold_prices = calculate_gold_prices(price)
        if gold_prices:
            msg = "📊 **جدول أسعار الذهب**\n"
            msg += "═" * 30 + "\n\n"
            for carat, data in gold_prices.items():
                g = data['gram_price']
                msg += f"**{data['emoji']} {data['name']}**\n"
                msg += f"• الأونصة: ${data['oz_price_usd']:.2f}\n"
                msg += f"• الجرام: ${g['usd']:.2f} | SAR {g['sar']:.2f}\n"
                msg += "• الأجزاء:\n"
                for fraction, prices in list(data['fractions'].items())[:4]:
                    msg += f"  - {fraction}: ${prices['usd']:.2f}\n"
                msg += "\n"
            await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر")

async def arbitrage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض فرص المراجحة"""
    await update.message.reply_text("⏳ جاري تحليل الأسعار المحلية...")
    await scrape_local_prices()
    msg = check_arbitrage()
    await update.message.reply_text(msg, parse_mode="Markdown")

async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنبيهات إدارة المخاطر"""
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour

    msg = "⚠️ **تنبيهات إدارة المخاطر**\n"
    msg += "═" * 30 + "\n\n"

    # تنبيه الجمعة
    if weekday == 4:  # الجمعة
        msg += "📅 اليوم جمعة\n"
        msg += "🔴 يُنصح بإغلاق الصفقات قبل الإغلاق بـ 3 ساعات\n"
        msg += "🔴 تجنب فتح مراكز جديدة\n\n"
    else:
        msg += f"📅 اليوم: {now.strftime('%A')}\n"
        msg += "🟢 لا توجد تحذيرات خاصة اليوم\n\n"

    # نصائح عامة
    msg += "📌 **نصائح عامة**:\n"
    msg += "• استخدم أوامر Stop Loss دائماً\n"
    msg += "• لا تخاطر بأكثر من 2% من رأس المال\n"
    msg += "• راقب الأخبار الاقتصادية الهامة\n"
    msg += "• تنويع محفظتك الاستثمارية"

    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حالة البوت"""
    msg = "📊 **حالة البوت**\n"
    msg += "═" * 30 + "\n\n"
    msg += f"🟢 البوت يعمل\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    msg += f"🔑 BOT_TOKEN: {'✅ موجود' if BOT_TOKEN else '❌ غير موجود'}\n"
    msg += f"📰 NEWS_API_KEY: {'✅ موجود' if NEWS_API_KEY else '❌ غير موجود'}\n"
    msg += f"📡 TELEGRAM_API: {'✅ موجود' if TELEGRAM_API_ID else '❌ غير موجود'}\n"
    msg += f"📡 CHANNELS: {'✅ موجود' if CHANNELS else '❌ غير موجود'}\n"
    msg += f"💱 سعر الصرف: 1 دولار = {EXCHANGE_RATE_SAR} SAR\n"
    msg += f"💱 سعر الصرف: 1 SAR = {EXCHANGE_RATE_YER:.0f} YER\n\n"
    msg += f"📊 الأسعار المحلية: {len(local_prices)} مدينة"

    if local_prices:
        for city, price in local_prices.items():
            msg += f"\n  • {city}: {price:.2f} ريال"

    await update.message.reply_text(msg, parse_mode="Markdown")

# ============================================================
# 8. الدالة الرئيسية
# ============================================================

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    logger.info("🚀 بدء تشغيل وكيل الذهب الذكي...")

    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN غير موجود!")
        sys.exit(1)

    try:
        # إنشاء التطبيق
        app = Application.builder().token(BOT_TOKEN).build()
        logger.info("✅ تم إنشاء التطبيق")

        # إضافة المعالجات
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_cmd))
        app.add_handler(CommandHandler("report", report_cmd))
        app.add_handler(CommandHandler("gold", gold_cmd))
        app.add_handler(CommandHandler("prices", prices_cmd))
        app.add_handler(CommandHandler("arbitrage", arbitrage_cmd))
        app.add_handler(CommandHandler("risk", risk_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        logger.info("✅ تم تسجيل الأوامر")

        # التحقق من البيئة
        hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

        if hostname:
            # وضع Webhook لـ Render
            logger.info(f"🌐 تشغيل في وضع Webhook على المنفذ {PORT}")
            webhook_url = f"https://{hostname}/webhook"
            logger.info(f"📍 Webhook URL: {webhook_url}")

            app.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path="webhook",
                webhook_url=webhook_url
            )
        else:
            # وضع Polling للتشغيل المحلي
            logger.info("💻 تشغيل في وضع Polling (محلي)")
            app.run_polling()

    except Exception as e:
        logger.error(f"❌ خطأ فادح: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
