import sqlite3
import uuid
from ui_i18n import st
import allocations
import workflow
from workflow_ui import LABELS, render_lifecycle

def show_last_reservation():
    remembered = st.session_state.get('allocation_flash')
    receipt = workflow.get_allocation(remembered['id']) if remembered else None
    if receipt:
        st.info(f"الحجز #{receipt['id']}: {receipt['quantity']} وجبات لـ {receipt['recipient_name']} · {LABELS[workflow.phase(receipt)]}")
        if receipt['status'] != 'cancelled' and st.button('عرض مسار توصيل الحجز', key=f"last_receipt_route_{receipt['id']}"):
            st.session_state['pending_route_allocation'] = receipt['id']
            st.switch_page('pages/1_map.py')

def render_reservation(donation, recipient, context):
    st.subheader('تأكيد التوزيع')
    prefix = f"reserve_{context}_{donation['id']}_{recipient['need_id']}_{recipient['need_revision']}_{donation['remaining_quantity']}_{donation['food_category']}"
    token = st.session_state.setdefault(prefix + '_token', uuid.uuid4().hex)
    with st.form(prefix):
        quantity = st.number_input('عدد الوجبات للجمعية', min_value=1, max_value=recipient['proposed_meals'], value=recipient['proposed_meals'], step=1, key=prefix + '_quantity')
        confirmed = st.checkbox('راجعت ملاءمة الأكل وملاحظات الجمعية واتّفقت على الاستلام', key=prefix + '_confirmed')
        submitted = st.form_submit_button('تأكيد التوزيع وحجز الوجبات',type='primary')
    if submitted:
        try:
            receipt = allocations.reserve(donation_id=donation['id'], need_id=recipient['need_id'], quantity=int(quantity), expected_need_revision=recipient['need_revision'], expected_available=donation['remaining_quantity'], food_category=donation['food_category'], request_token=token, confirmed=confirmed)
        except (ValueError, allocations.ReservationConflict) as error:
            st.warning(str(error))
        except sqlite3.Error:
            st.error('تعذر تأكيد الحجز. حدّث الصفحة وراجع الحجوزات قبل المحاولة مرة تانية.')
        else:
            st.session_state['allocation_flash'] = receipt
            st.rerun()

def render_allocations(donation_id=None):
    try:
        rows = allocations.list_allocations(donation_id)
    except sqlite3.Error:
        st.warning('تعذر تحميل الحجوزات الآن.')
        return
    if not rows:
        return
    labels = {'reserved': 'محجوز — في انتظار التسليم', 'delivered': 'تم التسليم', 'cancelled': 'ملغي'}
    st.dataframe([{'رقم الحجز': r['id'], 'المطعم': r['donor_name'], 'الجهة': r['recipient_name'], 'الوجبات': r['quantity'], 'الأكل': r['food_type'], 'الحالة': LABELS[workflow.phase(r)], 'عنوان الاستلام': r['pickup_address'], 'عنوان الجهة': r['recipient_address'], 'وقت الحجز': r['created_at']} for r in rows], hide_index=True)
    by_id = {r['id']: r for r in rows if r['status'] != 'cancelled'}
    if by_id:
        choice = st.selectbox('اختار حجزًا لعرض الطريق', list(by_id), index=None, format_func=lambda key: f"#{key} · {by_id[key]['recipient_name']}", key=f"ledger_route_choice_{donation_id or 'all'}", placeholder='اختار الحجز…')
        if st.button('عرض الطريق على الخريطة', disabled=choice is None, key=f"ledger_route_button_{donation_id or 'all'}"):
            st.session_state['pending_route_allocation'] = choice
            st.switch_page('pages/1_map.py')
    render_lifecycle(rows, str(donation_id or 'all'))
