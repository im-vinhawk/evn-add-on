# EVN Vietnam cho Home Assistant

EVN Vietnam là custom integration cho HACS, theo dõi điện năng, chi phí ước tính và lịch sử hóa đơn EVN từ tài khoản EVN CSKH. Integration hỗ trợ các mã khách hàng đã liên kết và tổng hợp cục bộ trong Home Assistant.

![Minh họa thẻ EVN Energy](docs/assets/evn-energy-card-demo.png)

Hướng dẫn HACS/GitHub bằng tiếng Anh: [README.md](README.md).

## Tính năng

- Custom integration HACS với Config Flow.
- Sensor từng công tơ và sensor tổng hợp tùy chọn.
- Lưu username và mật khẩu trong Config Entry của Home Assistant; refresh token, đăng nhập lại im lặng và keepalive phiên 8 phút.
- Thẻ Lovelace tự đăng ký qua `extra_module_url` và dashboard panel EVN Energy.
- Biểu đồ 7, 14, 30 ngày kết thúc ở hôm nay, có một cột cho mỗi ngày lịch, kể cả ngày EVN chưa trả dữ liệu, và ô chọn tháng (tháng hiện tại cùng 12 tháng trước).
- Biệt danh tùy chọn cho từng mã khách hàng, hiển thị trên thẻ.
- Điện năng và chi phí ước tính theo ngày được lưu thành thống kê dài hạn cho bảng Năng lượng, cùng ô so sánh ngày với cùng ngày tháng trước trên thẻ.
- Hóa đơn và chỉ số công tơ giữ bản tốt gần nhất khi một yêu cầu tới EVN thất bại.
- kWh của mỗi hóa đơn được đối chiếu với kWh theo ngày đã thu thập, và mỗi kỳ hóa đơn mới phát sự kiện `evn_vietnam_bill`.
- Hóa đơn chưa thanh toán được đọc cùng lịch sử đã thanh toán: mỗi hóa đơn có trạng thái thanh toán thật (`paid`, `unpaid` hoặc `unknown`, không bao giờ mặc định là "đã thanh toán"), hạn thanh toán và số tiền còn nợ; hóa đơn được thông báo ngay khi EVN phát hành, không đợi đến khi đã trả.
- Lịch ngừng cấp điện theo kế hoạch trong 14 ngày tới, dự kiến chi phí của kỳ hóa đơn đang chạy kèm cảnh báo trước bậc giá kế tiếp, và thẻ có bốn kiểu hiển thị (`mode`) cho dashboard bốn tab.

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

Để cập nhật, mở **EVN Vietnam** trong HACS, chọn **Update information**, rồi **Update** và khởi động lại Home Assistant.

## Thiết lập lần đầu

Nhập số điện thoại/tên đăng nhập và mật khẩu dùng cho ứng dụng EVN CSKH quốc gia. Home Assistant lưu cả hai trong Config Entry để integration có thể refresh hoặc tự khôi phục phiên EVN.

Hãy coi backup Home Assistant và Config Entry là dữ liệu nhạy cảm vì có mật khẩu. Không đưa thông tin đăng nhập vào YAML, dashboard, issue, log hoặc ảnh chụp màn hình.

## Mã bổ sung và phạm vi tổng hợp

Mở **Settings → Devices & services → EVN Vietnam → Configure**.

Chỉ thêm các mã khách hàng đã liên kết với cùng tài khoản EVN, rồi chọn các mã có mặt trong tổng hợp cục bộ. Tài khoản chính luôn được giữ; mỗi công tơ đã cấu hình vẫn có sensor riêng.

## Biệt danh

Sau khi lưu các tùy chọn ở trên, Home Assistant chuyển sang bước **Nicknames** với một ô tùy chọn cho mỗi mã khách hàng (tối đa 30 ký tự; để trống nếu không cần). Biệt danh chỉ lưu trong options của integration, không gửi tới EVN, và hiển thị qua thuộc tính `customer_alias`. Thẻ hiển thị `biệt danh (mã)` trong danh sách chọn và phần đầu thẻ; tên thống kê dùng biệt danh hoặc bốn chữ số cuối của mã.

