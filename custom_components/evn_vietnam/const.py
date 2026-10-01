"""Constants for the EVN Vietnam integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "evn_vietnam"
NAME = "EVN Vietnam"

CONF_CUSTOMER_CODES = "customer_codes"
CONF_SELECTED_CUSTOMER_CODES = "selected_customer_codes"
CONF_USERNAME = "username"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_ID = "device_id"
CONF_PRIMARY_CUSTOMER_CODE = "primary_customer_code"
CONF_CURRENT_CUSTOMER_CODE = "current_customer_code"
CONF_LINKED_CUSTOMERS = "linked_customers"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CUSTOMER_ALIASES = "customer_aliases"
CONF_RECONCILE_THRESHOLD_KWH = "reconcile_threshold_kwh"
# Keys blanked in diagnostics. Customer codes are aliased separately
# (models.anonymize_customer_codes) because they also appear as dict keys.
DIAGNOSTICS_TO_REDACT = frozenset(
    {CONF_USERNAME, "password", CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, CONF_DEVICE_ID}
)

DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)
# A bill's kWh is compared with the stored daily kWh; they agree when they differ by at most this many kWh.
DEFAULT_RECONCILE_THRESHOLD_KWH = 1.0
# On the account this was measured on, a bill period [start, end] agrees with the daily rows dated
# [start - 1 day, end - 1 day], as if EVN dated each daily row one day before the consumption it holds.
BILL_DAY_OFFSET = -1
# A period's notice can still be updated (status or amount changed) this many days after it was first seen.
BILL_UPDATE_DAYS = 10
# Seen billing periods remembered per code.
MAX_BILL_KEYS = 36
EVENT_BILL = "evn_vietnam_bill"
SESSION_KEEPALIVE_INTERVAL = timedelta(minutes=8)
DEFAULT_TIMEOUT = 20
DAILY_HISTORY_DAYS = 31
# Last month's final day can reach EVN's daily data late: ask again every few hours until this day of the month.
PREVIOUS_MONTH_TAIL_DAYS = 10
PREVIOUS_MONTH_TAIL_RETRY = timedelta(hours=3)
# Monthly readings change at most daily; one fetch per code serves several refreshes.
MONTHLY_READINGS_CACHE_SECONDS = 6 * 3600
# Historical daily backfill: how far back, how many months one refresh may fetch (for one code only),
# and the pause before each EVN request so the backfill never hammers the service.
MAX_BACKFILL_MONTHS = 36
BACKFILL_MONTHS_PER_CYCLE = 6
BACKFILL_PAUSE_SECONDS = 2.0
CARD_FILENAME = "evn-vietnam-energy-card.js"
CARD_MODULE_URL = f"/{DOMAIN}/{CARD_FILENAME}"

NATIONAL_BASE_URL = "https://cskh.evn.com.vn/cskh/v1"
REGIONAL_GATEWAYS: dict[str, str] = {
    "PB": "https://api.cskh.evnspc.vn/api-cskh-evn",
    "PK": "https://api.cskh.evnspc.vn/api-cskh-evn",
    "PP": "https://api.cskh.evnspc.vn/api-cskh-evn",
    "PA": "https://apicskhevn.npc.com.vn",
    "PM": "https://apicskhevn.npc.com.vn",
    "PN": "https://apicskhevn.npc.com.vn",
    "PH": "https://apicskhevn.npc.com.vn",
    "PT": "https://apicskhevn.npc.com.vn",
    "PC": "https://cskh-api.cpc.vn",
    "PQ": "https://cskh-api.cpc.vn",
    "HN": "https://gwkong.evnhanoi.vn",
    "PD": "https://gwkong.evnhanoi.vn",
    "PE": "https://openapi.evnhcmc.vn/evn-ttcskh/appcskh",
}

ATTRIBUTION = "Data provided by EVN CSKH"
