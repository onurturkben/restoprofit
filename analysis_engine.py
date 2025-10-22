# analysis_engine.py (DÜZELTİLMİŞ VERSİYON 2 - ImportError için)
# Bu dosya, Colab'de yazdığımız TÜM analiz motorlarını içerir.

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
    query = db.session.query(
        SatisKaydi.tarih, 
        SatisKaydi.adet, 
        SatisKaydi.hesaplanan_birim_fiyat
    ).filter_by(urun_id=urun_id)
    
    satislar = query.all()
    
    if not satislar:
        return None

    df_satislar = pd.DataFrame(satislar, columns=['tarih', 'adet', 'hesaplanan_birim_fiyat'])
    df_satislar['tarih'] = pd.to_datetime(df_satislar['tarih'])
    
    df_gunluk = df_satislar.set_index('tarih').resample('D').agg(
        toplam_adet=('adet', 'sum'),
        ortalama_fiyat=('hesaplanan_birim_fiyat', 'mean')
    ).dropna()
    df_gunluk = df_gunluk[df_gunluk['toplam_adet'] > 0]
    
    return df_gunluk

def simule_et_fiyat_degisikligi(urun_ismi, test_edilecek_yeni_fiyat):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        
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
            tahmini_yeni_satis = max(0, tahmini_yeni_satis)
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
                    tahmini_adet = df_gunluk['toplam_adet'].mean()
                
                tahmini_adet = max(0, tahmini_adet)
                tahmini_kar = (fiyat - maliyet) * tahmini_adet
                sonuclar.append({'test_fiyati': fiyat, 'tahmini_adet': tahmini_adet, 'tahmini_kar': tahmini_kar})

            if not sonuclar:
                return False, "HATA: Hiçbir sonuç hesaplanamadı."

            df_sonuclar = pd.DataFrame(sonuclar)
            optimum = df_sonuclar.loc[df_sonuclar['tahmini_kar'].idxmax()]
            
            mevcut_gunluk_satis_df = df_gunluk[df_gunluk['ortalama_fiyat'].round() == round(mevcut_fiyat)]
            if not mevcut_gunluk_satis_df.empty:
                mevcut_gunluk_satis = mevcut_gunluk_satis_df['toplam_adet'].mean()
            else:
                mevcut_gunluk_satis = df_gunluk['toplam_adet'].mean()
                
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


# --- Motor 4 & 5 (Colab Hücre 10 & 11) - BİRLEŞTİRİLDİ ---

def _get_sales_by_filter(field, value):
    """Yardımcı fonksiyon: Kategori veya Gruba göre satışları çeker."""
    if field == 'kategori':
        query = SatisKaydi.query.join(Urun).filter(Urun.kategori == value)
    elif field == 'kategori_grubu':
        query = SatisKaydi.query.join(Urun).filter(Urun.kategori_grubu == value)
    else:
        return None
        
    satislar = query.all()
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

def _hesapla_kategori_ozeti(df_periyot, grup_kolonu):
    """Genel yardımcı fonksiyon (Kategori veya Grup için)"""
    if df_periyot.empty:
        return {'toplam_kari': 0, 'karlar': {}, 'paylar': {}}
    
    toplam_kari = df_periyot['hesaplanan_kar'].sum()
    if toplam_kari <= 0:
        return {'toplam_kari': toplam_kari, 'karlar': {}, 'paylar': {}}

    karlar = df_periyot.groupby(grup_kolonu)['hesaplanan_kar'].sum()
    paylar = (karlar / toplam_kari) * 100
    
    return {
        'toplam_kari': toplam_kari,
        'karlar': karlar.to_dict(),
        'paylar': paylar.to_dict()
    }

# --- HATA BURADAYDI: İki fonksiyonu app.py'nin beklediği tek fonksiyonda birleştirdim ---
def analiz_et_kategori_veya_grup(tip, isim, gun_sayisi=7):
    """
    Hem Kategori (Hücre 10) hem de Kategori Grubu (Hücre 11) analizini
    yapabilen birleşik fonksiyon. (DÜZELTİLMİŞ)
    tip: 'kategori' veya 'kategori_grubu'
    isim: 'Burgerler' veya 'Ana Yemekler'
    """
    try:
        if tip == 'kategori':
            df_satislar = _get_sales_by_filter('kategori', isim)
            grup_kolonu = 'isim' # Kategori analizi, içindeki ÜRÜNLERİN payına bakar
            baslik = f"STRATEJİST ASİSTAN (FAZ 3): '{isim}' KATEGORİ ANALİZİ"
        elif tip == 'kategori_grubu':
            df_satislar = _get_sales_by_filter('kategori_grubu', isim)
            grup_kolonu = 'kategori' # Grup analizi, içindeki KATEGORİLERİN payına bakar
            baslik = f"GENEL STRATEJİST (FAZ 4): '{isim}' GRUP ANALİZİ"
        else:
            return False, "HATA: Geçersiz analiz tipi."

        if df_satislar is None:
            return False, f"HATA: '{isim}' için hiç satış verisi bulunamadı."
        
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

        ozet_bu = _hesapla_kategori_ozeti(df_bu_periyot, grup_kolonu)
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki_periyot, grup_kolonu)

        rapor = f"{baslik}\n(Periyot: Son {gun_sayisi} gün vs Önceki {gun_sayisi} gün)\n"
        rapor += "="*60 + "\n"

        rapor += f"--- ÖNCEKİ PERİYOT (Son {gun_sayisi}-{gun_sayisi*2} Gün) ---\n"
        rapor += f"  📊 TOPLAM KAR: {ozet_onceki['toplam_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Bu grup içinde):\n"
        for item_name, pay in ozet_onceki['paylar'].items():
            rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_onceki['karlar'].get(item_name, 0):.2f} TL)\n"
        
        rapor += f"\n--- BU PERİYOT (Son {gun_sayisi} Gün) ---\n"
        rapor += f"  📊 TOPLAM KAR: {ozet_bu['toplam_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Bu grup içinde):\n"
        for item_name, pay in ozet_bu['paylar'].items():
            rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_bu['karlar'].get(item_name, 0):.2f} TL)\n"
        
        rapor += "\n" + "="*60 + "\n"
        rapor += "  STRATEJİST TAVSİYESİ (Rasyonel Sonuç):\n"
        
        fark = ozet_bu['toplam_kari'] - ozet_onceki['toplam_kari']
        if fark > 0:
            rapor += f"  ✅ BAŞARILI! '{isim}' grubunun/kategorisinin toplam karı {fark:.2f} TL ARTTI."
        else:
            rapor += f"  ❌ DİKKAT! '{isim}' grubunun/kategorisinin toplam karı {abs(fark):.2f} TL AZALDI.\n"
            if tip == 'kategori_grubu':
                rapor += "  Bir kategorinin payı artmış olsa da, daha karlı bir kategoriden 'çapraz yamyamlık' olmuş olabilir.\n"
            else:
                rapor += "  Bir ürünün payı artmış olsa da, 'iç yamyamlık' olmuş olabilir.\n"
            rapor += "  Bu fiyat politikasını GÖZDEN GEÇİRİN."
        
        return True, rapor

    except Exception as e:
        return False, f"Stratejik analiz hatası: {e}"
