# EVN Vietnam setup prompt

```text
Set up EVN Vietnam from https://github.com/im-vinhawk/evn-add-on as a Home Assistant HACS custom integration. Read README.md and README_VN.md first. Add the repository in HACS as an Integration custom repository, install EVN Vietnam, and restart Home Assistant. In Settings → Devices & services, add EVN Vietnam and enter the EVN CSKH national-app login identifier and password only in the Config Flow. Do not put credentials in YAML.

Use Configure on the EVN integration to add only customer codes already linked to the same EVN account and choose the local aggregate selection. Discover the created entities in Developer Tools → States; do not guess entity IDs. Copy docs/evn-dashboard.example.yaml into a YAML dashboard, replace every sensor.evn_* placeholder with the discovered entities, and keep type: panel. The Lovelace card is auto-registered at /evn_vietnam/evn-vietnam-energy-card.js through extra_module_url. In storage-mode dashboards, lovelace.resources YAML is ignored, so do not add a duplicate resource.

Verify that per-meter sensors and the selected aggregate are available, that the aggregate follows the documented calculation contract, and that the card chart has one calendar column per day for 7, 14, and 30-day ranges. Never print, log, commit, or copy passwords, tokens, JWTs, raw EVN responses, phone numbers, customer names, customer codes, or bill data. Report only redacted status and counts. Do not attempt EVN OTP/link-new-customer, automatic iOS-linked-code discovery, or an Energy Dashboard state_class workaround; see the READMEs for current limitations.
```
