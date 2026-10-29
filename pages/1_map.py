import html
import json
import math
import re
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import folium
from ui_i18n import st
from branca.element import Element, Template
from folium.plugins import Fullscreen, Geocoder, MarkerCluster
from streamlit_folium import st_folium
from i18n import apply_text_direction, get_language, ensure_language
from ui_i18n import msg
from locations import CARE_PLACES, EXTRA_RESTAURANTS, CITY_RING_SNAPSHOT, INITIAL_OSM_PLACES, canonical_pickup, match_restaurant_at_point

def query_osm(query, timeout=55):
    endpoints = ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter']
    last_error = None
    for endpoint in endpoints:
        request = Request(endpoint, data=urlencode({'data': query}).encode('utf-8'), headers={'User-Agent': 'SaveBite-StudentProject/1.0'})
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.load(response)
            if not isinstance(data, dict) or data.get('remark'):
                raise ValueError('Incomplete map response')
            if not isinstance(data.get('elements'), list):
                raise ValueError('Invalid map response')
            return data
        except HTTPError as error:
            if error.code not in {500, 502, 503, 504}:
                raise
            last_error = error
        except (URLError, OSError, ValueError) as error:
            last_error = error
    raise last_error

@st.cache_data(ttl=86400, show_spinner=False)
def load_city_ring():
    return [point[:] for point in CITY_RING_SNAPSHOT]

def inside_city(lat, lon):
    inside = False
    for (ay, ax), (by, bx) in zip(CITY_RING, CITY_RING[1:]):
        cross = (lon - ax) * (by - ay) - (lat - ay) * (bx - ax)
        if abs(cross) < 1e-10 and min(ax, bx) - 1e-10 <= lon <= max(ax, bx) + 1e-10 and (min(ay, by) - 1e-10 <= lat <= max(ay, by) + 1e-10):
            return True
        if (ay > lat) != (by > lat):
            if lon < (bx - ax) * (lat - ay) / (by - ay) + ax:
                inside = not inside
    return inside

@st.cache_data(ttl=3600, show_spinner=False)
def load_places(bounds):
    (south, west), (north, east) = bounds
    bbox = f'{south},{west},{north},{east}'
    query = f'\n    [out:json][timeout:45];\n    (\n        nwr["amenity"~"^(restaurant|fast_food|food_court)$"]({bbox});\n        nwr["office"~"^(charity|ngo)$"]({bbox});\n        nwr["amenity"="social_facility"]({bbox});\n    );\n    out center tags;\n    '
    data = query_osm(query)
    if data.get('remark') or not isinstance(data.get('elements'), list):
        raise ValueError('Incomplete map response')
    result = []
    for item in data['elements']:
        tags = item.get('tags', {})
        center = item.get('center', {})
        lat = item.get('lat', center.get('lat'))
        lon = item.get('lon', center.get('lon'))
        if lat is None or lon is None or (not inside_city(lat, lon)):
            continue
        restaurant = tags.get('amenity') in {'restaurant', 'fast_food', 'food_court'}
        social = tags.get('office') in {'charity', 'ngo'} or tags.get('amenity') == 'social_facility'
        if not restaurant and (not social):
            continue
        address = tags.get('addr:full') or '، '.join((tags[k] for k in ('addr:housenumber', 'addr:street', 'addr:suburb') if tags.get(k)))
        result.append({'lat': lat, 'lon': lon, 'osm_id': f"{item['type']}/{item['id']}", 'source': 'OpenStreetMap', 'kind': 'restaurant' if restaurant else 'charity', 'name': tags.get('name:ar') or tags.get('name') or tags.get('name:en') or 'مكان بدون اسم مسجّل', 'address': address or 'العنوان غير مسجّل', 'phone': tags.get('contact:phone') or tags.get('phone') or 'غير مسجّل'})
    return result

