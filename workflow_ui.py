import sqlite3
import uuid
from pathlib import Path

from ui_i18n import st

import database
import workflow
import data_reset

LABELS={"pending":"محجوز — في انتظار الاستلام","in_transit":"جاري التوصيل",
    "delivered":"تم التسليم","cancelled":"ملغي"}

ERRORS={
    "Confirm the action before submitting.":"فعّل تأكيد العملية قبل التنفيذ.",
    "Reservation no longer exists. Refresh the page.":"الحجز لم يعد موجودًا؛ حدّث الصفحة.",
    "Reservation changed in another window. Refresh the page.":"الحجز اتغيّر في نافذة تانية؛ حدّث الصفحة وراجع حالته.",
    "This action is not allowed for the current reservation state.":"العملية غير مسموحة في حالة الحجز الحالية.",
    "Balances are inconsistent; cancellation was not applied.":"الأرصدة غير متطابقة؛ لم يتم الإلغاء أو تغيير الكميات.",
    "Pickup or recipient deadline has expired. Review the reservation before dispatch.":"انتهى موعد الاستلام أو احتياج الجهة؛ راجع الحجز قبل بدء التوصيل.",
    "Type RESET to confirm deletion.":"اكتب RESET لتأكيد مسح البيانات.",
    "Data changed after the preview. Review the updated counts before resetting.":"البيانات اتغيّرت بعد المعاينة؛ راجع الأعداد الجديدة قبل المسح.",
    "Need balances are inconsistent. Reset was not applied.":"أرصدة الاحتياجات غير متطابقة؛ لم يتم مسح أي بيانات.",
}


def sync_activity_state():
    with database.get_connection() as con:
        epoch=con.execute("SELECT value FROM app_metadata WHERE key='reset_epoch'").fetchone()[0]
    previous=st.session_state.get('_activity_epoch')
    if previous is not None and previous!=epoch:
        exact={'allocation_flash','pending_route_allocation','route_allocation_choice','pending_donation_pickup','workflow_notice','home_donation_choice'}
        prefixes=('_ui_value_','_ui_locale_','food_category_','food_description_','donation_','donor_','pickup_confirmed_','reserve_','legacy_category_','ledger_route_',
            'refresh_priority_','need_','needs_','lifecycle_')
        for key in list(st.session_state):
            if key in exact or key.startswith(prefixes):st.session_state.pop(key,None)
    st.session_state['_activity_epoch']=epoch
    reset=st.session_state.get('reset_notice')
    if reset and st.session_state.get('_reset_notice_seen')!=reset:
        st.toast('تم مسح البيانات المسجلة.',icon='✅')
        st.session_state['_reset_notice_seen']=reset
    notice=st.session_state.get('workflow_notice')
    if notice and st.session_state.get('_workflow_notice_seen')!=notice:
        st.toast(notice,icon='✅')
        st.session_state['_workflow_notice_seen']=notice


def render_lifecycle(rows,scope):
    eligible={r['id']:r for r in rows if r['status']=='reserved'}
    if not eligible:return
    selected=st.selectbox('إدارة حالة حجز',list(eligible),index=None,
        format_func=lambda key:f"#{key} · {eligible[key]['recipient_name']} · {LABELS[workflow.phase(eligible[key])]}",
        key=f'lifecycle_choice_{scope}',placeholder='اختار الحجز…')
    if selected is None:return
    row=eligible[selected];phase=workflow.phase(row)
    options=['start','cancel'] if phase=='pending' else ['deliver','cancel']
    names={'start':'بدء التوصيل','deliver':'تأكيد التسليم','cancel':'إلغاء الحجز وإرجاع الوجبات'}
    prefix=f"lifecycle_{scope}_{selected}_{row['revision']}"
    action=st.selectbox('العملية',options,index=None,format_func=names.get,key=prefix+'_action',placeholder='اختار العملية…')
    if action is None:return
    confirmations={
        'start':'الأكل تم استلامه من المطعم وبدأ التوصيل',
        'deliver':'تم التأكد من استلام الجهة لكل وجبات هذا الحجز',
        'cancel':('تم إلغاء التوصيل وكل وجبات الحجز رجعت فعليًا للمطعم' if phase=='in_transit'
            else 'أكدت إلغاء الحجز والوجبات ما زالت متاحة في المطعم'),
    }
    request_token=st.session_state.setdefault(prefix+'_'+action+'_token',uuid.uuid4().hex)
    with st.form(prefix+'_'+action):
        st.write(f"الحجز #{selected} · {row['quantity']} وجبات · {row['recipient_name']}")
        confirmed=st.checkbox(confirmations[action],key=prefix+'_'+action+'_confirmed')
        submit=st.form_submit_button(names[action])
    if submit:
        try:
            result=workflow.transition(allocation_id=selected,action=action,expected_revision=row['revision'],
                request_token=request_token,confirmed=confirmed)
        except ValueError as error:st.warning(ERRORS.get(str(error),'تعذر تنفيذ العملية؛ حدّث الصفحة وراجع حالة الحجز.'))
        except sqlite3.Error:st.error('تعذر الحفظ. راجع حالة الحجز قبل إعادة المحاولة؛ لا يتم تطبيق خصم أو إرجاع جزئي.')
        else:
            st.session_state['workflow_notice']=f"الحجز #{selected}: {LABELS[workflow.phase(result)]} ✅"
            st.rerun()


def render_reset():
    st.subheader('إعادة ضبط البيانات المسجلة')
    st.write('سيتم حذف التبرعات واحتياجات الجهات والحجوزات. الحسابات وأماكن الخريطة ستظل موجودة.')
    try:p=data_reset.preview()
    except sqlite3.Error:
        st.error('تعذر تجهيز معاينة المسح.');return
    a,b,c=st.columns(3)
    a.metric('تبرعات سيتم مسحها',p['donations'])
    b.metric('حجوزات سيتم مسحها',p['allocations'])
    c.metric('جمعيات واحتياجات سيتم مسحها',p['needs'])
    st.caption('يتم حفظ نسخة احتياطية تلقائيًا قبل المسح.')
    if st.session_state.get('reset_notice'):
        st.caption('النسخة الاحتياطية: '+st.session_state['reset_notice'])
    with st.form('reset_activity_'+p['digest']):
        confirmation=st.text_input('اكتب RESET لتأكيد مسح كل البيانات المسجلة',key='reset_confirm_'+p['digest'])
        admin_password=st.text_input('كلمة المرور الحالية', type='password', key='reset_admin_password')
        submit=st.form_submit_button('مسح بيانات المتبرعين والجمعيات المسجلة',disabled=p['donations']==0 and p['allocations']==0 and p['needs']==0)
    if submit:
        try:
            import auth
            auth.confirm_password(admin_password)
            result=data_reset.reset_registered_data(expected_digest=p['digest'],confirmation=confirmation)
        except ValueError as error:st.warning(ERRORS.get(str(error),'لم يتم المسح؛ راجع الأعداد وأعد التأكيد.'))
        except (sqlite3.Error,OSError):st.error('تعذر إنشاء النسخة الاحتياطية أو مسح البيانات؛ العملية لم تكتمل.')
        else:
            st.session_state['reset_notice']=result['backup'] or 'لا توجد بيانات لمسحها.'
            st.rerun()