## Thống kê dài hạn và bảng Năng lượng

Mỗi lần làm mới, integration lưu kWh theo ngày của từng mã và đăng ký chúng làm thống kê dài hạn của Home Assistant (nguồn `evn_vietnam`), nên lịch sử được giữ theo ngày thật thay vì theo thời điểm lấy dữ liệu:

- `evn_vietnam:<mã>_daily_energy` và `evn_vietnam:<mã>_daily_cost` cho từng mã khách hàng, mã viết thường, ví dụ `evn_vietnam:pb000001_daily_energy`.
- `evn_vietnam:total_daily_energy` và `evn_vietnam:total_daily_cost` cho các mã được chọn khi có từ hai mã trở lên. Chúng được dựng lại khi phạm vi chọn thay đổi.
- Tên có dạng `EVN <biệt danh hoặc …bốn số cuối> daily energy` và `… daily cost (estimate)`; tên không chứa toàn bộ mã khách hàng.

Sensor `current_month_consumption` ghi các id này trong thuộc tính `statistics_id` và `cost_statistics_id`.

Để dùng trong bảng Năng lượng: **Settings → Dashboards → Energy → Electricity grid → Add consumption**, chọn thống kê `EVN … daily energy`; với chi phí chọn **Use an entity tracking the total costs** rồi chọn `… daily cost (estimate)` tương ứng. Thống kê chi phí tính bằng VND nên đơn vị tiền tệ của Home Assistant phải là VND. Chỉ dùng thống kê tổng hoặc thống kê từng mã, không dùng cả hai, nếu không cùng một lượng kWh bị cộng hai lần. Developer Tools → Statistics cũng liệt kê chúng.

Cách lịch sử được bổ sung:

- Các ngày của tháng hiện tại được gộp vào mỗi lần làm mới. Trong năm ngày đầu của tháng, tháng trước được lấy lại mỗi ngày một lần vì EVN vẫn có thể hiệu chỉnh. Khi ngày cuối của tháng trước còn thiếu hoặc vẫn là số 0 tạm thời, tháng trước còn được lấy lại ba giờ một lần cho tới ngày 10, để một ngày EVN báo muộn không bị mất.
- Sau khi khởi động lại, các ngày cũ hơn được lấy trong các lần làm mới thường lệ, có thể làm lần đó chậm thêm tới khoảng một phút: mỗi lần làm mới chỉ một mã khách hàng, tối đa sáu tháng, nghỉ ít nhất hai giây trước mỗi yêu cầu. Lùi tối đa 36 tháng, dừng khi gặp hai tháng trống liên tiếp, tạm dừng khi EVN báo bất kỳ lỗi nào rồi tiếp tục ở lần làm mới sau; mã có yêu cầu bị lỗi được xếp sau các mã khác. Lần làm mới đầu tiên sau khi khởi động không bổ sung lịch sử và không xem lại tháng trước, nên quá trình khởi động không bị chậm.
- Diagnostics liệt kê ngày cũ nhất đã lưu của từng mã (mã đã được che) và việc bổ sung đã xong hay chưa.

Giới hạn:

- Mỗi ngày có một điểm, tại 00:00 giờ địa phương, nên hãy xem theo ngày, tuần, tháng hoặc năm; chế độ xem theo giờ không có ý nghĩa.
- EVN báo mỗi ngày chậm khoảng một ngày. Chỉ số 0 của hôm qua hoặc hôm nay được coi là "chưa báo" và chưa có điểm cho tới khi EVN báo.
- Chi phí là ước tính, tính theo phương pháp ở `estimate_method` bên dưới, không phải hóa đơn.
- Thống kê được dựng lại toàn bộ từ các ngày đã lưu sau mỗi thay đổi nên theo kịp hiệu chỉnh của EVN. Lỗi thống kê hoặc bổ sung lịch sử chỉ ghi log mức debug và không làm sensor ngừng cập nhật.

