# app.py — RestoProfit (optimize edilmiş, tutarlı ve güvenli sürüm)

import os
from datetime import datetime, timedelta
import pandas as pd

from flask import (
    Flask, render_template, render_template_string, request,
    redirect, url_for, flash, send_from_directory
)

from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)

from sqlalchemy import func, text
from sqlalchemy.orm import joinedload

# --- database.py içe aktarımları ---
# Bazı projelerde init_db tanımlı değilse fallback yapıyoruz.
try:
    from database import (
        db, init_db, Hammadde, Urun, Recete, SatisKaydi, User,
        guncelle_tum_urun_maliyetleri
    )
except ImportError:
    from database import (
        db, Hammadde, Urun, Recete, SatisKaydi, User,
        guncelle_tum_urun_maliyetleri
    )

    def init_db(app):
        """Fallback: database.init_app + DATABASE_URL var ise bağlanır."""
        db.init_app(app)


# --- analiz motorları ---
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

# -----------------------------------------------------------------------------
# Yardımcılar
# -----------------------------------------------------------------------------
def parse_decimal(value: str, default=None):
    """Virgüllü/noktalı ondalıkları güvenle float'a çevirir."""
    if value is None:
        return default
    try:
        return float(str(value).strip().replace(',', '.'))
    except (ValueError, TypeError):
        return default

def safe_int(value, default=None):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

