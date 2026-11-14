# Báo cáo cá nhân — Day 6: Data Ingestion Engineering

- **Học viên:** Đinh Hoàng Phong — **MSSV:** `04090` — **Lớp:** `L3B`
- **Chủ đề:** NIST AI Risk Management Framework (AI RMF 1.0 & Generative AI Profile)
- **Thời điểm chạy thực nghiệm:** `2026-11-14T03:08:00Z` (`10:08 +07:00`)

---

## Dữ liệu và pipeline

### 1. Chủ đề, nguồn/quyền, số trang và quy tắc tính trang
Corpus gồm 2 tài liệu kỹ thuật/chính sách công khai thuộc cùng hệ sinh thái quản trị rủi ro AI của NIST (Hoa Kỳ), lưu tại [starter/data/](../../starter/data):

1. **`nist-ai-100-1`** — *Artificial Intelligence Risk Management Framework (AI RMF 1.0)* ([NIST.AI.100-1.pdf](../../starter/data/NIST.AI.100-1.pdf), tháng 01/2023, nguồn: `https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf`):
   - **Quyền sử dụng:** Tài liệu chính phủ liên bang Hoa Kỳ (`17 U.S.C. 105`, public domain tại Hoa Kỳ; NIST cấp quyền phi độc quyền, miễn phí bản quyền để tải lại/sử dụng có ghi nguồn *"Republished courtesy of the National Institute of Standards and Technology"*). Không chứa dữ liệu cá nhân hay mật.
   - **Số trang:** `48` trang vật lý → loại `6` trang phi nội dung gồm trang bìa (`p1`), trang metadata/DOI (`p2`), thông báo bản quyền/version (`p3`), mục lục (`p4`), danh sách hình (`p5`) và bìa sau (`p48`) → còn **`42` trang nội dung hữu ích** (`p6`–`p47`, tương ứng trang in `1`–`42`), tổng `99.998` ký tự sau chuẩn hóa (`raw_chars = 101.860`).
2. **`nist-ai-600-1`** — *Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile* ([NIST.AI.600-1.pdf](../../starter/data/NIST.AI.600-1.pdf), tháng 07/2024, nguồn: `https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf`):
   - **Quyền sử dụng:** Tương tự như trên (công khai, U.S. Federal Government work `17 U.S.C. 105`).
   - **Số trang:** `64` trang vật lý → loại `11` trang phi nội dung gồm bìa/metadata/lời cảm ơn/mục lục (`p1`–`p4`) và các trang chỉ liệt kê tài liệu tham khảo Appendix B (`p58`–`p64`) → còn **`53` trang nội dung hữu ích** (`p5`–`p57`, trang in `1`–`53`), tổng `139.011` ký tự sau chuẩn hóa (`raw_chars = 137.327`).

- **Tổng hợp corpus ([data/manifest.json](../../data/manifest.json), [results/ingestion_log.json](../../results/ingestion_log.json)):** `112` trang vật lý → **`95` trang nội dung hữu ích** (vượt mức tối thiểu `30` trang), tổng `239.009` ký tự sau chuẩn hóa; **`0` trang trống / scan (`empty_pages = []`)**; có `3` trang ít chữ (`< 500` ký tự) đều là trang có sơ đồ hoặc tiêu đề ngắn chuyển mục đã kiểm tra trực tiếp: `nist-ai-100-1` `p16` (`376` ký tự — chú thích Fig. 3), `p39` (`434` ký tự — mở đầu Section 6), và `nist-ai-600-1` `p50` (`383` ký tự — action `MG-4.3-003`). Thời gian ingest + chuẩn hóa: `3.22s`.