## Dashboard

Sao chép [docs/evn-dashboard.example.yaml](docs/evn-dashboard.example.yaml) vào YAML dashboard và thay mọi placeholder `sensor.evn_*` bằng entity ID trong **Developer Tools → States**. Ví dụ dùng `type: panel` để card có toàn bộ chiều ngang cần thiết.

## Lưu ý về Lovelace card

Integration tự đăng ký `/evn_vietnam/evn-vietnam-energy-card.js` qua `extra_module_url`. Với dashboard storage mode mặc định, `lovelace.resources` trong `configuration.yaml` bị bỏ qua; không thêm YAML resource trùng lặp để xử lý lỗi tải card.

Card đọc `daily_history` từ month sensor đang chọn. Nếu biểu đồ trống, hãy kiểm tra sensor đó trước.

`daily_history`, `today_consumption` và `yesterday_consumption` lấy từ 31 ngày gần nhất trong kho kWh theo ngày cộng với các dòng của lần làm mới hiện tại, nên ngày mùng 1 vẫn thấy tháng trước. `yesterday_consumption` là không rõ (`unknown` trong Home Assistant, `—` trên thẻ) khi EVN chưa báo ngày đó; không bao giờ là số 0 bịa ra, và "hôm qua" của sensor tổng hợp không rõ khi bất kỳ mã nào được chọn không rõ.

Biểu đồ mặc định là 30 ngày gần nhất kết thúc ở hôm nay theo lịch của Home Assistant; ngày EVN chưa báo được vẽ là khoảng trống. Ô chọn phía trên biểu đồ chuyển sang một tháng lịch (tháng hiện tại và 12 tháng trước): tháng được đọc một lần từ thống kê ở trên, hiển thị cùng tổng tháng và, với mỗi hóa đơn của tháng đó, một dòng `Hoá đơn … kWh · Thu thập … kWh · Lệch … kWh · <trạng thái>`. Lựa chọn chỉ được giữ khi thẻ còn mở.

Bên dưới phần tóm tắt, một ô so sánh ngày đã chọn với cùng ngày của tháng trước và với mức trung bình ngày của tháng trước, theo kWh và, nếu có, theo chi phí. Ngày mặc định là ngày mới nhất có dữ liệu; bấm vào một cột của biểu đồ để chọn ngày khác. Ngày thiếu dữ liệu hiển thị `—`, không bao giờ là 0, và ngày 29 đến 31 không có ngày tương ứng trong tháng ngắn hơn. Ô này đọc các thống kê ở trên; khi chưa có thì hiển thị dòng mờ "Chưa có lịch sử".

### Các kiểu hiển thị của thẻ (`mode`)

Thẻ nhận tùy chọn `mode`, để dashboard dùng một thẻ cho mỗi tab. [docs/evn-dashboard-tabs.example.yaml](docs/evn-dashboard-tabs.example.yaml) là dashboard bốn view.

| `mode` | Hiển thị |
|---|---|
| `overview` | biểu ngữ hóa đơn chưa thanh toán (số lượng, tổng nợ, hạn gần nhất, liên kết tới `bills_path`) và biểu ngữ ngừng cấp điện trong 7 ngày tới; ngày mới nhất, tháng đến nay, dự kiến kỳ đang chạy ("dự kiến", kèm gợi ý bậc giá) và đối chiếu gần nhất; mỗi mã một dòng |
| `usage` | ô ngày mới nhất và tháng, hoặc với tháng chọn trong danh sách: tổng, tiền điện (theo hóa đơn nếu có, nếu không thì "≈" từ thống kê chi phí), trung bình ngày và ngày cao nhất; rồi biểu đồ theo ngày |
| `bills` | với một kỳ hóa đơn chọn: mỗi mã có kỳ, kWh hóa đơn, kWh thu thập, chênh lệch, kết quả (Khớp, Lệch, Chưa đủ ngày), số tiền và trạng thái thanh toán, cùng các tổng |
| `meter` | mỗi mã có chỉ số đầu và cuối của kỳ mới nhất, ngày chốt và kWh của kỳ, và chỉ số theo ngày mới nhất |

