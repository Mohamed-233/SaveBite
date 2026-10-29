import auth
from auth_ui import require_login, require_admin_page
from datetime import datetime, timedelta
import html
import sqlite3
import folium
from streamlit_folium import st_folium
from locations import canonical_pickup, get_restaurants
from ui_i18n import st
import database
import beneficiary_needs as needs
from priority_ui import render_recommendations
from allocation_ui import show_last_reservation
from workflow_ui import sync_activity_state, render_reset
from product_ui import apply_brand, home_hero
from i18n import apply_text_direction, render_language_selector, t, ensure_language
from validation import MAX_ADDRESS_LENGTH, MAX_MEALS, MAX_NAME_LENGTH, MIN_MEALS, validate_donation

def set_flash(level: str, key: str) -> None:
    """Remember a message to show after the next rerun."""
    st.session_state['flash'] = (level, key)

def show_flash() -> None:
    flash = st.session_state.pop('flash', None)
    if flash:
        level, key = flash
        if level == 'success':
            st.success(t(key))
        else:
            st.info(t(key))

def display_status(row, now: datetime) -> str:
    """Status code to show: an available donation past its deadline is 'expired'."""
    deadline = datetime.strptime(row['pickup_deadline'], database.DATETIME_FORMAT)
    if row['status'] == database.STATUS_AVAILABLE and deadline <= now:
        return 'expired'
    return row['status']

@st.cache_data(show_spinner=False)
def restaurant_catalogue():
    return get_restaurants()

def pickup_picker(version):
    restaurants = restaurant_catalogue()
    by_id = {place['place_id']: place for place in restaurants}
    selection_key = f'donation_restaurant_{version}'
    incoming = st.session_state.pop('pending_donation_pickup', None)
    if incoming is not None:
        try:
            incoming = canonical_pickup(incoming)
            st.session_state['donation_pickup_extra'] = incoming
            st.session_state[selection_key] = incoming['place_id']
        except ValueError as error:
            st.error(str(error))
    extra = st.session_state.get('donation_pickup_extra')
    if extra:
        try:
            extra = canonical_pickup(extra)
            by_id.setdefault(extra['place_id'], extra)
        except ValueError:
            pass
    if st.session_state.get(selection_key) not in by_id:
        st.session_state.pop(selection_key, None)
    choice = st.selectbox('مطعم أو مكان التبرع', options=list(by_id), index=None, placeholder='ابحث باسم المطعم أو عنوانه...', format_func=lambda place_id: f"{by_id[place_id]['name']} — {by_id[place_id]['address']}", key=selection_key)
    if choice is None:
        return None
    selected = canonical_pickup(by_id[choice])
    with st.expander('عرض موقع الاستلام على الخريطة'):
        preview = folium.Map(location=[selected['latitude'], selected['longitude']], zoom_start=17, tiles='OpenStreetMap')
        folium.Marker([selected['latitude'], selected['longitude']], tooltip=html.escape(selected['name']), icon=folium.Icon(color='orange', icon='cutlery', prefix='fa')).add_to(preview)
        st_folium(preview, key=f'pickup_preview_{version}_{choice}', height=260, use_container_width=True, returned_objects=[])
    return selected

def home_page():
    require_login()
    ensure_language('home')
    apply_brand()
    sync_activity_state()
    home_hero()
    now = datetime.now()
    rows = database.list_donations()
    available = [r for r in rows if display_status(r, now) == database.STATUS_AVAILABLE and r['remaining_quantity'] > 0]
    import beneficiary_needs as needs
    needs.init_needs_db()
    active = needs.list_active_needs(now)
    left, right = st.columns(2)
    left.metric('وجبات متاحة للتبرع', sum((r['remaining_quantity'] for r in available)))
    right.metric('وجبات مطلوبة حاليًا', sum((needs.remaining_need(r) for r in active)))
    if available:
        st.subheader('اقتراح جهة لتبرع محفوظ')
        selected_id = st.selectbox('اختار التبرع', [r['id'] for r in available], index=None, format_func=lambda key: next((f"#{r['id']} · {r['pickup_place_name'] or r['donor_name']} · {r['remaining_quantity']} وجبات" for r in available if r['id'] == key)), placeholder='اختر تبرعًا لعرض الجهات المقترحة', key='home_donation_choice')
        if selected_id is not None:
            render_recommendations(selected_id, context='home')
    from allocation_ui import render_allocations
    with st.expander('الحجوزات المسجلة'):
        render_allocations()
    with st.expander('التبرعات المسجلة'):
        donations_page()

