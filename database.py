# database.py — RestoProfit veri katmanı (Flask-SQLAlchemy 3.x / SQLAlchemy 2.x uyumlu)

import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship, backref

# SQLAlchemy nesnesi (app.py içinde init_db ile app'e bağlanacağız)
db = SQLAlchemy()


def _normalize_db_url(url: str | None) -> str | None:
    if not url:
        return url
    # Render & eski libler: postgres:// -> postgresql://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def init_db(app):
    """
    Uygulama başlatılırken çağrılır.
    DATABASE_URL varsa onu kullanır; yoksa sqlite dosyasına düşer.
    """
    db_url = _normalize_db_url(os.environ.get("DATABASE_URL"))
    if not db_url:
        # Lokal fallback (dosya tabanlı sqlite)
        db_url = "sqlite:///restoprofit.db"

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)


# -------------------------
# Modeller
# -------------------------

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    def is_authenticated(self):  # Flask-Login uyumluluğu (opsiyonel)
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


class Hammadde(db.Model):
    __tablename__ = "hammaddeler"

    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(120), unique=True, nullable=False, index=True)
    maliyet_birimi = db.Column(db.String(32), nullable=False)       # örn: "kg", "lt", "adet"
    maliyet_fiyati = db.Column(db.Float, nullable=False, default=0)  # birim başına TL

    # İlişkiler
    receteler = relationship("Recete", back_populates="hammadde", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Hammadde {self.isim} ({self.maliyet_birimi} @ {self.maliyet_fiyati} TL)>"


class Urun(db.Model):
    __tablename__ = "urunler"

    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(120), unique=True, nullable=False, index=True)
    excel_adi = db.Column(db.String(120), unique=True, nullable=False, index=True)
    mevcut_satis_fiyati = db.Column(db.Float, nullable=False, default=0.0)

    kategori = db.Column(db.String(120), nullable=True, index=True)
    kategori_grubu = db.Column(db.String(120), nullable=True, index=True)

    # Reçeteden hesaplanan toplam ürün maliyeti (TL)
    hesaplanan_maliyet = db.Column(db.Float, nullable=False, default=0.0)

    # İlişkiler
    receteler = relationship("Recete", back_populates="urun", cascade="all, delete-orphan")
    satis_kayitlari = relationship("SatisKaydi", back_populates="urun", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Urun {self.isim} ({self.kategori}/{self.kategori_grubu})>"


class Recete(db.Model):
    __tablename__ = "receteler"

    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id", ondelete="CASCADE"), nullable=False, index=True)
    hammadde_id = db.Column(db.Integer, db.ForeignKey("hammaddeler.id", ondelete="CASCADE"), nullable=False, index=True)

    # Bu ürün için kullanılan hammadde miktarı (hammadde.maliyet_birimi cinsinden)
    miktar = db.Column(db.Float, nullable=False, default=0.0)

    # İlişkiler
    urun = relationship("Urun", back_populates="receteler")
    hammadde = relationship("Hammadde", back_populates="receteler")

    __table_args__ = (
        # Her ürün-hammadde çifti 1 kez tanımlansın
        db.UniqueConstraint("urun_id", "hammadde_id", name="uq_recete_urun_hammadde"),
    )

    def __repr__(self):
        return f"<Recete urun={self.urun_id} hammadde={self.hammadde_id} miktar={self.miktar}>"


class SatisKaydi(db.Model):
    __tablename__ = "satis_kayitlari"

    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id", ondelete="CASCADE"), nullable=False, index=True)
    tarih = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    adet = db.Column(db.Integer, nullable=False, default=0)
    toplam_tutar = db.Column(db.Float, nullable=False, default=0.0)

    # Türev alanlar (uygulama yüklerken hesaplıyoruz)
    hesaplanan_birim_fiyat = db.Column(db.Float, nullable=False, default=0.0)
    hesaplanan_maliyet = db.Column(db.Float, nullable=False, default=0.0)
    hesaplanan_kar = db.Column(db.Float, nullable=False, default=0.0)

    urun = relationship("Urun", back_populates="satis_kayitlari")

    def __repr__(self):
        return f"<SatisKaydi urun={self.urun_id} tarih={self.tarih} adet={self.adet}>"


# -------------------------
# Yardımcı: Ürünlerin maliyetlerini reçetelerden güncelle
# -------------------------

def _hesapla_urun_maliyeti(urun: Urun) -> float:
    """
    Urun.receteler üzerinden toplam maliyeti (TL) hesaplar.
    Toplam = Σ( hammadde.maliyet_fiyati * miktar )
    """
    if not urun or not urun.receteler:
        return 0.0

    toplam = 0.0
    for rec in urun.receteler:
        if rec.hammadde and rec.miktar and rec.miktar > 0:
            birim_fiyat = float(rec.hammadde.maliyet_fiyati or 0.0)
            toplam += birim_fiyat * float(rec.miktar or 0.0)
    return round(toplam, 4)


def guncelle_tum_urun_maliyetleri(commit: bool = True) -> int:
    """
    Tüm ürünler için reçete bazlı maliyetleri yeniden hesaplar ve yazar.
    Dönüş: güncellenen ürün sayısı.
    """
    adet = 0
    urunler = Urun.query.all()
    for u in urunler:
        yeni = _hesapla_urun_maliyeti(u)
        if u.hesaplanan_maliyet != yeni:
            u.hesaplanan_maliyet = yeni
            adet += 1
    if commit and adet:
        db.session.commit()
    return adet
