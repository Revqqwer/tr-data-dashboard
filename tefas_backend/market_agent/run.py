"""
Market Agent — CLI Çalıştırıcı

Kullanım (PythonAnywhere bash):
    cd ~/tr-data-dashboard
    python tefas_backend/market_agent/run.py --daily
    python tefas_backend/market_agent/run.py --weekly
"""
import argparse, logging, os, sys, time, tempfile
from pathlib import Path

# Proje kökünü path'e ekle
_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# .env yükle
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

from tefas_backend.market_agent.collector import collect_all
from tefas_backend.market_agent.analyzer  import filter_news, generate_daily_report, generate_weekly_report
from tefas_backend.market_agent.reports   import save_report
import sqlite3 as _sqlite3, smtplib as _smtp, os as _os
from email.mime.text import MIMEText as _MIMEText
from email.mime.multipart import MIMEMultipart as _MIMEMultipart


def _send_to_subscribers(report_text: str, report_type: str):
    """Tüm onaylı abonelere raporu emaile gönder."""
    mail_user = _os.environ.get('MAIL_USERNAME', '')
    mail_pass = _os.environ.get('MAIL_PASSWORD', '')
    if not mail_user or not mail_pass:
        print("⚠️  MAIL_USERNAME/MAIL_PASSWORD ayarlanmamış, email gönderilmiyor.")
        return

    db_path = _ROOT / 'data' / 'cache.db'
    try:
        with _sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                'SELECT email, token FROM report_subscribers WHERE confirmed=1'
            ).fetchall()
    except Exception as e:
        print(f"⚠️  Abone listesi alınamadı: {e}")
        return

    if not rows:
        print("ℹ️  Onaylı abone yok.")
        return

    label = "Haftalık" if report_type == "weekly" else "Günlük"
    subject = f"3N Finans — {label} Piyasa Raporu"

    # Plain-text → basit HTML dönüşümü
    html_body = '<pre style="font-family:monospace;white-space:pre-wrap;font-size:13px;line-height:1.7">' + \
                report_text.replace('<','&lt;').replace('>','&gt;') + '</pre>'

    sent = 0
    for email, token in rows:
        unsub = f"https://www.3nfinans.com/unsubscribe/{token}"
        body = f"""
        <div style="font-family:'Segoe UI',sans-serif;max-width:600px;margin:0 auto;background:#060b16;color:#e2e8f0;border-radius:12px;overflow:hidden;">
          <div style="background:#f0b429;padding:4px 0"></div>
          <div style="padding:28px 32px">
            <h2 style="color:#f0b429;margin:0 0 20px;font-size:18px;">3N Finans — {label} Piyasa Raporu</h2>
            {html_body}
            <div style="margin-top:28px;padding-top:16px;border-top:1px solid #1e2d45;font-size:11px;color:#334155">
              <a href="https://www.3nfinans.com/dashboard" style="color:#f0b429;text-decoration:none;">Panele Git</a>
              &nbsp;·&nbsp;
              <a href="{unsub}" style="color:#475569;text-decoration:none;">Abonelikten Çık</a>
            </div>
          </div>
        </div>"""
        try:
            msg = _MIMEMultipart()
            msg['From']    = mail_user
            msg['To']      = email
            msg['Subject'] = subject
            msg.attach(_MIMEText(body, 'html', 'utf-8'))
            with _smtp.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as s:
                s.login(mail_user, mail_pass)
                s.sendmail(mail_user, email, msg.as_string())
            sent += 1
        except Exception as e:
            print(f"  ✗ {email}: {e}")

    print(f"✉️  {sent}/{len(rows)} aboneye rapor gönderildi.")



