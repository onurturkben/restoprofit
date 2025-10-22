# database.py
# Bu dosya, veritabanı modellerimizi (tabloları) ve
# veritabanını kuran/yöneten fonksiyonları içerir.
# SQLAlchemy kullanarak Colab'deki SQLite'tan PostgreSQL'e geçiş yapıyoruz.

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, text
import os # Ortam değişkenlerini (DATABASE_URL) okumak için
from flask_login import UserMixin

# SQLAlchemy veritabanı nesnesini oluştur
db = SQLAlchemy()

# --- MODELLER (Colab'deki Tablolarımızın Profesyonel Hali) ---
# Bu modeller, Python class'ları ile veritabanı tabloları arasında köprü kurar.

class Hammadde(db.Model):
    __tablename__ = 'hammaddeler'
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(100), unique=True, nullable=False)
    maliyet_birimi = db.Column(db.String(20))
    maliyet_fiyati = db.Column(db.Float, nullable=False)
    guncellenme_tarihi = db.Column(db.DateTime, server_default=db.func.now())
    # Geriye dönük ilişki: Bu hammadde hangi reçetelerde var?
    receteler = db.relationship('Recete', back_populates='hammadde')

class Urun(db.Model):
    __tablename__ = 'urunler'
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(100), unique=True, nullable=False)
    excel_adi = db.Column(db.String(100)) # Adisyon'daki adıyla eşleştirmek için
    mevcut_satis_fiyati = db.Column(db.Float, nullable=False)
    hesaplanan_maliyet = db.Column(db.Float, default=0.0)
    kategori = db.Column(db.String(100))
    kategori_grubu = db.Column(db.String(100))
    # Geriye dönük ilişkiler
    receteler = db.relationship('Recete', back_populates='urun', cascade="all, delete-orphan")
    satislar = db.relationship('SatisKaydi', back_populates='urun', cascade="all, delete-orphan")

class Recete(db.Model):
    __tablename__ = 'receteler'
    id = db.Column(db.Integer, primary_key=True)
    miktar = db.Column(db.Float, nullable=False)
    # İlişkiler (Hangi ürüne ait, hangi hammaddeyi kullanıyor)
    urun_id = db.Column(db.Integer, db.ForeignKey('urunler.id'), nullable=False)
    hammadde_id = db.Column(db.Integer, db.ForeignKey('hammaddeler.id'), nullable=False)
    # Python tarafından erişim için
    urun = db.relationship('Urun', back_populates='receteler')
    hammadde = db.relationship('Hammadde', back_populates='receteler')

class SatisKaydi(db.Model):
    __tablename__ = 'satis_kayitlari'
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.DateTime, nullable=False)
    adet = db.Column(db.Integer, nullable=False)
    toplam_tutar = db.Column(db.Float, nullable=False)
    hesaplanan_birim_fiyat = db.Column(db.Float)
    # TEKNİK BORÇ ÇÖZÜMÜ: Maliyet "o anki" haliyle buraya kilitlenir
    hesaplanan_maliyet = db.Column(db.Float) 
    hesaplanan_kar = db.Column(db.Float)
    # İlişki
    urun_id = db.Column(db.Integer, db.ForeignKey('urunler.id'), nullable=False)
    urun = db.relationship('Urun', back_populates='satislar')

# --- YÖNETİM FONKSİYONLARI ---

def init_db(app):
    """ Veritabanını Flask uygulamasına bağlar ve tabloları oluşturur. """
    # Render.com'un bize vereceği DATABASE_URL'i al
    # Eğer yoksa, test için geçici bir 'restoran.db' dosyası oluştur (Colab'deki gibi)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///restoran.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    
    with app.app_context():
        print("Veritabanı yapısı kontrol ediliyor...")
        db.create_all() # Sadece "yoksa" oluşturur (DROP/SİLMEZ)
        print("Veritabanı yapısı hazır.")

def guncelle_tum_urun_maliyetleri():
    """ 
    (Colab Hücre 3'ten)
    Tüm ürünlerin maliyetlerini reçetelere göre günceller.
    Bu artık web arayüzünden tetiklenecek.
    """
    try:
        urunler = Urun.query.all()
        for urun in urunler:
            toplam_maliyet = 0.0
            for recete_kalemi in urun.receteler:
                # recete_kalemi.hammadde -> İlişki sayesinde Hammadde tablosuna ulaşır
                if recete_kalemi.hammadde:
                    kalem_maliyeti = recete_kalemi.miktar * recete_kalemi.hammadde.maliyet_fiyati
                    toplam_maliyet += kalem_maliyeti
            
            urun.hesaplanan_maliyet = toplam_maliyet
        
        db.session.commit()
        print("Tüm ürün maliyetleri yeniden hesaplandı.")
        return True, "Tüm ürün maliyetleri başarıyla güncellendi."
    except Exception as e:
        db.session.rollback()
        print(f"Maliyet güncelleme hatası: {e}")
        return False, f"Maliyet güncelleme hatası: {e}"

def menuyu_sifirla_ve_kur(hammaddeler_data, urunler_data, receteler_data):
    """
    (Colab Hücre 3'ün GÜVENLİ versiyonu)
    SADECE Menü, Reçete ve Maliyetleri sıfırlar.
    SATIS_KAYITLARI'na dokunmaz.
    """
    try:
        # 1. Eski menüyü temizle (SATIŞLARA DOKUNMA)
        db.session.execute(text('DELETE FROM receteler'))
        db.session.execute(text('DELETE FROM urunler'))
        db.session.execute(text('DELETE FROM hammaddeler'))
        
        # 2. Yeni hammaddeleri ekle
        hammadde_map = {} # ID'leri hızlı bulmak için
        for h_data in hammaddeler_data:
            h = Hammadde(isim=h_data[0], maliyet_birimi=h_data[1], maliyet_fiyati=h_data[2])
            db.session.add(h)
            hammadde_map[h.isim] = h
            
        # 3. Yeni ürünleri ekle
        urun_map = {} # ID'leri hızlı bulmak için
        for u_data in urunler_data:
            u = Urun(isim=u_data[0], excel_adi=u_data[1], mevcut_satis_fiyati=u_data[2], kategori=u_data[3], kategori_grubu=u_data[4])
            db.session.add(u)
            urun_map[u.isim] = u

        # Veritabanına ID'lerin oluşması için commit et
        db.session.flush() 

        # 4. Yeni reçeteleri ekle
        for r_data in receteler_data:
            urun_adi, hammadde_adi, miktar = r_data
            urun_obj = urun_map.get(urun_adi)
            hammadde_obj = hammadde_map.get(hammadde_adi)
            
            if urun_obj and hammadde_obj:
                r = Recete(urun=urun_obj, hammadde=hammadde_obj, miktar=miktar)
                db.session.add(r)
            else:
                print(f"UYARI: Reçete için eşleşme bulunamadı - Ürün: {urun_adi}, Hammadde: {hammadde_adi}")

        # 5. Tüm işlemleri onayla
        db.session.commit()
        
        # 6. Maliyetleri hesapla
        guncelle_tum_urun_maliyetleri()
        
        return True, "Menü, Reçeteler ve Maliyetler başarıyla sıfırlandı ve güncellendi. Satış geçmişiniz korundu."
    
    except Exception as e:
        db.session.rollback() # Hata olursa tüm işlemleri geri al
        return False, f"Menü sıfırlama hatası: {e}"
