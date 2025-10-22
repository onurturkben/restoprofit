# analysis_engine.py (FAZ 5, AŞAMA 6: Grafik Verisi Eklendi)
import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from datetime import datetime, timedelta
from database import db, Urun, SatisKaydi
import warnings
import json # JSON formatında veri döndürmek için eklendi

# --- Motor 1: Hedef Marj Hesaplayıcı ---
def hesapla_hedef_marj(urun_ismi, hedef_marj_yuzdesi):
    try:
        urun = Urun.query.filter_by(isim=urun_ismi).first()
        if not urun:
            return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None
        
        maliyet = urun.hesaplanan_maliyet
        if maliyet <= 0:
            return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL veya negatif. Lütfen önce maliyetleri güncelleyin.", None
        
        if not (0 < hedef_marj_yuzdesi < 100):
            return False, "HATA: Hedef Marj Yüzdesi 0 ile 100 arasında bir sayı olmalıdır.", None

        marj_orani = hedef_marj_yuzdesi / 100.0
        gereken_satis_fiyati = maliyet / (1 - marj_orani)
        
        rapor = (
            f"--- HESAPLAMA SONUCU ---\n"
            f"  Ürün Adı: {urun.isim}\n"
            f"  Hesaplanan Güncel Maliyet (COGS): {maliyet:.2f} TL\n"
            f"  İstenen Kar Marjı: %{hedef_marj_yuzdesi:.0f}\n\n"
            f"  🎯 GEREKEN SATIŞ FİYATI: {gereken_satis_fiyati:.2f} TL 🎯"
        )
        # Bu analiz grafik döndürmez
        return True, rapor, None
    
    except Exception as e:
        return False, f"Hesaplama hatası: {e}", None


# --- Motor 2: Fiyat Simülatörü ---
def _get_daily_sales_data(urun_id):
    """Yardımcı fonksiyon: Analiz için günlük satış verisini çeker."""
    query = db.session.query(
        SatisKaydi.tarih, 
        SatisKaydi.adet, 
        SatisKaydi.hesaplanan_birim_fiyat
    ).filter_by(urun_id=urun_id)
    
    satislar = query.all()
    
    if not satislar or len(satislar) < 2: # Model için en az 2 veri noktası gerekir
        return None

    df_satislar = pd.DataFrame(satislar, columns=['tarih', 'adet', 'hesaplanan_birim_fiyat'])
    df_satislar['tarih'] = pd.to_datetime(df_satislar['tarih'])
    
    # Verileri fiyat bazında gruplayarak ortalama adedi alalım
    df_grouped = df_satislar.groupby('hesaplanan_birim_fiyat').agg(
        toplam_adet=('adet', 'sum'),
        gun_sayisi=('tarih', 'nunique')
    ).reset_index()
    
    df_grouped['ortalama_adet'] = df_grouped['toplam_adet'] / df_grouped['gun_sayisi']
    
    # En az 2 farklı fiyat noktasına ihtiyacımız var
    if len(df_grouped) < 2:
        return None
        
    return df_grouped

def simule_et_fiyat_degisikligi(urun_ismi, test_edilecek_yeni_fiyat):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None
            
            maliyet = urun.hesaplanan_maliyet
            df_gunluk = _get_daily_sales_data(urun.id)
            
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için en az 2 farklı fiyatta satış verisi bulunamadı. Simülasyon yapılamaz.", None

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

            X = df_gunluk[['ortalama_fiyat']]
            y = df_gunluk['toplam_adet']
            model = LinearRegression().fit(X, y)
            
            if model.coef_[0] >= 0:
                rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz veya hatalı.\n"
                return False, rapor, None

            tahmini_yeni_satis = model.predict(np.array([[test_edilecek_yeni_fiyat]]))[0]
            tahmini_yeni_satis = max(0, tahmini_yeni_satis) # Negatif satış olamaz
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
            
            # Bu analiz için de grafik verisi döndürebiliriz (Optimum Fiyat gibi)
            chart_data = _generate_price_curve_data(model, maliyet, mevcut_ortalama_fiyat, test_edilecek_yeni_fiyat)
            return True, rapor, chart_data
        
        except Exception as e:
            return False, f"Simülasyon hatası: {e}", None


# --- Motor 3 (Colab Hücre 8): Optimum Fiyat Motoru ---

