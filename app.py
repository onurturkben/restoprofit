# app.py (FAZ 5: GÜVENLİK VE CRUD TEMELİ - DÜZELTİLMİŞ)
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from database import (
    db, init_db, Hammadde, Urun, Recete, SatisKaydi, User, # User eklendi
    menuyu_sifirla_ve_kur
)
import pandas as pd
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from flask_bcrypt import Bcrypt # Güvenli şifreleme için eklendi
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user # Giriş sistemi eklendi
)

# --- Analiz Motorlarını "Beyinden" İçe Aktar ---
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'renderda_bunu_kesin_degistirmelisiniz123')

# Veritabanını başlat
init_db(app)

# Güvenlik eklentilerini başlat
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login' # Kullanıcı giriş yapmamışsa, onu 'login' sayfasına yönlendir
login_manager.login_message = "Lütfen devam etmek için giriş yapın."
login_manager.login_message_category = "warning"

@login_manager.user_loader
def load_user(user_id):
    """ Flask-Login'in kullanıcıyı oturumdan tanımasını sağlar """
    return User.query.get(int(user_id))


# --- İLK KULLANICIYI OLUŞTUR (Sadece bir kez çalışır) ---
# Bu, "admin" / "12345" olarak ilk kullanıcınızı oluşturur.
# Girdikten sonra mutlaka şifrenizi değiştirmelisiniz!
# (Bu, Faz 5'in bir sonraki adımı olacak)
with app.app_context():
    if not User.query.first():
        print("İlk admin kullanıcısı oluşturuluyor...")
        hashed_password = bcrypt.generate_password_hash("1234").decode('utf-8')
        admin_user = User(username="onur", password_hash=hashed_password)
        db.session.add(admin_user)
        db.session.commit()
        print("Kullanıcı 'admin', şifre '12345' olarak oluşturuldu.")

# --- GÜVENLİK SAYFALARI (Login / Logout) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard')) # Zaten giriş yapmışsa ana sayfaya yolla

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user) # Kullanıcıyı "giriş yapmış" olarak işaretle
            flash(f'Hoşgeldiniz, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Kullanıcı adı veya şifre hatalı.', 'danger')
            
    return render_template('login.html', title='Giriş Yap')

@app.route('/logout')
@login_required # Sadece giriş yapmışlar çıkış yapabilir
def logout():
    logout_user() # Kullanıcıyı "çıkış yapmış" olarak işaretle
    flash('Başarıyla çıkış yaptınız.', 'info')
    return redirect(url_for('login'))


# --- ANA SAYFA (DASHBOARD) ---
@app.route('/')
@login_required # BU SAYFA ARTIK KORUMALI
def dashboard():
    # Excel yükleme formu artık GET'te değil, ayrı bir route'da
    try:
        toplam_satis_kaydi = db.session.query(SatisKaydi).count()
        toplam_urun = db.session.query(Urun).count()
        summary = {
            'toplam_satis_kaydi': toplam_satis_kaydi,
            'toplam_urun': toplam_urun
        }
    except Exception as e:
        summary = {'toplam_satis_kaydi': 0, 'toplam_urun': 0}
        flash(f'Veritabanı bağlantı hatası: {e}', 'danger')

    return render_template('dashboard.html', title='Ana Ekran', summary=summary)

