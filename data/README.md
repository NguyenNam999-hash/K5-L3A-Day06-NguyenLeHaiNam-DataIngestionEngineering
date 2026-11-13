# Data

- `alice_raw.txt` — nguyên văn Project Gutenberg eBook #11, tải từ
  `https://www.gutenberg.org/files/11/11-0.txt` ngày 13/11/2026.
- `pages.jsonl` — 39 đơn vị nội dung đã chuẩn hóa; mỗi dòng có `document_id`, `source`, `page`,
  `text` và `metadata` (`section`, `chapter`, `language`, `document_type`, `char_count`).
- `manifest.json` — nguồn, giấy phép, snapshot, hash file gốc, quy tắc đếm trang và danh sách 39 đơn vị.

Tạo lại từ file gốc:

```bash
cd starter
.venv/bin/python scripts/build_corpus.py
```

License: public domain tại Hoa Kỳ; phân phối lại theo
[Project Gutenberg License](https://www.gutenberg.org/policy/license.html). Không có dữ liệu cá nhân
hoặc dữ liệu mật trong corpus.
