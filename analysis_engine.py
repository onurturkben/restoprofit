# analysis_engine.py
# Bu dosya, Colab'de yazdığımız TÜM analiz motorlarını içerir.
# (Hücre 7, 8, 9, 10, 11)
# Veritabanıyla konuşmak için 'database.py' dosyalarındaki modelleri kullanır.

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
from database import db, Urun, SatisKaydi
import warnings

# --- Motor 1 (Colab Hücre 9): Hedef Marj Hesaplayıcı ---

def hesapla_hedef_marj(urun_ismi, hedef_marj_yuzdesi):
    try:
        urun = Urun.query.filter_by(isim=urun_ismi).first()
        if not urun:
            return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı."
        
        maliyet = urun.hesaplanan_maliyet
        if maliyet <= 0:
            return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL veya negatif. Lütfen önce maliyetleri güncelleyin."
        
        if not (0 < hedef_marj_yuzdesi < 100):
            return False, "HATA: Hedef Marj Yüzdesi 0 ile 100 arasında olmalıdır."

        marj_orani = hedef_marj_yuzdesi / 100.0
        gereken_satis_fiyati = maliyet / (1 - marj_orani)
        
        rapor = (
            f"--- HESAPLAMA SONUCU ---\n"
            f"  Ürün Adı: {urun.isim}\n"
            f"  Hesaplanan Güncel Maliyet (COGS): {maliyet:.2f} TL\n"
            f"  İstenen Kar Marjı: %{hedef_marj_yuzdesi:.0f}\n\n"
            f"  🎯 GEREKEN SATIŞ FİYATI: {gereken_satis_fiyati:.2f} TL 🎯"
        )
        return True, rapor
    
    except Exception as e:
        return False, f"Hesaplama hatası: {e}"


# --- Motor 2 (Colab Hücre 7): Fiyat Simülatörü ---

def _get_daily_sales_data(urun_id):
    """Yardımcı fonksiyon: Analiz için günlük satış verisini çeker."""
    satislar = SatisKaydi.query.filter_by(urun_id=urun_id).all()
    if not satislar:
        return None

    df_satislar = pd.DataFrame([(s.tarih, s.adet, s.hesaplanan_birim_fiyat) for s in satislar], 
                               columns=['tarih', 'adet', 'hesaplanan_birim_fiyat'])
    df_satislar['tarih'] = pd.to_datetime(df_satislar['tarih'])
    
    df_gunluk = df_satislar.set_index('tarih').resample('D').agg(
        toplam_adet=('adet', 'sum'),
        ortalama_fiyat=('hesaplanan_birim_fiyat', 'mean')
    ).dropna()
    df_gunluk = df_gunluk[df_gunluk['toplam_adet'] > 0]
    
    return df_gunluk

def simule_et_fiyat_degisikligi(urun_ismi, test_edilecek_yeni_fiyat):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore") # Sklearn uyarılarını gizle
        
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı."
            
            maliyet = urun.hesaplanan_maliyet
            df_gunluk = _get_daily_sales_data(urun.id)
            
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için hiç satış verisi bulunamadı. Simülasyon yapılamaz."

            mevcut_ortalama_fiyat = df_gunluk['ortalama_fiyat'].mean()
            mevcut_gunluk_satis = df_gunluk['toplam_adet'].mean()
            mevcut_gunluk_kar = (mevcut_ortalama_fiyat - maliyet) * mevcut_gunluk_satis

            rapor = (
                f"--- MEVCUT DURUM (Geçmiş Veri Ortalaması) ---\n"
                f"  Ortalama Fiyat: {mevcut_ortalama_fiyat:.2f} TL\n"
                f"  Günlük Satış: {mevcut_gunluk_satis:.1f} adet\n"
                f"  Ürün Maliyeti: {maliyet:.2f} TL\n"
                f"  Tahmini Günlük Kar: {mevcut_gunluk_kar:.2f} TL\n"
                f"{'-'*50}\n"
            )
            
            if df_gunluk['ortalama_fiyat'].nunique() < 2:
                rapor += "UYARI: Ürün hep aynı fiyattan satılmış. Sağlıklı bir talep tahmini yapılamıyor.\nSimülasyon iptal edildi."
                return False, rapor

            X = df_gunluk[['ortalama_fiyat']]
            y = df_gunluk['toplam_adet']
            model = LinearRegression().fit(X, y)
            
            if model.coef_[0] >= 0:
                rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz.\n"

            tahmini_yeni_satis = model.predict(np.array([[test_edilecek_yeni_fiyat]]))[0]
            tahmini_yeni_satis = max(0, tahmini_yeni_satis) # Negatif olamaz
            tahmini_yeni_kar = (test_edilecek_yeni_fiyat - maliyet) * tahmini_yeni_satis
            kar_degisimi = tahmini_yeni_kar - mevcut_gunluk_kar
            
            rapor += (
                f"--- SİMÜLASYON SONUCU ({test_edilecek_yeni_fiyat:.2f} TL) ---\n"
                f"  Tahmini Günlük Satış: {tahmini_yeni_satis:.1f} adet\n"
                f"  Tahmini Günlük Kar: {tahmini_yeni_kar:.2f} TL\n"
                f"{'='*50}\n"
            )
            
            if kar_degisimi > 0:
                rapor += f"  SONUÇ (TAVSİYE): BAŞARILI!\n  Günlük karınızı TAHMİNİ {kar_degisimi:.2f} TL ARTIRABİLİR."
            else:
                rapor += f"  SONUÇ (UYARI): BAŞARISIZ!\n  Günlük karınızı TAHMİNİ {abs(kar_degisimi):.2f} TL AZALTABİLİR."
            
            return True, rapor
        
        except Exception as e:
            return False, f"Simülasyon hatası: {e}"


