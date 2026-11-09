---
trigger: always_on
---

# Content Engine Milestone 1

- Đọc README.md và dùng Content Engine CLI từ workspace này. Các lệnh trả dữ liệu có cấu trúc để nối các bước.
- Với dự án đã có, đọc `project status` trước. Giữ lại project ID, clip, lời đọc và timeline khi thử lại một bước.
- Đặt footage có hồ sơ nguồn và giấy phép vào `media/`, đặt WAV tiếng Việt vào `audio/`, rồi điền `assets.json` và `timeline.json`. Giữ format của dự án.
- Chạy `timeline validate`, `render`, `quality check`, rồi `project output`. Khi dự án bị chặn, xử lý lỗi được báo trước khi dựng lại.
- Giữ quy trình sản xuất cục bộ và dùng các đầu vào đã được cung cấp. Giao bản xem trước để người dùng duyệt và tự đăng.