def valid_places(value):
    if not isinstance(value, list):
        raise ValueError('Invalid places')
    result = []
    for original in value:
        if not isinstance(original, dict):
            raise ValueError('Invalid place')
        item = original.copy()
        lat, lon = (float(item['lat']), float(item['lon']))
        if not math.isfinite(lat) or not math.isfinite(lon):
            raise ValueError('Invalid coordinates')
        if item.get('kind') not in {'restaurant', 'charity'}:
            continue
        if not inside_city(lat, lon):
            continue
        item['lat'], item['lon'] = (lat, lon)
        for key, default in [('name', 'مكان بدون اسم مسجّل'), ('address', 'العنوان غير مسجّل'), ('phone', 'غير مسجّل')]:
            item[key] = str(item.get(key) or default)
        result.append(item)
    return result

def merge_places(*sources):
    result = []
    by_osm = {}
    by_name = {}
    unknown_names = {'مكان بدون اسم مسجّل', 'اسم غير مسجّل', ''}

    def normal_name(value):
        value = re.sub('[أإآ]', 'ا', value.casefold())
        return re.sub('[^\\w]', '', value)
    for source in sources:
        for item in valid_places(source):
            osm_id = item.get('osm_id')
            source_key = (item['kind'], osm_id) if osm_id else None
            if source_key and source_key in by_osm:
                continue
            name_key = (item['kind'], normal_name(item['name']))
            candidates = by_name.get(name_key, []) if item['name'] not in unknown_names else []
            if any((abs(old['lat'] - item['lat']) < 0.0005 and abs(old['lon'] - item['lon']) < 0.0005 for old in candidates)):
                continue
            result.append(item.copy())
            if source_key:
                by_osm[source_key] = item
            if item['name'] not in unknown_names:
                by_name.setdefault(name_key, []).append(item)
    return result

def snapshot_path():
    return Path(__file__).resolve().parent.parent / 'data' / 'savebite_map_cache_v6.json'

def read_snapshot():
    try:
        raw = json.loads(snapshot_path().read_text(encoding='utf-8'))
        if raw.get('area') != 'nasr-city-osm-1249382787':
            raise ValueError('Different area')
        return (valid_places(raw['places']), str(raw['updated']))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return (INITIAL_OSM_PLACES, '2026-10-29')

def write_snapshot(places, updated):
    target = snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, prefix='map-cache-', suffix='.tmp', delete=False) as file:
            name = file.name
            json.dump({'area': 'nasr-city-osm-1249382787', 'updated': updated, 'places': places}, file, ensure_ascii=False)
        Path(name).replace(target)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)

