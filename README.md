# PDF CMYK Ink Analyzer

A professional Python web application built with Streamlit for analyzing, estimating, and reporting CMYK ink consumption and coverage in PDF documents (newspapers, magazines, and commercial print jobs).

---

## 🚀 Key Features & Project Capabilities

- **CMYK Coverage Analysis**: Precise per-page and document-wide percentage breakdown for Cyan, Magenta, Yellow, and Black (Key).
- **Ink Weight & Volume Estimation**: Calculates ink consumption in kilograms ($kg$) or milliliters ($ml$) based on paper area and ink weight specs ($g/m^2$).
- **Background & K-Only Corrections**: Smart pre-processing that strips paper background tint and preserves neutral darks as K-only ink to prevent false CMY consumption.
- **Heatmap Overlays**: Visual density heatmaps overlaid on original pages to pinpoint high ink consumption zones.
- **Reporting & Database**: Automated PDF and CSV report generation with historical tracking powered by SQLite.

---

## 🛠️ Tech Stack

| Category | Technologies |
| :--- | :--- |
| **Frontend UI** | Streamlit |
| **PDF Rendering** | PyMuPDF (fitz), Ghostscript (`gswin64c`) |
| **Data Processing** | NumPy, Pillow (PIL) |
| **Data Visualization** | Plotly Express / Graph Objects, Matplotlib |
| **Report Generation** | ReportLab (PDF), CSV |
| **Database** | SQLite3 |

---

## 📐 Calculations Performed

### 1. Page Dimensions & Surface Area
$$\text{Width (m)} = \left(\frac{\text{Width (pt)}}{72}\right) \times 0.0254, \quad \text{Height (m)} = \left(\frac{\text{Height (pt)}}{72}\right) \times 0.0254$$
$$\text{Area } (m^2) = \text{Width (m)} \times \text{Height (m)}$$

### 2. CMYK Coverage Percentage
For each channel $c \in \{C, M, Y, K\}$ across all $N$ pixels on a rendered page matrix:
$$\text{Coverage}_c (\%) = \left( \frac{\sum_{i,j} P_{c,i,j}}{255 \times N} \right) \times 100$$

### 3. Ink Consumption Volume (kg per page)
Given consumption rate $R_c$ ($g/m^2$ at 100% coverage, e.g. $1.5\,g/m^2$):
$$\text{Ink Weight}_c (\text{kg/page}) = \text{Area } (m^2) \times \left(\frac{\text{Coverage}_c}{100}\right) \times \left(\frac{R_c}{1000}\right)$$
$$\text{Total Ink Weight (kg)} = \sum_{\text{pages}} \sum_{c \in \{C, M, Y, K\}} \text{Ink Weight}_c \times \text{Print Run Quantity}$$

---

## 📊 Outputs & Deliverables

- **Dashboard KPI Summary**: Real-time stats on total pages, ink usage ($kg$), net consumption, and average CMYK percentages.
- **Interactive Density Maps**: Visual color-coded heatmaps (`jet` colormap) overlaying high-coverage areas.
- **PDF / CSV Export**: Ready-to-print executive summary reports and full tabular dataset exports.
- **Upload History Analytics**: Filterable database view tracking past analyses by date and edition.

---

## ⚙️ Quick Start

### 1. Installation
```bash
# Clone repository
git clone <repository-url>
cd Ink_Analyzer

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run Application
```bash
streamlit run src/app.py
```
