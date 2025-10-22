# database.py (Düzeltilmiş - Render PostgreSQL için)
import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from flask_login import UserMixin

db = SQLAlchemy()

# --- Veritabanı Modelleri ---

class User(db.Model, UserMixin):
    """Kullanıcı modeli (Giriş yapmak için)"""
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    def __repr__(self):
        return f'<User {self.username}>'

class Hammadde(db.Model):
    """Hammadde modeli"""
    __tablename__ = 'hammaddeler'
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(100), unique=True, nullable=False)
    maliyet_birimi = db.Column(db.String(20)) # kg, litre, adet vb.
    maliyet_fiyati = db.Column(db.Float, nullable=False)
    guncellenme_tarihi = db.Column(db.DateTime(timezone=True), server_default=func.now())
    # Bir hammadde birden fazla reçetede olabilir
    receteler = db.relationship('Recete', back_populates='hammadde', lazy='dynamic') # cascade eklendi

class Urun(db.Model):
    """Satılan ürün (Menü kalemi) modeli"""
    __tablename__ = 'urunler'
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(100), unique=True, nullable=False)
    excel_adi = db.Column(db.String(100), nullable=False, unique=True) # Excel'deki adıyla eşleşme için
    mevcut_satis_fiyati = db.Column(db.Float, nullable=False)
    hesaplanan_maliyet = db.Column(db.Float, default=0.0)
    kategori = db.Column(db.String(100)) # Örn: Burgerler, İçecekler
    kategori_grubu = db.Column(db.String(100)) # Örn: Yiyecekler, İçecekler
    
    # İlişkiler
    # Ürün silindiğinde ilişkili reçete ve satış kayıtları da silinsin
    receteler = db.relationship('Recete', back_populates='urun', cascade="all, delete-orphan")
    satislar = db.relationship('SatisKaydi', back_populates='urun', cascade="all, delete-orphan")

class Recete(db.Model):
    """Ürün ve Hammaddeler arası ilişki tablosu (Bir üründe hangi hammaddeden ne kadar var)"""
    __tablename__ = 'receteler'
    id = db.Column(db.Integer, primary_key=True)
    miktar = db.Column(db.Float, nullable=False)
    
    urun_id = db.Column(db.Integer, db.ForeignKey('urunler.id'), nullable=False)
    hammadde_id = db.Column(db.Integer, db.ForeignKey('hammaddeler.id'), nullable=False)
    
    urun = db.relationship('Urun', back_populates='receteler')
    hammadde = db.relationship('Hammadde', back_populates='receteler')

class SatisKaydi(db.Model):
    """Satış verisi modeli (Excel'den gelen)"""
    __tablename__ = 'satis_kayitlari'
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey('urunler.id'), nullable=False)
    adet = db.Column(db.Integer, nullable=False)
    toplam_tutar = db.Column(db.Float, nullable=False)
    hesaplanan_birim_fiyat = db.Column(db.Float)
    hesaplanan_maliyet = db.Column(db.Float)
    hesaplanan_kar = db.Column(db.Float)
    tarih = db.Column(db.DateTime, nullable=False)
    
    urun = db.relationship('Urun', back_populates='satislar')

# --- YARDIMCI FONKSİYONLAR ---

def init_db(app):
    """ Veritabanını Flask uygulamasına bağlar ve tabloları oluşturur. """
    # Render PostgreSQL URL'sini kullan, yoksa lokal SQLite kullan
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith("postgres://"):
        # Render PostgreSQL URL'sini SQLAlchemy uyumlu hale getir
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    else:
        # Lokal geliştirme için SQLite fallback
        # 'instance' klasörü otomatik oluşturulur, oraya kaydedelim
        instance_path = os.path.join(app.instance_path)
        if not os.path.exists(instance_path):
            os.makedirs(instance_path)
        database_url = f'sqlite:///{os.path.join(instance_path, "restoran.db")}'
        print(f"UYARI: DATABASE_URL bulunamadı, lokal SQLite kullanılıyor: {database_url}")

    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
            
    db.init_app(app)
    
    with app.app_context():
        print("Veritabanı yapısı kontrol ediliyor...")
        db.create_all()
        print("Veritabanı yapısı hazır.")

def guncelle_tum_urun_maliyetleri():
    """ 
    Tüm ürünlerin maliyetlerini reçetelere göre günceller.
    Hammadde fiyatı değiştiğinde veya reçete güncellendiğinde çalıştırılır.
    """
    try:
        urunler = Urun.query.all()
        for urun in urunler:
            toplam_maliyet = 0.0
            # İlişkili reçeteleri doğrudan urun.receteler üzerinden alalım
            for recete_kalemi in urun.receteler: 
                if recete_kalemi.hammadde:
                    toplam_maliyet += recete_kalemi.miktar * recete_kalemi.hammadde.maliyet_fiyati
            urun.hesaplanan_maliyet = round(toplam_maliyet, 2) # Yuvarlama ekleyelim
        db.session.commit()
        # flash mesajı app.py içinde verilecek, burada sadece print kalsın
        print("Tüm ürün maliyetleri yeniden hesaplandı.") 
        return True, "Tüm ürün maliyetleri başarıyla güncellendi."
    except Exception as e:
        db.session.rollback()
        print(f"Maliyet güncelleme hatası: {e}")
        return False, f"Maliyet güncelleme hatası: {e}"