# --- EXCEL YÜKLEME (Artık kendi route'unda) ---
@app.route('/upload-excel', methods=['POST'])
@login_required # BU İŞLEM ARTIK KORUMALI
def upload_excel():
    if 'excel_file' not in request.files:
        flash('Dosya kısmı bulunamadı', 'danger')
        return redirect(request.url)
    
    file = request.files['excel_file']
    if file.filename == '':
        flash('Dosya seçilmedi', 'danger')
        return redirect(request.url)
    
    if file and file.filename.endswith('.xlsx'):
        try:
            # --- BURAYA "AKILLI HATA KONTROLÜ" (Sorun 3) GELECEK ---
            df = pd.read_excel(file)
            
            # Kolon kontrolü (Sorun 3'ün çözümü)
            required_columns = ['Urun_Adi', 'Adet', 'Toplam_Tutar', 'Tarih']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise ValueError(f"Excel dosyanızda şu kolonlar eksik: {', '.join(missing_columns)}")
            
            # --- Kontrol Tamam, İşleme Devam ---
            
            urunler_db = Urun.query.all()
            urun_eslestirme_haritasi = {u.excel_adi: u.id for u in urunler_db}
            urun_maliyet_haritasi = {u.id: u.hesaplanan_maliyet for u in urunler_db}
            
            yeni_kayit_listesi = []
            taninmayan_urunler = set()
            
            for index, satir in df.iterrows():
                excel_urun_adi = satir['Urun_Adi']
                adet = int(satir['Adet'])
                toplam_tutar = float(satir['Toplam_Tutar'])
                tarih = pd.to_datetime(satir['Tarih'])
                
                urun_id = urun_eslestirme_haritasi.get(excel_urun_adi)
                if not urun_id:
                    taninmayan_urunler.add(excel_urun_adi)
                    continue
                if adet == 0: continue
                
                o_anki_maliyet = urun_maliyet_haritasi.get(urun_id, 0)
                hesaplanan_toplam_maliyet = o_anki_maliyet * adet
                hesaplanan_kar = toplam_tutar - hesaplanan_toplam_maliyet
                
                yeni_kayit = SatisKaydi(
                    urun_id=urun_id, tarih=tarih, adet=adet, toplam_tutar=toplam_tutar,
                    hesaplanan_birim_fiyat=(toplam_tutar / adet),
                    hesaplanan_maliyet=hesaplanan_toplam_maliyet,
                    hesaplanan_kar=hesaplanan_kar
                )
                yeni_kayit_listesi.append(yeni_kayit)
            
            db.session.bulk_save_objects(yeni_kayit_listesi)
            db.session.commit()
            
            flash(f'Başarılı! {len(yeni_kayit_listesi)} adet satış kaydı veritabanına işlendi.', 'success')
            if taninmayan_urunler:
                flash(f"UYARI: Şu ürünler tanınmadı ve atlandı: {taninmayan_urunler}", 'warning')

        except ValueError as ve: # Yakalanan "Akıllı Hata"
            flash(f"HATA OLUŞTU: {ve}", 'danger')
        except Exception as e: # Yakalanamayan genel hata
            db.session.rollback()
            flash(f"BEKLENMEDİK HATA: {e}. Lütfen Excel formatınızı kontrol edin.", 'danger')
        
    return redirect(url_for('dashboard'))


# --- YÖNETİM PANELİ (Hücre 3'ün Arayüzü) ---
@app.route('/admin')
@login_required # BU SAYFA ARTIK KORUMALI
def admin_panel():
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
@login_required # BU İŞLEM ARTIK KORUMALI
def reset_menu_data():
    
    # --- BU BÖLÜM GELECEKTE KALKACAK (CRUD Geldiginde) ---
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
    # --- BU BÖLÜM GELECEKTE KALKACAK ---
    
    success, message = menuyu_sifirla_ve_kur(hammaddeler_data, urunler_data, receteler_data)
    
    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')
        
    return redirect(url_for('admin_panel'))


# --- ANALİZ RAPORLARI SAYFASI ---
@app.route('/reports', methods=['GET', 'POST'])
@login_required # BU SAYFA ARTIK KORUMALI
def reports():
    urun_listesi = [u.isim for u in Urun.query.order_by(Urun.isim).all()]
    kategori_listesi = sorted(list(set([u.kategori for u in Urun.query.all() if u.kategori])))
    grup_listesi = sorted(list(set([u.kategori_grubu for u in Urun.query.all() if u.kategori_grubu])))
    
    analiz_sonucu = None
    
    if request.method == 'POST':
        try:
            analiz_tipi = request.form.get('analiz_tipi')
            
            if analiz_tipi == 'hedef_marj':
                urun_ismi = request.form.get('urun_ismi')
                hedef_marj = float(request.form.get('hedef_marj'))
                success, sonuc = hesapla_hedef_marj(urun_ismi, hedef_marj)
            
            elif analiz_tipi == 'simulasyon':
                urun_ismi = request.form.get('urun_ismi')
                yeni_fiyat = float(request.form.get('yeni_fiyat'))
                success, sonuc = simule_et_fiyat_degisikligi(urun_ismi, yeni_fiyat)
                
            elif analiz_tipi == 'optimum_fiyat':
                urun_ismi = request.form.get('urun_ismi')
                success, sonuc = bul_optimum_fiyat(urun_ismi)
                
            elif analiz_tipi == 'kategori':
                kategori_ismi = request.form.get('kategori_ismi')
                gun_sayisi = int(request.form.get('gun_sayisi', 7))
                success, sonuc = analiz_et_kategori_veya_grup('kategori', kategori_ismi, gun_sayisi)
                
            elif analiz_tipi == 'grup':
                grup_ismi = request.form.get('grup_ismi')
                gun_sayisi = int(request.form.get('gun_sayisi', 7))
                success, sonuc = analiz_et_kategori_veya_grup('kategori_grubu', grup_ismi, gun_sayisi)
            
            else:
                success, sonuc = False, "Geçersiz analiz tipi."

            analiz_sonucu = sonuc
            if not success:
                flash(sonuc, 'danger')

        except Exception as e:
            flash(f"Analiz motoru hatası: {e}", 'danger')

    return render_template('reports.html', title='Analiz Motorları',
                           urun_listesi=urun_listesi,
                           kategori_listesi=kategori_listesi,
                           grup_listesi=grup_listesi,
                           analiz_sonucu=analiz_sonucu)

# Render.com'un uygulamayı çalıştırması için
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
