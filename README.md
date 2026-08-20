# EVN Vietnam for Home Assistant

Home Assistant custom integration for monitoring daily and monthly electricity
consumption, estimated cost, and official EVN bill history for one or more
customer codes.

## What it provides

- Config Flow and reauthentication; the password is used only during login and
  is not stored by this integration.
- Native Home Assistant sensors for each configured customer code and a local
  aggregate when two or more codes are configured.
- A coordinator that serializes EVN customer switching and polls at a
  configurable interval.
- A Lovelace card with daily history, bill history, incomplete-data warnings,
  and selectable color schemes: `auto`, `slate`, `forest`, `amber`, and
  `high_contrast`.

## Calculation contract

EVN provides data per customer code. Aggregate values are calculated locally:

- Energy is the sum of successful per-code values.
- Estimated cost is the sum of each meter's estimate; the tariff is never
  re-applied to aggregate kWh.
- Official bill totals are the sum of EVN `TONG_TIEN` values per bill period.
- When a code fails, the aggregate remains explicitly marked partial.

## HACS installation

1. In HACS, add this GitHub repository as a **custom Integration** repository.
2. Install **EVN Vietnam**, then restart Home Assistant.
3. Add the integration in **Settings → Devices & services** and sign in with
   the EVN app account.
4. Add further customer codes through the integration's **Configure** action.
5. Follow [the dashboard guide](docs/home-assistant-dashboard.md) to add the
   optional Lovelace card.

## Security

Never commit a Home Assistant backup, session file, token, password, customer
roster, APK, or raw upstream response. The integration has no standalone web
server and does not expose an unauthenticated API.

## Development

```sh
pytest -q
node --check custom_components/evn_vietnam/www/evn-vietnam-energy-card.js
```

Live EVN and Home Assistant validation must be performed in a real Home
Assistant instance using the user's own credentials.