def _report_to_telegram(report_text: str):
    """Raporu Türkçe sese çevirip Telegram kanalına gönder."""
    import anthropic, openai, requests

    bot_token = _os.environ.get('MARKET_TELEGRAM_BOT_TOKEN', '')
    chat_id   = _os.environ.get('MARKET_TELEGRAM_CHAT_ID', '')
    openai_key = _os.environ.get('OPENAI_API_KEY', '')

    if not bot_token or not chat_id or not openai_key:
        print("⚠️  MARKET_TELEGRAM_BOT_TOKEN / MARKET_TELEGRAM_CHAT_ID / OPENAI_API_KEY eksik, Telegram atlanıyor.")
        return

    print("\n🎙️  Rapor konuşma diline çevriliyor (Claude)...")
    try:
        claude = anthropic.Anthropic(api_key=_os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": f"""Aşağıdaki finans raporunu kısa ve öz bir sesli özete dönüştür.
Kurallar:
- SADECE konuşma dili kullan, sıcak ve akıcı Türkçe
- Semboller (━, │, ┌, └, ●, ▸), markdown (**bold**), emojiler KULLANMA
- Madde numaraları yerine geçiş cümleleri kullan
- Başlangıçta "3N Finans günlük piyasa özetine hoş geldiniz." de
- Sonunda kısa bir kapanış cümlesi ekle
- Maksimum 2 dakikalık konuşma (yaklaşık 1800 karakter)
- Sadece en önemli 3-4 gelişmeyi vurgula, detaylara girme

RAPOR:
{report_text}"""}]
        )
        speech_text = msg.content[0].text
        print(f"✓ Konuşma metni hazır ({len(speech_text)} karakter)")
    except Exception as e:
        print(f"⚠️  Claude dönüşümü başarısız: {e}")
        return

    print("🔊 OpenAI TTS ile ses üretiliyor...")
    try:
        oai = openai.OpenAI(api_key=openai_key)
        # OpenAI TTS max 4096 karakter
        if len(speech_text) > 4000:
            speech_text = speech_text[:4000].rsplit(' ', 1)[0] + '...'
        response = oai.audio.speech.create(
            model="tts-1",
            voice="nova",
            input=speech_text,
            speed=1.0,
        )
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        response.stream_to_file(tmp_path)
        print(f"✓ Ses dosyası oluşturuldu: {tmp_path}")
    except Exception as e:
        print(f"⚠️  TTS başarısız: {e}")
        return

    print("📤 Telegram kanalına gönderiliyor...")
    try:
        import datetime as _dt2
        bugun = _dt2.date.today().strftime("%d.%m.%Y")
        caption = f"📊 3N Finans — Günlük Piyasa Özeti\n{bugun}"
        with open(tmp_path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendAudio",
                data={"chat_id": chat_id, "caption": caption, "title": f"Günlük Özet {bugun}"},
                files={"audio": ("gunluk_ozet.mp3", f, "audio/mpeg")},
                timeout=120,
            )
        if r.ok:
            print("✅ Telegram'a başarıyla gönderildi!")
        else:
            print(f"⚠️  Telegram hatası: {r.text}")
    except Exception as e:
        print(f"⚠️  Telegram gönderimi başarısız: {e}")
    finally:
        try:
            _os.unlink(tmp_path)
        except:
            pass

def run_daily() -> str:
    print("=" * 60)
    print("🗞️  GÜNLÜK RAPOR OLUŞTURULUYOR")
    print("=" * 60)

    data     = collect_all(daily=True)

    # Bir önceki günlük raporu bağlam olarak al
    from tefas_backend.market_agent.reports import get_latest
    prev = get_latest("daily")
    prev_content = prev.get("content", "") if prev else ""
    if prev_content:
        print("📋 Önceki rapor bağlam olarak yüklendi (tekrar önleme aktif)")

    print("\n🤖 Haberler filtreleniyor (Claude Haiku)...")
    filtered = filter_news(data["news"], prev_report_content=prev_content)
    print(f"✓ {len(filtered)} haber seçildi\n")

    print("📝 Günlük rapor yazılıyor (Claude Sonnet)...")
    report = generate_daily_report(filtered, data["earnings"])

    save_report("daily", report)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # Abonelere gönder
    _send_to_subscribers(report, "daily")

    # Telegram sesli özet
    _report_to_telegram(report)

    return report


def run_weekly() -> str:
    print("=" * 60)
    print("📋 HAFTALIK RAPOR OLUŞTURULUYOR")
    print("=" * 60)

    data     = collect_all(daily=False)
    print("\n🤖 Haberler filtreleniyor (Claude Haiku)...")
    filtered = filter_news(data["news"])
    print(f"✓ {len(filtered)} haber seçildi\n")

    print("📝 Haftalık rapor yazılıyor (Claude Sonnet)...")
    report = generate_weekly_report(filtered, data["earnings"])

    save_report("weekly", report)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    _send_to_subscribers(report, "weekly")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Intelligence Agent")
    parser.add_argument("--daily",  action="store_true", help="Günlük rapor oluştur")
    parser.add_argument("--weekly", action="store_true", help="Haftalık rapor oluştur")
    parser.add_argument("--auto",   action="store_true",
                        help="Pazar → haftalık, diğer günler → günlük (scheduled task için)")
    args = parser.parse_args()

    if args.auto:
        from datetime import datetime
        # Pazar = 6 (weekday), Pazartesi = 0
        if datetime.now().weekday() == 6:
            print("📅 Pazar günü — haftalık rapor çalıştırılıyor")
            run_weekly()
        else:
            print(f"📅 Günlük rapor çalıştırılıyor")
            run_daily()
    elif args.daily:
        run_daily()
    elif args.weekly:
        run_weekly()
    else:
        print("Kullanım: python run.py --daily | --weekly | --auto")
        sys.exit(1)
