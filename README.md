# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bài làm — tóm tắt

Bài nộp đã hoàn thiện pipeline trên starter PostgreSQL + pgvector:

- **Corpus:** *Alice's Adventures in Wonderland* (Project Gutenberg #11, public domain tại Hoa Kỳ),
  **39 đơn vị nội dung**, 142.777 ký tự; manifest và quy tắc đếm ở `data/manifest.json`.
- **Embedding:** `BAAI/bge-base-en-v1.5` (768 chiều, fastembed/ONNX, chạy local, 0 USD).
- **Vector store:** PostgreSQL 16 + pgvector 0.8.7, metric cosine, exact search.
- **Ba cấu hình:** A fixed-size 1.600/200; B paragraph ≤1.000; C = B + filter `section`.
- **Benchmark:** 20 câu (15 dev / 5 test, 6 câu filter) trong `benchmark/benchmark.json`.
- **Kết quả cuối:** A 15/20 Hit@3, B 15/20, C **16/20**; chi tiết, trace và nhãn thủ công ở
  `results/`, phân tích ở `results/analysis.md`, báo cáo ở
  `report/individual/2A202603699_NguyenLeHaiNam.md`.

Chạy lại từ thư mục `starter/`:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
docker compose -p day6_student_starter up -d --wait
.venv/bin/python scripts/check_setup.py
.venv/bin/python ../benchmark/verify_benchmark.py
.venv/bin/python -m src.run --recreate
```

`--recreate` xóa/tạo lại bảng `chunks`; bỏ cờ này khi chạy lại để kiểm tra upsert không nhân đôi.
Lần đầu chạy sẽ tải embedding model local; cần Internet. `starter/.env` chỉ chứa `DATABASE_URL` và
tên model, không chứa API key.

## Bắt đầu nhanh

Bài làm dùng [starter PostgreSQL + pgvector](starter/README.md) và đã hiện thực đầy đủ pipeline;
các mục dưới đây là mô tả yêu cầu gốc của lab.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và báo cáo cá nhân [2A202603699_NguyenLeHaiNam.md](report/individual/2A202603699_NguyenLeHaiNam.md).
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

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện; corpus thật của bài làm nằm ở `data/pages.jsonl` + `data/manifest.json`. Pipeline và benchmark đã được hiện thực; không bắt buộc xây chatbot.
