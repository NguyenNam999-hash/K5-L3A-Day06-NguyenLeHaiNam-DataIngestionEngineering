# Results

| File | Nội dung |
|---|---|
| `traces.json` | 60 trace (3 strategy × 20 câu), gồm câu hỏi, filter, gold, top-3 chunk đầy đủ text/metadata/score và hit. |
| `metrics.json` | Hit@1 / Evidence Hit@3 theo strategy và split. |
| `metrics.csv` | Cùng số liệu ở dạng bảng. |
| `run_metadata.json` | Model, dimension, store, tham số A/B/C, số chunk/token/thời gian, chi phí. |
| `analysis.md` | Phân tích 5 khía cạnh, ví dụ lỗi và đánh đổi. |
| `coherence_audit.csv` | 30 nhãn coherence (10 vị trí × 3 strategy). |
| `coherence_candidates.csv` | Ảnh chụp chunk/neo trước khi chấm coherence; dùng để tái lập mẫu. |
| `metadata_audit.csv` | 45 bản ghi top-3 của 5 câu test; kiểm tra source/page/section và filter. |
| `grounding_audit.csv` | 15 nhãn grounding (5 câu test × 3 strategy). |
| `reingest_check.log` | Log chạy lại không `--recreate`; số chunk không đổi. |
| `final_run.log` | Log lần chạy cuối. |

`traces.json` là nguồn gốc cho mọi bảng; không chỉnh tay file này. Các file audit được tạo từ
trace và đối chiếu với `data/pages.jsonl`; nhãn 0/1/2 do tác giả chấm, không dùng LLM judge.