Không có `mode`, hoặc giá trị không hợp lệ, thẻ giữ bố cục như trước. Ở mọi kiểu, tháng chưa có ngày nào hiển thị "Chưa có số · EVN đăng trễ ~1 ngày" thay vì 0, "Hôm nay" được thay bằng "Ngày mới nhất (dd/mm)", và trạng thái thanh toán EVN không cung cấp hiển thị "Không rõ". `bills_path` (ví dụ `/evn-energy/hoa-don`) chỉ được tạo liên kết khi là đường dẫn bên trong Home Assistant.

Ở kiểu `bills`, tổng kWh chỉ cộng các mã có kết quả Khớp, Lệch hoặc Lệch ranh giới và đủ số liệu hợp lệ về kWh hóa đơn, thu thập và chênh lệch; mọi mã khác được nêu tên dưới các ô tổng cùng lý do ("chưa đủ ngày (n/N)", "chưa có kWh hoá đơn", "không kiểm được" …). Tổng tiền cộng mọi số tiền hóa đơn là số. Bảng xếp thành thẻ nhãn/giá trị khi vùng chứa hẹp (điện thoại). Các tab gọi mỗi mã bằng `label` của view, biệt danh, hoặc `customer_N`; không bao giờ in mã khách hàng.

## Bảo mật

- Không commit hoặc chia sẻ mật khẩu, token, JWT, Home Assistant backup, raw EVN response, tên khách hàng, số điện thoại hoặc danh sách mã khách hàng.
- Chỉ kiểm tra qua UI/API đã xác thực của Home Assistant; không đưa đường dẫn card ra reverse proxy công khai chưa có xác thực.
- Diagnostics đã che thông tin đăng nhập, token phiên và device id; mọi mã khách hàng được thay bằng bí danh (`customer_1`, …) nên có thể đính kèm khi báo lỗi.

## Hợp đồng tính toán

Tổng hợp được tính cục bộ:

- kWh là tổng kWh của các công tơ thành công.
- Tiền ước tính là tổng ước tính riêng của từng công tơ; không áp lại biểu giá trên kWh đã cộng.
- Hóa đơn chính thức là tổng `TONG_TIEN` EVN trong cùng kỳ.
- Công tơ lỗi được hiển thị là tổng hợp một phần, không bị coi là 0 một cách im lặng.
- kWh của hóa đơn lấy từ chỉ số công tơ theo tháng của EVN, ghép với hóa đơn theo tháng và kỳ; thiếu chỉ số thì `total_kwh` là chưa biết (`null`, hiển thị `—`), không bao giờ là 0.
- Trong bảng hóa đơn tổng hợp, `total_kwh` và `calculated_amount` của một kỳ là `null` ngay khi hóa đơn của một công tơ trong kỳ đó không có giá trị, để tổng một phần không trông như đầy đủ. Công tơ không có hóa đơn cho kỳ đó thì không tính.

### Lịch sử tốt gần nhất

Hóa đơn và chỉ số công tơ theo tháng của từng mã được giữ trong bộ nhớ. Khi EVN lỗi, bản thành công gần nhất (dù cũ đến đâu) được hiển thị thay vì lịch sử trống hoặc ngắn đi; hóa đơn đã chốt không đổi nên bản cũ vẫn đúng như bản mới. Chỉ mã chưa từng đọc thành công mới không hiển thị gì. Thuộc tính `history_fetched_at` của `current_month_consumption` là thời điểm của lần lấy thành công mà lịch sử đang hiển thị đến từ đó (với tổng hợp là thời điểm cũ nhất trong các mã). Công tơ lỗi dữ liệu trực tiếp vẫn nằm trong `partial_errors`, nhưng lịch sử của tổng hợp vẫn giữ các hóa đơn tốt gần nhất của nó. Không có gì được ghi ra đĩa, nên khởi động lại khi EVN đang lỗi sẽ bắt đầu từ trống.

### Biểu giá

