import os
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from flask_login import UserMixin
import base64 # Logo için base64 encode/decode işlemleri için eklendi

db = SQLAlchemy()

# --- Veritabanı Modelleri ---

class User(db.Model, UserMixin):
    """Kullanıcı modeli (Giriş yapmak için)"""
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

class Hammadde(db.Model):
    """Hammadde modeli"""
    __tablename__ = 'hammaddeler'
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(100), unique=True, nullable=False)
    maliyet_birimi = db.Column(db.String(20)) # kg, litre, adet vb.
    maliyet_fiyati = db.Column(db.Float, nullable=False)
    guncellenme_tarihi = db.Column(db.DateTime(timezone=True), server_default=func.now())
    
    # Bir hammadde birden fazla reçetede olabilir
    receteler = db.relationship('Recete', back_populates='hammadde', lazy='dynamic')

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

class Ayarlar(db.Model):
    """Site ayarlarını (logo, site adı vb.) tutmak için model."""
    __tablename__ = 'ayarlar'
    id = db.Column(db.Integer, primary_key=True)
    site_adi = db.Column(db.String(100), default='RestoProfit')
    logo_data = db.Column(db.Text, nullable=True) # Logo'yu Base64 string olarak saklayacağız
    logo_mimetype = db.Column(db.String(50), nullable=True) # Örn: 'image/png'

# --- YARDIMCI FONKSİYONLAR ---

def init_db(app):
    """ Veritabanını Flask uygulamasına bağlar ve tabloları oluşturur. """
    # Render.com'un sağladığı veritabanı URL'sini kullanır, bulamazsa lokal sqlite dosyası oluşturur.
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL_SQLALCHEMY', 'sqlite:///instance/app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Veritabanı dosyasının bulunacağı klasörün var olduğundan emin ol
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        db_dir = os.path.dirname(app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''))
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)
            
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
            receteler = Recete.query.filter_by(urun_id=urun.id).all()
            for recete_kalemi in receteler:
                if recete_kalemi.hammadde:
                    toplam_maliyet += recete_kalemi.miktar * recete_kalemi.hammadde.maliyet_fiyati
            urun.hesaplanan_maliyet = round(toplam_maliyet, 2)
        db.session.commit()
        return True, "Tüm ürün maliyetleri başarıyla güncellendi."
    except Exception as e:
        db.session.rollback()
        print(f"Maliyet güncelleme hatası: {e}")
        return False, f"Maliyet güncelleme hatası: {e}"