# -----------------------------------------------------------------------------
# Uygulama Yapılandırması
# -----------------------------------------------------------------------------
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'DEGISTIRIN:dev-secret-key')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB upload limiti
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'static/uploads')

    # Prod güvenlik/oturum (CDN kullandığımız için CSP gevşek)
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    SEND_FILE_MAX_AGE_DEFAULT = 86400  # 1 gün

    # SQLAlchemy: Render kopmalarına karşı güvenli havuz
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Upload klasörü
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # DB init (DATABASE_URL varsa Postgres, yoksa sqlite fallback)
    init_db(app)

    # Auth
    bcrypt = Bcrypt(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'
    login_manager.login_message = "Bu sayfayı görüntülemek için lütfen giriş yapın."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # İlk admin (ENV ile override edilebilir)
    with app.app_context():
        db.create_all()
        if not User.query.first():
            admin_user = os.environ.get('ADMIN_USER', 'onur')
            admin_pass = os.environ.get('ADMIN_PASS', 'RestoranSifrem!2025')
            try:
                hashed_password = bcrypt.generate_password_hash(admin_pass).decode('utf-8')
                db.session.add(User(username=admin_user, password_hash=hashed_password))
                db.session.commit()
                print(f"[INIT] Admin oluşturuldu -> kullanıcı: {admin_user}")
            except Exception as e:
                db.session.rollback()
                print(f"[INIT] Admin oluşturulamadı: {e}")

    # Global template değişkenleri
    @app.context_processor
    def inject_globals():
        return dict(current_user=current_user, site_name="RestoProfit")

    # Güvenlik başlıkları
    @app.after_request
    def set_security_headers(resp):
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'no-referrer-when-downgrade'
        # CDN’lerle uyumlu, temel CSP
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self' https: data: blob:; "
            "img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "script-src 'self' 'unsafe-inline' https:; "
            "font-src 'self' https: data:;"
        )
        return resp

    # -----------------------------------------------------------------------------
    # Hata Sayfaları
    # -----------------------------------------------------------------------------
    @app.errorhandler(413)
    def too_large(_e):
        flash("Yüklenen dosya çok büyük (16 MB sınır).", "danger")
        return redirect(url_for('dashboard'))

    @app.errorhandler(404)
    def not_found(_e):
        # Ayrı bir errors/404.html yoksa base.html ile boş sayfa render
        try:
            return render_template('errors/404.html', title='Bulunamadı'), 404
        except Exception:
            return render_template('base.html', title='Bulunamadı'), 404

    @app.errorhandler(500)
    def server_error(e):
        # burada log da atılabilir: app.logger.exception(e)
        try:
            return render_template('errors/500.html', title='Sunucu Hatası'), 500
        except Exception:
            # Temel, geri-dönüş render
            html = """
            {% extends 'base.html' %}
            {% block content %}
              <div class="container">
                <div class="alert alert-danger" role="alert">
                  Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin.
                </div>
              </div>
            {% endblock %}
            """
            return render_template_string(html), 500

    # -----------------------------------------------------------------------------
    # Yardımcı Servis Uçları
    # -----------------------------------------------------------------------------
    @app.route('/healthz')
    def healthz():
        # DB ping (SQLAlchemy 2.x uyumlu)
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            return ("db_fail", 500)
        return ("ok", 200)

    @app.route('/robots.txt')
    def robots_txt():
        return (
            "User-agent: *\n"
            "Disallow:\n",
            200,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    @app.route('/favicon.ico')
    def favicon():
        static_fav = os.path.join(app.root_path, 'static', 'favicon.ico')
        if os.path.exists(static_fav):
            return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico',
                                       mimetype='image/vnd.microsoft.icon')
        return ('', 204)

    # -----------------------------------------------------------------------------
    # ROUTES
    # -----------------------------------------------------------------------------

    # Giriş
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and bcrypt.check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for('dashboard'))
            flash('Kullanıcı adı veya şifre hatalı.', 'danger')

        return render_template('login.html', title='Giriş Yap')

    # Çıkış
    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Başarıyla çıkış yaptınız.', 'info')
        return redirect(url_for('login'))

    # Şifre Değiştir (tek sürüm)
    @app.route('/change-password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        if request.method == 'POST':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password or not bcrypt.check_password_hash(current_user.password_hash, current_password):
                flash('Mevcut şifreniz hatalı.', 'danger')
                return redirect(url_for('change_password'))

            if new_password != confirm_password:
                flash('Yeni şifreler birbiriyle eşleşmiyor.', 'danger')
                return redirect(url_for('change_password'))

            if not new_password or len(new_password) < 6:
                flash('Yeni şifreniz en az 6 karakter olmalıdır.', 'danger')
                return redirect(url_for('change_password'))

            try:
                hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
                current_user.password_hash = hashed_password
                db.session.commit()
                flash('Şifreniz güncellendi. Lütfen tekrar giriş yapın.', 'success')
                return redirect(url_for('logout'))
            except Exception as e:
                db.session.rollback()
                flash(f"Şifre güncellenirken hata: {e}", 'danger')
                return redirect(url_for('change_password'))

        return render_template('change_password.html', title='Şifre Değiştir')

    # Ana ekran
    @app.route('/')
    @login_required
    def dashboard():
        try:
            toplam_satis_kaydi = db.session.query(SatisKaydi).count()
            toplam_urun = db.session.query(Urun).count()
            summary = {'toplam_satis_kaydi': toplam_satis_kaydi, 'toplam_urun': toplam_urun}
        except Exception as e:
            summary = {'toplam_satis_kaydi': 0, 'toplam_urun': 0}
            flash(f'Veritabanı bağlantı hatası: {e}', 'danger')

        return render_template('dashboard.html', title='Ana Ekran', summary=summary)

    # Excel yükleme
    @app.route('/upload-excel', methods=['POST'])
    @login_required
    def upload_excel():
        file = request.files.get('excel_file')
        if not file or file.filename == '':
            flash('Excel dosyası seçilmedi.', 'danger')
            return redirect(url_for('dashboard'))

        if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            flash('Desteklenmeyen dosya türü. Lütfen .xlsx / .xls yükleyin.', 'danger')
            return redirect(url_for('dashboard'))

        try:
            df = pd.read_excel(file)
            required_columns = ['Urun_Adi', 'Adet', 'Toplam_Tutar', 'Tarih']
            missing = [c for c in required_columns if c not in df.columns]
            if missing:
                raise ValueError(f"Excel'de eksik kolon(lar): {', '.join(missing)}")

            # Ürün id & maliyet haritaları
            urunler_db = Urun.query.all()
            urun_eslestirme = {u.excel_adi: u.id for u in urunler_db}
            urun_maliyet = {u.id: (u.hesaplanan_maliyet or 0.0) for u in urunler_db}

            yeni_kayitlar = []
            taninmayan = set()
            hatali_satirlar = []

            for idx, row in df.iterrows():
                try:
                    excel_adi = str(row['Urun_Adi']).strip()
                    adet = safe_int(row['Adet'])
                    toplam_tutar = parse_decimal(row['Toplam_Tutar'])
                    tarih = pd.to_datetime(row['Tarih'], errors='coerce')

                    if excel_adi == '' or adet is None or adet <= 0 or toplam_tutar is None or toplam_tutar < 0 or pd.isna(tarih):
                        hatali_satirlar.append(idx + 2)  # başlık satırı offset
                        continue

                    urun_id = urun_eslestirme.get(excel_adi)
                    if not urun_id:
                        taninmayan.add(excel_adi)
                        continue

                    maliyet = urun_maliyet.get(urun_id, 0.0)
                    hesaplanan_toplam_maliyet = maliyet * adet
                    hesaplanan_kar = toplam_tutar - hesaplanan_toplam_maliyet
                    hesaplanan_birim_fiyat = (toplam_tutar / adet) if adet else 0.0

                    yeni_kayitlar.append(SatisKaydi(
                        urun_id=urun_id,
                        tarih=tarih,
                        adet=adet,
                        toplam_tutar=toplam_tutar,
                        hesaplanan_birim_fiyat=hesaplanan_birim_fiyat,
                        hesaplanan_maliyet=hesaplanan_toplam_maliyet,
                        hesaplanan_kar=hesaplanan_kar
                    ))
                except Exception:
                    hatali_satirlar.append(idx + 2)
                    continue

            if yeni_kayitlar:
                db.session.add_all(yeni_kayitlar)
                db.session.commit()
                flash(f'Başarılı! {len(yeni_kayitlar)} satış kaydı işlendi.', 'success')
            else:
                flash('İşlenecek geçerli satış kaydı bulunamadı.', 'warning')

            if taninmayan:
                flash("Bulunamayan ürün(ler): " + ", ".join(sorted(taninmayan)), 'warning')
            if hatali_satirlar:
                flash("Atlanan satırlar: " + ", ".join(map(str, sorted(set(hatali_satirlar)))), 'warning')

        except ValueError as ve:
            flash(f"Giriş hatası: {ve}", 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f"Beklenmedik hata: {e}. Lütfen Excel formatını kontrol edin.", 'danger')

        return redirect(url_for('dashboard'))

    # --- YÖNETİM PANELİ (CRUD) ---
    @app.route('/admin')
    @login_required
    def admin_panel():
        # URL parametreleri: ?page=2&per=25 gibi
        page = request.args.get('page', default=1, type=int)
        per = request.args.get('per', default=25, type=int)

        try:
            # Flask-SQLAlchemy 3.x uyumlu SELECT + scalars()
            hammaddeler = db.session.scalars(
                db.select(Hammadde).order_by(Hammadde.isim)
            ).all()

            urunler = db.session.scalars(
                db.select(Urun).order_by(Urun.isim)
            ).all()

            # Reçeteler için joinedload + SELECT
            recete_stmt = (
                db.select(Recete)
                  .options(
                      joinedload(Recete.urun),
                      joinedload(Recete.hammadde),
                  )
                  .join(Urun, Urun.id == Recete.urun_id)
                  .join(Hammadde, Hammadde.id == Recete.hammadde_id)
                  .order_by(Urun.isim, Hammadde.isim)
            )

            # Flask-SQLAlchemy 3.x’te paginate bu şekilde
            recete_pagination = db.paginate(
                recete_stmt,
                page=page,
                per_page=per,
                error_out=False
            )
            receteler = recete_pagination.items

            return render_template(
                'admin.html',
                title='Menü Yönetimi',
                hammaddeler=hammaddeler,
                urunler=urunler,
                receteler=receteler,
                recete_pagination=recete_pagination
            )

        except Exception as e:
            db.session.rollback()
            # Hata durumunda boş listelerle sayfayı aç, mesaj göster
            flash(f"Menü Yönetimi yüklenirken hata: {e}", "danger")
            return render_template(
                'admin.html',
                title='Menü Yönetimi',
                hammaddeler=[],
                urunler=[],
                receteler=[],
                recete_pagination=None
            )

    # --- Hammadde CRUD ---
    @app.route('/add-material', methods=['POST'])
    @login_required
    def add_material():
        isim = (request.form.get('h_isim') or '').strip()
        birim = (request.form.get('h_birim') or '').strip()
        fiyat = parse_decimal(request.form.get('h_fiyat'))

        if not isim or not birim or fiyat is None:
            flash("Tüm hammadde alanlarını doldurun.", 'danger')
            return redirect(url_for('admin_panel'))
        if fiyat <= 0:
            flash("Hammadde fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            db.session.add(Hammadde(isim=isim, maliyet_birimi=birim, maliyet_fiyati=fiyat))
            db.session.commit()
            flash(f"'{isim}' eklendi.", 'success')
        except Exception as e:
            db.session.rollback()
            # IntegrityError dahil tüm hatalar
            if 'UNIQUE' in str(e).upper():
                flash(f"'{isim}' zaten mevcut.", 'danger')
            else:
                flash(f"Hammadde eklenemedi: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/edit-material/<int:id>', methods=['POST'])
    @login_required
    def edit_material(id):
        h = db.session.get(Hammadde, id)
        if not h:
            flash('Hammadde bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))

        isim = (request.form.get('isim') or '').strip()
        birim = (request.form.get('birim') or '').strip()
        fiyat = parse_decimal(request.form.get('fiyat'))

        if not isim or not birim or fiyat is None:
            flash("Tüm hammadde alanlarını doldurun.", 'danger')
            return redirect(url_for('admin_panel'))
        if fiyat <= 0:
            flash("Hammadde fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            # isim çakışması kontrolü
            exists = db.session.scalar(
                db.select(Hammadde).where(Hammadde.isim == isim, Hammadde.id != id)
            )
            if exists:
                flash(f"'{isim}' adında başka bir hammadde var.", 'danger')
                return redirect(url_for('admin_panel'))

            h.isim = isim
            h.maliyet_birimi = birim
            h.maliyet_fiyati = fiyat
            db.session.commit()
            guncelle_tum_urun_maliyetleri()
            flash(f"'{h.isim}' güncellendi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Güncellenemedi: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/delete-material/<int:id>', methods=['POST'])
    @login_required
    def delete_material(id):
        h = db.session.get(Hammadde, id)
        if not h:
            flash("Hammadde bulunamadı.", 'warning')
            return redirect(url_for('admin_panel'))

        try:
            # ilişkili reçete var mı?
            linked = db.session.scalar(
                db.select(Recete).where(Recete.hammadde_id == id).limit(1)
            )
            if linked:
                flash(f"'{h.isim}' bir reçetede kullanıldığı için silinemez. Önce ilgili reçeteleri kaldırın.", 'danger')
                return redirect(url_for('admin_panel'))

            db.session.delete(h)
            db.session.commit()
            flash(f"'{h.isim}' silindi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Silme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    # --- Ürün CRUD ---
    @app.route('/add-product', methods=['POST'])
    @login_required
    def add_product():
        isim = (request.form.get('u_isim') or '').strip()
        excel_adi = (request.form.get('u_excel_adi') or '').strip()
        fiyat = parse_decimal(request.form.get('u_fiyat'))
        kategori = (request.form.get('u_kategori') or '').strip()
        grup = (request.form.get('u_grup') or '').strip()

        if not all([isim, excel_adi, fiyat is not None, kategori, grup]):
            flash("Tüm ürün alanlarını doldurun.", 'danger')
            return redirect(url_for('admin_panel'))
        if fiyat <= 0:
            flash("Ürün fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            urun = Urun(
                isim=isim, excel_adi=excel_adi, mevcut_satis_fiyati=fiyat,
                kategori=kategori, kategori_grubu=grup, hesaplanan_maliyet=0.0
            )
            db.session.add(urun)
            db.session.commit()
            flash(f"'{isim}' eklendi. Şimdi reçetesini oluşturun.", 'success')
        except Exception as e:
            db.session.rollback()
            if 'UNIQUE' in str(e).upper():
                flash(f"'{isim}' veya Excel adı '{excel_adi}' zaten mevcut.", 'danger')
            else:
                flash(f"Ürün eklenemedi: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/edit-product/<int:id>', methods=['POST'])
    @login_required
    def edit_product(id):
        urun = db.session.get(Urun, id)
        if not urun:
            flash('Ürün bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))

        isim = (request.form.get('isim') or '').strip()
        excel_adi = (request.form.get('excel_adi') or '').strip()
        fiyat = parse_decimal(request.form.get('fiyat'))
        kategori = (request.form.get('kategori') or '').strip()
        grup = (request.form.get('grup') or '').strip()

        if not all([isim, excel_adi, fiyat is not None, kategori, grup]):
            flash("Tüm ürün alanlarını doldurun.", 'danger')
            return redirect(url_for('admin_panel'))
        if fiyat <= 0:
            flash("Ürün fiyatı pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            # isim ve excel_adi çakışmaları
            exists_name = db.session.scalar(
                db.select(Urun).where(Urun.isim == isim, Urun.id != id)
            )
            if exists_name:
                flash(f"'{isim}' adında başka bir ürün var.", 'danger')
                return redirect(url_for('admin_panel'))

            exists_excel = db.session.scalar(
                db.select(Urun).where(Urun.excel_adi == excel_adi, Urun.id != id)
            )
            if exists_excel:
                flash(f"'{excel_adi}' Excel adına sahip başka bir ürün var.", 'danger')
                return redirect(url_for('admin_panel'))

            urun.isim = isim
            urun.excel_adi = excel_adi
            urun.mevcut_satis_fiyati = fiyat
            urun.kategori = kategori
            urun.kategori_grubu = grup
            db.session.commit()
            guncelle_tum_urun_maliyetleri()
            flash(f"'{urun.isim}' güncellendi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Ürün güncellenemedi: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/delete-product/<int:id>', methods=['POST'])
    @login_required
    def delete_product(id):
        urun = db.session.get(Urun, id)
        if not urun:
            flash("Ürün bulunamadı.", 'warning')
            return redirect(url_for('admin_panel'))

        try:
            db.session.delete(urun)
            db.session.commit()
            flash(f"'{urun.isim}' ve ilgili kayıtlar silindi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Silme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    # --- Reçete CRUD ---
    @app.route('/add-recipe', methods=['POST'])
    @login_required
    def add_recipe():
        urun_id = safe_int(request.form.get('r_urun_id'))
        hammadde_id = safe_int(request.form.get('r_hammadde_id'))
        miktar = parse_decimal(request.form.get('r_miktar'))

        if not urun_id or not hammadde_id or miktar is None:
            flash("Ürün, hammadde ve miktar zorunludur.", 'danger')
            return redirect(url_for('admin_panel'))
        if miktar <= 0:
            flash("Miktar pozitif olmalıdır.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            existing = db.session.scalar(
                db.select(Recete).where(Recete.urun_id == urun_id, Recete.hammadde_id == hammadde_id)
            )
            if existing:
                existing.miktar = miktar
                flash("Reçete kalemi mevcuttu, miktar güncellendi.", 'warning')
            else:
                db.session.add(Recete(urun_id=urun_id, hammadde_id=hammadde_id, miktar=miktar))
                flash("Reçete kalemi eklendi.", 'success')

            db.session.commit()
            guncelle_tum_urun_maliyetleri()
        except Exception as e:
            db.session.rollback()
            flash(f"Reçete hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/edit-recipe/<int:id>', methods=['POST'])
    @login_required
    def edit_recipe(id):
        rec = db.session.get(Recete, id)
        if not rec:
            flash('Reçete kalemi bulunamadı.', 'danger')
            return redirect(url_for('admin_panel'))

        miktar = parse_decimal(request.form.get('edit_r_miktar'))
        if miktar is None or miktar <= 0:
            flash("Geçerli bir miktar girin.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            rec.miktar = miktar
            db.session.commit()
            guncelle_tum_urun_maliyetleri()
            flash(f"'{rec.urun.isim}' / '{rec.hammadde.isim}' miktarı güncellendi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Güncelleme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    @app.route('/delete-recipe/<int:id>', methods=['POST'])
    @login_required
    def delete_recipe(id):
        rec = db.session.get(Recete, id)
        if not rec:
            flash("Reçete kalemi bulunamadı.", 'warning')
            return redirect(url_for('admin_panel'))

        try:
            urun_adi = rec.urun.isim
            hammadde_adi = rec.hammadde.isim
            db.session.delete(rec)
            db.session.commit()
            guncelle_tum_urun_maliyetleri()
            flash(f"'{urun_adi}' ürününden '{hammadde_adi}' kalemi silindi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Silme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    # --- Veriyi Yönet ---
    @app.route('/delete-sales-by-date', methods=['POST'])
    @login_required
    def delete_sales_by_date():
        date_str = request.form.get('delete_date')
        if not date_str:
            flash("Silmek için geçerli bir tarih seçin.", 'danger')
            return redirect(url_for('admin_panel'))
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            num_deleted = (db.session.query(SatisKaydi)
                           .filter(func.date(SatisKaydi.tarih) == target_date)
                           .delete(synchronize_session=False))
            db.session.commit()
            if num_deleted > 0:
                flash(f"{target_date.strftime('%d %B %Y')} tarihindeki {num_deleted} satış kaydı silindi.", 'success')
            else:
                flash(f"{target_date.strftime('%d %B %Y')} tarihinde satış kaydı bulunamadı.", 'info')
        except ValueError:
            flash("Geçersiz tarih formatı.", 'danger')
        except Exception as e:
            db.session.rollback()
            flash(f"Silme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))


    # --- RAPORLAR / ANALİZ MOTORLARI ---
    @app.route('/reports', methods=['GET', 'POST'])
    @login_required
    def reports():
        # Seçim listeleri (ürün / kategori / grup)
        try:
            urunler_db = Urun.query.order_by(Urun.isim).all()
            urun_listesi = [u.isim for u in urunler_db]

            kategoriler_db = (
                db.session.query(Urun.kategori)
                .distinct()
                .order_by(Urun.kategori)
                .all()
            )
            kategori_listesi = sorted([k[0] for k in kategoriler_db if k[0]])

            gruplar_db = (
                db.session.query(Urun.kategori_grubu)
                .distinct()
                .order_by(Urun.kategori_grubu)
                .all()
            )
            grup_listesi = sorted([g[0] for g in gruplar_db if g[0]])

        except Exception as e:
            flash(f'Veritabanından listeler çekilirken hata: {e}', 'danger')
            urun_listesi, kategori_listesi, grup_listesi = [], [], []

        # Çıktı & durum değişkenleri
        analiz_sonucu = None
        chart_data = None
        analiz_tipi_baslik = ""
        analiz_tipi = None  # güvenli varsayılan

        # POST ise analiz yap
        if request.method == 'POST':
            try:
                analiz_tipi = request.form.get('analiz_tipi')
                urun_ismi = request.form.get('urun_ismi')
                kategori_ismi = request.form.get('kategori_ismi')
                grup_ismi = request.form.get('grup_ismi')
                gun_sayisi = safe_int(request.form.get('gun_sayisi'), 7)

                if analiz_tipi == 'hedef_marj':
                    if not urun_ismi:
                        raise ValueError("Lütfen bir ürün seçin.")
                    hedef_marj = parse_decimal(request.form.get('hedef_marj'))
                    if hedef_marj is None:
                        raise ValueError("Lütfen bir hedef marj girin.")
                    analiz_tipi_baslik = f"Hedef Marj: {urun_ismi}"
                    success, sonuc, chart_json = hesapla_hedef_marj(urun_ismi, hedef_marj)
                    analiz_sonucu, chart_data = sonuc, chart_json

                elif analiz_tipi == 'simulasyon':
                    if not urun_ismi:
                        raise ValueError("Lütfen bir ürün seçin.")
                    yeni_fiyat = parse_decimal(request.form.get('yeni_fiyat'))
                    if yeni_fiyat is None:
                        raise ValueError("Lütfen geçerli bir fiyat girin.")
                    analiz_tipi_baslik = f"Fiyat Simülasyonu: {urun_ismi}"
                    success, sonuc, chart_json = simule_et_fiyat_degisikligi(urun_ismi, yeni_fiyat)
                    analiz_sonucu, chart_data = sonuc, chart_json

                elif analiz_tipi == 'optimum_fiyat':
                    if not urun_ismi:
                        raise ValueError("Lütfen bir ürün seçin.")
                    analiz_tipi_baslik = f"Optimum Fiyat: {urun_ismi}"
                    success, sonuc, chart_json = bul_optimum_fiyat(urun_ismi)
                    analiz_sonucu, chart_data = sonuc, chart_json

                elif analiz_tipi == 'kategori':
                    if not kategori_ismi:
                        raise ValueError("Lütfen bir kategori seçin.")
                    analiz_tipi_baslik = f"Kategori Analizi: {kategori_ismi} ({gun_sayisi} gün)"
                    success, sonuc, chart_json = analiz_et_kategori_veya_grup('kategori', kategori_ismi, gun_sayisi)
                    analiz_sonucu, chart_data = sonuc, chart_json

                elif analiz_tipi == 'grup':
                    if not grup_ismi:
                        raise ValueError("Lütfen bir grup seçin.")
                    analiz_tipi_baslik = f"Grup Analizi: {grup_ismi} ({gun_sayisi} gün)"
                    success, sonuc, chart_json = analiz_et_kategori_veya_grup('kategori_grubu', grup_ismi, gun_sayisi)
                    analiz_sonucu, chart_data = sonuc, chart_json

                else:
                    success, analiz_sonucu = False, "Geçersiz analiz tipi."

                if not success:
                    flash(analiz_sonucu, 'danger')
                    chart_data = None

            except ValueError as ve:
                flash(f"Giriş hatası: {ve}", 'danger')
                analiz_sonucu = None
                chart_data = None
            except Exception as e:
                db.session.rollback()
                flash(f"Analiz sırasında beklenmedik hata: {e}", 'danger')
                analiz_sonucu = None
                chart_data = None

        # SAYFA DÖNÜŞÜ
        return render_template(
            'reports.html',
            title='Analiz Motorları',
            urun_listesi=urun_listesi,
            kategori_listesi=kategori_listesi,
            grup_listesi=grup_listesi,
            analiz_sonucu=analiz_sonucu,
            chart_data=chart_data,
            analiz_tipi_baslik=analiz_tipi_baslik,
            aktif_analiz_tipi=analiz_tipi if request.method == 'POST' else None
        )


    # Flask run (lokal) / Render (gunicorn) uyumlu dönüş
    return app


# --- Ana uygulama çalıştırma ---
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Prod’da gunicorn kullanın; bu sadece lokal geliştirme içindir
    app.run(host='0.0.0.0', port=port, debug=bool(os.environ.get('FLASK_DEBUG')))
