#!/bin/bash

echo "🚀 بدء عملية البناء على Render..."
echo "📦 استخدام Python 3.11.8"

# تثبيت متطلبات النظام
echo "📦 تثبيت متطلبات البناء..."
apt-get update
apt-get install -y build-essential python3-dev python3-pip

# تحديث pip
pip install --upgrade pip setuptools wheel

# تثبيت numpy
echo "📦 تثبيت numpy 1.23.5..."
pip install numpy==1.23.5

# تثبيت pandas
echo "📦 تثبيت pandas 1.5.3..."
pip install pandas==1.5.3

# تثبيت باقي المتطلبات
echo "📦 تثبيت باقي المتطلبات..."
pip install -r requirements.txt

# التحقق من الإصدارات
echo "✅ الإصدارات المثبتة:"
pip show numpy pandas telethon cryptography

echo "✅ انتهت عملية البناء بنجاح!"
