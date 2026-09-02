import hashlib
import os
import tempfile
from pathlib import Path

# pyrefly: ignore [missing-import]
import numpy as np
import pandas as pd
# pyrefly: ignore [missing-import]
import plotly.express as px
# pyrefly: ignore [missing-import]
import plotly.graph_objects as go
# pyrefly: ignore [missing-import]
import streamlit as st

from analyzer import (
    process_pdf,
    get_page_preview_rgb,
    generate_heatmap_overlay,
    render_page_cmyk_pymupdf,
    render_page_rgb_array,
    remove_paper_background_ink,
    preserve_neutral_pixels_as_k_only,
    analyze_cmyk_array,
)
from db_store import (
    ensure_db,
    delete_upload_by_id,
    delete_upload_entirely,
    list_upload_history,
    parse_document_filename,
    register_upload,
    sync_local_uploads_to_db,
    update_analysis_result,
    save_page_analysis_results,
    get_page_analysis_results_for_upload,
    get_upload_target_path,
    get_analysis_result_for_upload,
    get_ink_consumption_by_date_range,
)
from report_generator import generate_csv_report, generate_pdf_report
from sample_generator import create_sample_pdf

LOCAL_UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def ensure_local_uploads_dir():
    LOCAL_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_UPLOADS_DIR


ensure_db()

