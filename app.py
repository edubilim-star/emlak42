from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, quote, urlparse
import re
from html import escape
import json

import pandas as pd
import requests
import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from supabase import Client, create_client


st.set_page_config(page_title="Ofis Portföyü", page_icon="🏠", layout="wide")
PHOTO_BUCKET = "property-photos"
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


def set_gallery_photo(photo_key: str, photo_index: int, photo_count: int) -> None:
    st.session_state[photo_key] = (photo_index + photo_count) % photo_count


@st.dialog("İlan fotoğrafı", width="large")
def show_photo_lightbox(photos: list[dict], photo_key: str) -> None:
    index = st.session_state.get(photo_key, 0) % len(photos)
    left, center, right = st.columns([1, 3, 1])
    left.button("‹ Önceki", disabled=len(photos) < 2, key=f"lightbox_prev_{photo_key}",
                on_click=set_gallery_photo, args=(photo_key, index - 1, len(photos)), use_container_width=True)
    center.markdown(f"<div style='text-align:center;color:#667085;padding:.45rem'>Fotoğraf {index + 1} / {len(photos)}</div>", unsafe_allow_html=True)
    right.button("Sonraki ›", disabled=len(photos) < 2, key=f"lightbox_next_{photo_key}",
                 on_click=set_gallery_photo, args=(photo_key, index + 1, len(photos)), use_container_width=True)
    st.image(photos[index]["url"], width="stretch")


def show_clickable_property_photo(url: str, key: str, photos: list[dict], photo_key: str, photo_index: int) -> None:
    result = streamlit_image_coordinates(url, width="stretch", key=key, cursor="zoom-in")
    if result and result.get("timestamp") is not None:
        click_key = f"{key}_last_click"
        timestamp = result.get("timestamp")
        if st.session_state.get(click_key) != timestamp:
            st.session_state[click_key] = timestamp
            st.session_state[photo_key] = photo_index
            show_photo_lightbox(photos, photo_key)


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
            photo_key = f"detail_photo_{property_id}"
            photo_index = st.session_state.get(photo_key, 0) % len(photos)
            previous, counter, following = st.columns([1, 3, 1])
            if previous.button("‹ Önceki", disabled=len(photos) < 2, key=f"prev_{property_id}"):
                st.session_state[photo_key] = (photo_index - 1) % len(photos)
                st.rerun()
            counter.caption(f"Fotoğraf {photo_index + 1} / {len(photos)} — büyütmek için görsele dokunun")
            if following.button("Sonraki ›", disabled=len(photos) < 2, key=f"next_{property_id}"):
                st.session_state[photo_key] = (photo_index + 1) % len(photos)
                st.rerun()
            show_clickable_property_photo(photos[photo_index]["url"], f"main_photo_{property_id}_{photo_index}", photos, photo_key, photo_index)
            thumb_cols = st.columns(min(len(photos), 5))
            for i, photo in enumerate(photos[:5]):
                with thumb_cols[i]:
                    show_clickable_property_photo(photo["url"], f"thumb_photo_{property_id}_{i}", photos, photo_key, i)
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


