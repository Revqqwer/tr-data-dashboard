# -*- coding: utf-8 -*-
"""
Ortak mail gonderim ayarlari — SMTP saglayicisi env ile degistirilebilir.

Varsayilan: Gmail (mevcut davranis korunur).
Brevo'ya gecerken SADECE env degiskenleri degisir, kod degismez:

    MAIL_SMTP_HOST=smtp-relay.brevo.com
    MAIL_SMTP_PORT=587
    MAIL_USERNAME=<brevo giris e-postasi>      # Brevo SMTP kullanici adi
    MAIL_PASSWORD=<brevo SMTP key>             # Gmail sifresi degil, SMTP anahtari
    MAIL_FROM=3N Finans <bulten@3nfinans.com>  # dogrulanmis gonderen
    MAIL_REPLY_TO=destek@3nfinans.com

Port 465 -> SSL, 587 -> STARTTLS otomatik secilir.
"""
import os
import smtplib
from email.utils import parseaddr, formataddr

SMTP_HOST = os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("MAIL_SMTP_PORT", "465"))

MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
MAIL_FROM     = os.environ.get("MAIL_FROM", "")
MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO", "")


def from_header() -> str:
    """Gorunen gonderen. MAIL_FROM yoksa hesabin kendisi + marka adi."""
    return MAIL_FROM or formataddr(("3N Finans", MAIL_USERNAME))


def envelope_sender() -> str:
    """Zarf gondericisi (Return-Path). SPF/DMARC hizalamasi buna bakar —
    kendi alan adina gecince MAIL_FROM'daki adres kullanilmali."""
    addr = parseaddr(MAIL_FROM)[1]
    return addr or MAIL_USERNAME


def reply_to() -> str:
    return MAIL_REPLY_TO or MAIL_USERNAME


def smtp_connect(timeout: int = 30):
    """Baglanmis + login olmus SMTP nesnesi dondurur (465=SSL, digeri=STARTTLS)."""
    if SMTP_PORT == 465:
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout)
        s.ehlo()
        s.starttls()
        s.ehlo()
    s.login(MAIL_USERNAME, MAIL_PASSWORD)
    return s
