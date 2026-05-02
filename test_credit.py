from app import load_excel_credit, fetch_bddk_credit, fetch_usdtry

df = load_excel_credit()
print(f"Excel: {len(df)} satir, son tarih: {df['tarih'].max().date()}")
print(df.tail(3).to_string(index=False))
print()
print("BDDK scraping...")
tarih, tuk, tic, tic_yp = fetch_bddk_credit()
print(f"Tarih:      {tarih}")
print(f"Tuketici:   {tuk}")
print(f"Ticari TL:  {tic}")
print(f"Ticari YP:  {tic_yp}")
print()
print("USDTRY cekilior...")
kur = fetch_usdtry(tarih)
print(f"USDTRY:     {kur}")
if tic_yp and kur:
    print(f"Ticari USD: {round(tic_yp/kur, 2)}")
