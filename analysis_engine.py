# analysis_engine.py — sağlamlaştırılmış sürüm
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
        "labels": [round(x, 2) if isinstance(x, (int, float)) else x for x in labels],
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

# ----------------------------------
# Motor 1: Hedef Marj Hesaplayıcı
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

# ---------------------------------------------------
# Ortak veri çıkarımı: fiyat–satış ilişkisi tablosu
# ---------------------------------------------------
def _get_daily_sales_data(urun_id):
    """
    Girdi: urun_id
    Çıktı: kolonlar -> ['ortalama_fiyat', 'toplam_adet', 'gun_sayisi', 'ortalama_adet']
    En az 2 farklı fiyat noktası yoksa None döner.
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
    df['tarih'] = pd.to_datetime(df['tarih'])

    grp = df.groupby('hesaplanan_birim_fiyat').agg(
        toplam_adet=('adet', 'sum'),
        gun_sayisi=('tarih', 'nunique')
    ).reset_index()

    grp['ortalama_adet'] = grp['toplam_adet'] / grp['gun_sayisi']
    # Kritik: analizde hep 'ortalama_fiyat' ismini kullanacağız
    grp['ortalama_fiyat'] = grp['hesaplanan_birim_fiyat']

    # En az iki farklı fiyat noktası şart
    if grp['ortalama_fiyat'].nunique() < 2:
        return None
    return grp

# -----------------------------------------
# Yardımcı: çizim için fiyat eğrisi üret
# -----------------------------------------
def _generate_price_curve_data(model, maliyet, referans_fiyat, simule_fiyat=None):
    fiyat_min = float(maliyet) * 1.10
    fiyat_max = float(referans_fiyat) * 2.0
    if simule_fiyat is not None:
        fiyat_max = max(fiyat_max, float(simule_fiyat) * 1.2)

    price_points = np.linspace(fiyat_min, fiyat_max, 20)
    demand = model.predict(price_points.reshape(-1, 1))
    demand[demand < 0] = 0
    profits = (price_points - float(maliyet)) * demand
    return _as_chartjs_line(price_points, profits)

# ----------------------------------
# Motor 2: Fiyat Simülatörü
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

            df_g = _get_daily_sales_data(urun.id)
            if df_g is None or df_g.empty:
                return False, f"HATA: '{urun.isim}' için en az 2 farklı fiyatta satış verisi bulunamadı.", None

            # Mevcut durum (ortalama)
            mevcut_ortalama_fiyat = float(df_g['ortalama_fiyat'].mean())
            mevcut_gunluk_satis = float(df_g['toplam_adet'].mean())
            mevcut_gunluk_kar = (mevcut_ortalama_fiyat - maliyet) * mevcut_gunluk_satis

            X = df_g[['ortalama_fiyat']]
            y = df_g['toplam_adet']
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
                f"  Günlük Satış: {mevcut_gunluk_satis:.1f} adet\n"
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

            chart_data = _generate_price_curve_data(model, maliyet, mevcut_ortalama_fiyat, yeni_fiyat)
            return True, rapor, chart_data

        except Exception as e:
            return False, f"Simülasyon hatası: {e}", None

# ----------------------------------
# Motor 3: Optimum Fiyat
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

            df_g = _get_daily_sales_data(urun.id)
            if df_g is None or df_g.empty:
                return False, f"HATA: '{urun.isim}' için analiz edecek yeterli veri yok.", None

            rapor = ""
            model = None
            if df_g['ortalama_fiyat'].nunique() < 2:
                rapor += "UYARI: Ürün hep aynı fiyata satılmış. Talep modeli kurulamaz; sabit talep varsayılacak.\n\n"
            else:
                X = df_g[['ortalama_fiyat']]
                y = df_g['toplam_adet']
                model = LinearRegression().fit(X, y)
                if float(model.coef_[0]) >= 0:
                    rapor += "UYARI: Model eğimi pozitif. Veri yetersiz/hatalı olabilir.\n"

            # Aralık
            min_fiyat = max(maliyet * 1.10, float(df_g['ortalama_fiyat'].min()) * 0.8)
            max_fiyat = float(df_g['ortalama_fiyat'].max()) * 1.5
            test_prices = np.linspace(min_fiyat, max_fiyat, 100)

            results = []
            for p in test_prices:
                if model is not None:
                    tahmini_adet = float(model.predict(np.array([[p]]))[0])
                else:
                    tahmini_adet = float(df_g['toplam_adet'].mean())
                tahmini_adet = max(0.0, tahmini_adet)
                tahmini_kar = (p - maliyet) * tahmini_adet
                results.append((p, tahmini_adet, tahmini_kar))

            if not results:
                return False, "HATA: Hiçbir sonuç hesaplanamadı.", None

            df_res = pd.DataFrame(results, columns=['test_fiyati', 'tahmini_adet', 'tahmini_kar'])
            optimum = df_res.loc[df_res['tahmini_kar'].idxmax()]

            # Mevcut kâr: mevcut fiyata en yakın gözlem yerine, ortalama düzey kullanmak daha istikrarlı
            mevcut_gunluk_satis = float(df_g['toplam_adet'].mean())
            mevcut_kar = (mevcut_fiyat - maliyet) * mevcut_gunluk_satis

            rapor += (
                f"--- MEVCUT DURUM ---\n"
                f"  Mevcut Fiyat: {mevcut_fiyat:.2f} TL\n"
                f"  Ortalama Günlük Kâr: {mevcut_kar:.2f} TL\n\n"
                f"--- OPTİMUM FİYAT ---\n"
                f"  🏆 Önerilen Fiyat: {optimum['test_fiyati']:.2f} TL\n"
                f"  Tahmini Satış: {optimum['tahmini_adet']:.1f} adet/gün\n"
                f"  Tahmini Maks. Kâr: {optimum['tahmini_kar']:.2f} TL/gün"
            )

            chart_data = _as_chartjs_line(df_res['test_fiyati'].tolist(),
                                          df_res['tahmini_kar'].tolist())
            return True, rapor, chart_data
        except Exception as e:
            return False, f"Optimizasyon hatası: {e}", None

# ---------------------------------------------------------
# Motor 4/5: Kategori / Grup (yamyamlık) karşılaştırmaları
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
            grup_kolonu = 'isim'            # kategori içi ürünler
            baslik = f"KATEGORİ ANALİZİ: {isim}"
        elif tip == 'kategori_grubu':
            df = _get_sales_by_filter('kategori_grubu', isim)
            grup_kolonu = 'kategori'        # grup içi kategoriler
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
