#!/bin/bash

echo "🚀 بدء عملية البناء على Render..."
echo "📦 استخدام Python 3.11.8"

# تحديث pip والأدوات
pip install --upgrade pip setuptools wheel

# تثبيت متطلبات النظام
echo "📦 تثبيت متطلبات البناء..."
apt-get update
apt-get install -y build-essential python3-dev

# تثبيت numpy أولاً
echo "📦 تثبيت numpy 1.24.3..."
pip install numpy==1.24.3

# تثبيت pandas
echo "📦 تثبيت pandas 2.0.3..."
pip install pandas==2.0.3

# تثبيت باقي المتطلبات
echo "📦 تثبيت باقي المتطلبات..."
pip install -r requirements.txt

# التحقق من الإصدارات
echo "✅ الإصدارات المثبتة:"
pip show numpy pandas telethon cryptography

echo "✅ انتهت عملية البناء بنجاح!"