Tiền ước tính và `calculated_amount` của từng hóa đơn dùng biểu giá điện sinh hoạt EVN có hiệu lực theo từng ngày: bảng áp dụng từ 10/05/2025 (evn.com.vn, VAT 8 %) và các bảng trước đó từ 09/11/2023. Tháng có đổi giá được chia theo số ngày như EVN tính hóa đơn. `calculated_amount` nằm cạnh `total_amount` thật trong `monthly_history` và `bills`; khi hai số bắt đầu lệch nhau là thiếu một đợt đổi giá: thêm một dòng kèm ngày hiệu lực vào `custom_components/evn_vietnam/tariff.py`. Chỉ tính cho tháng dương lịch đầy đủ từ 09/11/2023; kỳ khác có `calculated_amount: null`.

### Kiểm tra biểu giá theo từng mã

Mô hình bậc thang chỉ được tin khi nó tái hiện đúng các hóa đơn thật của mã đó. Thuộc tính `tariff_verified` của `current_month_amount` so `calculated_amount` với `total_amount` trên ba hóa đơn đã chốt gần nhất mà mô hình tính được và có số tiền lớn hơn 0:

- bằng nhau hết: `true`, ước tính dùng mô hình bậc thang (`estimate_method: tiered`);
- có hóa đơn lệch: `false`, ước tính bằng kWh của tháng nhân đơn giá thực tế của mã, là tổng tiền đã trả chia tổng kWh của ba hóa đơn gần nhất có kWh (`estimate_method: effective_price`);
- không có hóa đơn để so: `null`, coi như mô hình bậc thang.

Vì vậy mã tính theo biểu giá khác, hoặc mọi mã sau một đợt đổi giá hay VAT mà `tariff.py` chưa có dòng tương ứng, tự chuyển sang `effective_price`. Thêm dòng còn thiếu vào `tariff.py` sẽ đưa mã về lại khi các hóa đơn gần nhất khớp trở lại. Tổng hợp hiển thị `tariff_verified: false` khi có mã được chọn là `false` và `estimate_method: effective_price` khi có mã được chọn dùng nó; ước tính của tổng vẫn là tổng ước tính từng mã. `calculated_amount` trong lịch sử hóa đơn luôn là phép tính bậc thang thuần, làm mốc so sánh.

### Đối chiếu hóa đơn với số liệu đã thu thập

Mỗi kỳ hóa đơn của một mã (tháng cùng số kỳ; nhiều hóa đơn của một kỳ được gộp) được so với kWh theo ngày đã lưu. Trên tài khoản dùng để đo, hóa đơn của kỳ `[start, end]` khớp với các dòng ngày mang ngày từ `start − 1 ngày` tới `end − 1 ngày`, như thể EVN ghi ngày mỗi dòng trước lượng điện năng nó chứa một ngày; đó là cửa sổ được so (`BILL_DAY_OFFSET` trong `const.py`). Trên dữ liệu dùng để xây tính năng này, cửa sổ đó khớp hóa đơn trong vòng 1 kWh nhiều hơn hẳn so với kỳ như EVN ghi. Vùng EVN khác có thể ghi ngày khác; nếu phần lớn hóa đơn của một mã nằm ngoài ngưỡng, độ lệch ngày cần trở thành một tùy chọn.

`collected_kwh` là tổng các ngày đã lưu của cửa sổ và `diff_kwh = collected_kwh − kWh hóa đơn`. `missing_days` đếm số ngày của cửa sổ chưa được lưu; luôn được báo kèm mọi trạng thái. Trạng thái là quy tắc đầu tiên phù hợp:

| Trạng thái | Ý nghĩa |
|---|---|
| `no_kwh` | chưa biết kWh của hóa đơn hoặc ngày của kỳ |
| `match` | chênh lệch nằm trong ngưỡng |
| `boundary` | hai kỳ liền kề, đủ mọi ngày, lệch ngược chiều và triệt tiêu nhau trong ngưỡng: EVN chốt kỳ lệch một ngày và điện năng chuyển giữa hai hóa đơn. Hiển thị ở cả hai kỳ |
| `incomplete` | thiếu ngày trong cửa sổ và chênh lệch vượt ngưỡng |
| `mismatch` | đủ mọi ngày và chênh lệch vượt ngưỡng |

