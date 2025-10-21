# app.py
# Bu, ana web sunucusu dosyamızdır (Flask).
# Arayüzü (HTML) "beyne" (analysis_engine) ve "hafızaya" (database) bağlar.

import os
from flask import Flask, render_template, request, redirect, url_for, flash
from database import db, init_db, Hammadde, Urun, Recete, SatisKaydi, menuyu_sifirla_ve_kur
import pandas as pd
from datetime import datetime
from sqlalchemy.exc import IntegrityError

# --- Analiz Motorlarını "Beyinden" İçe Aktar ---
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

# Flask uygulamasını başlat
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'bu_bir_test_anahtaridir_renderda_degistirin')

# Veritabanını başlat (Render.com'daki DATABASE_URL'i otomatik bulacak)
init_db(app)

# --- ANA SAYFA (DASHBOARD) ---
# Burası hem ana ekran hem de Excel yükleme (Hücre 4) yeridir.
@app.route('/', methods=['GET', 'POST'])
def dashboard():
    if request.method == 'POST':
        # --- Excel Yükleme Mantığı (Hücre 4) ---
        if 'excel_file' not in request.files:
            flash('Dosya kısmı bulunamadı', 'danger')
            return redirect(request.url)
        
        file = request.files['excel_file']
        if file.filename == '':
            flash('Dosya seçilmedi', 'danger')
            return redirect(request.url)
        
        if file and file.filename.endswith('.xlsx'):
            try:
                # 1. Excel'i Pandas ile oku
                df = pd.read_excel(file)
                
                # 2. Gerekli haritaları (eşleştirme) veritabanından çek
                # (Teknik borcu ödedik: Sadece o anki güncel maliyetleri alıyoruz)
                urunler_db = Urun.query.all()
                urun_eslestirme_haritasi = {u.excel_adi: u.id for u in urunler_db}
                urun_maliyet_haritasi = {u.id: u.hesaplanan_maliyet for u in urunler_db}
                
                yeni_kayit_listesi = []
                taninmayan_urunler = set()
                
                # 3. Excel'i satır satır işle
                for index, satir in df.iterrows():
                    excel_urun_adi = satir['Urun_Adi']
                    adet = int(satir['Adet'])
                    toplam_tutar = float(satir['Toplam_Tutar'])
                    tarih = pd.to_datetime(satir['Tarih'])
                    
                    urun_id = urun_eslestirme_haritasi.get(excel_urun_adi)
                    
                    if not urun_id:
                        taninmayan_urunler.add(excel_urun_adi)
                        continue
                    
                    if adet == 0: continue # 0 adetli satırları atla
                    
                    # 4. TEKNİK BORÇ ÇÖZÜMÜ:
                    # Maliyeti, o anki güncel maliyet haritasından al
                    o_anki_maliyet = urun_maliyet_haritasi.get(urun_id, 0)
                    
                    # Hesaplamaları yap ve veritabanına "kilitle"
                    hesaplanan_toplam_maliyet = o_anki_maliyet * adet
                    hesaplanan_kar = toplam_tutar - hesaplanan_toplam_maliyet
                    
                    yeni_kayit = SatisKaydi(
                        urun_id=urun_id,
                        tarih=tarih,
                        adet=adet,
                        toplam_tutar=toplam_tutar,
                        hesaplanan_birim_fiyat=(toplam_tutar / adet),
                        hesaplanan_maliyet=hesaplanan_toplam_maliyet,
                        hesaplanan_kar=hesaplanan_kar
                    )
                    yeni_kayit_listesi.append(yeni_kayit)
                
                # 5. Tüm yeni kayıtları toplu halde veritabanına ekle
                db.session.bulk_save_objects(yeni_kayit_listesi)
                db.session.commit()
                
                flash(f'Başarılı! {len(yeni_kayit_listesi)} adet satış kaydı veritabanına işlendi.', 'success')
                if taninmayan_urunler:
                    flash(f"UYARI: Şu ürünler tanınamadı ve atlandı: {taninmayan_urunler}", 'warning')

            except Exception as e:
                db.session.rollback()
                flash(f"HATA OLUŞTU: {e}. Lütfen Excel formatınızı ('Urun_Adi', 'Adet', 'Toplam_Tutar', 'Tarih') kontrol edin.", 'danger')
            
            return redirect(url_for('dashboard'))

    # 'GET' isteği (sayfa ilk açıldığında)
    # Ana sayfa için özet verileri çek
    try:
        toplam_satis_kaydi = db.session.query(SatisKaydi).count()
        toplam_urun = db.session.query(Urun).count()
        summary = {
            'toplam_satis_kaydi': toplam_satis_kaydi,
            'toplam_urun': toplam_urun
        }
    except Exception as e:
        summary = {'toplam_satis_kaydi': 0, 'toplam_urun': 0}
        flash(f'Veritabanı bağlantı hatası (Render.com da DATABASE_URL ayarlandı mı?): {e}', 'danger')

    return render_template('dashboard.html', title='Ana Ekran', summary=summary)


# --- YÖNETİM PANELİ (Hücre 3'ün Arayüzü) ---
@app.route('/admin', methods=['GET'])
def admin_panel():
    """ Menü/Maliyet/Reçetelerin güncel listesini gösterir. """
    try:
        hammaddeler = Hammadde.query.all()
        urunler = Urun.query.all()
        receteler = Recete.query.all()
    except Exception as e:
        flash(f'Veritabanı hatası: {e}', 'danger')
        hammaddeler, urunler, receteler = [], [], []
        
    return render_template('admin.html', title='Menü Yönetimi', 
                           hammaddeler=hammaddeler, 
                           urunler=urunler, 
                           receteler=receteler)


