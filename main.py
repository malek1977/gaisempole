import os
import logging
import re
import asyncio
from datetime import datetime
import requests
import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from newsapi import NewsApiClient
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telethon import TelegramClient

# ---------- إعداد التسجيل ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------- متغيرات البيئة ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
CHANNELS = os.environ.get("CHANNELS", "")
EXCHANGE_RATE_SAR = float(os.environ.get("EXCHANGE_RATE_SAR", "3.75"))  # سعر صرف الدولار للريال السعودي
EXCHANGE_RATE_YER = float(os.environ.get("EXCHANGE_RATE_YER", "530"))    # سعر صرف الريال السعودي للريال اليمني

# ---------- ثوابت العيارات ----------
GOLD_CARATS = {
    24: {"name": "ذهب 24 قيراط", "purity": 0.9999},
    21: {"name": "ذهب 21 قيراط", "purity": 0.875},
    18: {"name": "ذهب 18 قيراط", "purity": 0.750}
}

# ---------- أدوات التحليل ----------
analyzer = SentimentIntensityAnalyzer()
newsapi = NewsApiClient(api_key=NEWS_API_KEY) if NEWS_API_KEY else None

# ---------- عميل تيليثون ----------
telethon_client = None
if TELEGRAM_API_ID and TELEGRAM_API_HASH:
    telethon_client = TelegramClient('agent_session', int(TELEGRAM_API_ID), TELEGRAM_API_HASH)

# ---------- تخزين مؤقت ----------
local_prices = {}  # {'صنعاء': 230.0, ...}

# ============================================================
# 1. دوال حساب الأسعار حسب العيار
# ============================================================

def calculate_gold_prices(oz_price_usd):
    """
    حساب أسعار الذهب لجميع العيارات
    المدخل: سعر الأونصة بالدولار
    المخرج: قاموس يحتوي على أسعار العيارات بالعملات المختلفة
    """
    if not oz_price_usd:
        return None

    results = {}

    for carat, carat_info in GOLD_CARATS.items():
        # سعر الجرام بالدولار (الأونصة = 31.1035 جرام)
        gram_price_usd = (oz_price_usd / 31.1035) * carat_info["purity"]

        # التحويل للعملات
        gram_price_sar = gram_price_usd * EXCHANGE_RATE_SAR
        gram_price_yer = gram_price_sar * EXCHANGE_RATE_YER

        # حساب أسعار الأجزاء
        fractions = {
            "ربع جرام": 0.25,
            "نصف جرام": 0.5,
            "5 جرام": 5,
            "10 جرام": 10,
            "أونصة": 31.1035
        }

        fraction_prices = {}
        for fraction_name, weight in fractions.items():
            fraction_prices[fraction_name] = {
                "usd": round(gram_price_usd * weight, 2),
                "sar": round(gram_price_sar * weight, 2),
                "yer": round(gram_price_yer * weight, 2)
            }

        results[carat] = {
            "name": carat_info["name"],
            "purity": carat_info["purity"],
            "oz_price_usd": round(oz_price_usd, 2),
            "gram_price": {
                "usd": round(gram_price_usd, 2),
                "sar": round(gram_price_sar, 2),
                "yer": round(gram_price_yer, 2)
            },
            "fractions": fraction_prices
        }

    return results

# ============================================================
# 2. دوال جلب البيانات
# ============================================================

def fetch_gold_price():
    """جلب سعر الذهب العالمي"""
    try:
        resp = requests.get("https://api.metals.live/v1/spot/gold", timeout=10)
        data = resp.json()
        price = data.get("price")
        if price:
            return round(float(price), 2)
    except Exception as e:
        logger.error(f"فشل جلب سعر الذهب: {e}")

    # محاولة بديلة
    try:
        resp = requests.get("https://www.gold-api.com/price/XAU", timeout=10)
        data = resp.json()
        return round(float(data.get("price", 0)), 2)
    except:
        pass

    return None

def fetch_news_sentiment():
    """تحليل المشاعر الإخبارية"""
    if not newsapi:
        return None, "⚠️ لم يتم تعيين NEWS_API_KEY"
    try:
        query = 'gold OR "Federal Reserve" OR geopolitics OR "interest rates"'
        articles = newsapi.get_everything(q=query, language='en', sort_by='publishedAt', page_size=50)
        scores = []
        headlines = []
        for article in articles.get('articles', []):
            title = article.get('title', '') or ''
            description = article.get('description', '') or ''
            text = title + ' ' + description
            if text.strip():
                score = analyzer.polarity_scores(text)['compound']
                scores.append(score)
                headlines.append(title[:80])
        if not scores:
            return None, "لا توجد أخبار كافية للتحليل"
        avg_score = sum(scores) / len(scores)
        if avg_score > 0.2:
            sentiment = "إيجابي (تفاؤل)"
        elif avg_score < -0.2:
            sentiment = "سلبي (قلق)"
        else:
            sentiment = "محايد"
        sample_headlines = headlines[:5]
        return avg_score, sentiment, sample_headlines
    except Exception as e:
        logger.error(f"خطأ تحليل الأخبار: {e}")
        return None, "فشل تحليل المشاعر"

