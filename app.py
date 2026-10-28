from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, quote, urlparse
import re
from html import escape
import inspect
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
# Yeni Streamlit sürümlerinde sekmeler session_state ile kodla değiştirilebilir.
TABS_STATE_SUPPORTED = "key" in _TAB_PARAMS and "on_change" in _TAB_PARAMS
MAX_PHOTO_BYTES = 10 * 1024 * 1024
DEFAULT_PROPERTY_IMAGE = Path(__file__).parent / "assets" / "no-property-photo.svg"

st.markdown("""
<style>
@media (max-width: 760px) {
  .block-container { padding: 1rem .8rem 2rem; }
  div[data-testid="stHorizontalBlock"] { flex-direction: column !important; gap: .55rem !important; }
  div[data-testid="stHorizontalBlock"] > div { width: 100% !important; flex: 1 1 100% !important; }
  h1 { font-size: 1.65rem !important; }
  h2 { font-size: 1.35rem !important; }
}
.st-key-home_title button { background: none !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; min-height: 0 !important; color: inherit !important; }
.st-key-home_title button p { font-size: 2.25rem; font-weight: 700; line-height: 1.2; }
.st-key-home_title button:hover p { text-decoration: underline; }
@media (max-width: 760px) { .st-key-home_title button p { font-size: 1.65rem; } }
[class*="st-key-listing_row_"] button { text-align: left; min-height: 76px; white-space: normal; }
.bottom-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.65rem; margin-top:1rem; }
.summary-card { background:#fff; border:1px solid #e4e7ec; border-radius:12px; padding:.8rem 1rem; }
.summary-label { color:#667085; font-size:.82rem; }
.summary-value { color:#101828; font-size:1.4rem; font-weight:700; margin-top:.15rem; }
@media(max-width:760px) { .bottom-summary { grid-template-columns:repeat(2,minmax(0,1fr)); gap:.45rem; } .summary-card { padding:.65rem; } }
</style>
""", unsafe_allow_html=True)


def get_client() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["anon_key"])


def get_admin_client() -> Client:
    key = st.secrets["supabase"].get("service_role_key") or st.secrets["supabase"].get("secret_key")
    if not key:
        raise RuntimeError("Yönetici işlemleri için Supabase service_role/secret key ayarlanmamış.")
    return create_client(st.secrets["supabase"]["url"], key)


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
    """Load the 81 provinces and their districts, cache for one day."""
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
        st.error("Fotoğraflar alınamadı. Supabase Storage bucket ve erişim kurulumunu kontrol edin.")
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


