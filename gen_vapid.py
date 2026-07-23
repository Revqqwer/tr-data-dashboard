# -*- coding: utf-8 -*-
"""
VAPID anahtar cifti uretir ve .env'e ekler. BIR KEZ calistirilir.

    cd ~/tr-data-dashboard && python gen_vapid.py

Ozel anahtar sunucudan disari cikmaz. Zaten anahtar varsa uzerine yazmaz
(yazarsa mevcut tum abonelikler gecersiz olur).
"""
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.env'


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def main():
    existing = ENV.read_text(encoding='utf-8') if ENV.exists() else ''
    if 'VAPID_PRIVATE_KEY=' in existing:
        print('!! .env icinde zaten VAPID anahtari var.')
        print('   Uzerine yazarsan MEVCUT TUM ABONELIKLER GECERSIZ olur.')
        print('   Gercekten yenilemek istiyorsan once .env icindeki VAPID_* satirlarini sil.')
        sys.exit(1)

    key = ec.generate_private_key(ec.SECP256R1())

    # Ozel anahtar: ham 32 bayt -> base64url (pywebpush bu formati kabul eder)
    priv_raw = key.private_numbers().private_value.to_bytes(32, 'big')
    # Acik anahtar: sikistirilmamis nokta (0x04 || X || Y) -> base64url
    pub_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    subject = os.environ.get('VAPID_SUBJECT', 'mailto:hakandeveli24@gmail.com')
    lines = (
        '\n# ── Web Push (VAPID) — gen_vapid.py tarafindan uretildi ──\n'
        f'VAPID_PUBLIC_KEY={b64u(pub_raw)}\n'
        f'VAPID_PRIVATE_KEY={b64u(priv_raw)}\n'
        f'VAPID_SUBJECT={subject}\n'
    )
    with open(ENV, 'a', encoding='utf-8') as f:
        f.write(lines)

    print('.env dosyasina eklendi:')
    print('  VAPID_PUBLIC_KEY =', b64u(pub_raw))
    print('  VAPID_PRIVATE_KEY = (gizli, yazilmadi)')
    print('  VAPID_SUBJECT =', subject)
    print('\nSonraki adim: PA Web sekmesinden Reload.')


if __name__ == '__main__':
    main()