def fetch_cot_report():
    """تحليل تقرير COT"""
    try:
        df = pd.read_excel("https://www.cftc.gov/dea/futures/deacmxsf.xls", sheet_name="Gold (Comex)", skiprows=2)
        long_col = [c for c in df.columns if 'LONG' in str(c).upper() and 'NON-COMM' in str(c).upper()]
        short_col = [c for c in df.columns if 'SHORT' in str(c).upper() and 'NON-COMM' in str(c).upper()]
        if not long_col or not short_col:
            return None, None, "لم يتم العثور على بيانات المراكز غير التجارية"
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row
        net = last_row[long_col[0]] - last_row[short_col[0]]
        prev_net = prev_row[long_col[0]] - prev_row[short_col[0]]
        change = net - prev_net
        if change > 5000:
            trend = "زيادة كبيرة في الشراء (صعودي بقوة)"
        elif change > 0:
            trend = "زيادة طفيفة في الشراء (صعودي)"
        elif change < -5000:
            trend = "زيادة كبيرة في البيع (هبوطي بقوة)"
        elif change < 0:
            trend = "زيادة طفيفة في البيع (هبوطي)"
        else:
            trend = "ثبات المراكز"
        return net, change, trend
    except Exception as e:
        logger.error(f"خطأ COT: {e}")
        return None, None, "فشل تحليل تقرير COT"

async def scrape_local_prices():
    """جلب أسعار الذهب المحلية من قنوات تيليجرام"""
    if not telethon_client or not CHANNELS:
        return
    try:
        if not telethon_client.is_connected():
            await telethon_client.start()
        channels = [ch.strip() for ch in CHANNELS.split(',') if ch.strip()]
        for channel in channels:
            async for message in telethon_client.iter_messages(channel, limit=5):
                if message.text:
                    text = message.text
                    for city in ["صنعاء", "إب", "عدن"]:
                        if city in text and ('ريال سعودي' in text or 'SAR' in text.upper()):
                            numbers = re.findall(r'\d+\.?\d*', text)
                            if numbers:
                                price = float(numbers[-1])
                                local_prices[city] = price
                                logger.info(f"تم تحديث {city}: {price} ريال")
                                break
    except Exception as e:
        logger.error(f"خطأ في قراءة القنوات: {e}")

def check_arbitrage():
    """تحليل فرص المراجحة"""
    if len(local_prices) < 2:
        return "لا توجد بيانات كافية للمقارنة (يلزم سعر مدينتين على الأقل)."
    sorted_cities = sorted(local_prices.items(), key=lambda x: x[1], reverse=True)
    high_city, high_price = sorted_cities[0]
    low_city, low_price = sorted_cities[-1]
    diff = high_price - low_price
    if diff > 3:
        return (f"🚀 **فرصة مراجحة**: شراء من {low_city} بسعر {low_price:.2f} ريال، "
                f"بيع في {high_city} بسعر {high_price:.2f} ريال. الفارق: {diff:.2f} ريال/جرام")
    else:
        return f"لا توجد فجوة مجدية حالياً (الفارق {diff:.2f} ريال)"

# ============================================================
# 3. توليد التقارير المطورة
# ============================================================

def format_price_table(gold_prices):
    """تنسيق جدول الأسعار بشكل جميل"""
    lines = []
    lines.append("┌" + "─" * 70 + "┐")
    lines.append("│" + " " * 25 + "أسعار الذهب حسب العيار" + " " * 26 + "│")
    lines.append("├" + "─" * 70 + "┤")

    for carat, data in gold_prices.items():
        lines.append(f"│ {data['name']} (نقاوة {data['purity']*100:.1f}%){' ' * (40 - len(data['name']))}│")
        lines.append(f"│   سعر الأونصة: {data['oz_price_usd']:,.2f} دولار{' ' * (42 - len(str(data['oz_price_usd'])))}│")
        lines.append(f"│   سعر الجرام: USD {data['gram_price']['usd']:.2f} | SAR {data['gram_price']['sar']:.2f} | YER {data['gram_price']['yer']:,.0f} │")
        lines.append("│" + "─" * 70 + "│")
        lines.append("│   أسعار الأجزاء:                                        │")
        for fraction, prices in data['fractions'].items():
            line = f"│     {fraction}: USD {prices['usd']:.2f} | SAR {prices['sar']:.2f} | YER {prices['yer']:,.0f}"
            line += " " * (70 - len(line)) + "│"
            lines.append(line)
        lines.append("├" + "─" * 70 + "┤")

    lines.append("└" + "─" * 70 + "┘")
    return "\n".join(lines)

