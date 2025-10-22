# app.py (Son Sürüm - CRUD, Veri Yönetimi, Şifre Değiştirme)
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
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

# --- Analiz Motorlarını "Beyinden" İçe Aktar ---
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
# DİKKAT: Gerçek bir uygulamada bu anahtarı ortam değişkeninden alın!
app.secret_key = os.environ.get('SECRET_KEY', 'default-super-secret-key-for-dev')

# Veritabanını başlat
init_db(app)

# Güvenlik eklentilerini başlat
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
# ÖNEMLİ: Güvenliğiniz için bu bilgileri GitHub'a yüklemeden önce değiştirin veya ortam değişkeni kullanın.
with app.app_context():
    if not User.query.first():
        print("İlk admin kullanıcısı oluşturuluyor...")
        # Lütfen bu şifreyi ilk girişten sonra hemen değiştirin!
        hashed_password = bcrypt.generate_password_hash("RestoranSifrem!2025").decode('utf-8')
        admin_user = User(username="onur", password_hash=hashed_password)
        db.session.add(admin_user)
        db.session.commit()
        print("Güvenli kullanıcı oluşturuldu.")

# --- GÜVENLİK SAYFALARI (Login / Logout / Şifre Değiştirme) ---
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


# --- ANA SAYFA (DASHBOARD) ---
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


# --- EXCEL YÜKLEME ---
@app.route('/upload-excel', methods=['POST'])
@login_required
def upload_excel():
    if 'excel_file' not in request.files:
        flash('Dosya kısmı bulunamadı', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['excel_file']
    if file.filename == '':
        flash('Dosya seçilmedi', 'danger')
        return redirect(url_for('dashboard'))

    if file and file.filename.endswith('.xlsx'):
        try:
            df = pd.read_excel(file)
            required_columns = ['Urun_Adi', 'Adet', 'Toplam_Tutar', 'Tarih']
            missing_columns = [col for col in required_columns if not col in df.columns]
            if missing_columns:
                raise ValueError(f"Excel dosyanızda şu kolonlar eksik: {', '.join(missing_columns)}")

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
                flash(f"UYARI: Şu ürünler tanınmadı ve atlandı: {', '.join(taninmayan_urunler)}", 'warning')

        except ValueError as ve:
            flash(f"HATA OLUŞTU: {ve}", 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f"BEKLENMEDİK HATA: {e}. Lütfen Excel formatınızı kontrol edin.", 'danger')

    return redirect(url_for('dashboard'))


# --- YÖNETİM PANELİ (Faz 5: CRUD) ---
@app.route('/admin')
@login_required
def admin_panel():
    try:
        hammaddeler = Hammadde.query.order_by(Hammadde.isim).all()
        urunler = Urun.query.order_by(Urun.isim).all()
        receteler = Recete.query.join(Urun).join(Hammadde).order_by(Urun.isim, Hammadde.isim).all()
    except Exception as e:
        flash(f'Veritabanı hatası: {e}', 'danger')
        hammaddeler, urunler, receteler = [], [], []

    return render_template('admin.html', title='Menü Yönetimi',
                           hammaddeler=hammaddeler,
                           urunler=urunler,
                           receteler=receteler)

@app.route('/add-material', methods=['POST'])
@login_required
def add_material():
    try:
        isim = request.form.get('h_isim')
        birim = request.form.get('h_birim')
        fiyat = float(request.form.get('h_fiyat'))
        
        yeni_hammadde = Hammadde(isim=isim, maliyet_birimi=birim, maliyet_fiyati=fiyat)
        db.session.add(yeni_hammadde)
        db.session.commit()
        flash(f"Başarılı! '{isim}' hammaddesi eklendi.", 'success')
    
    except IntegrityError: 
        db.session.rollback()
        flash(f"HATA: '{isim}' adında bir hammadde zaten mevcut. Eklenemedi.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Hammadde eklenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))


@app.route('/add-product', methods=['POST'])
@login_required
def add_product():
    try:
        isim = request.form.get('u_isim')
        excel_adi = request.form.get('u_excel_adi')
        fiyat = float(request.form.get('u_fiyat'))
        kategori = request.form.get('u_kategori')
        grup = request.form.get('u_grup')
        
        yeni_urun = Urun(
            isim=isim, 
            excel_adi=excel_adi, 
            mevcut_satis_fiyati=fiyat, 
            kategori=kategori, 
            kategori_grubu=grup,
            hesaplanan_maliyet=0
        )
        db.session.add(yeni_urun)
        db.session.commit()
        flash(f"Başarılı! '{isim}' ürünü eklendi. (Maliyet için reçete ekleyin)", 'success')
    
    except IntegrityError:
        db.session.rollback()
        flash(f"HATA: '{isim}' adında bir ürün zaten mevcut. Eklenemedi.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Ürün eklenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))

@app.route('/add-recipe', methods=['POST'])
@login_required
def add_recipe():
    try:
        urun_id = int(request.form.get('r_urun_id'))
        hammadde_id = int(request.form.get('r_hammadde_id'))
        miktar = float(request.form.get('r_miktar'))
        
        existing_recipe = Recete.query.filter_by(urun_id=urun_id, hammadde_id=hammadde_id).first()
        if existing_recipe:
            flash("UYARI: Bu ürün için bu hammadde zaten reçetede vardı. Miktarı güncellendi.", 'warning')
            existing_recipe.miktar = miktar
        else:
            yeni_recete = Recete(urun_id=urun_id, hammadde_id=hammadde_id, miktar=miktar)
            db.session.add(yeni_recete)
            flash("Başarılı! Reçete kalemi eklendi.", 'success')
        
        db.session.commit()
        
        # Reçete eklendiği anda, o ürünün toplam maliyetini GÜNCELLE
        guncelle_tum_urun_maliyetleri()
        
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Reçete eklenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))


# --- VERİ YÖNETİMİ ---
@app.route('/delete-sales-by-date', methods=['POST'])
@login_required
def delete_sales_by_date():
    try:
        date_str = request.form.get('delete_date')
        if not date_str:
            flash("HATA: Lütfen silmek için geçerli bir tarih seçin.", 'danger')
            return redirect(url_for('admin_panel'))
            
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        num_deleted = db.session.query(SatisKaydi).filter(
            func.date(SatisKaydi.tarih) == target_date
        ).delete(synchronize_session=False)
        
        db.session.commit()
        
        if num_deleted > 0:
            flash(f"Başarılı! {target_date.strftime('%d %B %Y')} tarihine ait {num_deleted} adet satış kaydı kalıcı olarak silindi.", 'success')
        else:
            flash(f"Bilgi: {target_date.strftime('%d %B %Y')} tarihinde zaten hiç satış kaydı bulunamadı.", 'info')
            
    except ValueError:
         flash("HATA: Geçersiz tarih formatı.", 'danger')
         db.session.rollback()
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Satış kayıtları silinirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))

# --- ANALİZ RAPORLARI SAYFASI ---
@app.route('/reports', methods=['GET', 'POST'])
@login_required
def reports():
    # Bu listeler formları doldurmak için gerekli
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
                success, sonuc, chart_data = bul_optimum_fiyat(urun_ismi)
                
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
                           analiz_sonucu=analiz_sonucu,
                           chart_data=None) # chart_data'yı şimdilik None olarak geçiyoruz

# Render.com'un uygulamayı çalıştırması için
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
