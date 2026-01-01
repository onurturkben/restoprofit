# analysis_engine.py — sağlamlaştırılmış sürüm (OPTIMUM FIX + PRICE BUCKETING)
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
from database import db, Urun, SatisKaydi
import warnings
import json

# -----------------------------
# Yardımcı: grafiğe uygun data
# -----------------------------
def _as_chartjs_line(labels, y_values, label="Tahmini Toplam Kâr (TL)"):
    return json.dumps({
        "labels": [round(float(x), 2) if isinstance(x, (int, float, np.number)) else x for x in labels],
        "datasets": [{
            "label": label,
            "data": [round(float(y), 2) for y in y_values],
            "borderColor": "#0d6efd",
            "backgroundColor": "rgba(13,110,253,.15)",
            "fill": True,
            "tension": 0.1
        }]
    })

def _as_chartjs_bar(labels, data_a, label_a, data_b, label_b):
    return json.dumps({
        "labels": labels,
        "datasets": [
            {
                "label": label_a,
                "data": [round(float(v), 2) for v in data_a],
                "backgroundColor": "rgba(54, 162, 235, 0.7)",
                "borderColor": "rgb(54, 162, 235)",
                "borderWidth": 1
            },
            {
                "label": label_b,
                "data": [round(float(v), 2) for v in data_b],
                "backgroundColor": "rgba(255, 99, 132, 0.7)",
                "borderColor": "rgb(255, 99, 132)",
                "borderWidth": 1
            }
        ]
    })

# ---------------------------------------------------
# Kritik FIX: Fiyatları bucket'layıp gruplayacağız
# ---------------------------------------------------
def _round_to_step(x: float, step: float) -> float:
    """
    x değerini step aralığına yuvarlar.
    Örn step=1 => 150.49 -> 150, 150.50 -> 151
    """
    if step <= 0:
        return float(x)
    return float(np.floor((float(x) / step) + 0.5) * step)

# ---------------------------------------------------
# Ortak veri çıkarımı: fiyat–satış ilişkisi tablosu
# ---------------------------------------------------
def _get_daily_sales_data(urun_id, price_step=1.0, lookback_days=None):
    """
    Çıktı kolonları:
      ['ortalama_fiyat', 'toplam_adet', 'gun_sayisi', 'ortalama_adet']
    En az 2 farklı fiyat noktası yoksa None döner.

    price_step:
      1.0 => 1 TL bucket
      0.5 => 0.5 TL bucket
    lookback_days:
      None => tüm veri
      int => son N gün
    """
    q = (db.session.query(
            SatisKaydi.tarih,
            SatisKaydi.adet,
            SatisKaydi.hesaplanan_birim_fiyat
        )
        .filter_by(urun_id=urun_id))

    rows = q.all()
    if not rows or len(rows) < 2:
        return None

    df = pd.DataFrame(rows, columns=['tarih', 'adet', 'hesaplanan_birim_fiyat'])
    df['tarih'] = pd.to_datetime(df['tarih'], errors='coerce')
    df = df.dropna(subset=['tarih', 'adet', 'hesaplanan_birim_fiyat'])

    if df.empty:
        return None

    # Son N gün filtresi (opsiyonel)
    if lookback_days is not None:
        cutoff = pd.Timestamp(datetime.now() - timedelta(days=int(lookback_days)))
        df = df[df['tarih'] >= cutoff]
        if df.empty:
            return None

    # Fiyat bucket: float gürültüsünü temizler
    df['fiyat_bucket'] = df['hesaplanan_birim_fiyat'].apply(lambda v: _round_to_step(v, price_step))

    # Günlük ortalama adet: aynı bucket kaç gün satılmış?
    grp = df.groupby('fiyat_bucket').agg(
        toplam_adet=('adet', 'sum'),
        gun_sayisi=('tarih', 'nunique')
    ).reset_index()

    grp = grp[grp['gun_sayisi'] > 0]
    if grp.empty:
        return None

    grp['ortalama_adet'] = grp['toplam_adet'] / grp['gun_sayisi']
    grp['ortalama_fiyat'] = grp['fiyat_bucket'].astype(float)

    # En az 2 farklı fiyat noktası şart
    if grp['ortalama_fiyat'].nunique() < 2:
        return None

    # Çok küçük örnekleri at (tek gün/tek satış gibi)
    # İstersen bu eşiği artırabilirsin.
    grp = grp[grp['gun_sayisi'] >= 1].copy()
    return grp.sort_values('ortalama_fiyat')

