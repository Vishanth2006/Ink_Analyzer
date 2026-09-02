import os
import subprocess
import glob
import tempfile
import numpy as np
from PIL import Image
import fitz  # PyMuPDF
import matplotlib.pyplot as plt

def validate_pdf(file_path):
    """
    Validates that the file exists, is a valid PDF, and is readable.
    """
    if not os.path.exists(file_path):
        return False, "File does not exist."
    
    # Check size (e.g. limit to 500MB for Web App safety)
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if file_size_mb > 500:
        return False, f"File is too large ({file_size_mb:.1f} MB). Max limit is 500 MB."
    
    try:
        # Check PDF header bytes
        with open(file_path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                return False, "Invalid file format. Not a valid PDF."
        
        # Test opening with PyMuPDF
        doc = fitz.open(file_path)
        page_count = len(doc)
        doc.close()
        
        if page_count == 0:
            return False, "PDF has 0 pages or is corrupted."
        
        return True, f"Valid PDF with {page_count} pages."
    except Exception as e:
        return False, f"Failed to read PDF: {str(e)}"

def find_ghostscript():
    """
    Attempts to locate the Ghostscript executable (gswin64c.exe, gswin32c.exe, or gs)
    in the system PATH or default Windows installation paths.
    """
    # 1. Search in PATH
    for gs_bin in ["gswin64c", "gs", "gswin32c"]:
        try:
            # Run a quick check
            subprocess.run([gs_bin, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return gs_bin
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
            
    # 2. Search in common Windows installation paths
    common_paths = [
        "C:\\Program Files\\gs\\gs*\\bin\\gswin64c.exe",
        "C:\\Program Files (x86)\\gs\\gs*\\bin\\gswin32c.exe",
    ]
    for pattern in common_paths:
        matches = glob.glob(pattern)
        if matches:
            # Return the latest version found (sorted descending)
            matches.sort(reverse=True)
            return matches[0]
            
    return None

def render_page_cmyk_gs(gs_path, pdf_path, page_idx, dpi=150):
    """
    Renders a single page (1-based index) of the PDF to a CMYK TIFF image using Ghostscript.
    Returns the CMYK NumPy array, or None if it fails.
    """
    # Ghostscript pages are 1-based
    gs_page_num = page_idx + 1
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_tiff = os.path.join(temp_dir, f"page_{gs_page_num}.tif")
        
        # Build command: -sDEVICE=tiff32nc produces 32-bit CMYK TIFF
        cmd = [
            gs_path,
            "-dNOPAUSE",
            "-dBATCH",
            "-sDEVICE=tiff32nc",
            f"-r{dpi}",
            f"-dFirstPage={gs_page_num}",
            f"-dLastPage={gs_page_num}",
            f"-sOutputFile={output_tiff}",
            pdf_path
        ]
        
        try:
            # Run command silently
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            if os.path.exists(output_tiff):
                with Image.open(output_tiff) as img:
                    # Verify it's loaded in CMYK mode
                    if img.mode != "CMYK":
                        img = img.convert("CMYK")
                    # Convert to numpy array
                    cmyk_arr = np.array(img)
                    return cmyk_arr
        except Exception as e:
            # Log error or print it
            print(f"Ghostscript rendering failed for page {gs_page_num}: {e}")
            
    return None

def render_page_cmyk_pymupdf(pdf_path, page_idx, dpi=150):
    """
    Renders a single page of the PDF to a CMYK NumPy array using PyMuPDF.
    This serves as a reliable fallback.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_idx]
        # Render page to CMYK Pixmap
        pix = page.get_pixmap(colorspace=fitz.csCMYK, dpi=dpi)
        
        # Convert raw samples bytes to NumPy array
        # CMYK has 4 channels: C, M, Y, K
        cmyk_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)
        return cmyk_arr.copy() # Make a copy to avoid buffer lifetime issues
    finally:
        doc.close()

def render_page_rgb_array(pdf_path, page_idx, dpi=150):
    """
    Renders a single page to an RGB NumPy array. This is used to identify
    visually neutral black/gray pixels before CMYK cost analysis.
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(colorspace=fitz.csRGB, dpi=dpi)
        rgb_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        return rgb_arr.copy()
    finally:
        doc.close()

def preserve_neutral_pixels_as_k_only(cmyk_arr, rgb_arr, neutral_tolerance=6):
    """
    Converts visually neutral black/gray/white pixels to K-only CMYK.

    Some renderers convert RGB black (0,0,0) into rich black CMYK, which makes
    black-only PDFs appear to consume cyan, magenta, and yellow. This correction
    uses the rendered RGB appearance to detect neutral pixels and maps them to:
    C=0, M=0, Y=0, K=(255 - gray value).
    """
    if cmyk_arr.ndim != 3 or cmyk_arr.shape[2] != 4:
        raise ValueError("Expected CMYK array with shape (height, width, 4).")
    if rgb_arr.ndim != 3 or rgb_arr.shape[2] != 3:
        raise ValueError("Expected RGB array with shape (height, width, 3).")

    cmyk_h, cmyk_w = cmyk_arr.shape[:2]
    rgb_h, rgb_w = rgb_arr.shape[:2]
    if (rgb_h, rgb_w) != (cmyk_h, cmyk_w):
        rgb_img = Image.fromarray(rgb_arr, mode="RGB").resize((cmyk_w, cmyk_h), Image.Resampling.BILINEAR)
        rgb_arr = np.array(rgb_img)

    rgb_i = rgb_arr.astype(np.int16)
    r_chan = rgb_i[:, :, 0]
    g_chan = rgb_i[:, :, 1]
    b_chan = rgb_i[:, :, 2]

    max_delta = np.maximum.reduce([
        np.abs(r_chan - g_chan),
        np.abs(r_chan - b_chan),
        np.abs(g_chan - b_chan)
    ])
    neutral_mask = max_delta <= neutral_tolerance

    gray_value = np.rint((r_chan + g_chan + b_chan) / 3.0)
    max_rgb = np.maximum.reduce([r_chan, g_chan, b_chan]).astype(np.float32)
    saturation = np.zeros_like(max_rgb)
    np.divide(max_delta.astype(np.float32), max_rgb, out=saturation, where=max_rgb > 0)
    dark_neutral_mask = (gray_value < 155) & (saturation < 0.45)
    neutral_mask = neutral_mask | dark_neutral_mask

    k_values = np.clip(255 - gray_value, 0, 255).astype(np.uint8)

    corrected = cmyk_arr.copy()
    corrected[neutral_mask, 0] = 0
    corrected[neutral_mask, 1] = 0
    corrected[neutral_mask, 2] = 0
    corrected[neutral_mask, 3] = k_values[neutral_mask]

    return corrected

def remove_paper_background_ink(cmyk_arr, rgb_arr, color_tolerance=48):
    """
    Treats the page's paper/background color as unprinted paper.

    Newspaper scans/PDFs can include a yellowish or gray paper tint. That tint is
    not printing ink, so it should not be counted in CMYK usage. The background
    color is estimated from the page border and matching bright pixels are reset
    to paper white: C=0, M=0, Y=0, K=0.
    """
    if rgb_arr.shape[:2] != cmyk_arr.shape[:2]:
        cmyk_h, cmyk_w = cmyk_arr.shape[:2]
        rgb_img = Image.fromarray(rgb_arr, mode="RGB").resize((cmyk_w, cmyk_h), Image.Resampling.BILINEAR)
        rgb_arr = np.array(rgb_img)

    height, width = rgb_arr.shape[:2]
    border_size = max(2, min(height, width) // 40)
    border_pixels = np.concatenate([
        rgb_arr[:border_size, :, :].reshape(-1, 3),
        rgb_arr[-border_size:, :, :].reshape(-1, 3),
        rgb_arr[:, :border_size, :].reshape(-1, 3),
        rgb_arr[:, -border_size:, :].reshape(-1, 3),
    ], axis=0).astype(np.float32)

    paper_rgb = np.median(border_pixels, axis=0)
    paper_luminance = (0.2126 * paper_rgb[0]) + (0.7152 * paper_rgb[1]) + (0.0722 * paper_rgb[2])
    if paper_luminance < 145:
        return cmyk_arr

    paper_max = float(np.max(paper_rgb))
    paper_min = float(np.min(paper_rgb))
    paper_chroma = paper_max - paper_min
    paper_saturation = (paper_chroma / paper_max) if paper_max > 0 else 0.0
    paper_is_neutral = paper_saturation < 0.12
    paper_is_yellowish = (
        paper_saturation < 0.32 and
        paper_rgb[0] >= paper_rgb[2] and
        paper_rgb[1] >= (paper_rgb[2] - 8)
    )
    if not (paper_is_neutral or paper_is_yellowish):
        return cmyk_arr

    rgb_f = rgb_arr.astype(np.float32)
    luminance = (0.2126 * rgb_f[:, :, 0]) + (0.7152 * rgb_f[:, :, 1]) + (0.0722 * rgb_f[:, :, 2])
    distance_from_paper = np.sqrt(np.sum(np.square(rgb_f - paper_rgb), axis=2))

    # Include normal white paper and tinted newsprint, but do not wipe out inked
    # text/images that are darker or visibly different from the border paper.
    max_rgb = np.max(rgb_f, axis=2)
    min_rgb = np.min(rgb_f, axis=2)
    chroma = max_rgb - min_rgb
    saturation = np.zeros_like(max_rgb)
    np.divide(chroma, max_rgb, out=saturation, where=max_rgb > 0)

    border_distances = np.sqrt(np.sum(np.square(border_pixels - paper_rgb), axis=1))
    adaptive_tolerance = max(color_tolerance, float(np.percentile(border_distances, 90)) + 18.0)

    paper_like_from_border = (distance_from_paper <= adaptive_tolerance) & (luminance > 140)
    newsprint_like = (
        (luminance > 175) &
        (saturation < 0.22) &
        (rgb_f[:, :, 0] >= (rgb_f[:, :, 2] - 6)) &
        (rgb_f[:, :, 1] >= (rgb_f[:, :, 2] - 10))
    )
    pale_yellow_paper = (
        (luminance > 165) &
        (saturation < 0.32) &
        (rgb_f[:, :, 0] >= rgb_f[:, :, 2]) &
        (rgb_f[:, :, 1] >= (rgb_f[:, :, 2] - 8))
    )

    paper_mask = paper_like_from_border | newsprint_like | pale_yellow_paper

    corrected = cmyk_arr.copy()
    corrected[paper_mask] = [0, 0, 0, 0]
    return corrected

def analyze_cmyk_array(cmyk_arr):
    """Given a CMYK array (H, W, 4), calculates average percentage coverage for each channel."""
    c_chan = cmyk_arr[:, :, 0]
    m_chan = cmyk_arr[:, :, 1]
    y_chan = cmyk_arr[:, :, 2]
    k_chan = cmyk_arr[:, :, 3]

    c_cov = (np.sum(c_chan) / (255.0 * c_chan.size)) * 100.0
    m_cov = (np.sum(m_chan) / (255.0 * m_chan.size)) * 100.0
    y_cov = (np.sum(y_chan) / (255.0 * y_chan.size)) * 100.0
    k_cov = (np.sum(k_chan) / (255.0 * k_chan.size)) * 100.0

    density_map = (c_chan.astype(np.float32) + m_chan + y_chan + k_chan) / 255.0 * 100.0

    return {
        "cyan": c_cov,
        "magenta": m_cov,
        "yellow": y_cov,
        "black": k_cov,
        "ink_density_map": density_map,
    }


def get_page_preview_rgb(pdf_path, page_idx, dpi=100):
    """Renders page as an RGB PIL Image to serve as the background preview."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(colorspace=fitz.csRGB, dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return img
    finally:
        doc.close()


def generate_heatmap_overlay(rgb_img, density_map, alpha=0.5):
    """Generates a blended image containing the RGB page layout and a semi-transparent ink-density heatmap."""
    orig_w, orig_h = rgb_img.size
    orig_arr = np.array(rgb_img)

    density_map = np.asarray(density_map, dtype=np.float32)
    if density_map.ndim != 2:
        raise ValueError("density_map must be a 2D array representing page ink density.")

    normalized_density = density_map / 100.0 if np.max(density_map) > 1.0 else density_map
    colormap = plt.get_cmap('jet')
    heatmap_rgba = colormap(normalized_density)
    heatmap_rgb = (heatmap_rgba[:, :, :3] * 255).astype(np.uint8)

    heatmap_pil = Image.fromarray(heatmap_rgb).resize((orig_w, orig_h), Image.Resampling.BILINEAR)
    heatmap_arr = np.array(heatmap_pil)

    ink_present = density_map > 1.0
    ink_mask_pil = Image.fromarray((ink_present * 255).astype(np.uint8)).resize((orig_w, orig_h), Image.Resampling.NEAREST)
    ink_mask_arr = np.array(ink_mask_pil) > 0

    blended_arr = orig_arr.copy()
    for c in range(3):
        blended_arr[:, :, c] = np.where(
            ink_mask_arr,
            (alpha * heatmap_arr[:, :, c] + (1 - alpha) * orig_arr[:, :, c]).astype(np.uint8),
            orig_arr[:, :, c],
        )

    return Image.fromarray(blended_arr)


def process_pdf(pdf_path, dpi=150, progress_cb=None, preserve_black=True, ignore_paper_background=True):
    """End-to-end PDF analysis with page-wise CMYK coverage results."""
    valid, msg = validate_pdf(pdf_path)
    if not valid:
        raise ValueError(msg)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    gs_path = find_ghostscript()
    engine = "Ghostscript" if gs_path else "PyMuPDF (Fallback)"

    results = []

    for i in range(total_pages):
        cmyk_arr = None
        if gs_path:
            cmyk_arr = render_page_cmyk_gs(gs_path, pdf_path, i, dpi=dpi)

        if cmyk_arr is None:
            cmyk_arr = render_page_cmyk_pymupdf(pdf_path, i, dpi=dpi)

        rgb_arr = None
        if preserve_black or ignore_paper_background:
            rgb_arr = render_page_rgb_array(pdf_path, i, dpi=dpi)
        if ignore_paper_background:
            cmyk_arr = remove_paper_background_ink(cmyk_arr, rgb_arr)
        if preserve_black:
            cmyk_arr = preserve_neutral_pixels_as_k_only(cmyk_arr, rgb_arr)

        stats = analyze_cmyk_array(cmyk_arr)

        doc_tmp = fitz.open(pdf_path)
        page_tmp = doc_tmp[i]
        width_pts, height_pts = page_tmp.rect.width, page_tmp.rect.height
        doc_tmp.close()

        width_m = (width_pts / 72.0) * 0.0254
        height_m = (height_pts / 72.0) * 0.0254
        area_m2 = width_m * height_m

        page_data = {
            "page_num": i + 1,
            "width_in": width_pts / 72.0,
            "height_in": height_pts / 72.0,
            "area_m2": area_m2,
            "cyan": stats["cyan"],
            "magenta": stats["magenta"],
            "yellow": stats["yellow"],
            "black": stats["black"],
            "ink_density_map": stats["ink_density_map"],
        }

        results.append(page_data)

        if progress_cb:
            progress_cb(i + 1, total_pages)

    return results, engine
