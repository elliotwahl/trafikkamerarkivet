"""Vad vi redan har. En sqlite-fil räcker gott och gör svepet idempotent —
avbryt när som helst, nästa körning tar vid."""

import sqlite3

import config


def anslut():
    config.STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.STATE_DB, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS senast (
            kamera_id TEXT PRIMARY KEY,
            phototime TEXT NOT NULL,
            sha256    TEXT,
            fil       TEXT,
            uppdaterad TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS svep (
            ts TEXT PRIMARY KEY,
            kameror INTEGER, nya INTEGER, sparade INTEGER,
            dubbletter INTEGER, fel INTEGER, bytes INTEGER, sekunder REAL
        );
        """
    )
    return db


def kant(db):
    """{kamera_id: (phototime, sha256)} för allt vi redan sparat."""
    return {r[0]: (r[1], r[2]) for r in db.execute("SELECT kamera_id, phototime, sha256 FROM senast")}


def notera(db, kamera_id, phototime, sha256, fil, nu):
    db.execute(
        "INSERT INTO senast (kamera_id, phototime, sha256, fil, uppdaterad) "
        "VALUES (?,?,?,?,?) ON CONFLICT(kamera_id) DO UPDATE SET "
        "phototime=excluded.phototime, sha256=excluded.sha256, "
        "fil=excluded.fil, uppdaterad=excluded.uppdaterad",
        (kamera_id, phototime, sha256, fil, nu),
    )
