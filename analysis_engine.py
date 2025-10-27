# analysis_engine.py
import json
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from database import db, Urun, SatisKaydi

# ---------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------

def _get_daily_sales_data(urun_id: int):
    """
    Fiyat -> talep ilişkisi için gerekli özet tabloyu döndürür.
    Dönüş: DataFrame(columns = ['ortalama_fiyat','toplam_adet','gun_sayisi','ortalama_adet'])
    En az 2 farklı fiyat noktası yoksa None.
    """
    query = (
        db.session.query(
            SatisKaydi.tarih,
            SatisKaydi.adet,
            SatisKaydi.hesaplanan_birim_fiyat,
        )
        .filter_by(urun_id=urun_id)
    )
    satislar = query.all()
    if not satislar or len(satislar) < 2:
        return None

    df = pd.DataFrame(satislar, columns=["tarih", "adet", "hesaplanan_birim_fiyat"])
    df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce")
    df = df.dropna(subset=["tarih"])

    grp = (
        df.groupby("hesaplanan_birim_fiyat")
        .agg(toplam_adet=("adet", "sum"), gun_sayisi=("tarih", "nunique"))
        .reset_index()
    )
    if grp.empty:
        return None

    grp["ortalama_adet"] = grp["toplam_adet"] / grp["gun_sayisi"].replace(0, np.nan)
    grp = grp.dropna(subset=["ortalama_adet"])
    # Model beklediği kolon adı:
    grp["ortalama_fiyat"] = grp["hesaplanan_birim_fiyat"]

    # En az 2 farklı fiyat noktası gerekli
    if grp["ortalama_fiyat"].nunique() < 2:
        return None

    return grp


def _generate_price_curve_data(model: LinearRegression, maliyet: float, mevcut_fiyat: float, simule_fiyat: float | None = None) -> str:
    """
    Modelden fiyat-kar eğrisi üretir. Chart.js ile uyumlu JSON string döner.
    """
    # Fiyat aralığı: maliyetin %10 üstü ile mevcut fiyatın 2 katı arası
    fiyat_min = max(maliyet * 1.10, 0.01)
    fiyat_max = max(mevcut_fiyat * 2.0, fiyat_min * 1.2)
    if simule_fiyat:
        fiyat_max = max(fiyat_max, simule_fiyat * 1.2)

    price_points = np.linspace(fiyat_min, fiyat_max, 50)
    y_pred = model.predict(price_points.reshape(-1, 1))
    y_pred = np.clip(y_pred, 0, None)  # negatif satış yok

    profit_points = (price_points - maliyet) * y_pred

    chart_data = {
        "labels": [round(float(p), 2) for p in price_points],
        "datasets": [
            {
                "label": "Tahmini Toplam Kâr (TL)",
                "data": [round(float(p), 2) for p in profit_points],
                "borderColor": "#0d6efd",
                "backgroundColor": "rgba(13,110,253,0.2)",
                "fill": True,
                "tension": 0.1,
            }
        ],
    }
    return json.dumps(chart_data)


def _get_sales_by_filter(col_adi: str, deger: str) -> pd.DataFrame | None:
    """
    Kategori/kategori_grubu bazlı satış verisi (ürün + satış join).
    Dönüş: DataFrame(columns=['tarih','isim','kategori','kategori_grubu','adet','toplam_tutar','hesaplanan_maliyet','hesaplanan_kar'])
    """
    # SQL join ile çek
    q = (
        db.session.query(
            SatisKaydi.tarih,
            Urun.isim,
            Urun.kategori,
            Urun.kategori_grubu,
            SatisKaydi.adet,
            SatisKaydi.toplam_tutar,
            SatisKaydi.hesaplanan_maliyet,
            SatisKaydi.hesaplanan_kar,
        )
        .join(Urun, Urun.id == SatisKaydi.urun_id)
    )

    if col_adi == "kategori":
        q = q.filter(Urun.kategori == deger)
    elif col_adi == "kategori_grubu":
        q = q.filter(Urun.kategori_grubu == deger)
    else:
        return None

    rows = q.all()
    if not rows:
        return None

    df = pd.DataFrame(
        rows,
        columns=[
            "tarih",
            "isim",
            "kategori",
            "kategori_grubu",
            "adet",
            "toplam_tutar",
            "hesaplanan_maliyet",
            "hesaplanan_kar",
        ],
    )
    df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce")
    df = df.dropna(subset=["tarih"])
    return df