async def generate_full_report():
    """توليد التقرير المفصل المطور"""
    lines = []
    lines.append("📊 **التقرير الذهبي الشامل - النسخة المطورة**")
    lines.append("═" * 40)

    # 1. السعر العالمي وحساب العيارات
    gold_price = fetch_gold_price()
    if gold_price:
        lines.append(f"🪙 **سعر الذهب العالمي**: {gold_price:.2f} دولار/الأونصة")
        lines.append("")

        # حساب أسعار العيارات
        gold_prices = calculate_gold_prices(gold_price)
        if gold_prices:
            lines.append("📊 **أسعار الذهب حسب العيار**")
            lines.append(format_price_table(gold_prices))
            lines.append("")
            lines.append(f"💱 **سعر الصرف المستخدم**: 1 دولار = {EXCHANGE_RATE_SAR} ريال سعودي")
            lines.append(f"💱 **سعر الصرف المستخدم**: 1 ريال سعودي = {EXCHANGE_RATE_YER:.0f} ريال يمني (صنعاء)")
    else:
        lines.append("❌ تعذر جلب السعر العالمي")

    lines.append("")

    # 2. تحليل المشاعر
    lines.append("🧠 **ثانياً: تحليل المشاعر الإخبارية**")
    sentiment_data = fetch_news_sentiment()
    if sentiment_data and isinstance(sentiment_data[0], float):
        avg_score, sentiment_label, headlines = sentiment_data
        lines.append(f"متوسط درجة المشاعر: {avg_score:.2f} ({sentiment_label})")
        lines.append("أهم العناوين:")
        for h in headlines:
            lines.append(f"  • {h}")
    else:
        lines.append(sentiment_data[1] if sentiment_data else "خطأ في التحليل")
    lines.append("")

    # 3. تقرير COT
    lines.append("📈 **ثالثاً: تقرير التزامات المتداولين (COT)**")
    cot_net, cot_change, cot_trend = fetch_cot_report()
    if cot_net is not None:
        lines.append(f"صافي عقود المضاربة: {cot_net:,.0f}")
        lines.append(f"التغير عن الأسبوع السابق: {cot_change:+,.0f}")
        lines.append(f"الاستنتاج: {cot_trend}")
    else:
        lines.append(cot_trend)
    lines.append("")

    # 4. الأسعار المحلية
    lines.append("🇾🇪 **رابعاً: أسعار الذهب المحلية (اليمن - عيار 21)**")
    await scrape_local_prices()
    if local_prices:
        for city, price in local_prices.items():
            lines.append(f"• {city}: {price:.2f} ريال سعودي/جرام")
    else:
        lines.append("⚠️ لا توجد بيانات محلية (تأكد من إعداد CHANNELS و TELEGRAM)")
    lines.append("")

    # 5. فرص المراجحة
    lines.append("💱 **خامساً: فرص المراجحة الجغرافية**")
    arb_msg = check_arbitrage()
    lines.append(arb_msg)
    lines.append("")

    # 6. توقعات الافتتاح
    lines.append("🔮 **سادساً: توقعات الافتتاح ونصائح التداول**")
    sentiment_neg = False
    cot_bearish = False
    if sentiment_data and isinstance(sentiment_data[0], float):
        sentiment_neg = sentiment_data[0] < -0.2
    if cot_trend and ("بيع" in cot_trend or "هبوطي" in cot_trend):
        cot_bearish = True

    if sentiment_neg and cot_bearish:
        lines.append("⚠️ **احتمال كبير لفجوة هابطة يوم الإثنين**.")
        lines.append("نصيحة: تجنب الشراء المباشر مع الافتتاح، انتظر 30 دقيقة لتهدأ فروق الأسعار.")
    elif not sentiment_neg and not cot_bearish and sentiment_data and isinstance(sentiment_data[0], float) and sentiment_data[0] > 0.2:
        lines.append("🌟 **احتمال فجوة صاعدة**.")
        lines.append("نصيحة: لا تلاحق السعر مع الافتتاح، استخدم أوامر معلقة.")
    else:
        lines.append("⚖️ **إشارات متضاربة أو محايدة**.")
        lines.append("يفضل مراقبة السوق أول 30 دقيقة.")
    lines.append("")

    # 7. إضافة تحليل سريع
    if gold_price:
        lines.append("📊 **تحليل سريع**")
        lines.append(f"• أعلى سعر للجرام عيار 24: ${gold_prices[24]['gram_price']['usd']:.2f}")
        lines.append(f"• أعلى سعر للجرام عيار 21: ${gold_prices[21]['gram_price']['usd']:.2f}")
        lines.append(f"• أعلى سعر للجرام عيار 18: ${gold_prices[18]['gram_price']['usd']:.2f}")

    lines.append("")
    lines.append("─" * 40)
    lines.append("تم إنشاء التقرير بواسطة وكيل الذهب الذكي 🚀 | النسخة المطورة v2.0")

    return "\n".join(lines)

