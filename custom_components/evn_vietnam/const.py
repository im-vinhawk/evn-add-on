"""Constants for the EVN Vietnam integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "evn_vietnam"
NAME = "EVN Vietnam"

CONF_CUSTOMER_CODES = "customer_codes"
CONF_USERNAME = "username"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_ID = "device_id"
CONF_PRIMARY_CUSTOMER_CODE = "primary_customer_code"
CONF_CURRENT_CUSTOMER_CODE = "current_customer_code"
CONF_LINKED_CUSTOMERS = "linked_customers"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)
DEFAULT_TIMEOUT = 20
DAILY_HISTORY_DAYS = 31

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