# -----------------------------------------
# Yardımcı: çizim için fiyat eğrisi üret
# -----------------------------------------
def _generate_price_curve_data_from_results(df_res):
    return _as_chartjs_line(df_res['test_fiyati'].tolist(), df_res['tahmini_kar'].tolist())

# ----------------------------------
# Motor 1: Hedef Marj
# ----------------------------------
def hesapla_hedef_marj(urun_ismi, hedef_marj_yuzdesi):
    try:
        urun = Urun.query.filter_by(isim=urun_ismi).first()
        if not urun:
            return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

        maliyet = float(urun.hesaplanan_maliyet or 0.0)
        if maliyet <= 0:
            return False, f"HATA: '{urun.isim}' ürününün maliyeti 0 TL veya negatif. Lütfen reçete ve hammadde fiyatlarını güncelleyin.", None

        if not (0 < float(hedef_marj_yuzdesi) < 100):
            return False, "HATA: Hedef marj %0 ile %100 arasında olmalıdır.", None

        m = float(hedef_marj_yuzdesi) / 100.0
        gereken_satis_fiyati = maliyet / (1 - m)

        rapor = (
            f"--- HESAPLAMA SONUCU ---\n"
            f"  Ürün: {urun.isim}\n"
            f"  Maliyet (COGS): {maliyet:.2f} TL\n"
            f"  Hedef Marj: %{hedef_marj_yuzdesi:.0f}\n\n"
            f"  🎯 GEREKEN SATIŞ FİYATI: {gereken_satis_fiyati:.2f} TL 🎯"
        )
        return True, rapor, None
    except Exception as e:
        return False, f"Hesaplama hatası: {e}", None

# ----------------------------------
# Motor 2: Fiyat Simülatörü (aynı FIX'ten faydalanır)
# ----------------------------------
def simule_et_fiyat_degisikligi(urun_ismi, test_edilecek_yeni_fiyat):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

            maliyet = float(urun.hesaplanan_maliyet or 0.0)
            if maliyet <= 0:
                return False, f"HATA: '{urun.isim}' ürününün maliyeti 0 TL. Lütfen reçeteleri tamamlayın.", None

            # FIX: bucket + son 180 gün mantıklı (istersen None yap)
            df_g = _get_daily_sales_data(urun.id, price_step=1.0, lookback_days=180)
            if df_g is None or df_g.empty:
                return False, f"HATA: '{urun.isim}' için en az 2 farklı fiyatta satış verisi bulunamadı.", None

            mevcut_ortalama_fiyat = float(df_g['ortalama_fiyat'].mean())
            mevcut_gunluk_satis = float(df_g['ortalama_adet'].mean())
            mevcut_gunluk_kar = (mevcut_ortalama_fiyat - maliyet) * mevcut_gunluk_satis

            X = df_g[['ortalama_fiyat']]
            y = df_g['ortalama_adet']  # FIX: günlük ortalama adet ile model kur
            model = LinearRegression().fit(X, y)

            if float(model.coef_[0]) >= 0:
                rapor = (
                    f"UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor (pozitif eğim). "
                    f"Veri yetersiz/hatalı olabilir.\n"
                )
                return False, rapor, None

            yeni_fiyat = float(test_edilecek_yeni_fiyat)
            tahmini_yeni_satis = max(0.0, float(model.predict(np.array([[yeni_fiyat]]))[0]))
            tahmini_yeni_kar = (yeni_fiyat - maliyet) * tahmini_yeni_satis
            kar_degisimi = tahmini_yeni_kar - mevcut_gunluk_kar

            rapor = (
                f"--- MEVCUT DURUM (Geçmiş Ort.) ---\n"
                f"  Ortalama Fiyat: {mevcut_ortalama_fiyat:.2f} TL\n"
                f"  Günlük Satış (Ort.): {mevcut_gunluk_satis:.1f} adet\n"
                f"  Maliyet: {maliyet:.2f} TL\n"
                f"  Tahmini Günlük Kâr: {mevcut_gunluk_kar:.2f} TL\n"
                f"{'-'*50}\n"
                f"--- SİMÜLASYON ({yeni_fiyat:.2f} TL) ---\n"
                f"  Tahmini Günlük Satış: {tahmini_yeni_satis:.1f} adet\n"
                f"  Tahmini Günlük Kâr: {tahmini_yeni_kar:.2f} TL\n"
                f"{'='*50}\n"
                f"{'BAŞARILI: Kâr artabilir.' if kar_degisimi>0 else 'UYARI: Kâr düşebilir.'} "
                f"(Δ={kar_degisimi:.2f} TL)"
            )

            # Grafik: fiyat aralığında kâr eğrisi
            fiyat_min = maliyet * 1.10
            fiyat_max = max(mevcut_ortalama_fiyat * 2.0, yeni_fiyat * 1.2)
            test_prices = np.linspace(fiyat_min, fiyat_max, 60)
            demand = model.predict(test_prices.reshape(-1, 1))
            demand[demand < 0] = 0
            profits = (test_prices - maliyet) * demand
            chart_data = _as_chartjs_line(test_prices.tolist(), profits.tolist())

            return True, rapor, chart_data

        except Exception as e:
            return False, f"Simülasyon hatası: {e}", None