def _generate_price_curve_data(model, maliyet, mevcut_fiyat, simule_fiyat=None):
    """Optimizasyon ve simülasyon için grafik verisi hazırlar."""
    # Fiyat aralığını belirle
    fiyat_min = maliyet * 1.1  # Maliyetin %10 üzeri
    fiyat_max = mevcut_fiyat * 2 # Mevcut fiyatın 2 katı
    if simule_fiyat:
        fiyat_max = max(fiyat_max, simule_fiyat * 1.2) # Simülasyon fiyatını da içersin
        
    # Test edilecek fiyat noktaları (20 nokta)
    price_points = np.linspace(fiyat_min, fiyat_max, 20)
    
    # Modeli kullanarak tahmin yap
    predicted_demand = model.predict(price_points.reshape(-1, 1))
    
    # Negatif adetleri sıfırla
    predicted_demand[predicted_demand < 0] = 0
    
    # Kar hesapla
    profit_points = (price_points - maliyet) * predicted_demand
    
    # Chart.js için veriyi formatla
    chart_data = {
        'labels': [round(p, 2) for p in price_points],
        'datasets': [{
            'label': 'Tahmini Toplam Kâr (TL)',
            'data': [round(p, 2) for p in profit_points],
            'borderColor': '#0d6efd',
            'backgroundColor': 'rgba(13, 110, 253, 0.2)',
            'fill': True,
            'tension': 0.1
        }]
    }
    return json.dumps(chart_data)