def _hesapla_kategori_ozeti(df: pd.DataFrame, grup_kolonu: str):
    """
    Bir periyot için grup bazlı (ürün veya kategori) toplam kâr ve payları.
    Dönüş: {"toplam_kari": float, "karlar": {label: float}, "paylar": {label: yüzde}}
    """
    if df.empty:
        return {"toplam_kari": 0.0, "karlar": {}, "paylar": {}}

    agg = (
        df.groupby(grup_kolonu)
        .agg(toplam_kar=("hesaplanan_kar", "sum"))
        .sort_values("toplam_kar", ascending=False)
    )
    toplam = float(agg["toplam_kar"].sum()) if not agg.empty else 0.0
    karlar = {str(ix): float(v) for ix, v in agg["toplam_kar"].items()}
    if toplam > 0:
        paylar = {k: (v / toplam) * 100.0 for k, v in karlar.items()}
    else:
        paylar = {k: 0.0 for k in karlar.keys()}

    return {"toplam_kari": toplam, "karlar": karlar, "paylar": paylar}


# ---------------------------------------------------------------------
# Motor 1: Hedef Marj
# ---------------------------------------------------------------------
def hesapla_hedef_marj(urun_ismi: str, hedef_marj_yuzdesi: float):
    try:
        urun = Urun.query.filter_by(isim=urun_ismi).first()
        if not urun:
            return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

        maliyet = float(urun.hesaplanan_maliyet or 0.0)
        if maliyet <= 0:
            return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL veya negatif. Lütfen önce maliyetleri güncelleyin.", None

        if not (0 < hedef_marj_yuzdesi < 100):
            return False, "HATA: Hedef Marj Yüzdesi 0 ile 100 arasında olmalıdır.", None

        marj_orani = hedef_marj_yuzdesi / 100.0
        gereken_satis_fiyati = maliyet / (1.0 - marj_orani)

        rapor = (
            f"--- HESAPLAMA SONUCU ---\n"
            f"  Ürün Adı: {urun.isim}\n"
            f"  Hesaplanan Güncel Maliyet (COGS): {maliyet:.2f} TL\n"
            f"  İstenen Kar Marjı: %{hedef_marj_yuzdesi:.0f}\n\n"
            f"  🎯 GEREKEN SATIŞ FİYATI: {gereken_satis_fiyati:.2f} TL 🎯"
        )
        return True, rapor, None
    except Exception as e:
        return False, f"Hesaplama hatası: {e}", None


# ---------------------------------------------------------------------
# Motor 2: Fiyat Simülasyonu
# ---------------------------------------------------------------------
def simule_et_fiyat_degisikligi(urun_ismi: str, test_edilecek_yeni_fiyat: float):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

            maliyet = float(urun.hesaplanan_maliyet or 0.0)
            df_gunluk = _get_daily_sales_data(urun.id)
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için en az 2 farklı fiyatta satış verisi bulunamadı. Simülasyon yapılamaz.", None

            # Mevcut durum (ortalama yaklaşımı)
            mevcut_ortalama_fiyat = float(df_gunluk["ortalama_fiyat"].mean())
            mevcut_gunluk_satis = float(df_gunluk["toplam_adet"].mean())
            mevcut_gunluk_kar = (mevcut_ortalama_fiyat - maliyet) * mevcut_gunluk_satis

            rapor = (
                f"--- MEVCUT DURUM (Geçmiş Veri Ortalaması) ---\n"
                f"  Ortalama Fiyat: {mevcut_ortalama_fiyat:.2f} TL\n"
                f"  Günlük Satış: {mevcut_gunluk_satis:.1f} adet\n"
                f"  Ürün Maliyeti: {maliyet:.2f} TL\n"
                f"  Tahmini Günlük Kar: {mevcut_gunluk_kar:.2f} TL\n"
                f"{'-'*50}\n"
            )

            X = df_gunluk[["ortalama_fiyat"]].to_numpy()
            y = df_gunluk["toplam_adet"].to_numpy()
            model = LinearRegression().fit(X, y)

            # Temel tutarlılık kontrolü: fiyat↑, talep↓ beklenir (negatif eğim)
            if model.coef_[0] >= 0:
                rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz veya anormal.\n"
                return False, rapor, None

            tahmini_yeni_satis = float(model.predict(np.array([[test_edilecek_yeni_fiyat]]))[0])
            tahmini_yeni_satis = max(0.0, tahmini_yeni_satis)
            tahmini_yeni_kar = (test_edilecek_yeni_fiyat - maliyet) * tahmini_yeni_satis
            kar_degisimi = tahmini_yeni_kar - mevcut_gunluk_kar

            rapor += (
                f"--- SİMÜLASYON SONUCU ({test_edilecek_yeni_fiyat:.2f} TL) ---\n"
                f"  Tahmini Günlük Satış: {tahmini_yeni_satis:.1f} adet\n"
                f"  Tahmini Günlük Kar: {tahmini_yeni_kar:.2f} TL\n"
                f"{'='*50}\n"
            )
            if kar_degisimi > 0:
                rapor += f"  SONUÇ (TAVSİYE): BAŞARILI!\n  Günlük karınızı TAHMİNİ {kar_degisimi:.2f} TL artırabilir."
            else:
                rapor += f"  SONUÇ (UYARI): BAŞARISIZ!\n  Günlük karınızı TAHMİNİ {abs(kar_degisimi):.2f} TL azaltabilir."

            chart_data = _generate_price_curve_data(model, maliyet, mevcut_ortalama_fiyat, test_edilecek_yeni_fiyat)
            return True, rapor, chart_data
        except Exception as e:
            return False, f"Simülasyon hatası: {e}", None


