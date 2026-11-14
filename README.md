# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## Hướng dẫn setup & chạy bài nộp (Đinh Hoàng Phong · MSSV 04090)

- **Corpus & Manifest:** [data/manifest.json](data/manifest.json) — 2 PDF công khai của NIST (`NIST.AI.100-1.pdf` và `NIST.AI.600-1.pdf`, tổng **95 trang nội dung hữu ích** / 112 trang vật lý), đặt trong `starter/data/`.
- **Benchmark & Tiêu chí chọn:** [benchmark/questions.json](benchmark/questions.json) & [benchmark/README.md](benchmark/README.md) (**24 câu**: `18 dev / 6 test`, gồm `5` câu metadata filter).
- **Pipeline & Vector Store:** Hiện thực trong [starter/src/](starter/src/) (`ingestion.py`, `chunking.py`, `embeddings.py`, `store.py`, `evaluation.py`, `run.py`), dùng `gemini-embedding-001` (`3072` chiều, `google-genai==3.3.0`) và PostgreSQL 16 + `pgvector 0.8.7`.

Chạy tái lập trên Windows PowerShell:

```powershell
cd starter
Copy-Item .env.example .env   # điền GEMINI_API_KEY và EMBEDDING_MODEL=gemini-embedding-001
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
python -m src.run
```

> Lưu ý: Không commit `starter/.env` hoặc `starter/.venv/`. Đầu ra benchmark, trace và nhãn audit lưu trong `results/`, bảng nhật ký trong [artifacts/experiment_log.csv](artifacts/experiment_log.csv) và báo cáo cá nhân trong [report/individual/04090_DinhHoangPhong.md](report/individual/04090_DinhHoangPhong.md).

### Xem nhanh kết quả & tài liệu nộp bài

- **Báo cáo cá nhân:** [report/individual/04090_DinhHoangPhong.md](report/individual/04090_DinhHoangPhong.md)
- **Bảng nhật ký thí nghiệm (render bảng trực tiếp trên GitHub):** [artifacts/experiment_log.csv](artifacts/experiment_log.csv)
- **Kết quả tổng hợp & audit thủ công:** [results/summary_metrics.json](results/summary_metrics.json) · [results/manual_audit_coherence.json](results/manual_audit_coherence.json) · [results/manual_audit_metadata_and_grounding.json](results/manual_audit_metadata_and_grounding.json)

| Strategy | Chunking / Filter | Chunks | Dev Hit@1 | Dev Ev. Hit@3 | Test Hit@1 | Test Ev. Hit@3 | Filter (Dev / Test) | Coherence `/2` | Grounding Test `/2` |
|---|---|---|---|---|---|---|---|---|---|
| **A** | `fixed_size(500, 50)` / none | 565 | 11/18 (61.11%) | 13/18 (72.22%) | 5/6 (83.33%) | 6/6 (100.00%) | 3/3 / 1/2 | 0.80 (`n=10`) | 1.83 (`n=6`) |
| **B** | `by_paragraph(1100)` / none | 302 | 15/18 (83.33%) | 18/18 (100.00%) | 5/6 (83.33%) | 5/6 (83.33%) | 2/3 / 1/2 | 2.00 (`n=10`) | 1.83 (`n=6`) |
| **C (Chọn)** | `by_paragraph(1100)` / SQL filter | 302 | 15/18 (83.33%) | 18/18 (100.00%) | 6/6 (100.00%) | 6/6 (100.00%) | 3/3 / 2/2 | 2.00 (`n=10`) | 2.00 (`n=6`) |

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.
