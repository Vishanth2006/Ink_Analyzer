import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ink_analysis.db"

EDITION_MAP = {
    "chnt": "Chennai Times",
    "etch": "Economic Times",
    "toich": "The Times of India",
    "omsch": "Supplement",
    "adych": "Adyar Plus",
    "angch": "Annanagar Plus",
    "tamch": "Tambaram Plus",
    "purch": "Purasai Plus",
    "tngch": "Tnagar Plus",
    "mylch": "Mylapore Plus",
    "etwd": "ET Wealth",
    "prch": "Times Property",
}


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_date TEXT,
            file_date_display TEXT,
            edition_code TEXT,
            edition_name TEXT,
            page_number INTEGER,
            document_key TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            analysis_status TEXT DEFAULT 'uploaded',
            pdf_path TEXT
        )
        """
    )
    conn.execute(
        "UPDATE uploads SET analysis_status = 'uploaded' WHERE analysis_status IS NULL OR analysis_status = 'pending'"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER NOT NULL,
            total_pages INTEGER,
            cyan_ink_kg REAL DEFAULT 0.0,
            magenta_ink_kg REAL DEFAULT 0.0,
            yellow_ink_kg REAL DEFAULT 0.0,
            black_ink_kg REAL DEFAULT 0.0,
            total_ink_kg REAL,
            net_total_kg REAL,
            gross_total_kg REAL,
            waste_kg REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(upload_id) REFERENCES uploads(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS page_analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER NOT NULL,
            page_num INTEGER NOT NULL,
            cyan_pct REAL NOT NULL,
            magenta_pct REAL NOT NULL,
            yellow_pct REAL NOT NULL,
            black_pct REAL NOT NULL,
            width_in REAL DEFAULT 8.5,
            height_in REAL DEFAULT 11.0,
            area_m2 REAL DEFAULT 0.0603,
            FOREIGN KEY(upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
        """
    )

    for col in ["cyan_ink_kg", "magenta_ink_kg", "yellow_ink_kg", "black_ink_kg"]:
        try:
            conn.execute(f"ALTER TABLE analysis_results ADD COLUMN {col} REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass

    conn.execute(
        """
        UPDATE uploads 
        SET file_date = strftime('%Y-%m-%d', uploaded_at),
            file_date_display = strftime('%d %B %Y', uploaded_at)
        WHERE file_date IS NULL OR file_date = '' OR file_date = 'None'
        """
    )

    conn.commit()
    conn.close()


def get_upload_target_path(base_upload_dir, filename, metadata=None):
    meta = metadata or parse_document_filename(filename)
    file_date = meta.get("file_date")
    if file_date:
        parts = file_date.split("-")
        if len(parts) == 3:
            year, month = parts[0], parts[1]
        else:
            now = datetime.now()
            year, month = f"{now.year:04d}", f"{now.month:02d}"
    else:
        now = datetime.now()
        year, month = f"{now.year:04d}", f"{now.month:02d}"

    target_dir = Path(base_upload_dir) / year / month
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / filename


def parse_document_filename(filename):
    safe_name = os.path.basename(filename or "")
    stem = os.path.splitext(safe_name)[0].strip().lower()
    now = datetime.now()

    if not stem:
        return {
            "original_filename": safe_name,
            "file_date": now.strftime("%Y-%m-%d"),
            "file_date_display": now.strftime("%d %B %Y"),
            "edition_code": "unknown",
            "edition_name": "Unknown",
            "page_number": None,
            "document_key": f"{now.strftime('%Y-%m-%d')}_unknown_00",
        }

    tokens = [token for token in re.split(r"[_\-\s]+", stem) if token]
    date_token = None
    if tokens and tokens[0].isdigit() and len(tokens[0]) == 8:
        date_token = tokens[0]

    edition_code = "unknown"
    for token in tokens[1:]:
        if token.lower() in EDITION_MAP:
            edition_code = token.lower()
            break

    page_number = None
    for token in tokens:
        if token.isdigit() and len(token) <= 3:
            page_number = int(token)
            if page_number > 0:
                break

    file_date = None
    file_date_display = None
    if date_token:
        try:
            parsed_dt = datetime.strptime(date_token, "%d%m%Y")
            file_date = parsed_dt.strftime("%Y-%m-%d")
            file_date_display = parsed_dt.strftime("%d %B %Y")
        except ValueError:
            file_date = None
            file_date_display = None

    if not file_date:
        file_date = now.strftime("%Y-%m-%d")
        file_date_display = now.strftime("%d %B %Y")

    edition_name = EDITION_MAP.get(edition_code, "Unknown")
    document_key = f"{file_date}_{edition_code}_{(page_number or 0):02d}"

    return {
        "original_filename": safe_name,
        "file_date": file_date,
        "file_date_display": file_date_display,
        "edition_code": edition_code,
        "edition_name": edition_name,
        "page_number": page_number,
        "document_key": document_key,
    }


def get_connection():
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def register_upload(filename, stored_filename, pdf_path=None, metadata=None):
    meta = metadata or parse_document_filename(filename)
    conn = get_connection()

    existing = conn.execute(
        "SELECT id FROM uploads WHERE stored_filename = ? OR original_filename = ? OR pdf_path = ?",
        (stored_filename, filename, str(pdf_path) if pdf_path else ""),
    ).fetchone()

    if existing:
        upload_id = existing["id"]
        conn.execute(
            """
            UPDATE uploads
            SET original_filename = ?,
                stored_filename = ?,
                file_date = ?,
                file_date_display = ?,
                edition_code = ?,
                edition_name = ?,
                page_number = ?,
                document_key = ?,
                pdf_path = ?,
                uploaded_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                meta.get("original_filename") or filename,
                stored_filename,
                meta.get("file_date"),
                meta.get("file_date_display"),
                meta.get("edition_code"),
                meta.get("edition_name"),
                meta.get("page_number"),
                meta.get("document_key"),
                str(pdf_path) if pdf_path else None,
                upload_id,
            ),
        )
        conn.commit()
        conn.close()
        return upload_id

    cursor = conn.execute(
        """
        INSERT INTO uploads (
            original_filename,
            stored_filename,
            file_date,
            file_date_display,
            edition_code,
            edition_name,
            page_number,
            document_key,
            pdf_path,
            analysis_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded')
        """,
        (
            meta.get("original_filename") or filename,
            stored_filename,
            meta.get("file_date"),
            meta.get("file_date_display"),
            meta.get("edition_code"),
            meta.get("edition_name"),
            meta.get("page_number"),
            meta.get("document_key"),
            str(pdf_path) if pdf_path else None,
        ),
    )
    upload_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return upload_id


def update_analysis_result(
    upload_id,
    total_pages,
    total_ink_kg,
    net_total_kg,
    gross_total_kg,
    waste_kg,
    cyan_ink_kg=0.0,
    magenta_ink_kg=0.0,
    yellow_ink_kg=0.0,
    black_ink_kg=0.0,
):
    conn = get_connection()
    conn.execute("DELETE FROM analysis_results WHERE upload_id = ?", (upload_id,))
    conn.execute(
        """
        INSERT INTO analysis_results (
            upload_id,
            total_pages,
            cyan_ink_kg,
            magenta_ink_kg,
            yellow_ink_kg,
            black_ink_kg,
            total_ink_kg,
            net_total_kg,
            gross_total_kg,
            waste_kg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            upload_id,
            total_pages,
            cyan_ink_kg,
            magenta_ink_kg,
            yellow_ink_kg,
            black_ink_kg,
            total_ink_kg,
            net_total_kg,
            gross_total_kg,
            waste_kg,
        ),
    )
    conn.execute(
        "UPDATE uploads SET analysis_status = 'complete' WHERE id = ?",
        (upload_id,),
    )
    conn.commit()
    conn.close()


def get_analysis_result_for_upload(upload_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM analysis_results WHERE upload_id = ? ORDER BY id DESC LIMIT 1",
        (upload_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_page_analysis_results(upload_id, results):
    if not upload_id or not results:
        return
    conn = get_connection()
    conn.execute("DELETE FROM page_analysis_results WHERE upload_id = ?", (upload_id,))
    for p in results:
        conn.execute(
            """
            INSERT INTO page_analysis_results (
                upload_id, page_num, cyan_pct, magenta_pct, yellow_pct, black_pct, width_in, height_in, area_m2
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                upload_id,
                int(p.get("page_num", 1)),
                float(p.get("cyan", 0.0)),
                float(p.get("magenta", 0.0)),
                float(p.get("yellow", 0.0)),
                float(p.get("black", 0.0)),
                float(p.get("width_in", 8.5)),
                float(p.get("height_in", 11.0)),
                float(p.get("area_m2", 0.0603)),
            ),
        )
    conn.commit()
    conn.close()


def get_page_analysis_results_for_upload(upload_id):
    if not upload_id:
        return []
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT 
            page_num,
            cyan_pct AS cyan,
            magenta_pct AS magenta,
            yellow_pct AS yellow,
            black_pct AS black,
            width_in,
            height_in,
            area_m2
        FROM page_analysis_results
        WHERE upload_id = ?
        ORDER BY page_num ASC
        """,
        (upload_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_upload_status(upload_id, status):
    conn = get_connection()
    conn.execute(
        "UPDATE uploads SET analysis_status = ? WHERE id = ?",
        (status, upload_id),
    )
    conn.commit()
    conn.close()


def delete_upload_entirely(upload_id=None, pdf_path=None, stored_filename=None):
    import gc
    gc.collect()
    conn = get_connection()
    ids_to_delete = set()
    paths_to_delete = set()
    base_dir = DB_PATH.parent / "uploads"

    if upload_id is not None:
        ids_to_delete.add(upload_id)
        rows = conn.execute("SELECT pdf_path, stored_filename FROM uploads WHERE id = ?", (upload_id,)).fetchall()
        for r in rows:
            if r["pdf_path"]:
                paths_to_delete.add(r["pdf_path"])
            if r["stored_filename"] and base_dir.exists():
                for p in base_dir.rglob(r["stored_filename"]):
                    paths_to_delete.add(str(p))

    if pdf_path is not None:
        paths_to_delete.add(str(pdf_path))
        rows = conn.execute("SELECT id FROM uploads WHERE pdf_path = ?", (str(pdf_path),)).fetchall()
        for r in rows:
            ids_to_delete.add(r["id"])

    if stored_filename is not None:
        rows = conn.execute("SELECT id, pdf_path FROM uploads WHERE stored_filename = ? OR original_filename = ?", (stored_filename, stored_filename)).fetchall()
        for r in rows:
            ids_to_delete.add(r["id"])
            if r["pdf_path"]:
                paths_to_delete.add(r["pdf_path"])
        if base_dir.exists():
            for p in base_dir.rglob(stored_filename):
                paths_to_delete.add(str(p))

    for uid in ids_to_delete:
        conn.execute("DELETE FROM page_analysis_results WHERE upload_id = ?", (uid,))
        conn.execute("DELETE FROM analysis_results WHERE upload_id = ?", (uid,))
        conn.execute("DELETE FROM uploads WHERE id = ?", (uid,))

    conn.commit()
    conn.close()

    for p in paths_to_delete:
        if p and os.path.exists(p):
            gc.collect()
            try:
                os.remove(p)
            except Exception:
                try:
                    with open(p, "wb") as f:
                        f.write(b"")
                    os.remove(p)
                except Exception:
                    pass


def delete_upload_by_id(upload_id):
    delete_upload_entirely(upload_id=upload_id)


def list_recent_uploads(limit=20):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM uploads
        ORDER BY uploaded_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_upload_history(limit=None):
    conn = get_connection()
    if limit is not None:
        rows = conn.execute(
            """
            SELECT * FROM uploads
            ORDER BY file_date DESC, uploaded_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM uploads
            ORDER BY file_date DESC, uploaded_at DESC
            """
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_date_upload_summary():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT file_date, COUNT(*) AS upload_count
        FROM uploads
        WHERE file_date IS NOT NULL
        GROUP BY file_date
        ORDER BY file_date ASC
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sync_local_uploads_to_db(upload_dir=None):
    target_dir = Path(upload_dir) if upload_dir is not None else (DB_PATH.parent / "uploads")
    if not target_dir.exists():
        return []

    conn = get_connection()
    synced = []
    for pdf_path in sorted(target_dir.rglob("*.pdf")):
        if not pdf_path.is_file() or pdf_path.stat().st_size < 100:
            continue

        stored_name = pdf_path.name
        existing = conn.execute(
            "SELECT id FROM uploads WHERE stored_filename = ? OR pdf_path = ?",
            (stored_name, str(pdf_path)),
        ).fetchone()
        if existing:
            continue

        metadata = parse_document_filename(stored_name)
        cursor = conn.execute(
            """
            INSERT INTO uploads (
                original_filename,
                stored_filename,
                file_date,
                file_date_display,
                edition_code,
                edition_name,
                page_number,
                document_key,
                pdf_path,
                analysis_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded')
            """,
            (
                metadata.get("original_filename") or stored_name,
                stored_name,
                metadata.get("file_date"),
                metadata.get("file_date_display"),
                metadata.get("edition_code"),
                metadata.get("edition_name"),
                metadata.get("page_number"),
                metadata.get("document_key"),
                str(pdf_path),
            ),
        )
        synced.append(cursor.lastrowid)

    conn.commit()
    conn.close()
    return synced


def get_ink_consumption_by_date_range(start_date_str, end_date_str):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT 
            u.id AS upload_id,
            u.original_filename,
            u.stored_filename,
            u.file_date,
            u.file_date_display,
            u.edition_name,
            u.page_number,
            u.uploaded_at,
            ar.total_pages,
            COALESCE(ar.cyan_ink_kg, 0.0) AS cyan_ink_kg,
            COALESCE(ar.magenta_ink_kg, 0.0) AS magenta_ink_kg,
            COALESCE(ar.yellow_ink_kg, 0.0) AS yellow_ink_kg,
            COALESCE(ar.black_ink_kg, 0.0) AS black_ink_kg,
            COALESCE(ar.total_ink_kg, 0.0) AS total_ink_kg
        FROM uploads u
        INNER JOIN analysis_results ar ON u.id = ar.upload_id
        WHERE u.file_date >= ? AND u.file_date <= ?
        ORDER BY u.file_date ASC, u.id ASC
        """,
        (start_date_str, end_date_str),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


ensure_db()