def add_property(client: Client, user_id: str, staff: pd.DataFrame, location_data: tuple[list[str], dict[str, list[str]]]) -> None:
    provinces, districts_by_province = location_data
    with st.container(border=True):
        st.subheader("Yeni ilan ekle")
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("İlan başlığı *", placeholder="Deniz manzaralı 2+1 daire")
        category = c2.selectbox("Emlak tipi", ["Daire", "Rezidans", "Müstakil ev", "Villa", "Yazlık", "İşyeri", "Arsa", "Bina", "Turistik tesis"])
        deal_type = c3.selectbox("İlan tipi", ["Kiralık", "Satılık"])
        c4, c5, c6 = st.columns(3)
        city_default = provinces.index("Konya") if "Konya" in provinces else 0
        city = c4.selectbox("İl *", provinces, index=city_default, key="new_property_city") if provinces else c4.text_input("İl *", key="new_property_city_text")
        district_options = districts_by_province.get(city, []) if provinces else []
        district_default = district_options.index("Meram") if city == "Konya" and "Meram" in district_options else 0
        district = c5.selectbox("İlçe *", district_options, index=district_default, key=f"new_property_district_{city}") if district_options else c5.text_input("İlçe *", key=f"new_property_district_text_{city}")
        price = c6.number_input("Fiyat (₺)", min_value=0, step=1000)
        c7, c8, c9 = st.columns(3)
        rooms = c7.selectbox("Oda sayısı", ["Belirtilmedi", "Stüdyo (1+0)", "1+1", "2+1", "3+1", "4+1", "5+1", "Diğer"])
        gross_area = c8.number_input("Brüt m²", min_value=0, step=5)
        net_area = c9.number_input("Net m²", min_value=0, step=5)
        c10, c11, c12 = st.columns(3)
        building_age = c10.selectbox("Bina yaşı", ["Belirtilmemiş", "0", "1", "2", "3", "4", "5-10", "11-15", "16-20", "21+"])
        floor = c11.selectbox("Bulunduğu kat", ["Belirtilmemiş", "Bodrum", "Giriş", "Bahçe katı", "Yüksek giriş", "1", "2", "3", "4", "5", "6-10", "11+", "Çatı"])
        total_floors = c12.number_input("Binadaki kat sayısı", min_value=0, step=1)
        c13, c14, c15 = st.columns(3)
        heating = c13.selectbox("Isıtma", ["Belirtilmemiş", "Kombi (Doğalgaz)", "Merkezi", "Yerden ısıtma", "Klima", "Soba", "Diğer"])
        bathrooms = c14.number_input("Banyo sayısı", min_value=0, step=1, value=1)
        kitchen = c15.selectbox("Mutfak", ["Belirtilmemiş", "Açık (Amerikan)", "Kapalı"])
        c16, c17, c18 = st.columns(3)
        balcony = c16.checkbox("Balkon")
        elevator = c17.checkbox("Asansör")
        furnished = c18.checkbox("Eşyalı")
        c19, c20, c21 = st.columns(3)
        parking = c19.selectbox("Otopark", ["Yok", "Açık", "Kapalı", "Açık ve kapalı"])
        usage_status = c20.selectbox("Kullanım durumu", ["Boş", "Kiracılı", "Mal sahibi oturuyor"])
        within_complex = c21.checkbox("Site içerisinde")
        c22, c23, c24 = st.columns(3)
        complex_name = c22.text_input("Site adı")
        dues = c23.number_input("Aylık aidat (₺)", min_value=0, step=100)
        title_deed = c24.selectbox("Tapu durumu", ["Belirtilmemiş", "Kat mülkiyetli", "Kat irtifaklı", "Arsa tapulu", "Hisseli", "Diğer"])
        c25, c26, c27 = st.columns(3)
        loan_eligible = c25.checkbox("Krediye uygun")
        swap_allowed = c26.checkbox("Takas değerlendirilir")
        status = c27.selectbox("İlan durumu", ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        address = st.text_input("Açık adres / konum tarifi", placeholder="Mahalle, cadde, bina no")
        google_query = ", ".join(part for part in [address, district, city, "Türkiye"] if part)
        google_search_url = f"https://www.google.com/maps/search/?api=1&query={quote(google_query)}"
        st.components.v1.iframe(f"https://maps.google.com/maps?q={quote(google_query)}&output=embed", height=260, scrolling=False)
        maps_url = st.text_input("Haritalar paylaşım bağlantısı (konumu seçtikten sonra yapıştırın)", placeholder="Google Maps → Paylaş → Bağlantıyı kopyala")
        latitude, longitude = parse_google_maps_location(maps_url) if maps_url else (None, None)
        if latitude and longitude:
            st.caption(f"Konum alındı: {latitude}, {longitude}")
            st.components.v1.iframe(f"https://maps.google.com/maps?q={latitude}%2C{longitude}&output=embed", height=220, scrolling=False)
        description = st.text_area("İlan açıklaması", height=150)
        owner = st.selectbox("Sorumlu çalışan", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0])
        photos = st.file_uploader("İlan fotoğrafları * (JPG, PNG veya WebP; her biri en fazla 10 MB)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="new_property_photos")
        saved = st.button("İlanı ve fotoğrafları kaydet", type="primary", use_container_width=True, key="save_new_property")
    if saved:
        if not title or not city or not district:
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
        payload = {
            "title": title, "city": city, "district": district, "deal_type": deal_type,
            "price": int(price), "rooms": rooms, "area_m2": int(gross_area), "net_area_m2": int(net_area),
            "property_category": category, "building_age": building_age, "floor": floor,
            "total_floors": int(total_floors), "heating": heating, "bathroom_count": int(bathrooms),
            "kitchen": kitchen, "balcony": balcony, "elevator": elevator, "parking": parking,
            "furnished": furnished, "usage_status": usage_status, "within_complex": within_complex,
            "complex_name": complex_name, "monthly_dues": int(dues), "title_deed_status": title_deed,
            "loan_eligible": loan_eligible, "swap_allowed": swap_allowed, "address": address,
            "description": description, "maps_url": maps_url or google_search_url,
            "latitude": float(latitude) if latitude else None, "longitude": float(longitude) if longitude else None,
            "status": status, "owner_id": owner, "created_by": user_id,
        }
        try:
            result = client.table("properties").insert(payload).select("id").single().execute()
            property_id = str(result.data["id"])
            count, errors = upload_property_photos(client, property_id, photos)
            st.session_state.property_save_notice = {"uploaded": count, "errors": errors}
            st.session_state.selected_property_id = property_id
            st.rerun()
        except Exception as exc:
            st.error("İlan kaydedilemedi. Yeni ilan alanlarının Supabase migration'ının çalıştırıldığını kontrol edin.")
            st.caption(str(exc))


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

    create_tab, edit_tab = st.tabs(["Kullanıcı ekle", "Düzenle / devre dışı bırak"])
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

    with st.sidebar:
        if st.button("🏠 Ofis Portföyü · Ana sayfa", use_container_width=True):
            st.session_state.pop("selected_property_id", None)
            st.rerun()
        st.markdown("### Bölümler")
        for section in ["Daireler", "Müşteri talepleri", "Yeni kayıt", "Çalışan özeti"]:
            st.caption("• " + section)
        st.caption("Yedekleme ve düzenleme, Daireler bölümünde.")
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
    if st.button("🏠 Ofis Portföyü", key="home_title"):
        st.session_state.pop("selected_property_id", None)
        st.rerun()
    st.title("Ofis genel görünümü")
    st.caption("Daireler ve müşteri talepleri ekip tarafından güncel tutulur.")
    save_notice = st.session_state.pop("property_save_notice", None)
    if save_notice:
        st.success(f"İlan kaydedildi; {save_notice['uploaded']} fotoğraf yüklendi.")
        for error in save_notice["errors"]:
            st.error(error)
    active = int((properties.status == "Aktif").sum()) if not properties.empty else 0
    new_requests = int((requests.status == "Yeni").sum()) if not requests.empty else 0

    tab_names = ["Daireler", "Müşteri talepleri", "Yeni kayıt", "Çalışan özeti"]
    if is_admin:
        tab_names.append("Yönetici")
    tabs = st.tabs(tab_names)
    tab1, tab2, tab3, tab4 = tabs[:4]
    with tab1:
        if not properties.empty:
            backup_payload = {"format": "ofis-portfoy-backup-v1", "properties": properties.drop(columns=["sorumlu"], errors="ignore").to_dict("records")}
            st.download_button("İlanları yedekle (JSON)", json.dumps(backup_payload, ensure_ascii=False, default=str, indent=2), "ilan_yedegi.json", "application/json")
        backup_file = st.file_uploader("Yedek dosyasından ilanları geri yükle", type=["json"], key="property_backup_upload")
        if backup_file and st.button("İlan yedeğini yükle", key="restore_property_backup"):
            try:
                data = json.load(backup_file)
                backup_rows = data.get("properties", [])
                allowed = {"title", "city", "district", "deal_type", "price", "rooms", "area_m2", "net_area_m2", "property_category", "building_age", "floor", "total_floors", "heating", "bathroom_count", "kitchen", "balcony", "elevator", "parking", "furnished", "usage_status", "within_complex", "complex_name", "monthly_dues", "title_deed_status", "loan_eligible", "swap_allowed", "address", "description", "maps_url", "latitude", "longitude", "status", "owner_id", "created_by"}
                for row in backup_rows:
                    client.table("properties").insert({k: v for k, v in row.items() if k in allowed}).execute()
                st.success(f"{len(backup_rows)} ilan geri yüklendi.")
                st.rerun()
            except Exception as exc:
                st.error(f"Yedek yüklenemedi: {exc}")
        search = st.text_input("Dairelerde ara", placeholder="İl, ilçe veya başlık")
        c1, c2 = st.columns(2)
        deal_filter = c1.selectbox("İşlem türü", ["Tümü", "Kiralık", "Satılık"])
        status_filter = c2.selectbox("Durum", ["Tümü", "Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        view = properties.copy()
        selected_property_id = st.session_state.get("selected_property_id")
        detail_row = properties[properties.id.astype(str) == str(selected_property_id)] if selected_property_id and not properties.empty else pd.DataFrame()
        if not detail_row.empty:
            show_property_detail(client, detail_row.iloc[0])
            with st.expander("İlanı düzenle"):
                editable = detail_row.iloc[0]
                edit_title = st.text_input("İlan başlığı", str(editable.get("title") or ""), key="edit_title")
                edit_city = st.text_input("İl", str(editable.get("city") or ""), key="edit_city")
                edit_district = st.text_input("İlçe", str(editable.get("district") or ""), key="edit_district")
                edit_price = st.number_input("Fiyat (₺)", min_value=0, value=int(editable.get("price") or 0), key="edit_price")
                edit_status = st.selectbox("Durum", ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"], index=["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"].index(editable.get("status")) if editable.get("status") in ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"] else 0, key="edit_status")
                edit_address = st.text_input("Adres", str(editable.get("address") or ""), key="edit_address")
                st.components.v1.iframe(f"https://maps.google.com/maps?q={quote(', '.join([edit_address, edit_district, edit_city]))}&output=embed", height=240, scrolling=False)
                if st.button("Değişiklikleri kaydet", type="primary"):
                    client.table("properties").update({"title": edit_title, "city": edit_city, "district": edit_district, "price": edit_price, "status": edit_status, "address": edit_address}).eq("id", str(editable.id)).execute()
                    st.success("İlan güncellendi.")
                    st.rerun()
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
    with tab2:
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
    with tab3:
        form1, form2 = st.tabs(["Daire ekle", "Talep ekle"])
        with form1:
            add_property(client, user.id, staff, location_data)
        with form2:
            add_request(client, user.id, staff, location_data)
    with tab4:
        summary = []
        for row in staff.to_dict("records"):
            owned_properties = properties[properties.owner_id == row["id"]] if not properties.empty else pd.DataFrame()
            owned_requests = requests[requests.owner_id == row["id"]] if not requests.empty else pd.DataFrame()
            summary.append({"Çalışan": row["name"], "Daire": len(owned_properties), "Aktif daire": int((owned_properties.status == "Aktif").sum()) if not owned_properties.empty else 0, "Talep": len(owned_requests), "Yeni talep": int((owned_requests.status == "Yeni").sum()) if not owned_requests.empty else 0})
        st.dataframe(pd.DataFrame(summary), hide_index=True, use_container_width=True)
        if not is_admin:
            st.caption("Çalışan listesi ofis yöneticisi tarafından Supabase profillerinden yönetilir.")
    if is_admin:
        with tabs[4]:
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


main()
