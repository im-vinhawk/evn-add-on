# EVN Vietnam Lovelace card

Sau khi cài integration và tạo config entry EVN Vietnam, Home Assistant phục vụ
card tại `/evn_vietnam/evn-vietnam-energy-card.js` và tự đăng ký module đó qua
frontend `extra_module_url`. Dashboard mặc định của HA đang chạy **storage
mode**, nên khối `lovelace.resources:` trong `configuration.yaml` **bị bỏ qua**
— đó là nguyên nhân thẻ báo Configuration error dù hard-reload UI.

Không cần thêm Resource bằng tay. Dashboard **EVN Energy** dùng `type: panel`
để thẻ chiếm hết chiều ngang trên web (companion vốn đã full-width). Nếu tự thêm
card vào dashboard masonry, thẻ sẽ bị thu nhỏ.

Chọn các entity thực tế mà Home Assistant đã tạo trong Developer Tools; không tự
gõ/suy đoán mã KH hoặc entity ID.

Biểu đồ 7/14/30 ngày luôn có **một cột mỗi ngày lịch**, kể cả ngày EVN chưa trả
số (cột trống). Nhãn trục không dồn hai ngày kề nhau.

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

Để thay đổi mã KH thuộc tổng hợp, dùng **Settings → Devices & services → EVN
Vietnam → Configure**. Mã tài khoản chính luôn có trong tổng; mã bổ sung được
lưu thành phạm vi tổng hợp và vẫn có entity riêng để hiển thị từng mã. Card chỉ
đổi giữa các entity đã khai báo tại chỗ, không gửi yêu cầu hay thông tin đăng
nhập tới EVN. Nếu chỉ còn một mã trong phạm vi đã chọn, integration không tạo
entity tổng hợp.

Khi cần chọn nhanh giữa tổng hợp và từng mã, khai báo rõ các entity đã có trong
Developer Tools (không đoán entity ID):

```yaml
type: custom:evn-vietnam-energy-card
title: Điện năng EVN
customer_views:
  - id: aggregate
    label: Tổng hợp đã chọn
    entity: sensor.<entity_tong_kwh_thang>
    cost_entity: sensor.<entity_tong_tien>
  - id: customer_one
    label: Mã KH 1
    entity: sensor.<entity_kh_1_kwh_thang>
    cost_entity: sensor.<entity_kh_1_tien>
```

Không đưa `/evn_vietnam/…` ra reverse proxy công cộng cho tới khi Home
Assistant đã được bảo vệ bằng cơ chế xác thực của chính nó. Card không gọi API
EVN và không chứa token hay mật khẩu.