Ngưỡng là tùy chọn **Bill check tolerance (kWh)** (Configure; 0 đến 100, mặc định 1.0). Mỗi dòng hóa đơn trong `monthly_history` và `bills` có `year`, `month`, `ky`, `collected_kwh`, `diff_kwh`, `missing_days`, `reconcile_status` và `paired_with`; chỉ hóa đơn đầu tiên của kỳ mang kết quả. Ở bản tổng hợp chúng là tổng, thành null ngay khi một mã có hóa đơn kỳ đó không có giá trị, kèm trạng thái xấu nhất của các mã. Thẻ hiển thị hai cột `Thu thập` và `Lệch` trong bảng hóa đơn.

### Sự kiện hóa đơn mới

Lần đầu một kỳ hóa đơn được thấy (kể cả khi còn chưa thanh toán), integration phát một sự kiện Home Assistant `evn_vietnam_bill`. Trong mười ngày sau đó, trạng thái hoặc số tiền của kỳ thay đổi (ví dụ kỳ `incomplete` thành `match` khi một ngày báo muộn tới, hoặc có hóa đơn thứ hai) sẽ phát thêm một sự kiện với `reason: update`. Sau mười ngày kỳ được khóa lại.

| Trường | Ý nghĩa |
|---|---|
| `bill_id` | id 12 ký tự không lộ thông tin của kỳ này của mã này; dùng làm id thông báo |
| `entry_id` | config entry |
| `label` | biệt danh, hoặc bốn ký tự cuối của mã khi biệt danh trống hoặc chứa thứ giống mã khách hàng |
| `period`, `ky` | `MM/YYYY` và số kỳ |
| `period_start`, `period_end` | kỳ như EVN ghi |
| `window_start`, `window_end` | các dòng ngày đã so |
| `bill_kwh`, `collected_kwh`, `diff_kwh`, `missing_days` | kết quả đối chiếu (số có thể là `null` khi `status` là `no_kwh`) |
| `status`, `previous_status` | trạng thái hiện tại và ở thông báo trước (`null` với `new`) |
| `reason` | `new` hoặc `update` |
| `compensates_previous` | `true` khi kỳ là nửa sau của một cặp `boundary` |
| `total_amount`, `calculated_amount` | VND của hóa đơn (cộng các hóa đơn của kỳ) và giá do add-on tự tính |
| `threshold_kwh` | ngưỡng đã dùng |

Sự kiện không bao giờ mang mã khách hàng. Việc phát là tối đa một lần: trạng thái đã thấy được lưu trước khi phát sự kiện, nên nếu sập giữa chừng thì mất đúng một thông báo thay vì lặp lại. Chỉ danh sách hóa đơn mới lấy được mới tính; bản lưu tạm (khi EVN lỗi) không bao giờ khởi tạo hay phát. Ở danh sách mới đầu tiên sau khi cài hoặc nâng cấp, các kỳ cũ được ghi nhận im lặng và chỉ tháng trước hoặc muộn hơn được thông báo.

Sự kiện có thêm `payment_status`, `due_date` và `amount_owed` (`null` khi không rõ). Trả hóa đơn về sau không phải là tin mới và không phát gì. Lần đầu đọc danh sách chưa thanh toán (kể cả khi nâng cấp từ 0.4.0), các kỳ cũ được ghi nhận im lặng và chỉ tháng trước hoặc muộn hơn được thông báo.

```yaml
automation:
  - alias: EVN bill notice
    trigger:
      - platform: event
        event_type: evn_vietnam_bill
    action:
      - service: persistent_notification.create
        data:
          notification_id: "evn_bill_{{ trigger.event.data.bill_id }}"
          title: "EVN bill {{ trigger.event.data.period }} – {{ trigger.event.data.label }}"
          message: >-
            Bill {{ trigger.event.data.bill_kwh }} kWh, collected {{ trigger.event.data.collected_kwh }} kWh,
            difference {{ trigger.event.data.diff_kwh }} kWh ({{ trigger.event.data.status }}).
```

