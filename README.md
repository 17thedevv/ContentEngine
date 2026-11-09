# Content Engine

CLI Python dựng bản xem trước trên máy theo yêu cầu. Milestone 1 nhận clip, lời đọc WAV và timeline đã chuẩn bị, sau đó dùng FFmpeg để xuất video và chạy kiểm tra kỹ thuật. Antigravity có thể điều phối các lệnh CLI và chỉnh file đầu vào trong workspace.

## Chạy trên Windows

Cần Python 3.12 trở lên, FFmpeg 8 trở lên có `libx264` và bộ lọc `ass` (libass), FFprobe cùng bản build, và font Arial hoặc font thay thế có dấu tiếng Việt. Đặt hai executable trong `PATH`, giải nén bản build vào `.tools/ffmpeg-*/bin/`, hoặc chỉ định đường dẫn bằng `CONTENT_ENGINE_FFMPEG` và `CONTENT_ENGINE_FFPROBE`. Workspace này đã có bản build FFmpeg trong `.tools/`. [Trang tải FFmpeg](https://ffmpeg.org/download.html) · [Bản build Windows](https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
$engine = '.\.venv\Scripts\content-engine.exe'
& $engine channel list
```

Chạy lệnh từ gốc workspace, hoặc truyền `--root` trước tên lệnh. CLI trả JSON UTF-8 trên stdout và ghi lỗi chẩn đoán ra stderr. Mã thoát `0` là thành công, `2` là đầu vào cần xử lý, `1` là lỗi thực thi hoặc QA không đạt, và `130` là bị ngắt.

## Dựng video từ file có sẵn

```powershell
$job = & $engine project create --channel wildlife_vi --topic 'Chủ đề video' | ConvertFrom-Json
$projectId = $job.project_id
$job.directory
```

Trong thư mục dự án, đặt clip vào `media/` và lời đọc PCM WAV vào `audio/`. Điền `assets.json` và `timeline.json` được tạo sẵn. Mỗi project giữ một bản cấu hình kênh tại thời điểm tạo, gồm kích thước, fps và thời lượng mục tiêu.

`assets.json` ghi nguồn gốc và quyền sử dụng footage. Các nguồn bên ngoài cần URL HTTP(S). Nếu giấy phép yêu cầu ghi công, thêm chuỗi `attribution` vào đối tượng `license`.

```json
{
  "schema_version": 1,
  "assets": [
    {
      "asset_id": "clip_01",
      "local_path": "media/clip_01.mp4",
      "source": "original",
      "creator": "Tên người tạo",
      "license": {
        "status": "verified",
        "name": "Footage do người sản xuất tự tạo",
        "checked_at": "2026-11-10",
        "attribution_required": false
      }
    }
  ]
}
```

Giữ nguyên `project_id` và `format` đã được CLI điền vào `timeline.json`. Thêm các cảnh vào `scenes` theo thứ tự dựng. Đây là một phần tử mẫu của mảng.

```json
{
  "id": "scene_01",
  "asset_id": "clip_01",
  "source_start": 0.5,
  "source_end": 7.5,
  "narration": "Nội dung đúng với lời đọc trong file WAV.",
  "narration_audio": "audio/scene_01.wav",
  "subtitle": true,
  "transition": "cut"
}
```

Các mốc nguồn tính theo giây. Engine hỗ trợ cắt thẳng, giữ tỷ lệ khung hình và thêm viền khi cần. Mỗi cảnh có một file WAV và tối đa một cue phụ đề theo thời lượng WAV. Khi lời đọc dài hơn phần hình khả dụng, CLI yêu cầu nới đoạn cắt hoặc thay WAV. Thời lượng đích của kênh được so sánh với timeline trong báo cáo QA.

```powershell
& $engine timeline validate $projectId
& $engine render $projectId
& $engine quality check $projectId
& $engine project status $projectId
& $engine project output $projectId
```

Khi QA đạt, dự án chuyển sang `AWAITING_APPROVAL`. Dùng các đường dẫn từ `project output` để mở `final.mp4`, `subtitles.ass` và `qa_report.json`. Phụ đề được hiển thị sẵn trên hình và có file ASS riêng. QA đối chiếu thông số, giải mã toàn bộ video và âm thanh, kiểm tra phụ đề bên ngoài và ghi lại hồ sơ footage. Người duyệt đối chiếu lời đọc, dữ kiện và giấy phép thực tế trước khi tự đăng.

Nếu quá trình dựng bị ngắt, chạy lại `render` với cùng ID. Engine giữ nguyên các file đầu vào và chỉ công bố một bản dựng sau khi QA đạt. Lệnh `render` trả `reused: true` khi nội dung các manifest, các file media được tham chiếu, cấu hình đầu ra và các file kết quả khớp với lần dựng đạt gần nhất. Các bản dựng thành công được lưu riêng trong `runs/`. SQLite lưu trạng thái cục bộ trong `data/content_engine.db`.

## Video mẫu và kiểm thử

Script demo dành cho PowerShell 7 trên Windows với giọng tiếng Việt Microsoft An đã cài. Nó tạo ba clip đồ họa và ba file WAV rồi đưa chúng vào cùng quy trình CLI như một dự án thông thường. Giọng Microsoft An chỉ được dùng trong script demo.

```powershell
.\examples\render-demo.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Bản demo dùng cấu hình Wildlife và dài 21 giây. Các bài test tích hợp dùng FFmpeg thật để kiểm tra nối cảnh, chữ trên hình, lỗi đầu vào, phục hồi trạng thái và vô hiệu hóa bản dựng cũ. Có thể sao chép một profile YAML để tạo cấu hình kênh khác với ID riêng.

Quy tắc để Antigravity sử dụng CLI nằm trong `.agents/rules/content-engine.md`. [Tài liệu quy tắc của Google Antigravity](https://antigravity.google/docs/rules/)
