# app.py — RestoProfit (optimize edilmiş, tutarlı ve güvenli sürüm)
# NOT: Bu sürümde /add-recipe endpoint'i "çoklu satır" (r_hammadde_id[] / r_miktar[]) destekler.
# ✅ EK: Dashboard'da son X güne göre en iyi / en kötü 3 ürün (marj) listesi

import os
import re
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
from sqlalchemy import func, text, desc, asc
from sqlalchemy.orm import joinedload

# --- database.py içe aktarımları ---
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
        """Fallback: db.init_app"""
        db.init_app(app)


# --- analiz motorları ---
from analysis_engine import (
    hesapla_hedef_marj,
    simule_et_fiyat_degisikligi,
    bul_optimum_fiyat,
    analiz_et_kategori_veya_grup
)

EMOJI_RX = re.compile(r'[\U0001F300-\U0001FAFF\U00002700-\U000027BF]+', flags=re.UNICODE)


def strip_emojis(text: str) -> str:
    if not isinstance(text, str):
        return text
    return EMOJI_RX.sub('', text).strip()


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


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'DEGISTIRIN:dev-secret-key')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'static/uploads')

    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    SEND_FILE_MAX_AGE_DEFAULT = 86400  # 1 gün

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    init_db(app)

    bcrypt = Bcrypt(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'
    login_manager.login_message = "Bu sayfayı görüntülemek için lütfen giriş yapın."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

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

    @app.context_processor
    def inject_globals():
        return dict(current_user=current_user, site_name="RestoProfit")

    @app.after_request
    def set_security_headers(resp):
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'no-referrer-when-downgrade'
        resp.headers['Content-Security-Policy'] = (
            "default-src 'self' https: data: blob:; "
            "img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline' https:; "
            "script-src 'self' 'unsafe-inline' https:; "
            "font-src 'self' https: data:;"
        )
        return resp

    @app.errorhandler(413)
    def too_large(_e):
        flash("Yüklenen dosya çok büyük (16 MB sınır).", "danger")
        return redirect(url_for('dashboard'))

    @app.errorhandler(404)
    def not_found(_e):
        try:
            return render_template('errors/404.html', title='Bulunamadı'), 404
        except Exception:
            return render_template('base.html', title='Bulunamadı'), 404

    @app.errorhandler(500)
    def server_error(_e):
        try:
            return render_template('errors/500.html', title='Sunucu Hatası'), 500
        except Exception:
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

    @app.route('/healthz')
    def healthz():
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
            return send_from_directory(
                os.path.join(app.root_path, 'static'),
                'favicon.ico',
                mimetype='image/vnd.microsoft.icon'
            )
        return ('', 204)

    # -------------------------
    # AUTH
    # -------------------------
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

    # -------------------------
    # DASHBOARD HELPERS
    # -------------------------
    def _top_bottom_products_by_margin(days: int = 30, limit: int = 3):
        """
        Son X gün satışlarına göre ürün bazında:
        - toplam ciro, toplam kâr, toplam adet
        - marj % = (toplam_kâr / toplam_ciro) * 100
        ve en iyi/en kötü N ürünü döndürür.
        """
        days = max(1, min(int(days or 30), 3650))
        since_dt = datetime.now() - timedelta(days=days)

        ciro_sum = func.coalesce(func.sum(SatisKaydi.toplam_tutar), 0.0).label("ciro")
        kar_sum = func.coalesce(func.sum(SatisKaydi.hesaplanan_kar), 0.0).label("kar")
        adet_sum = func.coalesce(func.sum(SatisKaydi.adet), 0).label("adet")

        marj_expr = (
            (kar_sum / func.nullif(ciro_sum, 0.0)) * 100.0
        ).label("marj")

        base_q = (
            db.session.query(
                Urun.id.label("urun_id"),
                Urun.isim.label("urun_adi"),
                ciro_sum,
                kar_sum,
                adet_sum,
                marj_expr
            )
            .join(SatisKaydi, SatisKaydi.urun_id == Urun.id)
            .filter(SatisKaydi.tarih >= since_dt)
            .group_by(Urun.id, Urun.isim)
            .having(func.sum(SatisKaydi.toplam_tutar) > 0)
        )

        best_rows = base_q.order_by(desc(marj_expr), desc(kar_sum)).limit(limit).all()
        worst_rows = base_q.order_by(asc(marj_expr), asc(kar_sum)).limit(limit).all()

        def _to_dict(row):
            return {
                "urun_id": row.urun_id,
                "urun_adi": row.urun_adi,
                "ciro": float(row.ciro or 0.0),
                "kar": float(row.kar or 0.0),
                "adet": int(row.adet or 0),
                "marj": float(row.marj or 0.0),
            }

        return [*_map_safe(best_rows, _to_dict)], [*_map_safe(worst_rows, _to_dict)]

    def _map_safe(rows, fn):
        for r in rows or []:
            try:
                yield fn(r)
            except Exception:
                continue

    # -------------------------
    # DASHBOARD
    # -------------------------
    @app.route('/', endpoint='index')
    @app.route('/dashboard', endpoint='dashboard')
    @login_required
    def dashboard():
        # 🔧 İstersen URL'den gün sayısını değiştirebilirsin: /dashboard?days=7
        days_window = safe_int(request.args.get("days"), 30)
        if not days_window:
            days_window = 30
        days_window = max(1, min(days_window, 3650))

        try:
            toplam_satis_kaydi = db.session.query(SatisKaydi).count()
            toplam_urun = db.session.query(Urun).count()
            summary = {'toplam_satis_kaydi': toplam_satis_kaydi, 'toplam_urun': toplam_urun}
        except Exception as e:
            summary = {'toplam_satis_kaydi': 0, 'toplam_urun': 0}
            flash(f'Veritabanı bağlantı hatası: {e}', 'danger')

        # ✅ En iyi / En kötü 3 ürün
        best_products, worst_products = [], []
        try:
            best_products, worst_products = _top_bottom_products_by_margin(days=days_window, limit=3)
        except Exception as e:
            # Dashboard asla kırılmasın
            best_products, worst_products = [], []
            flash(f"Dashboard ürün analizi hesaplanamadı: {e}", "warning")

        return render_template(
            'dashboard.html',
            title='Ana Ekran',
            summary=summary,
            best_products=best_products,
            worst_products=worst_products,
            days_window=days_window
        )

    # Menü Yönetimi alias
    @app.route('/menu-yonetimi')
    @login_required
    def menu_yonetimi():
        return redirect(url_for('admin_panel'))

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
                        hatali_satirlar.append(idx + 2)
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

    # -------------------------
    # ADMIN PANEL
    # -------------------------
    @app.route('/admin')
    @login_required
    def admin_panel():
        page = request.args.get('page', default=1, type=int)
        per = request.args.get('per', default=25, type=int)

        try:
            hammaddeler = db.session.scalars(
                db.select(Hammadde).order_by(Hammadde.isim)
            ).all()

            urunler = db.session.scalars(
                db.select(Urun).order_by(Urun.isim)
            ).all()

            recete_stmt = (
                db.select(Recete)
                  .options(joinedload(Recete.urun), joinedload(Recete.hammadde))
                  .join(Urun, Urun.id == Recete.urun_id)
                  .join(Hammadde, Hammadde.id == Recete.hammadde_id)
                  .order_by(Urun.isim, Hammadde.isim)
            )

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
            flash(f"Menü Yönetimi yüklenirken hata: {e}", "danger")
            return render_template(
                'admin.html',
                title='Menü Yönetimi',
                hammaddeler=[],
                urunler=[],
                receteler=[],
                recete_pagination=None
            )

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
            flash(f"'{urun.isim}' silindi.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Silme hatası: {e}", 'danger')
        return redirect(url_for('admin_panel'))

    # ✅ FIX: Çoklu reçete satırı destekli add_recipe
    @app.route('/add-recipe', methods=['POST'])
    @login_required
    def add_recipe():
        urun_id = safe_int(request.form.get('r_urun_id'))
        if not urun_id:
            flash("Ürün seçimi zorunludur.", 'danger')
            return redirect(url_for('admin_panel'))

        hammadde_ids = request.form.getlist('r_hammadde_id[]')
        miktarlar = request.form.getlist('r_miktar[]')

        if not hammadde_ids and request.form.get('r_hammadde_id') is not None:
            hammadde_ids = [request.form.get('r_hammadde_id')]
            miktarlar = [request.form.get('r_miktar')]

        if not hammadde_ids:
            flash("En az 1 hammadde satırı eklemelisiniz.", 'danger')
            return redirect(url_for('admin_panel'))

        normalized: dict[int, float] = {}
        skipped = 0

        n = min(len(hammadde_ids), len(miktarlar)) if miktarlar else len(hammadde_ids)

        for i in range(n):
            hid = safe_int(hammadde_ids[i])
            mikt = parse_decimal(miktarlar[i] if miktarlar else None)

            if not hid or mikt is None or mikt <= 0:
                skipped += 1
                continue

            normalized[hid] = float(normalized.get(hid, 0.0) + float(mikt))

        if not normalized:
            flash("Geçerli bir hammadde/miktar satırı bulunamadı.", 'danger')
            return redirect(url_for('admin_panel'))

        try:
            urun = db.session.get(Urun, urun_id)
            if not urun:
                flash("Seçilen ürün bulunamadı.", 'danger')
                return redirect(url_for('admin_panel'))

            valid_h_ids = set(
                db.session.scalars(
                    db.select(Hammadde.id).where(Hammadde.id.in_(list(normalized.keys())))
                ).all()
            )
            if not valid_h_ids:
                flash("Seçilen hammaddeler bulunamadı.", 'danger')
                return redirect(url_for('admin_panel'))

            missing_ids = [hid for hid in normalized.keys() if hid not in valid_h_ids]
            for hid in missing_ids:
                normalized.pop(hid, None)
                skipped += 1

            if not normalized:
                flash("Geçerli hammadde kalmadı.", 'danger')
                return redirect(url_for('admin_panel'))

            added = 0
            updated = 0

            existing_rows = db.session.scalars(
                db.select(Recete).where(
                    Recete.urun_id == urun_id,
                    Recete.hammadde_id.in_(list(normalized.keys()))
                )
            ).all()
            existing_map = {r.hammadde_id: r for r in existing_rows}

            for hid, mikt in normalized.items():
                if hid in existing_map:
                    existing_map[hid].miktar = float(mikt)
                    updated += 1
                else:
                    db.session.add(Recete(urun_id=urun_id, hammadde_id=hid, miktar=float(mikt)))
                    added += 1

            db.session.commit()
            guncelle_tum_urun_maliyetleri()

            msg = f"Reçete kaydedildi. Eklenen: {added}, Güncellenen: {updated}."
            if skipped:
                msg += f" Atlanan satır: {skipped}."
            flash(msg, 'success')

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

    @app.route('/delete-sales-by-date', methods=['POST'])
    @login_required
    def delete_sales_by_date():
        date_str = request.form.get('delete_date')
        if not date_str:
            flash("Silmek için geçerli bir tarih seçin.", 'danger')
            return redirect(url_for('admin_panel'))
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            num_deleted = (
                db.session.query(SatisKaydi)
                .filter(func.date(SatisKaydi.tarih) == target_date)
                .delete(synchronize_session=False)
            )
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

    # -------------------------
    # REPORTS / ANALYSIS
    # -------------------------
    @app.route('/reports', methods=['GET', 'POST'])
    @login_required
    def reports():
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

        analiz_sonucu = None
        chart_data = None
        analiz_tipi_baslik = ""
        analiz_tipi = None

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

        return render_template(
            'reports.html',
            title='Analiz Motorları',
            urun_listesi=urun_listesi,
            kategori_listesi=kategori_listesi,
            grup_listesi=grup_listesi,
            analiz_sonucu=analiz_sonucu,
            analiz_sonucu_clean=strip_emojis(analiz_sonucu) if analiz_sonucu else None,
            chart_data=chart_data,
            analiz_tipi_baslik=analiz_tipi_baslik,
            aktif_analiz_tipi=analiz_tipi if request.method == 'POST' else None
        )

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=bool(os.environ.get('FLASK_DEBUG')))
