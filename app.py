# app.py (FAZ 5, AŞAMA 2: GERÇEK CRUD YÖNETİM PANELİ - DÜZELTİLMİŞ)
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from database import (
    db, init_db, Hammadde, Urun, Recete, SatisKaydi, User,
    guncelle_tum_urun_maliyetleri
)
import pandas as pd
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from sqlalchemy import func
import json # Chart.js için

# --- Analiz Motorlarını "Beyinden" İçe Aktar ---
# analysis_engine.py dosyasının da güncel olduğundan emin olun!
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'renderda_bunu_kesin_degistirmelisiniz123')
# Statik dosyalar için upload klasörü (logo vb. için ileride kullanılabilir)
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

init_db(app) # Veritabanını başlat
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Bu sayfayı görüntülemek için lütfen giriş yapın."
login_manager.login_message_category = "warning"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- İLK KULLANICIYI OLUŞTUR ---
# Veritabanı ilk kez oluşturulduğunda varsayılan bir kullanıcı ekler.
with app.app_context():
    if not User.query.first():
        print("İlk admin kullanıcısı oluşturuluyor...")
        # Lütfen bu şifreyi ilk girişten sonra hemen değiştirin!
        hashed_password = bcrypt.generate_password_hash("RestoranSifrem!2025").decode('utf-8')
        admin_user = User(username="onur", password_hash=hashed_password)
        db.session.add(admin_user)
        db.session.commit()
        print("Güvenli kullanıcı oluşturuldu.")

# --- CONTEXT PROCESSOR ---
# Tüm templatelerde site adını kullanılabilir yap
@app.context_processor
def inject_settings():
    # Şimdilik site adını sabit olarak gönderiyoruz
    return dict(site_name="RestoProfit")

# --- GÜVENLİK SAYFALARI ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard')) 
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Kullanıcı adı veya şifre hatalı.', 'danger')
    return render_template('login.html', title='Giriş Yap')

@app.route('/logout')
@login_required 
def logout():
    logout_user()
    flash('Başarıyla çıkış yaptınız.', 'info')
    return redirect(url_for('login'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not bcrypt.check_password_hash(current_user.password_hash, current_password):
            flash('Mevcut şifreniz hatalı.', 'danger')
            return redirect(url_for('change_password'))
            
        if new_password != confirm_password:
            flash('Yeni şifreler birbiriyle eşleşmiyor.', 'danger')
            return redirect(url_for('change_password'))
            
        if len(new_password) < 6:
            flash('Yeni şifreniz en az 6 karakter olmalıdır.', 'danger')
            return redirect(url_for('change_password'))

        try:
            hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
            current_user.password_hash = hashed_password
            db.session.commit()
            flash('Şifreniz başarıyla güncellendi. Lütfen yeni şifrenizle tekrar giriş yapın.', 'success')
            return redirect(url_for('logout')) 
            
        except Exception as e:
            db.session.rollback()
            flash(f"Şifre güncellenirken bir hata oluştu: {e}", 'danger')
            return redirect(url_for('change_password'))
            
    return render_template('change_password.html', title='Şifre Değiştir')


# --- ANA SAYFA ---
@app.route('/')
@login_required 
def dashboard():
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


# --- VERİ YÖNETİMİ ---
@app.route('/upload-excel', methods=['POST'])
@login_required
def upload_excel():
    if 'excel_file' not in request.files:
        flash('Dosya kısmı bulunamadı', 'danger')
        return redirect(request.referrer or url_for('dashboard')) # Geri dön
    
    file = request.files['excel_file']
    if file.filename == '':
        flash('Dosya seçilmedi', 'danger')
        return redirect(request.referrer or url_for('dashboard')) # Geri dön
    
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(file)
            required_columns = ['Urun_Adi', 'Adet', 'Toplam_Tutar', 'Tarih']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise ValueError(f"Excel dosyanızda şu kolonlar eksik: {', '.join(missing_columns)}")
            
            # Tarih formatını kontrol et ve dönüştür
            try:
                # Farklı formatları dene
                df['Tarih'] = pd.to_datetime(df['Tarih'], errors='coerce') 
            except Exception as date_err:
                 raise ValueError(f"Tarih sütunu okunamadı veya formatı anlaşılamadı. Beklenen formatlardan biri: YYYY-AA-GG SS:DD:SS veya DD.MM.YYYY. Hata: {date_err}")

            df.dropna(subset=['Tarih'], inplace=True) # Geçersiz tarihleri at

            # Adet ve Toplam_Tutar'ı sayısal yap
            df['Adet'] = pd.to_numeric(df['Adet'], errors='coerce')
            df['Toplam_Tutar'] = pd.to_numeric(df['Toplam_Tutar'], errors='coerce')
            df.dropna(subset=['Adet', 'Toplam_Tutar'], inplace=True) # Geçersiz sayısal değerleri at

            urunler_db = Urun.query.all()
            urun_eslestirme_haritasi = {u.excel_adi: u.id for u in urunler_db}
            urun_maliyet_haritasi = {u.id: u.hesaplanan_maliyet for u in urunler_db}
            
            yeni_kayit_listesi = []
            taninmayan_urunler = set()
            hatali_satirlar = []
            
            for index, satir in df.iterrows():
                try:
                    excel_urun_adi = str(satir['Urun_Adi']).strip()
                    adet = int(satir['Adet'])
                    toplam_tutar = float(satir['Toplam_Tutar'])
                    tarih = satir['Tarih'] # Zaten datetime objesi

                    if pd.isna(tarih):
                        hatali_satirlar.append(index + 2) # +2: Excel 1 tabanlı + başlık satırı
                        continue

                    urun_id = urun_eslestirme_haritasi.get(excel_urun_adi)
                    if not urun_id:
                        taninmayan