# ----------------------------------
# Motor 3: Optimum Fiyat (FIX + GUARDRAIL)
# ----------------------------------
def bul_optimum_fiyat(urun_ismi):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

            maliyet = float(urun.hesaplanan_maliyet or 0.0)
            mevcut_fiyat = float(urun.mevcut_satis_fiyati or 0.0)

            if maliyet <= 0:
                return False, f"HATA: '{urun.isim}' ürününün maliyeti 0 TL. Lütfen reçete/hammaddeyi doldurun.", None
            if mevcut_fiyat <= 0:
                return False, f"HATA: '{urun.isim}' ürününün mevcut satış fiyatı 0 TL görünüyor. Ürün fiyatını girin.", None

            # FIX: son 180 gün + 1 TL bucket
            df_g = _get_daily_sales_data(urun.id, price_step=1.0, lookback_days=180)
            if df_g is None or df_g.empty:
                return False, f"HATA: '{urun.isim}' için analiz edecek yeterli veri yok (en az 2 farklı fiyat lazım).", None

            # Modeli günlük ortalama adet üzerinden kur
            X = df_g[['ortalama_fiyat']]
            y = df_g['ortalama_adet']
            model = LinearRegression().fit(X, y)

            # Eğer eğim pozitifse, optimum güvenilmez
            pozitif_egim = float(model.coef_[0]) >= 0

            # Test aralığı: veriye yakın kalsın (uçuk extrapolation yapmasın)
            min_obs = float(df_g['ortalama_fiyat'].min())
            max_obs = float(df_g['ortalama_fiyat'].max())

            min_fiyat = max(maliyet * 1.10, min_obs * 0.90)
            max_fiyat = max_obs * 1.25

            # Eğer mevcut fiyat gözlem aralığının dışındaysa, onu da kapsa
            min_fiyat = min(min_fiyat, mevcut_fiyat * 0.90)
            max_fiyat = max(max_fiyat, mevcut_fiyat * 1.10)

            test_prices = np.linspace(min_fiyat, max_fiyat, 120)

            # Tahmin
            demand = model.predict(test_prices.reshape(-1, 1))
            demand = np.maximum(demand, 0.0)

            profits = (test_prices - maliyet) * demand

            df_res = pd.DataFrame({
                'test_fiyati': test_prices,
                'tahmini_adet': demand,
                'tahmini_kar': profits
            })

            optimum = df_res.loc[df_res['tahmini_kar'].idxmax()]

            # Mevcut fiyatta model kârı (kıyas için)
            mevcut_talep_hat = max(0.0, float(model.predict(np.array([[mevcut_fiyat]]))[0]))
            mevcut_kar_hat = (mevcut_fiyat - maliyet) * mevcut_talep_hat

            # Ayrıca geçmiş gerçek veriden "günlük gerçek kâr" tahmini (daha sağlam baseline)
            # (fiyat bucketlara göre gittiği için, en yakın bucketı kullan)
            nearest_idx = (df_g['ortalama_fiyat'] - mevcut_fiyat).abs().idxmin()
            obs_price = float(df_g.loc[nearest_idx, 'ortalama_fiyat'])
            obs_daily_qty = float(df_g.loc[nearest_idx, 'ortalama_adet'])
            obs_daily_profit = (obs_price - maliyet) * obs_daily_qty

            # Guardrail: optimum kâr, mevcut (gerçek baseline) kârdan düşükse uyar
            rapor_uyari = ""
            if pozitif_egim:
                rapor_uyari += (
                    "⚠️ UYARI: Model eğimi pozitif çıktı (fiyat artınca satış artıyor gibi). "
                    "Bu genelde veri gürültüsü/az veri demektir. Sonuç temkinli yorumlanmalı.\n\n"
                )

            if float(optimum['tahmini_kar']) < obs_daily_profit:
                rapor_uyari += (
                    "⚠️ UYARI: Modelin bulduğu optimum kâr, geçmiş veride mevcut fiyata en yakın noktadaki "
                    "günlük kârdan düşük. Bu genelde indirim/kampanya/fiyat gürültüsü nedeniyle olur.\n"
                    "✅ ÖNERİ: Şimdilik mevcut fiyatı koruyun veya daha kontrollü farklı fiyat denemeleriyle veri toplayın.\n\n"
                )

            rapor = (
                f"{rapor_uyari}"
                f"--- MEVCUT DURUM (Veriden Baseline) ---\n"
                f"  Mevcut Liste Fiyatı: {mevcut_fiyat:.2f} TL\n"
                f"  (Veride en yakın fiyat: {obs_price:.2f} TL)\n"
                f"  Günlük Satış (Veri): {obs_daily_qty:.1f} adet/gün\n"
                f"  Günlük Kâr (Veri): {obs_daily_profit:.2f} TL/gün\n\n"
                f"--- MODEL TAHMİNİ (Mevcut Fiyat) ---\n"
                f"  Tahmini Satış: {mevcut_talep_hat:.1f} adet/gün\n"
                f"  Tahmini Günlük Kâr: {mevcut_kar_hat:.2f} TL/gün\n\n"
                f"--- OPTİMUM FİYAT (Model) ---\n"
                f"  🏆 Önerilen Fiyat: {float(optimum['test_fiyati']):.2f} TL\n"
                f"  Tahmini Satış: {float(optimum['tahmini_adet']):.1f} adet/gün\n"
                f"  Tahmini Maks. Kâr: {float(optimum['tahmini_kar']):.2f} TL/gün"
            )

            chart_data = _generate_price_curve_data_from_results(df_res)
            return True, rapor, chart_data

        except Exception as e:
            return False, f"Optimizasyon hatası: {e}", None