### Trạng thái thanh toán và hóa đơn chưa thanh toán

Lịch sử hóa đơn của EVN chỉ liệt kê hóa đơn đã thanh toán, nên add-on còn đọc danh sách hóa đơn đang chờ thanh toán (`tracuu/hoadon-thanhtoan`) và gộp hai nguồn theo kỳ hóa đơn (năm, tháng, số kỳ).

- Mỗi dòng hóa đơn có `payment_status` (`paid`, `unpaid` hoặc `unknown`), `is_paid` (`true`, `false` hoặc `null`), `due_date`, `amount_owed` (số tiền còn phải trả), `paid_on` và `payment_checked`. Mã trạng thái của EVN quyết định (`DATT` đã thanh toán, `CHUATT` chưa); có ngày thanh toán cũng chứng minh `paid`; mọi trường hợp khác là `unknown`, không bao giờ là "đã thanh toán".
- Kỳ có trong danh sách chưa thanh toán vừa đọc thì lấy theo danh sách đó. Dòng lịch sử đã thanh toán thắng một dòng chưa thanh toán chỉ là bản lưu tạm (hóa đơn trả giữa hai lần làm mới sẽ là `paid`). Dòng chưa thanh toán lưu tạm mà không có gì mâu thuẫn vẫn là `unpaid` với `payment_checked: false`; thẻ ghi "chưa kiểm lại".
- Danh sách chưa thanh toán được hỏi tối đa hai giờ một lần cho mỗi mã, và một lần sau khi khởi động; khi EVN lỗi thì dùng bản tốt gần nhất. Mã không đọc được danh sách này lần nào sẽ có `partial_errors` là `unpaid_bills`, vẫn giữ phần dữ liệu còn lại và trạng thái thanh toán là `unknown`.
- Sensor cho từng mã và cho sensor tổng: `unpaid_amount` (số tiền VND còn nợ; `0` khi không có hóa đơn chưa thanh toán, không rõ khi danh sách chưa tải được) và `next_due_date` (sensor ngày). Month sensor còn có `unpaid_count`, `unpaid_amount`, `next_due_date` và `unpaid_fresh` cho thẻ.
- Mọi sensor có thuộc tính `evn_role` (`meter` hoặc `aggregate`) và `evn_entry` (id config entry). Cả hai không định danh người nào; automation có thể chọn `evn_role == 'meter'` để mỗi công tơ đúng một sensor, dù có bao nhiêu config entry.

### Lịch ngừng cấp điện

Với mỗi mã, add-on hỏi EVN lịch ngừng cấp điện theo kế hoạch từ hôm nay đến 14 ngày tới (`tracuu/ngungcapdien`), tối đa sáu giờ một lần, giữ bản tốt gần nhất. Mỗi mã có sensor thời điểm `next_planned_outage` (không rõ khi không có) với thuộc tính `end`, `status` (mã ngắn của EVN), `upcoming_count` và `outages`, danh sách `{start, end, status}` không được recorder lưu. Lý do, khu vực và tên thiết bị của EVN là văn bản tự do có thể chứa tên địa điểm hoặc người, nên bị bỏ trước khi lưu bất cứ đâu. Mã không đọc được lịch có `partial_errors` là `outages`; dữ liệu khác giữ nguyên.

### Dự kiến kỳ đang chạy

Với mỗi mã, add-on ước tính hóa đơn của kỳ đang chạy từ kWh theo ngày đã lưu:

