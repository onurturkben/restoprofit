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
                        taninmayan_urunler.add(excel_urun_adi)
                        continue
                    if adet <= 0: # Adet 0 veya negatifse atla
                         hatali_satirlar.append(index + 2)
                         continue

                    o_anki_maliyet = urun_maliyet_haritasi.get(urun_id, 0.0) # Maliyet yoksa 0 varsay
                    if o_anki_maliyet is None: o_anki_maliyet = 0.0 # None gelme ihtimaline karşı

                    hesaplanan_toplam_maliyet = o_anki_maliyet * adet
                    hesaplanan_kar = toplam_tutar - hesaplanan_toplam_maliyet
                    
                    # Birim fiyat 0 olamaz
                    hesaplanan_birim_fiyat = toplam_tutar / adet if adet != 0 else 0
                    if hesaplanan_birim_fiyat == 0:
                        hatali_satirlar.append(index + 2)
                        continue


                    yeni_kayit = SatisKaydi(
                        urun_id=urun_id, 
                        tarih=tarih, 
                        adet=adet, 
                        toplam_tutar=toplam_tutar,
                        hesaplanan_birim_fiyat=hesaplanan_birim_fiyat,
                        hesaplanan_maliyet=hesaplanan_toplam_maliyet,
                        hesaplanan_kar=hesaplanan_kar
                    )
                    yeni_kayit_listesi.append(yeni_kayit)
                except Exception as row_error:
                    print(f"Satır {index + 2} işlenirken hata: {row_error}")
                    hatali_satirlar.append(index + 2)
                    continue # Bu satırı atla, diğerlerine devam et
            
            if yeni_kayit_listesi:
                db.session.add_all(yeni_kayit_listesi)
                db.session.commit()
                flash(f'Başarılı! {len(yeni_kayit_listesi)} adet satış kaydı veritabanına işlendi.', 'success')
            else:
                 flash('Excel dosyasından işlenecek geçerli satış kaydı bulunamadı.', 'warning')


            if taninmayan_urunler:
                flash(f"UYARI: Şu ürünler 'Menü Yönetimi'nde bulunamadı ve atlandı: {', '.join(taninmayan_urunler)}", 'warning')
            if hatali_satirlar:
                 flash(f"UYARI: Excel'deki şu satırlar hatalı veri içerdiği için atlandı: {', '.join(map(str, sorted(list(set(hatali_satirlar)))))}", 'warning')


        except ValueError as ve:
            flash(f"Excel İşleme Hatası: {ve}", 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f"BEKLENMEDİK HATA: {e}. Excel formatını veya veritabanı bağlantısını kontrol edin.", 'danger')
        
    else:
        flash("HATA: Yalnızca .xlsx uzantılı Excel dosyaları desteklenmektedir.", 'danger')

    return redirect(request.referrer or url_for('dashboard')) # Geldiği sayfaya geri dön


# --- YÖNETİM PANELİ (CRUD) ---
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

