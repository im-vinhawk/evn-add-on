# EVN Vietnam cho Home Assistant

EVN Vietnam là custom integration cho HACS, theo dõi điện năng, chi phí ước tính và lịch sử hóa đơn EVN từ tài khoản EVN CSKH. Integration hỗ trợ các mã khách hàng đã liên kết và tổng hợp cục bộ trong Home Assistant.

![Minh họa thẻ EVN Energy](docs/assets/evn-energy-card-demo.png)

Hướng dẫn HACS/GitHub bằng tiếng Anh: [README.md](README.md).

## Tính năng

- Custom integration HACS với Config Flow.
- Sensor từng công tơ và sensor tổng hợp tùy chọn.
- Lưu username và mật khẩu trong Config Entry của Home Assistant; refresh token, đăng nhập lại im lặng và keepalive phiên 8 phút.
- Thẻ Lovelace tự đăng ký qua `extra_module_url` và dashboard panel EVN Energy.
- Biểu đồ 7, 14, 30 ngày có một cột cho mỗi ngày lịch, kể cả ngày EVN chưa trả dữ liệu.

## Yêu cầu

- Home Assistant 2024.8+ (bao gồm các bản 2026.x hiện tại).
- HACS.
- Tài khoản ứng dụng EVN CSKH quốc gia.
- Các mã khách hàng đã liên kết sẵn trong ứng dụng EVN. `PB000001` chỉ là mã ví dụ.

## Cài bằng HACS

1. Trong HACS, mở **Integrations** → menu ba chấm → **Custom repositories**.
2. Thêm `https://github.com/im-vinhawk/evn-add-on` với loại **Integration**.
3. Tìm **EVN Vietnam**, cài đặt và khởi động lại Home Assistant.
4. Vào **Settings → Devices & services → Add integration**, sau đó chọn **EVN Vietnam**.

Đây là custom repository; integration chưa có trong HACS default store.

## Thiết lập lần đầu

Nhập số điện thoại/tên đăng nhập và mật khẩu dùng cho ứng dụng EVN CSKH quốc gia. Home Assistant lưu cả hai trong Config Entry để integration có thể refresh hoặc tự khôi phục phiên EVN.

Hãy coi backup Home Assistant và Config Entry là dữ liệu nhạy cảm vì có mật khẩu. Không đưa thông tin đăng nhập vào YAML, dashboard, issue, log hoặc ảnh chụp màn hình.

## Mã bổ sung và phạm vi tổng hợp

Mở **Settings → Devices & services → EVN Vietnam → Configure**.

Chỉ thêm các mã khách hàng đã liên kết với cùng tài khoản EVN, rồi chọn các mã có mặt trong tổng hợp cục bộ. Tài khoản chính luôn được giữ; mỗi công tơ đã cấu hình vẫn có sensor riêng.

## Dashboard

Sao chép [docs/evn-dashboard.example.yaml](docs/evn-dashboard.example.yaml) vào YAML dashboard và thay mọi placeholder `sensor.evn_*` bằng entity ID trong **Developer Tools → States**. Ví dụ dùng `type: panel` để card có toàn bộ chiều ngang cần thiết.

## Lưu ý về Lovelace card

Integration tự đăng ký `/evn_vietnam/evn-vietnam-energy-card.js` qua `extra_module_url`. Với dashboard storage mode mặc định, `lovelace.resources` trong `configuration.yaml` bị bỏ qua; không thêm YAML resource trùng lặp để xử lý lỗi tải card.

Card đọc `daily_history` từ month sensor đang chọn. Nếu biểu đồ trống, hãy kiểm tra sensor đó trước.

## Bảo mật

- Không commit hoặc chia sẻ mật khẩu, token, JWT, Home Assistant backup, raw EVN response, tên khách hàng, số điện thoại hoặc danh sách mã khách hàng.
- Chỉ kiểm tra qua UI/API đã xác thực của Home Assistant; không đưa đường dẫn card ra reverse proxy công khai chưa có xác thực.
- Diagnostics đã che thông tin đăng nhập và token phiên.

## Hợp đồng tính toán

Tổng hợp được tính cục bộ:

- kWh là tổng kWh của các công tơ thành công.
- Tiền ước tính là tổng tiền bậc thang của từng công tơ; không áp lại biểu giá trên kWh đã cộng.
- Hóa đơn chính thức là tổng `TONG_TIEN` EVN trong cùng kỳ.
- Công tơ lỗi được hiển thị là tổng hợp một phần, không bị coi là 0 một cách im lặng.

## Giới hạn đã biết

- EVN OTP và liên kết khách hàng mới chưa được hỗ trợ vì upstream hiện lỗi NPE.
- Integration không thể tự liệt kê toàn bộ mã đã liên kết qua iOS vì EVN không có list API phù hợp.
- Home Assistant Energy Dashboard vẫn có thể cảnh báo `state_class` (`measurement` so với `total`).
- Cần thêm repository này dưới dạng HACS custom repository để cài đặt.

## Prompt cho agent

Dùng [docs/agent-setup-prompt.md](docs/agent-setup-prompt.md), hoặc sao chép prompt sau:

```text
Set up EVN Vietnam from https://github.com/im-vinhawk/evn-add-on as a Home Assistant HACS custom integration. Read README.md and README_VN.md first. Add the repository in HACS as an Integration custom repository, install EVN Vietnam, and restart Home Assistant. In Settings → Devices & services, add EVN Vietnam and enter the EVN CSKH national-app login identifier and password only in the Config Flow. Do not put credentials in YAML.

Use Configure on the EVN integration to add only customer codes already linked to the same EVN account and choose the local aggregate selection. Discover the created entities in Developer Tools → States; do not guess entity IDs. Copy docs/evn-dashboard.example.yaml into a YAML dashboard, replace every sensor.evn_* placeholder with the discovered entities, and keep type: panel. The Lovelace card is auto-registered at /evn_vietnam/evn-vietnam-energy-card.js through extra_module_url. In storage-mode dashboards, lovelace.resources YAML is ignored, so do not add a duplicate resource.

Verify that per-meter sensors and the selected aggregate are available, that the aggregate follows the documented calculation contract, and that the card chart has one calendar column per day for 7, 14, and 30-day ranges. Never print, log, commit, or copy passwords, tokens, JWTs, raw EVN responses, phone numbers, customer names, customer codes, or bill data. Report only redacted status and counts. Do not attempt EVN OTP/link-new-customer, automatic iOS-linked-code discovery, or an Energy Dashboard state_class workaround; see the READMEs for current limitations.
```

## Phát triển

```sh
pytest -q
node --check custom_components/evn_vietnam/www/evn-vietnam-energy-card.js
node tests/test-evn-vietnam-energy-card-render.js
```
