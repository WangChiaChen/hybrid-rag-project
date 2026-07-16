"""Phase 5：自動生成 Word 分析報告"""
from docx import Document


def generate_report(company, period, metrics_summary, narrative_summary, output_path):
    doc = Document()
    doc.add_heading(f"{company} {period} 財務分析報告", level=1)

    doc.add_heading("一、關鍵指標變化", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "指標", "數值", "變化 (%)"
    for m in metrics_summary:
        row = table.add_row().cells
        row[0].text = str(m.get("name", ""))
        row[1].text = str(m.get("value", ""))
        row[2].text = f"{m.get('change', '')}%"

    doc.add_heading("二、經理人解釋摘要", level=2)
    doc.add_paragraph(narrative_summary or "（無相關敘述資料）")

    doc.save(output_path)


if __name__ == "__main__":
    generate_report(
        company="中信金控",
        period="2026Q1",
        metrics_summary=[{"name": "手續費淨收益", "value": "8054", "change": "3.19"}],
        narrative_summary="手續費淨收益成長主要受惠於財富管理業務回升。",
        output_path="outputs/sample_report.docx"
    )
    print("報告已生成：outputs/sample_report.docx")