class MapTools(Geocoder):
    _template = Template('\n    {% macro script(this, kwargs) %}\n    (function () {\n        const map = {{ this._parent.get_name() }};\n        const ring = {{ this.ring | tojson }};\n        const bounds = L.latLngBounds({{ this.bounds | tojson }});\n        const ui = {{ this.ui | tojson }};\n        let marker = null;\n        let message;\n        function contains(point) {\n            const y = point.lat, x = point.lng;\n            let inside = false;\n            for (let i = 0; i < ring.length - 1; i++) {\n                const [ay, ax] = ring[i], [by, bx] = ring[i + 1];\n                const cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax);\n                if (Math.abs(cross) < 1e-10 &&\n                    x >= Math.min(ax,bx)-1e-10 && x <= Math.max(ax,bx)+1e-10 &&\n                    y >= Math.min(ay,by)-1e-10 && y <= Math.max(ay,by)+1e-10) return true;\n                if ((ay > y) !== (by > y) && x < (bx-ax)*(y-ay)/(by-ay)+ax) inside = !inside;\n            }\n            return inside;\n        }\n        const status = L.control({position: \'bottomleft\'});\n        status.onAdd = function () {\n            message = L.DomUtil.create(\'div\', \'sb-status\');\n            message.setAttribute(\'role\', \'status\');\n            message.setAttribute(\'aria-live\', \'polite\');\n            message.textContent = \'\';\n            message.style.display = \'none\';\n            message.style.direction = ui.direction;\n            message.style.textAlign = ui.direction === \'rtl\' ? \'right\' : \'left\';\n            L.DomEvent.disableClickPropagation(message);\n            L.DomEvent.disableScrollPropagation(message);\n            return message;\n        };\n        status.addTo(map);\n        function select(point, label, pan) {\n            if (!contains(point)) {\n                message.style.display = \'block\';\n                message.textContent = ui.outside;\n                return;\n            }\n            if (marker) map.removeLayer(marker);\n            marker = L.marker(point, {icon: L.divIcon({\n                className: \'sb-selected\',\n                html: \'<div style="width:22px;height:22px;border-radius:50%;background:#374151;border:3px solid white"></div>\',\n                iconSize:[28,28], iconAnchor:[14,14]\n            })}).addTo(map);\n            const text = document.createElement(\'div\');\n            text.textContent = label;\n            marker.bindPopup(text).openPopup();\n            message.style.display = \'block\';\n            message.textContent = ui.temporary;\n            if (pan) map.setView(point, 16);\n        }\n        map.on(\'click\', e => select(e.latlng, ui.selected, false));\n        const options = {{ this.options | tojson }};\n        const addressGeocoder = L.Control.Geocoder.nominatim(options.providerOptions);\n        const localPlaces = {{ this.places | tojson }};\n        function normal(value) {\n            return value.toLowerCase().replace(/[أإآ]/g, \'ا\').replace(/[\\u064B-\\u065F]/g, \'\').replace(/\\s+/g, \' \').trim();\n        }\n        options.geocoder = {\n            geocode: function(query, callback, context) {\n                const q = normal(query);\n                const found = q.length < 2 ? [] : localPlaces.filter(p => normal(p.name).includes(q)).slice(0, 12);\n                if (found.length) {\n                    callback.call(context, found.map(p => ({\n                        name: p.name,\n                        center: L.latLng(p.lat, p.lon),\n                        bbox: L.latLng(p.lat, p.lon).toBounds(100)\n                    })));\n                } else {\n                    addressGeocoder.geocode(query, callback, context);\n                }\n            }\n        };\n        L.Control.geocoder(options).on(\'markgeocode\', function(e) {\n            select(e.geocode.center, e.geocode.name, true);\n        }).addTo(map);\n        const home = L.control({position: \'topleft\'});\n        home.onAdd = function () {\n            const box = L.DomUtil.create(\'div\', \'leaflet-bar\');\n            const button = L.DomUtil.create(\'a\', \'\', box);\n            button.href = \'#\'; button.textContent = \'⌂\';\n            button.title = ui.home;\n            button.setAttribute(\'role\', \'button\');\n            button.setAttribute(\'aria-label\', button.title);\n            button.style.cssText = \'font-size:25px;color:#15803d;background:white;\';\n            L.DomEvent.disableClickPropagation(box);\n            L.DomEvent.on(button, \'click\', function(e) {\n                L.DomEvent.stop(e);\n                map.fitBounds(bounds, {padding:[20,20], maxZoom:13});\n            });\n            return box;\n        };\n        home.addTo(map);\n        map.fitBounds(bounds, {padding:[20,20], maxZoom:13});\n    })();\n    {% endmacro %}\n    ')

    def __init__(self, bounds, ring, places):
        (south, west), (north, east) = bounds
        super().__init__(collapsed=False, position='topright', add_marker=False, provider_options={'geocodingQueryParams': {'countrycodes': 'eg', 'bounded': 1, 'viewbox': f'{west},{north},{east},{south}'}}, placeholder=msg('البحث داخل مدينة نصر…'), error_message=msg('لا توجد نتيجة. جرّب اسمًا آخر.'), suggest_min_length=1000)
        self.ui = {'direction': 'rtl' if get_language() == 'ar' else 'ltr', 'instruction': msg('ابحث أو اختار موقع داخل الحدود الخضراء.'), 'outside': msg('الموقع خارج نطاق مدينة نصر المرسوم.'), 'temporary': msg('موقع مؤقت؛ لم يتم حفظه مع التبرع.'), 'selected': msg('موقع مختار مؤقتًا'), 'home': msg('الرجوع لمدينة نصر')}
        self.bounds = bounds
        self.ring = ring
        self.places = [{'name': p['name'], 'lat': p['lat'], 'lon': p['lon']} for p in places]
