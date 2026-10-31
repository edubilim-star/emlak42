code = '''from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, quote, urlparse
import re
from html import escape
import base64
import inspect
import math
import zipfile
from zoneinfo import ZoneInfo
from functools import partial
import io
import time
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures
import json
import unicodedata

import pandas as pd
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium
from supabase import Client, create_client


st.set_page_config(page_title="Ofis Portföyü", page_icon="🏠", layout="wide")
PHOTO_BUCKET = "property-photos"
_TAB_PARAMS = inspect.signature(st.tabs).parameters
TABS_STATE_SUPPORTED = "key" in _TAB_PARAMS and "on_change" in _TAB_PARAMS
MAX_PHOTO_BYTES = 10 * 1024 * 1024
DOWNLOAD_CALLABLE = "callable" in (st.download_button.__doc__ or "").lower()
TZ = ZoneInfo("Europe/Istanbul")
try:
    from streamlit.delta_generator import DeltaGenerator as _DG

    if "placeholder" in inspect.signature(_DG.multiselect).parameters and not getattr(_DG.multiselect, "_tr_patched", False):
        _multiselect_original = _DG.multiselect

        def _multiselect_tr(self, *args, **kwargs):
            kwargs.setdefault("placeholder", "Seçiniz")
            return _multiselect_original(self, *args, **kwargs)

        _multiselect_tr._tr_patched = True
        _DG.multiselect = _multiselect_tr
        st.multiselect = lambda *args, **kwargs: _multiselect_tr(st._main, *args, **kwargs)
except Exception:
    pass
DEFAULT_OFFICE_NAME = "Ofis Portföyü"
DEFAULT_HOME_TITLE = "Ofis genel görünümü"
NEW_SQL_FILE = "supabase_yeni_ozellikler.sql"
OFFER_STATUSES = ["Beklemede", "Karşı teklif", "Kabul edildi", "Reddedildi", "İptal"]
DEFAULT_PROPERTY_IMAGE = Path(__file__).parent / "assets" / "no-property-photo.svg"

# --- MODERN TASARIM VE ŞABLON STİLLERİ ---
st.markdown("""
<style>
/* ===== GENEL ===== */
.block-container { padding-top: 3.5rem; padding-bottom: 5rem; max-width: 1320px; }
div[data-testid="stVerticalBlock"] { gap: .6rem; }
button[role="tab"] { padding-top: .35rem; padding-bottom: .35rem; }
h1 { letter-spacing: -.5px; }
h2, h3 { letter-spacing: -.3px; }

/* ===== MOBİL ===== */
@media (max-width: 760px) {
  .block-container { padding: 3.2rem .8rem 4.5rem; }
  div[data-testid="stHorizontalBlock"] { flex-direction: column !important; gap: .45rem !important; }
  div[data-testid="stHorizontalBlock"] > div { width: 100% !important; flex: 1 1 100% !important; }
  [class*="st-key-photo_grid"] div[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; }
  [class*="st-key-photo_grid"] div[data-testid="stHorizontalBlock"] > div { width: calc(33% - .4rem) !important; flex: 0 0 calc(33% - .4rem) !important; }
  [class*="st-key-listing_row_"] div[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; gap: .4rem !important; align-items: center !important; }
  [class*="st-key-listing_row_"] div[data-testid="stHorizontalBlock"] > div:first-child { width: 64px !important; flex: 0 0 64px !important; min-width: 64px !important; }
  [class*="st-key-listing_row_"] div[data-testid="stHorizontalBlock"] > div:last-child { width: auto !important; flex: 1 1 auto !important; min-width: 0 !important; }
  h1 { font-size: 1.5rem !important; }
  h2 { font-size: 1.25rem !important; }
  [class*="st-key-hero_controls"] div[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; align-items: center !important; }
  [class*="st-key-hero_controls"] div[data-testid="stHorizontalBlock"] > div { width: auto !important; flex: 1 1 auto !important; min-width: 0 !important; }
  [class*="st-key-hero_controls"] div[data-testid="stHorizontalBlock"] > div:first-child,
  [class*="st-key-hero_controls"] div[data-testid="stHorizontalBlock"] > div:last-child { flex: 0 0 56px !important; }
  .summary-strip { justify-content: flex-start; }
}

/* ===== NAVBAR (Ana başlık butonu) ===== */
.st-key-home_title button {
  background: #ffffff !important;
  border: 1px solid #e2e8f0 !important;
  border-radius: 12px !important;
  padding: .6rem 1.2rem !important;
  min-height: 48px !important;
  box-shadow: 0 1px 3px rgba(15,23,42,.06) !important;
  transition: all .15s ease !important;
}
.st-key-home_title button:hover {
  border-color: #0284c7 !important;
  box-shadow: 0 4px 12px rgba(2,132,199,.12) !important;
}
.st-key-home_title button p {
  font-size: 1.2rem;
  font-weight: 800;
  margin: 0;
  line-height: 1.2;
  letter-spacing: -.4px;
  color: #0f172a !important;
}
.st-key-home_title button p::before {
  content: "🏡 ";
}

/* ===== ÖZET ŞERİDİ (modern metric kartlar) ===== */
.summary-strip {
  display: flex;
  gap: .55rem;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.summary-strip .chip {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: .45rem .9rem;
  font-size: .78rem;
  font-weight: 600;
  color: #64748b !important;
  text-transform: uppercase;
  letter-spacing: .4px;
  min-width: 92px;
  text-align: center;
  line-height: 1.3;
}
.summary-strip .chip b {
  display: block;
  color: #0f172a !important;
  font-size: 1.35rem;
  font-weight: 800;
  margin-top: .1rem;
  letter-spacing: -.5px;
}

/* ===== İLAN LİSTE SATIRLARI ===== */
[class*="st-key-listing_row_"] button {
  text-align: left;
  min-height: 46px;
  padding: .45rem .9rem;
  white-space: normal;
  font-size: .88rem;
  border-radius: 10px !important;
  border: 1px solid transparent !important;
  transition: all .15s ease !important;
}
[class*="st-key-listing_row_"] button:hover {
  background: #f8fafc !important;
  border-color: #e2e8f0 !important;
}

/* ===== FİYAT KARTI ===== */
.price-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: .7rem 1rem;
  line-height: 1.45;
  font-size: .9rem;
  color: #0f172a !important;
}
.price-card .price {
  font-size: 1.7rem;
  font-weight: 800;
  color: #0284c7;
  line-height: 1.2;
  letter-spacing: -.5px;
}

/* ===== DEAL CARD (ilan detay fiyat kartı) ===== */
.deal-card {
  background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #0284c7 100%);
  border-radius: 14px;
  padding: .9rem 1.1rem;
  box-shadow: 0 8px 20px -6px rgba(15,23,42,.4);
}
.deal-card, .deal-card * { color: #ffffff !important; }
.deal-card .dc-top { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
.deal-card .dc-price { font-size: 1.7rem; font-weight: 800; line-height: 1.15; letter-spacing: -.5px; }
.deal-card .dc-price.muted { font-size: 1.05rem; font-weight: 600; opacity: .85; }
.deal-card .dc-status { font-size: .72rem; font-weight: 700; padding: .22rem .75rem; border-radius: 999px; white-space: nowrap; text-transform: uppercase; letter-spacing: .3px; }
.deal-card .dc-chips { display: flex; flex-wrap: wrap; gap: .35rem; margin: .65rem 0 .55rem; }
.deal-card .dc-chip { background: rgba(255,255,255,.16); border-radius: 999px; padding: .18rem .75rem; font-size: .78rem; font-weight: 600; }
.deal-card .dc-row { font-size: .88rem; margin-top: .25rem; }

/* ===== HERO BANNER ===== */
.hero {
  position: relative;
  height: 380px;
  border-radius: 16px;
  overflow: hidden;
  background-size: cover;
  background-position: center;
  background-color: #0f172a;
  box-shadow: 0 10px 15px -3px rgba(0,0,0,.1);
}
.hero .hero-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(15,23,42,.92) 0%, rgba(15,23,42,.45) 45%, rgba(15,23,42,0) 75%);
}
.hero .hero-body {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  padding: 1.6rem 1.5rem 1.3rem;
}
.hero, .hero * { color: #ffffff !important; }
.hero .hero-badge {
  display: inline-block;
  background: #0284c7;
  font-size: .7rem;
  font-weight: 700;
  letter-spacing: .6px;
  padding: .28rem .85rem;
  border-radius: 999px;
  text-transform: uppercase;
}
.hero .hero-title {
  font-size: 1.55rem;
  font-weight: 800;
  line-height: 1.25;
  margin: .5rem 0 .4rem;
  letter-spacing: -.5px;
  text-shadow: 0 2px 8px rgba(0,0,0,.5);
}
.hero .hero-meta {
  font-size: .95rem;
  opacity: .95;
}
.hero .hero-price {
  color: #38bdf8 !important;
  font-weight: 800;
  font-size: 1.3rem;
  margin-right: .7rem;
  letter-spacing: -.3px;
}
.hero-dots { text-align: center; letter-spacing: .25rem; font-size: .8rem; color: #94a3b8; }
@media (max-width: 760px) {
  .hero { height: 230px; }
  .hero .hero-title { font-size: 1.15rem; }
  .hero .hero-body { padding: 1rem 1rem .9rem; }
}

/* ===== AVATAR ===== */
.avatar-ph {
  width: 96px; height: 96px;
  border-radius: 50%;
  background: linear-gradient(135deg, #e0e7ff, #bae6fd);
  color: #0f172a !important;
  display: flex; align-items: center; justify-content: center;
  font-size: 2.2rem; font-weight: 800;
}

/* ===== ÖZELLİK TABLOSU ===== */
.property-feature-table {
  width: 100%;
  border-collapse: collapse;
  font-size: .84rem;
  border-radius: 12px;
  overflow: hidden;
}
.property-feature-table th, .property-feature-table td {
  padding: .38rem .6rem;
  border-bottom: 1px solid #eaecf0;
  text-align: left;
  background: #fff;
  color: #0f172a !important;
}
.property-feature-table th {
  width: 45%;
  color: #64748b !important;
  font-weight: 600;
  background: #f8fafc;
}
</style>
""", unsafe_allow_html=True)


def get_client() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["anon_key"])


def get_admin_client() -> Client:
    key = st.secrets["supabase"].get("service_role_key") or st.secrets["supabase"].get("secret_key")
    if not key:
        raise RuntimeError("Yönetici işlemleri için Supabase service_role/secret key ayarlanmamış.")
    return create_client(st.secrets["supabase"]["url"], key)


def now_tr() -> datetime:
    return datetime.now(TZ)


def feature_setup_message(feature: str, exc: Exception | None = None) -> None:
    st.warning(f"{feature} için Supabase'de ek tablolar gerekli. `{NEW_SQL_FILE}` dosyasını Supabase SQL Editor'da bir kez çalıştırın.")
    path = Path(__file__).parent / NEW_SQL_FILE
    if path.exists():
        with st.expander("Çalıştırılacak SQL"):
            st.code(path.read_text(encoding="utf-8"), language="sql")
    if exc is not None:
        st.caption(str(exc))


@st.cache_data(ttl=60, show_spinner=False)
def get_settings(_client: Client) -> dict:
    try:
        data = _client.table("app_settings").select("*").execute().data or []
    except Exception:
        return {}
    return {item["key"]: item.get("value") for item in data}


def load_announcements(client: Client, only_active: bool = True) -> list[dict]:
    try:
        query = client.table("announcements").select("*").order("created_at", desc=True)
        if only_active:
            query = query.eq("active", True)
        return query.execute().data or []
    except Exception:
        return []


def show_announcements(client: Client) -> None:
    items = load_announcements(client)
    for item in items[:3]:
        body = f"\n\n{item['body']}" if item.get("body") else ""
        st.info(f"**{item['title']}**{body}", icon="📢")
    if len(items) > 3:
        with st.expander(f"Diğer {len(items) - 3} duyuru"):
            for item in items[3:]:
                st.markdown(f"**{item['title']}**  \n{item.get('body') or ''}")


def settings_admin_tab(client: Client, admin_client: Client) -> None:
    settings = get_settings(client)
    st.caption("Burada yazdıklarınız tüm kullanıcılarda görünür. Ofis adı sol menüde ve PDF/Excel çıktılarının başlığında kullanılır.")
    office = st.text_input("Ofis / uygulama adı", value=settings.get("office_name") or DEFAULT_OFFICE_NAME, key="admin_office_name")
    home = st.text_input("Ana sayfa başlığı (üstteki buton)", value=settings.get("home_title") or DEFAULT_HOME_TITLE, key="admin_home_title")
    if st.button("Başlıkları kaydet", type="primary", key="admin_save_titles"):
        try:
            admin_client.table("app_settings").upsert([
                {"key": "office_name", "value": office.strip() or DEFAULT_OFFICE_NAME},
                {"key": "home_title", "value": home.strip() or DEFAULT_HOME_TITLE},
            ]).execute()
            get_settings.clear()
            st.success("Başlıklar kaydedildi.")
            st.rerun()
        except Exception as exc:
            feature_setup_message("Başlık ayarları", exc)


def announcements_admin_tab(admin_client: Client) -> None:
    try:
        items = admin_client.table("announcements").select("*").order("created_at", desc=True).execute().data or []
    except Exception as exc:
        feature_setup_message("Duyurular", exc)
        return
    generation = st.session_state.get("news_generation", 0)

    def bump() -> None:
        st.session_state["news_generation"] = generation + 1

    with st.expander("➕ Yeni duyuru ekle", expanded=not items):
        title = st.text_input("Duyuru başlığı *", key=f"news_title_{generation}")
        body = st.text_area("Duyuru metni", height=90, key=f"news_body_{generation}")
        if st.button("Duyuruyu yayınla", type="primary", key=f"news_add_{generation}"):
            if not title.strip():
                st.error("Duyuru başlığını yazın.")
            else:
                admin_client.table("announcements").insert({"title": title.strip(), "body": body.strip(), "active": True}).execute()
                bump()
                st.rerun()
    for item in items:
        item_id = str(item["id"])
        created = pd.to_datetime(item.get("created_at"), errors="coerce")
        stamp = created.strftime("%d.%m.%Y") if pd.notna(created) else ""
        with st.expander(f"{'🟢' if item.get('active') else '⚪'} {item['title']} · {stamp}"):
            new_title = st.text_input("Başlık", value=item["title"], key=f"news_edit_title_{item_id}_{generation}")
            new_body = st.text_area("Metin", value=item.get("body") or "", height=90, key=f"news_edit_body_{item_id}_{generation}")
            active = st.checkbox("Yayında (kullanıcılara gösterilsin)", value=bool(item.get("active")), key=f"news_edit_active_{item_id}_{generation}")
            b1, b2, b3 = st.columns([1, 1, 1], vertical_alignment="center")
            if b1.button("Güncelle", type="primary", key=f"news_update_{item_id}_{generation}", use_container_width=True):
                admin_client.table("announcements").update({"title": new_title.strip(), "body": new_body.strip(), "active": active, "updated_at": now_tr().isoformat()}).eq("id", item_id).execute()
                bump()
                st.rerun()
            confirm = b2.checkbox("Silmeyi onaylıyorum", key=f"news_confirm_{item_id}_{generation}")
            if b3.button("Duyuruyu sil", disabled=not confirm, key=f"news_delete_{item_id}_{generation}", use_container_width=True):
                admin_client.table("announcements").delete().eq("id", item_id).execute()
                bump()
                st.rerun()


def service_client(fallback: Client) -> Client:
    try:
        return get_admin_client()
    except Exception:
        return fallback


def login(client: Client) -> None:
    st.title("🏠 Ofis Portföyü")
    st.caption("Daireleri ve müşteri taleplerini tek ekranda yönetin.")
    with st.form("login"):
        email = st.text_input("E-posta")
        password = st.text_input("Şifre", type="password")
        submitted = st.form_submit_button("Giriş yap", type="primary")
    if submitted:
        try:
            result = client.auth.sign_in_with_password({"email": email, "password": password})
            st.session_state.user = result.user
            st.session_state.supabase_access_token = result.session.access_token
            st.session_state.supabase_refresh_token = result.session.refresh_token
            st.rerun()
        except Exception:
            st.error("Giriş yapılamadı. E-posta ve şifrenizi kontrol edin.")


def rows(client: Client, table: str) -> list[dict]:
    return client.table(table).select("*").order("created_at", desc=True).execute().data


@st.cache_data(ttl=86400, show_spinner=False)
def get_location_data() -> tuple[list[str], dict[str, list[str]]]:
    province_response = requests.get("https://api.turkiyeapi.dev/v2/datasets/2025/provinces.json", timeout=12)
    province_response.raise_for_status()
    district_response = requests.get("https://api.turkiyeapi.dev/v2/datasets/2025/districts.json", timeout=12)
    district_response.raise_for_status()
    provinces_raw = province_response.json()
    districts_raw = district_response.json()
    provinces_raw = provinces_raw.get("data", provinces_raw) if isinstance(provinces_raw, dict) else provinces_raw
    districts_raw = districts_raw.get("data", districts_raw) if isinstance(districts_raw, dict) else districts_raw
    provinces = {}
    for item in provinces_raw:
        name = item.get("name")
        if isinstance(name, dict):
            name = name.get("local") or name.get("tr")
        if name:
            provinces[str(item.get("id"))] = str(name)
    districts: dict[str, list[str]] = {name: [] for name in provinces.values()}
    for item in districts_raw:
        province_id = item.get("provinceId", item.get("province_id"))
        if isinstance(item.get("province"), dict):
            province_id = item["province"].get("id", province_id)
        province_name = provinces.get(str(province_id))
        name = item.get("name")
        if isinstance(name, dict):
            name = name.get("local") or name.get("tr")
        if province_name and name:
            districts[province_name].append(str(name))
    if len(provinces) != 81:
        raise ValueError("İl listesi beklenen 81 il sayısında değil.")
    return sorted(provinces.values(), key=str.casefold), {k: sorted(v, key=str.casefold) for k, v in districts.items()}


def parse_google_maps_location(url: str) -> tuple[str | None, str | None]:
    patterns = [r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)"]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    query = parse_qs(urlparse(url).query)
    for key in ("q", "query", "ll", "center"):
        value = query.get(key, [""])[0]
        match = re.search(r"(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)", value)
        if match:
            return match.group(1), match.group(2)
    return None, None


def upload_property_photos(client: Client, property_id: str, files: list) -> tuple[int, list[str]]:
    uploaded = 0
    errors = []
    bucket = client.storage.from_(PHOTO_BUCKET)
    for image in files:
        content = image.getvalue()
        if image.type not in {"image/jpeg", "image/png", "image/webp"}:
            errors.append(f"{image.name}: JPG, PNG veya WebP yükleyin.")
            continue
        if len(content) > MAX_PHOTO_BYTES:
            errors.append(f"{image.name}: dosya 10 MB sınırını aşıyor.")
            continue
        suffix = Path(image.name).suffix.lower()
        object_path = f"{property_id}/{uuid4().hex}{suffix}"
        try:
            bucket.upload(object_path, content, file_options={"content-type": image.type, "upsert": "false"})
            uploaded += 1
        except Exception as exc:
            errors.append(f"{image.name}: {exc}")
    return uploaded, errors


def show_property_gallery(client: Client, property_id: str) -> None:
    try:
        bucket = client.storage.from_(PHOTO_BUCKET)
        objects = bucket.list(property_id)
        if not objects:
            st.info("Bu ilana henüz fotoğraf eklenmemiş.")
            return
        columns = st.columns(3)
        for i, obj in enumerate(objects):
            path = f"{property_id}/{obj['name']}"
            response = bucket.create_signed_url(path, 3600)
            url = response.get("signedURL") or response.get("signedUrl")
            if url:
                columns[i % 3].image(url, caption=obj["name"], width="stretch")
    except Exception as exc:
        st.error("Fotoğraflar alınamadı.")
        st.caption(str(exc))


def get_property_photos(client: Client, property_id: str) -> list[dict]:
    bucket = client.storage.from_(PHOTO_BUCKET)
    objects = bucket.list(property_id, {"limit": 100, "sortBy": {"column": "name", "order": "asc"}})
    photos = []
    for obj in objects or []:
        if not obj.get("id"):
            continue
        path = f"{property_id}/{obj['name']}"
        response = bucket.create_signed_url(path, 3600)
        url = response.get("signedURL") or response.get("signedUrl")
        if url:
            photos.append({"name": obj["name"], "url": url})
    return photos


def embed_html(html: str, height: int) -> None:
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        st.components.v1.html(html, height=height)


def embed_page(url: str, height: int) -> None:
    if hasattr(st, "iframe"):
        st.iframe(url, height=height)
    else:
        st.components.v1.iframe(url, height=height, scrolling=False)


GALLERY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:system-ui,-apple-system,sans-serif;background:transparent}
.main{position:relative;height:290px;background:#101828;border-radius:12px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.main img{max-width:100%;max-height:100%;object-fit:contain;cursor:zoom-in;display:block}
.nav{position:absolute;top:50%;margin-top:-22px;width:44px;height:44px;border:0;border-radius:50%;background:rgba(255,255,255,.88);font-size:28px;line-height:1;color:#101828;cursor:pointer}
.prev{left:8px}.next{right:8px}
.count{position:absolute;bottom:8px;right:10px;background:rgba(0,0,0,.6);color:#fff;font-size:12px;padding:3px 9px;border-radius:12px}
.thumbs{display:flex;gap:6px;margin-top:8px;overflow-x:auto;padding-bottom:4px}
.thumbs img{height:54px;width:74px;object-fit:cover;border-radius:6px;border:2px solid transparent;opacity:.65;flex:none;cursor:pointer}
.thumbs img.active{border-color:#2563eb;opacity:1}
.hint{font-size:12px;color:#667085;margin-top:2px}
</style></head><body>
<div class="main" id="main"><img id="mainImg" alt="İlan fotoğrafı"><button type="button" class="nav prev" id="prev">&#8249;</button><button type="button" class="nav next" id="next">&#8250;</button><div class="count" id="count"></div></div>
<div class="thumbs" id="thumbs"></div>
<div class="hint">Büyütmek için fotoğrafa dokunun</div>
<script>
const photos = __PHOTOS__;
let idx = 0;
const $ = id => document.getElementById(id);
const mainImg = $("mainImg"), count = $("count"), thumbs = $("thumbs");
if (photos.length < 2) { $("prev").style.display = "none"; $("next").style.display = "none"; thumbs.style.display = "none"; }
photos.forEach((url, i) => { const t = document.createElement("img"); t.src = url; t.loading = "lazy"; t.onclick = () => show(i); thumbs.appendChild(t); });
function show(i) {
  idx = (i + photos.length) % photos.length;
  mainImg.src = photos[idx];
  count.textContent = (idx + 1) + " / " + photos.length;
  Array.from(thumbs.children).forEach((t, j) => t.classList.toggle("active", j === idx));
}
$("prev").onclick = () => show(idx - 1);
$("next").onclick = () => show(idx + 1);
show(0);
</script></body></html>"""


def render_photo_gallery(photos: list[dict]) -> None:
    urls = json.dumps([p["url"] for p in photos]).replace("</", "<\\/")
    embed_html(GALLERY_HTML.replace("__PHOTOS__", urls), 390)


def can_manage_property(row: pd.Series, user_id: str, is_admin: bool) -> bool:
    return is_admin or str(row.get("owner_id")) == user_id or str(row.get("created_by")) == user_id


def show_property_detail(client: Client, property_row: pd.Series, user_id: str, is_admin: bool, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]], favorites: set[str]) -> None:
    property_id = str(property_row.id)
    manage = can_manage_property(property_row, user_id, is_admin)
    back_col, fav_col, title_col = st.columns([1, 1.8, 4.5], vertical_alignment="center")
    if back_col.button("← Liste", key="detail_back", use_container_width=True):
        st.session_state.pop("selected_property_id", None)
        st.rerun()
    is_favorite = property_id in favorites
    fav_col.button("★ Favorilerden kaldır" if is_favorite else "☆ Favorilere ekle", key=f"fav_{property_id}", use_container_width=True,
                   type="primary" if is_favorite else "secondary", on_click=set_favorite, args=(client, user_id, property_id, not is_favorite))
    title_col.markdown(
        f"<div style='font-size:1.25rem;font-weight:700;line-height:1.25'>{escape(str(property_row.title))}</div>"
        f"<div style='color:#667085;font-size:.85rem'>{escape(str(property_row.get('property_category') or 'Daire'))} · {escape(str(property_row.get('city', '')))} / {escape(str(property_row.get('district', '')))} · İlan No: {property_id[:8].upper()}</div>",
        unsafe_allow_html=True,
    )
    left, right = st.columns([1.5, 1], gap="medium")
    try:
        photos = get_property_photos(client, property_id)
    except Exception:
        photos = []
    with left:
        if photos:
            render_photo_gallery(photos)
        else:
            st.image(str(DEFAULT_PROPERTY_IMAGE), width="stretch", alt="İlan için varsayılan fotoğraf")
            st.caption("Bu ilana henüz fotoğraf eklenmemiş.")
    with right:
        price_value = int(float(property_row.get("price") or 0))
        status_text = _clean(property_row.get("status")) or "Aktif"
        status_color = {"Aktif": "#16a34a", "Opsiyonlu": "#d97706", "Kiralandı/Satıldı": "#64748b", "Pasif": "#dc2626"}.get(status_text, "#475569")
        chips = [_clean(property_row.get("deal_type")), _clean(property_row.get("rooms")), (_clean(property_row.get("area_m2")) + " m²") if _clean(property_row.get("area_m2")) else "", _clean(property_row.get("property_category"))]
        chip_html = "".join(f"<span class='dc-chip'>{escape(chip)}</span>" for chip in chips if chip)
        price_html = f"<div class='dc-price'>{money_text(price_value)} ₺</div>" if price_value else "<div class='dc-price muted'>Fiyat belirtilmemiş</div>"
        st.markdown(
            f"<div class='deal-card'><div class='dc-top'>{price_html}<span class='dc-status' style='background:{status_color}'>{escape(status_text)}</span></div>"
            f"<div class='dc-chips'>{chip_html}</div>"
            f"<div class='dc-row'>📍 {escape(_clean(property_row.get('city')))} / {escape(_clean(property_row.get('district')))}</div>"
            f"<div class='dc-row'>👤 Sorumlu: <b>{escape(_clean(property_row.get('sorumlu')) or 'Atanmamış')}</b></div></div>",
            unsafe_allow_html=True,
        )
        detail_rows = [
            ("Brüt / net m²", f"{property_row.get('area_m2') or '—'} / {property_row.get('net_area_m2') or '—'}"),
            ("Bina yaşı", property_row.get("building_age")),
            ("Bulunduğu kat", property_row.get("floor")),
            ("Kat sayısı", property_row.get("total_floors")),
            ("Isıtma", property_row.get("heating")),
            ("Banyo", property_row.get("bathroom_count")),
            ("Eşyalı", "Evet" if property_row.get("furnished") else "Hayır"),
            ("Aidat", f"{int(property_row.get('monthly_dues') or 0):,}".replace(",", ".") + " ₺"),
        ]
        detail_rows = [(label, value) for label, value in detail_rows if value not in (None, "") and not (isinstance(value, float) and pd.isna(value))]
        table_rows = "".join(f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>" for label, value in detail_rows)
        st.markdown(f"<table class='property-feature-table'>{table_rows}</table>", unsafe_allow_html=True)

    tab_names = ["Açıklama", "Konum", "Fotoğraflar", "İlan teklifleri", "Mesaj", "İndir ve paylaş"] + (["Düzenle", "Sil"] if manage else [])
    detail_tabs = dict(zip(tab_names, st.tabs(tab_names)))
    with detail_tabs["Açıklama"]:
        st.write(property_row.get("description") or property_row.get("notes") or "Bu ilan için açıklama girilmemiş.")
        with st.expander("📈 Fiyat geçmişi"):
            price_history_table(client, property_id)
    with detail_tabs["Konum"]:
        st.write(f"{property_row.get('city', '')} / {property_row.get('district', '')} — {property_row.get('address') or 'Açık adres belirtilmemiş.'}")
        latitude, longitude = property_row.get("latitude"), property_row.get("longitude")
        has_coordinates = pd.notna(latitude) and pd.notna(longitude)
        map_query = f"{latitude},{longitude}" if has_coordinates else ", ".join(str(v) for v in (property_row.get("address"), property_row.get("district"), property_row.get("city")) if pd.notna(v))
        if map_query:
            embed_page(f"https://maps.google.com/maps?q={quote(map_query)}&output=embed", 300)
    with detail_tabs["Fotoğraflar"]:
        photos_tab_content(client, property_id, photos, manage)
    with detail_tabs["İlan teklifleri"]:
        offers_panel(client, property_id, user_id, is_admin, staff, str(property_row.get("owner_id")), "detail")
    with detail_tabs["Mesaj"]:
        owner_id = str(property_row.get("owner_id"))
        if owner_id == user_id:
            st.info("Bu ilanın sorumlusu sizsiniz.")
        else:
            compose_message_form(client, user_id, staff, f"detail_{property_id}", default_recipient=owner_id, lock_recipient=True, property_id=property_id, subject_hint=f"İlan: {_clean(property_row.get('title'))}")
    with detail_tabs["İndir ve paylaş"]:
        download_tab_content(property_row, photos)
    if manage:
        with detail_tabs["Düzenle"]:
            edit_tab_content(client, property_row, is_admin, staff, location_data)
        with detail_tabs["Sil"]:
            delete_tab_content(client, property_row)


USER_AGENT = {"User-Agent": "ofis-portfoy-streamlit/1.0"}
TURKEY_CENTER = (39.0, 35.0)


def _norm(text) -> str:
    text = str(text or "").replace("İ", "i").replace("I", "ı").lower()
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.replace("ı", "i")).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def reverse_geocode(lat: float, lng: float) -> dict:
    response = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 18, "accept-language": "tr"},
        headers=USER_AGENT, timeout=10,
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=3600, show_spinner=False)
def search_location(query: str) -> dict | None:
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 1, "countrycodes": "tr", "accept-language": "tr"},
        headers=USER_AGENT, timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return data[0] if data else None


def describe_location(result: dict, provinces: list[str], districts_by_province: dict[str, list[str]]) -> dict:
    addr = result.get("address") or {}
    province = None
    province_lookup = {_norm(p): p for p in provinces}
    for field in ("province", "state", "city", "region", "county"):
        value = addr.get(field)
        if value:
            province = province_lookup.get(_norm(re.sub(r"\s+(ili|province)$", "", str(value), flags=re.I)))
            if province:
                break
    district = None
    if province:
        district_lookup = {_norm(d): d for d in districts_by_province.get(province, [])}
        for field in ("county", "city_district", "district", "town", "municipality", "suburb", "city", "village"):
            value = addr.get(field)
            if value:
                cleaned = re.sub(r"\s+(belediyesi|ilçesi|merkez)$", "", str(value), flags=re.I)
                district = district_lookup.get(_norm(cleaned)) or district_lookup.get(_norm(value))
                if district:
                    break
    neighbourhood = addr.get("neighbourhood") or addr.get("quarter") or addr.get("suburb")
    street = addr.get("road") or addr.get("pedestrian") or addr.get("footway")
    if street and addr.get("house_number"):
        street = f"{street} No: {addr['house_number']}"
    address = ", ".join(part for part in (neighbourhood, street) if part)
    if not address:
        address = ", ".join(part.strip() for part in str(result.get("display_name", "")).split(",")[:3])
    return {"city": province, "district": district, "address": address}


def clear_form_state(prefix: str) -> None:
    for state_key in list(st.session_state.keys()):
        if state_key.startswith(prefix + "_") and not state_key.endswith("_last_click"):
            try:
                del st.session_state[state_key]
            except Exception:
                pass


def property_form(key: str, values: dict | None, location_data: tuple[list[str], dict[str, list[str]]], staff: pd.DataFrame, default_owner: str | None = None, owner_editable: bool = True, extra_tab: tuple | None = None) -> dict:
    provinces, districts_by_province = location_data
    values = values or {}
    is_new = not values

    def K(name: str) -> str:
        return f"{key}_{name}"

    def raw(name: str, default=None):
        value = values.get(name)
        if value is None:
            return default
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
        return value

    def init(name: str, value) -> None:
        if K(name) not in st.session_state:
            st.session_state[K(name)] = value

    def pick(col, label: str, name: str, options: list[str], default: str, db_name: str | None = None):
        current = str(raw(db_name or name, default))
        opts = list(options)
        if current not in opts:
            opts.append(current)
        init(name, current)
        return col.selectbox(label, opts, key=K(name))

    def number(col, label: str, name: str, step: int = 1, default: int = 0, db_name: str | None = None):
        init(name, int(raw(db_name or name, default) or 0))
        return col.number_input(label, min_value=0, step=step, key=K(name))

    def check(col, label: str, name: str):
        init(name, bool(raw(name, False)))
        return col.checkbox(label, key=K(name))

    tab_names = ["Temel bilgiler", "Konum ve adres", "Özellikler", "Açıklama"] + ([extra_tab[0]] if extra_tab else [])
    tabs = st.tabs(tab_names)
    tab_basic, tab_loc, tab_feat, tab_desc = tabs[:4]

    with tab_basic:
        c1, c2, c3 = st.columns(3)
        init("title", str(raw("title", "")))
        title = c1.text_input("İlan başlığı *", key=K("title"), placeholder="Deniz manzaralı 2+1 daire")
        category = pick(c2, "Emlak tipi", "category", ["Daire", "Rezidans", "Müstakil ev", "Villa", "Yazlık", "İşyeri", "Arsa", "Bina", "Turistik tesis"], "Daire", "property_category")
        deal_type = pick(c3, "İlan tipi", "deal_type", ["Kiralık", "Satılık"], "Kiralık")
        c4, c5, c6 = st.columns(3)
        price = number(c4, "Fiyat (₺)", "price", 1000)
        rooms = pick(c5, "Oda sayısı", "rooms", ["Belirtilmedi", "Stüdyo (1+0)", "1+1", "2+1", "3+1", "4+1", "5+1", "Diğer"], "Belirtilmedi")
        status = pick(c6, "İlan durumu", "status", ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"], "Aktif")
        c7, c8, c9 = st.columns(3)
        gross_area = number(c7, "Brüt m²", "area_m2", 5)
        net_area = number(c8, "Net m²", "net_area_m2", 5)
        building_age = pick(c9, "Bina yaşı", "building_age", ["Belirtilmemiş", "0", "1", "2", "3", "4", "5-10", "11-15", "16-20", "21+"], "Belirtilmemiş")

    with tab_loc:
        st.caption("Haritada ilanın yerine dokunarak işaretleyin; il, ilçe ve adres otomatik dolar.")
        init("lat", float(raw("latitude")) if raw("latitude") is not None else None)
        init("lng", float(raw("longitude")) if raw("longitude") is not None else None)

        def apply_pin(new_lat: float, new_lng: float, geo: dict | None = None) -> bool:
            st.session_state[K("lat")] = float(new_lat)
            st.session_state[K("lng")] = float(new_lng)
            try:
                geo = geo or reverse_geocode(round(float(new_lat), 6), round(float(new_lng), 6))
                info = describe_location(geo, provinces, districts_by_province)
            except Exception:
                return False
            if info["address"]:
                st.session_state[K("address")] = info["address"]
            if info["city"] and provinces:
                st.session_state[K("city")] = info["city"]
                if info["district"]:
                    st.session_state[K(f"district_{info['city']}")] = info["district"]
            return True

        search_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
        query = search_col.text_input("Adres veya yer ara (isteğe bağlı)", key=K("search"), placeholder="Örn. Çankaya, Ankara")
        if button_col.button("Haritada bul", key=K("search_btn"), use_container_width=True):
            if query.strip():
                try:
                    found = search_location(query.strip() + ", Türkiye")
                except Exception:
                    found = None
                if found:
                    apply_pin(float(found["lat"]), float(found["lon"]), geo=found)
                    st.rerun()

        lat, lng = st.session_state.get(K("lat")), st.session_state.get(K("lng"))
        has_pin = lat is not None and lng is not None
        center, zoom = ((lat, lng), 16) if has_pin else (TURKEY_CENTER, 6)
        fmap = folium.Map(location=center, zoom_start=zoom, control_scale=True)
        if has_pin:
            folium.Marker([lat, lng], tooltip="Seçilen konum").add_to(fmap)
        out = st_folium(fmap, height=300, use_container_width=True, key=K("map"), returned_objects=["last_clicked"])
        clicked = (out or {}).get("last_clicked")
        if clicked and clicked != st.session_state.get(K("last_click")):
            st.session_state[K("last_click")] = clicked
            apply_pin(clicked["lat"], clicked["lng"])
            st.rerun()

        c10, c11, c12 = st.columns([1, 1, 2])
        default_city = provinces[0] if provinces else ""
        if provinces:
            province_lookup = {_norm(p): p for p in provinces}
            init("city", province_lookup.get(_norm(raw("city", "")), default_city))
            city = c10.selectbox("İl *", provinces, key=K("city"))
            district_options = districts_by_province.get(city, [])
        else:
            init("city", str(raw("city", "")))
            city = c10.text_input("İl *", key=K("city"))
            district_options = []
        if district_options:
            district_lookup = {_norm(d): d for d in district_options}
            guess = district_lookup.get(_norm(raw("district", ""))) or district_options[0]
            init(f"district_{city}", guess)
            district = c11.selectbox("İlçe *", district_options, key=K(f"district_{city}"))
        else:
            init(f"district_{city}", str(raw("district", "")))
            district = c11.text_input("İlçe *", key=K(f"district_{city}"))
        init("address", str(raw("address", "")))
        address = c12.text_input("Açık adres", key=K("address"))

    with tab_feat:
        f1, f2, f3, f4 = st.columns(4)
        floor = pick(f1, "Bulunduğu kat", "floor", ["Belirtilmemiş", "Giriş", "1", "2", "3", "4", "5", "6-10", "11+"], "Belirtilmemiş")
        total_floors = number(f2, "Binadaki kat sayısı", "total_floors")
        heating = pick(f3, "Isıtma", "heating", ["Belirtilmemiş", "Kombi (Doğalgaz)", "Merkezi", "Yerden ısıtma", "Klima"], "Belirtilmemiş")
        bathrooms = number(f4, "Banyo sayısı", "bathroom_count", 1, default=1)
        h1, h2, h3, h4 = st.columns(4, vertical_alignment="bottom")
        init("complex_name", str(raw("complex_name", "")))
        complex_name = h1.text_input("Site adı", key=K("complex_name"))
        dues = number(h2, "Aylık aidat (₺)", "monthly_dues", 100)
        within_complex = check(h3, "Site içerisinde", "within_complex")
        furnished = check(h4, "Eşyalı", "furnished")

    with tab_desc:
        init("description", str(raw("description", "")))
        description = st.text_area("İlan açıklaması", height=130, key=K("description"))
        owner_id = None
        if owner_editable and not staff.empty:
            owner_ids = staff["id"].astype(str).tolist()
            stored_owner = str(raw("owner_id", default_owner or owner_ids[0]))
            init("owner", stored_owner if stored_owner in owner_ids else owner_ids[0])
            owner_id = st.selectbox("Sorumlu çalışan", owner_ids, format_func=lambda x: staff.loc[staff.id.astype(str) == x, "name"].iloc[0], key=K("owner"))

    if extra_tab:
        with tabs[4]:
            extra_tab[1]()

    payload = {
        "title": title.strip(), "city": city, "district": district, "deal_type": deal_type,
        "price": int(price), "rooms": rooms, "area_m2": int(gross_area), "net_area_m2": int(net_area),
        "property_category": category, "building_age": building_age, "floor": floor,
        "total_floors": int(total_floors), "heating": heating, "bathroom_count": int(bathrooms),
        "within_complex": within_complex, "complex_name": complex_name, "monthly_dues": int(dues),
        "furnished": furnished, "address": address, "description": description, "status": status,
    }
    if has_pin:
        payload["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={lat}%2C{lng}"
        payload["latitude"], payload["longitude"] = float(lat), float(lng)
    if owner_id:
        payload["owner_id"] = owner_id
    return payload


def delete_properties(db_client: Client, ids: list[str]) -> tuple[int, list[str]]:
    deleted, errors = 0, []
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        try:
            result = db_client.table("properties").delete().in_("id", chunk).execute()
        except Exception as exc:
            errors.append(f"İlanlar silinemedi: {exc}")
            continue
        removed = [str(r["id"]) for r in (result.data or [])]
        deleted += len(removed)
    return deleted, errors


def add_property(client: Client, user_id: str, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    holder: dict = {}

    def photo_tab() -> None:
        holder["files"] = st.file_uploader("İlan fotoğrafları", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="np_photos")

    with st.container(border=True):
        payload = property_form("np", {}, location_data, staff, default_owner=user_id, extra_tab=("Fotoğraflar", photo_tab))
        photos = holder.get("files")
        saved = st.button("İlanı kaydet", type="primary", use_container_width=True, key="save_new_property")
    if saved:
        if not payload["title"] or not payload["city"] or not payload["district"]:
            st.error("Başlık, il ve ilçe alanlarını doldurun.")
            return
        payload["created_by"] = user_id
        try:
            result = client.table("properties").insert(payload).select("id").single().execute()
            property_id = str(result.data["id"])
            if photos:
                upload_property_photos(client, property_id, photos)
            st.session_state.selected_property_id = property_id
            clear_form_state("np")
            go_to_section("Daireler")
            st.rerun()
        except Exception as exc:
            st.error("İlan kaydedilemedi.")
            st.caption(str(exc))


def edit_tab_content(client: Client, row: pd.Series, is_admin: bool, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    property_id = str(row.id)
    prefix = f"edit_{property_id}"
    payload = property_form(prefix, row.to_dict(), location_data, staff, owner_editable=is_admin)
    if st.button("Değişiklikleri kaydet", type="primary", use_container_width=True, key=f"{prefix}_save"):
        try:
            client.table("properties").update(payload).eq("id", property_id).execute()
            clear_form_state(prefix)
            st.success("İlan güncellendi.")
            st.rerun()
        except Exception as exc:
            st.error("İlan güncellenemedi.")
            st.caption(str(exc))


def delete_tab_content(client: Client, row: pd.Series) -> None:
    property_id = str(row.id)
    prefix = f"edit_{property_id}"
    st.warning("İlan ve fotoğrafları kalıcı olarak silinir.")
    confirm = st.checkbox("Bu ilanı silmek istediğimi onaylıyorum.", key=f"{prefix}_confirm_delete")
    if st.button("İlanı kalıcı olarak sil", type="primary", disabled=not confirm, key=f"{prefix}_delete"):
        deleted, _ = delete_properties(service_client(client), [property_id])
        if deleted:
            clear_form_state(prefix)
            st.session_state.pop("selected_property_id", None)
            st.rerun()


def photos_tab_content(client: Client, property_id: str, photos: list[dict], manage: bool) -> None:
    more_photos = st.file_uploader("Bu ilana fotoğraf ekle", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key=f"detail_upload_{property_id}")
    if st.button("Fotoğrafları yükle", disabled=not more_photos, key=f"detail_upload_button_{property_id}"):
        count, _ = upload_property_photos(client, property_id, more_photos or [])
        if count:
            thumb_store().pop(property_id, None)
            st.success(f"{count} fotoğraf yüklendi.")
            st.rerun()


def _clean(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def property_fact_rows(row: pd.Series) -> list[tuple[str, str]]:
    def money(name: str) -> str:
        value = row.get(name)
        try:
            return f"{int(value):,}".replace(",", ".") + " ₺" if value not in (None, "") and not pd.isna(value) else ""
        except (TypeError, ValueError):
            return ""
    return [
        ("İlan no", str(row.id)[:8].upper()), ("Başlık", _clean(row.get("title"))), ("İşlem", _clean(row.get("deal_type"))),
        ("Fiyat", money("price")), ("İl", _clean(row.get("city"))), ("İlçe", _clean(row.get("district"))),
        ("Oda sayısı", _clean(row.get("rooms"))), ("Brüt m²", _clean(row.get("area_m2"))), ("Durum", _clean(row.get("status"))),
    ]


def build_excel(facts: list[tuple[str, str]], images: list | None = None, office_name: str = DEFAULT_OFFICE_NAME) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "İlan"
    sheet.cell(row=1, column=1, value=office_name)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _make_export(kind: str, facts: list[tuple[str, str]], photo_urls: list[str], office_name: str = DEFAULT_OFFICE_NAME) -> bytes:
    return build_excel(facts, office_name=office_name)


def download_tab_content(row: pd.Series, photos: list[dict]) -> None:
    property_id = str(row.id)
    facts = property_fact_rows(row)
    office_name = st.session_state.get("office_name", DEFAULT_OFFICE_NAME)
    if st.download_button("📊 Excel Raporu İndir", data=partial(_make_export, "xlsx", facts, [], office_name), file_name=f"ilan_{property_id[:8]}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"dl_xlsx_{property_id}", use_container_width=True):
        pass


def add_request(client: Client, user_id: str, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    provinces, districts_by_province = location_data
    with st.container(border=True):
        st.subheader("Yeni müşteri talebi ekle")
        c1, c2, c3 = st.columns(3)
        customer = c1.text_input("Müşteri adı *")
        phone = c2.text_input("Telefon")
        deal = c3.selectbox("İşlem", ["Kiralık", "Satılık"])
        city = c1.selectbox("İstenen il", ["Farketmez"] + provinces) if provinces else c1.text_input("İstenen il")
        district = c2.selectbox("İstenen ilçe", ["Farketmez"])
        rooms = c3.selectbox("Oda tercihi", ["Farketmez", "1+1", "2+1", "3+1"])
        min_price = c1.number_input("En düşük bütçe (₺)", min_value=0, step=1000)
        max_price = c2.number_input("En yüksek bütçe (₺)", min_value=0, step=1000)
        owner = c3.selectbox("Sorumlu", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0])
        notes = st.text_area("Notlar")
        if st.button("Talebi kaydet", type="primary", use_container_width=True):
            if customer:
                client.table("requests").insert({"customer_name": customer, "phone": phone, "deal_type": deal, "city": city, "district": district, "rooms": rooms, "min_price": int(min_price), "max_price": int(max_price), "status": "Yeni", "notes": notes, "owner_id": owner, "created_by": user_id}).execute()
                st.success("Talep kaydedildi.")
                st.rerun()


def switch_to_tab(name: str, click_tab: bool = True) -> None:
    pass


def go_to_section(name: str) -> None:
    st.session_state["pending_tab"] = name


def admin_panel(client: Client, admin_client: Client, staff: pd.DataFrame, user_id: str) -> None:
    st.subheader("Yönetici Paneli & Çalışanlar")
    st.dataframe(staff[["name", "is_admin"]], hide_index=True, use_container_width=True)


@st.cache_resource
def thumb_store() -> dict:
    return {}


def _make_thumb(content: bytes) -> bytes:
    from PIL import Image, ImageOps
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
    image = ImageOps.fit(image, (160, 120), method=Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=72)
    return buffer.getvalue()


def _fetch_thumb(client: Client, store: dict, property_id: str) -> None:
    try:
        bucket = client.storage.from_(PHOTO_BUCKET)
        objects = bucket.list(property_id, {"limit": 5}) or []
        first = next((o for o in objects if o.get("id")), None)
        data = _make_thumb(bucket.download(f"{property_id}/{first['name']}")) if first else None
        store[property_id] = (time.time() + 900, data)
    except Exception:
        store[property_id] = (time.time() + 60, None)


def ensure_thumbs(client: Client, property_ids: list[str]) -> None:
    store = thumb_store()
    for pid in property_ids[:20]:
        if pid not in store:
            _fetch_thumb(client, store, pid)


def open_property(property_id: str) -> None:
    st.session_state["selected_property_id"] = str(property_id)
    st.session_state["pending_tab"] = "Daireler"


def render_property_rows(client: Client, items: list[dict], scope: str, favorites: set[str] | None = None) -> None:
    ensure_thumbs(client, [str(item["id"]) for item in items])
    store = thumb_store()
    with st.container(height=470, border=False):
        if not items:
            st.info("Gösterilecek ilan yok.")
        for item in items:
            property_id = str(item["id"])
            area = _clean(item.get("area_m2")) or "—"
            line = " · ".join(part for part in [
                _clean(item.get("title")) or "Başlıksız ilan", _clean(item.get("deal_type")),
                f"{_clean(item.get('city'))} / {_clean(item.get('district'))}", _clean(item.get("rooms")),
                f"{area} m²", money_text(item.get("price")) + " ₺", _clean(item.get("status")),
            ] if part)
            thumb = (store.get(property_id) or (0, None))[1]
            with st.container(key=f"listing_row_{scope}_{property_id}"):
                image_col, button_col = st.columns([1, 9], vertical_alignment="center")
                with image_col:
                    st.image(thumb if thumb else str(DEFAULT_PROPERTY_IMAGE), width=64)
                with button_col:
                    st.button(line, key=f"open_{scope}_{property_id}", use_container_width=True, on_click=open_property, args=(property_id,))


def match_properties(request: dict, properties: pd.DataFrame) -> list[tuple[dict, int, str]]:
    return []


def requests_tab(client: Client, requests_df: pd.DataFrame, properties: pd.DataFrame, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]], user_id: str, is_admin: bool, favorites: set[str]) -> None:
    if requests_df.empty:
        st.info("Henüz müşteri talebi yok.")
        return
    st.dataframe(requests_df, hide_index=True, use_container_width=True)


def load_offers(client: Client, property_id: str | None = None) -> list[dict]:
    query = client.table("offers").select("*").order("created_at", desc=True)
    if property_id:
        query = query.eq("property_id", property_id)
    return query.execute().data or []


def offers_panel(client: Client, property_id: str, user_id: str, is_admin: bool, staff: pd.DataFrame, property_owner_id: str, scope: str) -> None:
    st.caption("Teklifler bu alandan yönetilir.")


def offers_tab(client: Client, properties: pd.DataFrame, staff: pd.DataFrame, user_id: str, is_admin: bool) -> None:
    st.info("Teklifler sekmesi.")


def load_favorites(client: Client, user_id: str) -> tuple[set[str], bool]:
    try:
        data = client.table("favorites").select("property_id").eq("user_id", user_id).execute().data or []
        return {str(item["property_id"]) for item in data}, True
    except Exception:
        return set(), False


def set_favorite(client: Client, user_id: str, property_id: str, add: bool) -> None:
    try:
        if add:
            client.table("favorites").upsert({"user_id": user_id, "property_id": property_id}).execute()
        else:
            client.table("favorites").delete().eq("user_id", user_id).eq("property_id", property_id).execute()
    except Exception:
        pass


def favorites_view(client: Client, user_id: str, properties: pd.DataFrame, favorites: set[str]) -> None:
    items = properties[properties.id.astype(str).isin(favorites)].to_dict("records") if not properties.empty else []
    render_property_rows(client, items, "fav", favorites=favorites)


def load_price_history(client: Client, property_id: str | None = None) -> list[dict]:
    return []


def price_history_table(client: Client, property_id: str) -> None:
    st.caption("Fiyat geçmişi bulunmuyor.")


def price_changes_view(client: Client, properties: pd.DataFrame) -> None:
    st.caption("Fiyat değişimi bulunmuyor.")


def load_messages(client: Client, user_id: str) -> list[dict]:
    return []


def messages_tab(client: Client, staff: pd.DataFrame, user_id: str, messages: list[dict], available: bool, properties: pd.DataFrame) -> None:
    st.info("Mesajlaşma alanı.")


def load_reminders(client: Client, user_id: str) -> list[dict]:
    return []


def reminder_alerts(reminders: list[dict]) -> tuple[list[dict], list[dict]]:
    return [], []


def agenda_tab(client: Client, user_id: str, properties: pd.DataFrame, reminders: list[dict], available: bool) -> None:
    st.info("Ajanda alanı.")


def money_text(value) -> str:
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


HERO_SECONDS = 7


def hero_banner(client: Client, properties: pd.DataFrame) -> None:
    if properties.empty or "status" not in properties:
        return
    candidates = properties[properties.status == "Aktif"].head(5).to_dict("records")
    if not candidates:
        return
    item = candidates[0]
    badge = "SATILIK DAİRE"
    price = int(float(item.get("price") or 0))
    st.markdown(
        f"<div class='hero'><div class='hero-shade'></div><div class='hero-body'>"
        f"<span class='hero-badge'>{badge}</span><div class='hero-title'>{escape(_clean(item.get('title')) or 'İlan')}</div>"
        f"<div class='hero-meta'><span class='hero-price'>{money_text(price)} ₺</span>"
        f"📍 {escape(_clean(item.get('district')))}, {escape(_clean(item.get('city')))}</div></div></div>",
        unsafe_allow_html=True,
    )


def select_employee(employee_id: str) -> None:
    st.session_state["summary_employee"] = str(employee_id)


def employee_summary_tab(client: Client, staff: pd.DataFrame, properties: pd.DataFrame, requests_df: pd.DataFrame, is_admin: bool, user_id: str) -> None:
    st.dataframe(staff, hide_index=True, use_container_width=True)


def advanced_search_tab(client: Client, properties: pd.DataFrame, user_id: str, favorites: set[str]) -> None:
    render_property_rows(client, properties.to_dict("records") if not properties.empty else [], "adv", favorites=favorites)


def main() -> None:
    try:
        client = get_client()
    except Exception:
        st.title("🏠 Ofis Portföyü")
        st.error("Supabase bağlantısı ayarlı değil.")
        st.stop()
    if "user" not in st.session_state:
        login(client)
        st.stop()

    try:
        client.auth.set_session(
            st.session_state.supabase_access_token,
            st.session_state.supabase_refresh_token,
        )
    except Exception:
        st.session_state.pop("user", None)
        login(client)
        st.stop()

    user = st.session_state.user
    staff = pd.DataFrame(rows(client, "profiles"))
    current_id = str(user.id)
    me = staff[staff.id.astype(str) == current_id].iloc[0]
    is_admin = bool(me.get("is_admin", False))
    properties = pd.DataFrame(rows(client, "properties"))
    requests = pd.DataFrame(rows(client, "requests"))
    location_data = get_location_data() if "get_location_data" in globals() else ([], {})
    staff_names = dict(zip(staff.id, staff.name))
    settings = get_settings(client)
    office_name = (settings.get("office_name") or DEFAULT_OFFICE_NAME).strip()
    home_title = (settings.get("home_title") or DEFAULT_HOME_TITLE).strip()
    favorites, favorites_available = load_favorites(client, current_id)
    messages_data, messages_available = [], False
    reminders_data, reminders_available = [], False
    
    if not properties.empty:
        properties["sorumlu"] = properties.owner_id.map(staff_names).fillna("Atanmamış")

    section_names = ["Daireler", "Detaylı arama", "Müşteri talepleri", "Teklifler", "Ajanda", "Mesajlar", "Yeni kayıt", "Çalışan özeti"]
    if is_admin:
        section_names.append("Yönetici")

    # --- MODERN ÜST NAVBAR (Şablondan İlham Alınarak Eklendi) ---
    st.markdown(f"""
        <div class="nav-container">
            <div class="logo-text">🏡 {office_name.upper()}</div>
            <div style="font-size: 0.9rem; color: #64748b; font-weight: 500;">
                Oturum Açan: <b>{user.email}</b>
            </div>
        </div>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("### Bölümler")
        for name in section_names:
            st.button(name, key=f"nav_{name}", use_container_width=True, on_click=go_to_section, args=(name,))
        if st.button("Çıkış yap"):
            client.auth.sign_out()
            st.session_state.pop("user", None)
            st.rerun()

    applied_tab = st.session_state.pop("pending_tab", None)
    if applied_tab not in section_names:
        applied_tab = "Daireler"

    tabs = st.tabs(section_names)
    tab_by_name = dict(zip(section_names, tabs))

    with tab_by_name["Daireler"]:
        selected_property_id = st.session_state.get("selected_property_id")
        detail_row = properties[properties.id.astype(str) == str(selected_property_id)] if selected_property_id and not properties.empty else pd.DataFrame()
        if not detail_row.empty:
            show_property_detail(client, detail_row.iloc[0], current_id, is_admin, staff, location_data, favorites)
        elif properties.empty:
            st.info("Henüz daire kaydı yok.")
        else:
            hero_banner(client, properties)
            st.write("")
            render_property_rows(client, properties.to_dict("records"), "list", favorites=favorites)

    with tab_by_name["Detaylı arama"]:
        advanced_search_tab(client, properties, current_id, favorites)
    with tab_by_name["Müşteri talepleri"]:
        requests_tab(client, requests, properties, staff, location_data, current_id, is_admin, favorites)
    with tab_by_name["Teklifler"]:
        offers_tab(client, properties, staff, current_id, is_admin)
    with tab_by_name["Ajanda"]:
        agenda_tab(client, current_id, properties, reminders_data, reminders_available)
    with tab_by_name["Mesajlar"]:
        messages_tab(client, staff, current_id, messages_data, messages_available, properties)
    with tab_by_name["Yeni kayıt"]:
        form1, form2 = st.tabs(["Daire ekle", "Talep ekle"])
        with form1:
            add_property(client, user.id, staff, location_data)
        with form2:
            add_request(client, user.id, staff, location_data)
    with tab_by_name["Çalışan özeti"]:
        employee_summary_tab(client, staff, properties, requests, is_admin, current_id)
    if is_admin:
        with tab_by_name["Yönetici"]:
            admin_panel(client, get_admin_client(), staff, current_id)


main()
'''
with open("app.py", "w", encoding="utf-8") as f:
    f.write(code)
print("app.py saved successfully.")