st.set_page_config(
    page_title="PDF CMYK Ink Analyzer",
    page_icon="C",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .main-title {
        font-size: 2.6rem;
        font-weight: 800;
        color: #1E3A8A;
        margin-bottom: 0.35rem;
    }
    .subtitle {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.6rem;
    }
    .kpi-container {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: white;
        border-radius: 8px;
        padding: 1.15rem;
        border: 1px solid #E5E7EB;
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.05);
    }
    .kpi-label {
        font-size: 0.78rem;
        color: #6B7280;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .kpi-value {
        font-size: 1.65rem;
        font-weight: 800;
        color: #1E3A8A;
        margin-top: 0.2rem;
    }
    .kpi-sub {
        font-size: 0.76rem;
        color: #6B7280;
        margin-top: 0.25rem;
    }
    .loaded-file {
        background: #F8FAFC;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        padding: 0.85rem 1rem;
        margin-bottom: 1rem;
        color: #1F2937;
    }
    @media (max-width: 900px) {
        .kpi-container { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 600px) {
        .kpi-container { grid-template-columns: 1fr; }
    }
    [data-testid="stFileUploader"] > *:not(label):not([data-testid="stFileUploaderDropzone"]),
    .uploadedFiles,
    .stFileUploaderFile,
    [data-testid="stUploadedFiles"],
    [data-testid="stFileUploaderFilesContainer"],
    [data-testid="stFileUploaderPagination"] {
        display: none !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


def ensure_state():
    if "pdf_jobs" not in st.session_state:
        st.session_state.pdf_jobs = {}
    if "selected_pdf_name" not in st.session_state:
        st.session_state.selected_pdf_name = None
    if "deleted_files" not in st.session_state:
        st.session_state.deleted_files = set()
    if "selected_history_file_id" not in st.session_state:
        st.session_state.selected_history_file_id = None


def build_dashboard_key(job, suffix):
    seed = str(job.get("upload_id") or job.get("path") or job.get("filename") or "unknown")
    token = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"{suffix}_{token}"


def make_unique_name(filename, file_hash):
    existing = st.session_state.pdf_jobs
    if filename not in existing or existing[filename]["hash"] == file_hash:
        return filename

    stem, ext = os.path.splitext(filename)
    counter = 2
    while True:
        candidate = f"{stem} ({counter}){ext}"
        if candidate not in existing:
            return candidate
        counter += 1


def add_pdf_job(filename, pdf_bytes):
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    display_name = make_unique_name(filename, file_hash)

    if filename in st.session_state.deleted_files:
        st.session_state.deleted_files.remove(filename)
    if display_name in st.session_state.deleted_files:
        st.session_state.deleted_files.remove(display_name)
    if "last_deletion_notice" in st.session_state:
        st.session_state.pop("last_deletion_notice", None)

    existing = st.session_state.pdf_jobs.get(display_name)
    if existing and existing["hash"] == file_hash and existing.get("upload_id"):
        return display_name

    base_upload_dir = ensure_local_uploads_dir()
    metadata = parse_document_filename(filename)
    target_path = get_upload_target_path(base_upload_dir, display_name, metadata)

    with open(target_path, "wb") as saved_pdf:
        saved_pdf.write(pdf_bytes)

    upload_id = register_upload(filename, display_name, str(target_path), metadata)

    st.session_state.pdf_jobs[display_name] = {
        "filename": display_name,
        "orig_filename": filename,
        "path": str(target_path),
        "hash": file_hash,
        "results": None,
        "engine": None,
        "config": None,
        "error": None,
        "upload_id": upload_id,
        "metadata": metadata,
    }
    return display_name


def analyze_job(job, config, color_rates=None):
    if job["config"] != config:
        job["results"] = None
        job["engine"] = None
        job["error"] = None
        job["config"] = config

    progress_bar = st.progress(0)

    def update_progress(current, total):
        progress_bar.progress(int((current / total) * 100))

    results, engine = process_pdf(
        job["path"],
        dpi=config["dpi"],
        progress_cb=update_progress,
    )
    job["results"] = results
    job["engine"] = engine
    job["error"] = None

    effective_rates = normalize_color_rates(color_rates or {})
    job["current_rates"] = effective_rates

    if job.get("upload_id"):
        cyan_kg = sum(
            page["area_m2"] * (page["cyan"] / 100.0) * (effective_rates["Cyan"] / 1000.0)
            for page in results
        )
        magenta_kg = sum(
            page["area_m2"] * (page["magenta"] / 100.0) * (effective_rates["Magenta"] / 1000.0)
            for page in results
        )
        yellow_kg = sum(
            page["area_m2"] * (page["yellow"] / 100.0) * (effective_rates["Yellow"] / 1000.0)
            for page in results
        )
        black_kg = sum(
            page["area_m2"] * (page["black"] / 100.0) * (effective_rates["Black"] / 1000.0)
            for page in results
        )
        total_ink_kg = cyan_kg + magenta_kg + yellow_kg + black_kg

        update_analysis_result(
            job["upload_id"],
            len(results),
            total_ink_kg,
            total_ink_kg,
            total_ink_kg,
            0.0,
            cyan_ink_kg=cyan_kg,
            magenta_ink_kg=magenta_kg,
            yellow_ink_kg=yellow_kg,
            black_ink_kg=black_kg,
        )
        save_page_analysis_results(job["upload_id"], results)


def get_density_map_for_page(page_data, rgb_img, pdf_path=None, page_idx=0):
    if page_data.get("ink_density_map") is not None:
        return page_data["ink_density_map"]

    if pdf_path and os.path.exists(pdf_path):
        try:
            dpi = 120
            cmyk_arr = render_page_cmyk_pymupdf(pdf_path, page_idx, dpi=dpi)
            rgb_arr = render_page_rgb_array(pdf_path, page_idx, dpi=dpi)
            cmyk_arr = remove_paper_background_ink(cmyk_arr, rgb_arr)
            cmyk_arr = preserve_neutral_pixels_as_k_only(cmyk_arr, rgb_arr)
            stats = analyze_cmyk_array(cmyk_arr)
            return stats["ink_density_map"]
        except Exception:
            pass

    avg_coverage = (
        page_data.get("cyan", 0.0)
        + page_data.get("magenta", 0.0)
        + page_data.get("yellow", 0.0)
        + page_data.get("black", 0.0)
    ) / 4.0
    height, width = rgb_img.size[1], rgb_img.size[0]
    return np.full((height, width), avg_coverage, dtype=float)


def normalize_color_rates(rate_input):
    if isinstance(rate_input, dict):
        return {
            "Cyan": float(rate_input.get("Cyan", 0.0)),
            "Magenta": float(rate_input.get("Magenta", 0.0)),
            "Yellow": float(rate_input.get("Yellow", 0.0)),
            "Black": float(rate_input.get("Black", 0.0)),
        }

    scalar = float(rate_input)
    return {
        "Cyan": scalar,
        "Magenta": scalar,
        "Yellow": scalar,
        "Black": scalar,
    }


def calculate_page_ink_breakdown(page, color_rates):
    rates = normalize_color_rates(color_rates)
    return {
        "Cyan": page["area_m2"] * (page["cyan"] / 100.0) * (rates["Cyan"] / 1000.0),
        "Magenta": page["area_m2"] * (page["magenta"] / 100.0) * (rates["Magenta"] / 1000.0),
        "Yellow": page["area_m2"] * (page["yellow"] / 100.0) * (rates["Yellow"] / 1000.0),
        "Black": page["area_m2"] * (page["black"] / 100.0) * (rates["Black"] / 1000.0),
    }


def render_upload_history_picker():
    sync_local_uploads_to_db(LOCAL_UPLOADS_DIR)
    st.markdown("### Upload History & Date Picker")
    history = list_upload_history(limit=500)
    if not history:
        st.info("No saved uploads yet. Once PDFs are uploaded, the date picker will show the upload history here.")
        return

    df = pd.DataFrame(history)

    if "file_date" not in df.columns or df.empty:
        st.info("No saved uploads yet. Once PDFs are uploaded, the date picker will show the upload history here.")
        return

    df["file_date"] = pd.to_datetime(df["file_date"], errors="coerce")
    valid_dates = df["file_date"].dropna()
    if valid_dates.empty:
        st.info("No saved uploads yet. Once PDFs are uploaded, the date picker will show the upload history here.")
        return

    today_date = pd.Timestamp.now().date()
    min_date = min(valid_dates.min().date(), today_date)
    max_date = max(valid_dates.max().date(), today_date)

    if (
        "selected_history_date" not in st.session_state
        or not isinstance(st.session_state.selected_history_date, type(min_date))
        or st.session_state.selected_history_date < min_date
        or st.session_state.selected_history_date > max_date
    ):
        st.session_state.selected_history_date = max_date

    selected_date = st.date_input(
        "Choose upload date",
        value=st.session_state.selected_history_date,
        min_value=min_date,
        max_value=max_date,
        key="history_date_picker",
    )
    st.session_state.selected_history_date = selected_date

    selected_date_str = selected_date.strftime("%Y-%m-%d")
    filtered_history = df[df["file_date"].dt.strftime("%Y-%m-%d") == selected_date_str].copy() if not df.empty else pd.DataFrame()
    filtered_history = filtered_history.dropna(subset=["file_date"]).copy()

    if filtered_history.empty:
        st.info(f"No files were uploaded on {selected_date_str}.")
        return

    st.caption(f"{len(filtered_history)} file(s) uploaded on {selected_date_str}")
    st.markdown("### Files for selected date")

    for _, row in filtered_history.reset_index(drop=True).iterrows():
        file_id = row.get("id")
        file_name = row.get("original_filename") or row.get("stored_filename") or "Unknown file"
        view_key = f"history_view_{file_id}_{file_name}"
        delete_key = f"history_delete_{file_id}_{file_name}"

        col_name, col_date, col_view, col_delete = st.columns([3, 2, 1, 1])
        with col_name:
            st.markdown(f"**{file_name}**")
        with col_date:
            st.write(row.get("file_date_display") or "Not Parsed")
        with col_view:
            if st.button("View", key=view_key, use_container_width=True):
                st.session_state.selected_history_file_id = file_id
                st.rerun()

        with col_delete:
            if st.button("Delete", key=delete_key, use_container_width=True, type="secondary"):
                local_pdf_path = row.get("pdf_path")
                stored_name = row.get("stored_filename") or file_name
                orig_name = row.get("original_filename") or file_name
                delete_upload_entirely(upload_id=file_id, pdf_path=local_pdf_path, stored_filename=stored_name)

                for job_name, job in list(st.session_state.pdf_jobs.items()):
                    if str(job.get("upload_id")) == str(file_id) or job_name in (stored_name, orig_name) or job.get("path") == local_pdf_path:
                        st.session_state.pdf_jobs.pop(job_name, None)
                        st.session_state.deleted_files.add(job.get("orig_filename", job_name))
                        st.session_state.deleted_files.add(job_name)

                st.session_state.deleted_files.add(stored_name)
                st.session_state.deleted_files.add(orig_name)
                st.session_state.last_deletion_notice = "the uploaded document may be  deleted from the history"
                if st.session_state.selected_history_file_id == file_id:
                    st.session_state.selected_history_file_id = None
                st.rerun()


def render_history_file_details(file_id, color_rates, config):
    history = list_upload_history()
    df = pd.DataFrame(history)
    selected_row = df[df["id"] == file_id] if not df.empty else pd.DataFrame()

    if selected_row.empty:
        st.warning("the uploaded document may be  deleted from the history")
        if st.button("← Back to History List"):
            st.session_state.selected_history_file_id = None
            st.rerun()
        return

    selected_row = selected_row.iloc[0]
    file_name = selected_row.get("original_filename") or selected_row.get("stored_filename") or "Unknown file"

    col_back, col_del, col_title = st.columns([1, 1, 3])
    with col_back:
        if st.button("← Back to History", use_container_width=True):
            st.session_state.selected_history_file_id = None
            st.rerun()
    with col_del:
        if st.button("Delete PDF", key=f"del_detail_{file_id}", type="secondary", use_container_width=True):
            local_pdf_path = selected_row.get("pdf_path")
            stored_name = selected_row.get("stored_filename") or file_name
            orig_name = selected_row.get("original_filename") or file_name
            delete_upload_entirely(upload_id=file_id, pdf_path=local_pdf_path, stored_filename=stored_name)

            for job_name, job in list(st.session_state.pdf_jobs.items()):
                if str(job.get("upload_id")) == str(file_id) or job_name in (stored_name, orig_name) or job.get("path") == local_pdf_path:
                    st.session_state.pdf_jobs.pop(job_name, None)
                    st.session_state.deleted_files.add(job.get("orig_filename", job_name))
                    st.session_state.deleted_files.add(job_name)

            st.session_state.deleted_files.add(stored_name)
            st.session_state.deleted_files.add(orig_name)
            st.session_state.last_deletion_notice = "the uploaded document may be  deleted from the history"
            st.session_state.selected_history_file_id = None
            st.rerun()

    with col_title:
        st.markdown(f"### Selected File View: `{file_name}`")
    local_pdf_path = selected_row.get("pdf_path")
    stored_filename = selected_row.get("stored_filename") or file_name
    file_date = selected_row.get("file_date")

    if not (local_pdf_path and os.path.exists(local_pdf_path)):
        if file_date:
            parts = str(file_date).split("-")
            if len(parts) == 3:
                cand = LOCAL_UPLOADS_DIR / parts[0] / parts[1] / stored_filename
                if cand.exists():
                    local_pdf_path = str(cand)
        if not (local_pdf_path and os.path.exists(local_pdf_path)):
            cand = LOCAL_UPLOADS_DIR / stored_filename
            if cand.exists():
                local_pdf_path = str(cand)

    stored_page_results = get_page_analysis_results_for_upload(file_id)
    if stored_page_results:
        db_job = {
            "filename": stored_filename,
            "orig_filename": selected_row.get("original_filename") or file_name,
            "path": local_pdf_path,
            "results": stored_page_results,
            "engine": "Database Record",
            "config": config,
            "error": None,
            "upload_id": file_id,
            "metadata": {
                "file_date_display": selected_row.get("file_date_display"),
                "edition_name": selected_row.get("edition_name"),
                "page_number": selected_row.get("page_number"),
            },
        }
        render_dashboard(db_job, color_rates, key_prefix=f"history_view_{file_id}")
        return

    matching_job = None
    for job_name, job in st.session_state.pdf_jobs.items():
        if str(job.get("upload_id")) == str(file_id):
            matching_job = job
            break

    if matching_job is None:
        file_date = selected_row.get("file_date")

        if not (local_pdf_path and os.path.exists(local_pdf_path)):
            if file_date:
                parts = file_date.split("-")
                if len(parts) == 3:
                    cand = LOCAL_UPLOADS_DIR / parts[0] / parts[1] / stored_filename
                    if cand.exists():
                        local_pdf_path = str(cand)
            if not (local_pdf_path and os.path.exists(local_pdf_path)):
                cand = LOCAL_UPLOADS_DIR / stored_filename
                if cand.exists():
                    local_pdf_path = str(cand)

        if local_pdf_path and os.path.exists(local_pdf_path):
            with open(local_pdf_path, "rb") as pdf_file:
                reloaded_name = add_pdf_job(stored_filename, pdf_file.read())
            matching_job = st.session_state.pdf_jobs.get(reloaded_name)
            if matching_job:
                matching_job["upload_id"] = file_id

    if matching_job:
        if matching_job.get("results") is None:
            with st.spinner(f"Analyzing {matching_job['filename']} for complete dashboard view..."):
                try:
                    analyze_job(matching_job, config, color_rates)
                except Exception as exc:
                    st.error(f"Failed to process {matching_job['filename']}: {exc}")
                    return

        if matching_job.get("results"):
            render_dashboard(matching_job, color_rates, key_prefix=f"history_view_{file_id}")
    else:
        db_res = get_analysis_result_for_upload(file_id)
        if not db_res:
            st.info("No saved analysis record found in database for this upload.")
            return

        st.warning("PDF binary is not found on disk, displaying saved database metrics:")
        total_pages = db_res.get("total_pages", 1)
        cyan_kg = db_res.get("cyan_ink_kg", 0.0)
        magenta_kg = db_res.get("magenta_ink_kg", 0.0)
        yellow_kg = db_res.get("yellow_ink_kg", 0.0)
        black_kg = db_res.get("black_ink_kg", 0.0)
        total_ink_kg = db_res.get("total_ink_kg", cyan_kg + magenta_kg + yellow_kg + black_kg)

        denom = total_ink_kg if total_ink_kg > 0 else 1.0
        c_pct = (cyan_kg / denom) * 100.0
        m_pct = (magenta_kg / denom) * 100.0
        y_pct = (yellow_kg / denom) * 100.0
        k_pct = (black_kg / denom) * 100.0

        st.markdown("#### Saved Database Ink Metrics")

        k1, k2, k3 = st.columns(3)
        with k1:
            st.metric("Total Pages", total_pages)
        with k2:
            st.metric("Total Ink Volume", f"{total_ink_kg:.6f} kg")
        with k3:
            dom_dict = {"Cyan": cyan_kg, "Magenta": magenta_kg, "Yellow": yellow_kg, "Black": black_kg}
            dominant_channel = max(dom_dict, key=dom_dict.get)
            st.metric("Dominant Channel", dominant_channel)

        st.markdown("#### 4 CMYK Channels Ink Consumption (Database)")
        c1, c2, c3, c4 = st.columns(4)

        with c1:
            st.markdown(
                f"""
                <div style='background:#F0F9FF;border:1px solid #BAE6FD;border-radius:12px;padding:1rem;'>
                    <div style='font-size:0.75rem;color:#0369A1;text-transform:uppercase;font-weight:700;'>Cyan Ink</div>
                    <div style='font-size:1.4rem;font-weight:800;color:#0284C7;margin-top:0.3rem;'>{cyan_kg:.6f}kg({c_pct:.2f}%)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"""
                <div style='background:#FDF2F8;border:1px solid #FBCFE8;border-radius:12px;padding:1rem;'>
                    <div style='font-size:0.75rem;color:#BE185D;text-transform:uppercase;font-weight:700;'>Magenta Ink</div>
                    <div style='font-size:1.4rem;font-weight:800;color:#DB2777;margin-top:0.3rem;'>{magenta_kg:.6f}kg({m_pct:.2f}%)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f"""
                <div style='background:#FEFCE8;border:1px solid #FEF08A;border-radius:12px;padding:1rem;'>
                    <div style='font-size:0.75rem;color:#A16207;text-transform:uppercase;font-weight:700;'>Yellow Ink</div>
                    <div style='font-size:1.4rem;font-weight:800;color:#CA8A04;margin-top:0.3rem;'>{yellow_kg:.6f}kg({y_pct:.2f}%)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c4:
            st.markdown(
                f"""
                <div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:1rem;'>
                    <div style='font-size:0.75rem;color:#334155;text-transform:uppercase;font-weight:700;'>Black Ink (K)</div>
                    <div style='font-size:1.4rem;font-weight:800;color:#0F172A;margin-top:0.3rem;'>{black_kg:.6f}kg({k_pct:.2f}%)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_dashboard(job, color_rates, key_prefix="dashboard"):
    color_rates = normalize_color_rates(color_rates)
    results = job["results"]
    engine = job["engine"]
    filename = job["filename"]
    dashboard_key = build_dashboard_key(job, key_prefix)
    upload_meta = job.get("metadata", {})

    if upload_meta:
        st.markdown(
            f"""
            <div style='background:#0F172A;border:1px solid #1E293B;border-radius:12px;padding:0.9rem 1rem;margin-bottom:1rem;color:#E2E8F0;'>
                <div style='font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:#94A3B8;'>Document metadata</div>
                <div style='font-size:1.05rem;font-weight:700;margin-top:0.35rem;'>{upload_meta.get('file_date_display') or 'Date not parsed'} | {upload_meta.get('edition_name') or 'Unknown edition'} | Page {upload_meta.get('page_number') or 'N/A'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    total_pages = len(results)
    avg_c = sum(p["cyan"] for p in results) / total_pages
    avg_m = sum(p["magenta"] for p in results) / total_pages
    avg_y = sum(p["yellow"] for p in results) / total_pages
    avg_k = sum(p["black"] for p in results) / total_pages

    channel_avgs = {"Cyan": avg_c, "Magenta": avg_m, "Yellow": avg_y, "Black": avg_k}
    dom_ink = max(channel_avgs, key=channel_avgs.get)

    total_ink = sum(
        sum(calculate_page_ink_breakdown(p, color_rates).values())
        for p in results
    )

    st.markdown(
        f"""
        <div class="kpi-container">
            <div class="kpi-card">
                <div class="kpi-label">Total Pages</div>
                <div class="kpi-value">{total_pages}</div>
                <div class="kpi-sub">Pages rendered</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Total Ink Required</div>
                <div class="kpi-value">{total_ink:.6f} kg</div>
                <div class="kpi-sub">Combined document total</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Dominant Ink</div>
                <div class="kpi-value">{dom_ink}</div>
                <div class="kpi-sub">Avg. coverage: {channel_avgs[dom_ink]:.1f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2 = st.tabs(["Interactive Dashboard & Visualization", "Tabular Reports & Download"])

    with tab1:
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown("### Per-Page Ink Coverage Distribution")
            selected_page_num = st.selectbox(
                "Select Page to View",
                range(1, total_pages + 1),
                index=0,
                key=f"page_select_{dashboard_key}",
            )

            df_bar = pd.DataFrame(results)
            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(x=df_bar["page_num"], y=df_bar["cyan"], name="Cyan", marker_color="#0EA5E9"))
            fig_bar.add_trace(go.Bar(x=df_bar["page_num"], y=df_bar["magenta"], name="Magenta", marker_color="#EC4899"))
            fig_bar.add_trace(go.Bar(x=df_bar["page_num"], y=df_bar["yellow"], name="Yellow", marker_color="#EAB308"))
            fig_bar.add_trace(go.Bar(x=df_bar["page_num"], y=df_bar["black"], name="Black", marker_color="#1F2937"))
            fig_bar.update_layout(
                barmode="group",
                xaxis_title="Page Number",
                yaxis_title="Coverage Percentage (%)",
                height=350,
                margin=dict(l=20, r=20, t=10, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_bar, use_container_width=True, key=f"plotly_bar_{dashboard_key}_{selected_page_num}")

        with col2:
            st.markdown("### Total Ink Distribution Ratio")
            fig_pie = px.pie(
                names=["Cyan", "Magenta", "Yellow", "Black"],
                values=[avg_c, avg_m, avg_y, avg_k],
                color=["Cyan", "Magenta", "Yellow", "Black"],
                color_discrete_map={
                    "Cyan": "#0EA5E9",
                    "Magenta": "#EC4899",
                    "Yellow": "#EAB308",
                    "Black": "#1F2937",
                },
                hole=0.4,
            )
            fig_pie.update_layout(
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5),
            )
            st.plotly_chart(fig_pie, use_container_width=True, key=f"plotly_pie_{dashboard_key}")

        st.markdown("---")
        st.markdown("### Page Explorer & Ink Density Heatmap")

        page_index = selected_page_num - 1
        page_data = results[page_index]

        pdf_path_to_use = page_data.get("pdf_path", job.get("path"))
        page_idx_to_use = page_data.get("orig_page_idx", page_index)

        rgb_img = get_page_preview_rgb(pdf_path_to_use, page_idx_to_use, dpi=120)
        density_map = get_density_map_for_page(page_data, rgb_img, pdf_path=pdf_path_to_use, page_idx=page_idx_to_use)
        blended_heatmap = generate_heatmap_overlay(rgb_img, density_map, alpha=0.55)

        exp_col1, exp_col2, exp_col3 = st.columns([1, 1, 1])
        with exp_col1:
            st.markdown("**Original Layout Preview**")
            st.image(rgb_img, use_container_width=True)
        with exp_col2:
            st.markdown("**CMYK Density Heatmap**")
            st.image(blended_heatmap, use_container_width=True)
        with exp_col3:
            st.markdown("**Page Details**")
            if "source_pdf_name" in page_data:
                st.markdown(f"**Source Document:** `{page_data['source_pdf_name']}` (Page {page_data['orig_page_idx'] + 1})")

            st.markdown(f"**Cyan Channel:** {page_data['cyan']:.2f}%")
            st.progress(min(page_data["cyan"] / 100.0, 1.0))
            st.markdown(f"**Magenta Channel:** {page_data['magenta']:.2f}%")
            st.progress(min(page_data["magenta"] / 100.0, 1.0))
            st.markdown(f"**Yellow Channel:** {page_data['yellow']:.2f}%")
            st.progress(min(page_data["yellow"] / 100.0, 1.0))
            st.markdown(f"**Black Channel:** {page_data['black']:.2f}%")
            st.progress(min(page_data["black"] / 100.0, 1.0))

            st.markdown("---")
            st.markdown(
                f"**Page Dimensions:** {page_data['width_in']:.2f} in x {page_data['height_in']:.2f} in "
                f"({page_data['area_m2']:.4f} m2)"
            )
            page_ink_kg = sum(calculate_page_ink_breakdown(page_data, color_rates).values())
            st.markdown(f"**Ink Volume Consumed:** `{page_ink_kg:.6f} kg`")

    with tab2:
        st.markdown("### Complete Analysis Records")
        tbl_rows = []
        overall_total_ink = 0.0
        for page in results:
            breakdown = calculate_page_ink_breakdown(page, color_rates)
            c_kg = breakdown["Cyan"]
            m_kg = breakdown["Magenta"]
            y_kg = breakdown["Yellow"]
            k_kg = breakdown["Black"]
            page_ink_kg = c_kg + m_kg + y_kg + k_kg
            overall_total_ink += page_ink_kg
            tbl_rows.append(
                {
                    "Page": page["page_num"],
                    "Cyan": f"{c_kg:.6f}kg({page['cyan']:.2f}%)",
                    "Magenta": f"{m_kg:.6f}kg({page['magenta']:.2f}%)",
                    "Yellow": f"{y_kg:.6f}kg({page['yellow']:.2f}%)",
                    "Black": f"{k_kg:.6f}kg({page['black']:.2f}%)",
                    "Total Ink (kg)": f"{page_ink_kg:.6f} kg",
                }
            )
        st.dataframe(pd.DataFrame(tbl_rows), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### Overall Ink Summary")

        st.markdown(
            """
            <div style='background:#F8FAFC;border:1px solid #E5E7EB;border-radius:12px;padding:1.3rem 1.2rem;margin-bottom:1rem;box-shadow:0 2px 10px rgba(15,23,42,.04);'>
                <div style='font-size:0.72rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'>Total Ink</div>
                <div style='font-size:2.1rem;font-weight:800;color:#1E3A8A;margin-top:0.45rem;'> {:.6f} kg</div>
                <div style='font-size:0.82rem;color:#6B7280;margin-top:0.25rem;'>All uploaded pages combined</div>
            </div>
            """.format(overall_total_ink),
            unsafe_allow_html=True,
        )

        net_copies = st.number_input("Net copies", min_value=1, value=1, step=1, key=f"net_copies_{dashboard_key}")
        net_total = overall_total_ink * net_copies
        st.markdown(
            """
            <div style='background:#ECFDF5;border:1px solid #A7F3D0;border-radius:12px;padding:1.3rem 1.2rem;margin-bottom:1rem;box-shadow:0 2px 10px rgba(16,185,129,.08);'>
                <div style='font-size:0.72rem;color:#065F46;text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'>Net</div>
                <div style='font-size:2rem;font-weight:800;color:#065F46;margin-top:0.45rem;'> {:.6f} kg</div>
                <div style='font-size:0.82rem;color:#065F46;margin-top:0.25rem;'>{} copies × overall total</div>
            </div>
            """.format(net_total, net_copies),
            unsafe_allow_html=True,
        )

        gross_copies = st.number_input("Gross copies", min_value=1, value=1, step=1, key=f"gross_copies_{dashboard_key}")
        gross_total = overall_total_ink * gross_copies
        waste_total = gross_total - net_total
        st.markdown(
            """
            <div style='background:#FEF2F2;border:1px solid #FECACA;border-radius:12px;padding:1.3rem 1.2rem;margin-bottom:1rem;box-shadow:0 2px 10px rgba(239,68,68,.08);'>
                <div style='font-size:0.72rem;color:#991B1B;text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'>Gross</div>
                <div style='font-size:2rem;font-weight:800;color:#991B1B;margin-top:0.45rem;'> {:.6f} kg</div>
                <div style='font-size:0.82rem;color:#991B1B;margin-top:0.25rem;'>{} copies × overall total</div>
            </div>
            """.format(gross_total, gross_copies),
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <div style='background:#EEF2FF;border:1px solid #C7D2FE;border-radius:12px;padding:1.3rem 1.2rem;box-shadow:0 2px 10px rgba(99,102,241,.08);'>
                <div style='font-size:0.72rem;color:#3730A3;text-transform:uppercase;letter-spacing:0.04em;font-weight:700;'>Waste</div>
                <div style='font-size:2rem;font-weight:800;color:#312E81;margin-top:0.45rem;'> {:.6f} kg</div>
                <div style='font-size:0.82rem;color:#3730A3;margin-top:0.25rem;'>Gross − Net</div>
            </div>
            """.format(waste_total),
            unsafe_allow_html=True,
        )

        st.markdown("### Download Reports")
        d_col1, d_col2 = st.columns(2)
        base_name = os.path.splitext(filename)[0]

        csv_str = generate_csv_report(results, color_rates, job.get("metadata"))
        with d_col1:
            st.download_button(
                label="Download CSV Detailed Report",
                data=csv_str,
                file_name=f"cmyk_analysis_{base_name}.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"csv_download_{dashboard_key}",
            )

        pdf_bytes = generate_pdf_report(filename, results, color_rates, engine, job.get("metadata"))
        with d_col2:
            st.download_button(
                label="Download PDF Summary Report",
                data=pdf_bytes,
                file_name=f"cmyk_report_{filename}",
                mime="application/pdf",
                use_container_width=True,
                key=f"pdf_download_{dashboard_key}",
            )


def render_date_range_ink_summary():
    sync_local_uploads_to_db(LOCAL_UPLOADS_DIR)
    st.markdown("### 📊 Date Range Ink Consumption Summary")
    st.caption("Calculate cumulative CMYK ink consumption (kg) fetched dynamically from database records.")

    history = list_upload_history(limit=1000)
    df = pd.DataFrame(history)

    today = pd.Timestamp.now().date()
    min_date = today
    max_date = today

    if not df.empty and "file_date" in df.columns:
        df["file_date"] = pd.to_datetime(df["file_date"], errors="coerce")
        valid_dates = df["file_date"].dropna()
        if not valid_dates.empty:
            min_date = valid_dates.min().date()
            max_date = valid_dates.max().date()

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input(
            "Start Date",
            value=min_date,
            min_value=min_date,
            max_value=max_date,
            key="range_start_date",
        )
    with col_d2:
        end_date = st.date_input(
            "End Date",
            value=max_date,
            min_value=min_date,
            max_value=max_date,
            key="range_end_date",
        )

    if start_date > end_date:
        st.error("Start Date must be before or equal to End Date.")
        return

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    records = get_ink_consumption_by_date_range(start_str, end_str)

    if not records:
        st.info(f"No ink analysis records found between {start_str} and {end_str}.")
        return

    total_files = len(records)
    total_pages = sum(r["total_pages"] for r in records)
    cyan_total = sum(r["cyan_ink_kg"] for r in records)
    magenta_total = sum(r["magenta_ink_kg"] for r in records)
    yellow_total = sum(r["yellow_ink_kg"] for r in records)
    black_total = sum(r["black_ink_kg"] for r in records)
    overall_total = sum(r["total_ink_kg"] for r in records)

    st.markdown(
        f"""
        <div class="kpi-container">
            <div class="kpi-card">
                <div class="kpi-label">Date Range Total Ink</div>
                <div class="kpi-value">{overall_total:.6f} kg</div>
                <div class="kpi-sub">{start_str} to {end_str}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Documents Analyzed</div>
                <div class="kpi-value">{total_files}</div>
                <div class="kpi-sub">Total PDF files</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Total Pages</div>
                <div class="kpi-value">{total_pages}</div>
                <div class="kpi-sub">Combined pages</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 4 CMYK Channels Ink Consumption Breakdown")
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            f"""
            <div style='background:#F0F9FF;border:1px solid #BAE6FD;border-radius:12px;padding:1rem;box-shadow:0 2px 8px rgba(14,165,233,.08);'>
                <div style='font-size:0.75rem;color:#0369A1;text-transform:uppercase;font-weight:700;'>Cyan Ink</div>
                <div style='font-size:1.6rem;font-weight:800;color:#0284C7;margin-top:0.3rem;'>{cyan_total:.6f} kg</div>
                <div style='font-size:0.8rem;color:#0369A1;margin-top:0.2rem;'>{((cyan_total/overall_total)*100 if overall_total>0 else 0):.1f}% of total</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div style='background:#FDF2F8;border:1px solid #FBCFE8;border-radius:12px;padding:1rem;box-shadow:0 2px 8px rgba(236,72,153,.08);'>
                <div style='font-size:0.75rem;color:#BE185D;text-transform:uppercase;font-weight:700;'>Magenta Ink</div>
                <div style='font-size:1.6rem;font-weight:800;color:#DB2777;margin-top:0.3rem;'>{magenta_total:.6f} kg</div>
                <div style='font-size:0.8rem;color:#BE185D;margin-top:0.2rem;'>{((magenta_total/overall_total)*100 if overall_total>0 else 0):.1f}% of total</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div style='background:#FEFCE8;border:1px solid #FEF08A;border-radius:12px;padding:1rem;box-shadow:0 2px 8px rgba(234,179,8,.08);'>
                <div style='font-size:0.75rem;color:#A16207;text-transform:uppercase;font-weight:700;'>Yellow Ink</div>
                <div style='font-size:1.6rem;font-weight:800;color:#CA8A04;margin-top:0.3rem;'>{yellow_total:.6f} kg</div>
                <div style='font-size:0.8rem;color:#A16207;margin-top:0.2rem;'>{((yellow_total/overall_total)*100 if overall_total>0 else 0):.1f}% of total</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:1rem;box-shadow:0 2px 8px rgba(30,41,59,.08);'>
                <div style='font-size:0.75rem;color:#334155;text-transform:uppercase;font-weight:700;'>Black Ink (K)</div>
                <div style='font-size:1.6rem;font-weight:800;color:#0F172A;margin-top:0.3rem;'>{black_total:.6f} kg</div>
                <div style='font-size:0.8rem;color:#334155;margin-top:0.2rem;'>{((black_total/overall_total)*100 if overall_total>0 else 0):.1f}% of total</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")

    chart_col1, chart_col2 = st.columns([3, 2])
    with chart_col1:
        st.markdown("#### Daily CMYK Ink Usage Timeline")
        df_rec = pd.DataFrame(records)
        df_daily = df_rec.groupby("file_date")[["cyan_ink_kg", "magenta_ink_kg", "yellow_ink_kg", "black_ink_kg"]].sum().reset_index()

        fig_daily = go.Figure()
        fig_daily.add_trace(go.Bar(x=df_daily["file_date"], y=df_daily["cyan_ink_kg"], name="Cyan (kg)", marker_color="#0EA5E9"))
        fig_daily.add_trace(go.Bar(x=df_daily["file_date"], y=df_daily["magenta_ink_kg"], name="Magenta (kg)", marker_color="#EC4899"))
        fig_daily.add_trace(go.Bar(x=df_daily["file_date"], y=df_daily["yellow_ink_kg"], name="Yellow (kg)", marker_color="#EAB308"))
        fig_daily.add_trace(go.Bar(x=df_daily["file_date"], y=df_daily["black_ink_kg"], name="Black (kg)", marker_color="#1F2937"))
        fig_daily.update_layout(
            barmode="stack",
            xaxis_title="Date",
            yaxis_title="Ink Weight (kg)",
            height=360,
            margin=dict(l=20, r=20, t=20, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_daily, use_container_width=True, key="range_daily_chart")

    with chart_col2:
        st.markdown("#### Ink Channel Proportions Ratio")
        fig_donut = px.pie(
            names=["Cyan", "Magenta", "Yellow", "Black"],
            values=[cyan_total, magenta_total, yellow_total, black_total],
            color=["Cyan", "Magenta", "Yellow", "Black"],
            color_discrete_map={
                "Cyan": "#0EA5E9",
                "Magenta": "#EC4899",
                "Yellow": "#EAB308",
                "Black": "#1F2937",
            },
            hole=0.45,
        )
        fig_donut.update_layout(
            height=360,
            margin=dict(l=10, r=10, t=20, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5),
        )
        st.plotly_chart(fig_donut, use_container_width=True, key="range_pie_chart")

    st.markdown("---")
    st.markdown("#### Document Breakdown for Selected Period")

    table_data = []
    for r in records:
        file_id = r.get("upload_id") or r.get("id")
        doc_name = r["original_filename"] or r["stored_filename"]
        file_date = r["file_date_display"] or r["file_date"]
        tot_pages = r["total_pages"]
        tot_ink = r["total_ink_kg"]
        denom = tot_ink if tot_ink > 0 else 1.0

        c_pct = (r["cyan_ink_kg"] / denom) * 100.0
        m_pct = (r["magenta_ink_kg"] / denom) * 100.0
        y_pct = (r["yellow_ink_kg"] / denom) * 100.0
        k_pct = (r["black_ink_kg"] / denom) * 100.0

        table_data.append(
            {
                "id": file_id,
                "Document Name": doc_name,
                "File Date": file_date,
                "Pages": tot_pages,
                "Cyan": f"{r['cyan_ink_kg']:.6f}kg({c_pct:.1f}%)",
                "Magenta": f"{r['magenta_ink_kg']:.6f}kg({m_pct:.1f}%)",
                "Yellow": f"{r['yellow_ink_kg']:.6f}kg({y_pct:.1f}%)",
                "Black": f"{r['black_ink_kg']:.6f}kg({k_pct:.1f}%)",
                "Total Ink (kg)": f"{tot_ink:.6f} kg",
            }
        )

    # Render interactive document rows with View action
    st.markdown(
        """
        <div style='display:grid;grid-template-columns:3fr 2fr 1fr 2fr 2fr 2fr 2fr 2fr 1fr;font-weight:700;padding:0.5rem;background:#0F172A;color:#F8FAFC;border-radius:6px;font-size:0.85rem;'>
            <div>Document Name</div>
            <div>File Date</div>
            <div>Pages</div>
            <div>Cyan</div>
            <div>Magenta</div>
            <div>Yellow</div>
            <div>Black</div>
            <div>Total Ink (kg)</div>
            <div>Action</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for r in records:
        file_id = r.get("upload_id") or r.get("id")
        doc_name = r["original_filename"] or r["stored_filename"]
        file_date = r["file_date_display"] or r["file_date"]
        tot_pages = r["total_pages"]
        tot_ink = r["total_ink_kg"]
        denom = tot_ink if tot_ink > 0 else 1.0

        c_pct = (r["cyan_ink_kg"] / denom) * 100.0
        m_pct = (r["magenta_ink_kg"] / denom) * 100.0
        y_pct = (r["yellow_ink_kg"] / denom) * 100.0
        k_pct = (r["black_ink_kg"] / denom) * 100.0

        col_name, col_date, col_p, col_c, col_m, col_y, col_k, col_tot, col_btn = st.columns([3, 2, 1, 2, 2, 2, 2, 2, 1])
        with col_name:
            st.markdown(f"**{doc_name}**")
        with col_date:
            st.caption(file_date or "N/A")
        with col_p:
            st.write(tot_pages)
        with col_c:
            st.caption(f"{r['cyan_ink_kg']:.6f}kg({c_pct:.1f}%)")
        with col_m:
            st.caption(f"{r['magenta_ink_kg']:.6f}kg({m_pct:.1f}%)")
        with col_y:
            st.caption(f"{r['yellow_ink_kg']:.6f}kg({y_pct:.1f}%)")
        with col_k:
            st.caption(f"{r['black_ink_kg']:.6f}kg({k_pct:.1f}%)")
        with col_tot:
            st.write(f"**{tot_ink:.6f} kg**")
        with col_btn:
            if st.button("View", key=f"btn_range_view_{file_id}", use_container_width=True):
                st.session_state.selected_range_file_id = file_id
                st.rerun()

    st.markdown("---")

    df_export = pd.DataFrame([
        {
            "Document Name": r["Document Name"],
            "File Date": r["File Date"],
            "Pages": r["Pages"],
            "Cyan": r["Cyan"],
            "Magenta": r["Magenta"],
            "Yellow": r["Yellow"],
            "Black": r["Black"],
            "Total Ink (kg)": r["Total Ink (kg)"],
        }
        for r in table_data
    ])
    csv_buf = df_export.to_csv(index=False)
    st.download_button(
        label="📥 Download Date Range Ink Report (CSV)",
        data=csv_buf,
        file_name=f"ink_consumption_{start_str}_to_{end_str}.csv",
        mime="text/csv",
        use_container_width=True,
        key="range_csv_download",
    )

    # Display Document Analysis Detail View BELOW the table
    if st.session_state.get("selected_range_file_id"):
        sel_id = st.session_state.selected_range_file_id
        st.markdown("---")
        st.markdown("<div style='background:#1E293B;padding:0.6rem 1rem;border-radius:8px;margin-bottom:1rem;'><strong>📄 Document Analysis Detail View</strong></div>", unsafe_allow_html=True)
        if st.button("✖️ Close Document Analysis View", type="secondary", key="close_range_doc_view"):
            st.session_state.selected_range_file_id = None
            st.rerun()

        render_history_file_details(sel_id, color_rates, config)


ensure_state()

st.markdown("<h1 class='main-title'>PDF CMYK Ink Coverage Analyzer</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='subtitle'>Analyze PDF documents independently for CMYK ink coverage, density maps, and total ink consumption.</p>",
    unsafe_allow_html=True,
)
st.sidebar.header("Document Source")
uploaded_files = st.sidebar.file_uploader("Upload PDF files", type=["pdf"], accept_multiple_files=True)
use_sample = st.sidebar.button("Use Sample CMYK PDF")

st.sidebar.markdown("---")
st.sidebar.header("Processing Settings")
dpi = st.sidebar.selectbox(
    "PPI (Rendering Resolution)",
    [72, 100, 150, 200, 300, 400, 600, 800, 1000, 1200],
    index=2,
    help="Higher PPI improves analysis precision but increases processing time.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("Ink Consumption Rate")
st.sidebar.caption("Company-specific ink weight per square meter (g/m²)")

color_rates = {
    "Cyan": st.sidebar.number_input("Cyan (g/m²)", min_value=0.0, max_value=500.0, value=15.0, step=0.5, format="%.2f"),
    "Magenta": st.sidebar.number_input("Magenta (g/m²)", min_value=0.0, max_value=500.0, value=18.0, step=0.5, format="%.2f"),
    "Yellow": st.sidebar.number_input("Yellow (g/m²)", min_value=0.0, max_value=500.0, value=14.0, step=0.5, format="%.2f"),
    "Black": st.sidebar.number_input("Black (g/m²)", min_value=0.0, max_value=500.0, value=26.0, step=0.5, format="%.2f"),
}

overall_rate_sidebar = sum(color_rates.values())
st.sidebar.caption(f"Overall rate: {overall_rate_sidebar:.2f} g/m²")

config = {"dpi": dpi}

if uploaded_files:
    current_uploaded_names = {f.name for f in uploaded_files}
    st.session_state.deleted_files = {name for name in st.session_state.deleted_files if name in current_uploaded_names}

    added_any = False
    for uploaded_file in uploaded_files:
        if uploaded_file.name in st.session_state.deleted_files:
            continue
        if uploaded_file.name not in st.session_state.pdf_jobs:
            selected_name = add_pdf_job(uploaded_file.name, uploaded_file.getvalue())
            if st.session_state.selected_pdf_name is None:
                st.session_state.selected_pdf_name = selected_name
            added_any = True

    if added_any:
        st.session_state.selected_history_file_id = None
        if hasattr(st, "rerun"):
            st.rerun()
        else:
            st.experimental_rerun()

if use_sample:
    sample_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    create_sample_pdf(sample_temp.name)
    sample_temp.close()
    with open(sample_temp.name, "rb") as sample_file:
        st.session_state.selected_pdf_name = add_pdf_job("sample_cmyk_test.pdf", sample_file.read())
    st.session_state.selected_history_file_id = None
    st.sidebar.info("Using Sample CMYK PDF.")

# Main View Mode Navigation
view_mode = st.radio(
    "Select Display Mode",
    ["📤 Active Upload & Analysis", "📅 History & Date Explorer", "📊 Date Range Ink Consumption Summary"],
    horizontal=True,
    key="main_view_mode_radio",
)

st.markdown("---")

if view_mode == "📅 History & Date Explorer":
    if st.session_state.selected_history_file_id is not None:
        render_history_file_details(st.session_state.selected_history_file_id, color_rates, config)
    else:
        render_upload_history_picker()

elif view_mode == "📊 Date Range Ink Consumption Summary":
    st.session_state.selected_history_file_id = None
    render_date_range_ink_summary()

else:
    # Active Upload & Analysis View Mode
    st.session_state.selected_history_file_id = None

    if st.session_state.get("last_deletion_notice"):
        st.warning(st.session_state.last_deletion_notice)

    pdf_names = list(st.session_state.pdf_jobs.keys())

    if pdf_names:
        if st.session_state.selected_pdf_name not in pdf_names:
            st.session_state.selected_pdf_name = pdf_names[0]

        st.markdown("### Loaded Documents")
        for name in pdf_names:
            col_name, col_delete = st.columns([5, 1])
            with col_name:
                st.markdown(f"📄 **{name}**")
            with col_delete:
                if st.button("Delete", key=f"delete_file_{name}", type="secondary", use_container_width=True):
                    job = st.session_state.pdf_jobs.pop(name, None)
                    if job:
                        delete_upload_by_id(job.get("upload_id"))
                        orig = job.get("orig_filename", name)
                        st.session_state.deleted_files.add(orig)
                        st.session_state.deleted_files.add(name)

                    st.session_state.last_deletion_notice = "the uploaded document may be  deleted from the history"

                    if st.session_state.selected_pdf_name == name:
                        st.session_state.selected_pdf_name = None

                    if hasattr(st, "rerun"):
                        st.rerun()
                    else:
                        st.experimental_rerun()
        st.markdown("---")

        analysis_mode = st.radio(
            "Analysis Mode",
            ["Analyze Single PDF", "Analyze Selected PDFs"],
            horizontal=True,
            key="analysis_mode",
        )

        if analysis_mode == "Analyze Single PDF":
            if st.session_state.selected_pdf_name not in pdf_names:
                st.session_state.selected_pdf_name = pdf_names[0]

            selected_pdf_name = st.selectbox(
                "Select PDF to view results",
                pdf_names,
                index=pdf_names.index(st.session_state.selected_pdf_name),
                key="selected_pdf_dropdown",
                label_visibility="visible",
            )
            st.session_state.selected_pdf_name = selected_pdf_name
            selected_job = st.session_state.pdf_jobs.get(selected_pdf_name)

            if selected_job:
                st.markdown(
                    f"<div class='loaded-file'><strong>Active File:</strong> {selected_job['filename']}</div>",
                    unsafe_allow_html=True,
                )

                if selected_job["results"] is None:
                    with st.spinner(f"Analyzing {selected_job['filename']} and extracting CMYK channels..."):
                        try:
                            analyze_job(selected_job, config, color_rates)
                            st.success(f"{selected_job['filename']} analyzed successfully using {selected_job['engine']} engine.")
                        except Exception as exc:
                            selected_job["results"] = None
                            selected_job["error"] = str(exc)
                            st.error(f"Failed to process {selected_job['filename']}: {exc}")

                if selected_job["error"]:
                    st.error(selected_job["error"])
                elif selected_job["results"]:
                    render_dashboard(selected_job, color_rates, key_prefix="main_dashboard")
            else:
                st.warning("the uploaded document may be  deleted from the history")

        else:
            st.markdown("### Select PDFs for Combined Analysis")
            selected_names = []
            cols = st.columns(min(len(pdf_names), 4))
            for idx, name in enumerate(pdf_names):
                col = cols[idx % len(cols)]
                with col:
                    is_selected = st.checkbox(name, key=f"select_pdf_{name}")
                    if is_selected:
                        selected_names.append(name)

            if not selected_names:
                st.warning("Please select at least one PDF for combined analysis.")
            else:
                selected_jobs = [st.session_state.pdf_jobs[n] for n in selected_names if n in st.session_state.pdf_jobs]
                for job in selected_jobs:
                    if job["results"] is None:
                        with st.spinner(f"Analyzing {job['filename']} and extracting CMYK channels..."):
                            try:
                                analyze_job(job, config, color_rates)
                                st.success(f"{job['filename']} analyzed successfully using {job['engine']} engine.")
                            except Exception as exc:
                                job["results"] = None
                                job["error"] = str(exc)
                                st.error(f"Failed to process {job['filename']}: {exc}")

                ready_jobs = [job for job in selected_jobs if job["results"] is not None]
                if len(ready_jobs) == len(selected_jobs) and selected_jobs:
                    combined_results = []
                    page_num_counter = 1
                    for job in selected_jobs:
                        for p_idx, page_data in enumerate(job["results"]):
                            p_clone = page_data.copy()
                            p_clone["page_num"] = page_num_counter
                            p_clone["pdf_path"] = job["path"]
                            p_clone["orig_page_idx"] = p_idx
                            p_clone["source_pdf_name"] = job["filename"]
                            combined_results.append(p_clone)
                            page_num_counter += 1

                    combined_job = {
                        "filename": "Combined Selection (" + ", ".join([job["filename"] for job in selected_jobs]) + ")",
                        "path": None,
                        "results": combined_results,
                        "engine": selected_jobs[0]["engine"] if selected_jobs else "Combined Engine",
                        "config": config,
                        "error": None,
                        "metadata": {
                            "file_date_display": "Combined selection",
                            "edition_name": "Multiple editions",
                            "page_number": len(combined_results),
                        },
                    }

                    st.markdown(
                        f"<div class='loaded-file'><strong>Active Selection:</strong> {len(selected_jobs)} PDFs ({len(combined_results)} pages total)</div>",
                        unsafe_allow_html=True,
                    )

                    render_dashboard(combined_job, color_rates, key_prefix="combined_dashboard")
    else:
        st.markdown(
            """
            <div style="background-color:#EFF6FF;border-left:6px solid #3B82F6;padding:1.4rem;border-radius:8px;margin-bottom:1.5rem;">
                <h4 style="color:#1E3A8A;margin-top:0;">Get Started</h4>
                <p style="color:#1E3A8A;margin-bottom:0;">
                    Upload one or more PDF files from the sidebar. Each PDF will appear in the uploaded list, and selecting a PDF will show its independent CMYK analysis.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("##### CMYK Split Analysis")
            st.caption("Separates rendered pages into Cyan, Magenta, Yellow, and Black coverage.")
        with col2:
            st.markdown("##### Newspaper-Aware Ink Logic")
            st.caption("Treats black text as K-only and ignores paper-like newsprint background tint.")
        with col3:
            st.markdown("##### Independent PDF Reports")
            st.caption("Exports the page-by-page data as CSV and summary PDF files.")
