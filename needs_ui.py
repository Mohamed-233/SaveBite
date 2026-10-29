"""Register actual reported needs before priority-based allocation."""
from datetime import datetime, timedelta
import sqlite3
from ui_i18n import st
import beneficiary_needs as needs

def render_needs():
    st.subheader('احتياجات الجهات')
    try:
        needs.init_needs_db()
        records = needs.list_needs()
    except sqlite3.Error:
        st.error('تعذر فتح بيانات الاحتياجات. تأكد أن مجلد المشروع قابل للكتابة وحاول مرة أخرى.')
        st.stop()
    flash = st.session_state.pop('needs_flash', None)
    if flash:
        st.success(flash)
    places = {p['place_id']: p for p in needs.get_beneficiaries()}
    by_place = {r['place_id']: r for r in records}
    now = datetime.now()
    active = [r for r in records if needs.effective_status(r, now) == 'active']
    a, b, c = st.columns(3)
    a.metric('جهات لها احتياج نشط', len(active))
    b.metric('الوجبات المطلوبة حاليًا', sum((needs.remaining_need(r) for r in active)))
    c.metric('الأفراد المسجلون في الاحتياجات النشطة', sum((r['people'] for r in active)))
    st.subheader('تسجيل أو تعديل احتياج')
    place_id = st.selectbox('ابحث عن الجمعية أو دار الرعاية', list(places), index=None, format_func=lambda key: f"{places[key]['name']} — {places[key]['address']}", placeholder='اكتب اسم الجهة…', key='needs_place_choice')
    if place_id:
        place = places[place_id]
        existing = by_place.get(place_id)
        st.write(f"**الجهة:** {place['name']}")
        with st.expander('عرض موقع الجهة للمراجعة'):
            st.map([{'lat': place['latitude'], 'lon': place['longitude']}], zoom=16)
        if existing:
            st.info('تعديل الاحتياج المسجل.')
        if existing and existing.get('meals_reserved', 0):
            pass
        revision = existing['revision'] if existing else None
        version = st.session_state.get('needs_form_version', 0)
        prefix = f'need_{place_id}_{revision}_{version}'
        default_deadline = st.session_state.setdefault(prefix + '_default_deadline', datetime.strptime(existing['needed_by'], needs.FORMAT) if existing else now + timedelta(hours=4))
        with st.form(prefix):
            category = st.selectbox('نوع الجهة', needs.CATEGORIES, index=needs.CATEGORIES.index(existing['category']) if existing else None, placeholder='حدد نوع الجهة', key=prefix + '_category')
            address = st.text_input('عنوان الوصول بعد المراجعة', value=existing['address'] if existing else place['address'], max_chars=300, key=prefix + '_address')
            left, right = st.columns(2)
            people = left.number_input('عدد الأفراد المحتاجين', min_value=1, max_value=100000, value=existing['people'] if existing else 1, step=1, key=prefix + '_people')
            meals = right.number_input('إجمالي الوجبات المطلوبة لهذا الاحتياج', min_value=1, max_value=100000, value=existing['meals_requested'] if existing else 1, step=1, key=prefix + '_meals')
            urgency = st.selectbox('درجة الاستعجال حسب إفادة الجهة', list(needs.URGENCY), index=list(needs.URGENCY).index(existing['urgency']) if existing else None, format_func=needs.URGENCY.get, placeholder='حدد الاستعجال', key=prefix + '_urgency')
            foods = st.multiselect('أنواع الأكل المناسبة', list(needs.FOOD_TYPES), default=existing['food_types'] if existing else [], format_func=needs.FOOD_TYPES.get, key=prefix + '_foods', placeholder='اختار أنواع الأكل المناسبة')
            notes = st.text_area('قيود الأكل أو ملاحظات التسليم (بدون بيانات شخصية للأفراد)', value=existing['dietary_notes'] if existing else '', max_chars=1000, key=prefix + '_notes')
            left, right = st.columns(2)
            date = left.date_input('تاريخ الاحتياج', value=default_deadline.date(), key=prefix + '_date')
            time = right.time_input('آخر وقت مناسب للوصول — بتوقيت جهازك', value=default_deadline.time().replace(second=0, microsecond=0), key=prefix + '_time')
            status = st.selectbox('حالة الاحتياج', list(needs.STATUSES), index=list(needs.STATUSES).index(existing['status']) if existing else 0, format_func=needs.STATUSES.get, key=prefix + '_status')
            confirmed = st.checkbox('تواصلت مع الجهة وراجعت الاحتياج والعنوان ونقطة الوصول', key=prefix + '_confirmed')
            submitted = st.form_submit_button('حفظ التعديلات' if existing else 'حفظ الاحتياج',type='primary')
        if submitted:
            try:
                needs.save_need(place_id=place_id, address=address, category=category, people=int(people), meals_requested=int(meals), urgency=urgency, food_types=foods, dietary_notes=notes, needed_by=datetime.combine(date, time), confirmed=confirmed, status=status, expected_revision=revision)
            except needs.ConflictError as error:
                st.warning(str(error))
            except ValueError as error:
                st.error(str(error))
            except sqlite3.Error:
                st.error('الحفظ لم يكتمل. بيانات النموذج ما زالت موجودة؛ حاول مرة أخرى.')
            else:
                st.session_state['needs_flash'] = 'تم حفظ احتياج الجهة بنجاح ✅'
                st.session_state['needs_form_version'] = version + 1
                st.rerun()
    st.subheader('الاحتياجات المسجلة')
    status_labels = dict(needs.STATUSES, expired='انتهى موعده', fulfilled='تمت تغطيته بالحجز')
    status_filter = st.selectbox('عرض حسب الحالة', ['all', 'active', 'fulfilled', 'expired', 'paused', 'closed'], format_func=lambda value: 'كل الحالات' if value == 'all' else status_labels[value], key='needs_status_filter')
    query = st.text_input('بحث في الاحتياجات بالاسم أو العنوان', key='needs_query').strip().casefold()
    shown = [r for r in records if (status_filter == 'all' or needs.effective_status(r, now) == status_filter) and (not query or query in (r['place_name'] + ' ' + r['address']).casefold())]
    if shown:
        st.dataframe([{'رقم': r['id'], 'الجهة': r['place_name'], 'نوع الجهة': r['category'], 'الأفراد': r['people'], 'إجمالي الطلب': r['meals_requested'], 'المحجوز': r.get('meals_reserved', 0), 'المتبقي': needs.remaining_need(r), 'الاستعجال': needs.URGENCY[r['urgency']], 'الأكل المناسب': '، '.join((needs.FOOD_TYPES[f] for f in r['food_types'])), 'الموعد': r['needed_by'], 'الحالة': status_labels[needs.effective_status(r, now)], 'العنوان': r['address'], 'الملاحظات': r['dietary_notes'], 'آخر تحديث': r['updated_at']} for r in shown], hide_index=True, width='stretch')
    else:
        st.info('لا توجد احتياجات مطابقة. سجّل الاحتياج الفعلي من النموذج بالأعلى.')
