#!/bin/bash

echo "🚀 بدء عملية البناء..."

# تحديث pip
pip install --upgrade pip setuptools wheel

# تثبيت المتطلبات
pip install -r requirements.txt

# التحقق من التثبيت
echo "✅ تم تثبيت المتطلبات"

# عرض الإصدارات
pip list

echo "✅ انتهت عملية البناء"