from auth_ui import require_login
require_login()
st.set_page_config(page_title='SaveBite | Nasr City', page_icon='🗺️', layout='wide', initial_sidebar_state='auto')
ensure_language('map')
from product_ui import apply_brand
apply_brand()
st.markdown('\n<style>\n.block-container {padding-top:4.1rem; padding-bottom:2rem;}\niframe[title="streamlit_folium.st_folium"] {\n    border-radius:16px; border:1px solid #dce7e1;\n    box-shadow:0 8px 28px rgba(15,54,34,.09);\n}\n</style>\n', unsafe_allow_html=True)
from workflow_ui import sync_activity_state
sync_activity_state()
st.title('الخريطة')
from routing_ui import render_route_selector
with st.expander('مسارات التوصيل', expanded=bool(st.session_state.get('pending_route_allocation') or st.session_state.get('route_allocation_choice'))):
    showing_route = render_route_selector()
if showing_route:
    if st.button('استكشاف الأماكن', key='route_back_to_places'):
        for route_key in ('route_allocation_choice', 'pending_route_allocation', '_ui_value_route_allocation_choice', '_ui_locale_route_allocation_choice'):
            st.session_state.pop(route_key, None)
        st.rerun()
    st.stop()
try:
    with st.spinner('جاري تحميل نطاق مدينة نصر...'):
        CITY_RING = load_city_ring()
except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
    st.error('تعذّر تحميل نطاق مدينة نصر. راجع الإنترنت، ثم جرّب إعادة التحميل.')
    st.button('إعادة المحاولة')
    st.stop()
lats, lons = zip(*CITY_RING)
BOUNDS = [[min(lats), min(lons)], [max(lats), max(lons)]]
south, west = BOUNDS[0]
north, east = BOUNDS[1]
DATA_KEY = 'nasr_city_places_v6'
DATE_KEY = 'nasr_city_places_updated_v6'
if DATA_KEY not in st.session_state:
    saved_places, saved_date = read_snapshot()
    st.session_state[DATA_KEY] = saved_places
    st.session_state[DATE_KEY] = saved_date
if 'nasr_city_revision' not in st.session_state:
    st.session_state['nasr_city_revision'] = 0
if st.button('تحديث بيانات الخريطة التلقائية'):
    try:
        load_places.clear()
        with st.spinner('جاري تحديث بيانات مدينة نصر...'):
            new_places = valid_places(load_places(BOUNDS))
        if not new_places:
            st.warning('الخدمة لم ترجع أماكن؛ ما زالت آخر بيانات محفوظة معروضة.')
        else:
            updated = datetime.now(timezone.utc).isoformat(timespec='seconds')
            st.session_state[DATA_KEY] = new_places
            st.session_state[DATE_KEY] = updated
            st.session_state['nasr_city_revision'] += 1
            try:
                write_snapshot(new_places, updated)
            except OSError:
                st.warning('تم التحديث، لكن تعذّر حفظ النسخة المحلية على الجهاز.')
    except (URLError, OSError, ValueError, KeyError, TypeError):
        st.warning('تعذّر التحديث حاليًا؛ آخر بيانات محفوظة ما زالت معروضة.')
places = merge_places(EXTRA_RESTAURANTS, CARE_PLACES, st.session_state[DATA_KEY])
pickup_options = {}
for place in places:
    try:
        pickup = canonical_pickup(place)
    except ValueError:
        continue
    pickup_options.setdefault(pickup['place_id'], pickup)