def add_donation_page():
    require_login()
    ensure_language('donation')
    apply_brand()
    sync_activity_state()
    show_last_reservation()
    st.title('إضافة تبرع')
    summary = st.session_state.get('donation_saved_summary')
    if summary:
        st.success('تم حفظ تبرعك ✅')
        st.write(f"**{summary['name']}** · {summary['meals']} وجبات · {summary['food']}")
        st.write('آخر موعد للاستلام: ' + summary['deadline'])
        if summary.get('donation_id'):
            render_recommendations(summary['donation_id'], context='saved')
        else:
            st.info('تقدر تختار التبرع من الرئيسية لعرض الجهات المقترحة.')
        if st.button('إضافة تبرع آخر'):
            st.session_state.pop('donation_saved_summary', None)
            st.rerun()
        st.page_link(st.session_state['savebite_home_page'], label='الرجوع للرئيسية', icon='🏠')
        return
    version = st.session_state.setdefault('donation_form_version', 0)
    defaults_key = f'donation_default_deadline_{version}'
    if defaults_key not in st.session_state:
        st.session_state[defaults_key] = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    default_deadline = st.session_state[defaults_key]
    selected_pickup = pickup_picker(version)
    place_key = selected_pickup['place_id'] if selected_pickup else 'none'
    address_default = selected_pickup['address'] if selected_pickup else ''
    if address_default in {'العنوان غير مسجّل', 'غير مسجّل'}:
        address_default = ''
    with st.form(f'donation_form_{version}'):
        food_category = st.selectbox('نوع الأكل', list(needs.FOOD_TYPES), index=None, format_func=needs.FOOD_TYPES.get, placeholder='اختار نوع الأكل', key=f'food_category_{version}')
        meals = st.number_input('عدد الوجبات', min_value=MIN_MEALS, max_value=MAX_MEALS, value=MIN_MEALS, step=1, key=f'donation_meals_{version}')
        left, right = st.columns(2)
        deadline_date = left.date_input('تاريخ آخر استلام', value=default_deadline.date(), key=f'donation_date_{version}')
        deadline_time = right.time_input('آخر وقت للاستلام', value=default_deadline.time(), step=900, key=f'donation_time_{version}')
        with st.expander('تفاصيل الأكل والمتبرع والعنوان'):
            description = st.text_input('وصف الأكل (اختياري)', max_chars=MAX_NAME_LENGTH, placeholder='مثال: أرز وفراخ، أو نوع المخبوزات', key=f'food_description_{version}')
            donor_name = st.text_input('اسم المتبرع أو المطعم', max_chars=MAX_NAME_LENGTH, value=selected_pickup['name'][:MAX_NAME_LENGTH] if selected_pickup else '', key=f'donor_name_{version}_{place_key}')
            address = st.text_input('عنوان الاستلام', max_chars=MAX_ADDRESS_LENGTH, value=address_default[:MAX_ADDRESS_LENGTH], key=f'donation_address_{version}_{place_key}')
        pickup_confirmed = st.checkbox('راجعت عنوان وموقع الاستلام وبيانات التبرع', key=f'pickup_confirmed_{version}_{place_key}')
        submitted = st.form_submit_button('حفظ التبرع', disabled=selected_pickup is None, type='primary')
    if not submitted:
        return
    donor_name = donor_name.strip()
    if food_category not in needs.FOOD_TYPES:
        st.error('اختار نوع الأكل علشان نقدر نطابقه مع احتياج الجهة.')
        return
    if food_category == 'other' and (not description.strip()):
        st.error('وضح نوع الأكل الآخر في تفاصيل الأكل.')
        return
    food_type = description.strip() or needs.FOOD_TYPES[food_category]
    address = address.strip()
    deadline = datetime.combine(deadline_date, deadline_time)
    if selected_pickup is None:
        st.error('اختار مطعم التبرع علشان نحفظ موقع الاستلام مع التبرع.')
        return
    if not pickup_confirmed:
        st.error('راجع الموقع والعنوان وفعّل تأكيد نقطة الاستلام قبل الحفظ.')
        return
    try:
        selected_pickup = canonical_pickup(selected_pickup)
    except ValueError as error:
        st.error(str(error))
        return
    errors = validate_donation(donor_name, food_type, int(meals), address, deadline)
    if errors:
        for key, params in errors:
            st.error(t(key, **params))
        return
    try:
        saved_id = database.insert_donation_record(donor_name, food_type, int(meals), address, deadline, latitude=selected_pickup['latitude'], longitude=selected_pickup['longitude'], pickup_place_id=selected_pickup['place_id'], pickup_place_name=selected_pickup['name'], location_source=selected_pickup['source'], pickup_confirmed=pickup_confirmed, food_category=food_category)
    except (ValueError, sqlite3.Error):
        st.error('تعذّر حفظ التبرع. راجع البيانات وحاول تاني؛ بيانات النموذج لسه موجودة.')
        return
    if saved_id is None:
        st.info('التبرع ده محفوظ بالفعل؛ مش محتاج تسجله مرة تانية.')
        return
    st.session_state['donation_saved_summary'] = {'donation_id': saved_id, 'name': selected_pickup['name'], 'food': food_type, 'meals': int(meals), 'deadline': deadline.strftime('%Y-%m-%d %H:%M')}
    st.session_state['donation_form_version'] = version + 1
    st.rerun()