- Kỳ bắt đầu vào ngày sau khi kỳ gần nhất đã biết kết thúc. Một tháng lịch trọn vẹn được nối tiếp bằng tháng lịch kế tiếp trọn vẹn; kỳ khác được nối tiếp bằng một kỳ cùng độ dài.
- kWh đã thu thập là các ngày đã lưu trong cửa sổ của kỳ (cùng cửa sổ lùi một ngày như phần đối chiếu hóa đơn); tốc độ là trung bình bảy ngày mới nhất có dữ liệu; kWh dự kiến = đã thu thập + tốc độ × số ngày còn lại.
- Số tiền dùng mô hình giá của mã. Mã dùng mô hình bậc thang chỉ có số tiền khi kỳ là một tháng lịch trọn vẹn; kỳ khác không có (`projected_amount: null`) và không có gì được tính theo tỷ lệ.
- Với mã có mô hình bậc thang đã được xác minh, trong tháng lịch không đổi giá, dự kiến còn cho biết bậc đang ở (`tier`), số kWh còn lại trước bậc kế tiếp (`kwh_to_next_tier`) và giá bậc đó chưa VAT (`next_tier_price`).

Sensor `projected_period_amount` mang các giá trị này trong thuộc tính (`period_start`, `expected_end`, `data_until`, `collected_kwh`, `rate_kwh_per_day`, `projected_kwh`, `projected_amount`, `method`, `calendar_month`, `tier`, `kwh_to_next_tier`, `next_tier_price`). Nó không có state class nên ước tính không vào thống kê dài hạn, và sensor tổng không rõ khi một mã không rõ. Đây là ước tính, không phải hóa đơn.

### Quyền riêng tư của dữ liệu mới

Các dòng hóa đơn chưa thanh toán và lịch ngừng điện còn chứa tên, địa chỉ, id hóa đơn, đơn vị quản lý, số công tơ và điểm đo của khách hàng. Không cái nào được chép vào thuộc tính, sự kiện, dòng log, diagnostics hay fixture kiểm thử; diagnostics chỉ ghi tên khóa và kiểu giá trị của từng dòng thô. Mã khách hàng vẫn ở những chỗ đã có (entity id, tên thiết bị, thuộc tính `customer_code`); phần mới không lặp lại nó.

## Giới hạn đã biết

- EVN OTP và liên kết khách hàng mới chưa được hỗ trợ vì upstream hiện lỗi NPE.
- Integration không thể tự liệt kê toàn bộ mã đã liên kết qua iOS vì EVN không có list API phù hợp.
- Home Assistant Energy Dashboard vẫn có thể cảnh báo `state_class` (`measurement` so với `total`).
- Cần thêm repository này dưới dạng HACS custom repository để cài đặt.
- Đối chiếu hóa đơn giả định độ lệch một ngày nêu trên; nó được đo trên một tài khoản duy nhất.
- Các thuộc tính lịch sử ngày, lịch sử tháng và hóa đơn không được recorder lưu (thẻ đọc từ trạng thái hiện tại), nhờ đó mỗi trạng thái nằm dưới giới hạn kích thước thuộc tính của Home Assistant.
- Hóa đơn mà EVN chưa báo kWh được thông báo với `no_kwh`, sau đó cập nhật.
- Không bảo đảm mọi mã khách hàng của một tài khoản đều trả lời yêu cầu hóa đơn chưa thanh toán và lịch ngừng điện qua việc chuyển mã; mã nào không trả lời sẽ có trạng thái thanh toán `unknown` và lịch ngừng điện không rõ, không bao giờ là "đã thanh toán".
- Các mã trạng thái khác `DATT` và `CHUATT` của EVN (trả một phần, quá hạn) được hiển thị là `unknown`.
- Dự kiến giả định kỳ kế tiếp dài bằng kỳ trước (hoặc là tháng lịch kế tiếp); nếu EVN chốt độ dài khác thì ngày kết thúc dự kiến lệch cho đến hóa đơn kế tiếp.

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
node tests/test-evn-vietnam-energy-card-modes.js
```

`tests/test_card_layout_browser.py` mở từng kiểu hiển thị của thẻ trong Chrome hoặc Chromium chạy nền ở chiều ngang máy tính và điện thoại (không cuộn ngang, hàng bảng xếp chồng, không ô trống) và được bỏ qua khi cả hai chưa cài.