def capture_pickup_click():
    payload = st.session_state.get(map_key, {}) or {}
    point = payload.get('last_object_clicked')
    pickup = match_restaurant_at_point(places, point, payload.get('last_object_clicked_tooltip'))
    if pickup is not None and pickup['place_id'] in pickup_options:
        st.session_state['map_pickup_choice'] = pickup['place_id']
        st.session_state['map_pickup_feedback'] = 'تم اختيار مطعم الاستلام.'
    elif point is not None:
        st.session_state['map_pickup_choice'] = None
        st.session_state['map_pickup_feedback'] = 'اختار مطعمًا من القائمة؛ العلامة دي مش مطعمًا قابلًا للاختيار أو فيها أكثر من مكان.'
if st.session_state.get('map_pickup_choice') not in pickup_options:
    st.session_state.pop('map_pickup_choice', None)
st.subheader('اختيار المطعم')
choice = st.selectbox('ابحث عن مطعم الاستلام أو اضغط على علامته في الخريطة', options=list(pickup_options), index=None, format_func=lambda place_id: f"{pickup_options[place_id]['name']} — {pickup_options[place_id]['address']}", placeholder='اختار مطعمًا...', key='map_pickup_choice')
if st.session_state.get('map_pickup_feedback'):
    pass
if choice:
    chosen_pickup = pickup_options[choice]
if st.button('إضافة تبرع من المطعم المختار', disabled=choice is None, type='primary'):
    st.session_state['pending_donation_pickup'] = pickup_options[choice].copy()
    st.session_state.pop('donation_saved_summary', None)
    st.switch_page('pages/3_donate.py')
loaded = bool(places)
n_rest = sum((p['kind'] == 'restaurant' for p in places))
n_charity = sum((p['kind'] == 'charity' for p in places))
left, right = st.columns(2)
show_rest = left.checkbox(f'🟠 مطاعم وأماكن طعام ({n_rest})', value=True, key='nasr_show_rest', disabled=not loaded)
show_charity = right.checkbox(f'🟢 جمعيات وجهات رعاية ({n_charity})', value=True, key='nasr_show_charity', disabled=not loaded)
if not loaded:
    st.info('حدّث بيانات الأماكن لتفعيل الفلاتر.')
elif n_charity == 0:
    st.info('البحث لم يرجع جهات اجتماعية مسجّلة داخل النطاق؛ ده مش دليل إن مفيش جمعيات.')
if loaded and (not show_rest) and (not show_charity):
    st.info('كل علامات الأماكن مخفية؛ فعّل فلتر علشان تظهر.')