# ---------------------------------------------------------------------
# Motor 3: Optimum Fiyat
# ---------------------------------------------------------------------
def bul_optimum_fiyat(urun_ismi: str, fiyat_deneme_araligi: float = 1.0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None

            maliyet = float(urun.hesaplanan_maliyet or 0.0)
            mevcut_fiyat = float(urun.mevcut_satis_fiyati or 0.0)
            if maliyet <= 0:
                return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL. Lütfen önce reçete ve hammadde fiyatlarını girin.", None

            df_gunluk = _get_daily_sales_data(urun.id)
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için analiz edilecek yeterli satış verisi bulunamadı.", None

            rapor = ""
            model = None
            if df_gunluk["ortalama_fiyat"].nunique() < 2:
                rapor += "UYARI: Ürün hep aynı fiyattan satılmış. Talep modeli kurulamaz.\nOptimizasyon mevcut ortalama adedi baz alır (yaklaşık).\n\n"
            else:
                X = df_gunluk[["ortalama_fiyat"]].to_numpy()
                y = df_gunluk["toplam_adet"].to_numpy()
                model = LinearRegression().fit(X, y)
                if model.coef_[0] >= 0:
                    rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz/anormal olabilir.\n"

            # Denenecek fiyat aralığı
            min_fiyat = max(maliyet * 1.10, df_gunluk["ortalama_fiyat"].min() * 0.8, 0.01)
            max_fiyat = max(df_gunluk["ortalama_fiyat"].max() * 1.5, min_fiyat * 1.2)
            test_prices = np.linspace(min_fiyat, max_fiyat, 120)

            sonuclar = []
            ort_adet = float(df_gunluk["toplam_adet"].mean())
            for fiyat in test_prices:
                if model is not None:
                    tahmini_adet = float(model.predict(np.array([[fiyat]]))[0])
                else:
                    tahmini_adet = ort_adet  # model yoksa kaba yaklaşım
                tahmini_adet = max(0.0, tahmini_adet)
                tahmini_kar = (fiyat - maliyet) * tahmini_adet
                sonuclar.append((fiyat, tahmini_adet, tahmini_kar))

            if not sonuclar:
                return False, "HATA: Hiçbir sonuç hesaplanamadı.", None

            df_son = pd.DataFrame(sonuclar, columns=["test_fiyati", "tahmini_adet", "tahmini_kar"])
            idx = int(df_son["tahmini_kar"].idxmax())
            optimum = df_son.loc[idx]

            # Mevcut kar (kaba yaklaşım: en yüksek fiyattaki toplam_adet’i kullanmak hatalıydı → ortalama kullan)
            mevcut_gunluk_satis = float(df_gunluk["toplam_adet"].mean())
            mevcut_kar = (mevcut_fiyat - maliyet) * mevcut_gunluk_satis

            rapor += (
                f"--- MEVCUT DURUM (Menü Fiyatı) ---\n"
                f"  Mevcut Fiyat: {mevcut_fiyat:.2f} TL\n"
                f"  Ortalama Günlük Kar (yaklaşık): {mevcut_kar:.2f} TL\n\n"
                f"--- OPTİMUM FİYAT TAVSİYESİ ---\n"
                f"  🏆 MAKSİMUM KÂR İÇİN TAVSİYE EDİLEN FİYAT: {optimum['test_fiyati']:.2f} TL 🏆\n\n"
                f"  Bu fiyattan tahmini günlük satış: {optimum['tahmini_adet']:.1f} adet\n"
                f"  Tahmini maksimum günlük kâr: {optimum['tahmini_kar']:.2f} TL"
            )

            chart_data = {
                "labels": [round(float(p), 2) for p in df_son["test_fiyati"]],
                "datasets": [
                    {
                        "label": "Tahmini Toplam Kâr (TL)",
                        "data": [round(float(p), 2) for p in df_son["tahmini_kar"]],
                        "borderColor": "#0d6efd",
                        "backgroundColor": "rgba(13,110,253,0.2)",
                        "fill": True,
                        "tension": 0.1,
                    }
                ],
            }
            return True, rapor, json.dumps(chart_data)
        except Exception as e:
            return False, f"Optimizasyon hatası: {e}", None


# ---------------------------------------------------------------------
# Motor 4/5: Kategori & Grup Analizi
# ---------------------------------------------------------------------
def analiz_et_kategori_veya_grup(tip: str, isim: str, gun_sayisi: int = 7):
    try:
        if tip == "kategori":
            df = _get_sales_by_filter("kategori", isim)
            grup_kolonu = "isim"  # kategori içi ürünler
            baslik = f"KATEGORİ ANALİZİ: '{isim}'"
        elif tip == "kategori_grubu":
            df = _get_sales_by_filter("kategori_grubu", isim)
            grup_kolonu = "kategori"  # grup içi kategoriler
            baslik = f"KATEGORİ GRUBU ANALİZİ: '{isim}'"
        else:
            return False, "HATA: Geçersiz analiz tipi.", None

        if df is None or df.empty:
            return False, f"HATA: '{isim}' için hiç satış verisi bulunamadı.", None

        df["tarih"] = pd.to_datetime(df["tarih"], errors="coerce")
        df = df.dropna(subset=["tarih"])

        bugun = datetime.now().date()
        bu_periyot_basi = bugun - timedelta(days=gun_sayisi)
        onceki_periyot_basi = bu_periyot_basi - timedelta(days=gun_sayisi)

        df_bu = df[df["tarih"] >= pd.to_datetime(bu_periyot_basi)]
        df_onceki = df[(df["tarih"] >= pd.to_datetime(onceki_periyot_basi)) & (df["tarih"] < pd.to_datetime(bu_periyot_basi))]

        if df_bu.empty or df_onceki.empty:
            return False, f"UYARI: Karşılaştırma için yeterli veri yok. (Son {gun_sayisi} gün ve önceki {gun_sayisi} gün ayrı ayrı gerek.)", None

        ozet_bu = _hesapla_kategori_ozeti(df_bu, grup_kolonu)
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki, grup_kolonu)

        rapor = f"{baslik}\n(Son {gun_sayisi} gün vs önceki {gun_sayisi} gün)\n" + "=" * 60 + "\n\n"

        rapor += f"--- ÖNCEKİ PERİYOT ({onceki_periyot_basi} - {bu_periyot_basi}) ---\n"
        rapor += f"  📊 TOPLAM KÂR: {ozet_onceki['toplam_kari']:.2f} TL\n  Kar Payları:\n"
        if not ozet_onceki["paylar"]:
            rapor += "    - Veri yok.\n"
        else:
            for item_name, pay in ozet_onceki["paylar"].items():
                rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_onceki['karlar'].get(item_name, 0):.2f} TL)\n"

        rapor += f"\n--- BU PERİYOT (Son {gun_sayisi} Gün) ---\n"
        rapor += f"  📊 TOPLAM KÂR: {ozet_bu['toplam_kari']:.2f} TL\n  Kar Payları:\n"
        if not ozet_bu["paylar"]:
            rapor += "    - Veri yok.\n"
        else:
            for item_name, pay in ozet_bu["paylar"].items():
                rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_bu['karlar'].get(item_name, 0):.2f} TL)\n"

        rapor += "\n" + "=" * 60 + "\n  STRATEJİST TAVSİYESİ:\n"
        fark = ozet_bu["toplam_kari"] - ozet_onceki["toplam_kari"]
        if fark > 0:
            rapor += f"  ✅ BAŞARILI! '{isim}' toplam kârı {fark:.2f} TL arttı."
        else:
            rapor += f"  ❌ DİKKAT! '{isim}' toplam kârı {abs(fark):.2f} TL azaldı.\n  'Yamyamlık' etkisini (cannibalization) kontrol edin.\n"

        # Chart.js bar için
        labels = sorted(list(set(ozet_onceki["karlar"].keys()) | set(ozet_bu["karlar"].keys())))
        data_onceki = [ozet_onceki["karlar"].get(l, 0.0) for l in labels]
        data_bu = [ozet_bu["karlar"].get(l, 0.0) for l in labels]

        chart_data = {
            "labels": labels,
            "datasets": [
                {
                    "label": f"Önceki {gun_sayisi} Gün Kâr (TL)",
                    "data": data_onceki,
                    "backgroundColor": "rgba(255,99,132,0.5)",
                    "borderColor": "rgb(255,99,132)",
                    "borderWidth": 1,
                },
                {
                    "label": f"Son {gun_sayisi} Gün Kâr (TL)",
                    "data": data_bu,
                    "backgroundColor": "rgba(54,162,235,0.5)",
                    "borderColor": "rgb(54,162,235)",
                    "borderWidth": 1,
                },
            ],
        }
        return True, rapor, json.dumps(chart_data)
    except Exception as e:
        return False, f"Stratejik analiz hatası: {e}", None
