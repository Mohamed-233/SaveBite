"""Small recommendation panel reused by home and the save-success view."""
import sqlite3
from datetime import datetime
from ui_i18n import st
import database
import beneficiary_needs as needs
from priority import MATCHABLE_FOOD, rank_recipients
from allocation_ui import render_reservation, render_allocations

def render_recommendations(donation_id, context):
    st.subheader('الجهة المقترحة')
    st.button('تحديث الاقتراح', key=f'refresh_priority_{context}_{donation_id}')
    try:
        donation = database.get_donation(donation_id)
        if donation is None:
            st.info('التبرع غير موجود.')
            return
        needs.init_needs_db()
        records = needs.list_needs()
    except sqlite3.Error:
        st.warning('تم حفظ التبرع، لكن تعذر تحميل الاقتراح الآن. جرّب تحديث الاقتراح.')
        return
    with st.expander('الحجوزات من التبرع ده'):
        render_allocations(donation_id)
    if donation.get('food_category') is None:
        category = st.selectbox('حدد نوع الأكل للتبرع القديم', sorted(MATCHABLE_FOOD), index=None, format_func=needs.FOOD_TYPES.get, placeholder='اختار الفئة المناسبة', key=f'legacy_category_{context}_{donation_id}')
        if category is None:
            return
        donation = dict(donation, food_category=category)
    try:
        ranked = rank_recipients(donation, records)
    except ValueError as error:
        st.info(str(error))
        return
    if not ranked:
        st.info('لا يوجد احتياج مناسب حاليًا. احتفظ بالتبرع وحدّث الاقتراح لاحقًا.')
        return
    best = ranked[0]
    st.markdown(f"**{best['name']}**")
    st.write(f"الكمية المقترحة: **{best['proposed_meals']} وجبات** من أصل {donation['remaining_quantity']} متاحة")
    st.write(f"الاستعجال: {needs.URGENCY[best['urgency']]} · الطلب: {best['requested']} وجبات · عدد الأفراد: {best['people']}")
    st.write('العنوان: ' + best['address'])
    st.write('الاحتياج قبل: ' + best['needed_by'][:16])
    if best['dietary_notes']:
        st.warning('راجع قيود الجهة وملاحظاتها: ' + best['dietary_notes'])
    render_reservation(donation, best, context)
    if len(ranked) > 1:
        with st.expander('جهات أخرى'):
            st.dataframe([{'الجهة': row['name'], 'الكمية المقترحة': row['proposed_meals'], 'الاستعجال': needs.URGENCY[row['urgency']], 'الموعد': row['needed_by'][:16]} for row in ranked[1:6]], hide_index=True)