@app.route('/reset-menu-data', methods=['POST'])
def reset_menu_data():
    """ 
    YÖNETİM PANELİNDEKİ "MENÜYÜ SIFIRLA" BUTONUNUN ÇALIŞTIRDIĞI YER
    Verileri kodun içinden alır (Colab'deki Hücre 3 gibi).
    Gelecekte bu, formdan veri alacak şekilde geliştirilebilir.
    """
    
    # --- Colab Hücre 3'teki verilerinizi buraya gömüyoruz ---
    # (DİKKAT: menuyu_sifirla_ve_kur SATIŞLARI SİLMEZ, GÜVENLİDİR)
    
    hammaddeler_data = [
        ('Köfte Harcı', 'kg', 1200.0), ('Cheeseburger Ekmeği', 'adet', 15.0),
        ('Cheddar Peyniri', 'kg', 700.0), ('Steak Eti (Bonfile)', 'kg', 1800.0), 
        ('Pesto Sos', 'kg', 450.0), ('Makarna (Pişmemiş)', 'kg', 100.0),
        ('Domates', 'kg', 80.0), ('Bira (Fıçı)', 'litre', 200.0),
    ]
    
    urunler_data = [
        ('Cheeseburger',    'Cheeseburger',    250.0, 'Burgerler',     'Ana Yemekler'),
        ('Steak Burger',    'Steak Burger',    400.0, 'Burgerler',     'Ana Yemekler'),
        ('Steak Tabağı',    'Steak Tabağı',    550.0, 'Et Yemekleri',  'Ana Yemekler'),
        ('Pesto Makarna',  'Pesto Makarna',  220.0, 'Makarnalar',    'Ana Yemekler'),
        ('Domates Çorbası', 'Domates Çorbası',  90.0, 'Başlangıçlar',  'Başlangıçlar'),
        ('Bira 50cl',       'Bira 50cl',       100.0, 'İçecekler',     'İçecekler'),
    ]
    
    receteler_data = [
        ('Cheeseburger', 'Köfte Harcı', 0.150), ('Cheeseburger', 'Cheeseburger Ekmeği', 1),   
        ('Cheeseburger', 'Cheddar Peyniri', 0.020), ('Steak Burger', 'Steak Eti (Bonfile)', 0.180), 
        ('Steak Burger', 'Cheeseburger Ekmeği', 1), ('Steak Tabağı', 'Steak Eti (Bonfile)', 0.220), 
        ('Pesto Makarna', 'Makarna (Pişmemiş)', 0.120), ('Pesto Makarna', 'Pesto Sos', 0.080),        
        ('Domates Çorbası', 'Domates', 0.250), ('Bira 50cl', 'Bira (Fıçı)', 0.5), 
    ]
    # --- Veri bitti ---
    
    success, message = menuyu_sifirla_ve_kur(hammaddeler_data, urunler_data, receteler_data)
    
    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')
        
    return redirect(url_for('admin_panel'))


# --- ANALİZ RAPORLARI SAYFASI ---
@app.route('/reports', methods=['GET', 'POST'])
def reports():
    urun_listesi = [u.isim for u in Urun.query.order_by(Urun.isim).all()]
    kategori_listesi = sorted(list(set([u.kategori for u in Urun.query.all() if u.kategori])))
    grup_listesi = sorted(list(set([u.kategori_grubu for u in Urun.query.all() if u.kategori_grubu])))
    
    analiz_sonucu = None
    
    if request.method == 'POST':
        try:
            analiz_tipi = request.form.get('analiz_tipi')
            
            if analiz_tipi == 'hedef_marj': (Hücre 9)
                urun_ismi = request.form.get('urun_ismi')
                hedef_marj = float(request.form.get('hedef_marj'))
                success, sonuc = hesapla_hedef_marj(urun_ismi, hedef_marj)
            
            elif analiz_tipi == 'simulasyon': (Hücre 7)
                urun_ismi = request.form.get('urun_ismi')
                yeni_fiyat = float(request.form.get('yeni_fiyat'))
                success, sonuc = simule_et_fiyat_degisikligi(urun_ismi, yeni_fiyat)
                
            elif analiz_tipi == 'optimum_fiyat': (Hücre 8)
                urun_ismi = request.form.get('urun_ismi')
                success, sonuc = bul_optimum_fiyat(urun_ismi)
                
            elif analiz_tipi == 'kategori': (Hücre 10)
                kategori_ismi = request.form.get('kategori_ismi')
                gun_sayisi = int(request.form.get('gun_sayisi', 7))
                success, sonuc = analiz_et_kategori_veya_grup('kategori', kategori_ismi, gun_sayisi)
                
            elif analiz_tipi == 'grup': (Hücre 11)
                grup_ismi = request.form.get('grup_ismi')
                gun_sayisi = int(request.form.get('gun_sayisi', 7))
                success, sonuc = analiz_et_kategori_veya_grup('kategori_grubu', grup_ismi, gun_sayisi)
            
            else:
                success, sonuc = False, "Geçersiz analiz tipi."

            analiz_sonucu = sonuc # Raporu arayüze göndermek için sakla
            if not success:
                flash(sonuc, 'danger')

        except Exception as e:
            flash(f"Analiz motoru hatası: {e}", 'danger')

    # Rapor sonucunu <pre> etiketiyle göstermek için 'reports.html'e gönder
    return render_template('reports.html', title='Analiz Motorları',
                           urun_listesi=urun_listesi,
                           kategori_listesi=kategori_listesi,
                           grup_listesi=grup_listesi,
                           analiz_sonucu=analiz_sonucu)

# Render.com'un uygulamayı çalıştırması için
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
