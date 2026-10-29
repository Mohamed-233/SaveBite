import html
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import folium
import streamlit as st
from folium.plugins import MarkerCluster


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_places(south, west, north, east):
    """Load mapped places inside the service area."""
    bbox = f"{south},{west},{north},{east}"

    query = f"""
    [out:json][timeout:25];
    (
        nwr["amenity"~"^(restaurant|fast_food)$"]({bbox});
        nwr["office"="charity"]({bbox});
        nwr["amenity"="social_facility"]
           ["social_facility"~"^(food_bank|soup_kitchen)$"]({bbox});
    );
    out center tags;
    """

    request = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": query}).encode("utf-8"),
        headers={
            "User-Agent": "SaveBite-StudentProject/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    with urlopen(request, timeout=40) as response:
        data = json.load(response)

    # A timeout can return an incomplete response.
    if data.get("remark"):
        raise ValueError("Incomplete response")

    elements = data.get("elements")

    if not isinstance(elements, list):
        raise ValueError("Invalid response")

    places = []

    for element in elements:
        tags = element.get("tags", {})
        center = element.get("center", {})

        lat = element.get("lat", center.get("lat"))
        lon = element.get("lon", center.get("lon"))

        if lat is None or lon is None:
            continue

        # Ways and relations may have centers outside the box.
        if not (south <= lat <= north and west <= lon <= east):
            continue

        is_restaurant = tags.get("amenity") in {
            "restaurant",
            "fast_food",
        }

        name = (
            tags.get("name:ar")
            or tags.get("name")
            or tags.get("name:en")
            or "مكان بدون اسم مسجّل"
        )

        address = tags.get("addr:full")

        if not address:
            address = "، ".join(
                tags[key]
                for key in (
                    "addr:housenumber",
                    "addr:street",
                    "addr:suburb",
                    "addr:city",
                )
                if tags.get(key)
            )

        places.append({
            "name": name,
            "lat": lat,
            "lon": lon,
            "kind": "restaurant" if is_restaurant else "charity",
            "address": address or "العنوان غير مسجّل",
            "phone": (
                tags.get("contact:phone")
                or tags.get("phone")
                or "غير مسجّل"
            ),
        })

    return places


def add_places_to_map(city_map, bounds):
    """Render loading controls and add places to the map."""
    (south, west), (north, east) = bounds

    # Keep each service area's results separate.
    area_key = (south, west, north, east)
    state_key = "savebite_places_by_area"

    if state_key not in st.session_state:
        st.session_state[state_key] = {}

    if st.button("📍 تحميل المطاعم والجمعيات"):
        try:
            with st.spinner("جاري تحميل الأماكن من الخريطة..."):
                places = fetch_places(south, west, north, east)

            st.session_state[state_key][area_key] = places

        except HTTPError as error:
            if error.code == 429:
                st.error(
                    "خدمة البحث مشغولة بطلبات كتير. "
                    "استنى شوية وجرّب تاني."
                )
            else:
                st.error(
                    "خدمة البحث غير متاحة حاليًا. "
                    "جرّب تاني بعد شوية."
                )

        except (URLError, TimeoutError, OSError):
            st.error(
                "تعذّر الاتصال بخدمة الأماكن. "
                "راجع الإنترنت وجرّب تاني."
            )

        except (ValueError, KeyError, TypeError):
            st.error(
                "خدمة الأماكن رجّعت بيانات غير مكتملة. "
                "جرّب تاني بعد شوية."
            )

    places = st.session_state[state_key].get(area_key)

    if places is None:
        st.info("اضغط تحميل المطاعم والجمعيات لإظهار الأماكن.")
        return

    if not places:
        st.warning(
            "لم تظهر أماكن مسجّلة بالتصنيفات المطلوبة داخل النطاق. "
            "ده مش معناه إن المنطقة مفيهاش مطاعم أو جمعيات."
        )
        return

    restaurants = folium.FeatureGroup(
        name="🟠 المطاعم",
        show=True,
    ).add_to(city_map)

    charities = folium.FeatureGroup(
        name="🟢 جمعيات وجهات غذاء — غير مؤكدة",
        show=True,
    ).add_to(city_map)

    restaurant_cluster = MarkerCluster().add_to(restaurants)
    charity_cluster = MarkerCluster().add_to(charities)

    restaurant_count = 0
    charity_count = 0

    for place in places:
        is_restaurant = place["kind"] == "restaurant"

        if is_restaurant:
            restaurant_count += 1
            group = restaurant_cluster
            color = "orange"
            icon = "cutlery"
            category = "مطعم"
            notice = "المشاركة في التبرع غير مؤكدة."
        else:
            charity_count += 1
            group = charity_cluster
            color = "green"
            icon = "heart"
            category = "جمعية أو جهة خدمات غذائية"
            notice = "استقبال تبرعات الطعام غير مؤكّد."

        name = html.escape(place["name"])
        address = html.escape(place["address"])
        phone = html.escape(place["phone"])

        popup = f"""
        <div dir="rtl" style="text-align:right;min-width:200px">
            <b>{name}</b>
            <hr>
            <div>النوع: {category}</div>
            <div>العنوان: {address}</div>
            <div>الهاتف: {phone}</div>
            <br>
            <small>{notice}</small>
            <br>
            <small>المصدر: OpenStreetMap</small>
        </div>
        """

        folium.Marker(
            location=[place["lat"], place["lon"]],
            tooltip=name,
            popup=folium.Popup(popup, max_width=320),
            icon=folium.Icon(
                color=color,
                icon=icon,
                prefix="fa",
            ),
        ).add_to(group)

    st.success(
        f"الأماكن المسجّلة: {restaurant_count} مطعم، "
        f"{charity_count} جمعية أو جهة غذاء."
    )

    st.caption(
        "من قائمة الطبقات أسفل يمين الخريطة، "
        "تقدر تظهر أو تخفي كل نوع. "
        "الأرقام على الدوائر بتجمع العلامات القريبة؛ "
        "اضغط عليها للتكبير."
    )