# --- Motor 3 (Colab Hücre 8): Optimum Fiyat Motoru ---

def bul_optimum_fiyat(urun_ismi, fiyat_deneme_araligi=1.0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı."
            
            maliyet = urun.hesaplanan_maliyet
            mevcut_fiyat = urun.mevcut_satis_fiyati
            if maliyet <= 0:
                return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL. Lütfen önce maliyetleri güncelleyin."
                
            df_gunluk = _get_daily_sales_data(urun.id)
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için hiç satış verisi bulunamadı. Optimizasyon yapılamaz."
            
            model = None
            rapor = ""
            if df_gunluk['ortalama_fiyat'].nunique() < 2:
                rapor += "UYARI: Ürün hep aynı fiyattan satılmış. Talep modeli kurulamaz.\nOptimizasyon, mevcut ortalama satış adedine göre TAHMİNİDİR.\n\n"
            else:
                X = df_gunluk[['ortalama_fiyat']]
                y = df_gunluk['toplam_adet']
                model = LinearRegression().fit(X, y)
                if model.coef_[0] >= 0:
                    rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz.\n"

            min_fiyat = max(maliyet * 1.1, mevcut_fiyat * 0.5) 
            max_fiyat = mevcut_fiyat * 2.0
            test_edilecek_fiyatlar = np.arange(min_fiyat, max_fiyat, fiyat_deneme_araligi)
            
            if test_edilecek_fiyatlar.size == 0:
                return False, f"HATA: Geçerli bir fiyat aralığı bulunamadı. (Min: {min_fiyat}, Max: {max_fiyat})"

            sonuclar = []
            for fiyat in test_edilecek_fiyatlar:
                if model:
                    tahmini_adet = model.predict(np.array([[fiyat]]))[0]
                else:
                    tahmini_adet = df_gunluk['toplam_adet'].mean() # Model yoksa ortalamayı al
                
                tahmini_adet = max(0, tahmini_adet)
                tahmini_kar = (fiyat - maliyet) * tahmini_adet
                sonuclar.append({'test_fiyati': fiyat, 'tahmini_adet': tahmini_adet, 'tahmini_kar': tahmini_kar})

            if not sonuclar:
                return False, "HATA: Hiçbir sonuç hesaplanamadı."

            df_sonuclar = pd.DataFrame(sonuclar)
            optimum = df_sonuclar.loc[df_sonuclar['tahmini_kar'].idxmax()]
            
            mevcut_gunluk_satis = df_gunluk[df_gunluk['ortalama_fiyat'].round() == round(mevcut_fiyat)]['toplam_adet'].mean()
            if pd.isna(mevcut_gunluk_satis) or mevcut_gunluk_satis == 0:
                mevcut_gunluk_satis = df_gunluk['toplam_adet'].mean() # Ortalamayı al
                
            mevcut_kar = (mevcut_fiyat - maliyet) * mevcut_gunluk_satis

            rapor += (
                f"--- MEVCUT DURUM (Menü Fiyatı) ---\n"
                f"  Mevcut Fiyat: {mevcut_fiyat:.2f} TL\n"
                f"  Tahmini Günlük Kar: {mevcut_kar:.2f} TL\n\n"
                f"--- OPTİMUM FİYAT TAVSİYESİ ---\n"
                f"  🏆 MAKSİMUM KAR İÇİN TAVSİYE EDİLEN FİYAT: {optimum['test_fiyati']:.2f} TL 🏆\n\n"
                f"  Bu fiyattan tahmini günlük satış: {optimum['tahmini_adet']:.1f} adet\n"
                f"  Tahmini maksimum günlük kar: {optimum['tahmini_kar']:.2f} TL"
            )
            return True, rapor
            
        except Exception as e:
            return False, f"Optimizasyon hatası: {e}"


# --- Motor 4 (Colab Hücre 10): Kategori Analizi ---

def _get_sales_by_filter(field, value):
    """Yardımcı fonksiyon: Kategori veya Gruba göre satışları çeker."""
    if field == 'kategori':
        satislar = SatisKaydi.query.join(Urun).filter(Urun.kategori == value).all()
    elif field == 'kategori_grubu':
        satislar = SatisKaydi.query.join(Urun).filter(Urun.kategori_grubu == value).all()
    else:
        return None
        
    if not satislar:
        return None

    df_data = []
    for s in satislar:
        df_data.append({
            'tarih': s.tarih,
            'hesaplanan_kar': s.hesaplanan_kar,
            'isim': s.urun.isim,
            'kategori': s.urun.kategori
        })
    return pd.DataFrame(df_data)

def analiz_et_kategori(kategori_ismi, gun_sayisi=7):
    try:
        df_satislar = _get_sales_by_filter('kategori', kategori_ismi)
        if df_satislar is None:
            return False, f"HATA: '{kategori_ismi}' kategorisi için hiç satış verisi bulunamadı."
        
        df_satislar['tarih'] = pd.to_datetime(df_satislar['tarih'])
        
        bugun = datetime.now().date()
        bu_periyot_basi = bugun - timedelta(days=gun_sayisi)
        onceki_periyot_basi = bu_periyot_basi - timedelta(days=gun_sayisi)

        df_bu_periyot = df_satislar[df_satislar['tarih'] >= pd.to_datetime(bu_periyot_basi)]
        df_onceki_periyot = df_satislar[
            (df_satislar['tarih'] >= pd.to_datetime(onceki_periyot_basi)) & 
            (df_satislar['tarih'] < pd.to_datetime(bu_periyot_basi))
        ]

        if df_bu_periyot.empty or df_onceki_periyot.empty:
            return False, f"UYARI: Karşılaştırma için yeterli veri bulunamadı (Son {gun_sayisi} gün ve önceki {gun_sayisi} gün için)."

        ozet_bu = _hesapla_kategori_ozeti(df_bu_periyot, 'isim') # Ürün bazlı pay
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki_periyot, 'isim') # Ürün bazlı pay

        rapor = f"--- ÖNCEKİ PERİYOT (Son {gun_sayisi}-{gun_sayisi*2} Gün) ---\n"
        rapor += f"  📊 TOPLAM KATEGORİ KARI: {ozet_onceki['toplam_kategori_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Bu kategori içinde):\n"
        for urun, pay in ozet_onceki['urun_paylari'].items():
            rapor += f"    - {urun:<20}: %{pay:.1f}  ({ozet_onceki['urun_karlari'].get(urun, 0):.2f} TL)\n"
        
        rapor += f"\n--- BU PERİYOT (Son {gun_sayisi} Gün) ---\n"
        rapor += f"  📊 TOPLAM KATEGORİ KARI: {ozet_bu['toplam_kategori_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Bu kategori içinde):\n"
        for urun, pay in ozet_bu['urun_paylari'].items():
            rapor += f"    - {urun:<20}: %{pay:.1f}  ({ozet_bu['urun_karlari'].get(urun, 0):.2f} TL)\n"
        
        rapor += "\n" + "="*60 + "\n"
        rapor += "  STRATEJİST TAVSİYESİ (Rasyonel Sonuç):\n"
        
        fark = ozet_bu['toplam_kategori_kari'] - ozet_onceki['toplam_kategori_kari']
        if fark > 0:
            rapor += f"  ✅ BAŞARILI! '{kategori_ismi}' kategorisinin toplam karı {fark:.2f} TL ARTTI."
        else:
            rapor += f"  ❌ DİKKAT! '{kategori_ismi}' kategorisinin toplam karı {abs(fark):.2f} TL AZALDI.\n"
            rapor += "  Bir ürünün kar payı artmış olsa da, yamyamlık olmuş olabilir.\n"
            rapor += "  Bu fiyat politikasını GÖZDEN GEÇİRİN."
        
        return True, rapor

    except Exception as e:
        return False, f"Kategori analizi hatası: {e}"