# ============================================================
# 4. أوامر البوت المطورة
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🪙 **مرحباً بك في وكيل الذهب الذكي - النسخة المطورة**\n\n"
        "✨ **المميزات الجديدة**:\n"
        "• عرض أسعار الذهب لـ 18, 21, 24 قيراط\n"
        "• أسعار الجرام وأجزائه (ربع، نصف، 5، 10 جرام)\n"
        "• الأسعار بـ USD, SAR, YER\n\n"
        "📋 **الأوامر المتاحة**:\n"
        "/report - تقرير مفصل مع جميع العيارات\n"
        "/gold - سعر الذهب العالمي فقط\n"
        "/arbitrage - فرص المراجحة المحلية\n"
        "/prices - عرض أسعار العيارات فقط\n"
        "/risk - تنبيه إدارة المخاطر",
        parse_mode="Markdown"
    )

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ جارٍ إعداد التقرير المفصل، قد يستغرق بضع ثوانٍ...")
    try:
        report = await generate_full_report()
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text(report, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"خطأ في إنشاء التقرير: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء إعداد التقرير")

async def gold_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = fetch_gold_price()
    if price:
        gold_prices = calculate_gold_prices(price)
        msg = f"🪙 **سعر الذهب العالمي**: {price:.2f} دولار/الأونصة\n\n"
        msg += "📊 **أسعار الجرام حسب العيار**:\n"
        for carat, data in gold_prices.items():
            msg += f"• {data['name']}: ${data['gram_price']['usd']:.2f} | SAR {data['gram_price']['sar']:.2f} | YER {data['gram_price']['yer']:,.0f}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر")

async def prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أسعار العيارات فقط"""
    price = fetch_gold_price()
    if price:
        gold_prices = calculate_gold_prices(price)
        msg = "📊 **جدول أسعار الذهب حسب العيار**\n"
        msg += "═" * 30 + "\n\n"
        for carat, data in gold_prices.items():
            msg += f"**{data['name']}** (نقاوة {data['purity']*100:.1f}%)\n"
            msg += f"• الأونصة: ${data['oz_price_usd']:.2f}\n"
            msg += f"• الجرام: ${data['gram_price']['usd']:.2f} | SAR {data['gram_price']['sar']:.2f} | YER {data['gram_price']['yer']:,.0f}\n"
            msg += "• الأجزاء:\n"
            for fraction, prices in data['fractions'].items():
                msg += f"  - {fraction}: ${prices['usd']:.2f}\n"
            msg += "\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر")

async def arbitrage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await scrape_local_prices()
    msg = check_arbitrage()
    await update.message.reply_text(msg, parse_mode="Markdown")

async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    if now.weekday() == 4:
        await update.message.reply_text("⚠️ اليوم جمعة: يُنصح بإغلاق الصفقات قبل الإغلاق بـ 3 ساعات.")
    else:
        await update.message.reply_text("🟢 لا يوجد تحذير خاص اليوم.")

# ============================================================
# 5. الدالة الرئيسية
# ============================================================

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN غير موجود!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("gold", gold_cmd))
    app.add_handler(CommandHandler("prices", prices_cmd))
    app.add_handler(CommandHandler("arbitrage", arbitrage_cmd))
    app.add_handler(CommandHandler("risk", risk_cmd))

    # إعداد الـ Webhook
    port = int(os.environ.get("PORT", 8443))
    hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if hostname:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=f"https://{hostname}/webhook"
        )
    else:
        # للتشغيل المحلي
        app.run_polling()

    logger.info("البوت يعمل...")

if __name__ == "__main__":
    main()