def bul_optimum_fiyat(urun_ismi, fiyat_deneme_araligi=1.0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        
        try:
            urun = Urun.query.filter_by(isim=urun_ismi).first()
            if not urun:
                return False, f"HATA: '{urun_ismi}' adında bir ürün bulunamadı.", None
            
            maliyet = urun.hesaplanan_maliyet
            mevcut_fiyat = urun.mevcut_satis_fiyati
            if maliyet <= 0:
                return False, f"HATA: '{urun_ismi}' ürününün maliyeti 0 TL. Lütfen önce reçete ve hammadde fiyatlarını girin.", None
                
            df_gunluk = _get_daily_sales_data(urun.id)
            if df_gunluk is None or df_gunluk.empty:
                return False, f"HATA: '{urun_ismi}' için analiz edilecek yeterli satış verisi bulunamadı.", None
            
            model = None
            rapor = ""
            
            if df_gunluk['ortalama_fiyat'].nunique() < 2:
                rapor += "UYARI: Ürün hep aynı fiyattan satılmış. Talep modeli kurulamaz.\nOptimizasyon, mevcut ortalama satış adedine göre TAHMİNİDİR.\n\n"
                model = None # Modeli devredışı bırak
            else:
                X = df_gunluk[['ortalama_fiyat']]
                y = df_gunluk['toplam_adet']
                model = LinearRegression().fit(X, y)
                if model.coef_[0] >= 0:
                    rapor += "UYARI: Model, fiyat arttıkça satışların ARTTIĞINI söylüyor! Veri yetersiz veya hatalı.\n"

            # Fiyat aralığını belirle
            min_fiyat = max(maliyet * 1.1, df_gunluk['ortalama_fiyat'].min() * 0.8) # Maliyetin %10 fazlası veya en düşük satış fiyatının %80'i
            max_fiyat = df_gunluk['ortalama_fiyat'].max() * 1.5 # En yüksek satış fiyatının 1.5 katı
            
            # Fiyat aralığını 100 adıma böl
            test_prices = np.linspace(min_fiyat, max_fiyat, 100)
            
            sonuclar = []
            for fiyat in test_prices:
                if model:
                    tahmini_adet = model.predict(np.array([[fiyat]]))[0]
                else:
                    tahmini_adet = df_gunluk['toplam_adet'].mean() # Model yoksa, talebi sabit varsay
                
                tahmini_adet = max(0, tahmini_adet) # Negatif satış olamaz
                tahmini_kar = (fiyat - maliyet) * tahmini_adet
                sonuclar.append({'test_fiyati': fiyat, 'tahmini_adet': tahmini_adet, 'tahmini_kar': tahmini_kar})

            if not sonuclar:
                return False, "HATA: Hiçbir sonuç hesaplanamadı.", None

            df_sonuclar = pd.DataFrame(sonuclar)
            
            optimum = df_sonuclar.loc[df_sonuclar['tahmini_kar'].idxmax()]
            
            # Mevcut karı hesapla
            mevcut_gunluk_satis = df_gunluk.loc[df_gunluk['ortalama_fiyat'].idxmax()]['toplam_adet']
            mevcut_kar = (mevcut_fiyat - maliyet) * mevcut_gunluk_satis
            
            rapor += (
                f"--- MEVCUT DURUM (Menü Fiyatı) ---\n"
                f"  Mevcut Fiyat: {mevcut_fiyat:.2f} TL\n"
                f"  Ortalama Günlük Kar: {mevcut_kar:.2f} TL\n\n"
                f"--- OPTİMUM FİYAT TAVSİYESİ ---\n"
                f"  🏆 MAKSİMUM KAR İÇİN TAVSİYE EDİLEN FİYAT: {optimum['test_fiyati']:.2f} TL 🏆\n\n"
                f"  Bu fiyattan tahmini günlük satış: {optimum['tahmini_adet']:.1f} adet\n"
                f"  Tahmini maksimum günlük kar: {optimum['tahmini_kar']:.2f} TL"
            )
            
            # Chart.js için veriyi formatla
            chart_data = {
                'labels': [round(p, 2) for p in df_sonuclar['test_fiyati']],
                'datasets': [{
                    'label': 'Tahmini Toplam Kâr (TL)',
                    'data': [round(p, 2) for p in df_sonuclar['tahmini_kar']],
                    'borderColor': '#0d6efd',
                    'backgroundColor': 'rgba(13, 110, 253, 0.2)',
                    'fill': True,
                    'tension': 0.1
                }]
            }
            return True, rapor, json.dumps(chart_data)
            
        except Exception as e:
            return False, f"Optimizasyon hatası: {e}", None


# --- Motor 4 & 5 (Colab Hücre 10 & 11): Kategori ve Grup Analizi ---
def analiz_et_kategori_veya_grup(tip, isim, gun_sayisi=7):
    """
    Hem Kategori hem de Kategori Grubu analizini yapabilen birleşik fonksiyon.
    """
    try:
        if tip == 'kategori':
            df_satislar = _get_sales_by_filter('kategori', isim)
            grup_kolonu = 'isim' # Kategori içi ürünler
            baslik = f"KATEGORİ ANALİZİ: '{isim}'"
        elif tip == 'kategori_grubu':
            df_satislar = _get_sales_by_filter('kategori_grubu', isim)
            grup_kolonu = 'kategori' # Grup içi kategoriler
            baslik = f"KATEGORİ GRUBU ANALİZİ: '{isim}'"
        else:
            return False, "HATA: Geçersiz analiz tipi.", None

        if df_satislar is None or df_satislar.empty:
            return False, f"HATA: '{isim}' için hiç satış verisi bulunamadı.", None
        
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
            return False, f"UYARI: Karşılaştırma için yeterli veri bulunamadı. (Son {gun_sayisi} gün ve önceki {gun_sayisi} gün için ayrı ayrı veri gerekli).", None

        ozet_bu = _hesapla_kategori_ozeti(df_bu_periyot, grup_kolonu)
        ozet_onceki = _hesapla_kategori_ozeti(df_onceki_periyot, grup_kolonu)

        # Rapor için Metin Oluşturma
        rapor = f"{baslik}\n(Son {gun_sayisi} gün ile önceki {gun_sayisi} gün karşılaştırması)\n"
        rapor += "="*60 + "\n\n"

        rapor += f"--- ÖNCEKİ PERİYOT ({onceki_periyot_basi} - {bu_periyot_basi}) ---\n"
        rapor += f"  📊 TOPLAM KAR: {ozet_onceki['toplam_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Grup içinde):\n"
        if not ozet_onceki['paylar']:
            rapor += "    - Veri yok.\n"
        for item_name, pay in ozet_onceki['paylar'].items():
            rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_onceki['karlar'].get(item_name, 0):.2f} TL)\n"
        
        rapor += f"\n--- BU PERİYOT (Son {gun_sayisi} Gün) ---\n"
        rapor += f"  📊 TOPLAM KAR: {ozet_bu['toplam_kari']:.2f} TL\n"
        rapor += "  Kar Payları (Grup içinde):\n"
        if not ozet_bu['paylar']:
            rapor += "    - Veri yok.\n"
        for item_name, pay in ozet_bu['paylar'].items():
            rapor += f"    - {item_name:<20}: %{pay:.1f}  ({ozet_bu['karlar'].get(item_name, 0):.2f} TL)\n"
        
        rapor += "\n" + "="*60 + "\n"
        rapor += "  STRATEJİST TAVSİYESİ:\n"
        
        fark = ozet_bu['toplam_kari'] - ozet_onceki['toplam_kari']
        if fark > 0:
            rapor += f"  ✅ BAŞARILI! '{isim}' grubunun/kategorisinin toplam karı {fark:.2f} TL ARTTI."
        else:
            rapor += f"  ❌ DİKKAT! '{isim}' grubunun/kategorisinin toplam karı {abs(fark):.2f} TL AZALDI.\n"
            rapor += "  Bu durum 'yamyamlık' (cannibalization) etkisi olabilir. Detayları inceleyin.\n"
        
        # Chart.js için Veri Hazırlama
        labels = sorted(list(set(ozet_onceki['karlar'].keys()) | set(ozet_bu['karlar'].keys())))
        data_onceki = [ozet_onceki['karlar'].get(label, 0) for label in labels]
        data_bu = [ozet_bu['karlar'].get(label, 0) for label in labels]
        
        chart_data = {
            'labels': labels,
            'datasets': [
                {
                    'label': f'Önceki {gun_sayisi} Gün Kâr (TL)',
                    'data': data_onceki,
                    'backgroundColor': 'rgba(255, 99, 132, 0.5)',
                    'borderColor': 'rgb(255, 99, 132)',
                    'borderWidth': 1
                },
                {
                    'label': f'Son {gun_sayisi} Gün Kâr (TL)',
                    'data': data_bu,
                    'backgroundColor': 'rgba(54, 162, 235, 0.5)',
                    'borderColor': 'rgb(54, 162, 235)',
                    'borderWidth': 1
                }
            ]
        }
        
        return True, rapor, json.dumps(chart_data)

    except Exception as e:
        print(f"Stratejik analiz hatası: {e}")
        return False, f"Stratejik analiz hatası: {e}", None