# ---------------------------------------------------------
# Motor 4/5: Kategori / Grup (aynı)
# ---------------------------------------------------------
def _get_sales_by_filter(column_name, value):
    q = (db.session.query(
            SatisKaydi.tarih,
            SatisKaydi.adet,
            SatisKaydi.toplam_tutar,
            Urun.isim,
            Urun.kategori,
            Urun.kategori_grubu,
            Urun.hesaplanan_maliyet
        )
        .join(Urun, Urun.id == SatisKaydi.urun_id))

    if column_name == 'kategori':
        q = q.filter(Urun.kategori == value)
    elif column_name == 'kategori_grubu':
        q = q.filter(Urun.kategori_grubu == value)
    else:
        return None

    rows = q.all()
    if not rows:
        return None

    df = pd.DataFrame(rows, columns=[
        'tarih', 'adet', 'toplam_tutar', 'isim', 'kategori', 'kategori_grubu', 'maliyet'
    ])
    return df

def _hesapla_kategori_ozeti(df, grup_kolonu):
    df = df.copy()
    df['kar'] = df['toplam_tutar'] - (df['maliyet'].fillna(0.0) * df['adet'])
    karlar = df.groupby(grup_kolonu)['kar'].sum().to_dict()
    toplam_kari = float(sum(karlar.values()))
    paylar = {k: (0.0 if toplam_kari == 0 else (v / toplam_kari * 100.0)) for k, v in karlar.items()}
    return {"karlar": karlar, "paylar": paylar, "toplam_kari": toplam_kari}