def _hesapla_kategori_ozeti(df_periyot, grup_kolonu):
    """Genel yardımcı fonksiyon (Kategori veya Grup için)"""
    if df_periyot.empty:
        return {'toplam_kategori_kari': 0, 'urun_karlari': {}, 'urun_paylari': {}}
    
    toplam_kategori_kari = df_periyot['hesaplanan_kar'].sum()
    if toplam_kategori_kari <= 0:
        return {'toplam_kategori_kari': toplam_kategori_kari, 'urun_karlari': {}, 'urun_paylari': {}}

    urun_karlari = df_periyot.groupby(grup_kolonu)['hesaplanan_kar'].sum()
    urun_paylari = (urun_karlari / toplam_kategori_kari) * 100
    
    return {
        'toplam_kategori_kari': toplam_kategori_kari,
        'urun_karlari': urun_karlari.to_dict(),
        'urun_paylari': urun_paylari.to_dict()
    }


# --- Motor 5 (Colab Hücre 11): Kategori GRUBU Analizi (En Stratejik) ---

def analiz_et_kategori_grubu(grup_ismi, gun_sayisi=7):
    try:
        df_satislar = _get_sales_by_filter('kategori_grubu', grup_ismi)
        if df_satislar is None:
            return False, f"HATA: '{grup_ismi}' kategori grubu için hiç satış verisi bulunamadı."
        
        df_satislar['tarih'] = pd.to_datetime(df_satislar['tarih'])
        
        bugun = datetime.now().date()
        bu_periyot_basi = bugun - timedelta(days=gun_sayisi)
        onceki_periyot_basi = bu_periyot_basi - timedelta(days=gun_sayisi)

        df_bu_periyot = df_satislar[df_satislar['tarih'] >= pd.to_datetime(bu_periyot_basi)]
        df_onceki_periyot = df_satislar[
            (df_satislar['tarih'] >= pd.to_datetime(onceki_periyot_basi)) & 
            (df_satislar['tarih'] < pd.to_datetime(bu_periyot_basi))
        ]

        if df_bu_periyot.empty or df_onceki_periyot.empty:
            return False, f"UYARI: Karşılaştırma için yeterli veri bulunamadı (Son {gun_sayisi} gün ve önceki {gun_sayisi} gün için)."

        ozet_bu = _hesapla_kategori_ozeti(df_bu_periyot, 'kategori') # Kategori bazlı pay
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki_periyot, 'kategori') # Kategori bazlı pay

        rapor = f"--- ÖNCEKİ PERİYOT (Son {gun_sayisi}-{gun_sayisi*2} Gün) ---\n"
        rapor += f"  📊 TOPLAM GRUP KARI: {ozet_onceki['toplam_kategori_kari']:.2f} TL\n"
        rapor += "  Kategori Kar Payları (Bu grup içinde):\n"
        for kategori, pay in ozet_onceki['urun_paylari'].items():
            rapor += f"    - {kategori:<15}: %{pay:.1f}  ({ozet_onceki['urun_karlari'].get(kategori, 0):.2f} TL)\n"
        
        rapor += f"\n--- BU PERİYOT (Son {gun_sayisi} Gün) ---\n"
        rapor += f"  📊 TOPLAM GRUP KARI: {ozet_bu['toplam_kategori_kari']:.2f} TL\n"
        rapor += "  Kategori Kar Payları (Bu grup içinde):\n"
        for kategori, pay in ozet_bu['urun_paylari'].items():
            rapor += f"    - {kategori:<15}: %{pay:.1f}  ({ozet_bu['urun_karlari'].get(kategori, 0):.2f} TL)\n"
        
        rapor += "\n" + "="*60 + "\n"
        rapor += "  GENEL STRATEJİST TAVSİYESİ (Rasyonel Sonuç):\n"
        
        fark = ozet_bu['toplam_kategori_kari'] - ozet_onceki['toplam_kategori_kari']
        if fark > 0:
            rapor += f"  ✅ BAŞARILI! '{grup_ismi}' grubunun toplam karı {fark:.2f} TL ARTTI."
        else:
            rapor += f"  ❌ DİKKAT! '{grup_ismi}' grubunun toplam karı {abs(fark):.2f} TL AZALDI.\n"
            rapor += "  Bir kategorinin payı artmış olsa da, daha karlı bir kategoriden 'çapraz yamyamlık' olmuş olabilir.\n"
            rapor += "  Bu genel fiyat stratejisini GÖZDEN GEÇİRİN."
        
        return True, rapor

    except Exception as e:
        return False, f"Kategori Grubu analizi hatası: {e}"