### 2. Chuẩn hóa, lỗi và giới hạn
Pipeline đọc PDF bằng `pypdf 6.21.4` qua `_extract_page_with_layout_breaks` ([starter/src/ingestion.py](../../starter/src/ingestion.py)) theo tọa độ dọc `tm[5]` trong content stream để giữ khoảng cách ngắt đoạn (`> 17.5 pt` hoặc nhảy cột), sau đó `normalize_text()` thực hiện:
- Chuẩn hóa Unicode `NFKC` để hợp nhất ký tự ligature (`ﬁ`, `ﬀ`, `ﬂ` → `fi`, `ff`, `fl`).
- Gỡ running header/footer lặp (`NIST AI 100-1 AI RMF 1.0`, `NIST AI 600-1 AI RMF: GAI Profile`, số trang rời) và gộp block bảng `Action ID` (`GV-*`, `MP-*`, `MS-*`, `MG-*`).
- Nối từ bị ngắt gạch nối qua dòng, có danh sách `_KEEP_HYPHEN_PAIRS` để giữ lại gạch nối cho các từ ghép thật (`third-party`, `pre-trained`, `red-teaming`, `real-world`, `decision-making`, `ai-generated`, ...).

**Lỗi và giới hạn gặp phải trong bước ingestion/chunking:**
1. **Dính chữ hiếm gặp ở PDF gốc:** Ví dụ trong `NIST.AI.100-1.pdf` `p9` (`AI RMF,risk`) hoặc `NIST.AI.600-1.pdf` `p11` (`i n`), `p12` (`carbon-i ntensive th an`) do encoding kerning/glyph khoảng trắng bên trong stream PDF gốc.
2. **Tách câu/đoạn qua ranh giới trang:** `load_pdf` đọc độc lập từng trang để giữ chặt truy vết `page`, nên các đoạn văn vắt ngang từ cuối trang trước sang đầu trang sau (như `3.4 Accountable and Transparent` vắt từ `p20` sang `p21` trong `nist-ai-100-1`) vẫn tạo chunk mở đầu cụt ở đầu trang sau (`ple, how a human operator...` tại `nist-ai-100-1:p021:para:000`).
3. **Lỗi heuristic cập nhật `section` ở cấp chunk trong B/C:** Hàm `_detect_chunk_section()` ([starter/src/chunking.py](../../starter/src/chunking.py#L19-L35)) kiểm tra `re.search(r"\bAppendix\s+[A-D]\b", head)` trên 260 ký tự đầu không neo đầu dòng `^`, khiến `8/302` chunk của `NIST.AI.100-1.pdf` (`p6`, `p12`, `p14`, `p16`) có cụm `"(see Appendix A)"` hoặc `"See Appendix A"` bị gán nhầm `section = "appendix"` thay vì `"executive_summary"`/`"foundational"`. (Ngược lại, tại các trang chuyển mục thật như `nist-ai-600-1:p026:para:003` từ `govern` sang `MAP 1.1` và `p032:para:001` từ `map` sang `MEASURE 1.1`, hàm đã cập nhật đúng.)

### 3. Embedding model, vector store, metric, lệnh chạy và chi phí
- **Môi trường & phiên bản:** Python `3.14.2`, `psycopg 3.3.8`, `pypdf 6.21.4`, `python-dotenv 1.2.4`, `google-genai 3.3.0`.
- **Embedding model:** Google Gemini Developer API (`google-genai 3.3.0`), model **`gemini-embedding-001`**, số chiều **`3072`**, giữ nguyên cho cả 3 chiến lược A, B, C.
  - Document embedding dùng `task_type="RETRIEVAL_DOCUMENT"`; Query embedding dùng `task_type="RETRIEVAL_QUERY"` (mỗi câu hỏi embed đúng 1 lần trước khi truy vấn A, B, C).
  - Do Gemini Free Tier giới hạn **`100` items/phút** (`embed_content_free_tier_requests`) và **`30.000` token/phút**, [MyEmbedder](../../starter/src/embeddings.py) dùng `batch_size = 18` và sliding-window pacer (`<= 90` items và `<= 22.000` token ước lượng trong cửa sổ `63s`), kèm cờ `--cache-file` lưu tạm ra ngoài repo để phòng mất tiến độ nếu ngắt quãng. Không dùng vector mock/ngẫu nhiên.
- **Vector store & idempotency:** PostgreSQL **`16.16`** + **`pgvector 0.8.7`** chạy qua Docker (`starter/docker-compose.yml`, port `127.0.0.1:55433`, DB `day6_lab`), lưu bền vững vào bảng `chunks`.
  - Metric tìm kiếm: **cosine distance** (`embedding <=> %s::vector`), `similarity = 1 - distance`, chạy **exact sequential scan** (không tạo HNSW/IVFFlat để tránh sai số xấp xỉ trên 302–565 vector).
  - Metadata filter dùng câu lệnh SQL `WHERE` có tham số hóa trên danh sách cột cho phép (`_ALLOWED_FILTERS`).
  - **Kiểm tra idempotency ([results/summary_metrics.json](../../results/summary_metrics.json#L84-L98)):** Nạp lại toàn bộ `302` chunk của Strategy B không gây nhân đôi (`302 -> 302`, `deleted_obsolete = 0`); probe co tài liệu giả lập từ `3` xuống `1` chunk đã xóa đúng `2` bản ghi lỗi thời (`deleted_obsolete_rows = 2`, còn `1` hàng), và dọn sạch về `0` khi tài liệu rỗng.
- **Lệnh chạy tái lập:**
  ```powershell
  docker compose -f starter/docker-compose.yml -p day6_student_starter up -d --wait
  starter\.venv\Scripts\python.exe starter\scripts\check_setup.py
  Set-Location starter; .venv\Scripts\python.exe -m src.run
  ```
- **Chi phí thực tế:** **\$0.00** (sử dụng Gemini API Free Tier; tổng `73` lượt gọi API cho `888` văn bản thực gửi + `3` cache hits deduplication trong B; `total_api_seconds = 75.091s` thời gian RPC thuần, `api_errors_retried = []`). Giá niêm yết tham chiếu của `gemini-embedding-001` là \$0.15/1M token nhưng phiên chạy này tốn \$0.00 thực chi.

### 4. Metadata bắt buộc và trường lọc bổ sung
- **Metadata bắt buộc cho truy vết:** `document_id` (`TEXT`), `source` (`TEXT`), `page` (`INTEGER`, số trang vật lý PDF), `printed_page` (`TEXT`, số trang in ở chân trang), `char_start`/`char_end` hoặc vị trí chunk tuần tự (`chunk_id`).
- **Trường lọc bổ sung được lập chỉ mục (`CREATE INDEX idx_chunks_strategy_filters ON chunks (strategy, year, document_type, section)`):**
  - `year` (`INTEGER`): `2023` (lấy trực tiếp từ trang metadata AI RMF 1.0) hoặc `2024` (từ trang metadata GAI Profile).
  - `document_type` (`TEXT`): `"core_framework"` cho `nist-ai-100-1` vs `"genai_profile"` cho `nist-ai-600-1`. **Lý do chọn:** GAI Profile lặp lại nguyên văn nhiều mã mục `GOVERN`/`MAP`/`MEASURE`/`MANAGE` của AI RMF 1.0 rồi gắn action riêng cho Generative AI, nên nếu người dùng hỏi chính xác quy định của Core Framework 2023 (ví dụ `q23`), lọc theo `year`/`document_type` giúp tránh nhầm sang tài liệu 2024.
  - `section` (`TEXT`): phân vùng cấu trúc tài liệu (`executive_summary`, `foundational`, `govern`, `map`, `measure`, `manage`, `profiles`, `gai_risks`, `appendix`), gán tự động theo dải trang tài liệu và tinh chỉnh tại heading mở đầu chunk.

---

## Cấu hình và benchmark

### 1. Ba cấu hình thử nghiệm có kiểm soát (A / B / C)
Chi tiết ghi trong [artifacts/experiment_log.csv](../../artifacts/experiment_log.csv):
- **Strategy A (Baseline):** `fixed_size(size=500, overlap=50)` → `565` chunks (`min 53`, `p50 500`, `mean 464.3`, `p95 500`, `max 500` ký tự). Lưu đủ metadata nhưng **không lọc (`filter = none`)**.
- **Strategy B (Thay duy nhất chunking so với A):** `by_paragraph(max_size=1100)` → `302` chunks (`min 131`, `p50 854.5`, `mean 790.1`, `p95 1081`, `max 1100` ký tự), gộp tiêu đề mục với đoạn giải thích và giữ liền mạch khối `Action ID` + `AI Actor Tasks`, không cắt giữa từ/câu. Giữ nguyên model, vector store, metric, metadata; **không lọc (`filter = none`)**.
- **Strategy C (Thay duy nhất filter so với B):** Dùng lại y hệt `302` chunks và `302` embedding vector của Strategy B (`embed_C_reused_from_B = 0.0s`), chỉ kích hoạt SQL `WHERE` filter (`year`, `document_type`, `section`) khi câu hỏi có `filter` cung cấp trước.

### 2. Bộ câu hỏi benchmark (24 câu: 18 dev / 6 test)
Chốt toàn bộ trong [benchmark/questions.json](../../benchmark/questions.json) và khóa trước khi gọi API/DB ([benchmark/README.md](../../benchmark/README.md)):
- **Phân chia:** `18 dev` (`q01`–`q18`, gồm `10` từ `100-1` và `8` từ `600-1`) / `6 test` (`q19`–`q24`, gồm `3` từ `100-1` và `3` từ `600-1`). Tập `test` được giữ làm holdout, không chỉnh sửa tham số hay câu hỏi sau khi chạy.
- **Loại câu hỏi:** `8` `truc_tiep`, `6` `dien_dat_lai`, `5` `dieu_kien_ngoai_le`, `5` `metadata_filter` (`3` trong `dev`: `q16`–`q18`; `2` trong `test`: `q23`–`q24`). Các trường `filter` là **input phạm vi giả định có sẵn trước khi truy xuất** từ ngữ cảnh câu hỏi (không chứa `gold_source_id` hay số trang đích).
- **Kiểm chứng evidence & nguồn hợp lệ đa tài liệu:** Chạy `validate_benchmark()` trong [run.py](../../starter/src/run.py) xác minh mọi `evidence_quote` đều nằm đúng trên trang nguồn sau chuẩn hóa (`24/24` hợp lệ). Ba câu hỏi về subcategory lặp nguyên văn ở cả Core Framework 1.0 và GAI Profile (`q06`, `q07`, `q20`) khai báo `alternative_gold` để không chấm sai oan nguồn hợp lệ tương đương; riêng `q17` và `q23` cố ý hỏi hẹp vào Core Framework 2023 nên không cho phép `alternative_gold`.

### 3. Tiêu chí chọn chiến lược chốt trước khi xem test
1. **Ưu tiên 1:** Tối đa hóa `Evidence Hit@3` trên `dev` và tỷ lệ tuân thủ `Filter` với câu có phạm vi.
2. **Ưu tiên 2:** Cao hơn về `Chunk Coherence` và `Grounding Quality` (không cắt đứt điều kiện, định nghĩa hay bảng action).
3. **Ưu tiên 3:** Cao hơn về `Hit@1` và thấp hơn về chi phí chỉ mục (`chunk_count`, thời gian embed/truy vấn).

---

## Kết quả

### Bảng 1: Độ chính xác truy xuất, số lượng chunk, thời gian và chi phí

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 11/18 (61.11%) | 13/18 (72.22%) | 565 | ingest 3.22s; embed 383.67s; upsert 0.78s; query 0.64s | \$0.00 (Free Tier) |
| A | test | 5/6 (83.33%) | 6/6 (100.00%) | 565 | dùng chung index A | \$0.00 |
| B | dev | 15/18 (83.33%) | 18/18 (100.00%) | 302 | ingest 3.22s; embed 256.03s; upsert 0.41s; query 0.49s | \$0.00 (Free Tier) |
| B | test | 5/6 (83.33%) | 5/6 (83.33%) | 302 | dùng chung index B | \$0.00 |
| C | dev | 15/18 (83.33%) | 18/18 (100.00%) | 302 | ingest 3.22s; embed 0.00s (tái dùng vector B); upsert 0.44s; query 0.55s | \$0.00 |
| C | test | 6/6 (100.00%) | 6/6 (100.00%) | 302 | dùng chung index C | \$0.00 |

*Ghi chú đo đạc phụ:* Thời gian `embed_queries` dùng chung cho cả 3 chiến lược là `9.366s` (`24` câu). Tổng thời gian `embed_A` (`383.67s`) và `embed_B` (`256.03s`) phản ánh wall-clock bao gồm thời gian ngủ giãn nhịp Free Tier 100 RPM (`6` cửa sổ chờ ở A vs `4` cửa sổ chờ ở B/C; tổng thời gian RPC API thực tế là `75.091s`). Nếu xét chỉ số phụ **hợp bằng chứng đa chunk** cho Strategy A trên `dev` (nối 2 chunk kề có bỏ phần `overlap=50` ở `q09` và `q13`), tỷ lệ phủ tăng từ `13/18` lên `15/18` (nhưng vẫn giữ `13/18` làm định nghĩa chính).

### Bảng 2: Đánh giá thủ công Coherence, Metadata, Filter Compliance và Grounding

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0.80/2 (n=10; 0:2, 1:8, 2:0) | 18/18 (test); 54/54 (dev) | dev: 3/3; test: 1/2 | 0: 0, 1: 1, 2: 5 — TB 1.83/2 (n=6 test) |
| B | 2.00/2 (n=10; 0:0, 1:0, 2:10) | 18/18 (test); 51/54 (dev)* | dev: 2/3; test: 1/2 | 0: 0, 1: 1, 2: 5 — TB 1.83/2 (n=6 test) |
| C | 2.00/2 (n=10; 0:0, 1:0, 2:10) | 18/18 (test); 51/54 (dev)* | dev: 3/3; test: 2/2 | 0: 0, 1: 0, 2: 6 — TB 2.00/2 (n=6 test) |

*\*Ghi chú minh bạch về Metadata của B/C:* Trên toàn bộ `6` câu test × top-3 (`18/18` bản ghi mỗi chiến lược), cả `source`, `page`, `printed_page`, `section`, `year`, `document_type` đều đúng `100%`. Tuy nhiên, trên tập `dev` (`54` bản ghi/chiến lược), `3/54` bản ghi thuộc `nist-ai-100-1:p012` (`q02` hạng 1–2 và `q10` hạng 3) bị lỗi heuristic `section="appendix"` do văn bản có `"(see Appendix A)"`, khiến `dev` đạt `51/54` đúng (`nist-ai-600-1:p026:para:003` ở `q17` B hạng 2 chuyển sang `map` là cập nhật đúng trang chuyển mục).

- **Đường dẫn trace & nhãn thủ công:**
  - Số liệu tổng hợp, thời gian & idempotency: [results/summary_metrics.json](../../results/summary_metrics.json)
  - Toàn bộ 72 trace (24 câu × 3 chiến lược): [results/traces_all.json](../../results/traces_all.json)
  - Trace 6 câu test cho audit: [results/test_traces_for_audit.json](../../results/test_traces_for_audit.json)
  - Mẫu Coherence `L01`–`L10` và bảng chấm điểm: [results/coherence_samples.json](../../results/coherence_samples.json) → [results/manual_audit_coherence.json](../../results/manual_audit_coherence.json)
  - Bảng audit Metadata + Filter + Grounding `q19`–`q24`: [results/manual_audit_metadata_and_grounding.json](../../results/manual_audit_metadata_and_grounding.json)
- **Cách chọn mẫu & ghi nhận thiên lệch chấm:** `10` vị trí neo `L01`–`L10` được chốt cố định trong `COHERENCE_ANCHORS` ([run.py](../../starter/src/run.py#L31-L42)) trải đều hai tài liệu trước khi chạy retrieval. Metadata & Grounding được chấm trên **toàn bộ `6/6` câu test** (`18` bản ghi/chiến lược, vượt yêu cầu tối thiểu 5 câu để kiểm đủ cả 2 câu filter trong test). Người chấm là tác giả bài nộp với sự hỗ trợ kiểm tra của AI assistant, do đó có nguy cơ thiên lệch xác nhận; mọi điểm chấm đều kèm trích dẫn văn bản thực tế và các lỗi phát hiện thêm của B/C trên `dev` đều được ghi nhận đầy đủ.

---

## Phân tích và kết luận

### 1. So sánh có kiểm soát A–B (Ảnh hưởng của Chunking) và B–C (Ảnh hưởng của Metadata Filter)
- **So sánh A → B (chỉ thay chunking `fixed_size(500, overlap=50)` sang `by_paragraph(max_size=1100)`):**
  - Trên `dev`, `Hit@1` tăng từ **`11/18` (`61.11%`) lên `15/18` (`83.33%`)** (`+22.22 điểm %`) và `Evidence Hit@3` tăng từ **`13/18` (`72.22%`) lên `18/18` (`100.00%`)** (`+27.78 điểm %`).
  - `Chunk Coherence` trên 10 vị trí neo tăng vọt từ **`0.80/2` lên `2.00/2`**: Strategy A cắt đứt định nghĩa Risk ngay giữa vế 1/2 (`L02 = 0`), cắt đứt định nghĩa Validation (`L04 = 0`), và làm đứt chữ ở `8/10` mẫu còn lại; trong khi B giữ trọn khối tiêu đề + đoạn + thẻ `GAI Risks`.
  - **Tài nguyên giảm mạnh:** Số chunk giảm **`46.5%`** (`565 → 302` chunks), giúp thời gian embed wall-clock giảm **`33.3%`** (`383.67s → 256.03s`, tiết kiệm hạn mức RPD/RPM Gemini) và thời gian query search giảm **`23.6%`** (`0.64s → 0.49s`).
  - **Nhược điểm bất ngờ khi sang B:** Tuy retrieval theo ngữ nghĩa mạnh hơn, do không lọc metadata nên `Filter compliance` trên `dev` của B lại tụt nhẹ từ `3/3` (A) xuống **`2/3`** (cụ thể ở `q17`, B kéo `nist-ai-600-1:p026` ở hạng 2 với `score=0.7758`), và trên `test` B chỉ đạt **`5/6`** ở `Evidence Hit@3` do thất bại ở `q23`.
- **So sánh B → C (giữ nguyên chunk & vector, chỉ thêm SQL metadata filter):**
  - **Dev:** Giữ nguyên `Hit@1 = 15/18` và `Evidence Hit@3 = 18/18`, đồng thời khôi phục `Filter compliance` từ `2/3 → 3/3` (loại `genai_profile` khỏi `q17`).
  - **Test:** `Hit@1` tăng `5/6 → 6/6` (`100%`), `Evidence Hit@3` tăng `5/6 → 6/6` (`100%`), `Filter compliance` tăng `1/2 → 2/2`, và `Grounding` tăng `1.83/2 → 2.00/2`.
  - **Ý nghĩa của trường metadata & hạn chế đi kèm:** Trường `document_type` / `year` kết hợp `section` giúp phân tách hai tài liệu trùng mã mục (`GOVERN 6.2`, `MAP 1.1`) nhưng chỉ hoạt động khi filter được cung cấp chính xác từ đầu và metadata `section` đáng tin. Nếu ứng dụng thực tế không trích xuất được đúng `filter` từ truy vấn người dùng (hoặc dính lỗi gán nhầm `section="appendix"` như 8 chunk ở trên khi câu hỏi lọc `section="foundational"`), bộ lọc cứng SQL `WHERE` có thể **lọc sót** đáp án đúng.

### 2. Phân tích cụ thể các ví dụ thất bại (≥ 2 ví dụ có ID câu và nguồn)
1. **Lỗi cắt giữa điều kiện ở Strategy A — Câu `dev` `q03` (`nist-ai-100-1:p19`) và `q09` (`nist-ai-100-1:p43`):**
   - Với `q03` (*"what must a robust AI system do when operating outside expected deployment conditions?"*), chunk hạng 1 `nist-ai-100-1:p019:fixed:002` của A đặt đúng trang 19 nhưng cắt cụt ngay chữ cuối chunk: `"...if it is operating in an unexpec"`, làm mất hẳn cụm `"ted setting."` ở điều kiện áp dụng (`ev_hit@3 = False`). Tương tự ở `q09`, evidence câu về rủi ro pre-trained model bị xẻ đôi giữa `p043:fixed:003` (`"...and cause issues"`) và `p043:fixed:004` (`"evels of statistical uncertainty and cause issues with bias management..."`) vì `overlap=50` nhỏ hơn độ dài câu (`~195` ký tự). Trong Strategy B, cả hai trang được gom nguyên đoạn nên đều đạt `Hit@1 = True`.
2. **Lỗi xung đột tài liệu cùng mã mục khi thiếu filter ở Strategy B (và rò phạm vi ở A) — Câu `test` `q23` (`nist-ai-100-1:p29`, `filter={"document_type": "core_framework", "section": "govern"}`):**
   - Câu hỏi yêu cầu nội dung của `GOVERN 6.2` trong **Core Framework 2023**. Vì `NIST.AI.600-1.pdf` (2024 GAI Profile) lặp lại nguyên dòng `GOVERN 6.2` kèm rất nhiều đoạn mô tả chi tiết `GV-6.2-001..007`, tìm kiếm vector thuần túy ở **Strategy B** bị kéo toàn bộ **3/3 kết quả top-3** sang `2024 GAI Profile` (`p026`, `p025`, `p026`; `score 0.7292–0.7416`), trượt hoàn toàn nguồn 2023 Core Framework (`hit@1 = False`, `ev_hit@3 = False`, `filter = False`, `Grounding = 1/2`). Ở **Strategy A**, hạng 1 chạm đúng `100-1:p029:fixed:003` nhưng cắt mất tiêu đề cha `GOVERN 6` và vẫn kéo nhầm 2 chunk 2024 ở hạng 2 và hạng 3 (`Filter = False`, `Grounding = 1/2`). Khi **Strategy C** bật SQL filter, cả 2 tài liệu được tách rạch ròi và `100-1:p029:para:001` lên thẳng hạng 1 (`score = 0.7153`, trọn khối `GOVERN 6..6.2`, `Grounding = 2/2`).
3. **Lỗi xếp hạng ngữ nghĩa còn sót ở B/C trên `dev` (ví dụ `q04` `nist-ai-100-1:p21` và `q15` `nist-ai-600-1:p53`):**
   - Ở `q04`, chunk định nghĩa phân biệt `Transparency / Explainability / Interpretability` (`p022:para:000`, `0.7570`) có độ tương đồng cosine cao hơn chút so với đoạn ngoại lệ ở `p021:para:000` (`0.7475`, hạng 2). Ở `q15`, hai đoạn nói tổng quát về `TEVV` và `Measurement gaps` (`0.7172`, `0.7129`) xếp trên đoạn nêu ví dụ kỳ thi chuẩn hóa cho con người (`p053:para:000`, `0.7057`, hạng 3). Điều này cho thấy dù đã chia đoạn tốt, dense cosine thuần túy đôi khi vẫn ưu tiên đoạn tổng quan hơn đoạn chứa điều kiện ngoại lệ/ví dụ cụ thể trong top-1.

### 3. Chiến lược được chọn, đánh đổi và điều kiện áp dụng
- **Chiến lược chọn:** **Strategy C** (`by_paragraph(max_size=1100)` + `pgvector` + SQL metadata filter có kiểm soát).
- **Lý do theo tiêu chí chốt trước:** C đạt tối đa mọi chỉ tiêu ưu tiên trên cả `dev` (`Evidence Hit@3 = 18/18`, `Hit@1 = 15/18`, `Filter = 3/3`) và `test` (`Hit@1 = 6/6`, `Evidence Hit@3 = 6/6`, `Filter = 2/2`), đồng thời đạt `Coherence = 2.00/2`, `Grounding = 2.00/2` với chỉ `302` chunks (ít hơn `46.5%` so với A).
- **Đánh đổi & điều kiện áp dụng:**
  - Chọn `max_size = 1100` (gấp đôi A) làm mật độ thông tin mỗi chunk lớn hơn: thuận lợi giữ ngữ cảnh bảng action/định nghĩa cho LLM grounding, nhưng nếu áp cho corpus có giới hạn context window hẹp hoặc câu hỏi siêu chi tiết ở giữa đoạn dài, điểm tương đồng ngữ nghĩa trung bình toàn đoạn có thể hơi loãng (như hạng 2/3 ở `q04`, `q15`).
  - Lọc SQL của C chỉ phát huy tác dụng an toàn khi câu hỏi/ứng dụng cung cấp filter chuẩn hóa (ví dụ chọn năm/loại tài liệu trên UI) và metadata `section` được phân loại chính xác.

### 4. Hạn chế của thí nghiệm
- **Mẫu test nhỏ (`6` câu, trong đó chỉ có `2` câu filter):** Cho thấy rõ phương sai mẫu nhỏ — ví dụ Strategy A trên `dev` thất bại tới `5/18` câu (`72.22%`), nhưng ngẫu nhiên 6 câu `test` lại không có câu nào có evidence bị cắt đứt giữa chừng ở A nên A vẫn đạt `6/6` `Evidence Hit@3` trên test (dù vẫn rớt `q19` xuống hạng 2 và rớt filter `q23`). Vì vậy **không thể suy rộng** kết quả `100%` của C ngoài corpus NIST 2 tài liệu này.
- **Đo thời gian & chi phí bị chi phối bởi Free Tier rate-limit:** `embed_A` (`383.67s`) và `embed_B` (`256.03s`) chủ yếu là thời gian chờ pacer 100 RPM (`334.5s` qua 6 cửa sổ chờ ở A và `225.4s` qua 4 cửa sổ chờ ở B), còn tổng thời gian RPC mạng thực tế cho toàn bộ phiên chỉ là `75.091s`; lần chạy đầu chưa bật pacer từng gặp lỗi `429` trước khi hoàn tất không lỗi ở lần chạy cuối có pacer.
- **Chấm thủ công & giới hạn dữ liệu:** Nhãn thủ công do chính tác giả thực hiện với hỗ trợ đối chiếu của AI assistant; chiến lược chunking vẫn cắt theo từng trang độc lập (`load_pdf`) và vẫn còn lỗi `_detect_chunk_section` ở `8/302` chunk nêu ở trên.

### 5. Hướng cải tiến nếu làm lại hoặc mở rộng
1. **Sửa lỗi `_detect_chunk_section` bằng neo đầu dòng (`^`):** Chỉ chuyển `section` khi dòng tiêu đề thực sự bắt đầu ở đầu dòng (`^(?:Appendix\s+[A-D][.:]|5\.[1-4]\s+...)`) thay vì `re.search` tự do trong 260 ký tự đầu, loại bỏ hoàn toàn `8/302` chunk gán nhầm `appendix`.
2. **Gộp dòng văn bản liên trang trước khi chia đoạn:** Sau khi gắn anchor trang nguồn cho từng đoạn/câu, ghép các đoạn bị ngắt dở giữa cuối trang `p` và đầu trang `p+1` (lưu `pages: [20, 21]` trong metadata) để loại bỏ đoạn mở đầu cụt từ như `p021:para:000`.
3. **Bổ sung truy xuất lai (Hybrid Dense + BM25/Postgres `tsvector`) + Reranking:** Kết hợp cosine trên `pgvector` với tìm kiếm từ khóa chính xác cho mã mục (`GOVERN 6.2`, `MG-4.3-001`, `Fig. 4`) nhằm kéo các câu chứa ngoại lệ hoặc định danh cụ thể (`q04`, `q13`, `q15`) từ hạng 2–3 lên thẳng hạng 1.
4. **Mở rộng bộ test & chấm độc lập (inter-annotator):** Tăng tập `test` lên `15–20` câu (gồm `5+` câu filter đa dạng cả `section` và `year`) và nhờ người chấm thứ hai chấm mù nguồn chiến lược để loại bỏ thiên lệch tác giả.