use_clusters = st.toggle('تجميع العلامات القريبة', value=True, key='nasr_use_clusters')
city_map = folium.Map(location=[(south + north) / 2, (west + east) / 2], zoom_start=13, min_zoom=11, max_zoom=19, tiles=None, control_scale=True, max_bounds=True, min_lat=south, max_lat=north, min_lon=west, max_lon=east, max_bounds_viscosity=1.0)
folium.map.CustomPane('soft_map', z_index=200).add_to(city_map)
folium.TileLayer('OpenStreetMap', name=msg('خريطة فاتحة'), pane='soft_map', bounds=BOUNDS, no_wrap=True, show=True).add_to(city_map)
folium.TileLayer('OpenStreetMap', name=msg('خريطة الشوارع'), bounds=BOUNDS, no_wrap=True, show=False).add_to(city_map)
world = [[-85, -180], [-85, 180], [85, 180], [85, -180]]
folium.Polygon([world, CITY_RING], stroke=False, fill=True, fill_color='#15251f', fill_opacity=0.78, fill_rule='evenodd', interactive=False).add_to(city_map)
folium.PolyLine(CITY_RING, color='#16a34a', weight=3, interactive=False).add_to(city_map)
for kind, visible, color, icon in [('restaurant', show_rest, '#f97316', 'cutlery'), ('charity', show_charity, '#16a34a', 'heart')]:
    if not visible:
        continue
    group = folium.FeatureGroup(name=kind, control=False).add_to(city_map)
    cluster_icon = 'function(cluster) {\n        return L.divIcon({\n            html: \'<div class="sb-cluster" style="background:COLOR">\' + cluster.getChildCount() + \'</div>\',\n            className: \'sb-cluster-wrap\', iconSize: [42, 42], iconAnchor: [21, 21]\n        });\n    }'.replace('COLOR', color)
    target = MarkerCluster(icon_create_function=cluster_icon, disable_clustering_at_zoom=17, show_coverage_on_hover=False).add_to(group) if use_clusters else group
    for place in places:
        if place['kind'] != kind:
            continue
        name, address, phone = [html.escape(place[k]) for k in ('name', 'address', 'phone')]
        source = html.escape(place.get('source', 'OpenStreetMap'))
        location_note = html.escape(msg(place.get('location_note', 'موقع مسجّل في OpenStreetMap')))
        popup_dir = 'rtl' if get_language() == 'ar' else 'ltr'
        popup_align = 'right' if popup_dir == 'rtl' else 'left'
        phone_line = f'<br>{html.escape(msg("الهاتف"))}: {phone}' if phone.strip() else ''
        body = f'<div dir="{popup_dir}" style="text-align:{popup_align};min-width:180px"><b>{name}</b><p>{address}</p>{phone_line}</div>'
        folium.Marker([place['lat'], place['lon']], tooltip=name, popup=folium.Popup(body, max_width=320), icon=folium.DivIcon(html=f'<div class="sb-pin" style="background:{color}"><i class="fa fa-{icon}"></i></div>', class_name='sb-pin-wrap', icon_size=(28, 28), icon_anchor=(14, 14))).add_to(target)
MapTools(BOUNDS, CITY_RING, places).add_to(city_map)
Fullscreen(position='topleft', title=msg('ملء الشاشة'), title_cancel=msg('الخروج من ملء الشاشة'), force_separate_button=True).add_to(city_map)
folium.LayerControl(position='bottomright', collapsed=False).add_to(city_map)
city_map.get_root().header.add_child(Element('\n<style>\n.leaflet-container {font-family:Arial,sans-serif;background:#15251f;}\n.leaflet-soft_map-pane {filter:grayscale(100%) brightness(1.05);}\n.leaflet-control-geocoder {border-radius:10px!important;box-shadow:0 4px 16px #0002;}\n.leaflet-control-geocoder-form input {width:min(260px,calc(100vw - 125px));padding:9px 4px;}\n.leaflet-control-layers {border-radius:10px;padding:10px;line-height:1.8;}\n.leaflet-bar {border-radius:8px;overflow:hidden;}\n.sb-pin {width:28px;height:28px;border-radius:50%;border:2px solid white;\nbox-shadow:0 2px 7px #0005;display:flex;align-items:center;justify-content:center;color:white;font-size:14px;box-sizing:border-box;}\n.sb-cluster {width:42px;height:42px;border-radius:50%;border:3px solid white;\nbox-shadow:0 2px 7px #0005;display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;box-sizing:border-box;}\n.sb-status {background:white;color:#14532d;padding:10px;border-radius:10px;\nmax-width:min(340px,60vw);font-size:13px;direction:rtl;box-shadow:0 4px 16px #0002;}\n</style>\n'))
map_key = f"{get_language()}_nasr_map_v6_{int(show_rest)}_{int(show_charity)}_{int(use_clusters)}_{st.session_state['nasr_city_revision']}"
st_folium(city_map, key=map_key, height=700, use_container_width=True, returned_objects=['last_object_clicked', 'last_object_clicked_tooltip'], on_change=capture_pickup_click)