def analiz_et_kategori_veya_grup(tip, isim, gun_sayisi=7):
    try:
        if tip == 'kategori':
            df = _get_sales_by_filter('kategori', isim)
            grup_kolonu = 'isim'
            baslik = f"KATEGORİ ANALİZİ: {isim}"
        elif tip == 'kategori_grubu':
            df = _get_sales_by_filter('kategori_grubu', isim)
            grup_kolonu = 'kategori'
            baslik = f"KATEGORİ GRUBU ANALİZİ: {isim}"
        else:
            return False, "HATA: Geçersiz analiz tipi.", None

        if df is None or df.empty:
            return False, f"HATA: '{isim}' için satış verisi yok.", None

        df['tarih'] = pd.to_datetime(df['tarih'])
        bugun = datetime.now().date()
        bu_bas = bugun - timedelta(days=int(gun_sayisi))
        onceki_bas = bu_bas - timedelta(days=int(gun_sayisi))

        df_bu = df[df['tarih'] >= pd.to_datetime(bu_bas)]
        df_onceki = df[(df['tarih'] >= pd.to_datetime(onceki_bas)) & (df['tarih'] < pd.to_datetime(bu_bas))]

        if df_bu.empty or df_onceki.empty:
            return False, f"UYARI: Son {gun_sayisi} gün ve önceki {gun_sayisi} gün için yeterli veri yok.", None

        ozet_bu = _hesapla_kategori_ozeti(df_bu, grup_kolonu)
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki, grup_kolonu)

        rapor = f"{baslik}\n(Son {gun_sayisi} gün vs. önceki {gun_sayisi} gün)\n" + "="*60 + "\n\n"
        rapor += f"--- ÖNCEKİ PERİYOT ---\n  📊 TOPLAM KÂR: {ozet_onceki['toplam_kari']:.2f} TL\n"
        for name, pay in ozet_onceki['paylar'].items():
            rapor += f"    - {name:<20}: %{pay:.1f}  ({ozet_onceki['karlar'].get(name, 0):.2f} TL)\n"
        rapor += f"\n--- BU PERİYOT ---\n  📊 TOPLAM KÂR: {ozet_bu['toplam_kari']:.2f} TL\n"
        for name, pay in ozet_bu['paylar'].items():
            rapor += f"    - {name:<20}: %{pay:.1f}  ({ozet_bu['karlar'].get(name, 0):.2f} TL)\n"

        fark = ozet_bu['toplam_kari'] - ozet_onceki['toplam_kari']
        rapor += "\n" + "="*60 + "\n"
        if fark > 0:
            rapor += f"✅ BAŞARILI: Toplam kâr {fark:.2f} TL arttı."
        else:
            rapor += f"❌ DİKKAT: Toplam kâr {abs(fark):.2f} TL azaldı. Yamyamlık etkisini inceleyin."

        labels = sorted(list(set(ozet_onceki['karlar'].keys()) | set(ozet_bu['karlar'].keys())))
        data_onceki = [ozet_onceki['karlar'].get(k, 0.0) for k in labels]
        data_bu = [ozet_bu['karlar'].get(k, 0.0) for k in labels]

        chart_data = _as_chartjs_bar(
            labels,
            data_onceki, f"Önceki {gun_sayisi} Gün Kâr (TL)",
            data_bu, f"Son {gun_sayisi} Gün Kâr (TL)"
        )
        return True, rapor, chart_data

    except Exception as e:
        return False, f"Stratejik analiz hatası: {e}", None