# --- HAMMADDE CRUD ---
@app.route('/add-material', methods=['POST'])
@login_required
def add_material():
    try:
        isim = request.form.get('h_isim').strip()
        birim = request.form.get('h_birim').strip()
        fiyat_str = request.form.get('h_fiyat')
        
        if not isim or not birim or not fiyat_str:
            flash("HATA: Tüm hammadde alanları doldurulmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))
            
        fiyat = float(fiyat_str.replace(',', '.')) # Virgülü noktaya çevir

        if fiyat <= 0:
            flash("HATA: Hammadde fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))
            
        yeni_hammadde = Hammadde(isim=isim, maliyet_birimi=birim, maliyet_fiyati=fiyat)
        db.session.add(yeni_hammadde)
        db.session.commit()
        flash(f"Başarılı! '{isim}' hammaddesi eklendi.", 'success')
    
    except IntegrityError: 
        db.session.rollback()
        flash(f"HATA: '{isim}' adında bir hammadde zaten mevcut.", 'danger')
    except ValueError:
        db.session.rollback()
        flash("HATA: Fiyat geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Hammadde eklenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))

@app.route('/edit-material/<int:id>', methods=['POST'])
@login_required
def edit_material(id):
    try:
        hammadde = db.session.get(Hammadde, id)
        if not hammadde:
            flash('Hammadde bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))
        
        isim = request.form.get('edit_h_isim').strip()
        birim = request.form.get('edit_h_birim').strip()
        fiyat_str = request.form.get('edit_h_fiyat')

        if not isim or not birim or not fiyat_str:
            flash("HATA: Tüm hammadde alanları doldurulmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        fiyat = float(fiyat_str.replace(',', '.'))

        if fiyat <= 0:
            flash("HATA: Hammadde fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))
        
        # İsim değişikliği varsa ve yeni isim zaten varsa kontrol et
        if hammadde.isim != isim and Hammadde.query.filter(Hammadde.isim == isim, Hammadde.id != id).first():
             flash(f"HATA: '{isim}' adında başka bir hammadde zaten mevcut.", 'danger')
             return redirect(url_for('admin_panel'))

        hammadde.isim = isim
        hammadde.maliyet_birimi = birim
        hammadde.maliyet_fiyati = fiyat
        db.session.commit()
        guncelle_tum_urun_maliyetleri() # Fiyat değiştiği için maliyetleri yeniden hesapla
        flash(f"'{hammadde.isim}' güncellendi.", 'success')

    except ValueError:
        db.session.rollback()
        flash("HATA: Fiyat geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Güncelleme sırasında bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/delete-material/<int:id>', methods=['POST'])
@login_required
def delete_material(id):
    try:
        hammadde = db.session.get(Hammadde, id)
        if hammadde:
            # Reçetelerde kullanılıp kullanılmadığını kontrol et
            if hammadde.receteler.first(): # İlişkili reçete varsa
                flash(f"HATA: '{hammadde.isim}' bir veya daha fazla reçetede kullanıldığı için silinemez. Önce ilgili reçeteleri silin.", 'danger')
                return redirect(url_for('admin_panel'))
                
            db.session.delete(hammadde)
            db.session.commit()
            flash(f"'{hammadde.isim}' hammaddesi silindi.", 'success')
        else:
            flash("Hammadde bulunamadı.", 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Hammadde silinirken bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))

# --- ÜRÜN CRUD ---
@app.route('/add-product', methods=['POST'])
@login_required
def add_product():
    try:
        isim = request.form.get('u_isim').strip()
        excel_adi = request.form.get('u_excel_adi').strip()
        fiyat_str = request.form.get('u_fiyat')
        kategori = request.form.get('u_kategori').strip()
        grup = request.form.get('u_grup').strip()

        if not isim or not excel_adi or not fiyat_str or not kategori or not grup:
            flash("HATA: Tüm ürün alanları doldurulmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        fiyat = float(fiyat_str.replace(',', '.'))
        
        if fiyat <= 0:
            flash("HATA: Ürün fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        yeni_urun = Urun(
            isim=isim, 
            excel_adi=excel_adi, 
            mevcut_satis_fiyati=fiyat, 
            kategori=kategori, 
            kategori_grubu=grup,
            hesaplanan_maliyet=0 # Başlangıç maliyeti 0
        )
        db.session.add(yeni_urun)
        db.session.commit()
        flash(f"Başarılı! '{isim}' ürünü eklendi. Şimdi reçetesini oluşturun.", 'success')
    
    except IntegrityError:
        db.session.rollback()
        flash(f"HATA: '{isim}' adında veya '{excel_adi}' Excel adında bir ürün zaten mevcut.", 'danger')
    except ValueError:
        db.session.rollback()
        flash("HATA: Fiyat geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Ürün eklenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))
    
@app.route('/edit-product/<int:id>', methods=['POST'])
@login_required
def edit_product(id):
    try:
        urun = db.session.get(Urun, id)
        if not urun:
            flash('Ürün bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))
            
        isim = request.form.get('edit_u_isim').strip()
        excel_adi = request.form.get('edit_u_excel_adi').strip()
        fiyat_str = request.form.get('edit_u_fiyat')
        kategori = request.form.get('edit_u_kategori').strip()
        grup = request.form.get('edit_u_grup').strip()

        if not isim or not excel_adi or not fiyat_str or not kategori or not grup:
            flash("HATA: Tüm ürün alanları doldurulmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        fiyat = float(fiyat_str.replace(',', '.'))

        if fiyat <= 0:
            flash("HATA: Ürün fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))
            
        # İsim veya Excel adı değişikliği varsa ve yenisi zaten varsa kontrol et
        if urun.isim != isim and Urun.query.filter(Urun.isim == isim, Urun.id != id).first():
             flash(f"HATA: '{isim}' adında başka bir ürün zaten mevcut.", 'danger')
             return redirect(url_for('admin_panel'))
        if urun.excel_adi != excel_adi and Urun.query.filter(Urun.excel_adi == excel_adi, Urun.id != id).first():
             flash(f"HATA: '{excel_adi}' Excel adına sahip başka bir ürün zaten mevcut.", 'danger')
             return redirect(url_for('admin_panel'))

        urun.isim = isim
        urun.excel_adi = excel_adi
        urun.mevcut_satis_fiyati = fiyat
        urun.kategori = kategori
        urun.kategori_grubu = grup
        db.session.commit()
        # Ürün bilgileri değişti, maliyetler etkilenmez ama yine de güncel tutalım
        # guncelle_tum_urun_maliyetleri() # Bu aslında gereksiz ama zararı yok
        flash(f"'{urun.isim}' güncellendi.", 'success')

    except ValueError:
        db.session.rollback()
        flash("HATA: Fiyat geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Ürün güncellenirken bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))

@app.route('/delete-product/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    try:
        urun = db.session.get(Urun, id)
        if urun:
            # SQLAlchemy cascade="all, delete-orphan" ayarı sayesinde
            # ilişkili Recete ve SatisKaydi kayıtları otomatik silinir.
            db.session.delete(urun)
            db.session.commit()
            flash(f"'{urun.isim}' ürünü ve ilişkili tüm veriler (reçete, satışlar) silindi.", 'success')
        else:
            flash("Ürün bulunamadı.", 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Ürün silinirken bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))

# --- REÇETE CRUD ---
@app.route('/add-recipe', methods=['POST'])
@login_required
def add_recipe():
    try:
        urun_id_str = request.form.get('r_urun_id')
        hammadde_id_str = request.form.get('r_hammadde_id')
        miktar_str = request.form.get('r_miktar')

        if not urun_id_str or not hammadde_id_str or not miktar_str:
            flash("HATA: Reçete için ürün, hammadde ve miktar seçilmelidir.", 'danger')
            return redirect(url_for('admin_panel'))
            
        urun_id = int(urun_id_str)
        hammadde_id = int(hammadde_id_str)
        miktar = float(miktar_str.replace(',', '.'))

        if miktar <= 0:
            flash("HATA: Miktar pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))
            
        existing_recipe = Recete.query.filter_by(urun_id=urun_id, hammadde_id=hammadde_id).first()
        if existing_recipe:
            flash("UYARI: Bu ürün için bu hammadde zaten reçetede vardı. Miktarı güncellendi.", 'warning')
            existing_recipe.miktar = miktar
        else:
            # Seçilen ürün ve hammaddenin var olup olmadığını kontrol et
            urun = db.session.get(Urun, urun_id)
            hammadde = db.session.get(Hammadde, hammadde_id)
            if not urun or not hammadde:
                flash("HATA: Seçilen ürün veya hammadde bulunamadı.", 'danger')
                return redirect(url_for('admin_panel'))

            yeni_recete = Recete(urun_id=urun_id, hammadde_id=hammadde_id, miktar=miktar)
            db.session.add(yeni_recete)
            flash("Başarılı! Reçete kalemi eklendi.", 'success')
        
        db.session.commit()
        guncelle_tum_urun_maliyetleri() # Maliyeti hemen güncelle
        
    except ValueError:
        db.session.rollback()
        flash("HATA: Miktar geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Reçete işlenirken bir hata oluştu: {e}", 'danger')
        
    return redirect(url_for('admin_panel'))

@app.route('/edit-recipe/<int:id>', methods=['POST'])
@login_required
def edit_recipe(id):
    try:
        recete_item = db.session.get(Recete, id)
        if not recete_item:
            flash('Reçete kalemi bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))

        miktar_str = request.form.get('edit_r_miktar')
        if not miktar_str:
            flash("HATA: Miktar alanı boş olamaz.", 'danger')
            return redirect(url_for('admin_panel'))

        miktar = float(miktar_str.replace(',', '.'))
        
        if miktar <= 0:
            flash("HATA: Miktar pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        recete_item.miktar = miktar
        db.session.commit()
        guncelle_tum_urun_maliyetleri()
        flash(f"'{recete_item.urun.isim}' ürününün '{recete_item.hammadde.isim}' reçete kalemi güncellendi.", 'success')
        
    except ValueError:
        db.session.rollback()
        flash("HATA: Miktar geçerli bir sayı olmalıdır.", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Reçete güncellenirken bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))


@app.route('/delete-recipe/<int:id>', methods=['POST'])
@login_required
def delete_recipe(id):
    try:
        recete_item = db.session.get(Recete, id)
        if recete_item:
            urun_adi = recete_item.urun.isim # Silmeden önce ismi alalım
            hammadde_adi = recete_item.hammadde.isim
            db.session.delete(recete_item)
            db.session.commit()
            guncelle_tum_urun_maliyetleri() # Maliyeti yeniden güncelle
            flash(f"'{urun_adi}' ürününden '{hammadde_adi}' kalemi silindi.", 'success')
        else:
            flash("Reçete kalemi bulunamadı.", 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f"HATA: Reçete kalemi silinirken bir hata oluştu: {e}", 'danger')
    return redirect(url_for('admin_panel'))


# --- VERİ SİLME ---
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
        ).delete(synchronize_session=False) # Performansı artırmak için
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
    try:
        urunler_db = Urun.query.order_by(Urun.isim).all()
        urun_listesi = [u.isim for u in urunler_db]
        
        kategoriler_db = db.session.query(Urun.kategori).distinct().order_by(Urun.kategori).all()
        kategori_listesi = [k[0] for k in kategoriler_db if k[0]]
        
        gruplar_db = db.session.query(Urun.kategori_grubu).distinct().order_by(Urun.kategori_grubu).all()
        grup_listesi = [g[0] for g in gruplar_db if g[0]]
        
    except Exception as e:
        flash(f'Veritabanından listeler çekilirken hata oluştu: {e}', 'danger')
        urun_listesi, kategori_listesi, grup_listesi = [], [], []

    analiz_sonucu = None
    chart_data = None # Grafik verisi için
    analiz_tipi_baslik = "" # Sonuç bölümünde hangi analizin yapıldığını belirtmek için
    
    if request.method == 'POST':
        try:
            analiz_tipi = request.form.get('analiz_tipi')
            urun_ismi = request.form.get('urun_ismi')
            kategori_ismi = request.form.get('kategori_ismi')
            grup_ismi = request.form.get('grup_ismi')
            gun_sayisi_str = request.form.get('gun_sayisi', '7')
            
            # Gelen parametreleri doğrula
            try:
                gun_sayisi = int(gun_sayisi_str) if gun_sayisi_str.isdigit() else 7
            except ValueError:
                gun_sayisi = 7
                flash("Geçersiz gün sayısı, varsayılan 7 gün kullanıldı.", "warning")

            if analiz_tipi == 'hedef_marj':
                analiz_tipi_baslik = f"Hedef Marj ({urun_ismi})"
                hedef_marj_str = request.form.get('hedef_marj')
                if not hedef_marj_str:
                    raise ValueError("Hedef marj belirtilmedi.")
                hedef_marj = float(hedef_marj_str)
                success, sonuc = hesapla_hedef_marj(urun_ismi, hedef_marj)
            
            elif analiz_tipi == 'simulasyon':
                analiz_tipi_baslik = f"Fiyat Simülasyonu ({urun_ismi})"
                yeni_fiyat_str = request.form.get('yeni_fiyat')
                if not yeni_fiyat_str:
                    raise ValueError("Yeni fiyat belirtilmedi.")
                yeni_fiyat = float(yeni_fiyat_str)
                success, sonuc = simule_et_fiyat_degisikligi(urun_ismi, yeni_fiyat)
                
            elif analiz_tipi == 'optimum_fiyat':
                analiz_tipi_baslik = f"Optimum Fiyat ({urun_ismi})"
                success, sonuc, chart_data_dict = bul_optimum_fiyat(urun_ismi)
                # Chart.js için veriyi JSON string'e çevir
                chart_data = json.dumps(chart_data_dict) if success and chart_data_dict else None

            elif analiz_tipi == 'kategori':
                analiz_tipi_baslik = f"Kategori Analizi ({kategori_ismi} - {gun_sayisi} gün)"
                success, sonuc, chart_data_dict = analiz_et_kategori_veya_grup('kategori', kategori_ismi, gun_sayisi)
                chart_data = json.dumps(chart_data_dict) if success and chart_data_dict else None
                
            elif analiz_tipi == 'grup':
                analiz_tipi_baslik = f"Grup Analizi ({grup_ismi} - {gun_sayisi} gün)"
                success, sonuc, chart_data_dict = analiz_et_kategori_veya_grup('kategori_grubu', grup_ismi, gun_sayisi)
                chart_data = json.dumps(chart_data_dict) if success and chart_data_dict else None
            
            else:
                success, sonuc = False, "Geçersiz analiz tipi."

            analiz_sonucu = sonuc
            if not success:
                flash(sonuc, 'danger')
                chart_data = None # Hata varsa grafik gönderme

        except ValueError as ve:
             flash(f"Giriş hatası: {ve}", 'danger')
             analiz_sonucu = None
             chart_data = None
        except Exception as e:
            flash(f"Analiz sırasında beklenmedik bir hata oluştu: {e}", 'danger')
            analiz_sonucu = None
            chart_data = None

    return render_template('reports.html', title='Analiz Motorları',
                           urun_listesi=urun_listesi,
                           kategori_listesi=kategori_listesi,
                           grup_listesi=grup_listesi,
                           analiz_sonucu=analiz_sonucu,
                           chart_data=chart_data,
                           analiz_tipi_baslik=analiz_tipi_baslik)

# Render.com'un uygulamayı çalıştırması için
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Debug modunu Render'da kapatmayı unutma!
    # app.run(host='0.0.0.0', port=port, debug=True) 
    app.run(host='0.0.0.0', port=port)
