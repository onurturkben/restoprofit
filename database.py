from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Hammadde(db.Model):
    __tablename__ = "hammadde"
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(255), unique=True, nullable=False)
    maliyet_birimi = db.Column(db.String(50), nullable=False)
    maliyet_fiyati = db.Column(db.Float, nullable=False)


class Urun(db.Model):
    __tablename__ = "urun"
    id = db.Column(db.Integer, primary_key=True)
    isim = db.Column(db.String(255), unique=True, nullable=False)
    excel_adi = db.Column(db.String(255), unique=True, nullable=False)
    kategori = db.Column(db.String(255), index=True)
    kategori_grubu = db.Column(db.String(255), index=True)
    mevcut_satis_fiyati = db.Column(db.Float, nullable=False, default=0.0)
    hesaplanan_maliyet = db.Column(db.Float, nullable=False, default=0.0)

    __table_args__ = (
        db.Index("idx_urun_kategori_grup", "kategori", "kategori_grubu"),
    )


class Recete(db.Model):
    __tablename__ = "recete"
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("urun.id"), index=True, nullable=False)
    hammadde_id = db.Column(db.Integer, db.ForeignKey("hammadde.id"), index=True, nullable=False)
    miktar = db.Column(db.Float, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("urun_id", "hammadde_id", name="uq_recete_urun_hammadde"),
    )

    urun = db.relationship("Urun", backref="receteler")
    hammadde = db.relationship("Hammadde", backref="receteler")


class SatisKaydi(db.Model):
    __tablename__ = "satis_kaydi"
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("urun.id"), index=True, nullable=False)
    tarih = db.Column(db.DateTime, index=True, nullable=False)
    adet = db.Column(db.Integer, nullable=False)
    toplam_tutar = db.Column(db.Float, nullable=False)
    hesaplanan_birim_fiyat = db.Column(db.Float, index=True)
    hesaplanan_maliyet = db.Column(db.Float)
    hesaplanan_kar = db.Column(db.Float)

    __table_args__ = (
        db.Index("idx_satis_urun_tarih", "urun_id", "tarih"),
        db.Index("idx_satis_birim_fiyat", "hesaplanan_birim_fiyat"),
    )

    urun = db.relationship("Urun", backref="satislar")
