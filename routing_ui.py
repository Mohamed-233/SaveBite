"""A route belongs to an existing allocation and its immutable location snapshot."""
import json
import sqlite3
import time
import folium
from streamlit_folium import st_folium
from ui_i18n import st, msg
from i18n import get_language
import allocations
import routing_data
from astar_routing import RoutingError, route_points

@st.cache_resource(show_spinner=False)
def cached_graph(version):
    return routing_data.load_road_graph()

def render_route(allocation_id):
    try:
        receipt = next((r for r in allocations.list_allocations() if r['id'] == allocation_id), None)
    except sqlite3.Error:
        st.error('تعذر فتح بيانات الحجز.')
        return
    if receipt is None:
        st.info('الحجز غير موجود.')
        return
    if receipt['status'] == 'cancelled':
        st.info('الحجز ملغي؛ لا يوجد توصيل مطلوب.')
        return
    try:
        snapshot = json.loads(receipt['snapshot'])
        origin = (snapshot['donation']['latitude'], snapshot['donation']['longitude'])
        destination = (snapshot['need']['latitude'], snapshot['need']['longitude'])
    except (ValueError, TypeError, KeyError):
        st.error('الحجز لا يحتوي نقطتي وصول صالحتين. راجع بيانات الموقع.')
        return
    st.write(f"**{receipt['donor_name']} ← {receipt['recipient_name']}** · {receipt['quantity']} وجبات")
    try:
        path = routing_data.SNAPSHOT
        version = (str(path), path.stat().st_mtime_ns if path.exists() else 0)
        with st.spinner('جاري حساب الطريق…'):
            data, graph = cached_graph(version)
            started = time.perf_counter()
            route = route_points(graph, origin, destination)
            elapsed = (time.perf_counter() - started) * 1000
    except (RoutingError, OSError) as error:
        st.warning(str(error))
        if st.button('إعادة محاولة تحميل الطريق', key=f'retry_route_{allocation_id}'):
            cached_graph.clear()
            st.rerun()
        return
    a, b = st.columns(2)
    a.metric('مسافة الشوارع', f"{route['distance_m'] / 1000:.2f} كم")
    b.metric('الوجبات المحجوزة', receipt['quantity'])
    if len(route['nodes']) == 1:
        st.info('نقطتا الربط على نفس موضع الطريق.')
    view = folium.Map(location=origin, zoom_start=14, tiles='OpenStreetMap')
    folium.Marker(origin, tooltip=msg('الاستلام: ') + receipt['donor_name'], icon=folium.Icon(color='orange', icon='cutlery', prefix='fa')).add_to(view)
    folium.Marker(destination, tooltip=msg('التسليم: ') + receipt['recipient_name'], icon=folium.Icon(color='green', icon='heart', prefix='fa')).add_to(view)
    if len(route['coordinates']) > 1:
        folium.PolyLine(route['coordinates'], color='#2563eb', weight=6, tooltip=msg('مسار التوصيل')).add_to(view)
    for endpoint, network_point in [(origin, route['coordinates'][0]), (destination, route['coordinates'][-1])]:
        folium.PolyLine([endpoint, network_point], color='#64748b', weight=3, dash_array='5,7', tooltip=msg('ربط تقريبي بالموقع؛ ليس شارعًا محسوبًا')).add_to(view)
    view.fit_bounds([origin, destination] + route['coordinates'])
    st_folium(view, height=520, use_container_width=True, returned_objects=[], key=f'route_view_{allocation_id}_{get_language()}')
    st.caption('الخط المتقطع: ربط تقريبي بالموقع.')
    with st.expander('خيارات المسار'):
        if st.button('تنزيل نسخة أحدث من الشوارع', key=f'update_roads_{allocation_id}'):
            try:
                with st.spinner('جاري تحديث بيانات الشوارع…'):
                    routing_data.download_snapshot()
                cached_graph.clear()
                st.rerun()
            except RoutingError as error:
                st.warning(str(error) + ' النسخة السابقة محفوظة.')

def render_route_selector():
    try:
        rows = [r for r in allocations.list_allocations() if r['status'] != 'cancelled']
    except sqlite3.Error:
        st.warning('تعذر تحميل الحجوزات.')
        return
    if not rows:
        st.info('أكد حجز تبرع أولًا علشان تعرض مسار توصيله.')
        return
    by_id = {r['id']: r for r in rows}
    pending = st.session_state.pop('pending_route_allocation', None)
    if pending in by_id:
        st.session_state['route_allocation_choice'] = pending
    if st.session_state.get('route_allocation_choice') not in by_id:
        st.session_state.pop('route_allocation_choice', None)
    selected = st.selectbox('اختار الحجز لعرض مسار التوصيل', list(by_id), index=None, format_func=lambda key: f"حجز #{key} · {by_id[key]['donor_name']} ← {by_id[key]['recipient_name']}", key='route_allocation_choice', placeholder='اختار حجزًا…')
    if selected is not None:
        render_route(selected)
    return selected is not None
