from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="Ofis Portföyü", page_icon="🏠", layout="wide")


def get_client() -> Client:
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["anon_key"])


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
            st.rerun()
        except Exception:
            st.error("Giriş yapılamadı. E-posta ve şifrenizi kontrol edin.")


def rows(client: Client, table: str) -> list[dict]:
    return client.table(table).select("*").order("created_at", desc=True).execute().data


def add_property(client: Client, user_id: str, staff: pd.DataFrame) -> None:
    with st.form("property_form", clear_on_submit=True):
        st.subheader("Yeni daire ekle")
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("İlan başlığı *", placeholder="2+1 daire, Çankaya")
        city = c2.text_input("İl *")
        district = c3.text_input("İlçe *")
        c4, c5, c6 = st.columns(3)
        kind = c4.selectbox("İşlem", ["Kiralık", "Satılık"])
        price = c5.number_input("Fiyat (₺)", min_value=0, step=1000)
        rooms = c6.selectbox("Oda sayısı", ["Belirtilmedi", "1+0", "1+1", "2+1", "3+1", "4+1", "Diğer"])
        c7, c8 = st.columns(2)
        area = c7.number_input("Brüt m²", min_value=0, step=5)
        status = c8.selectbox("Durum", ["Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        owner = st.selectbox("Sorumlu çalışan", staff["id"].tolist(), format_func=lambda x: staff.loc[staff.id == x, "name"].iloc[0])
        notes = st.text_area("Notlar")
        saved = st.form_submit_button("Daireyi kaydet", type="primary")
    if saved:
        if not title or not city or not district:
            st.error("Başlık, il ve ilçe alanlarını doldurun.")
            return
        client.table("properties").insert({"title": title, "city": city, "district": district, "deal_type": kind, "price": int(price), "rooms": rooms, "area_m2": int(area), "status": status, "notes": notes, "owner_id": owner, "created_by": user_id}).execute()
        st.success("Daire kaydedildi.")
        st.rerun()


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

    user = st.session_state.user
    staff_rows = rows(client, "profiles")
    staff = pd.DataFrame(staff_rows)
    if staff.empty:
        st.error("Çalışan profili bulunamadı. Kurulum rehberindeki profil adımını tamamlayın.")
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
        me = staff[staff.id == user.id]
        is_admin = bool(me.iloc[0].get("is_admin", False)) if not me.empty else False
        mine = st.toggle("Yalnızca benim kayıtlarım", value=False)
        if st.button("Çıkış yap"):
            client.auth.sign_out()
            st.session_state.pop("user", None)
            st.rerun()

    if mine:
        properties = properties[properties.owner_id == user.id] if not properties.empty else properties
        requests = requests[requests.owner_id == user.id] if not requests.empty else requests
    st.title("Ofis genel görünümü")
    st.caption("Daireler ve müşteri talepleri ekip tarafından güncel tutulur.")
    m1, m2, m3, m4 = st.columns(4)
    active = int((properties.status == "Aktif").sum()) if not properties.empty else 0
    new_requests = int((requests.status == "Yeni").sum()) if not requests.empty else 0
    m1.metric("Toplam daire", len(properties))
    m2.metric("Aktif daire", active)
    m3.metric("Toplam talep", len(requests))
    m4.metric("Yeni talep", new_requests)

    tab1, tab2, tab3, tab4 = st.tabs(["Daireler", "Müşteri talepleri", "Yeni kayıt", "Çalışan özeti"])
    with tab1:
        search = st.text_input("Dairelerde ara", placeholder="İl, ilçe veya başlık")
        c1, c2 = st.columns(2)
        deal_filter = c1.selectbox("İşlem türü", ["Tümü", "Kiralık", "Satılık"])
        status_filter = c2.selectbox("Durum", ["Tümü", "Aktif", "Opsiyonlu", "Kiralandı/Satıldı", "Pasif"])
        view = properties.copy()
        if not view.empty:
            if search:
                mask = view[["title", "city", "district"]].fillna("").astype(str).apply(lambda col: col.str.contains(search, case=False)).any(axis=1)
                view = view[mask]
            if deal_filter != "Tümü":
                view = view[view.deal_type == deal_filter]
            if status_filter != "Tümü":
                view = view[view.status == status_filter]
            columns = [c for c in ["title", "deal_type", "city", "district", "rooms", "area_m2", "price", "status", "sorumlu"] if c in view]
            st.dataframe(view[columns], hide_index=True, use_container_width=True)
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


main()