GALLERY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:system-ui,-apple-system,sans-serif;background:transparent}
.main{position:relative;height:330px;background:#101828;border-radius:12px;overflow:hidden;display:flex;align-items:center;justify-content:center}
.main img{max-width:100%;max-height:100%;object-fit:contain;cursor:zoom-in;display:block}
.nav{position:absolute;top:50%;margin-top:-22px;width:44px;height:44px;border:0;border-radius:50%;background:rgba(255,255,255,.88);font-size:28px;line-height:1;color:#101828;cursor:pointer}
.prev{left:8px}.next{right:8px}
.count{position:absolute;bottom:8px;right:10px;background:rgba(0,0,0,.6);color:#fff;font-size:12px;padding:3px 9px;border-radius:12px}
.thumbs{display:flex;gap:6px;margin-top:8px;overflow-x:auto;padding-bottom:4px}
.thumbs img{height:62px;width:84px;object-fit:cover;border-radius:6px;border:2px solid transparent;opacity:.65;flex:none;cursor:pointer}
.thumbs img.active{border-color:#2563eb;opacity:1}
.hint{font-size:12px;color:#667085;margin-top:2px}
</style></head><body>
<div class="main" id="main"><img id="mainImg" alt="İlan fotoğrafı"><button type="button" class="nav prev" id="prev">&#8249;</button><button type="button" class="nav next" id="next">&#8250;</button><div class="count" id="count"></div></div>
<div class="thumbs" id="thumbs"></div>
<div class="hint">Büyütmek için fotoğrafa dokunun · kaydırarak ileri geri gidebilirsiniz</div>
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
  const a = thumbs.children[idx];
  if (a) thumbs.scrollTo({left: a.offsetLeft - thumbs.clientWidth / 2 + a.clientWidth / 2, behavior: "smooth"});
}
function swipe(el, onLeft, onRight) {
  let sx = 0, sy = 0, multi = false;
  el.addEventListener("touchstart", e => { if (e.touches.length > 1) { multi = true; return; } multi = false; sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, {passive: true});
  el.addEventListener("touchend", e => {
    if (multi) return;
    const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.4) { dx < 0 ? onLeft() : onRight(); }
  }, {passive: true});
}
$("prev").onclick = () => show(idx - 1);
$("next").onclick = () => show(idx + 1);
swipe($("main"), () => show(idx + 1), () => show(idx - 1));
mainImg.onclick = openLightbox;

function openLightbox() {
  let doc;
  try { doc = window.parent.document; void doc.body; } catch (e) { window.open(photos[idx], "_blank"); return; }
  const old = doc.getElementById("ofis-lightbox"); if (old) old.remove();
  const previousOverflow = doc.body.style.overflow;
  doc.body.style.overflow = "hidden";
  const ov = doc.createElement("div");
  ov.id = "ofis-lightbox";
  ov.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.96);z-index:2147483000;display:flex;align-items:center;justify-content:center;";
  const img = doc.createElement("img");
  img.style.cssText = "max-width:100%;max-height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none;";
  const btn = (label, css) => { const b = doc.createElement("button"); b.type = "button"; b.textContent = label; b.style.cssText = "position:absolute;border:0;border-radius:50%;width:48px;height:48px;font-size:28px;line-height:1;background:rgba(255,255,255,.9);color:#101828;cursor:pointer;padding:0;" + css; return b; };
  const closeBtn = btn("✕", "top:14px;right:14px;font-size:22px;");
  const prevBtn = btn("‹", "left:10px;top:50%;margin-top:-24px;");
  const nextBtn = btn("›", "right:10px;top:50%;margin-top:-24px;");
  const cnt = doc.createElement("div");
  cnt.style.cssText = "position:absolute;top:22px;left:50%;transform:translateX(-50%);color:#fff;font:14px system-ui,sans-serif;background:rgba(0,0,0,.55);padding:4px 12px;border-radius:12px;";
  let cur = idx;
  const render = () => { img.src = photos[cur]; cnt.textContent = (cur + 1) + " / " + photos.length; };
  const go = d => { cur = (cur + d + photos.length) % photos.length; render(); };
  const onKey = e => { if (e.key === "Escape") close(); else if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1); };
  function close() {
    doc.removeEventListener("keydown", onKey);
    ov.remove();
    doc.body.style.overflow = previousOverflow;
    try { show(cur); } catch (e) {}
  }
  closeBtn.onclick = close;
  prevBtn.onclick = e => { e.stopPropagation(); go(-1); };
  nextBtn.onclick = e => { e.stopPropagation(); go(1); };
  ov.addEventListener("click", e => { if (e.target === ov) close(); });
  doc.addEventListener("keydown", onKey);
  swipe(ov, () => go(1), () => go(-1));
  ov.appendChild(img); ov.appendChild(cnt); ov.appendChild(closeBtn);
  if (photos.length > 1) { ov.appendChild(prevBtn); ov.appendChild(nextBtn); }
  doc.body.appendChild(ov);
  render();
}
show(0);
</script></body></html>"""


def render_photo_gallery(photos: list[dict]) -> None:
    urls = json.dumps([p["url"] for p in photos]).replace("</", "<\\/")
    st.components.v1.html(GALLERY_HTML.replace("__PHOTOS__", urls), height=450)


def show_property_detail(client: Client, property_row: pd.Series) -> None:
    property_id = str(property_row.id)
    if st.button("← İlan listesine dön"):
        st.session_state.pop("selected_property_id", None)
        st.rerun()
    st.markdown(f"## {property_row.title}")
    st.caption(f"{property_row.get('property_category') or 'Daire'} · {property_row.get('city', '')} / {property_row.get('district', '')} · İlan No: {property_id[:8].upper()}")
    left, right = st.columns([1.6, 1], gap="large")
    try:
        photos = get_property_photos(client, property_id)
    except Exception as exc:
        photos = []
        st.warning("Fotoğraf galerisi yüklenemedi. Storage kurulumu kontrol edilmeli.")
        st.caption(str(exc))
    with left:
        if photos:
            render_photo_gallery(photos)
        else:
            st.image(str(DEFAULT_PROPERTY_IMAGE), width="stretch", alt="İlan için varsayılan fotoğraf")
            st.caption("Bu ilana henüz fotoğraf eklenmemiş.")
        more_photos = st.file_uploader("Bu ilana fotoğraf ekle", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key=f"detail_upload_{property_id}")
        if st.button("Fotoğrafları yükle", disabled=not more_photos, key=f"detail_upload_button_{property_id}"):
            count, errors = upload_property_photos(client, property_id, more_photos or [])
            if count:
                st.success(f"{count} fotoğraf yüklendi.")
                st.rerun()
            for error in errors:
                st.error(error)
    with right:
        amount = f"{int(property_row.get('price') or 0):,}".replace(",", ".")
        with st.container(border=True):
            st.markdown(f"# {amount} ₺")
            st.markdown(f"**{property_row.get('deal_type', '')}** · {property_row.get('rooms') or 'Oda bilgisi yok'}")
            st.markdown(f"**{property_row.get('city', '')} / {property_row.get('district', '')}**")
            st.markdown(f"Sorumlu: **{property_row.get('sorumlu', 'Atanmamış')}**")
            st.markdown(f"İlan durumu: **{property_row.get('status', 'Aktif')}**")
        with st.container(border=True):
            detail_rows = [
                ("Emlak tipi", property_row.get("property_category")),
                ("Brüt / net m²", f"{property_row.get('area_m2') or '—'} / {property_row.get('net_area_m2') or '—'}"),
                ("Oda sayısı", property_row.get("rooms")),
                ("Bina yaşı", property_row.get("building_age")),
                ("Bulunduğu kat", property_row.get("floor")),
                ("Kat sayısı", property_row.get("total_floors")),
                ("Isıtma", property_row.get("heating")),
                ("Banyo", property_row.get("bathroom_count")),
                ("Mutfak", property_row.get("kitchen")),
                ("Balkon", "Var" if property_row.get("balcony") else "Yok"),
                ("Asansör", "Var" if property_row.get("elevator") else "Yok"),
                ("Otopark", property_row.get("parking")),
                ("Eşyalı", "Evet" if property_row.get("furnished") else "Hayır"),
                ("Kullanım durumu", property_row.get("usage_status")),
                ("Site içerisinde", "Evet" if property_row.get("within_complex") else "Hayır"),
                ("Site adı", property_row.get("complex_name")),
                ("Aidat", f"{int(property_row.get('monthly_dues') or 0):,}".replace(",", ".") + " ₺"),
                ("Tapu durumu", property_row.get("title_deed_status")),
                ("Krediye uygun", "Evet" if property_row.get("loan_eligible") else "Hayır"),
                ("Takas", "Evet" if property_row.get("swap_allowed") else "Hayır"),
            ]
            detail_rows = [(label, value) for label, value in detail_rows if value not in (None, "")]
            table_rows = "".join(
                f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
                for label, value in detail_rows
            )
            st.markdown(
                "<style>.property-feature-table{width:100%;border-collapse:collapse;font-size:.92rem}"
                ".property-feature-table th,.property-feature-table td{padding:.58rem .7rem;border-bottom:1px solid #eaecf0;text-align:left}"
                ".property-feature-table th{width:45%;color:#475467;font-weight:600;background:#f9fafb}"
                ".property-feature-table tr:last-child th,.property-feature-table tr:last-child td{border-bottom:0}</style>"
                f"<table class='property-feature-table'>{table_rows}</table>", unsafe_allow_html=True
            )
    details_tab, location_tab = st.tabs(["İlan detayları", "Konum ve bölge bilgisi"])
    with details_tab:
        st.subheader("Açıklama")
        st.write(property_row.get("description") or property_row.get("notes") or "Bu ilan için açıklama girilmemiş.")
    with location_tab:
        st.subheader("Konum ve bölge bilgisi")
        st.write(f"{property_row.get('city', '')} / {property_row.get('district', '')}")
        st.write(property_row.get("address") or "Açık adres belirtilmemiş.")
        latitude, longitude = property_row.get("latitude"), property_row.get("longitude")
        has_coordinates = (
            pd.notna(latitude) and pd.notna(longitude)
            and str(latitude).strip() and str(longitude).strip()
        )
        map_query = f"{latitude},{longitude}" if has_coordinates else ", ".join(
            str(value) for value in (property_row.get("address"), property_row.get("district"), property_row.get("city"))
            if pd.notna(value) and str(value).strip()
        )
        if map_query:
            embed_url = f"https://maps.google.com/maps?q={quote(map_query)}&output=embed"
            st.components.v1.iframe(embed_url, height=380, scrolling=False)
        maps_url = property_row.get("maps_url")
        if pd.notna(maps_url) and str(maps_url).strip():
            st.link_button("Google Haritalar'da aç", str(maps_url), use_container_width=True)
        elif has_coordinates:
            map_url = f"https://www.google.com/maps/search/?api=1&query={latitude}%2C{longitude}"
            st.link_button("Google Haritalar'da aç", map_url, use_container_width=True)


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


def property_form(key: str, values: dict | None, location_data: tuple[list[str], dict[str, list[str]]], staff: pd.DataFrame, default_owner: str | None = None, owner_editable: bool = True) -> dict:
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

    c1, c2, c3 = st.columns(3)
    init("title", str(raw("title", "")))
    title = c1.text_input("İlan başlığı *", key=K("title"), placeholder="Deniz manzaralı 2+1 daire")
    category = pick(c2, "Emlak tipi", "category", ["Daire", "Rezidans", "Müstakil ev", "Villa", "Yazlık", "İşyeri", "Arsa", "Bina", "Turistik tesis"], "Daire", "property_category")
    deal_type = pick(c3, "İlan tipi", "deal_type", ["Kiralık", "Satılık"], "Kiralık")

    # ---- Haritadan konum seçimi ----
    st.markdown("##### 📍 Konum")
    st.caption("Haritada ilanın bulunduğu yere dokunarak işaretleyin; il, ilçe ve adres otomatik doldurulur. İsterseniz önce aşağıdan adres arayabilirsiniz.")
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
    query = search_col.text_input("Adres veya yer ara (isteğe bağlı)", key=K("search"), placeholder="Örn. Yeni Meram, Konya")
    if button_col.button("Haritada bul", key=K("search_btn"), use_container_width=True):
        if not query.strip():
            st.warning("Aranacak adresi yazın.")
        else:
            try:
                found = search_location(query.strip() + ", Türkiye")
            except Exception:
                found = None
                st.warning("Adres arama servisine şu anda ulaşılamadı. Haritaya dokunarak işaretleyebilirsiniz.")
            if found:
                apply_pin(float(found["lat"]), float(found["lon"]), geo=found)
                st.rerun()
            elif found is None:
                st.warning("Adres bulunamadı. Haritada doğrudan dokunarak işaretleyebilirsiniz.")

    lat, lng = st.session_state.get(K("lat")), st.session_state.get(K("lng"))
    has_pin = lat is not None and lng is not None
    if has_pin:
        center, zoom = (lat, lng), 16
    else:
        center, zoom = TURKEY_CENTER, 6
        city_now = st.session_state.get(K("city")) or raw("city") or ("Konya" if "Konya" in provinces else None)
        district_now = st.session_state.get(K(f"district_{city_now}")) or raw("district")
        if city_now:
            try:
                found = search_location(", ".join(part for part in (district_now, city_now, "Türkiye") if part))
                if found:
                    center, zoom = (float(found["lat"]), float(found["lon"])), (12 if district_now else 10)
            except Exception:
                pass
    fmap = folium.Map(location=center, zoom_start=zoom, control_scale=True)
    if has_pin:
        folium.Marker([lat, lng], tooltip="Seçilen konum").add_to(fmap)
    out = st_folium(fmap, height=380, use_container_width=True, key=K("map"), returned_objects=["last_clicked"])
    clicked = (out or {}).get("last_clicked")
    if clicked and clicked != st.session_state.get(K("last_click")):
        st.session_state[K("last_click")] = clicked
        if not apply_pin(clicked["lat"], clicked["lng"]):
            st.session_state[K("geo_failed")] = True
        else:
            st.session_state.pop(K("geo_failed"), None)
        st.rerun()
    if has_pin:
        info_col, clear_col = st.columns([4, 1], vertical_alignment="center")
        info_col.caption(f"📌 İşaretlenen konum: {lat:.5f}, {lng:.5f}. Adres bilgileri otomatik dolduruldu; gerekirse aşağıdan düzeltebilirsiniz.")
        if clear_col.button("Konumu kaldır", key=K("clear_pin"), use_container_width=True):
            st.session_state[K("lat")] = None
            st.session_state[K("lng")] = None
            st.rerun()
        if st.session_state.get(K("geo_failed")):
            st.warning("Bu noktanın adresi otomatik alınamadı; il, ilçe ve adresi elle girin. Koordinat yine de kaydedilecek.")

    c4, c5, c6 = st.columns(3)
    default_city = "Konya" if "Konya" in provinces else (provinces[0] if provinces else "")
    if provinces:
        province_lookup = {_norm(p): p for p in provinces}
        init("city", province_lookup.get(_norm(raw("city", "")), default_city))
        city = c4.selectbox("İl *", provinces, key=K("city"))
        district_options = districts_by_province.get(city, [])
    else:
        init("city", str(raw("city", "")))
        city = c4.text_input("İl *", key=K("city"))
        district_options = []
    if district_options:
        district_lookup = {_norm(d): d for d in district_options}
        guess = district_lookup.get(_norm(raw("district", ""))) or ("Meram" if is_new and city == "Konya" and "Meram" in district_options else district_options[0])
        init(f"district_{city}", guess)
        district = c5.selectbox("İlçe *", district_options, key=K(f"district_{city}"))
    else:
        init(f"district_{city}", str(raw("district", "")))
        district = c5.text_input("İlçe *", key=K(f"district_{city}"))
    price = number(c6, "Fiyat (₺)", "price", 1000)

    c7, c8, c9 = st.columns(3)
    rooms = pick(c7, "Oda sayısı", "rooms", ["Belirtilmedi", "Stüdyo (1+0)", "1+1", "2+1", "3+1", "4+1", "5+1", "Diğer"], "Belirtilmedi")
    gross_area = number(c8, "Brüt m²", "area_m2", 5)
    net_area = number(c9, "Net m²", "net_area_m2", 5)
    c10, c11, c12 = st.columns(3)
    building_age = pick(c10, "Bina yaşı", "building_age", ["Belirtilmemiş", "0", "1", "2", "3", "4", "5-10", "11-15", "16-20", "21+"], "Belirtilmemiş")
    floor = pick(c11, "Bulunduğu kat", "floor", ["Belirtilmemiş", "Bodrum", "Giriş", "Bahçe katı", "Yüksek giriş", "1", "2", "3", "4", "5", "6-10", "11+", "Çatı"], "Belirtilmemiş")
    total_floors = number(c12, "Binadaki kat sayısı", "total_floors")
    c13, c14, c15 = st.columns(3)
    heating = pick(c13, "Isıtma", "heating", ["Belirtilmemiş", "Kombi (Doğalgaz)", "Merkezi", "Yerden ısıtma", "Klima", "Soba", "Diğer"], "Belirtilmemiş")
    bathrooms = number(c14, "Banyo sayısı", "bathroom_count", 1, default=1)
    kitchen = pick(c15, "Mutfak", "kitchen", ["Belirtilmemiş", "Açık (Amerikan)", "Kapalı"], "Belirtilmemiş")
    c16, c17, c18 = st.columns(3)
    balcony = check(c16, "Balkon", "balcony")
    elevator = check(c17, "Asansör", "elevator")
    furnished = check(c18, "Eşyalı", "furnished")
    c19, c20, c21 = st.columns(3)
    parking = pick(c19, "Otopark", "parking", ["Yok", "Açık", "Kapalı", "Açık ve kapalı"], "Yok")
    usage_status = pick(c20, "Kullanım durumu", "usage_status", ["Boş", "Kiracılı", "Mal sahibi oturuyor"], "Boş")
    within_complex = check(c21, "Site içerisinde", "within_complex")
    c22, c23, c24 = st.columns(3)
    init("complex_name", str(raw("complex_name", "")))
    complex_name = c22.text_input("Site adı", key=K("complex_name"))
    dues = number(c23, "Aylık aidat (₺)", "monthly_dues", 100)
    title_deed = pick(c24, "Tapu durumu", "title_deed_status", ["Belirtilmemiş", "Kat mülkiyetli", "Kat irtifaklı", "Arsa tapulu", "Hisseli", "Diğer"], "Belirtilmemiş")
    c25, c26, c27 = st.columns(3)
    loan_eligible = check(c25, "Krediye uygun", "loan_eligible")
    swap_allowed = check(c26, "Takas değerlendirilir", "swap_allowed")
    status = pick(c27, "İlan durumu", "status", ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"], "Aktif")
    init("address", str(raw("address", "")))
    address = st.text_input("Açık adres / konum tarifi", key=K("address"), placeholder="Mahalle, cadde, bina no (haritadan otomatik dolar)")
    init("description", str(raw("description", "")))
    description = st.text_area("İlan açıklaması", height=150, key=K("description"))

    payload = {
        "title": title.strip(), "city": city, "district": district, "deal_type": deal_type,
        "price": int(price), "rooms": rooms, "area_m2": int(gross_area), "net_area_m2": int(net_area),
        "property_category": category, "building_age": building_age, "floor": floor,
        "total_floors": int(total_floors), "heating": heating, "bathroom_count": int(bathrooms),
        "kitchen": kitchen, "balcony": balcony, "elevator": elevator, "parking": parking,
        "furnished": furnished, "usage_status": usage_status, "within_complex": within_complex,
        "complex_name": complex_name, "monthly_dues": int(dues), "title_deed_status": title_deed,
        "loan_eligible": loan_eligible, "swap_allowed": swap_allowed, "address": address,
        "description": description, "status": status,
    }
    if has_pin:
        payload["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={lat}%2C{lng}"
        payload["latitude"], payload["longitude"] = float(lat), float(lng)
    else:
        google_query = ", ".join(part for part in [address, district, city, "Türkiye"] if part)
        payload["maps_url"] = f"https://www.google.com/maps/search/?api=1&query={quote(google_query)}"
        payload["latitude"], payload["longitude"] = None, None

    if owner_editable and not staff.empty:
        owner_ids = staff["id"].astype(str).tolist()
        stored_owner = str(raw("owner_id", default_owner or owner_ids[0]))
        init("owner", stored_owner if stored_owner in owner_ids else owner_ids[0])
        payload["owner_id"] = st.selectbox("Sorumlu çalışan", owner_ids, format_func=lambda x: staff.loc[staff.id.astype(str) == x, "name"].iloc[0], key=K("owner"))
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
        for property_id in removed:
            try:
                bucket = db_client.storage.from_(PHOTO_BUCKET)
                paths = [f"{property_id}/{o['name']}" for o in (bucket.list(property_id, {"limit": 1000}) or []) if o.get("id")]
                if paths:
                    bucket.remove(paths)
            except Exception as exc:
                errors.append(f"{property_id[:8].upper()} numaralı ilanın fotoğrafları silinemedi: {exc}")
    return deleted, errors


def add_property(client: Client, user_id: str, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    with st.container(border=True):
        st.subheader("Yeni ilan ekle")
        payload = property_form("np", {}, location_data, staff, default_owner=user_id)
        photos = st.file_uploader("İlan fotoğrafları * (JPG, PNG veya WebP; her biri en fazla 10 MB)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="np_photos")
        saved = st.button("İlanı ve fotoğrafları kaydet", type="primary", use_container_width=True, key="save_new_property")
    if saved:
        if not payload["title"] or not payload["city"] or not payload["district"]:
            st.error("Başlık, il ve ilçe alanlarını doldurun.")
            return
        if not photos:
            st.error("İlanı kaydetmek için en az bir fotoğraf ekleyin.")
            return
        if len(photos) > 20:
            st.error("Bir ilana en fazla 20 fotoğraf ekleyebilirsiniz.")
            return
        invalid = [f.name for f in photos if f.type not in {"image/jpeg", "image/png", "image/webp"} or len(f.getvalue()) > MAX_PHOTO_BYTES]
        if invalid:
            st.error("Fotoğrafları kontrol edin (JPG, PNG veya WebP; her biri en fazla 10 MB): " + ", ".join(invalid))
            return
        payload["created_by"] = user_id
        try:
            result = client.table("properties").insert(payload).select("id").single().execute()
            property_id = str(result.data["id"])
            count, errors = upload_property_photos(client, property_id, photos)
            st.session_state.property_save_notice = {"uploaded": count, "errors": errors}
            st.session_state.selected_property_id = property_id
            clear_form_state("np")
            go_to_section("Daireler")
            st.rerun()
        except Exception as exc:
            st.error("İlan kaydedilemedi. Yeni ilan alanlarının Supabase migration'ının çalıştırıldığını kontrol edin.")
            st.caption(str(exc))


def edit_property_panel(client: Client, row: pd.Series, user_id: str, is_admin: bool, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    property_id = str(row.id)
    owns = str(row.get("owner_id")) == user_id or str(row.get("created_by")) == user_id
    if not (is_admin or owns):
        st.info("Bu ilanı yalnızca ilanı ekleyen veya sorumlu çalışan ile yöneticiler düzenleyebilir ya da silebilir.")
        return
    prefix = f"edit_{property_id}"
    with st.expander("✏️ İlanı düzenle"):
        payload = property_form(prefix, row.to_dict(), location_data, staff, owner_editable=is_admin)
        if st.button("Değişiklikleri kaydet", type="primary", use_container_width=True, key=f"{prefix}_save"):
            if not payload["title"] or not payload["city"] or not payload["district"]:
                st.error("Başlık, il ve ilçe alanlarını doldurun.")
            else:
                try:
                    client.table("properties").update(payload).eq("id", property_id).execute()
                    clear_form_state(prefix)
                    st.success("İlan güncellendi.")
                    st.rerun()
                except Exception as exc:
                    st.error("İlan güncellenemedi.")
                    st.caption(str(exc))
    with st.expander("🗑️ İlanı sil"):
        st.warning("İlan ve fotoğrafları kalıcı olarak silinir. Bu işlem geri alınamaz.")
        confirm = st.checkbox("Bu ilanı silmek istediğimi onaylıyorum.", key=f"{prefix}_confirm_delete")
        if st.button("İlanı kalıcı olarak sil", type="primary", disabled=not confirm, key=f"{prefix}_delete"):
            deleted, errors = delete_properties(client, [property_id])
            if deleted:
                clear_form_state(prefix)
                st.session_state.pop("selected_property_id", None)
                st.session_state["property_delete_notice"] = "İlan silindi."
                st.rerun()
            elif errors:
                for error in errors:
                    st.error(error)
            else:
                st.error("İlan silinemedi. Supabase'de bu tablo için silme (DELETE) izni tanımlı olmayabilir; yöneticinizle iletişime geçin.")


def add_request(client: Client, user_id: str, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    provinces, districts_by_province = location_data
    with st.container(border=True):
        st.subheader("Yeni müşteri talebi ekle")
        c1, c2, c3 = st.columns(3)
        customer = c1.text_input("Müşteri adı *")
        phone = c2.text_input("Telefon")
        deal = c3.selectbox("İşlem", ["Kiralık", "Satılık"])
        c4, c5, c6 = st.columns(3)
        city = c4.selectbox("İstenen il", ["Farketmez"] + provinces, key="request_city") if provinces else c4.text_input("İstenen il")
        district_options = districts_by_province.get(city, []) if city != "Farketmez" else []
        if city == "Farketmez":
            district = c5.selectbox("İstenen ilçe", ["Farketmez"], key="request_district_any")
        elif district_options:
            district = c5.selectbox("İstenen ilçe", district_options, key=f"request_district_{city}")
        else:
            district = c5.text_input("İstenen ilçe", key=f"request_district_text_{city}")
        rooms = c6.selectbox("Oda tercihi", ["Farketmez", "1+0", "1+1", "2+1", "3+1", "4+1", "Diğer"])
        c7, c8, c9 = st.columns(3)
        min_price = c7.number_input("En düşük bütçe (₺)", min_value=0, step=1000)
        max_price = c8.number_input("En yüksek bütçe (₺)", min_value=0, step=1000)
        deadline = c9.date_input("Hedef tarih", value=None)
        owner = st.selectbox("Sorumlu çalışan", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0], key="request_owner")
        notes = st.text_area("Müşteri notları")
        saved = st.button("Talebi kaydet", type="primary", use_container_width=True, key="save_new_request")
    if saved:
        if not customer:
            st.error("Müşteri adı alanını doldurun.")
            return
        if max_price and min_price > max_price:
            st.error("En düşük bütçe en yüksek bütçeden fazla olamaz.")
            return
        client.table("requests").insert({"customer_name": customer, "phone": phone, "deal_type": deal, "city": city, "district": district, "rooms": rooms, "min_price": int(min_price), "max_price": int(max_price), "target_date": deadline.isoformat() if isinstance(deadline, date) else None, "status": "Yeni", "notes": notes, "owner_id": owner, "created_by": user_id}).execute()
        st.success("Talep kaydedildi.")
        st.rerun()


BACKUP_FORMAT = "ofis-portfoy-backup-v1"
BACKUP_ALLOWED_COLUMNS = {
    "title", "city", "district", "deal_type", "price", "rooms", "area_m2", "net_area_m2",
    "property_category", "building_age", "floor", "total_floors", "heating", "bathroom_count",
    "kitchen", "balcony", "elevator", "parking", "furnished", "usage_status", "within_complex",
    "complex_name", "monthly_dues", "title_deed_status", "loan_eligible", "swap_allowed",
    "address", "description", "maps_url", "latitude", "longitude", "status", "owner_id", "created_by",
}


def property_backup_tools(admin_client: Client) -> None:
    notice = st.session_state.pop("restore_notice", None)
    if notice:
        st.success(notice["message"])
        for error in notice["errors"]:
            st.error(error)

    st.markdown("#### İlanları yedekle")
    try:
        all_properties = rows(admin_client, "properties")
    except Exception as exc:
        st.error("İlanlar okunamadı.")
        st.caption(str(exc))
        all_properties = []
    if all_properties:
        payload = {"format": BACKUP_FORMAT, "properties": all_properties}
        st.download_button(
            f"İlanları yedekle (JSON) — {len(all_properties)} ilan",
            json.dumps(payload, ensure_ascii=False, default=str, indent=2),
            f"ilan_yedegi_{date.today().isoformat()}.json",
            "application/json",
            key="download_property_backup",
        )
        st.caption("Yedek, tüm ilanların kayıt bilgilerini içerir. Fotoğraflar yedeğe dahil değildir.")
    else:
        st.info("Yedeklenecek ilan yok.")

    st.divider()
    st.markdown("#### Yedek dosyasından ilanları geri yükle")
    st.warning("Geri yükleme, dosyadaki ilanları yeni kayıt olarak ekler; mevcut ilanları silmez veya güncellemez. Aynı yedeği iki kez yüklerseniz ilanlar çift olur.")
    backup_file = st.file_uploader("Yedek dosyası (JSON)", type=["json"], key="property_backup_upload")
    if backup_file and st.button("İlan yedeğini yükle", type="primary", key="restore_property_backup"):
        try:
            data = json.load(backup_file)
        except Exception as exc:
            st.error(f"Yedek dosyası okunamadı: {exc}")
            return
        backup_rows = data.get("properties", []) if isinstance(data, dict) else []
        if not backup_rows:
            st.error("Dosyada geri yüklenecek ilan bulunamadı.")
            return
        restored, errors = 0, []
        for number, row in enumerate(backup_rows, start=1):
            try:
                admin_client.table("properties").insert({k: v for k, v in row.items() if k in BACKUP_ALLOWED_COLUMNS}).execute()
                restored += 1
            except Exception as exc:
                errors.append(f"{number}. ilan ({row.get('title') or 'başlıksız'}): {exc}")
        st.session_state.restore_notice = {"message": f"{restored} / {len(backup_rows)} ilan geri yüklendi.", "errors": errors}
        st.rerun()


def property_delete_tools(admin_client: Client, staff: pd.DataFrame) -> None:
    notice = st.session_state.pop("delete_notice", None)
    if notice:
        st.success(notice["message"])
        for error in notice["errors"]:
            st.error(error)
    try:
        all_properties = rows(admin_client, "properties")
    except Exception as exc:
        st.error("İlanlar okunamadı.")
        st.caption(str(exc))
        return
    if not all_properties:
        st.info("Silinecek ilan yok.")
        return
    generation = st.session_state.get("delete_generation", 0)
    names = dict(zip(staff.id.astype(str), staff.name))
    labels = {
        str(p["id"]): f"{p.get('title') or 'Başlıksız ilan'} — {p.get('city', '')}/{p.get('district', '')} — {p.get('status', '')} — {names.get(str(p.get('owner_id')), 'Atanmamış')}"
        for p in all_properties
    }

    def finish(deleted: int, errors: list[str]) -> None:
        st.session_state["delete_generation"] = generation + 1
        st.session_state["delete_notice"] = {"message": f"{deleted} ilan silindi.", "errors": errors}
        st.session_state.pop("selected_property_id", None)
        st.rerun()

    st.markdown("#### Seçili ilanları sil")
    selected = st.multiselect("Silinecek ilanlar", list(labels), format_func=labels.get, key=f"admin_delete_select_{generation}")
    confirm = st.checkbox("Seçili ilanların ve fotoğraflarının kalıcı olarak silineceğini anlıyorum.", key=f"admin_delete_confirm_{generation}")
    if st.button(f"Seçili {len(selected)} ilanı sil", type="primary", disabled=not (selected and confirm), key=f"admin_delete_button_{generation}"):
        finish(*delete_properties(admin_client, selected))

    st.divider()
    st.markdown("#### Tüm ilanları sil")
    st.error(f"Bu işlem {len(all_properties)} ilanın tamamını ve fotoğraflarını kalıcı olarak siler. Önce 'İlan yedeği' sekmesinden yedek almanız önerilir.")
    phrase = st.text_input("Onaylamak için TÜMÜNÜ SİL yazın", key=f"admin_delete_all_phrase_{generation}")
    if st.button("Tüm ilanları sil", type="primary", disabled=_norm(phrase) != _norm("TÜMÜNÜ SİL"), key=f"admin_delete_all_button_{generation}"):
        finish(*delete_properties(admin_client, list(labels)))


def switch_to_tab(name: str, click_tab: bool = True) -> None:
    nonce = st.session_state.get("goto_nonce", 0)
    script = """<script>
// __NONCE__
const wanted = __NAME__;
const clickTab = __CLICK__;
const W = window.parent;
let tries = 0;
function press(el) {
  ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(type => {
    const Ctor = type.startsWith("pointer") && W.PointerEvent ? W.PointerEvent : W.MouseEvent;
    el.dispatchEvent(new Ctor(type, {bubbles: true, cancelable: true, view: W, button: 0}));
  });
}
function collapseSidebar(doc) {
  try {
    if (W.innerWidth < 800) {
      const collapse = doc.querySelector('[data-testid="stSidebarCollapseButton"] button');
      if (collapse) collapse.click();
    }
    W.scrollTo(0, 0);
  } catch (e) {}
}
const timer = setInterval(() => {
  tries += 1;
  let doc;
  try { doc = W.document; } catch (e) { clearInterval(timer); return; }
  if (!clickTab) { collapseSidebar(doc); clearInterval(timer); return; }
  const tab = Array.from(doc.querySelectorAll('[role="tab"]')).find(b => b.textContent.trim() === wanted);
  if (tab) {
    press(tab);
    clearInterval(timer);
    collapseSidebar(doc);
  } else if (tries > 30) { clearInterval(timer); }
}, 100);
</script>""".replace("__NONCE__", str(nonce)).replace("__NAME__", json.dumps(name)).replace("__CLICK__", "true" if click_tab else "false")
    st.components.v1.html(script, height=1)


def go_to_section(name: str) -> None:
    st.session_state["pending_tab"] = name
    st.session_state["goto_nonce"] = st.session_state.get("goto_nonce", 0) + 1
    if name == "Daireler":
        st.session_state.pop("selected_property_id", None)


def admin_panel(client: Client, admin_client: Client, staff: pd.DataFrame, user_id: str) -> None:
    st.subheader("Çalışan hesapları")
    st.caption("Yeni kullanıcı oluşturabilir, çalışan bilgilerini düzenleyebilir veya hesabı geçici olarak devre dışı bırakabilirsiniz.")
    try:
        listed_users = admin_client.auth.admin.list_users()
        auth_users = listed_users if isinstance(listed_users, list) else listed_users.users
        user_emails = {str(u.id): u.email for u in auth_users}
    except Exception as exc:
        st.error("Auth kullanıcı listesi okunamadı. Admin key ayarını kontrol edin.")
        st.code(str(exc))
        return

    roster = staff.copy()
    roster["E-posta"] = roster["id"].astype(str).map(user_emails).fillna("—")
    roster["Durum"] = roster.get("is_active", True).apply(lambda active: "Aktif" if active else "Devre dışı") if isinstance(roster.get("is_active", True), pd.Series) else "Aktif"
    roster["Rol"] = roster["is_admin"].map(lambda admin: "Yönetici" if admin else "Çalışan")
    st.dataframe(roster[["name", "E-posta", "Rol", "Durum"]].rename(columns={"name": "Ad Soyad"}), hide_index=True, use_container_width=True)

    create_tab, edit_tab, backup_tab, delete_tab = st.tabs(["Kullanıcı ekle", "Düzenle / devre dışı bırak", "İlan yedeği", "İlan sil"])
    with backup_tab:
        property_backup_tools(admin_client)
    with delete_tab:
        property_delete_tools(admin_client, staff)
    with create_tab:
        with st.form("create_employee", clear_on_submit=True):
            name = st.text_input("Ad soyad")
            email = st.text_input("E-posta")
            password = st.text_input("İlk şifre", type="password", help="Güçlü ve geçici bir şifre belirleyip çalışana güvenli şekilde iletin.")
            role = st.selectbox("Yetki", ["Çalışan", "Yönetici"])
            create = st.form_submit_button("Kullanıcı oluştur", type="primary")
        if create:
            if not name.strip() or not email.strip() or len(password) < 8:
                st.error("Ad, e-posta girin ve en az 8 karakterli bir ilk şifre belirleyin.")
            else:
                created = None
                try:
                    result = admin_client.auth.admin.create_user({"email": email.strip(), "password": password, "email_confirm": True, "user_metadata": {"name": name.strip(), "display_name": name.strip()}})
                    created = result.user
                    admin_client.table("profiles").insert({"id": created.id, "name": name.strip(), "is_admin": role == "Yönetici", "is_active": True}).execute()
                    st.success(f"{email.strip()} hesabı oluşturuldu.")
                    st.rerun()
                except Exception as exc:
                    if created:
                        try:
                            admin_client.auth.admin.delete_user(str(created.id))
                        except Exception:
                            pass
                    st.error("Kullanıcı oluşturulamadı. E-posta daha önce kullanılmış olabilir veya Admin key yetkisini kontrol edin.")
                    st.code(str(exc))

    with edit_tab:
        if staff.empty:
            st.info("Düzenlenecek kullanıcı yok.")
            return
        ids = staff.id.astype(str).tolist()
        selected_id = st.selectbox("Çalışan", ids, format_func=lambda ident: f"{staff.loc[staff.id.astype(str) == ident, 'name'].iloc[0]} — {user_emails.get(ident, 'e-posta yok')}")
        current = staff[staff.id.astype(str) == selected_id].iloc[0]
        with st.form("edit_employee"):
            updated_name = st.text_input("Ad soyad", value=str(current.name))
            updated_email = st.text_input("E-posta", value=user_emails.get(selected_id, ""))
            updated_admin = st.checkbox("Yönetici yetkisi", value=bool(current.is_admin))
            active_default = bool(current.get("is_active", True))
            updated_active = st.checkbox("Hesap aktif", value=active_default)
            save = st.form_submit_button("Değişiklikleri kaydet", type="primary")
        if save:
            admins = staff[staff.is_admin.astype(bool)]
            active_admins = admins[admins.get("is_active", pd.Series(True, index=admins.index)).astype(bool)]
            if (not updated_admin or not updated_active) and selected_id in active_admins.id.astype(str).tolist() and len(active_admins) <= 1:
                st.error("Son aktif yöneticinin yetkisi kaldırılamaz veya hesabı kapatılamaz.")
            elif not updated_name.strip() or not updated_email.strip():
                st.error("Ad ve e-posta boş bırakılamaz.")
            else:
                try:
                    if selected_id == user_id and not updated_active:
                        st.error("Kendi hesabınızı buradan devre dışı bırakamazsınız.")
                        return
                    admin_client.auth.admin.update_user_by_id(selected_id, {"email": updated_email.strip(), "user_metadata": {"name": updated_name.strip(), "display_name": updated_name.strip()}})
                    admin_client.table("profiles").update({"name": updated_name.strip(), "is_admin": updated_admin, "is_active": updated_active}).eq("id", selected_id).execute()
                    st.success("Çalışan bilgileri güncellendi.")
                    st.rerun()
                except Exception as exc:
                    st.error("Değişiklik kaydedilemedi. Auth veya tablo izinlerini kontrol edin.")
                    st.code(str(exc))

        st.warning("Devre dışı bırakılan çalışan yeni oturum açamaz; mevcut daire ve talepleri korunur. Hesabı daha sonra yeniden etkinleştirebilirsiniz.")


def main() -> None:
    try:
        client = get_client()
    except Exception:
        st.title("🏠 Ofis Portföyü")
        st.error("Supabase bağlantısı ayarlı değil. Kurulum adımları için README.md dosyasına bakın.")
        st.stop()
    if "user" not in st.session_state:
        login(client)
        st.stop()

    # Streamlit reruns the script and creates a fresh Supabase client each time.
    # Restore this browser session's auth tokens so protected profile reads use
    # the authenticated role instead of silently querying as anon.
    try:
        client.auth.set_session(
            st.session_state.supabase_access_token,
            st.session_state.supabase_refresh_token,
        )
        current_session = client.auth.get_session()
        if current_session:
            st.session_state.supabase_access_token = current_session.access_token
            st.session_state.supabase_refresh_token = current_session.refresh_token
    except Exception:
        st.session_state.pop("user", None)
        st.session_state.pop("supabase_access_token", None)
        st.session_state.pop("supabase_refresh_token", None)
        st.warning("Oturum süresi dolmuş. Lütfen yeniden giriş yapın.")
        login(client)
        st.stop()

    user = st.session_state.user
    try:
        staff_rows = rows(client, "profiles")
    except Exception as exc:
        st.error("`profiles` tablosu okunamadı. Supabase API erişim izni veya tablo kurulumu kontrol edilmeli.")
        st.code(str(exc))
        st.stop()
    staff = pd.DataFrame(staff_rows)
    current_id = str(user.id)
    matching_profile = not staff.empty and current_id in staff.get("id", pd.Series(dtype=str)).astype(str).tolist()
    if not matching_profile:
        st.error("Bu Supabase projesindeki `profiles` tablosunda oturum kullanıcınızın UUID’si bulunamadı.")
        st.markdown("### Hızlı eşleştirme kontrolü")
        st.markdown("Aşağıdaki sorguları **Streamlit Secrets’ta tanımlı olan aynı Supabase projesinin** SQL Editor’ında çalıştırın.")
        st.write("Uygulamada giriş yapan kullanıcı UUID’si:")
        st.code(current_id)
        st.write("1. Bu UUID satır döndürüyor mu?")
        st.code(f"select id, email from auth.users where id = '{current_id}';", language="sql")
        st.write("2. Bu UUID için profil var mı?")
        st.code(f"select id, name from public.profiles where id = '{current_id}';", language="sql")
        st.write("3. Profil yoksa yalnızca şu sorguyu çalıştırın:")
        st.code(f"""insert into public.profiles (id, name, is_admin)
values ('{current_id}', 'Adınız Soyadınız', true)
on conflict (id) do update set name = excluded.name;""", language="sql")
        st.caption("2. sorgu satır döndürüyorsa ama uygulama hâlâ bulamıyorsa, uygulamanın Supabase Project URL’si SQL Editor’da açık projenin URL’siyle aynı değildir. `42P07 relation already exists` burada çalıştırılacak sorgu değildir; `create table` içeren tam kurulum dosyasını tekrar çalıştırmayın.")
        st.stop()
    me = staff[staff.id.astype(str) == current_id].iloc[0]
    is_admin = bool(me.get("is_admin", False))
    if not bool(me.get("is_active", True)):
        client.auth.sign_out()
        st.session_state.pop("user", None)
        st.session_state.pop("supabase_access_token", None)
        st.session_state.pop("supabase_refresh_token", None)
        st.error("Hesabınız devre dışı bırakılmış. Ofis yöneticinizle iletişime geçin.")
        st.stop()
    properties = pd.DataFrame(rows(client, "properties"))
    requests = pd.DataFrame(rows(client, "requests"))
    try:
        location_data = get_location_data()
    except Exception:
        location_data = ([], {})
        st.warning("İl/ilçe listesine şu anda ulaşılamadı; adres alanları geçici olarak elle doldurulabilir.")
    staff_names = dict(zip(staff.id, staff.name))
    if not properties.empty:
        properties["sorumlu"] = properties.owner_id.map(staff_names).fillna("Atanmamış")
    if not requests.empty:
        requests["sorumlu"] = requests.owner_id.map(staff_names).fillna("Atanmamış")

    section_names = ["Daireler", "Müşteri talepleri", "Yeni kayıt", "Çalışan özeti"]
    if is_admin:
        section_names.append("Yönetici")

    with st.sidebar:
        st.button("🏠 Ofis Portföyü · Ana sayfa", use_container_width=True, key="sidebar_home",
                  on_click=go_to_section, args=("Daireler",))
        st.markdown("### Bölümler")
        for name in section_names:
            st.button(name, key=f"nav_{name}", use_container_width=True,
                      on_click=go_to_section, args=(name,))
        st.caption("İlan düzenleme Daireler bölümünde; yedekleme ve geri yükleme Yönetici bölümündedir." if is_admin else "İlan düzenleme Daireler bölümündedir.")
        st.caption(user.email)
        mine = st.toggle("Yalnızca benim kayıtlarım", value=False)
        if st.button("Çıkış yap"):
            client.auth.sign_out()
            st.session_state.pop("user", None)
            st.session_state.pop("supabase_access_token", None)
            st.session_state.pop("supabase_refresh_token", None)
            st.rerun()

    if mine:
        properties = properties[properties.owner_id == user.id] if not properties.empty else properties
        requests = requests[requests.owner_id == user.id] if not requests.empty else requests
    if st.button("Ofis genel görünümü", key="home_title", help="Daireler bölümüne git"):
        go_to_section("Daireler")
        st.rerun()
    st.caption("Daireler ve müşteri talepleri ekip tarafından güncel tutulur.")
    save_notice = st.session_state.pop("property_save_notice", None)
    if save_notice:
        st.success(f"İlan kaydedildi; {save_notice['uploaded']} fotoğraf yüklendi.")
        for error in save_notice["errors"]:
            st.error(error)
    delete_notice = st.session_state.pop("property_delete_notice", None)
    if delete_notice:
        st.success(delete_notice)
    active = int((properties.status == "Aktif").sum()) if not properties.empty else 0
    new_requests = int((requests.status == "Yeni").sum()) if not requests.empty else 0

    applied_tab = st.session_state.pop("pending_tab", None)
    if applied_tab not in section_names:
        applied_tab = None
    if applied_tab and TABS_STATE_SUPPORTED:
        st.session_state["main_tabs"] = applied_tab
    if TABS_STATE_SUPPORTED:
        tabs = st.tabs(section_names, key="main_tabs", on_change="rerun")
    else:
        tabs = st.tabs(section_names)
    tab_by_name = dict(zip(section_names, tabs))
    with tab_by_name["Daireler"]:
        search = st.text_input("Dairelerde ara", placeholder="İl, ilçe veya başlık")
        c1, c2 = st.columns(2)
        deal_filter = c1.selectbox("İşlem türü", ["Tümü", "Kiralık", "Satılık"])
        status_filter = c2.selectbox("Durum", ["Tümü", "Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        view = properties.copy()
        selected_property_id = st.session_state.get("selected_property_id")
        detail_row = properties[properties.id.astype(str) == str(selected_property_id)] if selected_property_id and not properties.empty else pd.DataFrame()
        if not detail_row.empty:
            show_property_detail(client, detail_row.iloc[0])
            edit_property_panel(client, detail_row.iloc[0], current_id, is_admin, staff, location_data)
        elif not view.empty:
            if search:
                mask = view[["title", "city", "district"]].fillna("").astype(str).apply(lambda col: col.str.contains(search, case=False)).any(axis=1)
                view = view[mask]
            if deal_filter != "Tümü":
                view = view[view.deal_type == deal_filter]
            if status_filter != "Tümü":
                view = view[view.status == status_filter]
            st.caption("Detayları açmak için ilan satırının herhangi bir yerine dokunun.")
            for item in view.to_dict("records"):
                title = item.get("title") or "Başlıksız ilan"
                price_text = f"{int(item.get('price') or 0):,}".replace(",", ".") + " ₺"
                listing_line = f"🏠 İlan: {title}  |  İşlem: {item.get('deal_type', '')}  |  İl/İlçe: {item.get('city', '')} / {item.get('district', '')}  |  Oda: {item.get('rooms', '')}  |  Brüt: {item.get('area_m2') or '—'} m²  |  Fiyat: {price_text}  |  Durum: {item.get('status', '')}  |  Sorumlu: {item.get('sorumlu', '')}"
                with st.container(key=f"listing_row_{item['id']}"):
                    if st.button(listing_line, key=f"listing_{item['id']}", use_container_width=True):
                        st.session_state.selected_property_id = str(item["id"])
                        st.rerun()
        else:
            st.info("Henüz daire kaydı yok.")
    with tab_by_name["Müşteri talepleri"]:
        view = requests.copy()
        if not view.empty:
            choices = ["Yeni", "İletişimde", "Gösterim", "Tamamlandı", "İptal"]
            labels = view.apply(lambda r: f"{r.customer_name} — {r.city or 'Konum belirtilmedi'}, {r.district or ''} — {r.sorumlu}", axis=1)
            selected_label = st.selectbox("Durumunu güncelle", labels.tolist(), key="request_to_update")
            selected_id = view.iloc[labels.tolist().index(selected_label)].id
            selected_status = st.selectbox("Yeni durum", choices, index=choices.index(view[view.id == selected_id].iloc[0].status) if view[view.id == selected_id].iloc[0].status in choices else 0)
            if st.button("Talep durumunu kaydet"):
                client.table("requests").update({"status": selected_status}).eq("id", selected_id).execute()
                st.rerun()
            columns = [c for c in ["customer_name", "phone", "deal_type", "city", "district", "rooms", "min_price", "max_price", "target_date", "status", "sorumlu"] if c in view]
            st.dataframe(view[columns], hide_index=True, use_container_width=True)
        else:
            st.info("Henüz müşteri talebi yok.")
    with tab_by_name["Yeni kayıt"]:
        form1, form2 = st.tabs(["Daire ekle", "Talep ekle"])
        with form1:
            add_property(client, user.id, staff, location_data)
        with form2:
            add_request(client, user.id, staff, location_data)
    with tab_by_name["Çalışan özeti"]:
        summary = []
        for row in staff.to_dict("records"):
            owned_properties = properties[properties.owner_id == row["id"]] if not properties.empty else pd.DataFrame()
            owned_requests = requests[requests.owner_id == row["id"]] if not requests.empty else pd.DataFrame()
            summary.append({"Çalışan": row["name"], "Daire": len(owned_properties), "Aktif daire": int((owned_properties.status == "Aktif").sum()) if not owned_properties.empty else 0, "Talep": len(owned_requests), "Yeni talep": int((owned_requests.status == "Yeni").sum()) if not owned_requests.empty else 0})
        st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)
        if not is_admin:
            st.caption("Çalışan listesi ofis yöneticisi tarafından Supabase profillerinden yönetilir.")
    if is_admin:
        with tab_by_name["Yönetici"]:
            try:
                admin_panel(client, get_admin_client(), staff, current_id)
            except Exception as exc:
                st.error("Yönetici işlemleri için Supabase service_role/secret key gerekir. Bu anahtarı Streamlit Cloud Secrets içine ekleyin; GitHub'a koymayın.")
                st.code(str(exc))

    st.markdown(f"""
    <div class="bottom-summary">
      <div class="summary-card"><div class="summary-label">Toplam daire</div><div class="summary-value">{len(properties)}</div></div>
      <div class="summary-card"><div class="summary-label">Aktif daire</div><div class="summary-value">{active}</div></div>
      <div class="summary-card"><div class="summary-label">Toplam talep</div><div class="summary-value">{len(requests)}</div></div>
      <div class="summary-card"><div class="summary-label">Yeni talep</div><div class="summary-value">{new_requests}</div></div>
    </div>
    """, unsafe_allow_html=True)

    if applied_tab:
        switch_to_tab(applied_tab, click_tab=not TABS_STATE_SUPPORTED)


main()