def donations_page():
    require_login()
    st.subheader('التبرعات المسجلة')
    rows = database.list_donations()
    if not rows:
        st.info(t('donations.empty'))
        return
    now = datetime.now()
    table = []
    available_meals = 0
    for row in rows:
        status = display_status(row, now)
        if status == database.STATUS_AVAILABLE:
            available_meals += row['remaining_quantity']
        table.append({t('donations.col.id'): row['id'], t('donations.col.donor'): row['donor_name'], t('donations.col.food_type'): row['food_type'], t('donations.col.original'): row['original_quantity'], t('donations.col.remaining'): row['remaining_quantity'], t('donations.col.address'): row['address'], 'مطعم الاستلام': row['pickup_place_name'] or 'لم يُسجّل', 'خط العرض': row['latitude'], 'خط الطول': row['longitude'], 'الموقع مؤكد من المتبرع': 'نعم' if row['pickup_confirmed'] else 'لم يؤكَّد', 'مصدر الموقع': row['location_source'] or 'لم يُسجّل', t('donations.col.deadline'): row['pickup_deadline'][:16], t('donations.col.status'): 'محجوز بالكامل' if status == 'reserved' else t(f'status.{status}'), t('donations.col.created'): row['created_at'][:16]})
    col1, col2 = st.columns(2)
    col1.metric(t('donations.metric.total'), len(rows))
    col2.metric(t('donations.metric.available_meals'), available_meals)
    basic_columns = ['مطعم الاستلام', t('donations.col.food_type'), t('donations.col.remaining'), t('donations.col.deadline'), t('donations.col.status')]
    st.dataframe([{k: r[k] for k in basic_columns} for r in table], hide_index=True)

def administration_page():
    require_admin_page()
    ensure_language('admin')
    apply_brand()
    sync_activity_state()
    show_last_reservation()
    st.title('الإدارة')
    tab_needs, tab_donations, tab_allocations, tab_accounts, tab_settings = st.tabs(['احتياجات الجهات', 'التبرعات', 'الحجوزات', 'الحسابات', 'إعادة الضبط'])
    with tab_accounts:
        from auth_ui import account_admin
        account_admin()
    with tab_needs:
        from needs_ui import render_needs
        render_needs()
    with tab_donations:
        donations_page()
    with tab_allocations:
        from allocation_ui import render_allocations
        render_allocations()
    with tab_settings:
        render_reset()
