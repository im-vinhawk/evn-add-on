# EVN Vietnam Lovelace card

Sau khi cài integration và tạo config entry EVN Vietnam, Home Assistant phục vụ
card tại `/evn_vietnam/evn-vietnam-energy-card.js`.

Thêm URL đó vào **Settings → Dashboards → Resources** với loại **JavaScript
Module**, rồi thêm manual card. Chọn các entity thực tế mà Home Assistant đã tạo
trong Developer Tools; không tự gõ/suy đoán mã KH hoặc entity ID.

```yaml
type: custom:evn-vietnam-energy-card
title: Điện năng EVN
entity: sensor.<entity_thang_nay_kwh>
cost_entity: sensor.<entity_tien_tam_tinh>
today_entity: sensor.<entity_hom_nay_kwh>
yesterday_entity: sensor.<entity_hom_qua_kwh>
color_scheme: auto # auto | slate | forest | amber | high_contrast
```

`entity` giữ `daily_history` và `monthly_history`; `cost_entity` giữ hóa đơn.
Khi chọn entity tổng, card hiển thị cảnh báo rõ ràng nếu bất kỳ mã KH nào lỗi.
Tổng kWh và tiền luôn là tổng theo từng mã KH: tiền tạm tính không áp lại biểu
giá bậc thang lên kWh đã gộp, còn hóa đơn dùng tổng `TONG_TIEN` chính thức.

Không đưa `/evn_vietnam/…` ra reverse proxy công cộng cho tới khi Home
Assistant đã được bảo vệ bằng cơ chế xác thực của chính nó. Card không gọi API
EVN và không chứa token hay mật khẩu.
