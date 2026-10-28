from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="Ofis Portföyü", page_icon="🏠", layout="wide")
PHOTO_BUCKET = "property-photos"
MAX_PHOTO_BYTES = 10 * 1024 * 1024


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
            selected_photo = st.selectbox("Fotoğraf", list(range(len(photos))), format_func=lambda i: f"{i + 1} / {len(photos)} — {photos[i]['name']}", label_visibility="collapsed", key=f"detail_photo_{property_id}")
            st.image(photos[selected_photo]["url"], width="stretch")
            thumb_cols = st.columns(min(len(photos), 5))
            for i, photo in enumerate(photos[:5]):
                thumb_cols[i].image(photo["url"], caption=f"{i + 1}", width="stretch")
        else:
            st.info("Bu ilana fotoğraf eklenmemiş.")
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
            for label, value in detail_rows:
                if value not in (None, ""):
                    a, b = st.columns([1, 1.3])
                    a.caption(label)
                    b.write(value)
    details_tab, location_tab = st.tabs(["İlan detayları", "Konum ve bölge bilgisi"])
    with details_tab:
        st.subheader("Açıklama")
        st.write(property_row.get("description") or property_row.get("notes") or "Bu ilan için açıklama girilmemiş.")
    with location_tab:
        st.subheader("Konum ve bölge bilgisi")
        st.write(f"{property_row.get('city', '')} / {property_row.get('district', '')}")
        st.write(property_row.get("address") or "Açık adres belirtilmemiş.")


def add_property(client: Client, user_id: str, staff: pd.DataFrame) -> None:
    with st.form("property_form", clear_on_submit=True):
        st.subheader("Yeni ilan ekle")
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("İlan başlığı *", placeholder="Deniz manzaralı 2+1 daire")
        category = c2.selectbox("Emlak tipi", ["Daire", "Rezidans", "Müstakil ev", "Villa", "Yazlık", "İşyeri", "Arsa", "Bina", "Turistik tesis"])
        deal_type = c3.selectbox("İlan tipi", ["Kiralık", "Satılık"])
        c4, c5, c6 = st.columns(3)
        city = c4.text_input("İl *")
        district = c5.text_input("İlçe / mahalle *")
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
        address = st.text_input("Açık adres / konum tarifi")
        description = st.text_area("İlan açıklaması", height=150)
        owner = st.selectbox("Sorumlu çalışan", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0])
        photos = st.file_uploader("İlan fotoğrafları * (JPG, PNG veya WebP; her biri en fazla 10 MB)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key="new_property_photos")
        saved = st.form_submit_button("Daireyi kaydet", type="primary")
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
            "description": description, "status": status, "owner_id": owner, "created_by": user_id,
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


def add_request(client: Client, user_id: str, staff: pd.DataFrame) -> None:
    with st.form("request_form", clear_on_submit=True):
        st.subheader("Yeni müşteri talebi ekle")
        c1, c2, c3 = st.columns(3)
        customer = c1.text_input("Müşteri adı *")
        phone = c2.text_input("Telefon")
        deal = c3.selectbox("İşlem", ["Kiralık", "Satılık"])
        c4, c5, c6 = st.columns(3)
        city = c4.text_input("İstenen il")
        district = c5.text_input("İstenen ilçe")
        rooms = c6.selectbox("Oda tercihi", ["Farketmez", "1+0", "1+1", "2+1", "3+1", "4+1", "Diğer"])
        c7, c8, c9 = st.columns(3)
        min_price = c7.number_input("En düşük bütçe (₺)", min_value=0, step=1000)
        max_price = c8.number_input("En yüksek bütçe (₺)", min_value=0, step=1000)
        deadline = c9.date_input("Hedef tarih", value=None)
        owner = st.selectbox("Sorumlu çalışan", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0], key="request_owner")
        notes = st.text_area("Müşteri notları")
        saved = st.form_submit_button("Talebi kaydet", type="primary")
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
    staff_names = dict(zip(staff.id, staff.name))
    if not properties.empty:
        properties["sorumlu"] = properties.owner_id.map(staff_names).fillna("Atanmamış")
    if not requests.empty:
        requests["sorumlu"] = requests.owner_id.map(staff_names).fillna("Atanmamış")

    with st.sidebar:
        st.markdown("## 🏠 Ofis Portföyü")
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
    st.title("Ofis genel görünümü")
    st.caption("Daireler ve müşteri talepleri ekip tarafından güncel tutulur.")
    save_notice = st.session_state.pop("property_save_notice", None)
    if save_notice:
        st.success(f"İlan kaydedildi; {save_notice['uploaded']} fotoğraf yüklendi.")
        for error in save_notice["errors"]:
            st.error(error)
    m1, m2, m3, m4 = st.columns(4)
    active = int((properties.status == "Aktif").sum()) if not properties.empty else 0
    new_requests = int((requests.status == "Yeni").sum()) if not requests.empty else 0
    m1.metric("Toplam daire", len(properties))
    m2.metric("Aktif daire", active)
    m3.metric("Toplam talep", len(requests))
    m4.metric("Yeni talep", new_requests)

    tab_names = ["Daireler", "Müşteri talepleri", "Yeni kayıt", "Çalışan özeti"]
    if is_admin:
        tab_names.append("Yönetici")
    tabs = st.tabs(tab_names)
    tab1, tab2, tab3, tab4 = tabs[:4]
    with tab1:
        search = st.text_input("Dairelerde ara", placeholder="İl, ilçe veya başlık")
        c1, c2 = st.columns(2)
        deal_filter = c1.selectbox("İşlem türü", ["Tümü", "Kiralık", "Satılık"])
        status_filter = c2.selectbox("Durum", ["Tümü", "Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        view = properties.copy()
        selected_property_id = st.session_state.get("selected_property_id")
        detail_row = properties[properties.id.astype(str) == str(selected_property_id)] if selected_property_id and not properties.empty else pd.DataFrame()
        if not detail_row.empty:
            show_property_detail(client, detail_row.iloc[0])
        elif not view.empty:
            if search:
                mask = view[["title", "city", "district"]].fillna("").astype(str).apply(lambda col: col.str.contains(search, case=False)).any(axis=1)
                view = view[mask]
            if deal_filter != "Tümü":
                view = view[view.deal_type == deal_filter]
            if status_filter != "Tümü":
                view = view[view.status == status_filter]
            columns = [c for c in ["title", "deal_type", "city", "district", "rooms", "area_m2", "price", "status", "sorumlu"] if c in view]
            st.caption("Detay sayfasını açmak için bir ilan satırına tıklayın.")
            selection = st.dataframe(view.reset_index(drop=True)[columns], hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row")
            selected_rows = selection.selection.rows
            if selected_rows:
                selected = view.reset_index(drop=True).iloc[selected_rows[0]]
                st.session_state.selected_property_id = str(selected.id)
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
            add_property(client, user.id, staff)
        with form2:
            add_request(client, user.id, staff)
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


main()
