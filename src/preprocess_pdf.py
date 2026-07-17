"""Phase 1.1：把 PDF 每一頁轉成圖片，準備給 VLM 解析"""
import fitz  # pymupdf
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pdf_to_images(pdf_path, output_dir=None, max_pages=None):
    """max_pages：只轉前 N 頁。財報動輒 200-300 頁，但通常只解析前面幾十頁，
    不設限的話會白白多轉上百張 PNG，浪費時間和磁碟。"""
    if output_dir is None:
        output_dir = os.path.join(BASE_DIR, "pages")
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    image_paths = []
    for i, page in enumerate(doc):
        if max_pages is not None and i >= max_pages:
            break
        pix = page.get_pixmap(dpi=150)
        path = os.path.join(output_dir, f"page_{i + 1}.png")
        pix.save(path)
        image_paths.append(path)
    doc.close()
    return image_paths


if __name__ == "__main__":
    pdf_path = os.path.join(BASE_DIR, "sample.pdf")
    if os.path.exists(pdf_path):
        paths = pdf_to_images(pdf_path)
        print(f"已轉換 {len(paths)} 頁，存放於 pages/ 資料夾")
    else:
        print(f"找不到 {pdf_path}，請把你的簡報 PDF 放到專案根目錄並改名 sample.pdf")
